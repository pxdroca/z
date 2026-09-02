"""
bookmakers/superbet.py
=======================
Adaptador da Superbet.

URL real confirmada ao vivo (celular, rede no Brasil, 31/08/2026):
    https://superbet.bet.br/apostas/tenis?day=hoje

Duas correções importantes em relação à pesquisa inicial (feita de um
ambiente fora do Brasil, que só enxergava `superbet.com` — domínio
bloqueado geograficamente e sem os mesmos paths):
  1. O domínio de produção brasileiro é **`superbet.bet.br`**, não
     `superbet.com`.
  2. O filtro de dia usa uma **palavra-chave** (`day=hoje`), não uma data
     ISO (`YYYY-MM-DD`) como o `superbet.com` internacional sugeria. Só
     confirmamos `hoje`; `amanha` é uma suposição razoável (não testada) —
     ajuste `_DAY_KEYWORDS` abaixo se descobrir o valor certo, e se o site
     também aceitar uma data explícita, é só trocar por isso.

O site ainda é uma SPA (os jogos carregam via JavaScript), por isso o uso
de Playwright em vez de requests simples. Os seletores em `_SELECTORS` são
um ponto de partida — ajuste-os com base no HTML real (veja README, seção
"Calibrando os adaptadores de casas").

Busca por nome (`/busca?query=...`) — calibrada ao vivo em 02/09/2026:
A Superbet tem um endpoint de busca simples via query string
(`https://superbet.bet.br/busca?query=SOBRENOME`) que devolve os jogos de
qualquer esporte cujo nome bata (não só tênis, e não é fuzzy — é substring
simples). Os cards de resultado usam os MESMOS seletores `_SELECTORS` da
listagem por dia, então a mesma extração serve para os dois casos. Essa
busca é a estratégia primária: é ordens de magnitude mais rápida (não
precisa rolar ~300+ jogos/dia, que é o que a listagem por dia tem hoje em
época de torneio grande) e mais precisa. A listagem por dia (`_scrape_day`)
fica como fallback, para o caso da busca não achar nada (nome muito
abreviado pelo OCR, jogador sem jogo hoje etc.) — ver `find_exact_link`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from urllib.parse import quote, urljoin

from config import settings
from nameutils import pair_matches

from .base import BookmakerAdapter

logger = logging.getLogger(__name__)

# Calibrado ao vivo em 31/08/2026 contra https://superbet.bet.br/apostas/tenis?day=hoje.
# O site é uma SPA Vue.js (não React/Next.js como a pesquisa inicial supôs);
# as classes "e2e-*" são usadas nos testes automatizados da própria Superbet
# e tendem a ser mais estáveis entre deploys que classes de estilo puras.
# Os mesmos seletores servem para os cards da página de busca (/busca?query=).
_SELECTORS = {
    "match_card": "a.e2e-event-row",
    "player1_name": ".e2e-event-team1-name",
    "player2_name": ".e2e-event-team2-name",
    "match_time": ".event-time",
    "match_link": "a.e2e-event-row",  # o próprio card já é o link (href absoluto)
}

_SEARCH_URL = "https://superbet.bet.br/busca"

# offset em dias (relativo a "agora") -> palavra-chave aceita pela Superbet.
# Só "hoje" (offset 0) foi confirmado ao vivo; "amanha" é um chute educado.
# offsets fora daqui caem para a página sem filtro de dia (ver _day_param).
_DAY_KEYWORDS = {0: "hoje", 1: "amanha"}


@dataclass
class _RawMatch:
    jogador1: str
    jogador2: str
    horario_texto: str
    link: str


def _day_param(offset: int) -> Optional[str]:
    return _DAY_KEYWORDS.get(offset)


def _offset_days(data_hora: Optional[datetime]) -> int:
    if not data_hora:
        return 0
    return (data_hora.date() - datetime.now().date()).days


def _parse_match_time(texto: str, referencia: datetime) -> Optional[datetime]:
    texto = (texto or "").strip().lower()
    hora_match = re.search(r"(\d{1,2}):(\d{2})", texto)
    if not hora_match:
        return None
    hora, minuto = int(hora_match.group(1)), int(hora_match.group(2))
    try:
        return referencia.replace(hour=hora, minute=minuto, second=0, microsecond=0)
    except ValueError:
        return None


class SuperbetAdapter(BookmakerAdapter):
    slug = "superbet"
    display_name = "Superbet"
    base_url = settings.SUPERBET_BASE_URL  # ex: https://superbet.bet.br/apostas/tenis

    def _url_for_offset(self, offset: int) -> str:
        param = _day_param(offset)
        return f"{self.base_url}?day={param}" if param else self.base_url

    def build_fallback_link(self, torneio, data_hora):
        return self._url_for_offset(_offset_days(data_hora))

    def _scrape_day(self, page, offset: int) -> list[_RawMatch]:
        url = self._url_for_offset(offset)
        if not self._safe_goto(page, url):
            return []

        # A lista de jogos usa virtualização: a página pode ter centenas de
        # jogos no total, mas só mantém uma "janela" pequena no DOM por vez
        # (cards do topo somem conforme novos entram embaixo). Por isso
        # acumulamos os matches a cada passo do scroll (por href único) em
        # vez de só ler o DOM no final — senão perderíamos os que já saíram
        # da janela. Para quando não aparece nenhum href novo, com teto de
        # segurança para não rolar indefinidamente.
        matches_by_href: dict[str, _RawMatch] = {}

        def _collect() -> int:
            for card in page.query_selector_all(_SELECTORS["match_card"]):
                link_el = card.query_selector(_SELECTORS["match_link"]) or card
                href = link_el.get_attribute("href") if link_el else None
                if not href or href in matches_by_href:
                    continue
                j1_el = card.query_selector(_SELECTORS["player1_name"])
                j2_el = card.query_selector(_SELECTORS["player2_name"])
                jogador1 = j1_el.inner_text().strip() if j1_el else ""
                jogador2 = j2_el.inner_text().strip() if j2_el else ""
                if not jogador1 or not jogador2:
                    continue
                horario_el = card.query_selector(_SELECTORS["match_time"])
                horario_texto = horario_el.inner_text().strip() if horario_el else ""
                matches_by_href[href] = _RawMatch(
                    jogador1=jogador1, jogador2=jogador2, horario_texto=horario_texto, link=urljoin(url, href)
                )
            return len(matches_by_href)

        total_anterior = _collect()
        for _ in range(40):
            page.mouse.wheel(0, 2000)
            page.wait_for_timeout(400)
            total_atual = _collect()
            # só considera "estabilizou" depois de já ter achado algo — os
            # primeiros scrolls podem não render nada ainda (carregamento
            # inicial mais lento que os seguintes).
            if total_atual == total_anterior and total_atual > 0:
                break
            total_anterior = total_atual

        return list(matches_by_href.values())

    def _search(self, page, termo: str) -> list[_RawMatch]:
        """Busca por nome via /busca?query=... — ver docstring do módulo."""
        url = f"{_SEARCH_URL}?query={quote(termo)}"
        if not self._safe_goto(page, url):
            return []

        matches: list[_RawMatch] = []
        for card in page.query_selector_all(_SELECTORS["match_card"]):
            href = card.get_attribute("href")
            # A busca cobre todos os esportes — filtra só tênis pelo path
            # do link (evita falso positivo tipo um time de futebol/e-sports
            # cujo nome contém o termo buscado, ex: "Sinner" -> time "Sinners").
            if not href or "/odds/tenis/" not in href:
                continue
            j1_el = card.query_selector(_SELECTORS["player1_name"])
            j2_el = card.query_selector(_SELECTORS["player2_name"])
            jogador1 = j1_el.inner_text().strip() if j1_el else ""
            jogador2 = j2_el.inner_text().strip() if j2_el else ""
            if not jogador1 or not jogador2:
                continue
            horario_el = card.query_selector(_SELECTORS["match_time"])
            horario_texto = horario_el.inner_text().strip() if horario_el else ""
            matches.append(_RawMatch(jogador1=jogador1, jogador2=jogador2, horario_texto=horario_texto, link=urljoin(url, href)))
        return matches

    def find_exact_link(self, jogador1, jogador2, torneio, data_hora):
        threshold = settings.SUPERBET_FUZZY_THRESHOLD

        def _buscar(page):
            # Estratégia primária: busca por nome (rápida e direta — ver
            # docstring do módulo). Tenta os dois jogadores separadamente,
            # pois o OCR às vezes captura melhor um nome que o outro.
            for termo in (jogador1, jogador2):
                if not termo:
                    continue
                for c in self._search(page, termo):
                    if pair_matches(jogador1, jogador2, c.jogador1, c.jogador2, threshold):
                        return c.link

            # Fallback: rola a listagem do dia inteira (mais lenta) — cobre
            # o caso da busca não achar nada (nome muito abreviado, etc.).
            offset_alvo = _offset_days(data_hora)
            offsets = sorted({o for o in (offset_alvo, 0, 1) if _day_param(o) is not None})
            for offset in offsets:
                for c in self._scrape_day(page, offset):
                    if pair_matches(jogador1, jogador2, c.jogador1, c.jogador2, threshold):
                        return c.link
            return None

        return self._run_with_browser(_buscar)
