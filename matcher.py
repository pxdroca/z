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
from nameutils import names_match, search_variants
from sofascore_client import find_canonical_match, find_canonical_match_by_name

logger = logging.getLogger(__name__)


def buscar_confronto_na_superbet(nome: str) -> Optional[tuple[str, str]]:
    """Procura o confronto de `nome` na busca da Superbet, tentando o nome
    inteiro e depois só o sobrenome/primeiro nome (ver
    nameutils.search_variants). Devolve (jogador1, jogador2) do confronto
    único encontrado, ou None.

    Serve de segunda opinião para o SofaScore em find_match. A Superbet é
    boa nesse papel por um motivo específico: ela só lista jogos abertos
    pra aposta, isto é, futuros. Isso descarta de graça as duas formas de
    erro que o SofaScore comete sozinho (03/09/2026):

      - "Alexandra muller" (o tipster quis dizer Alexandre Muller): o
        SofaScore casou com uma dupla feminina de JUNHO DE 2021.
      - "Hara friend": o jogador tem dois perfis no SofaScore e o
        featured-event do perfil desatualizado apontava pra um jogo de
        julho/2026.

    Se houver mais de um confronto plausível, devolve None em vez de
    escolher: ambiguidade aqui é justamente o sinal de que não dá pra
    confiar, e um palpite errado é pior que um "não encontrado".
    """
    adapter_cls = REGISTRY.get("superbet")
    if adapter_cls is None:
        return None
    adapter = adapter_cls()
    threshold = settings.SUPERBET_FUZZY_THRESHOLD

    for variante in search_variants(nome):
        try:
            confrontos = adapter.buscar_confrontos(variante)
        except Exception:
            logger.exception("Superbet: busca por %r falhou — seguindo sem validação cruzada.", variante)
            return None
        if not confrontos:
            continue

        # Filtra pelo nome COMPLETO que o tipster mandou, não pela variante
        # curta que foi usada na busca. A variante serve pra achar
        # candidatos no site (é ela que tolera o typo); a escolha entre
        # eles tem que usar toda a informação disponível.
        #
        # Bug pego no teste (03/09/2026): filtrando por "hara", a dupla
        # "A.Brooks/M.Komano x A.Hrastar/K.Sharabura" casava junto com o
        # jogo certo ("Billy Harris x Jay Dylan Hara Friend") — 2
        # plausíveis, ambíguo, e o confronto correto era descartado.
        # Filtrando por "Hara friend", só o jogo certo sobra.
        # Exige também que o SOBRENOME apareça no lado que casou. Sem isso,
        # "Alexandra muller" casava com "Alexandra Eala" (primeiro nome
        # idêntico é suficiente pro fuzzy) e trazia a jogadora errada. O
        # sobrenome é o que de fato identifica o tenista.
        sobrenome = (search_variants(nome) or [nome])[-1]

        def _lado_confere(lado: str) -> bool:
            if not names_match(nome, lado, threshold):
                return False
            return names_match(sobrenome, lado, threshold)

        plausiveis = [
            (j1, j2) for j1, j2, _ in confrontos
            if _lado_confere(j1) or _lado_confere(j2)
        ]
        if len(plausiveis) == 1:
            j1, j2 = plausiveis[0]
            logger.info(
                "Superbet confirmou (busca por %r): %s x %s", variante, j1, j2,
            )
            return j1, j2
        if len(plausiveis) > 1:
            logger.info(
                "Superbet: %d confrontos plausíveis para %r (%s) — ambíguo, não vou escolher.",
                len(plausiveis), variante,
                "; ".join(f"{a} x {b}" for a, b in plausiveis[:4]),
            )
            return None

    return None


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
        # Só o favorito citado: aqui o SofaScore precisa adivinhar o
        # adversário, e é exatamente onde ele erra (nome com typo, jogador
        # com perfil duplicado/desatualizado). Pede a segunda opinião da
        # Superbet ANTES, porque ela lista só jogos futuros e por isso
        # resolve typo e perfil velho de uma vez; com o par em mãos, a
        # busca no SofaScore deixa de ser adivinhação e passa a ser
        # confirmação de um confronto específico.
        par_superbet = buscar_confronto_na_superbet(jogador1)
        canonico = None
        if par_superbet:
            s1, s2 = par_superbet
            canonico = find_canonical_match(s1, s2, dias_de_busca=dias_de_busca, referencia=referencia)
            if canonico is None:
                logger.info(
                    "Superbet achou %s x %s, mas o SofaScore não confirmou — "
                    "seguindo para a busca só por nome.", s1, s2,
                )
        if canonico is None:
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
