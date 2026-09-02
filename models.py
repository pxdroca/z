"""
models.py
=========
Estruturas de dados compartilhadas entre os módulos do pipeline
(listener -> extractor -> matcher -> database -> notifier -> app).

Manter os módulos comunicando-se via estes dataclasses (em vez de dicts soltos)
evita erros de "chave errada" e deixa o código autodocumentado.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

# Reexportado aqui por conveniência (bet.links usa este tipo); a definição
# canônica vive em bookmakers/base.py para evitar import circular
# (bookmakers/* não deveria depender de models.py).


class TipoAposta(str, Enum):
    """Distingue uma tip de 1 confronto (o caso original do projeto) de uma
    aposta múltipla/combinada (vários jogos numa odd só) — ver
    extractor.py e README para o critério de detecção."""

    SIMPLES = "simples"
    MULTIPLA = "multipla"


class BetStatus(str, Enum):
    """Ciclo de vida de uma aposta dentro do dashboard — sobre o JOGO
    (agendado/ao vivo/encerrado), não sobre se a aposta em si ganhou."""

    NAO_ENCONTRADA = "nao_encontrada"   # extraído do print, mas não achamos o confronto na Superbet
    AGENDADA = "agendada"               # confronto encontrado, ainda não começou
    AO_VIVO = "ao_vivo"                 # já passou do horário de início
    ENCERRADA = "encerrada"             # marcada manualmente como finalizada (ganhou/perdeu/void)
    ERRO_EXTRACAO = "erro_extracao"     # OCR/parsing não conseguiu extrair dados suficientes


class ResultadoAposta(str, Enum):
    """Se A APOSTA (não o jogo) ganhou — definido manualmente pelo usuário
    no painel, já que depende do mercado específico (ex: vencedor da
    partida vs. vencedor do 1º set), algo que o pipeline não decide sozinho."""

    PENDENTE = "pendente"
    GREEN = "green"
    RED = "red"
    VOID = "void"


@dataclass
class ExtractedBet:
    """Saída crua do extractor.py — o que conseguimos ler do print/texto."""

    jogador1: Optional[str] = None
    jogador2: Optional[str] = None
    torneio: Optional[str] = None
    mercado: Optional[str] = None
    odd: Optional[float] = None
    confianca: float = 0.0          # 0-1, quão confiante o extractor está do resultado
    texto_bruto: str = ""           # texto OCR + legenda, para debug/auditoria
    tipo_aposta: str = TipoAposta.SIMPLES.value  # ver TipoAposta — só o motor Gemini detecta multipla hoje
    selecoes: list[str] = field(default_factory=list)  # só para multipla, ver Bet.selecoes

    @property
    def valido(self) -> bool:
        """
        Válido o suficiente para seguir adiante:
          - multipla: exige só a odd (ver extract_with_gemini) — segue
            direto pro notifier.py, NÃO passa pelo matcher.py (não há um
            confronto único pra confirmar). `selecoes` pode vir vazia (ex:
            "+N seleções mais" escondendo os nomes) — nesse caso vira uma
            notificação genérica no listener.py, mas ainda é um resultado
            válido (não um erro de extração): sabemos que É uma múltipla,
            só não sabemos os detalhes de cada perna.
          - simples: os 2 jogadores, OU só o favorito + a odd (o adversário
            nesse caso é resolvido depois via SofaScore — ver
            matcher.find_match). Exigir a odd nesse segundo caso evita
            seguir com ruído de OCR que "parece" um nome mas não é uma tip
            de verdade.
        """
        if self.tipo_aposta == TipoAposta.MULTIPLA.value:
            return self.odd is not None
        if self.jogador1 and self.jogador2:
            return True
        return bool(self.jogador1) and self.odd is not None


@dataclass
class MatchInfo:
    """
    Saída do matcher.py — dados oficiais do confronto (via SofaScore) e um
    link por casa de apostas habilitada (Superbet/Betano/bet365/...).

    `links` mapeia slug da casa -> {"nome": ..., "url": ..., "exato": bool}.
    "exato" indica se achamos a URL da partida específica (True) ou se
    caímos para um link aproximado de torneio/dia (False).
    """

    encontrado: bool = False
    data_hora: Optional[datetime] = None
    torneio_oficial: Optional[str] = None
    jogador1_oficial: Optional[str] = None
    jogador2_oficial: Optional[str] = None
    links: dict = field(default_factory=dict)
    sofascore_event_id: Optional[int] = None


@dataclass
class Bet:
    """Registro completo, como é persistido no SQLite e consumido pelo app.py."""

    id: Optional[int] = None
    jogador1: str = ""
    jogador2: str = ""
    torneio: Optional[str] = None
    mercado: Optional[str] = None
    odd: Optional[float] = None
    data_hora: Optional[datetime] = None
    links: dict = field(default_factory=dict)  # slug -> {"nome", "url", "exato"} — ver MatchInfo.links
    status: str = BetStatus.NAO_ENCONTRADA.value
    fonte_texto: str = ""
    mensagem_id: Optional[int] = None
    criado_em: Optional[datetime] = None
    sofascore_event_id: Optional[int] = None  # usado por score_updater.py para acompanhar o placar
    placar_final: Optional[str] = None        # ex: "6-4, 6-3", preenchido quando a partida termina
    vencedor_partida: Optional[str] = None    # nome do jogador vencedor, preenchido junto com placar_final
    unidades: float = 1.0                     # stake em unidades — fixo em 1.0 (tipster não indica stake)
    resultado: str = ResultadoAposta.PENDENTE.value  # se A APOSTA ganhou — definido manualmente no painel
    tipo_aposta: str = TipoAposta.SIMPLES.value      # "simples" (1 confronto) ou "multipla" (ver TipoAposta)
    selecoes: list[str] = field(default_factory=list)  # só para multipla: 1 item por seleção, ex: "Alcaraz"

    @property
    def jogo(self) -> str:
        if self.tipo_aposta == TipoAposta.MULTIPLA.value:
            if self.selecoes:
                return ", ".join(self.selecoes)
            return self.jogador1 or "Múltipla (detalhes no print original)"
        return f"{self.jogador1} vs {self.jogador2}"
