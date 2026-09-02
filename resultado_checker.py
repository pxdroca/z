"""
resultado_checker.py
=====================
Interpreta o texto livre de bet.mercado (montado por extractor.py) e decide
se a aposta bateu (green) ou não (red), dado o resultado real da partida
(sets já jogados + quem venceu — ver sofascore_client.EventStatus.sets).

Usado por:
  - score_updater.py: quando a partida termina, grava o resultado
    automaticamente (ver update_score_result em database.py) para os
    mercados que este módulo sabe interpretar com segurança.
  - app.py: mesma lógica, exibida como sugestão pro usuário (ver
    _sugerir_resultado) pra apostas que por algum motivo ainda não tiveram
    o resultado gravado automaticamente.

Cobertura hoje (só mercados que dá pra decidir com o dado que temos —
sets/vencedor da partida; NÃO cobre aces, games, dupla falta, placar
exato, tie-break, "vencer com/sem perder N sets" — esses seguem manuais):
  - "{Nome} vencer a partida" / "Vencedor da Partida" / "{Nome} ganhar"
    (padrão antigo) — compara com o vencedor real do jogo.
  - "{Nome} vencer o Nº set" — compara com quem ganhou aquele set exato.
  - "{Nome} vencer um set" (genérico, sem número) — o jogador ganhou pelo
    menos 1 set na partida (mesmo perdendo o jogo).
  - "{Nome} vencer sem perder set" (sem número) — o jogador venceu a
    partida E não perdeu nenhum set.

Devolve None quando o mercado não é reconhecido (mercados fora da lista
acima) ou quando falta dado pra decidir (ex: pediu o 3º set mas a partida
não chegou lá) — nesses casos o resultado continua "pendente", conferência
manual.
"""

from __future__ import annotations

import re
from typing import Optional

from models import ResultadoAposta
from nameutils import names_match
from sofascore_client import EventStatus

_THRESHOLD_NOME = 80

_PADRAO_PARTIDA = re.compile(r"^(.+?)\s+(?:vencer a partida|vencedor da partida|ganhar)$", re.IGNORECASE)
_PADRAO_SET_ESPECIFICO = re.compile(r"^(.+?)\s+vencer o (\d)º set$", re.IGNORECASE)
_PADRAO_SET_GENERICO = re.compile(r"^(.+?)\s+vencer um set$", re.IGNORECASE)
_PADRAO_SEM_PERDER_SET = re.compile(r"^(.+?)\s+vencer sem perder set(?:s)?$", re.IGNORECASE)


def _nome_bate(citado: str, jogador1: Optional[str], jogador2: Optional[str]) -> Optional[str]:
    """Devolve "jogador1"/"jogador2" conforme qual nome bate com o citado no
    mercado, ou None se nenhum bater (nome não reconhecido)."""
    if jogador1 and names_match(citado, jogador1, threshold=_THRESHOLD_NOME):
        return "jogador1"
    if jogador2 and names_match(citado, jogador2, threshold=_THRESHOLD_NOME):
        return "jogador2"
    return None


def _parse_placar_final(placar_final: Optional[str]) -> list[tuple[int, int]]:
    """Reconstrói a lista de sets a partir da string já salva no banco (ex:
    "6-4, 3-6, 6-2", formato de Bet.placar_final) — usado só por
    checar_resultado_de_bet(), quando não temos mais o EventStatus original
    (sofascore_client.EventStatus.sets) à mão, só o que já foi persistido."""
    if not placar_final:
        return []
    sets = []
    for parte in placar_final.split(","):
        m = re.match(r"\s*(\d+)\s*-\s*(\d+)\s*", parte)
        if m:
            sets.append((int(m.group(1)), int(m.group(2))))
    return sets


def checar_resultado_de_bet(bet) -> Optional[str]:
    """
    Mesma interpretação de checar_resultado(), mas a partir de um Bet já
    persistido (placar_final/vencedor_partida como string, sem o
    EventStatus original) — usado por app.py, que não tem acesso ao
    EventStatus vindo do score_updater.py, só o que já foi salvo no banco.
    """
    if not bet.placar_final or not bet.vencedor_partida or bet.status != "encerrada":
        return None
    sets = _parse_placar_final(bet.placar_final)
    vencedor_lado = _nome_bate(bet.vencedor_partida, bet.jogador1, bet.jogador2)
    if vencedor_lado is None:
        return None
    evt = EventStatus(
        status="finished",
        placar=bet.placar_final,
        vencedor="home" if vencedor_lado == "jogador1" else "away",
        sets=sets,
    )
    return checar_resultado(bet.mercado, bet.jogador1, bet.jogador2, evt)


def checar_resultado(
    mercado: Optional[str],
    jogador1: Optional[str],
    jogador2: Optional[str],
    evt: EventStatus,
) -> Optional[str]:
    """
    Devolve ResultadoAposta.GREEN.value / .RED.value, ou None se o mercado
    não é reconhecido ou falta dado (partida ainda não com sets suficientes
    — não deveria acontecer se evt.status == "finished", mas por segurança).
    """
    if not mercado or evt.status != "finished":
        return None
    mercado = mercado.strip()

    m = _PADRAO_PARTIDA.match(mercado)
    if m:
        lado = _nome_bate(m.group(1).strip(), jogador1, jogador2)
        if lado is None or evt.vencedor is None:
            return None
        venceu = (lado == "jogador1" and evt.vencedor == "home") or (lado == "jogador2" and evt.vencedor == "away")
        return ResultadoAposta.GREEN.value if venceu else ResultadoAposta.RED.value

    m = _PADRAO_SET_ESPECIFICO.match(mercado)
    if m:
        lado = _nome_bate(m.group(1).strip(), jogador1, jogador2)
        indice_set = int(m.group(2)) - 1
        if lado is None or indice_set < 0 or indice_set >= len(evt.sets):
            return None
        games_j1, games_j2 = evt.sets[indice_set]
        if games_j1 == games_j2:  # nunca deveria empatar um set de tênis, mas por segurança
            return None
        venceu = (lado == "jogador1" and games_j1 > games_j2) or (lado == "jogador2" and games_j2 > games_j1)
        return ResultadoAposta.GREEN.value if venceu else ResultadoAposta.RED.value

    m = _PADRAO_SET_GENERICO.match(mercado)
    if m:
        lado = _nome_bate(m.group(1).strip(), jogador1, jogador2)
        if lado is None or not evt.sets:
            return None
        ganhou_algum_set = any(
            (games_j1 > games_j2) if lado == "jogador1" else (games_j2 > games_j1)
            for games_j1, games_j2 in evt.sets
        )
        return ResultadoAposta.GREEN.value if ganhou_algum_set else ResultadoAposta.RED.value

    m = _PADRAO_SEM_PERDER_SET.match(mercado)
    if m:
        lado = _nome_bate(m.group(1).strip(), jogador1, jogador2)
        if lado is None or evt.vencedor is None or not evt.sets:
            return None
        venceu_partida = (lado == "jogador1" and evt.vencedor == "home") or (lado == "jogador2" and evt.vencedor == "away")
        if not venceu_partida:
            return ResultadoAposta.RED.value
        perdeu_algum_set = any(
            (games_j1 < games_j2) if lado == "jogador1" else (games_j2 < games_j1)
            for games_j1, games_j2 in evt.sets
        )
        return ResultadoAposta.RED.value if perdeu_algum_set else ResultadoAposta.GREEN.value

    return None
