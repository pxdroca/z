"""
matcher.py
==========
Orquestrador da etapa de "descoberta do confronto". Dado (jogador1,
jogador2) extraídos do print pelo extractor.py, este módulo:

  1. Consulta o SofaScore (sofascore_client.py) pra confirmar o jogo oficial
     — nomes corretos, torneio, data/hora exata. Isso é gratuito, estável e
     não depende de navegador (JSON puro).
  2. Para cada casa de apostas habilitada em BOOKMAKERS (.env) — por padrão
     Superbet, Betano e bet365 — pede um link (bookmakers/<casa>.py). Cada
     adaptador tenta achar o link EXATO daquela partida com um navegador
     stealth e, se não conseguir, cai automaticamente para um link
     aproximado (torneio/dia). Isso nunca falha "alto" — na pior das
     hipóteses todas as casas caem no fallback.

Ver README, seção "Calibrando os adaptadores de casas", para ajustar
seletores e domínios/paths específicos de cada casa.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from bookmakers import REGISTRY
from config import settings
from models import MatchInfo
from sofascore_client import find_canonical_match, find_canonical_match_by_name

logger = logging.getLogger(__name__)


def build_enabled_adapters():
    adapters = []
    for slug in settings.BOOKMAKERS:
        adapter_cls = REGISTRY.get(slug)
        if adapter_cls is None:
            logger.warning("Casa de apostas desconhecida em BOOKMAKERS: %r (ignorando)", slug)
            continue
        adapters.append(adapter_cls())
    return adapters


def find_match(
    jogador1: str,
    jogador2: Optional[str],
    dias_de_busca: int = 3,
    referencia: Optional[datetime] = None,
) -> MatchInfo:
    """
    Ponto de entrada principal, chamado pelo listener.py depois do
    extractor. `jogador2` pode ser None — caso do tipster ter citado só o
    favorito (ver extractor.find_favorite_only); nesse caso o adversário é
    resolvido consultando o SofaScore só pelo jogador1.
    """
    if not jogador1:
        return MatchInfo(encontrado=False)

    if jogador2:
        canonico = find_canonical_match(jogador1, jogador2, dias_de_busca=dias_de_busca, referencia=referencia)
    else:
        canonico = find_canonical_match_by_name(jogador1, referencia=referencia)

    # Nomes/torneio/hora "oficiais": usa o que o SofaScore confirmou, ou
    # cai para o que o OCR extraiu, se o SofaScore não achar nada.
    j1 = canonico.jogador1_oficial if canonico else jogador1
    j2 = canonico.jogador2_oficial if canonico else jogador2
    torneio = canonico.torneio_oficial if canonico else None
    data_hora = canonico.data_hora if canonico else None

    # Se o SofaScore não achou o adversário (canonico None quando jogador2
    # era None de entrada — ver acima), os adaptadores sempre caem no link
    # aproximado aqui: pair_matches() nunca bate com j2=None, e é isso
    # mesmo que queremos — sem confirmar o confronto, não faz sentido
    # arriscar um link "exato" errado.
    links: dict = {}
    for adapter in build_enabled_adapters():
        try:
            link = adapter.get_link(j1, j2, torneio, data_hora)
        except Exception:
            logger.exception("Falha inesperada no adaptador %s — pulando essa casa.", adapter.slug)
            continue
        links[adapter.slug] = {"nome": link.nome, "url": link.url, "exato": link.exato}
        logger.info(
            "%s: link %s (%s)", adapter.slug, link.url, "exato" if link.exato else "aproximado",
        )

    return MatchInfo(
        encontrado=canonico is not None,
        data_hora=data_hora,
        torneio_oficial=torneio,
        jogador1_oficial=j1,
        jogador2_oficial=j2,
        links=links,
        sofascore_event_id=canonico.sofascore_event_id if canonico else None,
    )
