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
  - statusText: "Fim" quando terminado, "Programação" quando agendado,
    texto livre tipo "1º set"/"2º set" quando em andamento.
  - statusGroup é o campo confiável pra saber a fase do jogo. Levantado
    sobre os 92 jogos de tênis de 03/09/2026 (ver _STATUS_GROUP_*):
        grp=2 -> agendado      ("Programação"), 88 jogos
        grp=3 -> em andamento  ("1º set"/"2º set"/"3º set"), 3 jogos
        grp=4 -> encerrado/cancelado ("Cancelado"), 1 jogo
    Não foi observado grp=1 em nenhum jogo.
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
from datetime import datetime, timedelta, timezone
from typing import Optional

from playwright.sync_api import Page, sync_playwright

from zoneinfo import ZoneInfo

from models import Esporte
from nameutils import names_match, pair_matches
from sofascore_client import CanonicalMatch, EventStatus

logger = logging.getLogger(__name__)

_BASE_URL = "https://webws.365scores.com/web/games/allscores/"
_SPORT_ID_TENIS = 3
# Confirmado ao vivo em 03/09/2026 via GET https://webws.365scores.com/web/
# sports/ (endpoint público de metadados do 365scores, devolve a lista
# completa de esportes com id/name): {"id": 2, "name": "Basketball",
# "nameForURL": "basketball"}. Validado batendo sports=2 contra o dia real —
# devolveu jogos de clubes reais de basquete (Joventut de Badalona, Virtus
# Bologna, Turk Telekom etc.), mesmo formato de campos que o tênis.
_SPORT_ID_BASQUETE = 2

_SPORT_IDS = {
    Esporte.TENIS.value: _SPORT_ID_TENIS,
    Esporte.BASQUETE.value: _SPORT_ID_BASQUETE,
}

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
)

# Nome do stage de tênis ("1º set"..) confirmado ao vivo (ver docstring do
# módulo). "quarto"/"tempo" aceitos por analogia para basquete, mas na
# prática o campo `stages` do 365scores vem sempre None/vazio para basquete
# (confirmado ao vivo em 03/09/2026, ~109 jogos finalizados de vários dias,
# nenhum com stages preenchido — o próprio objeto do jogo traz
# "hasPointByPoint": false). Ou seja: para basquete, este fallback nunca
# devolve placar por quarto (EventStatus.sets fica sempre []), só o placar
# final e o vencedor (via homeCompetitor/awayCompetitor.score/isWinner) —
# suficiente para conferir moneyline, mas NÃO para handicap/total de pontos
# (ver resultado_checker._checar_resultado_basquete, que precisa de
# evt.sets). Isso só afeta o fallback: o SofaScore (fonte primária) traz
# period1..4 normalmente para basquete (ver sofascore_client.py).
_SET_STAGE_PATTERN = re.compile(r"^(\d)º (?:set|quarto|tempo)$")

# statusGroup do 365scores -> fase do jogo. Levantado sobre os 92 jogos de
# tênis de 03/09/2026 (ver docstring do módulo).
#
# Bug real que isto corrige: o código antes tratava só `statusGroup == 1`
# como "não começou" (um chute, comentado no próprio código como "não
# confirmado formalmente") e mandava TODO o resto pro `else` = "em
# andamento". Como o valor real de jogo agendado é 2, as 6 apostas do dia
# viraram "ao vivo" às 02h da manhã — jogos que só começavam às 09:00.
# Ninguém notou antes porque isto só roda quando o SofaScore falha, e o
# SofaScore bloqueia o IP do runner do GitHub Actions com 403 justamente
# em produção (onde nenhum humano lê o log em tempo real).
_STATUS_GROUP_AGENDADO = 2
_STATUS_GROUP_EM_ANDAMENTO = 3
_STATUS_GROUP_ENCERRADO = 4


# Fuso pedido no request (`timezoneName=America/Sao_Paulo`) — é nele que o
# 365scores devolve horário e agrupa os jogos por dia, então é nele que a
# data da consulta e os horários devolvidos têm que ser interpretados.
_FUSO_DO_SITE = ZoneInfo("America/Sao_Paulo")

# Formatos de horário já vistos nessa API, tentados depois do ISO-8601.
_FORMATOS_DATA = ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M")


def _no_fuso_do_site(dia: datetime) -> datetime:
    """Mesma instante, expresso no fuso que pedimos ao 365scores."""
    if dia.tzinfo is None:
        return dia
    return dia.astimezone(_FUSO_DO_SITE)


def _horario_do_game(game: dict) -> Optional[datetime]:
    """Horário de início do jogo, sempre devolvido em UTC (aware) — mesmo
    contrato do sofascore_client._extract_event, pra que o resto do
    pipeline não precise saber de qual fonte o confronto veio.

    Defensivo de propósito: se o campo mudar de nome ou de formato, isso
    devolve None (o confronto ainda é aproveitado, só sem horário) em vez
    de derrubar a busca inteira."""
    bruto = game.get("startTime") or game.get("gameStartTime") or game.get("startTimeStr")
    if not isinstance(bruto, str) or not bruto.strip():
        return None
    texto = bruto.strip().replace("Z", "+00:00")

    dt: Optional[datetime] = None
    try:
        dt = datetime.fromisoformat(texto)
    except ValueError:
        for formato in _FORMATOS_DATA:
            try:
                dt = datetime.strptime(texto, formato)
                break
            except ValueError:
                continue
    if dt is None:
        logger.warning("365scores: não consegui interpretar o horário %r do jogo.", bruto)
        return None

    # Sem fuso explícito = está no fuso que pedimos no request.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_FUSO_DO_SITE)
    return dt.astimezone(timezone.utc)


def _torneio_do_game(game: dict) -> Optional[str]:
    for chave in ("competitionDisplayName", "competitionName", "compName"):
        valor = game.get(chave)
        if isinstance(valor, str) and valor.strip():
            return valor.strip()
    return None


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


def _fetch_games(page: Page, dia: datetime, esporte: str = Esporte.TENIS.value) -> list[dict]:
    # O request pede timezoneName=America/Sao_Paulo, então a data tem que
    # ser a data NESSE fuso. Bug real que isto evita: o runner do GitHub
    # Actions roda em UTC, e entre 21:00 e 00:00 de Brasília "hoje" em UTC
    # já é o dia seguinte — a consulta ia pro dia errado justamente na
    # janela de virada, que é quando o tipster posta as tips (madrugada).
    data_str = _no_fuso_do_site(dia).strftime("%d/%m/%Y")
    sport_id = _SPORT_IDS[esporte]
    url = (
        f"{_BASE_URL}?appTypeId=5&langId=31&timezoneName=America/Sao_Paulo"
        f"&userCountryId=21&sports={sport_id}&startDate={data_str}&endDate={data_str}"
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

    grupo = game.get("statusGroup")
    if status_text == "fim":
        status = "finished"
    elif grupo == _STATUS_GROUP_EM_ANDAMENTO:
        status = "inprogress"
    elif grupo == _STATUS_GROUP_ENCERRADO:
        # Encerrado sem ser "Fim" (ex: "Cancelado", W.O.). Não é
        # "inprogress", e tratar como finished sem vencedor deixa o
        # resultado_checker decidir em vez de inventar um green/red.
        status = "finished"
    else:
        # Agendado (grupo 2) e qualquer valor novo/desconhecido. O default
        # seguro é "não começou": marcar um jogo como ao vivo por engano
        # trava a aposta num estado errado no painel, enquanto tratar um
        # jogo ao vivo como agendado se resolve no ciclo seguinte.
        if grupo is not None and grupo != _STATUS_GROUP_AGENDADO:
            logger.warning(
                "365scores: statusGroup=%r desconhecido (statusText=%r) — assumindo que não começou.",
                grupo, game.get("statusText"),
            )
        status = "notstarted"

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


def find_canonical_match_365(
    jogador1: str,
    jogador2: Optional[str],
    threshold: int,
    esporte: str = Esporte.TENIS.value,
    dias_de_busca: int = 2,
    dias_atras: int = 1,
    referencia: Optional[datetime] = None,
) -> Optional[CanonicalMatch]:
    """
    Confirma o CONFRONTO (nomes oficiais, torneio, horário) pelo 365scores —
    a mesma coisa que sofascore_client.find_canonical_match faz, mas por uma
    fonte independente e MUITO mais barata: 1 request por dia consultado,
    contra ~80 do SofaScore (que precisa listar os torneios do dia e depois
    pedir os jogos de cada um, ver a docstring de sofascore_client).

    Por isso esta é hoje a fonte PRIMÁRIA do matcher.py: além de barata, é
    a que continua respondendo quando o SofaScore devolve 403 pro IP do
    runner do GitHub Actions — que é o modo de falha real e recorrente
    deste projeto em produção.

    Devolve CanonicalMatch com `sofascore_event_id=None` (o id daqui é de
    outro site e não serve pra consultar o SofaScore depois) — o
    acompanhamento de placar dessas apostas é feito por nome, ver
    find_status_by_names e score_updater._consultar_status.

    A janela cobre `dias_atras` dias para trás porque a tip pode ser
    processada depois da virada do dia (o tipster posta de madrugada, e o
    runner pensa em UTC) — sem isso, um jogo de ontem à noite fica fora do
    alcance da busca e a aposta nunca é confirmada.
    """
    referencia = referencia or datetime.now(timezone.utc)

    def _buscar(page: Page) -> Optional[CanonicalMatch]:
        for offset in range(-dias_atras, dias_de_busca + 1):
            dia = referencia + timedelta(days=offset)
            for game in _fetch_games(page, dia, esporte):
                home = (game.get("homeCompetitor") or {}).get("name", "")
                away = (game.get("awayCompetitor") or {}).get("name", "")
                if not home or not away:
                    continue
                # jogador2 pode ser None (o tipster citou só o favorito).
                # pair_matches exige os DOIS nomes e nunca casa nesse caso —
                # bug real (04/09/2026): a tip "Trotter odd 1.50" ficou
                # "não encontrada" mesmo com o jogo listado aqui, porque a
                # busca era feita sem adversário. Sem jogador2, basta um dos
                # lados bater com jogador1.
                if jogador2:
                    casou = pair_matches(jogador1, jogador2, home, away, threshold)
                else:
                    casou = names_match(jogador1, home, threshold) or names_match(
                        jogador1, away, threshold
                    )
                if casou:
                    candidato = CanonicalMatch(
                        jogador1_oficial=home,
                        jogador2_oficial=away,
                        torneio_oficial=_torneio_do_game(game),
                        data_hora=_horario_do_game(game),
                        sofascore_event_id=None,
                    )
                    logger.info(
                        "365scores confirmou: %s x %s | %s | %s",
                        home, away, candidato.torneio_oficial, candidato.data_hora,
                    )
                    return candidato
        return None

    resultado = _run_with_browser(_buscar)
    if resultado is None:
        logger.warning("365scores: confronto não encontrado para %s x %s", jogador1, jogador2)
    return resultado


def find_status_by_names(
    jogador1: str,
    jogador2: str,
    threshold: int,
    esporte: str = Esporte.TENIS.value,
    dias_de_busca: int = 1,
    referencia: Optional[datetime] = None,
    dias_atras: int = 1,
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
    referencia = referencia or datetime.now(timezone.utc)

    def _buscar(page: Page) -> Optional[EventStatus]:
        # Também olha `dias_atras` dias para trás: um jogo que começou
        # ontem à noite no horário de Brasília já é "anteontem" para um
        # runner que pensa em UTC — sem isso o placar dele nunca era achado.
        for offset in range(-dias_atras, dias_de_busca + 1):
            dia = referencia + timedelta(days=offset)
            for game in _fetch_games(page, dia, esporte):
                home = (game.get("homeCompetitor") or {}).get("name", "")
                away = (game.get("awayCompetitor") or {}).get("name", "")
                if pair_matches(jogador1, jogador2, home, away, threshold):
                    return _game_to_status(game)
        return None

    resultado = _run_with_browser(_buscar)
    if resultado is None:
        logger.warning("365scores: jogo não encontrado para %s x %s", jogador1, jogador2)
    return resultado
