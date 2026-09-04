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

Basquete (ver _checar_resultado_basquete) cobre separadamente:
  - Moneyline/vencedor da partida (mesmo padrão/lógica do tênis).
  - Handicap de pontos por time (ex: "Lakers -5.5").
  - Total de pontos, over/under (ex: "Mais de 215.5 pontos").
  Mercados de jogador individual (pontos/rebotes/assistências) NÃO são
  cobertos — o SofaScore/365scores client usados aqui não trazem estatística
  de jogador, só placar por período. Ficam manuais, igual aos mercados de
  tênis não cobertos.

Devolve None quando o mercado não é reconhecido (mercados fora da lista
acima) ou quando falta dado pra decidir (ex: pediu o 3º set mas a partida
não chegou lá) — nesses casos o resultado continua "pendente", conferência
manual.
"""

from __future__ import annotations

import re
from typing import Optional

from models import Esporte, ResultadoAposta
from nameutils import names_match
from sofascore_client import EventStatus

_THRESHOLD_NOME = 80

_PADRAO_PARTIDA = re.compile(r"^(.+?)\s+(?:vencer a partida|vencedor da partida|ganhar)$", re.IGNORECASE)
_PADRAO_SET_ESPECIFICO = re.compile(r"^(.+?)\s+vencer o (\d)º set$", re.IGNORECASE)
_PADRAO_SET_GENERICO = re.compile(r"^(.+?)\s+vencer um set$", re.IGNORECASE)
_PADRAO_SEM_PERDER_SET = re.compile(r"^(.+?)\s+vencer sem perder set(?:s)?$", re.IGNORECASE)

# Basquete, nome ANTES da linha: "Lakers -5.5" / "Handicap Celtics +3.5"
# / "Handicap: Lakers -5.5"
_PADRAO_HANDICAP = re.compile(
    r"^(?:handicap\s*(?:de pontos)?\s*[:\-]?\s*)?(.+?)\s+([+-]\s?\d+(?:[.,]\d+)?)$", re.IGNORECASE,
)

# Basquete, linha ANTES do nome: "Handicap -20.5 França".
#
# A Superbet escreve nessa ordem, e o padrão acima (que espera o nome
# primeiro) não casava — a aposta #121 (04/09/2026) ficou encerrada com
# resultado pendente mesmo com o placar por quarto disponível no
# SofaScore.
#
# O sinal é opcional porque o OCR às vezes o perde; sem ele assume-se
# negativo (favorito dando pontos), que é como o mercado é escrito na
# prática — "Handicap 20.5 França" quer dizer "França -20.5".
_PADRAO_HANDICAP_INVERTIDO = re.compile(
    r"^handicap\s*(?:de pontos)?\s*:?\s*([+-]?\s?\d+(?:[.,]\d+)?)\s+(.+)$", re.IGNORECASE,
)
# Basquete: "Mais de 215.5 pontos" / "Menos de 210.5" / "Over 215.5" / "Under 210.5 pontos"
_PADRAO_TOTAL_PONTOS = re.compile(
    r"^(mais|menos|over|under)\s+de\s+(\d+(?:[.,]\d+)?)\s*(?:pontos?)?$", re.IGNORECASE,
)


# Palavras que o tipster cola no nome e não fazem parte dele: "hijikata
# NA DUPLAS vencer a partida", "IRMÃOS Cerúndolo". Tirar isso antes de
# comparar é o que faz o nome citado casar com o do lado da partida.
_RUIDO_NO_NOME = re.compile(
    r"\b(?:na|nas|no|nos|de|da|do)?\s*(?:duplas?|doubles|irm[ãa]os?|irm[ãa]s?)\b",
    re.IGNORECASE,
)


def _lados_do_nome(nome: Optional[str]) -> list[str]:
    """Quebra "Borges N./Hijikata R." nos dois parceiros.

    A dupla é gravada com "/" (formato das fontes), e comparar a string
    inteira com um nome só nunca bate: o tipster cita UM dos dois
    ("hijikata na duplas vencer a partida"). Devolve também o nome
    completo, pra continuar casando quando ele cita a dupla inteira.
    """
    if not nome:
        return []
    partes = [p.strip() for p in nome.split("/") if p.strip()]
    return [nome, *partes] if len(partes) > 1 else [nome]


def _nome_bate(citado: str, jogador1: Optional[str], jogador2: Optional[str]) -> Optional[str]:
    """Devolve "jogador1"/"jogador2" conforme qual nome bate com o citado no
    mercado, ou None se nenhum bater (nome não reconhecido).

    Em duplas compara com cada parceiro separadamente: o tipster cita só um
    deles. Bug real (04/09/2026): as apostas #103 ("hijikata na duplas
    vencer a partida" x "Borges N./Hijikata R.") e #104 ("Irmãos Cerúndolo"
    x "Cerundolo F./Cerundolo J.") ficaram encerradas com resultado
    pendente — o placar e o vencedor estavam no card, mas o nome citado
    nunca casava com a string inteira da dupla.
    """
    limpo = _RUIDO_NO_NOME.sub(" ", citado).strip()
    candidatos = [c for c in (citado, limpo) if c]

    for lado, nome in (("jogador1", jogador1), ("jogador2", jogador2)):
        for parte in _lados_do_nome(nome):
            if any(names_match(c, parte, threshold=_THRESHOLD_NOME) for c in candidatos):
                return lado
    return None


def _linha_do_handicap(linha_texto: str) -> Optional[float]:
    """Converte a linha do handicap em número, resolvendo o "-" solto.

    "Handicap - 20.5 França" é ambíguo: o "-" pode ser o sinal do handicap
    ou só um separador entre a palavra "handicap" e o número (é por isso
    que o extractor agora escreve "-20.5", colado — ver
    extractor._normalizar_handicap). Aqui, na dúvida, assume-se NEGATIVO:
    o mercado escrito assim é sempre o favorito dando pontos, e é como a
    Superbet o apresenta.
    """
    texto = linha_texto.replace(" ", "").replace(",", ".")
    try:
        valor = float(texto)
    except ValueError:
        return None
    # Sem sinal explícito no número, o handicap é do favorito dando
    # pontos: negativo.
    if not texto.startswith(("+", "-")):
        valor = -valor
    return valor


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
    esporte = getattr(bet, "esporte", None) or Esporte.TENIS.value
    return checar_resultado(bet.mercado, bet.jogador1, bet.jogador2, evt, esporte=esporte)


def checar_resultado(
    mercado: Optional[str],
    jogador1: Optional[str],
    jogador2: Optional[str],
    evt: EventStatus,
    esporte: str = Esporte.TENIS.value,
) -> Optional[str]:
    """
    Devolve ResultadoAposta.GREEN.value / .RED.value / .VOID.value, ou None
    se o mercado não é reconhecido ou falta dado pra decidir. Despacha para
    a lógica do esporte correspondente (ver _checar_resultado_tenis /
    _checar_resultado_basquete).
    """
    if not mercado or evt.status != "finished":
        return None
    mercado = mercado.strip()

    if esporte == Esporte.BASQUETE.value:
        return _checar_resultado_basquete(mercado, jogador1, jogador2, evt)
    return _checar_resultado_tenis(mercado, jogador1, jogador2, evt)


def _checar_moneyline(mercado: str, jogador1: Optional[str], jogador2: Optional[str], evt: EventStatus) -> Optional[str]:
    """Vencedor da partida — mesmo texto de mercado e mesma lógica para
    tênis e basquete (compara com evt.vencedor, agnóstico de esporte)."""
    m = _PADRAO_PARTIDA.match(mercado)
    if not m:
        return None
    lado = _nome_bate(m.group(1).strip(), jogador1, jogador2)
    if lado is None or evt.vencedor is None:
        return None
    venceu = (lado == "jogador1" and evt.vencedor == "home") or (lado == "jogador2" and evt.vencedor == "away")
    return ResultadoAposta.GREEN.value if venceu else ResultadoAposta.RED.value


def _checar_resultado_tenis(mercado: str, jogador1: Optional[str], jogador2: Optional[str], evt: EventStatus) -> Optional[str]:
    m = _PADRAO_PARTIDA.match(mercado)
    if m:
        return _checar_moneyline(mercado, jogador1, jogador2, evt)

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


def _checar_resultado_basquete(mercado: str, jogador1: Optional[str], jogador2: Optional[str], evt: EventStatus) -> Optional[str]:
    """Cobre moneyline, handicap de pontos por time e total de pontos
    (over/under). Mercados de jogador individual (pontos/rebotes/
    assistências) não são cobertos — evt.sets só tem placar por período, sem
    estatística de jogador — caem em None (pendente, conferência manual)."""
    if _PADRAO_PARTIDA.match(mercado):
        return _checar_moneyline(mercado, jogador1, jogador2, evt)

    if not evt.sets:
        return None

    # Handicap nas duas ordens: "Lakers -5.5" e "Handicap -20.5 França".
    nome_citado: Optional[str] = None
    linha_texto: Optional[str] = None

    m = _PADRAO_HANDICAP.match(mercado)
    if m:
        nome_citado, linha_texto = m.group(1).strip(), m.group(2)
    else:
        m = _PADRAO_HANDICAP_INVERTIDO.match(mercado)
        if m:
            linha_texto, nome_citado = m.group(1), m.group(2).strip()

    if nome_citado and linha_texto:
        lado = _nome_bate(nome_citado, jogador1, jogador2)
        if lado is None:
            return None
        linha = _linha_do_handicap(linha_texto)
        if linha is None:
            return None
        pontos_j1 = sum(h for h, _ in evt.sets)
        pontos_j2 = sum(a for _, a in evt.sets)
        pontos_lado, pontos_adversario = (pontos_j1, pontos_j2) if lado == "jogador1" else (pontos_j2, pontos_j1)
        margem_ajustada = (pontos_lado - pontos_adversario) + linha
        if margem_ajustada == 0:
            return ResultadoAposta.VOID.value  # push: só acontece com handicap inteiro
        return ResultadoAposta.GREEN.value if margem_ajustada > 0 else ResultadoAposta.RED.value

    m = _PADRAO_TOTAL_PONTOS.match(mercado)
    if m:
        direcao = m.group(1).strip().lower()
        try:
            linha = float(m.group(2).replace(",", "."))
        except ValueError:
            return None
        total = sum(h + a for h, a in evt.sets)
        if total == linha:
            return ResultadoAposta.VOID.value  # push: só acontece com linha inteira
        acima = total > linha
        quer_mais = direcao in ("mais", "over")
        venceu = acima if quer_mais else not acima
        return ResultadoAposta.GREEN.value if venceu else ResultadoAposta.RED.value

    return None
