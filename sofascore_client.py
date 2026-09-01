"""
sofascore_client.py
====================
Busca o confronto OFICIAL (nomes corretos, torneio, data/hora exata) usando
a API interna do SofaScore — que não é pública/documentada oficialmente,
mas é usada por dezenas de projetos open source há anos e costuma ser bem
mais estável que raspar diretamente uma casa de apostas: é JSON puro (sem
precisar renderizar JavaScript de verdade), não tem bloqueio geográfico
conhecido, e não depende de login.

⚠️ Descobertas feitas ao validar isto ao vivo (calibração deste projeto):

1. O host correto é `www.sofascore.com` (não `api.sofascore.com` — esse
   subdomínio devolve 403 mesmo com headers de navegador via `requests`/
   `curl`, é bloqueado na borda). Além disso, mesmo `www.sofascore.com`
   bloqueia clientes HTTP simples (sem stack de navegador real) — por isso
   este módulo usa Playwright (Chromium real) só para fazer as chamadas de
   API, em vez de `requests`.

2. Diferente dos adaptadores em bookmakers/, este módulo NÃO usa
   tf-playwright-stealth: testado ao vivo, o stealth faz o SofaScore
   devolver 403 (o fingerprint alterado por ele é tratado como suspeito por
   este anti-bot específico), enquanto um Chromium comum passa normal. Se
   isso mudar no futuro e o SofaScore passar a bloquear sem stealth, vale
   tentar reativar (ver bookmakers/base.py para o padrão).

3. Não existe mais um endpoint único "todos os jogos de tênis do dia". O
   fluxo real (o mesmo que o próprio site usa) é em duas etapas:
     a) GET /api/v1/sport/tennis/scheduled-tournaments/{YYYY-MM-DD}/page/{n}
        lista os torneios com jogo nesse dia (paginado).
     b) GET /api/v1/unique-tournament/{id}/scheduled-events/{YYYY-MM-DD}
        lista os jogos daquele torneio nesse dia — aqui sim aparecem
        homeTeam/awayTeam/startTimestamp.

**Se o SofaScore mudar de novo**: rode `python sofascore_client.py --debug`
(salva `sofascore_debug.json`) e confira se os campos ainda batem com o que
`_extract_event()` espera; ajuste ali se necessário.
"""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from playwright.sync_api import Page, sync_playwright

from config import settings
from nameutils import pair_matches

logger = logging.getLogger(__name__)

SOFASCORE_BASE_URL = "https://www.sofascore.com/api/v1"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
)

# Cache simples em memória por processo: evita bater várias vezes na mesma
# data quando várias tips do mesmo dia chegam em sequência.
_cache: dict[str, list[dict]] = {}


@dataclass
class CanonicalMatch:
    jogador1_oficial: str
    jogador2_oficial: str
    torneio_oficial: Optional[str]
    data_hora: Optional[datetime]
    sofascore_event_id: Optional[int] = None
    rodada: Optional[str] = None


@dataclass
class EventStatus:
    """Saída de get_event_status() — estado ao vivo/final de uma partida já
    confirmada (usada por score_updater.py para acompanhar o jogo)."""

    status: str  # "notstarted" | "inprogress" | "finished" | "unknown"
    placar: Optional[str] = None    # ex: "7-5, 3-6, 2-6, 4-2" (sets já jogados, na ordem)
    vencedor: Optional[str] = None  # "home" | "away" | None (só quando finished)
    jogador1_nome: Optional[str] = None  # homeTeam — útil pra montar a notificação sem outra consulta
    jogador2_nome: Optional[str] = None  # awayTeam


def _polite_delay() -> None:
    """Pequeno atraso aleatório entre chamadas — evita bater feito metralhadora no mesmo host."""
    time.sleep(random.uniform(0.4, 1.0))


def _get_json(page: Page, url: str) -> Optional[dict]:
    try:
        _polite_delay()
        resp = page.goto(url, timeout=20_000)
        if resp is None or not resp.ok:
            logger.warning("SofaScore respondeu %s para %s", getattr(resp, "status", "?"), url)
            return None
        return resp.json()
    except Exception:
        logger.exception("Falha ao consultar SofaScore (%s)", url)
        return None


def _list_tournament_ids(page: Page, dia: datetime) -> list[int]:
    """Etapa 1: lista os IDs de todo torneio de tênis com jogo agendado no dia (todas as páginas)."""
    data_str = dia.strftime("%Y-%m-%d")
    ids: set[int] = set()
    pagina = 1
    while True:
        url = f"{SOFASCORE_BASE_URL}/sport/tennis/scheduled-tournaments/{data_str}/page/{pagina}"
        data = _get_json(page, url)
        if not data:
            break
        for item in data.get("scheduled", []):
            ut = ((item.get("tournament") or {}).get("uniqueTournament") or {})
            if ut.get("id"):
                ids.add(ut["id"])
        if not data.get("hasNextPage"):
            break
        pagina += 1
    return sorted(ids)


def _fetch_scheduled_events(page: Page, dia: datetime) -> list[dict]:
    """Etapa 2: para cada torneio do dia, busca os jogos (homeTeam/awayTeam/startTimestamp)."""
    data_str = dia.strftime("%Y-%m-%d")
    if data_str in _cache:
        return _cache[data_str]

    eventos: list[dict] = []
    for tournament_id in _list_tournament_ids(page, dia):
        url = f"{SOFASCORE_BASE_URL}/unique-tournament/{tournament_id}/scheduled-events/{data_str}"
        data = _get_json(page, url)
        if data:
            eventos.extend(data.get("events", []))

    _cache[data_str] = eventos
    logger.debug("SofaScore devolveu %d eventos de tênis para %s", len(eventos), data_str)
    return eventos


def _extract_event(evt: dict) -> Optional[CanonicalMatch]:
    """
    Converte um item bruto de `events` no formato do SofaScore para
    CanonicalMatch. Isolado numa função própria para facilitar ajuste caso
    o formato real divirja do documentado (veja o aviso no topo do arquivo).
    """
    try:
        home = evt["homeTeam"]["name"]
        away = evt["awayTeam"]["name"]
    except (KeyError, TypeError):
        return None

    torneio = None
    tournament = evt.get("tournament") or {}
    torneio = tournament.get("name")
    categoria = (tournament.get("category") or {}).get("name")
    if categoria and torneio and categoria.lower() not in torneio.lower():
        torneio = f"{categoria} - {torneio}"

    data_hora = None
    ts = evt.get("startTimestamp")
    if ts:
        try:
            data_hora = datetime.fromtimestamp(int(ts))
        except (ValueError, TypeError, OSError):
            data_hora = None

    rodada = (evt.get("roundInfo") or {}).get("name")

    return CanonicalMatch(
        jogador1_oficial=home,
        jogador2_oficial=away,
        torneio_oficial=torneio,
        data_hora=data_hora,
        sofascore_event_id=evt.get("id"),
        rodada=rodada,
    )


def _run_with_browser(fn):
    """
    Abre um Chromium headless e chama fn(page), fechando tudo no final.

    Importante: diferente de bookmakers/base.py, este módulo NÃO aplica
    tf-playwright-stealth — testado ao vivo, o stealth faz o SofaScore
    devolver 403 (o fingerprint alterado pelo stealth aparentemente é
    tratado como suspeito por este anti-bot específico), enquanto um
    Chromium comum (headers de navegador real, sem stealth) passa normal.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=settings.PLAYWRIGHT_HEADLESS)
        try:
            context = browser.new_context(
                user_agent=_USER_AGENT,
                locale="pt-BR",
                extra_http_headers={"Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"},
            )
            page = context.new_page()
            try:
                return fn(page)
            finally:
                context.close()
        finally:
            browser.close()


def find_canonical_match(
    jogador1: str,
    jogador2: str,
    dias_de_busca: int = 3,
    referencia: Optional[datetime] = None,
) -> Optional[CanonicalMatch]:
    """
    Varre hoje + os próximos `dias_de_busca` dias no SofaScore procurando um
    confronto cujos nomes batam (fuzzy) com jogador1/jogador2. Devolve None
    se não encontrar — o chamador (matcher.py) decide o que fazer (ex: cair
    para os dados crus do OCR).
    """
    referencia = referencia or datetime.now()
    threshold = settings.SUPERBET_FUZZY_THRESHOLD  # mesmo limiar usado nas casas de apostas

    def _buscar(page: Page) -> Optional[CanonicalMatch]:
        for offset in range(dias_de_busca + 1):
            dia = referencia + timedelta(days=offset)
            for evt in _fetch_scheduled_events(page, dia):
                candidato = _extract_event(evt)
                if not candidato:
                    continue
                if pair_matches(jogador1, jogador2, candidato.jogador1_oficial, candidato.jogador2_oficial, threshold):
                    logger.info(
                        "SofaScore confirmou: %s x %s | %s | %s",
                        candidato.jogador1_oficial, candidato.jogador2_oficial,
                        candidato.torneio_oficial, candidato.data_hora,
                    )
                    return candidato
        return None

    resultado = _run_with_browser(_buscar)
    if resultado is None:
        logger.warning("SofaScore: confronto não encontrado para %s x %s", jogador1, jogador2)
    return resultado


# Winner code confirmado ao vivo (evento finalizado, 31/08/2026): 1 = casa,
# presume-se 2 = visitante (não confirmado ao vivo, mas é o padrão universal
# de outros esportes na mesma API — ajuste aqui se algum dia vier diferente).
_WINNER_CODE_HOME = 1
_WINNER_CODE_AWAY = 2


def _format_placar(home_score: dict, away_score: dict) -> Optional[str]:
    """Monta "7-5, 3-6, 2-6, 4-2" a partir dos campos period1..period5 de
    homeScore/awayScore — cada um é o placar de games de um set. Sets ainda
    não jogados vêm ausentes/None e são ignorados."""
    sets = []
    for i in range(1, 6):  # tênis tem no máximo 5 sets
        chave = f"period{i}"
        h = home_score.get(chave)
        a = away_score.get(chave)
        if h is None or a is None:
            continue
        sets.append(f"{h}-{a}")
    return ", ".join(sets) if sets else None


def get_event_status(event_id: int) -> Optional[EventStatus]:
    """
    Consulta GET /api/v1/event/{event_id} pra saber o estado atual de uma
    partida já confirmada (usado por score_updater.py).

    Formato confirmado ao vivo (31/08/2026):
      - event.status.type: "notstarted" | "inprogress" | "finished"
      - event.homeScore/awayScore: {"current": <sets vencidos>, "period1"..
        "period5": <games por set>} — sets não jogados ficam ausentes.
      - event.winnerCode: 1 (casa) ou 2 (visitante), só presente quando
        status.type == "finished".
      - event.homeTeam.name / event.awayTeam.name: nomes dos jogadores.

    Devolve None se a consulta falhar (evento removido, rede fora, etc) —
    o chamador deve simplesmente tentar de novo no próximo ciclo.
    """
    url = f"{SOFASCORE_BASE_URL}/event/{event_id}"

    def _buscar(page: Page) -> Optional[EventStatus]:
        data = _get_json(page, url)
        if not data:
            return None
        evt = data.get("event") or {}
        status_tipo = (evt.get("status") or {}).get("type", "unknown")

        home_score = evt.get("homeScore") or {}
        away_score = evt.get("awayScore") or {}
        placar = _format_placar(home_score, away_score)

        vencedor = None
        if status_tipo == "finished":
            winner_code = evt.get("winnerCode")
            if winner_code == _WINNER_CODE_HOME:
                vencedor = "home"
            elif winner_code == _WINNER_CODE_AWAY:
                vencedor = "away"

        return EventStatus(
            status=status_tipo,
            placar=placar,
            vencedor=vencedor,
            jogador1_nome=(evt.get("homeTeam") or {}).get("name"),
            jogador2_nome=(evt.get("awayTeam") or {}).get("name"),
        )

    return _run_with_browser(_buscar)


if __name__ == "__main__":
    # Uso: python sofascore_client.py --debug [YYYY-MM-DD]
    # Salva o JSON bruto do dia (hoje, se omitido) em sofascore_debug.json
    # para você conferir se os nomes de campo usados em _extract_event()
    # batem com a resposta real da API na sua rede.
    import sys

    logging.basicConfig(level=logging.INFO)
    dia = datetime.now()
    if len(sys.argv) > 2:
        dia = datetime.strptime(sys.argv[2], "%Y-%m-%d")

    eventos = _run_with_browser(lambda page: _fetch_scheduled_events(page, dia))
    with open("sofascore_debug.json", "w", encoding="utf-8") as f:
        json.dump(eventos, f, indent=2, ensure_ascii=False)
    print(f"{len(eventos)} eventos salvos em sofascore_debug.json — confira a estrutura real.")
