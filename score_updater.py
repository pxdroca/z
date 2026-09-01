"""
score_updater.py
=================
Processo separado (rode em paralelo ao listener.py e ao streamlit) que
acompanha o placar/resultado ao vivo das apostas já confirmadas no
SofaScore, usando o sofascore_event_id salvo por listener.py/matcher.py.

Uso:
    python score_updater.py                  # loop contínuo (padrão: ~3 min entre ciclos)
    python score_updater.py --once           # roda um ciclo só e sai (útil pra testar)
    python score_updater.py --interval 120   # ciclo a cada 120s em vez do padrão

A cada ciclo:
  1. Busca no banco toda aposta com status "agendada" ou "ao_vivo" e um
     sofascore_event_id conhecido (list_trackable_bets, em database.py).
  2. Para cada uma, consulta get_event_status() (sofascore_client.py):
       - "inprogress" e a aposta ainda estava "agendada" -> promove pra "ao_vivo".
       - "finished" -> grava placar_final + vencedor_partida, muda o status
         para "encerrada" e manda uma notificação avisando o resultado.
       - "notstarted" -> não faz nada (ainda não começou).
  3. Espera um atraso aleatório entre cada partida consultada (mesmo espírito
     "educado" dos outros módulos que batem no SofaScore/casas de apostas).

Não decide se A APOSTA em si ganhou ou perdeu (isso depende do mercado
específico, ex: "vencedor do 1º set" vs "vencedor da partida") — só informa
o resultado da partida, cabe a você conferir se bateu com sua aposta.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import sys
import time

from config import settings
from database import init_db, list_trackable_bets, update_score_result
from models import Bet, BetStatus
from notifier import send_plain_message
from sofascore_client import EventStatus, get_event_status

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(settings.resolve_path("logs") / "score_updater.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("score_updater")

DEFAULT_INTERVAL_SECONDS = 180  # 3 minutos


def _polite_delay() -> None:
    """Mesmo espírito dos outros módulos: não bater no SofaScore feito metralhadora."""
    time.sleep(random.uniform(1.0, 3.0))


def _vencedor_nome(bet: Bet, evt: EventStatus) -> str | None:
    """Traduz "home"/"away" pro nome real do jogador. Prioriza os nomes
    oficiais do SofaScore (evt) — mais confiáveis que os salvos na aposta,
    que podem ter vindo só do OCR se o matcher não tiver confirmado."""
    if evt.vencedor == "home":
        return evt.jogador1_nome or bet.jogador1
    if evt.vencedor == "away":
        return evt.jogador2_nome or bet.jogador2
    return None


def _processar_bet(bet: Bet) -> None:
    evt = get_event_status(bet.sofascore_event_id)
    if evt is None:
        logger.warning("Aposta #%s: falha ao consultar evento %s, tentando de novo no próximo ciclo.", bet.id, bet.sofascore_event_id)
        return

    if evt.status == "finished":
        vencedor = _vencedor_nome(bet, evt)
        update_score_result(
            bet.id,
            status=BetStatus.ENCERRADA.value,
            placar_final=evt.placar,
            vencedor_partida=vencedor,
        )
        logger.info("Aposta #%s: partida encerrada — %s venceu %s", bet.id, vencedor, evt.placar)

        odd_txt = f"{bet.odd:.2f}" if bet.odd is not None else "?"
        texto = (
            f"🏁 Partida encerrada: {vencedor or '?'} venceu {evt.placar or '(placar indisponível)'} — "
            f"não esqueça de conferir se sua aposta ({bet.mercado or 'mercado não identificado'}, "
            f"odd {odd_txt}) bateu."
        )
        asyncio.run(send_plain_message(texto))

    elif evt.status == "inprogress" and bet.status == BetStatus.AGENDADA.value:
        update_score_result(bet.id, status=BetStatus.AO_VIVO.value)
        logger.info("Aposta #%s: partida começou (ao vivo). Placar parcial: %s", bet.id, evt.placar)

    else:
        logger.debug("Aposta #%s: sem mudança (status SofaScore=%s)", bet.id, evt.status)


def run_once() -> int:
    """Roda um ciclo: consulta todas as apostas rastreáveis. Devolve quantas foram processadas."""
    apostas = list_trackable_bets()
    logger.info("Ciclo iniciado: %d aposta(s) para acompanhar.", len(apostas))
    for bet in apostas:
        try:
            _processar_bet(bet)
        except Exception:
            logger.exception("Erro ao processar aposta #%s (evento %s)", bet.id, bet.sofascore_event_id)
        _polite_delay()
    return len(apostas)


def run_loop(interval_seconds: int) -> None:
    logger.info("score_updater iniciado — ciclo a cada ~%ss.", interval_seconds)
    while True:
        run_once()
        time.sleep(interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Acompanha placar/resultado ao vivo via SofaScore.")
    parser.add_argument("--once", action="store_true", help="Roda um ciclo só e sai (útil pra testar).")
    parser.add_argument(
        "--interval", type=int, default=DEFAULT_INTERVAL_SECONDS,
        help=f"Segundos entre ciclos no modo contínuo (padrão: {DEFAULT_INTERVAL_SECONDS}).",
    )
    args = parser.parse_args()

    init_db()

    if args.once:
        run_once()
    else:
        run_loop(args.interval)


if __name__ == "__main__":
    main()
