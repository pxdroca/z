"""
notifier.py
===========
Envia o "card" formatado da aposta para o seu chat privado no Telegram,
usando a Bot API (gratuita, sem limite de uso normal). Agora com um botão
inline por casa de apostas habilitada (Superbet/Betano/bet365/...), usando
o teclado inline nativo do Telegram (bem melhor que um link de texto).

Por que um Bot separado do userbot (Telethon)? Porque o listener.py precisa
de uma conta de USUÁRIO para ler um grupo privado do qual você já participa
(a Bot API não consegue ler histórico de grupos privados como membro comum
igual um userbot consegue) — mas para RECEBER notificações, um Bot simples
é mais seguro e simples: ele só fala com você, não precisa de sessão de
usuário, e o token pode ser revogado a qualquer momento pelo @BotFather.
"""

from __future__ import annotations

import logging
from typing import Optional

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from config import settings
from models import Bet

logger = logging.getLogger(__name__)

_bot: Optional[Bot] = None


def _get_bot() -> Bot:
    global _bot
    if _bot is None:
        if not settings.TELEGRAM_BOT_TOKEN:
            raise RuntimeError("TELEGRAM_BOT_TOKEN não configurado no .env")
        _bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    return _bot


def _escape_markdown_v2(text: str) -> str:
    """Escapa caracteres especiais do MarkdownV2 do Telegram."""
    especiais = r"_*[]()~`>#+-=|{}.!"
    for ch in especiais:
        text = text.replace(ch, f"\\{ch}")
    return text


def format_bet_card(bet: Bet) -> str:
    """Monta o texto do card em MarkdownV2 (os links viram botões, não texto — veja build_keyboard)."""
    jogo = _escape_markdown_v2(bet.jogo)  # bet.jogo já formata direito pra simples e pra múltipla (ver models.py)
    torneio = _escape_markdown_v2(bet.torneio or "não identificado")
    mercado = _escape_markdown_v2(bet.mercado or "não identificado")
    odd_txt = _escape_markdown_v2(f"{bet.odd:.2f}" if bet.odd is not None else "?")

    if bet.data_hora:
        data_txt = _escape_markdown_v2(bet.data_hora.strftime("%d/%m/%Y às %H:%M"))
    else:
        data_txt = "não encontrada ⚠️"

    status_emoji = {
        "agendada": "🟢",
        "ao_vivo": "🔴",
        "nao_encontrada": "⚠️",
        "erro_extracao": "❌",
        "encerrada": "⏹️",
    }.get(bet.status, "ℹ️")

    eh_multipla = bet.tipo_aposta == "multipla"
    titulo = "🎾 *Nova Múltipla Detectada*" if eh_multipla else "🎾 *Nova Tip Detectada*"
    rotulo_jogo = "*Seleções:*" if eh_multipla else "*Jogo:*"

    linhas = [
        f"{titulo} {status_emoji}",
        "",
        f"{rotulo_jogo} {jogo}",
    ]
    # torneio/data específicos não existem numa múltipla (são vários jogos
    # diferentes) — omite essas linhas em vez de mostrar "não identificado".
    if not eh_multipla:
        linhas.append(f"*Torneio:* {torneio}")
    linhas.append(f"*Mercado:* {mercado}")
    linhas.append(f"*Odd:* {odd_txt}")
    if not eh_multipla:
        linhas.append(f"*Data/Hora:* {data_txt}")
    return "\n".join(linhas)


def build_keyboard(bet: Bet) -> Optional[InlineKeyboardMarkup]:
    """
    Monta um botão por casa de apostas em bet.links. Cada botão mostra se o
    link é exato (🎯) ou aproximado/torneio-dia (📍), pra você saber o que
    esperar antes de clicar.
    """
    if not bet.links:
        return None
    linhas = []
    for info in bet.links.values():
        icone = "🎯" if info.get("exato") else "📍"
        texto = f"{icone} {info.get('nome', '?')}"
        linhas.append([InlineKeyboardButton(text=texto, url=info["url"])])
    return InlineKeyboardMarkup(linhas) if linhas else None


async def send_bet_notification(bet: Bet) -> None:
    """Envia o card da aposta para o chat privado configurado."""
    if not settings.TELEGRAM_BOT_CHAT_ID:
        logger.warning("TELEGRAM_BOT_CHAT_ID não configurado — pulando notificação.")
        return

    bot = _get_bot()
    texto = format_bet_card(bet)
    teclado = build_keyboard(bet)

    try:
        await bot.send_message(
            chat_id=settings.TELEGRAM_BOT_CHAT_ID,
            text=texto,
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=teclado,
        )
        logger.info("Notificação enviada para o chat %s (aposta #%s)", settings.TELEGRAM_BOT_CHAT_ID, bet.id)
    except Exception:
        logger.exception("Falha ao enviar notificação do Telegram para a aposta #%s", bet.id)


async def send_plain_message(texto: str) -> None:
    """Utilitário simples para mandar avisos gerais (ex: 'listener iniciado')."""
    if not settings.TELEGRAM_BOT_CHAT_ID:
        return

    bot = _get_bot()

    try:
        await bot.send_message(chat_id=settings.TELEGRAM_BOT_CHAT_ID, text=texto)
    except Exception:
        logger.exception("Falha ao enviar mensagem simples do Telegram.")
