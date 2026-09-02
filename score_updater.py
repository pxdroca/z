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
  3. Reconsulta o matcher.py para toda aposta com status "nao_encontrada"
     (list_unmatched_bets, em database.py) — necessário desde que o
     listener.py passou a rodar em lote (poll periódico, em vez de processar
     cada mensagem instantaneamente): uma tip cujo jogo o SofaScore ainda não
     tinha listado no momento do poll não tem mais uma "próxima mensagem"
     natural que a reprocesse, então esse retry cobre esse caso.
  4. Espera um atraso aleatório entre cada partida consultada (mesmo espírito
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
from datetime import datetime
from typing import Optional

from config import settings
from database import init_db, list_trackable_bets, list_unmatched_bets, update_match_info, update_score_result
from matcher import find_match
from models import Bet, BetStatus
from notifier import send_plain_message
from scores365_client import find_status_by_names
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


def _consultar_status(bet: Bet) -> Optional[EventStatus]:
    """
    SofaScore é a fonte primária (mesmo event_id salvo desde o matcher.py).
    Se falhar, tenta 365scores como fallback, buscando por nome dos
    jogadores — ver scores365_client.py para o motivo (SofaScore às vezes
    bloqueia TODO o IP do runner do GitHub Actions com 403, não só um
    request pontual, e o retry de sofascore_client não ajuda nesse caso
    porque o IP continua o mesmo).
    """
    evt = get_event_status(bet.sofascore_event_id)
    if evt is not None:
        return evt

    logger.warning("Aposta #%s: SofaScore falhou (evento %s), tentando 365scores.", bet.id, bet.sofascore_event_id)
    try:
        evt = find_status_by_names(bet.jogador1, bet.jogador2, threshold=settings.SUPERBET_FUZZY_THRESHOLD)
    except Exception:
        logger.exception("Aposta #%s: 365scores também falhou.", bet.id)
        return None
    if evt is not None:
        logger.info("Aposta #%s: status obtido via 365scores (fallback).", bet.id)
    return evt


def _processar_bet(bet: Bet) -> Optional[str]:
    """Devolve o texto de notificação a enviar (partida encerrada), ou None
    se não há nada a notificar. Não envia a notificação aqui — ver run_once,
    que acumula os textos de todo o ciclo e manda todos juntos num único
    asyncio.run() (chamar asyncio.run() uma vez por partida, dentro deste
    loop síncrono, esgotava o pool de conexões HTTP do bot do Telegram —
    _get_bot() em notifier.py cacheia o Bot/httpx.AsyncClient entre
    chamadas, e cada asyncio.run() novo cria um event loop novo, incompatível
    com um client já vinculado ao loop anterior — bug real visto em
    produção: TimedOut/PoolTimeout ao notificar mais de 1 partida por ciclo)."""
    evt = _consultar_status(bet)
    if evt is None:
        logger.warning("Aposta #%s: falha ao consultar evento %s (SofaScore e 365scores), tentando de novo no próximo ciclo.", bet.id, bet.sofascore_event_id)
        return None

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
        return (
            f"🏁 Partida encerrada: {vencedor or '?'} venceu {evt.placar or '(placar indisponível)'} — "
            f"não esqueça de conferir se sua aposta ({bet.mercado or 'mercado não identificado'}, "
            f"odd {odd_txt}) bateu."
        )

    elif evt.status == "inprogress" and bet.status == BetStatus.AGENDADA.value:
        update_score_result(bet.id, status=BetStatus.AO_VIVO.value)
        logger.info("Aposta #%s: partida começou (ao vivo). Placar parcial: %s", bet.id, evt.placar)

    else:
        logger.debug("Aposta #%s: sem mudança (status SofaScore=%s)", bet.id, evt.status)
    return None


async def _enviar_notificacoes(textos: list[str]) -> None:
    for texto in textos:
        await send_plain_message(texto)


def _retentar_bet_nao_encontrada(bet: Bet) -> None:
    """Reconsulta o matcher.py pra uma aposta que não achou o confronto na
    primeira tentativa — ver docstring do módulo pra contexto.

    bet.jogador2 == "?" é o placeholder que listener.py salva quando só o
    favorito foi citado e o SofaScore não confirmou o adversário na 1ª
    tentativa (ver listener.py, jogador2 NOT NULL no banco) — precisa virar
    None aqui, senão find_match tentaria achar um confronto contra um
    jogador literal chamado "?" (bug real: "?" é truthy em Python, então
    `if jogador2:` em matcher.find_match não detectava o placeholder e
    nunca reencaminhava pra find_canonical_match_by_name)."""
    jogador2 = bet.jogador2 if bet.jogador2 != "?" else None
    match = find_match(bet.jogador1, jogador2)
    if not match.encontrado:
        logger.debug("Aposta #%s: ainda não encontrada (%s x %s)", bet.id, bet.jogador1, bet.jogador2)
        return

    status = BetStatus.AO_VIVO.value if (match.data_hora and match.data_hora <= datetime.now()) else BetStatus.AGENDADA.value
    update_match_info(bet.id, data_hora=match.data_hora, links=match.links, status=status)
    logger.info("Aposta #%s: confronto encontrado no retry — %s x %s", bet.id, bet.jogador1, bet.jogador2)


def run_once() -> int:
    """Roda um ciclo: consulta todas as apostas rastreáveis e retenta as não
    encontradas. Devolve quantas foram processadas no total."""
    apostas = list_trackable_bets()
    logger.info("Ciclo iniciado: %d aposta(s) para acompanhar.", len(apostas))
    notificacoes: list[str] = []
    for bet in apostas:
        try:
            texto = _processar_bet(bet)
            if texto:
                notificacoes.append(texto)
        except Exception:
            logger.exception("Erro ao processar aposta #%s (evento %s)", bet.id, bet.sofascore_event_id)
        _polite_delay()

    if notificacoes:
        asyncio.run(_enviar_notificacoes(notificacoes))

    nao_encontradas = list_unmatched_bets()
    if nao_encontradas:
        logger.info("Retentando %d aposta(s) não encontrada(s).", len(nao_encontradas))
    for bet in nao_encontradas:
        try:
            _retentar_bet_nao_encontrada(bet)
        except Exception:
            logger.exception("Erro ao retentar aposta #%s (%s x %s)", bet.id, bet.jogador1, bet.jogador2)
        _polite_delay()

    return len(apostas) + len(nao_encontradas)


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
