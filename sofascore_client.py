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
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from playwright.sync_api import Page, sync_playwright

from config import settings
from models import Esporte
from nameutils import pair_matches

logger = logging.getLogger(__name__)

SOFASCORE_BASE_URL = "https://www.sofascore.com/api/v1"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
)

# Cache simples em memória por processo: evita bater várias vezes na mesma
# data quando várias tips do mesmo dia chegam em sequência. Chave inclui o
# esporte (ver _fetch_scheduled_events) para não misturar eventos de tênis e
# basquete do mesmo dia num cache compartilhado.
_cache: dict[str, list[dict]] = {}

# Slug de esporte usado nos endpoints do SofaScore (/sport/{slug}/...).
# "tennis" confirmado ao vivo por este projeto (ver docstring do módulo).
# "basketball" confirmado ao vivo em 03/09/2026: /sport/basketball/
# scheduled-tournaments/{data}/page/1 devolveu 29 torneios reais no dia, e
# /event/{id} de jogos finalizados reais (ex: Catarinense, Paranaense)
# devolveu homeScore/awayScore com period1..period4 (4 quartos) + campos
# normaltime/overtime — mesmo formato assumido por _extrair_sets, sem
# ajuste necessário.
_SPORT_SLUGS = {
    Esporte.TENIS.value: "tennis",
    Esporte.BASQUETE.value: "basketball",
}

# Janela máxima entre o jogo devolvido pelo SofaScore e o momento da tip,
# usada em find_canonical_match_by_name. Bug real (03/09/2026): sem esse
# limite, "Alexandra muller" casou com uma dupla feminina de JUNHO DE 2021
# e "Hara friend" com uma dupla de julho/2026 — o endpoint
# /team/{id}/featured-event devolve o último jogo conhecido do jogador,
# que para quem não tem jogo agendado é um jogo antigo qualquer. Como o
# código só escolhia o candidato "mais próximo" sem piso de qualidade,
# aceitava esse lixo e gravava a aposta no confronto errado.
#
# 36h cobre o caso real de uso (o tipster manda tips de hoje e de amanhã
# de manhã, ver mensagens 122/123 do grupo) com folga pra fuso horário,
# sem chegar perto de pegar um jogo de outra semana.
_JANELA_MAX_POR_NOME = timedelta(hours=36)


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
    # Mesma informação de `placar`, mas estruturada: 1 tupla (games_home,
    # games_away) por set já disputado, na ordem. Usado por score_updater.py
    # pra descobrir quem ganhou um set específico (ex: conferir uma aposta
    # "vencer o 2º set") sem precisar reparsear a string `placar`.
    sets: list[tuple[int, int]] = field(default_factory=list)


def _polite_delay() -> None:
    """Pequeno atraso aleatório entre chamadas — evita bater feito metralhadora no mesmo host."""
    time.sleep(random.uniform(0.4, 1.0))


# Status codes que valem retry: bloqueios/rate-limit temporários e erros
# de servidor — confirmado em produção que o SofaScore devolve 403 mesmo
# pra requests legítimos de vez em quando (provavelmente bloqueio momentâneo
# da faixa de IP compartilhada dos runners do GitHub Actions; o mesmo
# request refeito minutos depois, de outra rede, funcionou normalmente).
# 404 fica de fora de propósito — é resposta válida de "não existe", não um
# erro transitório, e re-tentar só atrasaria a resposta correta (None).
_RETRYABLE_STATUS = {403, 408, 429, 500, 502, 503, 504}


def _get_json(page: Page, url: str, tentativas: int = 3) -> Optional[dict]:
    for tentativa in range(tentativas):
        try:
            _polite_delay()
            resp = page.goto(url, timeout=20_000)
            status = getattr(resp, "status", None)
            if resp is not None and resp.ok:
                return resp.json()
            if status not in _RETRYABLE_STATUS or tentativa == tentativas - 1:
                logger.warning("SofaScore respondeu %s para %s", status, url)
                return None
            espera = 2 ** tentativa + random.uniform(0, 1)  # 1-2s, 2-3s, ...
            logger.warning(
                "SofaScore respondeu %s para %s (tentativa %d/%d) — retry em %.1fs",
                status, url, tentativa + 1, tentativas, espera,
            )
            time.sleep(espera)
        except Exception:
            if tentativa == tentativas - 1:
                logger.exception("Falha ao consultar SofaScore (%s)", url)
                return None
            logger.warning("Erro ao consultar SofaScore (%s), tentativa %d/%d", url, tentativa + 1, tentativas)
            time.sleep(2 ** tentativa + random.uniform(0, 1))
    return None


def _list_tournament_ids(page: Page, dia: datetime, esporte: str) -> list[int]:
    """Etapa 1: lista os IDs de todo torneio do esporte com jogo agendado no dia (todas as páginas)."""
    data_str = dia.strftime("%Y-%m-%d")
    slug = _SPORT_SLUGS[esporte]
    ids: set[int] = set()
    pagina = 1
    while True:
        url = f"{SOFASCORE_BASE_URL}/sport/{slug}/scheduled-tournaments/{data_str}/page/{pagina}"
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


def _fetch_scheduled_events(page: Page, dia: datetime, esporte: str = Esporte.TENIS.value) -> list[dict]:
    """Etapa 2: para cada torneio do dia, busca os jogos (homeTeam/awayTeam/startTimestamp)."""
    data_str = dia.strftime("%Y-%m-%d")
    cache_key = f"{esporte}:{data_str}"
    if cache_key in _cache:
        return _cache[cache_key]

    eventos: list[dict] = []
    for tournament_id in _list_tournament_ids(page, dia, esporte):
        url = f"{SOFASCORE_BASE_URL}/unique-tournament/{tournament_id}/scheduled-events/{data_str}"
        data = _get_json(page, url)
        if data:
            eventos.extend(data.get("events", []))

    _cache[cache_key] = eventos
    logger.debug("SofaScore devolveu %d eventos de %s para %s", len(eventos), esporte, data_str)
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
            # startTimestamp é epoch UTC. `fromtimestamp(ts)` sem tz converte
            # pro fuso da MÁQUINA que está rodando — bug real (03/09/2026): o
            # mesmo jogo virava 08:00 processado no PC local (UTC-3) e 11:00
            # no runner do GitHub Actions (UTC), e como o horário ia ingênuo
            # pro TIMESTAMPTZ do Postgres, apostas com jogo só de manhã eram
            # promovidas a "ao_vivo" na hora. Fixar em UTC aqui mantém o
            # instante correto independente de onde o pipeline roda; a
            # exibição em horário de Brasília fica com quem apresenta.
            data_hora = datetime.fromtimestamp(int(ts), tz=timezone.utc)
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
    esporte: str = Esporte.TENIS.value,
    dias_de_busca: int = 3,
    referencia: Optional[datetime] = None,
) -> Optional[CanonicalMatch]:
    """
    Varre hoje + os próximos `dias_de_busca` dias no SofaScore procurando um
    confronto cujos nomes batam (fuzzy) com jogador1/jogador2. Devolve None
    se não encontrar — o chamador (matcher.py) decide o que fazer (ex: cair
    para os dados crus do OCR).
    """
    # Aware (UTC) pra manter a aritmética de datas consistente com o
    # data_hora dos candidatos, que agora também é aware — ver _extract_event.
    referencia = referencia or datetime.now(timezone.utc)
    threshold = settings.SUPERBET_FUZZY_THRESHOLD  # mesmo limiar usado nas casas de apostas

    def _buscar(page: Page) -> Optional[CanonicalMatch]:
        for offset in range(dias_de_busca + 1):
            dia = referencia + timedelta(days=offset)
            for evt in _fetch_scheduled_events(page, dia, esporte):
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


def find_canonical_match_by_name(
    jogador: str,
    aceitar_duplas: bool = False,
    esporte: str = Esporte.TENIS.value,
    referencia: Optional[datetime] = None,
) -> Optional[CanonicalMatch]:
    """
    Variante de find_canonical_match() para quando o tipster cita só o
    jogador favorito, sem o adversário (ex: "Thomas Faurel odd: 1.74") — o
    adversário nesse caso vem do próprio SofaScore.

    Usa a busca por nome do próprio SofaScore (GET /api/v1/search/all?q=...)
    em vez de varrer todos os torneios de tênis do dia — calibrado ao vivo
    em 02/09/2026: mais rápido (2 chamadas de API em vez de ~80+) e evita um
    bug real observado varrendo o dia inteiro (o Chromium crasha depois de
    muitas navegações seguidas na mesma page). Para cada jogador que a busca
    devolver com esse nome (pode haver mais de um — nomes se repetem), busca
    o jogo mais recente/atual dele (GET /api/v1/team/{id}/featured-event) e
    fica com o candidato cujo confronto está mais perto de `referencia`
    (agora, por padrão) — jogos muito no passado ou muito no futuro (torneio
    diferente, mesmo nome de outro jogador) são descartados.
    """
    # Aware (UTC): o data_hora dos candidatos é aware desde _extract_event,
    # e subtrair aware de ingênuo é TypeError.
    referencia = referencia or datetime.now(timezone.utc)

    def _buscar(page: Page) -> Optional[CanonicalMatch]:
        search_url = f"{SOFASCORE_BASE_URL}/search/all?q={jogador}"
        search_data = _get_json(page, search_url)
        if not search_data:
            return None

        player_ids = []
        for r in search_data.get("results", []):
            entity = r.get("entity") or {}
            sport = (entity.get("sport") or {}).get("slug")
            # a busca cobre todos os esportes/times/torneios; filtra só
            # entidades do esporte pedido com id (jogadores/times aparecem
            # como type="team" nesse endpoint — é como o SofaScore trata
            # "participante" aqui, tanto pra tênis quanto pra basquete).
            if sport == _SPORT_SLUGS[esporte] and entity.get("id"):
                player_ids.append(entity["id"])

        if not player_ids:
            return None

        melhor: Optional[CanonicalMatch] = None
        menor_diferenca: Optional[float] = None
        for pid in player_ids:
            featured_url = f"{SOFASCORE_BASE_URL}/team/{pid}/featured-event"
            data = _get_json(page, featured_url)
            evt = (data or {}).get("featuredEvent")
            if not evt:
                continue
            candidato = _extract_event(evt)
            if not candidato or not candidato.data_hora:
                continue
            diferenca = abs((candidato.data_hora - referencia).total_seconds())
            # Piso de qualidade: featured-event devolve o último jogo
            # conhecido do jogador, então um jogador sem jogo agendado
            # traz um confronto antigo. Sem esse corte, "o mais próximo"
            # vira "o menos absurdo" e a aposta é gravada no jogo errado
            # (ver _JANELA_MAX_POR_NOME).
            if diferenca > _JANELA_MAX_POR_NOME.total_seconds():
                logger.info(
                    "SofaScore: descartando %s x %s (%s) para '%s' — %.1f dias "
                    "de distância da tip, fora da janela de %s.",
                    candidato.jogador1_oficial, candidato.jogador2_oficial,
                    candidato.data_hora, jogador,
                    diferenca / 86400, _JANELA_MAX_POR_NOME,
                )
                continue
            # Descarta jogo de DUPLAS. O tipster aposta em simples, e o
            # featured-event de um tenista pode perfeitamente ser a dupla
            # dele — foi o que aconteceu com a tip "de la torre" em
            # 03/09/2026: rejeitado o jogo de amanhã (certo), a busca por
            # nome trouxe "Ingildsen/Poulsen x Friend/Montes-de la Torre"
            # em vez do simples de hoje contra Daniel Rincon.
            #
            # O SofaScore nomeia duplas com "/" separando os parceiros,
            # igual à Superbet (ver matcher._escolher_confronto).
            # aceitar_duplas: a tip disse "na duplas", então o jogo de
            # duplas é justamente o desejado (ver matcher.tip_e_de_duplas).
            e_duplas = "/" in candidato.jogador1_oficial or "/" in candidato.jogador2_oficial
            if e_duplas and not aceitar_duplas:
                logger.info(
                    "SofaScore: descartando %s x %s para '%s' — é jogo de duplas.",
                    candidato.jogador1_oficial, candidato.jogador2_oficial, jogador,
                )
                continue
            if menor_diferenca is None or diferenca < menor_diferenca:
                menor_diferenca = diferenca
                melhor = candidato

        if melhor:
            logger.info(
                "SofaScore confirmou (nome único): %s x %s | %s | %s",
                melhor.jogador1_oficial, melhor.jogador2_oficial,
                melhor.torneio_oficial, melhor.data_hora,
            )
        return melhor

    resultado = _run_with_browser(_buscar)
    if resultado is None:
        logger.warning("SofaScore: confronto não encontrado (nome único) para %s", jogador)
    return resultado


# Winner code confirmado ao vivo (evento finalizado, 31/08/2026): 1 = casa,
# presume-se 2 = visitante (não confirmado ao vivo, mas é o padrão universal
# de outros esportes na mesma API — ajuste aqui se algum dia vier diferente).
_WINNER_CODE_HOME = 1
_WINNER_CODE_AWAY = 2


_MAX_PERIODOS = 9  # tênis: até 5 sets. basquete: 4 quartos + overtimes (raramente mais de 1-2, mas sem teto formal)


def _extrair_sets(home_score: dict, away_score: dict) -> list[tuple[int, int]]:
    """Lista de (pontos_home, pontos_away) por período, a partir dos campos
    period1..periodN de homeScore/awayScore. Períodos ainda não jogados vêm
    ausentes/None e são ignorados.

    Nome mantido "sets"/"_extrair_sets" por compatibilidade com todo código
    existente (EventStatus.sets, resultado_checker.py, score_updater.py,
    espelho TS) — para tênis é games-por-set, para basquete é pontos-por-
    quarto/overtime (ver models.Esporte). A estrutura (list[tuple[int,int]])
    é idêntica nos dois casos, só a semântica muda."""
    sets = []
    for i in range(1, _MAX_PERIODOS + 1):
        chave = f"period{i}"
        h = home_score.get(chave)
        a = away_score.get(chave)
        if h is None or a is None:
            continue
        sets.append((int(h), int(a)))
    return sets


def _format_placar(sets: list[tuple[int, int]]) -> Optional[str]:
    """Monta "7-5, 3-6, 2-6, 4-2" a partir da lista de sets estruturada."""
    return ", ".join(f"{h}-{a}" for h, a in sets) if sets else None


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
        sets = _extrair_sets(home_score, away_score)

        vencedor = None
        if status_tipo == "finished":
            winner_code = evt.get("winnerCode")
            if winner_code == _WINNER_CODE_HOME:
                vencedor = "home"
            elif winner_code == _WINNER_CODE_AWAY:
                vencedor = "away"

        return EventStatus(
            status=status_tipo,
            placar=_format_placar(sets),
            vencedor=vencedor,
            jogador1_nome=(evt.get("homeTeam") or {}).get("name"),
            jogador2_nome=(evt.get("awayTeam") or {}).get("name"),
            sets=sets,
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
