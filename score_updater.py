"""
score_updater.py
=================
Processo separado (rode em paralelo ao listener.py) que
acompanha o placar/resultado ao vivo das apostas já confirmadas no
SofaScore, usando o sofascore_event_id salvo por listener.py/matcher.py.

Uso:
    python score_updater.py                  # loop contínuo (padrão: ~3 min entre ciclos)
    python score_updater.py --once           # roda um ciclo só e sai (útil pra testar)
    python score_updater.py --interval 120   # ciclo a cada 120s em vez do padrão

A cada ciclo:
  1. Busca no banco toda aposta com status "agendada" ou "ao_vivo"
     (list_trackable_bets, em database.py). As que têm sofascore_event_id
     são consultadas por id; as confirmadas pelo 365scores/Superbet (sem
     event_id do SofaScore) são consultadas por nome — ver _consultar_status.
  2. Para cada uma, consulta get_event_status() (sofascore_client.py):
       - "inprogress" e a aposta ainda estava "agendada" -> promove pra "ao_vivo".
       - "finished" -> grava placar_final + vencedor_partida, muda o status
         para "encerrada" e manda uma notificação avisando o resultado.
       - "notstarted" -> não faz nada (ainda não começou).
  3. Reconsulta o matcher.py para toda aposta "nao_encontrada" das últimas
     JANELA_DE_RETRY horas (list_unmatched_bets, em database.py) —
     necessário desde que o listener.py passou a rodar em lote (poll
     periódico, em vez de processar cada mensagem instantaneamente): uma
     tip cujo jogo a fonte de dados ainda não tinha listado no momento do
     poll não tem mais uma "próxima mensagem" natural que a reprocesse,
     então esse retry cobre esse caso. A janela é o que impede essa fila
     de crescer para sempre — ver JANELA_DE_RETRY.
  4. Espera um atraso aleatório entre cada partida consultada (mesmo espírito
     "educado" dos outros módulos que batem no SofaScore/casas de apostas).

Não decide se A APOSTA em si ganhou ou perdeu (isso depende do mercado
específico, ex: "vencedor do 1º set" vs "vencedor da partida") — só informa
o resultado da partida, cabe a você conferir se bateu com sua aposta.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import resultado_checker
from config import settings
from database import (
    init_db,
    list_bets_sem_link_exato,
    list_trackable_bets,
    list_unmatched_bets,
    update_links,
    update_match_info,
    update_score_result,
)
from matcher import build_enabled_adapters, find_match
from models import Bet, BetStatus, ResultadoAposta
from notifier import send_plain_message
from scores365_client import find_status_by_names
from sofascore_client import EventStatus, get_event_status

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(settings.resolve_path("logs") / "score_updater.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("score_updater")

DEFAULT_INTERVAL_SECONDS = 180  # 3 minutos

# Por quanto tempo uma aposta "não encontrada" continua sendo retentada.
#
# Sem esse teto, TODA aposta não encontrada de TODOS os dias era retentada
# a cada ciclo, para sempre — e como a busca do confronto olha do dia da
# tip em diante, um jogo que já aconteceu nunca poderia ser achado. A fila
# só crescia: cada dia empilhava as novas sobre as velhas, o ciclo ficava
# mais lento, e passava a estourar o timeout do workflow (10 min) antes de
# chegar nas apostas do dia — ou seja, as antigas (insalváveis) impediam o
# retry das novas (salváveis). 36h cobre com folga a tip da madrugada cujo
# jogo é no dia seguinte.
JANELA_DE_RETRY = timedelta(hours=36)


def _polite_delay() -> None:
    """Mesmo espírito dos outros módulos: não bater no SofaScore feito metralhadora."""
    time.sleep(random.uniform(1.0, 3.0))


def _vencedor_nome(bet: Bet, evt: EventStatus) -> str | None:
    """Traduz "home"/"away" pro nome real do jogador. Prioriza os nomes
    oficiais do SofaScore (evt) — mais confiáveis que os salvos na aposta,
    que podem ter vindo só do OCR se o matcher não tiver confirmado."""
    if evt.vencedor == "home":
        return evt.jogador1_nome or bet.jogador1
    if evt.vencedor == "away":
        return evt.jogador2_nome or bet.jogador2
    return None


def _consultar_status(bet: Bet) -> Optional[EventStatus]:
    """
    SofaScore é a fonte primária (mesmo event_id salvo desde o matcher.py).
    Se falhar, tenta 365scores como fallback, buscando por nome dos
    jogadores — ver scores365_client.py para o motivo (SofaScore às vezes
    bloqueia TODO o IP do runner do GitHub Actions com 403, não só um
    request pontual, e o retry de sofascore_client não ajuda nesse caso
    porque o IP continua o mesmo).
    """
    if bet.sofascore_event_id is not None:
        evt = get_event_status(bet.sofascore_event_id)
        if evt is not None:
            return evt
        logger.warning(
            "Aposta #%s: SofaScore falhou (evento %s), tentando 365scores.",
            bet.id, bet.sofascore_event_id,
        )
    else:
        # Confronto confirmado pelo 365scores ou pela Superbet — não existe
        # event_id do SofaScore pra consultar, então vai direto pra busca
        # por nome (ver matcher.find_match).
        logger.debug("Aposta #%s: sem event_id do SofaScore, consultando o 365scores por nome.", bet.id)

    try:
        evt = find_status_by_names(
            bet.jogador1, bet.jogador2,
            threshold=settings.SUPERBET_FUZZY_THRESHOLD,
            esporte=bet.esporte,
            # Ancorar no horário do jogo (quando conhecido) evita procurar
            # no dia errado quando o ciclo roda depois da virada do dia.
            referencia=bet.data_hora or bet.criado_em,
        )
    except Exception:
        logger.exception("Aposta #%s: 365scores também falhou.", bet.id)
        return None
    if evt is not None:
        logger.info("Aposta #%s: status obtido via 365scores (fallback).", bet.id)
    return evt


def _formata_placar_por_linha(evt: EventStatus) -> str:
    """"6-4\n3-6\n6-2" em vez de "6-4, 3-6, 6-2" — mais legível na
    notificação do Telegram (ver formato pedido pelo usuário)."""
    if not evt.sets:
        return evt.placar or "(placar indisponível)"
    return "\n".join(f"Set {i}: {h}-{a}" for i, (h, a) in enumerate(evt.sets, start=1))


def _resultado_emoji(resultado: Optional[str]) -> str:
    if resultado == ResultadoAposta.GREEN.value:
        return "✅"
    if resultado == ResultadoAposta.RED.value:
        return "❌"
    return "⏳ Confira manualmente (mercado não reconhecido automaticamente)"


def _processar_bet(bet: Bet, evt: EventStatus) -> None:
    """Atualiza o status/resultado da aposta no banco a partir do EventStatus
    já consultado (ver _processar_jogo, que consulta 1x por evento e chama
    isto pra cada aposta daquele evento)."""
    if evt.status == "finished":
        vencedor = _vencedor_nome(bet, evt)
        resultado = resultado_checker.checar_resultado(bet.mercado, bet.jogador1, bet.jogador2, evt, esporte=bet.esporte)
        update_score_result(
            bet.id,
            status=BetStatus.ENCERRADA.value,
            placar_final=evt.placar,
            vencedor_partida=vencedor,
            resultado=resultado,
        )
        logger.info(
            "Aposta #%s: partida encerrada — %s venceu %s (resultado: %s)",
            bet.id, vencedor, evt.placar, resultado or "não determinado",
        )

    elif evt.status == "inprogress":
        # Placar parcial (reaproveita a coluna placar_final — quando o jogo
        # ainda está rolando, ela guarda "o placar mais recente conhecido",
        # não necessariamente o final; ver Bet.placar_final/app.py, que só
        # mostra "🏆 X venceu" quando status == encerrada) atualizado a
        # cada ciclo enquanto a partida estiver ao vivo, não só na primeira
        # vez que sai de "agendada" — script_updater.py roda a cada ~poucos
        # minutos, então o placar do app.py nunca fica muito desatualizado.
        novo_status = BetStatus.AO_VIVO.value
        if bet.status == BetStatus.AGENDADA.value:
            logger.info("Aposta #%s: partida começou (ao vivo).", bet.id)
        update_score_result(bet.id, status=novo_status, placar_final=evt.placar)

    elif evt.status == "notstarted" and bet.status == BetStatus.AO_VIVO.value:
        # Corrige um "ao vivo" que não corresponde à realidade. Acontecia de
        # verdade (03/09/2026): enquanto o data_hora era gravado no fuso da
        # máquina (ver _extract_event em sofascore_client.py), jogos de
        # manhã entravam como já começados e eram promovidos a ao_vivo; o
        # SofaScore dizia "notstarted", mas este ramo só logava em debug,
        # então o status errado ficava grudado pra sempre no painel — 4
        # jogos apareciam "ao vivo" de madrugada, um deles com 8h de
        # antecedência. A promoção era irreversível; agora não é.
        logger.info(
            "Aposta #%s: SofaScore diz que ainda não começou — revertendo ao_vivo para agendada.",
            bet.id,
        )
        update_score_result(bet.id, status=BetStatus.AGENDADA.value)

    else:
        logger.debug("Aposta #%s: sem mudança (status SofaScore=%s)", bet.id, evt.status)


def _monta_notificacao_encerrada(apostas: list[Bet], evt: EventStatus) -> str:
    """
    1 notificação por JOGO (não por aposta) — se houver mais de 1 aposta no
    mesmo confronto (ex: "vencedor da partida" feita antes + "vencer o 2º
    set" feita ao vivo depois), lista o resultado de cada uma junto, em vez
    de mandar uma mensagem quase idêntica repetida. Ver run_once/
    _processar_jogo, que agrupa por sofascore_event_id antes de chamar isto.
    """
    vencedor = _vencedor_nome(apostas[0], evt)
    linhas = [f"🏁 Partida encerrada: {vencedor or '?'} venceu", "", _formata_placar_por_linha(evt), ""]
    for bet in apostas:
        resultado = resultado_checker.checar_resultado(bet.mercado, bet.jogador1, bet.jogador2, evt, esporte=bet.esporte)
        odd_txt = f"{bet.odd:.2f}" if bet.odd is not None else "?"
        linhas.append(f"• {bet.mercado or 'mercado não identificado'} (odd {odd_txt}) — {_resultado_emoji(resultado)}")
    return "\n".join(linhas)


async def _enviar_notificacoes(textos: list[str]) -> None:
    for texto in textos:
        await send_plain_message(texto)


def _retentar_bet_nao_encontrada(bet: Bet) -> None:
    """Reconsulta o matcher.py pra uma aposta que não achou o confronto na
    primeira tentativa — ver docstring do módulo pra contexto.

    bet.jogador2 == "?" é o placeholder que listener.py salva quando só o
    favorito foi citado e o SofaScore não confirmou o adversário na 1ª
    tentativa (ver listener.py, jogador2 NOT NULL no banco) — precisa virar
    None aqui, senão find_match tentaria achar um confronto contra um
    jogador literal chamado "?" (bug real: "?" é truthy em Python, então
    `if jogador2:` em matcher.find_match não detectava o placeholder e
    nunca reencaminhava pra find_canonical_match_by_name)."""
    jogador2 = bet.jogador2 if bet.jogador2 != "?" else None
    # referencia = quando a tip chegou, não "agora": a busca do confronto é
    # uma janela de dias ao redor da referência, e ancorar em "agora" fazia
    # o retry procurar o jogo da tip de ontem no dia de hoje — nunca achava.
    match = find_match(
        bet.jogador1, jogador2, bet.esporte,
        referencia=bet.criado_em,
        odd_tip=bet.odd,
        # fonte_texto guarda a legenda/OCR original — é onde está o "na
        # duplas" que autoriza o matcher a aceitar confronto de duplas.
        texto_tip=bet.fonte_texto or bet.mercado,
    )
    if not match.encontrado:
        logger.debug("Aposta #%s: ainda não encontrada (%s x %s)", bet.id, bet.jogador1, bet.jogador2)
        return

    # datetime.now(timezone.utc): match.data_hora vem aware (UTC) do
    # SofaScore — comparar com um now() ingênuo é TypeError.
    agora = datetime.now(timezone.utc)
    status = BetStatus.AO_VIVO.value if (match.data_hora and match.data_hora <= agora) else BetStatus.AGENDADA.value
    update_match_info(
        bet.id,
        data_hora=match.data_hora,
        links=match.links,
        status=status,
        sofascore_event_id=match.sofascore_event_id,
        jogador1=match.jogador1_oficial or bet.jogador1,
        jogador2=match.jogador2_oficial or bet.jogador2,
        torneio=match.torneio_oficial,
    )
    logger.info(
        "Aposta #%s: confronto encontrado no retry — %s x %s",
        bet.id, match.jogador1_oficial or bet.jogador1, match.jogador2_oficial or bet.jogador2,
    )


def _retentar_link_exato(bet: Bet) -> None:
    """Tenta completar o link da partida na Superbet numa aposta cujo
    confronto já está confirmado.

    A busca da Superbet é intermitente: se ela não responde no instante em
    que a tip chega, o adaptador cai no link aproximado (a listagem do dia)
    e antes nada tentava de novo — a aposta ficava sem o link da partida
    pra sempre. Aqui os nomes oficiais já estão gravados, então
    find_exact_link tem tudo o que precisa (era o caso da #116,
    Ajdukovic x Clarke: o link exato aparecia na primeira consulta feita
    depois, com os mesmos nomes).

    Só a Superbet: ver list_bets_sem_link_exato.
    """
    adapter = next((a for a in build_enabled_adapters() if a.slug == "superbet"), None)
    if adapter is None:
        return

    url = adapter.find_exact_link(
        bet.jogador1, bet.jogador2, bet.torneio, bet.data_hora, bet.esporte
    )
    if not url:
        logger.debug("Aposta #%s: Superbet ainda sem link exato.", bet.id)
        return

    links = dict(bet.links or {})
    links["superbet"] = {"nome": adapter.display_name, "url": url, "exato": True}
    update_links(bet.id, links)
    logger.info("Aposta #%s: link exato da Superbet completado — %s", bet.id, url)


def run_once() -> int:
    """Roda um ciclo: consulta todas as apostas rastreáveis, retenta as não
    encontradas e completa os links exatos que faltaram. Devolve quantas
    foram processadas no total."""
    apostas = list_trackable_bets()
    logger.info("Ciclo iniciado: %d aposta(s) para acompanhar.", len(apostas))

    # Agrupa por sofascore_event_id: consulta o status 1x por JOGO (não 1x
    # por aposta) — economiza chamadas de rede quando há mais de 1 aposta no
    # mesmo confronto, e permite juntar todas numa única notificação (ver
    # _monta_notificacao_encerrada) em vez de mandar 1 mensagem quase
    # idêntica por aposta.
    # Apostas sem event_id (confronto confirmado pelo 365scores/Superbet)
    # são consultadas por NOME, uma a uma — não dá pra agrupar todas sob a
    # chave None, senão o status de UM jogo seria aplicado a apostas de
    # jogos diferentes.
    apostas_por_evento: dict[object, list[Bet]] = {}
    for bet in apostas:
        chave = bet.sofascore_event_id if bet.sofascore_event_id is not None else f"sem_event_id:{bet.id}"
        apostas_por_evento.setdefault(chave, []).append(bet)

    notificacoes: list[str] = []
    for event_id, apostas_do_evento in apostas_por_evento.items():
        try:
            evt = _consultar_status(apostas_do_evento[0])
            if evt is None:
                logger.warning(
                    "Evento %s (%d aposta(s)): falha ao consultar SofaScore e 365scores, tentando de novo no próximo ciclo.",
                    event_id, len(apostas_do_evento),
                )
                continue

            for bet in apostas_do_evento:
                _processar_bet(bet, evt)

            if evt.status == "finished":
                notificacoes.append(_monta_notificacao_encerrada(apostas_do_evento, evt))
        except Exception:
            logger.exception("Erro ao processar evento %s (%d aposta(s))", event_id, len(apostas_do_evento))
        _polite_delay()

    if notificacoes:
        asyncio.run(_enviar_notificacoes(notificacoes))

    nao_encontradas = list_unmatched_bets(desde=datetime.now(timezone.utc) - JANELA_DE_RETRY)
    if nao_encontradas:
        logger.info(
            "Retentando %d aposta(s) não encontrada(s) das últimas %s.",
            len(nao_encontradas), JANELA_DE_RETRY,
        )
    for bet in nao_encontradas:
        try:
            _retentar_bet_nao_encontrada(bet)
        except Exception:
            logger.exception("Erro ao retentar aposta #%s (%s x %s)", bet.id, bet.jogador1, bet.jogador2)
        _polite_delay()

    # Confronto confirmado, mas o link da Superbet ficou aproximado — o
    # link exato é a conveniência principal do painel (abre a partida, não
    # a listagem do dia inteiro), e a busca da casa costuma responder num
    # ciclo seguinte. Ver _retentar_link_exato.
    sem_link = list_bets_sem_link_exato(desde=datetime.now(timezone.utc) - JANELA_DE_RETRY)
    if sem_link:
        logger.info("Completando o link exato de %d aposta(s).", len(sem_link))
    for bet in sem_link:
        try:
            _retentar_link_exato(bet)
        except Exception:
            logger.exception("Erro ao buscar link exato da aposta #%s", bet.id)
        _polite_delay()

    return len(apostas) + len(nao_encontradas)


def run_loop(interval_seconds: int) -> None:
    logger.info("score_updater iniciado — ciclo a cada ~%ss.", interval_seconds)
    while True:
        run_once()
        time.sleep(interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Acompanha placar/resultado ao vivo via SofaScore.")
    parser.add_argument("--once", action="store_true", help="Roda um ciclo só e sai (útil pra testar).")
    parser.add_argument(
        "--interval", type=int, default=DEFAULT_INTERVAL_SECONDS,
        help=f"Segundos entre ciclos no modo contínuo (padrão: {DEFAULT_INTERVAL_SECONDS}).",
    )
    args = parser.parse_args()

    init_db()

    if args.once:
        run_once()
    else:
        run_loop(args.interval)


if __name__ == "__main__":
    main()
