"""
matcher.py
==========
Orquestrador da etapa de "descoberta do confronto". Dado (jogador1,
jogador2) extraídos do print pelo extractor.py, este módulo:

  1. Confirma o jogo oficial (nomes corretos, torneio, data/hora exata)
     consultando, NESTA ORDEM, até alguma responder:
       a) 365scores (scores365_client.py) — 1 request por dia consultado;
       b) SofaScore (sofascore_client.py), varredura do dia — ~80 requests
          por dia consultado, e é a fonte que bloqueia o IP dos runners do
          GitHub Actions com 403 de forma recorrente;
       c) SofaScore, busca por nome — outra rota, costuma passar quando a
          varredura não passa;
       d) Superbet (bookmakers/superbet.py) — a casa onde a aposta existe.
          É a única que enxerga o jogo quando as fontes esportivas falham,
          e traz nomes, dia/hora e o link direto da partida.
     Ver find_match/_confirmar_par/_confronto_pela_superbet.
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
import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from bookmakers import REGISTRY
from config import settings
from database import list_confrontos_conhecidos
from models import Esporte, MatchInfo
from nameutils import names_match, pair_matches, search_variants
from scores365_client import find_canonical_match_365
from sofascore_client import CanonicalMatch, find_canonical_match, find_canonical_match_by_name

logger = logging.getLogger(__name__)

# Fuso em que as casas de apostas brasileiras mostram o horário dos jogos.
# Fixo (não settings.TIMEZONE): é o relógio DO SITE, não o do usuário.
_FUSO_DA_CASA = ZoneInfo("America/Sao_Paulo")

# O tipster avisa quando a aposta é de duplas ("Borges e hijikata na
# duplas", "Irmãos cerundolo na duplas", "hijikata/duplas").
_DUPLAS_PATTERN = re.compile(r"\b(duplas?|doubles)\b", re.IGNORECASE)


def tip_e_de_duplas(texto: Optional[str]) -> bool:
    """A tip diz explicitamente que a aposta é de DUPLAS?

    O matcher descarta confrontos de duplas por padrão, e isso está certo
    na maioria dos casos: um tenista tem simples e duplas no mesmo dia, e
    aceitar a dupla gravava a aposta no confronto errado (bug real com
    "Hara friend"). Mas o tipster passou a mandar tips de duplas de
    verdade — "Borges e hijikata NA DUPLAS odd: 1.87" (04/09/2026) — e aí
    rejeitar a dupla é rejeitar a aposta certa.

    Quando o texto da tip diz "duplas", a preferência se INVERTE: o
    confronto de duplas passa a ser o desejado.
    """
    return bool(texto and _DUPLAS_PATTERN.search(texto))


def buscar_confronto_na_superbet(
    nome: str,
    esporte: str = Esporte.TENIS.value,
    odd_tip: Optional[float] = None,
    prefere_duplas: bool = False,
):
    """Procura o confronto de `nome` na busca da Superbet, tentando o nome
    inteiro e depois só o sobrenome (ver nameutils.search_variants).
    Devolve o card escolhido (bookmakers.superbet._RawMatch: nomes, texto do
    horário, link e odds), ou None.

    Quem chama usa `_card_eh_hoje(card.horario_texto)` para saber se o card
    é de hoje. Importa porque a Superbet lista só jogos abertos pra aposta:
    se o jogo de hoje do jogador já terminou, ela oferece o de amanhã, e
    aceitar isso grava a aposta no confronto errado. Bug real (03/09/2026): a tip
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

        escolhido = _escolher_confronto(plausiveis, variante, odd_tip, prefere_duplas)
        if escolhido is None:
            return None

        logger.info(
            "Superbet confirmou (busca por %r): %s x %s | card=%r odds=%s",
            variante, escolhido.jogador1, escolhido.jogador2,
            escolhido.horario_texto, escolhido.odds or "-",
        )
        return escolhido

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


def _escolher_confronto(
    plausiveis: list, variante: str, odd_tip: Optional[float], prefere_duplas: bool = False
):
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
    # A Superbet escreve duplas como "A.Sobrenome/B.Sobrenome", então a
    # barra é o sinal.
    def _e_duplas(c) -> bool:
        return "/" in c.jogador1 or "/" in c.jogador2

    # Candidato único ainda precisa ser do TIPO certo. Bug real
    # (04/09/2026, aposta #118): a tip "Diana vencer um set odd: 2.20" é de
    # simples, a busca por 'shnaider' devolveu só o jogo de duplas
    # (Mertens/Shnaider no US Open) e o atalho de candidato único aceitava
    # sem olhar — gravaria a aposta no confronto errado, que é pior que
    # "não encontrada".
    if len(plausiveis) == 1:
        unico = plausiveis[0]
        if _e_duplas(unico) != prefere_duplas:
            logger.info(
                "Superbet: único confronto para %r é de %s, mas a tip é de %s (%s x %s) — ignorando.",
                variante,
                "duplas" if _e_duplas(unico) else "simples",
                "duplas" if prefere_duplas else "simples",
                unico.jogador1, unico.jogador2,
            )
            return None
        return unico

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

    # Normalmente queremos o de SIMPLES; quando a tip diz "na duplas", a
    # preferência inverte (ver tip_e_de_duplas e _e_duplas acima).
    desejados = [c for c in plausiveis if _e_duplas(c) == prefere_duplas]
    rotulo = "duplas" if prefere_duplas else "simples"

    if len(desejados) == 1:
        logger.info(
            "Superbet: %d confrontos para %r, 1 de %s — usando o de %s.",
            len(plausiveis), variante, rotulo, rotulo,
        )
        return desejados[0]

    if len(desejados) > 1:
        logger.info(
            "Superbet: %d jogos de %s plausíveis para %r (%s) — ambíguo, não vou escolher.",
            len(desejados), rotulo, variante,
            "; ".join(f"{c.jogador1} x {c.jogador2}" for c in desejados[:4]),
        )
        return None

    logger.info(
        "Superbet: nenhum jogo de %s para %r (%s) — ignorando.",
        rotulo, variante, "; ".join(f"{c.jogador1} x {c.jogador2}" for c in plausiveis[:4]),
    )
    return None


def _data_hora_do_card(horario_texto: str, referencia: Optional[datetime]) -> Optional[datetime]:
    """Converte o rótulo do card da Superbet ("Hoje, 14:30" / "Amanhã, 09:00")
    no horário real do jogo, em UTC — mesmo contrato das outras fontes.

    A Superbet mostra o horário de Brasília, então é nesse fuso que "hoje"
    e "14:30" são interpretados; converter no fim evita o erro clássico de
    gravar como UTC um horário que era local (que foi o que já colocou
    jogos de manhã como "ao vivo de madrugada" neste projeto).

    Devolve None quando o card não traz hora (jogo ao vivo mostra placar no
    lugar do horário) — o confronto ainda vale, só fica sem data_hora."""
    texto = (horario_texto or "").strip().lower()
    achou_hora = re.search(r"(\d{1,2}):(\d{2})", texto)
    if not achou_hora:
        return None
    hora, minuto = int(achou_hora.group(1)), int(achou_hora.group(2))
    if not (0 <= hora <= 23 and 0 <= minuto <= 59):
        return None

    base = referencia or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    local = base.astimezone(_FUSO_DA_CASA)
    if "amanh" in texto:
        local = local + timedelta(days=1)

    try:
        inicio = local.replace(hour=hora, minute=minuto, second=0, microsecond=0)
    except ValueError:
        return None
    return inicio.astimezone(timezone.utc)


# Por quanto tempo uma aposta já confirmada serve de fonte pro confronto
# de uma tip nova.
#
# 12h cobre com folga o intervalo entre duas tips do mesmo jogo (a #101 e
# a #118 saíram com 5h30 de diferença) sem alcançar o jogo do mesmo
# jogador em OUTRO dia do torneio — que seria o confronto errado.
_JANELA_CONFRONTO_ANTERIOR = timedelta(hours=12)


def _confronto_de_aposta_anterior(
    jogador1: str, esporte: str, referencia: Optional[datetime]
) -> Optional[CanonicalMatch]:
    """Procura o confronto numa aposta recente já confirmada do mesmo jogador.

    O tipster manda várias apostas no mesmo jogo ("Diana vencer a partida"
    de manhã, "Diana vencer um set" à tarde), e o matcher tratava cada uma
    como se fosse a primeira — a segunda ia procurar do zero e às vezes
    não achava, nascendo "não encontrada" com a resposta já gravada na
    linha de cima (caso real: #101 confirmou Townsend x Shnaider, a #118
    meia hora depois não achou).

    Só aceita quando o nome bate com UM dos dois lados e o jogo está
    dentro da janela — ver _JANELA_CONFRONTO_ANTERIOR.
    """
    ancora = referencia or datetime.now(timezone.utc)
    threshold = settings.SUPERBET_FUZZY_THRESHOLD

    try:
        anteriores = list_confrontos_conhecidos(desde=ancora - _JANELA_CONFRONTO_ANTERIOR)
    except Exception:
        logger.exception("Falha ao consultar confrontos já conhecidos — seguindo sem essa fonte.")
        return None

    for bet in anteriores:
        if (bet.esporte or Esporte.TENIS.value) != esporte:
            continue
        if bet.data_hora is None:
            continue
        # O jogo tem que estar na mesma janela: o mesmo jogador joga de
        # novo em outro dia do torneio, e aí o confronto é outro.
        if abs((bet.data_hora - ancora).total_seconds()) > _JANELA_CONFRONTO_ANTERIOR.total_seconds():
            continue
        if not (
            names_match(jogador1, bet.jogador1, threshold)
            or names_match(jogador1, bet.jogador2, threshold)
        ):
            continue

        logger.info(
            "Confronto reaproveitado da aposta #%s: %s x %s | %s | %s",
            bet.id, bet.jogador1, bet.jogador2, bet.torneio, bet.data_hora,
        )
        return CanonicalMatch(
            jogador1_oficial=bet.jogador1,
            jogador2_oficial=bet.jogador2,
            torneio_oficial=bet.torneio,
            data_hora=bet.data_hora,
            sofascore_event_id=bet.sofascore_event_id,
        )

    return None


def horario_da_primeira_selecao(
    selecoes: Optional[list],
    esporte: str = Esporte.TENIS.value,
    referencia: Optional[datetime] = None,
) -> Optional[datetime]:
    """Horário do jogo mais cedo entre as seleções de uma múltipla.

    A múltipla não tem um confronto único, então nunca ganhava data_hora —
    e sem ela a aposta cai na seção "horário não encontrado" da imagem e
    não tem por onde ser ordenada no painel. O horário útil é o da
    PRIMEIRA perna a começar: é quando a múltipla passa a valer.

    Procura cada seleção como "só o favorito" (é o que o print dá: um nome
    por perna), e devolve o menor horário achado. Seleção que nenhuma
    fonte reconhece é ignorada — meia informação é melhor que nenhuma.

    Best-effort de propósito: nunca levanta, e devolve None se não achar
    nada (aí a múltipla segue como era antes).
    """
    if not selecoes:
        return None

    horarios: list[datetime] = []
    for nome in selecoes:
        if not nome:
            continue
        try:
            canonico = _confronto_de_aposta_anterior(nome, esporte, referencia)
            if canonico is None:
                canonico = find_canonical_match_by_name(
                    nome, esporte=esporte, referencia=referencia
                )
        except Exception:
            logger.exception("Múltipla: falha ao buscar o horário de %r — ignorando essa perna.", nome)
            continue

        if canonico and canonico.data_hora:
            logger.info("Múltipla: %r joga %s", nome, canonico.data_hora.isoformat())
            horarios.append(canonico.data_hora)

    if not horarios:
        logger.info("Múltipla: nenhuma seleção teve horário confirmado (%s).", selecoes)
        return None

    return min(horarios)


def _completar_event_id(
    canonico: CanonicalMatch,
    esporte: str,
    dias_de_busca: int,
    referencia: Optional[datetime],
) -> CanonicalMatch:
    """Tenta preencher o sofascore_event_id de um confronto já confirmado.

    Serve pros confrontos que vieram do 365scores ou da Superbet, que não
    têm esse id. Ter o id faz o score_updater consultar o placar por ID
    (exato) em vez de por nome (fuzzy, e sujeito a divergência de grafia
    entre fontes).

    Nunca troca o confronto: só aproveita o id quando o jogo que o
    SofaScore devolve é o MESMO (pair_matches sobre os nomes oficiais).
    Falha em silêncio — sem o id o pipeline continua funcionando.
    """
    if canonico.sofascore_event_id is not None:
        return canonico

    try:
        do_sofascore = find_canonical_match(
            canonico.jogador1_oficial, canonico.jogador2_oficial,
            esporte=esporte, dias_de_busca=dias_de_busca, referencia=referencia,
        )
    except Exception:
        logger.debug("Busca do event_id no SofaScore falhou — seguindo sem ele.", exc_info=True)
        return canonico

    if do_sofascore is None or do_sofascore.sofascore_event_id is None:
        return canonico

    if not pair_matches(
        canonico.jogador1_oficial, canonico.jogador2_oficial,
        do_sofascore.jogador1_oficial, do_sofascore.jogador2_oficial,
        settings.SUPERBET_FUZZY_THRESHOLD,
    ):
        return canonico

    logger.info(
        "event_id %s obtido no SofaScore para %s x %s (confronto veio de outra fonte).",
        do_sofascore.sofascore_event_id, canonico.jogador1_oficial, canonico.jogador2_oficial,
    )
    canonico.sofascore_event_id = do_sofascore.sofascore_event_id
    return canonico


def _confirmar_par(
    jogador1: str,
    jogador2: str,
    esporte: str,
    dias_de_busca: int,
    referencia: Optional[datetime],
) -> Optional[CanonicalMatch]:
    """Confirma um confronto de que já temos os DOIS nomes, tentando as
    fontes em ordem de custo/confiabilidade:

      1. 365scores — 1 request por dia consultado. É a fonte primária desde
         que ficou claro que o SofaScore bloqueia o IP dos runners do
         GitHub Actions com 403 de forma recorrente (ver
         scores365_client.find_canonical_match_365).
      2. SofaScore, varredura do dia (~80 requests por dia consultado).
      3. SofaScore, busca por nome — rota diferente (/search/all +
         /team/{id}/featured-event), costuma passar quando a varredura não
         passa. Só aceita se o confronto devolvido bater com os dois nomes
         que já tínhamos, senão um homônimo entraria no lugar.

    Devolve None se nenhuma confirmar — aí find_match ainda tenta a
    Superbet, que é a única fonte que enxerga o jogo do ponto de vista de
    quem vai apostar nele.
    """
    threshold = settings.SUPERBET_FUZZY_THRESHOLD

    canonico = find_canonical_match_365(
        jogador1, jogador2, threshold, esporte=esporte, referencia=referencia,
    )
    if canonico is not None:
        # O 365scores confirma o confronto mas NÃO devolve event_id do
        # SofaScore (é id de outro site). Sem event_id, o score_updater
        # acompanha o placar por NOME, que é mais frágil: se o nome
        # divergir entre as fontes, o resultado nunca fecha e a aposta fica
        # "ao vivo" pra sempre.
        #
        # Medido em 04/09/2026: 14 das 15 apostas do dia estavam sem
        # event_id, porque o 365scores é a primeira fonte e o SofaScore
        # nunca era consultado.
        #
        # Então: usa os nomes OFICIAIS que o 365scores devolveu (melhores
        # que os do OCR) pra tentar o event_id no SofaScore. Se falhar,
        # segue com o confronto do 365scores — o event_id é um bônus, não
        # requisito.
        canonico = _completar_event_id(canonico, esporte, dias_de_busca, referencia)
        return canonico

    canonico = find_canonical_match(
        jogador1, jogador2, esporte=esporte, dias_de_busca=dias_de_busca, referencia=referencia,
    )
    if canonico is not None:
        return canonico

    for nome in (jogador1, jogador2):
        candidato = find_canonical_match_by_name(nome, esporte=esporte, referencia=referencia)
        if candidato and pair_matches(
            jogador1, jogador2,
            candidato.jogador1_oficial, candidato.jogador2_oficial,
            threshold,
        ):
            logger.info(
                "SofaScore: confronto resolvido pela busca por nome (%r) — "
                "as outras fontes não confirmaram.", nome,
            )
            return candidato
    return None


def _confronto_pela_superbet(
    jogador1: str,
    jogador2: Optional[str],
    esporte: str,
    dias_de_busca: int,
    referencia: Optional[datetime],
    odd_tip: Optional[float],
    exigir_hoje: bool,
    ja_tentou_par: bool = False,
    prefere_duplas: bool = False,
) -> tuple[Optional[CanonicalMatch], Optional[str]]:
    """Última tentativa: usa a busca da Superbet como fonte do confronto.

    Antes isso só acontecia quando o tipster citava um jogador só. Passou a
    valer sempre porque, quando as duas fontes de dados esportivos falham
    (SofaScore bloqueado, jogo não listado no 365scores), a casa de apostas
    é a única que ainda enxerga o jogo — e ela tem tudo que precisamos:
    os dois nomes, o dia/horário e até o link direto da partida.

    Faz duas coisas, nessa ordem:
      1. Com o par corrigido pela Superbet (ela resolve typo do tipster e
         perfil desatualizado de uma vez), tenta CONFIRMAR nas fontes
         esportivas — que é sempre melhor, porque traz o event_id do
         SofaScore e o nome oficial do torneio.
      2. Se nem assim, aceita o próprio card da Superbet como o confronto.

    Devolve (confronto, link_do_card). O link vem junto porque é o link
    exato da partida naquela casa — melhor que o link aproximado que o
    adaptador montaria depois.
    """
    card = buscar_confronto_na_superbet(jogador1, esporte, odd_tip, prefere_duplas)
    if card is None and jogador2:
        card = buscar_confronto_na_superbet(jogador2, esporte, odd_tip, prefere_duplas)
    if card is None:
        return None, None

    if exigir_hoje and not _card_eh_hoje(card.horario_texto):
        # A Superbet só lista jogo aberto pra aposta: se o card não é de
        # hoje, o jogo de hoje deste jogador já acabou e o que sobrou na
        # busca é o de amanhã. Confirmar por ele grava a aposta no
        # confronto errado — bug real com "de la torre" (ver docstring de
        # buscar_confronto_na_superbet).
        logger.info(
            "Superbet achou %s x %s, mas o card não é de hoje — "
            "provavelmente o jogo de hoje já encerrou; ignorando esse card.",
            card.jogador1, card.jogador2,
        )
        return None, None

    # Só vale reconsultar as fontes esportivas se a Superbet trouxe nomes
    # DIFERENTES dos que já falharam (typo corrigido, apelido expandido).
    # Se são os mesmos, repetir a consulta é garantir a mesma resposta
    # negativa gastando o tempo do ciclo — que é justamente o que falta
    # quando tudo está lento/bloqueado.
    par_e_novidade = not (
        ja_tentou_par
        and jogador2
        and pair_matches(
            jogador1, jogador2, card.jogador1, card.jogador2,
            settings.SUPERBET_FUZZY_THRESHOLD,
        )
    )
    if par_e_novidade:
        confirmado = _confirmar_par(card.jogador1, card.jogador2, esporte, dias_de_busca, referencia)
        if confirmado is not None:
            return confirmado, card.link

    data_hora = _data_hora_do_card(card.horario_texto, referencia)
    logger.info(
        "Confronto aceito pela Superbet (nenhuma fonte esportiva confirmou): "
        "%s x %s | card=%r | horário=%s",
        card.jogador1, card.jogador2, card.horario_texto, data_hora,
    )
    return (
        CanonicalMatch(
            jogador1_oficial=card.jogador1,
            jogador2_oficial=card.jogador2,
            torneio_oficial=None,   # o card não traz o torneio
            data_hora=data_hora,
            sofascore_event_id=None,  # id da Superbet não serve pro SofaScore
        ),
        card.link,
    )


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
    texto_tip: Optional[str] = None,
) -> MatchInfo:
    """
    Ponto de entrada principal, chamado pelo listener.py depois do
    extractor. `jogador2` pode ser None — caso do tipster ter citado só o
    favorito (ver extractor.find_favorite_only); nesse caso o adversário é
    resolvido consultando o SofaScore só pelo jogador1.

    `odd_tip` é a odd que o tipster mandou; quando informada, desempata
    entre o jogo de simples e o de duplas do mesmo jogador na Superbet (ver
    buscar_confronto_na_superbet).

    `texto_tip` é o texto original da tip. Serve pra detectar aposta de
    DUPLAS ("Borges e hijikata na duplas"), que por padrão é descartada —
    ver tip_e_de_duplas.
    """
    if not jogador1:
        return MatchInfo(encontrado=False, esporte=esporte)

    link_superbet: Optional[str] = None
    prefere_duplas = tip_e_de_duplas(texto_tip)

    if jogador2:
        # Com os dois nomes em mãos, vale tentar as fontes esportivas
        # direto (365scores primeiro, ver _confirmar_par).
        canonico = _confirmar_par(jogador1, jogador2, esporte, dias_de_busca, referencia)
        if canonico is None:
            # Nenhuma fonte esportiva confirmou. Antes a busca parava aqui
            # e a aposta nascia "não encontrada" — inclusive quando o único
            # problema era o SofaScore bloqueando o runner e o jogo não
            # estar listado no 365scores. A Superbet enxerga o jogo (é onde
            # a aposta existe, afinal) e resolve de quebra typo do tipster.
            canonico, link_superbet = _confronto_pela_superbet(
                jogador1, jogador2, esporte, dias_de_busca, referencia, odd_tip,
                exigir_hoje=False, ja_tentou_par=True, prefere_duplas=prefere_duplas,
            )
    else:
        # Só o favorito citado: aqui as fontes esportivas precisariam
        # adivinhar o adversário, e é exatamente onde elas erram (nome com
        # typo, jogador com perfil duplicado/desatualizado). A Superbet vem
        # ANTES porque lista só jogos abertos pra aposta e por isso resolve
        # typo e perfil velho de uma vez; com o par em mãos, a consulta às
        # fontes esportivas deixa de ser adivinhação e vira confirmação de
        # um confronto específico (é o que _confronto_pela_superbet faz).
        #
        # exigir_hoje: sem o adversário, um card de amanhã quase sempre
        # significa que o jogo de hoje desse jogador já acabou — aceitar
        # gravaria a aposta no confronto errado.
        # ANTES de tudo: o confronto pode já estar gravado numa aposta
        # anterior do mesmo jogo. É a fonte mais confiável que existe (foi
        # confirmada por uma fonte esportiva na primeira vez) e a mais
        # barata (uma consulta ao banco, sem rede).
        canonico = _confronto_de_aposta_anterior(jogador1, esporte, referencia)

        if canonico is None:
            canonico, link_superbet = _confronto_pela_superbet(
                jogador1, None, esporte, dias_de_busca, referencia, odd_tip,
                exigir_hoje=True, prefere_duplas=prefere_duplas,
            )
        if canonico is None:
            canonico = find_canonical_match_by_name(
                jogador1, esporte=esporte, referencia=referencia, aceitar_duplas=prefere_duplas
            )

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
        # Se a própria busca da Superbet já devolveu o card da partida, esse
        # link é o exato — reaproveita em vez de abrir outro navegador pra
        # procurar de novo o que já foi achado.
        if link_superbet and adapter.slug == "superbet":
            links[adapter.slug] = {"nome": adapter.display_name, "url": link_superbet, "exato": True}
            logger.info("%s: link %s (exato, reaproveitado da busca)", adapter.slug, link_superbet)
            continue
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
