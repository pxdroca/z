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
from models import Esporte, MatchInfo
from nameutils import names_match, search_variants
from sofascore_client import find_canonical_match, find_canonical_match_by_name

logger = logging.getLogger(__name__)


def buscar_confronto_na_superbet(
    nome: str,
    esporte: str = Esporte.TENIS.value,
    odd_tip: Optional[float] = None,
) -> Optional[tuple[str, str, bool]]:
    """Procura o confronto de `nome` na busca da Superbet, tentando o nome
    inteiro e depois só o sobrenome (ver nameutils.search_variants).
    Devolve (jogador1, jogador2, é_hoje) do confronto escolhido, ou None.

    O terceiro item diz se o card da Superbet marcava o jogo como sendo de
    HOJE. Importa porque a Superbet lista só jogos abertos pra aposta: se o
    jogo de hoje do jogador já terminou, ela oferece o de amanhã, e aceitar
    isso grava a aposta no confronto errado. Bug real (03/09/2026): a tip
    "de la torre odd 2.00" era Daniel Rincon x Montes-de la Torre (11:55,
    já encerrado), mas foi gravada como o jogo de 04/09 contra Jack
    Pinnington Jones — o painel mostrava "agendada" para uma aposta que já
    tinha resultado.

    `odd_tip` (a odd que o tipster mandou) desempata quando o jogador tem
    simples e duplas no mesmo dia: as odds divergem bastante entre os dois
    (ideia do usuário, confirmada ao vivo — Bucsa simples 1.51/2.55 vs
    duplas 2.82/1.43, tip de 1.58). É mais robusto que só preferir simples,
    porque funciona também se algum dia vier uma tip de duplas.

    Serve de segunda opinião para o SofaScore em find_match, que sozinho
    erra assim (também 03/09/2026):
      - "Alexandra muller" (o tipster quis dizer Alexandre Muller): casou
        com uma dupla feminina de JUNHO DE 2021.
      - "Hara friend": o jogador tem dois perfis e o featured-event do
        desatualizado apontava pra um jogo de julho/2026.
    """
    adapter_cls = REGISTRY.get("superbet")
    if adapter_cls is None:
        return None
    adapter = adapter_cls()
    threshold = settings.SUPERBET_FUZZY_THRESHOLD

    for variante in search_variants(nome):
        try:
            confrontos = adapter.buscar_confrontos(variante, esporte)
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

        plausiveis = [c for c in confrontos if _lado_confere(c.jogador1) or _lado_confere(c.jogador2)]
        if not plausiveis:
            continue

        escolhido = _escolher_confronto(plausiveis, variante, odd_tip)
        if escolhido is None:
            return None

        eh_hoje = _card_eh_hoje(escolhido.horario_texto)
        logger.info(
            "Superbet confirmou (busca por %r): %s x %s | card=%r odds=%s",
            variante, escolhido.jogador1, escolhido.jogador2,
            escolhido.horario_texto, escolhido.odds or "-",
        )
        return escolhido.jogador1, escolhido.jogador2, eh_hoje

    return None


def _card_eh_hoje(horario_texto: str) -> bool:
    """O card da Superbet marca este jogo como sendo de hoje?

    A Superbet rotula o card com "Hoje, HH:MM" / "Amanhã, HH:MM" (visto ao
    vivo em 03/09/2026). Um card ao vivo pode vir só com o placar/minuto,
    sem rótulo de dia — nesse caso também é hoje.
    """
    t = (horario_texto or "").strip().lower()
    if not t:
        return False
    if "amanh" in t:
        return False
    return True


def _escolher_confronto(plausiveis: list, variante: str, odd_tip: Optional[float]):
    """Escolhe um confronto entre os candidatos da busca da Superbet.

    Um tenista costuma ter simples E duplas no mesmo dia, então vários
    candidatos quase nunca é ambiguidade real — é o jogo certo mais a dupla
    dele. Dois desempates, na ordem:

      1. A odd da tip. As odds divergem bastante entre simples e duplas
         (ideia do usuário, confirmada ao vivo: Bucsa simples 1.51/2.55 vs
         duplas 2.82/1.43, tip de 1.58). Escolhe o card cuja odd mais
         próxima da tip é a mais próxima de todas. Funciona nos dois
         sentidos — inclusive se algum dia vier uma tip de duplas, que hoje
         nunca veio.
      2. Sem odd (ou sem odds no card): prefere o jogo de simples, que é o
         que o tipster aposta. A Superbet escreve duplas como
         "A.Sobrenome/B.Sobrenome", então a barra é o sinal.

    Devolve None quando nem isso resolve — chutar gravaria a aposta no
    confronto errado, que é pior que "não encontrada".
    """
    if len(plausiveis) == 1:
        return plausiveis[0]

    if odd_tip is not None:
        com_odds = [c for c in plausiveis if c.odds]
        if com_odds:
            def _distancia(c) -> float:
                return min(abs(o - odd_tip) for o in c.odds)

            ranked = sorted(com_odds, key=_distancia)
            melhor, dist = ranked[0], _distancia(ranked[0])
            # Só aceita se a odd realmente bate de perto. 0.35 absorve a
            # variação normal entre casas (a tip vem de outra casa que não
            # a Superbet) sem deixar passar um jogo cuja odd é claramente
            # de outro confronto.
            if dist <= 0.35 and (len(ranked) == 1 or _distancia(ranked[1]) > dist):
                logger.info(
                    "Superbet: %d confrontos para %r — escolhi por odd (tip=%.2f, card=%s, dif=%.2f).",
                    len(plausiveis), variante, odd_tip, melhor.odds, dist,
                )
                return melhor

    simples = [c for c in plausiveis if "/" not in c.jogador1 and "/" not in c.jogador2]
    if len(simples) == 1:
        logger.info(
            "Superbet: %d confrontos para %r, 1 de simples — usando o de simples.",
            len(plausiveis), variante,
        )
        return simples[0]

    if len(simples) > 1:
        logger.info(
            "Superbet: %d jogos de simples plausíveis para %r (%s) — ambíguo, não vou escolher.",
            len(simples), variante,
            "; ".join(f"{c.jogador1} x {c.jogador2}" for c in simples[:4]),
        )
        return None

    logger.info(
        "Superbet: só achei jogos de duplas para %r (%s) — ignorando.",
        variante, "; ".join(f"{c.jogador1} x {c.jogador2}" for c in plausiveis[:4]),
    )
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
    esporte: str = Esporte.TENIS.value,
    dias_de_busca: int = 3,
    referencia: Optional[datetime] = None,
    odd_tip: Optional[float] = None,
) -> MatchInfo:
    """
    Ponto de entrada principal, chamado pelo listener.py depois do
    extractor. `jogador2` pode ser None — caso do tipster ter citado só o
    favorito (ver extractor.find_favorite_only); nesse caso o adversário é
    resolvido consultando o SofaScore só pelo jogador1.

    `odd_tip` é a odd que o tipster mandou; quando informada, desempata
    entre o jogo de simples e o de duplas do mesmo jogador na Superbet (ver
    buscar_confronto_na_superbet).
    """
    if not jogador1:
        return MatchInfo(encontrado=False, esporte=esporte)

    if jogador2:
        canonico = find_canonical_match(jogador1, jogador2, esporte=esporte, dias_de_busca=dias_de_busca, referencia=referencia)
    else:
        # Só o favorito citado: aqui o SofaScore precisa adivinhar o
        # adversário, e é exatamente onde ele erra (nome com typo, jogador
        # com perfil duplicado/desatualizado). Pede a segunda opinião da
        # Superbet ANTES, porque ela lista só jogos futuros e por isso
        # resolve typo e perfil velho de uma vez; com o par em mãos, a
        # busca no SofaScore deixa de ser adivinhação e passa a ser
        # confirmação de um confronto específico.
        par_superbet = buscar_confronto_na_superbet(jogador1, esporte, odd_tip)
        canonico = None
        if par_superbet:
            s1, s2, eh_hoje = par_superbet
            if not eh_hoje:
                # A Superbet só lista jogo aberto pra aposta: se o card não
                # é de hoje, o jogo de hoje deste jogador já acabou e o que
                # sobrou na busca é o de amanhã. Confirmar por ele grava a
                # aposta no confronto errado — bug real com "de la torre"
                # (ver docstring de buscar_confronto_na_superbet). Deixa o
                # SofaScore resolver, que enxerga jogo já encerrado.
                logger.info(
                    "Superbet achou %s x %s, mas o card não é de hoje — "
                    "provavelmente o jogo de hoje já encerrou; usando o SofaScore.",
                    s1, s2,
                )
            else:
                canonico = find_canonical_match(s1, s2, esporte=esporte, dias_de_busca=dias_de_busca, referencia=referencia)
                if canonico is None:
                    logger.info(
                        "Superbet achou %s x %s, mas o SofaScore não confirmou — "
                        "seguindo para a busca só por nome.", s1, s2,
                    )
        if canonico is None:
            canonico = find_canonical_match_by_name(jogador1, esporte=esporte, referencia=referencia)

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
            link = adapter.get_link(j1, j2, torneio, data_hora, esporte)
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
        esporte=esporte,
    )
