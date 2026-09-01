"""
bookmakers/betano.py
=====================
Adaptador da Betano (domínio brasileiro: betano.bet.br).

Calibrado ao vivo em 31/08/2026 contra https://betano.bet.br/sport/tenis/ —
diferente do que o README avisava, essa URL (o valor padrão de
BETANO_TENNIS_PATH) já é a seção de tênis correta (confirmado pelo <title>
"Apostas Tênis" e pelo <h1> "Tênis" da página) e a página respondeu 200 sem
bloqueio (nem CAPTCHA, nem DOM vazio) usando o mesmo navegador stealth de
bookmakers/base.py.

A listagem de jogos usa um componente de cards ("hero cards") com atributos
`data-qa` estáveis (usados pelos próprios testes automatizados da Betano):
cada jogo é um `[data-qa^="hero_card_container_"]` contendo os nomes dos
dois jogadores, a data/hora e um link relativo (`/odds/.../<id>/`).

⚠️ Se a Betano voltar a bloquear no futuro (403, CAPTCHA, DOM vazio mesmo
com stealth), o adaptador cai automaticamente para o link aproximado — o
pipeline não quebra, só se perde a conveniência do link exato nessa casa.
"""

from __future__ import annotations

import logging
from urllib.parse import urljoin

from config import settings
from nameutils import pair_matches

from .base import BookmakerAdapter

logger = logging.getLogger(__name__)

_SELECTORS = {
    "match_card": "[data-qa^='hero_card_container_']",
    "player1_name": "[data-qa='first-participant-name']",
    "player2_name": "[data-qa='second-participant-name']",
    "match_time": "[data-qa='hero_card_date']",
    "match_link": "[data-qa='hero_card_link']",
}


class BetanoAdapter(BookmakerAdapter):
    slug = "betano"
    display_name = "Betano"
    base_url = settings.BETANO_BASE_URL       # ex: https://betano.bet.br
    tennis_path = settings.BETANO_TENNIS_PATH  # ex: /sport/tenis/

    def build_fallback_link(self, torneio, data_hora):
        # Sem parâmetro de dia confirmado — leva pra seção geral de tênis.
        return f"{self.base_url.rstrip('/')}{self.tennis_path}"

    def find_exact_link(self, jogador1, jogador2, torneio, data_hora):
        threshold = settings.SUPERBET_FUZZY_THRESHOLD
        url = f"{self.base_url.rstrip('/')}{self.tennis_path}"

        def _buscar(page):
            if not self._safe_goto(page, url):
                return None
            cards = page.query_selector_all(_SELECTORS["match_card"])
            logger.debug("Betano: %d cards encontrados em %s", len(cards), url)
            for card in cards:
                j1_el = card.query_selector(_SELECTORS["player1_name"])
                j2_el = card.query_selector(_SELECTORS["player2_name"])
                nome1 = j1_el.inner_text().strip() if j1_el else ""
                nome2 = j2_el.inner_text().strip() if j2_el else ""
                if not nome1 or not nome2:
                    continue
                if pair_matches(jogador1, jogador2, nome1, nome2, threshold):
                    link_el = card.query_selector(_SELECTORS["match_link"]) or card
                    href = link_el.get_attribute("href") if link_el else None
                    return urljoin(url, href) if href else url
            return None

        return self._run_with_browser(_buscar)
