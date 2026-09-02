"""
listener.py
===========
"Userbot" baseado em Telethon: conecta com a SUA conta pessoal do Telegram
(por isso pede login por telefone na primeira vez, localmente) e lê novas
mensagens no grupo privado de tips configurado em TELEGRAM_SOURCE_CHAT.

Para cada mensagem nova (com foto e/ou texto), o pipeline é:

    listener.py --(imagem/texto)--> extractor.py --(dados brutos)-->
    matcher.py --(data/hora + link)--> database.py --(salva)-->
    notifier.py --(mensagem formatada)--> seu Telegram privado

Um userbot é necessário aqui (em vez de só o Bot API) porque bots comuns do
Telegram não conseguem ler mensagens de grupos onde eles não têm permissão
explícita de admin/leitura — e muitos grupos de tips não permitem adicionar
bots. Rodando com sua própria conta, o listener enxerga tudo que você já
enxerga como membro.

Dois modos de operação:

  - Escuta contínua (`run_listener`, uso local): fica conectado o tempo
    todo, processa cada mensagem assim que ela chega. Requer um processo
    sempre ativo — bom pra rodar na sua própria máquina, mas incompatível
    com hosts gratuitos que não sustentam workers 24/7.

  - Poll-once (`--poll-once`, uso em produção via GitHub Actions): conecta,
    busca todas as mensagens novas desde a última execução (usando o
    último `message.id` processado, guardado em `sync_state` no Postgres —
    ver database.py), processa cada uma, e desconecta. Pensado pra ser
    chamado por um cron (ex: a cada 5 min via workflow do GitHub Actions),
    já que o runner é uma máquina nova a cada execução, sem estado local.

Autenticação em produção (poll-once): a sessão do Telethon vem de
TELEGRAM_SESSION_STRING (uma StringSession gerada uma única vez, localmente
— veja generate_session_string.py) em vez do arquivo .session local, porque
o runner do GitHub Actions não tem disco persistente entre execuções e não
há como fazer login por telefone interativamente lá.

Uso:
    python listener.py                 # inicia a escuta contínua (local)
    python listener.py --poll-once     # busca mensagens novas desde a última vez e sai (produção)
    python listener.py --list-chats    # lista seus chats/grupos e IDs
                                        # (útil para descobrir TELEGRAM_SOURCE_CHAT)
    python listener.py --backfill 50   # reprocessa as últimas 50 mensagens do grupo
                                        # (útil para testar sem esperar uma tip nova)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# O console do Windows normalmente usa uma codepage legada (cp1252) que não
# suporta vários caracteres Unicode usados em logs/bibliotecas (emojis,
# acentos, barras de progresso do EasyOCR como "█"). Forçar UTF-8 aqui evita
# UnicodeEncodeError nesses casos, sem depender de configuração externa.
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.custom.message import Message

from config import settings
from database import bet_exists_for_message, get_sync_state, init_db, insert_bet, set_sync_state
from extractor import extract_bet_info
from matcher import build_enabled_adapters, find_match
from models import Bet, BetStatus, ExtractedBet, MatchInfo, TipoAposta
from notifier import send_bet_notification, send_plain_message

# Chave usada em sync_state (database.py) para lembrar até onde o polling já
# processou — necessário porque o runner do GitHub Actions não tem disco
# persistente entre execuções (ver poll_new_messages()).
_SYNC_STATE_KEY = "listener_last_message_id"

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(settings.resolve_path("logs") / "listener.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("listener")

MEDIA_DIR = settings.resolve_path(settings.MEDIA_DIR)


def _build_client() -> TelegramClient:
    if not settings.TELEGRAM_API_ID or not settings.TELEGRAM_API_HASH:
        raise RuntimeError(
            "TELEGRAM_API_ID / TELEGRAM_API_HASH não configurados no .env. "
            "Veja o passo a passo no README.md para obtê-los em https://my.telegram.org"
        )
    # Produção (GitHub Actions): StringSession, sem arquivo local — o runner
    # não tem disco persistente entre execuções, então login por telefone
    # interativo não é uma opção lá; a sessão precisa já vir pronta via
    # secret (gerada uma vez, localmente, com generate_session_string.py).
    if settings.TELEGRAM_SESSION_STRING:
        session = StringSession(settings.TELEGRAM_SESSION_STRING)
    else:
        session = str(settings.resolve_path(settings.TELEGRAM_SESSION_NAME))
    return TelegramClient(session, settings.TELEGRAM_API_ID, settings.TELEGRAM_API_HASH)


async def _resolve_source_chat(client: TelegramClient):
    """
    Resolve o grupo de tips a partir de TELEGRAM_SOURCE_CHAT, em 3 formatos
    possíveis:

      1. ID numérico fixo (ex: "-1001234567890") — grupo permanente.
      2. @username fixo (ex: "meugrupo_tips") — grupo permanente.
      3. Prefixo de nome (ex: "Cansadão Apostas") — usado quando o grupo é
         recriado periodicamente com um sufixo variável no nome (ex: data:
         "Cansadão Apostas 31/08", "Cansadão Apostas 01/09"). Nesse caso,
         varremos os diálogos procurando pelo grupo mais recente cujo nome
         comece com esse prefixo — sem precisar atualizar nada manualmente
         quando um grupo novo é liberado.

    Como diferenciamos os casos: se o valor não é um ID numérico nem existe
    um chat com esse @username/nome exato, tratamos como prefixo (caso 3).
    """
    raw = settings.TELEGRAM_SOURCE_CHAT.strip()
    if not raw:
        raise RuntimeError("TELEGRAM_SOURCE_CHAT não configurado no .env")

    # Caso 1: ID numérico.
    try:
        chat_id = int(raw)
        return await client.get_entity(chat_id)
    except ValueError:
        pass

    # Caso 2: @username fixo (só faz sentido tentar se não tiver espaços —
    # username do Telegram nunca tem espaço, prefixo de nome de grupo tem).
    if " " not in raw:
        try:
            return await client.get_entity(raw)
        except (ValueError, TypeError):
            pass

    # Caso 3: prefixo de nome — acha o diálogo mais recente cujo título
    # comece com o prefixo configurado.
    candidatos = []
    async for dialog in client.iter_dialogs():
        if dialog.name and dialog.name.strip().lower().startswith(raw.lower()):
            candidatos.append(dialog)

    if not candidatos:
        raise RuntimeError(
            f"Nenhum grupo encontrado com nome começando em {raw!r}. "
            "Confirme se você já entrou no grupo do dia, ou rode "
            "'python listener.py --list-chats' para conferir os nomes exatos."
        )

    # dialog.date é a data da última mensagem/atividade — o grupo do dia
    # mais recente tende a ser o mais ativo agora.
    mais_recente = max(candidatos, key=lambda d: d.date)
    if len(candidatos) > 1:
        logger.info(
            "Múltiplos grupos com prefixo %r encontrados (%s) — usando o mais recente: %r",
            raw, [d.name for d in candidatos], mais_recente.name,
        )
    return mais_recente.entity


async def process_message(message: Message) -> None:
    """
    Núcleo do pipeline: roda para cada mensagem nova (ou de backfill).
    Idempotente — se a msg já foi processada (mesmo message.id), é ignorada.
    """
    if bet_exists_for_message(message.id):
        logger.debug("Mensagem %s já processada, ignorando.", message.id)
        return

    caption = message.raw_text or ""
    image_path: str | None = None

    if message.photo:
        dest = MEDIA_DIR / f"{message.id}.jpg"
        image_path = await message.download_media(file=str(dest))
        logger.info("Imagem da mensagem %s salva em %s", message.id, image_path)

    if not caption and not image_path:
        logger.debug("Mensagem %s sem texto nem imagem, ignorando.", message.id)
        return

    # --- extractor.py -----------------------------------------------------
    # extract_bet_info é síncrono e pesado (OCR local via EasyOCR/torch) —
    # despachamos para uma thread separada pra não travar o loop asyncio
    # (Telethon) enquanto processa.
    extracted: ExtractedBet = await asyncio.to_thread(extract_bet_info, image_path, caption)

    if not extracted.valido:
        logger.warning("Mensagem %s: extração insuficiente (faltam jogadores). Texto: %r", message.id, caption[:200])
        bet = Bet(
            jogador1=extracted.jogador1 or "?",
            jogador2=extracted.jogador2 or "?",
            torneio=extracted.torneio,
            mercado=extracted.mercado,
            odd=extracted.odd,
            status=BetStatus.ERRO_EXTRACAO.value,
            fonte_texto=extracted.texto_bruto,
            mensagem_id=message.id,
            unidades=1.0,
        )
        bet.id = insert_bet(bet)
        # Ainda salva no banco (auditoria/debug), mas só notifica no
        # Telegram se achou ALGUM indício de tip de verdade (um nome ou uma
        # odd) — mensagem sem sinal nenhum (ex: sticker/print aleatório sem
        # legenda, mandado no grupo) vira ruído de notificação "❌" toda
        # vez, sem nada acionável pro usuário conferir.
        if extracted.jogador1 or extracted.odd is not None:
            await send_bet_notification(bet)
        else:
            logger.debug("Mensagem %s: nenhum indício de tip (sem nome nem odd), notificação pulada.", message.id)
        return

    # --- múltipla/combinada (várias seleções, sem 1 confronto único) -------
    # Não passa pelo matcher.py: não há "o jogo" pra confirmar no SofaScore
    # nem link exato de casa de apostas — só um link genérico de tênis do
    # dia. Ver extractor._GEMINI_PROMPT para o critério de detecção e
    # models.Bet.jogo para como isso vira texto de exibição.
    if extracted.tipo_aposta == TipoAposta.MULTIPLA.value:
        links = {
            adapter.slug: {
                "nome": adapter.display_name,
                "url": adapter.build_fallback_link(None, None),
                "exato": False,
            }
            for adapter in build_enabled_adapters()
        }
        bet = Bet(
            jogador1=", ".join(extracted.selecoes) if extracted.selecoes else "Múltipla (ver print original)",
            jogador2="",
            mercado="Múltipla" + (f" ({len(extracted.selecoes)} seleções)" if extracted.selecoes else ""),
            odd=extracted.odd,
            links=links,
            status=BetStatus.AGENDADA.value,
            fonte_texto=extracted.texto_bruto,
            mensagem_id=message.id,
            unidades=1.0,
            tipo_aposta=TipoAposta.MULTIPLA.value,
            selecoes=extracted.selecoes,
        )
        bet.id = insert_bet(bet)
        await send_bet_notification(bet)
        return

    # --- matcher.py ---------------------------------------------------------
    # find_match roda Playwright em modo síncrono (sofascore_client.py e
    # bookmakers/*.py) — incompatível com o loop asyncio deste listener, por
    # isso despachamos para uma thread separada.
    match: MatchInfo = await asyncio.to_thread(find_match, extracted.jogador1, extracted.jogador2)

    status = BetStatus.AGENDADA.value if match.encontrado else BetStatus.NAO_ENCONTRADA.value

    # jogador2 é NOT NULL no banco. Fica None quando o tipster citou só o
    # favorito (ver extractor.find_favorite_only) E o SofaScore não
    # conseguiu confirmar o adversário sozinho (ambíguo ou sem jogo hoje) —
    # nesse caso o status já fica NAO_ENCONTRADA (match.encontrado=False),
    # então "?" aqui só evita quebrar a constraint, não afeta o app.
    bet = Bet(
        jogador1=match.jogador1_oficial or extracted.jogador1,
        jogador2=match.jogador2_oficial or extracted.jogador2 or "?",
        torneio=match.torneio_oficial or extracted.torneio,
        mercado=extracted.mercado,
        odd=extracted.odd,
        data_hora=match.data_hora,
        links=match.links,
        status=status,
        fonte_texto=extracted.texto_bruto,
        mensagem_id=message.id,
        sofascore_event_id=match.sofascore_event_id,
        unidades=1.0,
    )

    # --- database.py ---------------------------------------------------------
    bet.id = insert_bet(bet)

    # --- notifier.py -----------------------------------------------------------
    await send_bet_notification(bet)


async def run_listener() -> None:
    init_db()
    client = _build_client()

    async with client:
        source_chat = await _resolve_source_chat(client)
        logger.info("Escutando o chat: %s", getattr(source_chat, "title", source_chat))
        await send_plain_message("🎾 Tennis Bet Monitor iniciado e escutando o grupo de tips.")

        @client.on(events.NewMessage(chats=source_chat))
        async def _handler(event: events.NewMessage.Event) -> None:
            try:
                await process_message(event.message)
            except Exception:
                logger.exception("Erro ao processar mensagem %s", event.message.id)

        await client.run_until_disconnected()


async def list_chats() -> None:
    """Utilitário: lista seus diálogos com nome e ID, para achar TELEGRAM_SOURCE_CHAT."""
    client = _build_client()
    async with client:
        async for dialog in client.iter_dialogs():
            print(f"{dialog.id:>15}  |  {dialog.name}")


async def backfill(limit: int) -> None:
    """Reprocessa as últimas `limit` mensagens do grupo — útil para testar o pipeline."""
    init_db()
    client = _build_client()
    async with client:
        source_chat = await _resolve_source_chat(client)
        logger.info("Backfill: buscando as últimas %d mensagens de %s", limit, getattr(source_chat, "title", ""))
        async for message in client.iter_messages(source_chat, limit=limit):
            try:
                await process_message(message)
            except Exception:
                logger.exception("Erro no backfill da mensagem %s", message.id)


async def poll_new_messages() -> None:
    """
    Modo de produção (GitHub Actions): busca todas as mensagens novas desde
    a última execução e sai — em vez de ficar conectado 24/7 (run_listener),
    pensado pra ser chamado por um cron.

    "Última execução" é rastreado via sync_state no Postgres (chave
    _SYNC_STATE_KEY), não em disco local, porque o runner do Actions é uma
    máquina nova a cada execução.
    """
    init_db()
    client = _build_client()
    async with client:
        source_chat = await _resolve_source_chat(client)

        last_id_raw = get_sync_state(_SYNC_STATE_KEY)
        min_id = int(last_id_raw) if last_id_raw else 0

        mensagens = []
        async for message in client.iter_messages(source_chat, min_id=min_id):
            mensagens.append(message)
        # iter_messages devolve do mais novo pro mais antigo — processa na
        # ordem cronológica real, e min_id=0 na 1ª execução processaria o
        # histórico inteiro, então nesse caso só pega a mais recente (não
        # queremos backfill automático surpresa; use --backfill pra isso).
        if min_id == 0 and mensagens:
            mensagens = mensagens[:1]
        mensagens.reverse()

        logger.info(
            "Poll: %d mensagem(ns) nova(s) em %s (min_id=%s)",
            len(mensagens), getattr(source_chat, "title", ""), min_id,
        )

        maior_id = min_id
        for message in mensagens:
            try:
                await process_message(message)
            except Exception:
                logger.exception("Erro ao processar mensagem %s", message.id)
            maior_id = max(maior_id, message.id)

        if maior_id != min_id:
            set_sync_state(_SYNC_STATE_KEY, str(maior_id))


def main() -> None:
    parser = argparse.ArgumentParser(description="Tennis Bet Monitor — listener do Telegram")
    parser.add_argument("--list-chats", action="store_true", help="Lista seus chats e IDs, depois sai.")
    parser.add_argument("--backfill", type=int, metavar="N", help="Reprocessa as últimas N mensagens e sai.")
    parser.add_argument(
        "--poll-once", action="store_true",
        help="Busca mensagens novas desde a última execução e sai (modo produção/cron).",
    )
    args = parser.parse_args()

    if args.list_chats:
        asyncio.run(list_chats())
    elif args.backfill:
        asyncio.run(backfill(args.backfill))
    elif args.poll_once:
        asyncio.run(poll_new_messages())
    else:
        asyncio.run(run_listener())


if __name__ == "__main__":
    main()
