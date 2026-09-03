"""
scripts/fix_bets_once.py
=========================
Correção pontual (rodado uma única vez via workflow_dispatch, ver
.github/workflows/fix-bets-once.yml) das apostas #67 e #69, que ficaram
"não encontrada" porque o Gemini caiu (503) no momento em que essas 2 tips
chegaram, e o fallback da época (antes da correção em extractor.py) ignorava
a imagem por completo — o nome extraído da legenda ficou truncado
("Moro canas" sem sobrenome/adversário, "Kilian" sem sobrenome), então nem
Superbet nem SofaScore conseguiram casar.

Nomes corretos, confirmados lendo os prints originais:
  #67: Moro Cañas vencer o 2º set (adversário: Cecchinato)
  #69: Kilian Feldbausch vencer o 2º set (adversário: Andrej Nedic)

Corrige jogador1/jogador2/mercado no banco com o nome completo e tenta
find_match de novo (agora com dados suficientes pra Superbet/SofaScore
confirmarem o confronto de verdade). Rode uma vez e apague este script e o
workflow depois — não é parte do pipeline normal.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from database import get_bet, get_connection, update_match_info
from matcher import find_match
from models import BetStatus

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("fix_bets_once")

# id -> (jogador1 correto, jogador2 correto, mercado correto)
CORRECOES = {
    67: ("Moro Cañas", "Cecchinato", "Moro Cañas vencer o 2º set"),
    69: ("Kilian Feldbausch", "Andrej Nedic", "Kilian Feldbausch vencer o 2º set"),
}


def _corrigir_nomes_e_mercado(bet_id: int, jogador1: str, jogador2: str, mercado: str) -> None:
    """UPDATE direto — database.py não expõe um update genérico de
    jogador1/jogador2/mercado (não existe caso de uso normal pra isso, ver
    listener.py/matcher.py), então este script pontual faz o SQL na mão."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE bets SET jogador1 = %s, jogador2 = %s, mercado = %s WHERE id = %s",
            (jogador1, jogador2, mercado, bet_id),
        )


def main() -> None:
    for bet_id, (jogador1, jogador2, mercado) in CORRECOES.items():
        bet = get_bet(bet_id)
        if bet is None:
            logger.warning("Aposta #%s não encontrada no banco — pulando.", bet_id)
            continue

        logger.info(
            "Aposta #%s: corrigindo nomes de %r x %r -> %r x %r",
            bet_id, bet.jogador1, bet.jogador2, jogador1, jogador2,
        )
        _corrigir_nomes_e_mercado(bet_id, jogador1, jogador2, mercado)

        match = find_match(jogador1, jogador2, bet.esporte)
        if not match.encontrado:
            logger.warning(
                "Aposta #%s: nomes corrigidos no banco, mas find_match ainda não confirmou o "
                "confronto (%s x %s) — status continua 'nao_encontrada', o retry automático do "
                "score_updater.py vai tentar de novo nos próximos ciclos.",
                bet_id, jogador1, jogador2,
            )
            continue

        agora = datetime.now(timezone.utc)
        status = BetStatus.AO_VIVO.value if (match.data_hora and match.data_hora <= agora) else BetStatus.AGENDADA.value
        update_match_info(
            bet_id,
            data_hora=match.data_hora,
            links=match.links,
            status=status,
            sofascore_event_id=match.sofascore_event_id,
            jogador1=match.jogador1_oficial or jogador1,
            jogador2=match.jogador2_oficial or jogador2,
            torneio=match.torneio_oficial,
        )
        logger.info(
            "Aposta #%s: confronto confirmado — %s x %s | torneio=%s | status=%s",
            bet_id, match.jogador1_oficial, match.jogador2_oficial, match.torneio_oficial, status,
        )


if __name__ == "__main__":
    main()
