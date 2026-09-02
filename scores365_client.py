"""
scores365_client.py
====================
Fonte alternativa de placar/status ao vivo (via 365scores.com), usada por
score_updater.py como FALLBACK quando o SofaScore falhar — ver
sofascore_client.get_event_status().

Por quê um fallback: confirmado em produção (02/09/2026) que o SofaScore às
vezes devolve 403 pra TODAS as consultas de um mesmo ciclo do
score_updater.py rodando no GitHub Actions — sinal de bloqueio momentâneo da
faixa de IP compartilhada dos runners, não de um request específico (retry
com o mesmo IP não ajuda nesse caso). Ter uma segunda fonte independente
reduz bastante a chance de as duas falharem ao mesmo tempo.

Calibrado ao vivo em 02/09/2026 contra:
    GET https://webws.365scores.com/web/games/allscores/
        ?appTypeId=5&langId=31&timezoneName=America/Sao_Paulo&userCountryId=21
        &sports=3&startDate=DD/MM/YYYY&endDate=DD/MM/YYYY

  - sports=3 é o ID de tênis nesse site (confirmado pela resposta real).
  - Endpoint público, sem autenticação, JSON puro — diferente do Flashscore
    (que exige um header/token gerado via JS, 401 em chamada crua) e mais
    simples que o SofaScore (não precisa da etapa de listar torneios).
  - homeCompetitor/awayCompetitor têm "name" (sem acentos às vezes, ex:
    "Alex Molcan" vs "Alex Molčan" do SofaScore — nameutils.pair_matches já
    normaliza acentos, então isso não deveria ser problema pro fuzzy-match).
  - statusText: "Fim" quando terminado, texto livre tipo "2º set"/"Ao vivo"
    quando em andamento, ou algo tipo horário quando ainda não começou.
  - stages: lista com 1 entrada por set (name="1º set".."5º set") mais
    entradas agregadas (name="Sets", "Game") — filtra-se pelas que batem
    com o padrão "Nº set" pra montar o placar final.
  - homeCompetitor.isWinner / awayCompetitor.isWinner: bool direto, só um
    dos dois true quando o jogo termina.

Não busca por ID (diferente do SofaScore) porque não temos um "365scores
event id" salvo no banco — busca pelos NOMES dos jogadores já confirmados
pelo SofaScore no momento do match inicial (ver find_status_by_names).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from playwright.sync_api import Page, sync_playwright

from nameutils import pair_matches
from sofascore_client import EventStatus

logger = logging.getLogger(__name__)

_BASE_URL = "https://webws.365scores.com/web/games/allscores/"
_SPORT_ID_TENIS = 3

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
)

_SET_STAGE_PATTERN = re.compile(r"^(\d)º set$")


def _run_with_browser(fn):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(user_agent=_USER_AGENT, locale="pt-BR")
            page = context.new_page()
            try:
                return fn(page)
            finally:
                context.close()
        finally:
            browser.close()


def _fetch_games(page: Page, dia: datetime) -> list[dict]:
    data_str = dia.strftime("%d/%m/%Y")
    url = (
        f"{_BASE_URL}?appTypeId=5&langId=31&timezoneName=America/Sao_Paulo"
        f"&userCountryId=21&sports={_SPORT_ID_TENIS}&startDate={data_str}&endDate={data_str}"
    )
    try:
        resp = page.goto(url, timeout=20_000)
        if resp is None or not resp.ok:
            logger.warning("365scores respondeu %s", getattr(resp, "status", "?"))
            return []
        return resp.json().get("games", [])
    except Exception:
        logger.exception("Falha ao consultar 365scores")
        return []


def _sets_das_stages(stages: list[dict]) -> list[tuple[int, int]]:
    """Lista de (games_home, games_away) por set, a partir de stages tipo
    {name: "1º set", homeCompetitorScore, awayCompetitorScore} — só sets já
    com placar (score >= 0; sets ainda não jogados vêm com -1)."""
    sets = []
    for stage in sorted(stages, key=lambda s: s.get("id", 0)):
        m = _SET_STAGE_PATTERN.match(stage.get("name", ""))
        if not m:
            continue
        h, a = stage.get("homeCompetitorScore"), stage.get("awayCompetitorScore")
        if h is None or a is None or h < 0 or a < 0:
            continue
        sets.append((int(h), int(a)))
    return sets


def _placar_dos_sets(sets: list[tuple[int, int]]) -> Optional[str]:
    """Monta "6-4, 3-6, 7-5" a partir da lista de sets estruturada."""
    return ", ".join(f"{h}-{a}" for h, a in sets) if sets else None


def _game_to_status(game: dict) -> EventStatus:
    home = game.get("homeCompetitor") or {}
    away = game.get("awayCompetitor") or {}
    status_text = (game.get("statusText") or "").strip().lower()

    if status_text == "fim":
        status = "finished"
    elif game.get("statusGroup") == 1:  # não confirmado formalmente, mas
        status = "notstarted"           # statusGroup 1 = agendado nos testes
    else:
        status = "inprogress"

    vencedor = None
    if status == "finished":
        if home.get("isWinner"):
            vencedor = "home"
        elif away.get("isWinner"):
            vencedor = "away"

    sets = _sets_das_stages(game.get("stages") or [])
    return EventStatus(
        status=status,
        placar=_placar_dos_sets(sets),
        vencedor=vencedor,
        sets=sets,
        jogador1_nome=home.get("name"),
        jogador2_nome=away.get("name"),
    )


def find_status_by_names(
    jogador1: str,
    jogador2: str,
    threshold: int,
    dias_de_busca: int = 1,
    referencia: Optional[datetime] = None,
) -> Optional[EventStatus]:
    """
    Busca o jogo por nome dos dois jogadores (hoje + `dias_de_busca` dias
    seguintes, cobrindo o caso do jogo ter sido agendado ontem/virado o
    dia) e devolve o EventStatus equivalente ao que sofascore_client
    devolveria — mesma dataclass, pra score_updater.py usar sem precisar
    saber qual das duas fontes respondeu.

    Devolve None se não achar (jogo não listado nesse site, nomes não
    batem, ou o site também falhou) — o chamador decide o que fazer.
    """
    referencia = referencia or datetime.now()

    def _buscar(page: Page) -> Optional[EventStatus]:
        for offset in range(dias_de_busca + 1):
            dia = referencia + timedelta(days=offset)
            for game in _fetch_games(page, dia):
                home = (game.get("homeCompetitor") or {}).get("name", "")
                away = (game.get("awayCompetitor") or {}).get("name", "")
                if pair_matches(jogador1, jogador2, home, away, threshold):
                    return _game_to_status(game)
        return None

    resultado = _run_with_browser(_buscar)
    if resultado is None:
        logger.warning("365scores: jogo não encontrado para %s x %s", jogador1, jogador2)
    return resultado
