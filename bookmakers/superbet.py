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
from zoneinfo import ZoneInfo

from config import settings
from models import Esporte
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

# Slug de path usado no filtro da busca (/odds/{slug}/...) — ver _search.
# "tenis" confirmado ao vivo em 02/09/2026 (ver docstring do módulo).
# "basquete" confirmado ao vivo em 03/09/2026: busca por "Lakers" devolveu
# 'https://superbet.bet.br/odds/basquete/los-angeles-lakers-x-philadelphia-
# 76ers-14461896' — o filtro por substring "/odds/basquete/" corretamente
# aceita esse link e rejeita "/odds/e-sport-basquete/..." (que também
# apareceu na mesma busca, mas é e-sports, não basquete real).
_PATH_SLUG = {
    Esporte.TENIS.value: "tenis",
    Esporte.BASQUETE.value: "basquete",
}


@dataclass
class _RawMatch:
    jogador1: str
    jogador2: str
    horario_texto: str
    link: str
    # Odds do mercado "Vencedor da Partida" (1 e 2), quando o card as mostra.
    # Usadas por matcher.buscar_confronto_na_superbet pra desempatar entre o
    # jogo de simples e o de duplas do mesmo jogador — ver o porquê lá.
    odds: tuple[float, ...] = ()


def _day_param(offset: int) -> Optional[str]:
    return _DAY_KEYWORDS.get(offset)


# Odd no card da busca: um decimal com 1-3 casas, entre 1.01 e 999.
# Deliberadamente NÃO casa inteiros ("1"/"2" são os rótulos das colunas
# casa/fora, não odds) nem horários ("16:00", separador diferente).
_ODD_PATTERN = re.compile(r"\b(\d{1,3}[.,]\d{1,3})\b")


def _odds_do_card(card) -> tuple[float, ...]:
    """Odds do mercado "Vencedor da Partida" visíveis no card da busca.

    Lido do texto dos botões de aposta em vez de um seletor de classe:
    inspecionando o DOM ao vivo (03/09/2026), as classes com "odd" no nome
    envolvem o bloco inteiro do mercado (o mesmo texto repetido em 21
    elementos aninhados), enquanto cada botão contém exatamente um par
    rótulo+odd ("1/1.51", "2/2.55"). O botão é o nível certo de
    granularidade.

    Devolve tupla vazia quando o card não mostra odds (mercado suspenso,
    jogo ao vivo, layout diferente) — quem chama trata isso como "não sei",
    nunca como "odd zero".
    """
    odds: list[float] = []
    try:
        botoes = card.query_selector_all("button")
    except Exception:
        return ()
    for b in botoes:
        try:
            texto = b.inner_text()
        except Exception:
            continue
        for bruto in _ODD_PATTERN.findall(texto or ""):
            try:
                valor = float(bruto.replace(",", "."))
            except ValueError:
                continue
            if 1.01 <= valor <= 999:
                odds.append(valor)
    return tuple(odds)


def _offset_days(data_hora: Optional[datetime]) -> int:
    """Quantos dias à frente de hoje está o jogo — vira a aba hoje/amanhã do
    site da Superbet.

    Os dois lados da conta são convertidos para o fuso local configurado: o
    data_hora do SofaScore é timezone-aware em UTC, e o site da Superbet
    mostra o dia em horário de Brasília. Comparar a data UTC com a data
    local erra o offset na janela entre 21:00 e 00:00 local (já é o dia
    seguinte em UTC), mandando a busca pra aba do dia errado.
    """
    if not data_hora:
        return 0
    fuso = ZoneInfo(settings.TIMEZONE)
    hoje_local = datetime.now(fuso).date()
    if data_hora.tzinfo is None:
        dia_jogo = data_hora.date()
    else:
        dia_jogo = data_hora.astimezone(fuso).date()
    return (dia_jogo - hoje_local).days


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
    base_url = settings.SUPERBET_BASE_URL  # ex: https://superbet.bet.br/apostas/tenis — mantido p/ compatibilidade
    # URL base por esporte — ver config.py (SUPERBET_BASKETBALL_URL é TODO de
    # calibração ao vivo, path presumido por analogia com o de tênis).
    _BASE_URLS = {
        Esporte.TENIS.value: settings.SUPERBET_BASE_URL,
        Esporte.BASQUETE.value: settings.SUPERBET_BASKETBALL_URL,
    }

    def _url_for_offset(self, offset: int, esporte: str = Esporte.TENIS.value) -> str:
        base_url = self._BASE_URLS[esporte]
        param = _day_param(offset)
        return f"{base_url}?day={param}" if param else base_url

    def build_fallback_link(self, torneio, data_hora, esporte: str = Esporte.TENIS.value):
        return self._url_for_offset(_offset_days(data_hora), esporte)

    def _scrape_day(self, page, offset: int, esporte: str = Esporte.TENIS.value) -> list[_RawMatch]:
        url = self._url_for_offset(offset, esporte)
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

    def _search(self, page, termo: str, esporte: str = Esporte.TENIS.value) -> list[_RawMatch]:
        """Busca por nome via /busca?query=... — ver docstring do módulo."""
        url = f"{_SEARCH_URL}?query={quote(termo)}"
        if not self._safe_goto(page, url):
            return []

        # Espera os cards renderizarem. A busca é uma SPA Vue: o goto
        # retorna com a casca da página pronta e os resultados chegam por
        # XHR depois. Lendo o DOM na hora, a mesma busca dava 2 resultados
        # ou 0 de forma alternada — medido em 03/09/2026, ~1 em 3 chamadas
        # vinha vazia. Como a validação cruzada de matcher.py depende desta
        # busca, esse "vazio" intermitente virava jogo não confirmado (ou,
        # pior, confirmado pela fonte errada) sem nenhum erro no log.
        #
        # Timeout curto e engolido de propósito: "nenhum resultado" é uma
        # resposta legítima (jogador sem jogo aberto), e nesse caso o
        # seletor nunca aparece — não é erro, é a resposta.
        try:
            page.wait_for_selector(_SELECTORS["match_card"], timeout=8000)
        except Exception:
            logger.debug("superbet: busca por %r não renderizou cards (sem resultados?).", termo)
            return []

        path_slug = _PATH_SLUG[esporte]
        matches: list[_RawMatch] = []
        for card in page.query_selector_all(_SELECTORS["match_card"]):
            href = card.get_attribute("href")
            # A busca cobre todos os esportes — filtra pelo path do esporte
            # pedido (evita falso positivo tipo um time de futebol/e-sports
            # cujo nome contém o termo buscado, ex: "Sinner" -> time "Sinners").
            if not href or f"/odds/{path_slug}/" not in href:
                continue
            j1_el = card.query_selector(_SELECTORS["player1_name"])
            j2_el = card.query_selector(_SELECTORS["player2_name"])
            jogador1 = j1_el.inner_text().strip() if j1_el else ""
            jogador2 = j2_el.inner_text().strip() if j2_el else ""
            if not jogador1 or not jogador2:
                continue
            horario_el = card.query_selector(_SELECTORS["match_time"])
            horario_texto = horario_el.inner_text().strip() if horario_el else ""
            matches.append(_RawMatch(
                jogador1=jogador1, jogador2=jogador2, horario_texto=horario_texto,
                link=urljoin(url, href), odds=_odds_do_card(card),
            ))
        return matches

    def buscar_confrontos(self, termo: str, esporte: str = Esporte.TENIS.value) -> list[_RawMatch]:
        """Confrontos que a busca da Superbet devolve para `termo`, no
        esporte pedido.

        Devolve os _RawMatch inteiros (nomes, horário do card, odds, link) —
        matcher.buscar_confronto_na_superbet usa o horário pra rejeitar um
        jogo que não é de hoje e as odds pra desempatar simples vs. duplas.

        Existe para a validação cruzada em matcher.confirmar_confronto: a
        Superbet lista SÓ jogos futuros/abertos pra aposta, então servir de
        segunda opinião resolve de graça dois erros que o SofaScore sozinho
        comete — devolver um jogo antigo (o featured-event de um perfil
        desatualizado) e casar com um homônimo que não joga hoje.
        """
        if not termo:
            return []
        def _buscar(page):
            return self._search(page, termo, esporte)
        return self._run_with_browser(_buscar) or []

    def find_exact_link(self, jogador1, jogador2, torneio, data_hora, esporte: str = Esporte.TENIS.value):
        threshold = settings.SUPERBET_FUZZY_THRESHOLD

        def _buscar(page):
            # Estratégia primária: busca por nome (rápida e direta — ver
            # docstring do módulo). Tenta os dois jogadores separadamente,
            # pois o OCR às vezes captura melhor um nome que o outro.
            for termo in (jogador1, jogador2):
                if not termo:
                    continue
                for c in self._search(page, termo, esporte):
                    if pair_matches(jogador1, jogador2, c.jogador1, c.jogador2, threshold):
                        return c.link

            # Fallback: rola a listagem do dia inteira (mais lenta) — cobre
            # o caso da busca não achar nada (nome muito abreviado, etc.).
            offset_alvo = _offset_days(data_hora)
            offsets = sorted({o for o in (offset_alvo, 0, 1) if _day_param(o) is not None})
            for offset in offsets:
                for c in self._scrape_day(page, offset, esporte):
                    if pair_matches(jogador1, jogador2, c.jogador1, c.jogador2, threshold):
                        return c.link
            return None

        return self._run_with_browser(_buscar)
