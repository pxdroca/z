"""
bookmakers/betano.py
=====================
Adaptador da Betano (domínio brasileiro: betano.bet.br).

Calibrado ao vivo em 02/09/2026: a URL certa para achar o link exato de
qualquer jogo do dia é https://betano.bet.br/sport/tenis/jogos-de-hoje/
(não https://betano.bet.br/sport/tenis/, que foi a URL usada na calibração
anterior deste arquivo). A diferença é real: /sport/tenis/ é majoritariamente
uma página de conteúdo/SEO ("Por que escolher a Betano...", "Tipos de
apostas...") com só uma pequena seção de destaques no topo — bug real visto
em produção, jogos reais do dia (ex: Ben Shelton x Hubert Hurkacz, US Open)
não apareciam ali. /jogos-de-hoje/ já mostra a lista completa dos próximos
jogos, ~80 jogos de tênis num dia de Grand Slam, sem precisar de scroll.

A estrutura de card também é diferente da versão anterior: cada jogo é um
`a[data-qa='pre-event']` (o próprio link, href relativo tipo
"/odds/jogador1-jogador2/91503834/"), contendo `[data-qa='participants']`
com os 2 nomes em `<div>` filhos diretos (sem atributo individual por
jogador, diferente do que a calibração anterior assumia).

⚠️ Se a Betano voltar a bloquear no futuro (403, CAPTCHA, DOM vazio mesmo
com stealth) ou mudar a estrutura de novo, o adaptador cai automaticamente
para o link aproximado — o pipeline não quebra, só se perde a conveniência
do link exato nessa casa.
"""

from __future__ import annotations

import logging
from urllib.parse import urljoin

from config import settings
from nameutils import pair_matches

from .base import BookmakerAdapter

logger = logging.getLogger(__name__)

_SELECTORS = {
    "match_card": "a[data-qa='pre-event']",
    "player_names": "[data-qa='participants'] > div > div",
}


class BetanoAdapter(BookmakerAdapter):
    slug = "betano"
    display_name = "Betano"
    base_url = settings.BETANO_BASE_URL       # ex: https://betano.bet.br
    tennis_path = settings.BETANO_TENNIS_PATH  # ex: /sport/tenis/
    jogos_de_hoje_path = "/sport/tenis/jogos-de-hoje/"

    def build_fallback_link(self, torneio, data_hora):
        # Sem parâmetro de dia confirmado — leva pra seção geral de tênis.
        return f"{self.base_url.rstrip('/')}{self.tennis_path}"

    def find_exact_link(self, jogador1, jogador2, torneio, data_hora):
        threshold = settings.SUPERBET_FUZZY_THRESHOLD
        url = f"{self.base_url.rstrip('/')}{self.jogos_de_hoje_path}"

        def _buscar(page):
            if not self._safe_goto(page, url):
                return None
            cards = page.query_selector_all(_SELECTORS["match_card"])
            logger.debug("Betano: %d cards encontrados em %s", len(cards), url)
            for card in cards:
                nomes_el = card.query_selector_all(_SELECTORS["player_names"])
                nomes = [el.inner_text().strip() for el in nomes_el if el.inner_text().strip()]
                if len(nomes) < 2:
                    continue
                if pair_matches(jogador1, jogador2, nomes[0], nomes[1], threshold):
                    href = card.get_attribute("href")
                    return urljoin(url, href) if href else url
            return None

        return self._run_with_browser(_buscar)
