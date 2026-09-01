"""
bookmakers/bet365.py
=====================
Adaptador do bet365.

URL confirmada ao vivo (celular, rede no Brasil, 31/08/2026):
    https://www.bet365.bet.br/?lng=33#/AS/B13/K%5E5/

⚠️ Status de confiança: O MAIS BAIXO dos três — CONFIRMADO ao tentar
calibrar os seletores ao vivo (31/08/2026):

  1. O domínio/path (`bet365.bet.br`, `#/AS/B13/K^5/`) está correto: a home
     carrega normalmente via Playwright, e clicar no item de menu "Tênis"
     navega para essa mesma URL de hash (ou seja, o path é estável, não é
     um acidente de sessão como se suspeitava).
  2. Mesmo assim, o painel de conteúdo mostra "Não é possível exibir este
     conteúdo" em vez da lista de jogos. O console do navegador revela a
     causa: um erro JavaScript real DENTRO do próprio app do bet365 —
     `TypeError: Cannot read properties of undefined (reading 'split')`
     dentro do componente `ErrorGrid` deles — ou seja, o app tenta
     renderizar a grade de jogos, falha com uma exceção não tratada, e cai
     no próprio componente de erro. Não é um CAPTCHA nem um 403 explícito;
     é o app quebrando ao rodar num Chromium automatizado (headless, sem
     histórico/sessão "orgânica" — provavelmente falta algum dado que o
     app espera ter em localStorage/sessão real).
  3. Como não há grade de jogos renderizada, não existe HTML real pra
     calibrar `_SELECTORS` contra — os valores abaixo continuam sendo uma
     estimativa baseada em nomenclatura comum do bet365 (prefixos
     `rcl-`/`gl-`/`sl-`), não confirmada. Se um dia o app carregar
     normalmente nesse ambiente, revalide com HTML real antes de confiar
     neles.

Na prática: espere este adaptador usar o link aproximado quase sempre — é
o comportamento esperado e documentado, não um bug. O app/site do bet365
é rápido de navegar manualmente a partir da seção de tênis.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from config import settings
from nameutils import pair_matches

from .base import BookmakerAdapter

logger = logging.getLogger(__name__)

# Seletores-PLACEHOLDER — o bet365 troca a estrutura do DOM com frequência
# e por região; espere ter que recalibrar isso mais do que nas outras casas.
_SELECTORS = {
    "match_card": ".rcl-ParticipantFixtureDetailsHigher_Wrapper, .gl-Market_General, .sl-CouponParticipantWithBookCloses",
    "player_name": ".rcl-ParticipantFixtureDetailsHigher_TeamName, .rcl-ParticipantFixtureDetails_TeamName",
    "match_time": ".rcl-ParticipantFixtureDetailsHigher_BookCloses, .rcl-MarketHeaderLabel--time",
    "match_link": "a",
}


class Bet365Adapter(BookmakerAdapter):
    slug = "bet365"
    display_name = "bet365"
    base_url = settings.BET365_BASE_URL        # CONFIRME o domínio real! (ver aviso acima)
    tennis_path = settings.BET365_TENNIS_PATH   # ex: /SP/tenis  (CONFIRME!)

    def build_fallback_link(self, torneio, data_hora):
        return f"{self.base_url.rstrip('/')}{self.tennis_path}"

    def find_exact_link(self, jogador1, jogador2, torneio, data_hora):
        threshold = settings.SUPERBET_FUZZY_THRESHOLD
        url = f"{self.base_url.rstrip('/')}{self.tennis_path}"

        def _buscar(page):
            if not self._safe_goto(page, url):
                return None
            cards = page.query_selector_all(_SELECTORS["match_card"])
            logger.debug("bet365: %d cards encontrados em %s", len(cards), url)
            for card in cards:
                nomes_el = card.query_selector_all(_SELECTORS["player_name"])
                nomes = [el.inner_text().strip() for el in nomes_el if el.inner_text().strip()]
                if len(nomes) < 2:
                    continue
                if pair_matches(jogador1, jogador2, nomes[0], nomes[1], threshold):
                    link_el = card.query_selector(_SELECTORS["match_link"]) or card
                    href = link_el.get_attribute("href") if link_el else None
                    if href and href.startswith("/"):
                        return f"{self.base_url.rstrip('/')}{href}"
                    return href or url
            return None

        return self._run_with_browser(_buscar)
