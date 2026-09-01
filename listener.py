"""
listener.py
===========
"Userbot" baseado em Telethon: conecta com a SUA conta pessoal do Telegram
(por isso pede login por telefone na primeira vez) e fica escutando, em
tempo real, novas mensagens no grupo privado de tips configurado em
TELEGRAM_SOURCE_CHAT.

Para cada mensagem nova (com foto e/ou texto), o pipeline é:

    listener.py --(imagem/texto)--> extractor.py --(dados brutos)-->
    matcher.py --(data/hora + link)--> database.py --(salva)-->
    notifier.py --(mensagem formatada)--> seu Telegram privado

Um userbot é necessário aqui (em vez de só o Bot API) porque bots comuns do
Telegram não conseguem ler mensagens de grupos onde eles não têm permissão
explícita de admin/leitura — e muitos grupos de tips não permitem adicionar
bots. Rodando com sua própria conta, o listener enxerga tudo que você já
enxerga como membro.

Uso:
    python listener.py                 # inicia a escuta contínua
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
from telethon.tl.custom.message import Message

from config import settings
from database import bet_exists_for_message, init_db, insert_bet
from extractor import extract_bet_info
from matcher import find_match
from models import Bet, BetStatus, ExtractedBet, MatchInfo
from notifier import send_bet_notification, send_plain_message

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
    session_path = settings.resolve_path(settings.TELEGRAM_SESSION_NAME)
    return TelegramClient(str(session_path), settings.TELEGRAM_API_ID, settings.TELEGRAM_API_HASH)


async def _resolve_source_chat(client: TelegramClient):
    """Aceita tanto @username quanto ID numérico (inclusive negativo) em TELEGRAM_SOURCE_CHAT."""
    raw = settings.TELEGRAM_SOURCE_CHAT.strip()
    if not raw:
        raise RuntimeError("TELEGRAM_SOURCE_CHAT não configurado no .env")
    try:
        chat_id = int(raw)
        return await client.get_entity(chat_id)
    except ValueError:
        return await client.get_entity(raw)


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
        await send_bet_notification(bet)
        return

    # --- matcher.py ---------------------------------------------------------
    # find_match roda Playwright em modo síncrono (sofascore_client.py e
    # bookmakers/*.py) — incompatível com o loop asyncio deste listener, por
    # isso despachamos para uma thread separada.
    match: MatchInfo = await asyncio.to_thread(find_match, extracted.jogador1, extracted.jogador2)

    status = BetStatus.AGENDADA.value if match.encontrado else BetStatus.NAO_ENCONTRADA.value

    bet = Bet(
        jogador1=match.jogador1_oficial or extracted.jogador1,
        jogador2=match.jogador2_oficial or extracted.jogador2,
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Tennis Bet Monitor — listener do Telegram")
    parser.add_argument("--list-chats", action="store_true", help="Lista seus chats e IDs, depois sai.")
    parser.add_argument("--backfill", type=int, metavar="N", help="Reprocessa as últimas N mensagens e sai.")
    args = parser.parse_args()

    if args.list_chats:
        asyncio.run(list_chats())
    elif args.backfill:
        asyncio.run(backfill(args.backfill))
    else:
        asyncio.run(run_listener())


if __name__ == "__main__":
    main()
