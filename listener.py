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
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from typing import Optional

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
from database import (
    bet_exists_for_message,
    get_sync_state,
    init_db,
    insert_bet,
    list_apostas_ativas,
    set_sync_state,
    update_resultado,
)
from extractor import detectar_aviso_cashout, extract_bet_info
from matcher import build_enabled_adapters, find_match, horario_da_primeira_selecao
from models import Bet, BetStatus, Esporte, ExtractedBet, MatchInfo, ResultadoAposta, TipoAposta
from nameutils import names_match
from notifier import send_bet_notification, send_plain_message

# Chave usada em sync_state (database.py) para lembrar até onde o polling já
# processou — necessário porque o runner do GitHub Actions não tem disco
# persistente entre execuções (ver poll_new_messages()).
_SYNC_STATE_KEY = "listener_last_message_id"

# Quanto de histórico o primeiro poll de um chat novo aceita processar.
# Como o grupo é recriado todo dia, "chat novo" é rotina, não exceção — a
# janela precisa cobrir as tips que o tipster já postou antes do primeiro
# poll do dia (ele costuma mandar as bets de madrugada, ver mensagens
# 98-103 de 03/09/2026), sem abrir a porta pra reprocessar um grupo antigo
# inteiro caso TELEGRAM_SOURCE_CHAT aponte pra outro lugar.
#
# 30h e não 12h: o grupo do dia seguinte é criado no FIM DO DIA anterior e
# já recebe tips ("vou adiantar as nossas bets de madrugada/amanhã cedo
# agora logo", 04/09/2026 21:02). O que a janela precisa cobrir é o
# PIPELINE PARADO, não a entrada tardia no grupo: se as tips foram
# postadas às 21h e o workflow ficou fora do ar (falha, cron não
# disparado) até as 11h do dia seguinte, são 14h — e 12h descartariam
# tudo silenciosamente.
#
# Medido no grupo real (04/09/2026): a entrada no grupo acontece na
# véspera (msg #88, 03/09 23:22) e as tips vêm depois — a dos Lions foi
# processada às 02:43. E o Telegram entrega até o que veio ANTES da
# entrada: a msg #1 desse grupo é de 03/09 17:35, quase 6h antes de
# entrar, e o Telethon a lê. Então o histórico nunca foi o limite; a
# janela era.
#
# 30h cobre uma paralisação de mais de um dia e ainda descarta um grupo
# genuinamente antigo (o de 2 dias atrás já está fora, e
# _JANELA_GRUPOS_ATIVOS nem o entrega pra cá).
_JANELA_PRIMEIRO_POLL = timedelta(hours=30)

# Quando TELEGRAM_SOURCE_CHAT é um PREFIXO de nome, quais grupos com esse
# prefixo continuam sendo lidos: todos os que tiveram atividade nas últimas
# 36h — na prática, o de hoje e o de amanhã.
#
# Por que mais de um: o tipster cria o grupo do dia seguinte no FIM DO DIA e
# já manda tips nele, enquanto o grupo de hoje ainda está ativo (resultados,
# avisos de cash-out, conversa). Nessa janela existem dois grupos vivos ao
# mesmo tempo, e escolher "o mais recente" é cara ou coroa: se a última
# mensagem caiu no grupo de hoje, as tips de amanhã não são lidas; se caiu
# no de amanhã, os avisos de cash-out de hoje é que se perdem — e cada poll
# pode decidir diferente do anterior, então dá pra alternar entre os dois
# erros na mesma noite.
#
# Ler os dois resolve os dois casos e não custa quase nada: cada chat tem seu
# próprio ponteiro em sync_state, e a idempotência é por (chat_id,
# mensagem_id), então nada é reprocessado.
_JANELA_GRUPOS_ATIVOS = timedelta(hours=36)

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


async def _resolver_chats_de_origem(client: TelegramClient) -> list:
    """
    Resolve os grupos de tips a partir de TELEGRAM_SOURCE_CHAT, em 3 formatos
    possíveis:

      1. ID numérico fixo (ex: "-1001234567890") — grupo permanente.
      2. @username fixo (ex: "meugrupo_tips") — grupo permanente.
      3. Prefixo de nome (ex: "Cansadão Apostas") — usado quando o grupo é
         recriado periodicamente com um sufixo variável no nome (ex: data:
         "Cansadão Apostas 31/08", "Cansadão Apostas 01/09").

    Devolve uma LISTA (plural) de propósito. Nos casos 1 e 2 ela tem um
    item só. No caso 3 ela tem todos os grupos com aquele prefixo ativos
    nas últimas _JANELA_GRUPOS_ATIVOS — ver o comentário lá em cima para o
    porquê: no fim do dia o grupo de hoje e o de amanhã existem ao mesmo
    tempo, e ficar com só um deles perde mensagens do outro.

    Como diferenciamos os casos: se o valor não é um ID numérico nem existe
    um chat com esse @username/nome exato, tratamos como prefixo (caso 3).
    """
    raw = settings.TELEGRAM_SOURCE_CHAT.strip()
    if not raw:
        raise RuntimeError("TELEGRAM_SOURCE_CHAT não configurado no .env")

    # Caso 1: ID numérico.
    try:
        chat_id = int(raw)
        return [await client.get_entity(chat_id)]
    except ValueError:
        pass

    # Caso 2: @username fixo (só faz sentido tentar se não tiver espaços —
    # username do Telegram nunca tem espaço, prefixo de nome de grupo tem).
    if " " not in raw:
        try:
            return [await client.get_entity(raw)]
        except (ValueError, TypeError):
            pass

    # Caso 3: prefixo de nome — todos os diálogos cujo título comece com o
    # prefixo configurado.
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

    # dialog.date é a data da última mensagem/atividade.
    limite = datetime.now(timezone.utc) - _JANELA_GRUPOS_ATIVOS
    ativos = [d for d in candidatos if d.date and d.date >= limite]
    if not ativos:
        # Nenhum grupo teve atividade recente (fim de semana sem jogo, ou o
        # tipster ainda não postou hoje). Cair pro mais recente conhecido é
        # melhor que devolver lista vazia e não ler nada.
        ativos = [max(candidatos, key=lambda d: d.date)]

    # Mais antigo primeiro: quando o grupo de hoje e o de amanhã estão
    # ativos, as mensagens são lidas na ordem cronológica dos grupos.
    ativos.sort(key=lambda d: d.date)

    logger.info(
        "Grupos com prefixo %r sendo lidos: %s (de %d encontrado(s) no total)",
        raw, [d.name for d in ativos], len(candidatos),
    )

    await _sair_de_grupos_velhos(client, candidatos, ativos)
    return [d.entity for d in ativos]


# Depois de quanto tempo sem atividade sair do grupo do dia.
#
# Bem maior que _JANELA_GRUPOS_ATIVOS (36h) de propósito: o grupo precisa
# primeiro sair da janela de leitura e só depois ser abandonado, senão uma
# corrida entre as duas regras poderia nos tirar de um grupo que ainda
# tinha aviso de cash-out por vir. A margem de 12h cobre isso com folga.
_JANELA_SAIR_DO_GRUPO = timedelta(hours=48)


async def _sair_de_grupos_velhos(client: TelegramClient, candidatos: list, ativos: list) -> None:
    """Sai dos grupos do prefixo que já passaram de _JANELA_SAIR_DO_GRUPO.

    O tipster cria um grupo por dia e nunca apaga os antigos, então a lista
    de diálogos só cresce — e cada poll varre todos eles. Sair dos que já
    não têm nada a dizer mantém a varredura curta.

    Conservador de propósito, porque sair é irreversível sem novo convite:
      - nunca sai de um grupo que está sendo lido agora (`ativos`);
      - exige que a última atividade seja mais velha que 48h;
      - falha de saída é logada e ignorada (não derruba o poll).
    """
    ids_ativos = {d.id for d in ativos}
    limite = datetime.now(timezone.utc) - _JANELA_SAIR_DO_GRUPO

    for dialog in candidatos:
        if dialog.id in ids_ativos:
            continue
        if not dialog.date or dialog.date >= limite:
            continue
        try:
            await client.delete_dialog(dialog.entity)
            logger.info(
                "Saí do grupo %r (última atividade em %s, mais de %s atrás).",
                dialog.name, dialog.date.isoformat(), _JANELA_SAIR_DO_GRUPO,
            )
        except Exception:
            logger.exception("Não consegui sair do grupo %r — seguindo.", dialog.name)


async def _processar_aviso_cashout(mensagem_id: int, chat_id: Optional[int], nome_citado: str, caption: str) -> None:
    """
    Casa o nome citado num aviso de cash-out ("Fulano está pago!") com
    apostas em andamento (agendada/ao_vivo) daquele jogador — pode haver mais
    de uma (ex: pré-jogo "vencedor da partida" + ao vivo "vencer o 2º set"
    no mesmo confronto, como já visto em produção), e TODAS batem como
    green/cashout: o aviso é sobre o jogador ter sido "pago" no geral, o
    tipster não costuma discriminar qual aposta específica foi encerrada.

    Salva de qualquer forma um registro erro_extracao pra essa mensagem
    (idempotência via bet_exists_for_message / auditoria), independente de
    ter achado alguma aposta pra casar ou não.
    """
    ativas = list_apostas_ativas()
    casadas = [b for b in ativas if names_match(nome_citado, b.jogador1, threshold=80) or names_match(nome_citado, b.jogador2, threshold=80)]

    for bet in casadas:
        update_resultado(bet.id, ResultadoAposta.CASHOUT.value)
        logger.info("Aposta #%s: cash-out antecipado confirmado pelo tipster (%r).", bet.id, nome_citado)

    if casadas:
        nomes = ", ".join(f"{b.jogo} ({b.mercado or '?'})" for b in casadas)
        await send_plain_message(f"💰 Cash-out confirmado pelo tipster — {nomes}")
    else:
        logger.warning(
            "Aviso de cash-out (%r) não casou com nenhuma aposta ativa. Texto: %r",
            nome_citado, caption[:200],
        )

    # Salva um registro de auditoria (não é uma tip nova, mas idempotência
    # via bet_exists_for_message depende de toda mensagem processada deixar
    # um rastro no banco — senão reprocessaria esse aviso indefinidamente).
    bet_registro = Bet(
        jogador1=nome_citado,
        jogador2="?",
        mercado=f"[aviso de cash-out — {len(casadas)} aposta(s) casada(s)]",
        status=BetStatus.ERRO_EXTRACAO.value,
        fonte_texto=caption,
        mensagem_id=mensagem_id,
        chat_id=chat_id,
        unidades=1.0,
        esporte=casadas[0].esporte if casadas else Esporte.TENIS.value,
    )
    insert_bet(bet_registro)


async def process_message(message: Message) -> None:
    """
    Núcleo do pipeline: roda para cada mensagem nova (ou de backfill).
    Idempotente — se a msg já foi processada (mesmo message.id), é ignorada.
    """
    # A idempotência é por (chat_id, message.id) — o id da mensagem só é
    # único DENTRO de um chat, e o grupo de tips é recriado todo dia (ver
    # database._migrar_unicidade_da_mensagem). Log em info, não debug: um
    # "já processada" inesperado era invisível em produção justamente
    # quando mais importava.
    chat_id = getattr(message, "chat_id", None)
    if bet_exists_for_message(message.id, chat_id):
        logger.info("Mensagem %s do chat %s já processada, ignorando.", message.id, chat_id)
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

    # --- aviso de cash-out antecipado ("Fulano está pago!"/"...Cash") ------
    # Só quando não há imagem (o padrão real observado é sempre texto puro
    # solto no grupo, nunca junto de um print) — ver
    # extractor.detectar_aviso_cashout para o porquê e os exemplos reais.
    # Não é uma tip nova: casa por nome com uma aposta já em andamento
    # (agendada/ao_vivo) e marca resultado=cashout na hora, sem esperar o
    # jogo terminar — o tipster já confirmou green por cash-out, ganhe ou
    # perca o jogador depois.
    if caption and not image_path:
        nome_citado = detectar_aviso_cashout(caption)
        if nome_citado:
            await _processar_aviso_cashout(message.id, chat_id, nome_citado, caption)
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
            chat_id=chat_id,
            unidades=1.0,
            esporte=extracted.esporte,
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
        # O mercado que o extractor leu do print vem PRIMEIRO. Antes o
        # rótulo era sempre "Múltipla (N seleções)", que descarta uma
        # informação que costuma estar bem visível no print — numa múltipla
        # de 2 pernas dá pra ler o mercado sem esforço. O genérico fica só
        # como fallback, pra quando o print não deixa claro.
        mercado_multipla = extracted.mercado or (
            "Múltipla" + (f" ({len(extracted.selecoes)} seleções)" if extracted.selecoes else "")
        )

        # Horário: o do PRIMEIRO jogo da múltipla. Sem isso ela nasce sem
        # data_hora e cai na seção "horário não encontrado" da imagem, além
        # de não ter onde ser ordenada no painel. Ver
        # matcher.horario_da_primeira_selecao.
        data_hora_multipla = await asyncio.to_thread(
            partial(
                horario_da_primeira_selecao,
                extracted.selecoes,
                extracted.esporte,
                referencia=message.date,
            )
        )

        bet = Bet(
            jogador1=", ".join(extracted.selecoes) if extracted.selecoes else "Múltipla (ver print original)",
            jogador2="",
            mercado=mercado_multipla,
            odd=extracted.odd,
            data_hora=data_hora_multipla,
            links=links,
            status=BetStatus.AGENDADA.value,
            fonte_texto=extracted.texto_bruto,
            mensagem_id=message.id,
            chat_id=chat_id,
            unidades=1.0,
            tipo_aposta=TipoAposta.MULTIPLA.value,
            selecoes=extracted.selecoes,
            esporte=extracted.esporte,
        )
        bet.id = insert_bet(bet)
        await send_bet_notification(bet)
        return

    # --- matcher.py ---------------------------------------------------------
    # find_match roda Playwright em modo síncrono (sofascore_client.py e
    # bookmakers/*.py) — incompatível com o loop asyncio deste listener, por
    # isso despachamos para uma thread separada.
    # A odd extraída vai junto: quando o tipster cita só o favorito, ela
    # desempata entre o jogo de simples e o de duplas do mesmo jogador na
    # Superbet (ver matcher.buscar_confronto_na_superbet).
    # referencia = data da MENSAGEM, não "agora": num backfill (ou num poll
    # que atrasou/represou mensagens) "agora" pode ser horas depois da tip,
    # e a busca do confronto é uma janela de dias ao redor da referência —
    # ancorar na tip é o que mantém a janela sobre o jogo certo.
    match: MatchInfo = await asyncio.to_thread(
        partial(
            find_match,
            extracted.jogador1, extracted.jogador2, extracted.esporte,
            referencia=message.date,
            odd_tip=extracted.odd,
            # Texto original: detecta tip de DUPLAS ("na duplas"), que o
            # matcher descarta por padrão — ver matcher.tip_e_de_duplas.
            texto_tip=extracted.texto_bruto or caption,
        )
    )

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
        chat_id=chat_id,
        sofascore_event_id=match.sofascore_event_id,
        unidades=1.0,
        esporte=extracted.esporte,
    )

    # --- database.py ---------------------------------------------------------
    bet.id = insert_bet(bet)

    # --- notifier.py -----------------------------------------------------------
    await send_bet_notification(bet)


async def run_listener() -> None:
    init_db()
    client = _build_client()

    async with client:
        chats = await _resolver_chats_de_origem(client)
        logger.info("Escutando: %s", [getattr(c, "title", c) for c in chats])
        await send_plain_message("🎾 Tennis Bet Monitor iniciado e escutando o grupo de tips.")

        @client.on(events.NewMessage(chats=chats))
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
        for chat in await _resolver_chats_de_origem(client):
            logger.info("Backfill: buscando as últimas %d mensagens de %s", limit, getattr(chat, "title", ""))
            async for message in client.iter_messages(chat, limit=limit):
                try:
                    await process_message(message)
                except Exception:
                    logger.exception("Erro no backfill da mensagem %s", message.id)


async def poll_new_messages() -> None:
    """
    Modo de produção (GitHub Actions): busca todas as mensagens novas desde
    a última execução e sai — em vez de ficar conectado 24/7 (run_listener),
    pensado pra ser chamado por um cron.

    "Última execução" é rastreado via sync_state no Postgres, POR CHAT (a
    chave inclui o chat.id) — bug real encontrado em produção: quando
    TELEGRAM_SOURCE_CHAT é um prefixo de nome (ver _resolver_chats_de_origem)
    e o grupo é recriado a cada dia, cada grupo novo é um chat_id diferente
    do Telegram com sua PRÓPRIA numeração de mensagens (reinicia perto de
    1). Guardar um único last_message_id global (sem o chat_id) fazia o
    poll do dia seguinte reaplicar o min_id do grupo de ONTEM (ex: 326) no
    grupo de HOJE — como as mensagens novas do grupo novo têm ids bem
    menores que esse min_id herdado, ficavam todas escondidas pra sempre,
    silenciosamente (sem erro, só "0 mensagens novas" no log).

    Lê TODOS os grupos ativos, não só um: no fim do dia o grupo de amanhã
    já existe (com tips) enquanto o de hoje ainda recebe resultado e aviso
    de cash-out — ver _JANELA_GRUPOS_ATIVOS.
    """
    init_db()
    client = _build_client()
    async with client:
        chats = await _resolver_chats_de_origem(client)
        for chat in chats:
            try:
                await _poll_chat(client, chat)
            except Exception:
                logger.exception("Erro ao ler o chat %s", getattr(chat, "title", chat))


async def _poll_chat(client: TelegramClient, chat) -> None:
    """Lê as mensagens novas de UM chat e atualiza o ponteiro dele."""
    sync_key = f"{_SYNC_STATE_KEY}:{chat.id}"

    last_id_raw = get_sync_state(sync_key)
    min_id = int(last_id_raw) if last_id_raw else 0

    mensagens = []
    async for message in client.iter_messages(chat, min_id=min_id):
        mensagens.append(message)

    # min_id=0 = primeira execução NESTE chat. Não dá pra processar o
    # histórico inteiro (backfill surpresa de um grupo antigo), mas
    # também não dá pra pegar só a última mensagem: como o grupo é
    # recriado todo dia (ver docstring), "primeira execução" acontece
    # TODO DIA, e o corte em [:1] descartava tudo que o tipster já
    # tinha postado antes do primeiro poll do grupo novo.
    #
    # Bug real (03/09/2026): 6 tips (msgs 98-103, 01:47-01:53) foram
    # perdidas silenciosamente porque no primeiro poll do grupo de hoje
    # a mensagem mais recente era um comentário solto (id 124) — as
    # apostas ficaram "atrás" dela e nunca foram vistas. Tiveram que
    # ser reprocessadas à mão.
    #
    # Janela de tempo em vez de contagem: pega o que é plausivelmente
    # "do dia corrente" e ignora histórico antigo de verdade.
    if min_id == 0 and mensagens:
        limite = datetime.now(timezone.utc) - _JANELA_PRIMEIRO_POLL
        recentes = [m for m in mensagens if m.date and m.date >= limite]
        logger.info(
            "Primeiro poll em %s: %d de %d mensagem(ns) dentro da janela de %s.",
            getattr(chat, "title", ""), len(recentes), len(mensagens),
            _JANELA_PRIMEIRO_POLL,
        )
        mensagens = recentes

    # iter_messages devolve do mais novo pro mais antigo — processa na
    # ordem cronológica real.
    mensagens.reverse()

    logger.info(
        "Poll: %d mensagem(ns) nova(s) em %s (min_id=%s)",
        len(mensagens), getattr(chat, "title", ""), min_id,
    )

    # Ponteiro gravado A CADA mensagem, não só no fim do loop.
    #
    # Bug real (05/09/2026): no primeiro poll de um grupo novo a janela de
    # 30h traz dezenas de mensagens, cada uma passando por OCR/LLM. O
    # workflow tem `timeout-minutes: 4` — quando estourava no meio, o
    # ponteiro nunca era gravado e o poll seguinte recomeçava do zero,
    # reprocessando as mesmas mensagens. O resultado era "uma aposta de
    # cada vez, muito devagar", que foi exatamente o sintoma observado.
    #
    # Gravando a cada mensagem, um ciclo interrompido não perde o
    # progresso: o próximo continua de onde parou. As mensagens são
    # processadas em ordem crescente de id (ver reverse() acima), então o
    # ponteiro nunca pula uma mensagem não processada.
    maior_id = min_id
    for message in mensagens:
        try:
            await process_message(message)
        except Exception:
            logger.exception("Erro ao processar mensagem %s", message.id)
        if message.id > maior_id:
            maior_id = message.id
            set_sync_state(sync_key, str(maior_id))


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
