"""
extractor.py
============
Recebe o print (imagem) e/ou a legenda (texto) de uma mensagem do grupo de
tips e devolve um ExtractedBet estruturado: jogador1, jogador2, torneio,
mercado e odd.

Dois motores possíveis (escolhidos via .env -> OCR_ENGINE):

  - "easyocr" (padrão): 100% local e gratuito. EasyOCR lê o texto da imagem,
    e depois aplicamos regras/regex (parse_free_text) para estruturar os
    campos. Primeira execução baixa os modelos (~200MB) e demora mais;
    depois disso roda offline.

  - "gemini": usa o Free Tier da API Gemini (multimodal) para interpretar a
    imagem diretamente e devolver JSON já estruturado. Mais robusto com
    prints "bagunçados", mas depende de internet e de uma GEMINI_API_KEY.

Em ambos os casos, se o tipster também escrever um texto/legenda junto do
print (ex: "Mercado: Vencedor do jogo | Odd: 1.85"), esse texto é combinado
com o resultado do OCR — texto explícito manda mais que o OCR.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from typing import Optional

from config import settings
from models import Esporte, ExtractedBet
from nameutils import names_match

logger = logging.getLogger(__name__)


# ==============================================================================
# 1) MOTOR EASYOCR (local, gratuito)
# ==============================================================================

@lru_cache(maxsize=1)
def _get_easyocr_reader():
    """Carrega o modelo do EasyOCR uma única vez (é pesado para instanciar)."""
    import easyocr  # import tardio: só carrega se OCR_ENGINE=easyocr

    logger.info("Carregando modelo EasyOCR (pt + en)... isso pode demorar na 1a vez.")
    return easyocr.Reader(["pt", "en"], gpu=False)


def ocr_image_easyocr(image_path: str) -> str:
    """Roda o EasyOCR na imagem e devolve todo o texto detectado, uma linha por bloco."""
    reader = _get_easyocr_reader()
    resultados = reader.readtext(image_path, detail=0, paragraph=True)
    texto = "\n".join(resultados)
    logger.debug("OCR (easyocr) extraiu: %r", texto)
    return texto


# ==============================================================================
# 2) MOTOR GEMINI (free tier, opcional)
# ==============================================================================

_GEMINI_PROMPT = """\
Você recebe o print de uma dica (tip) de aposta esportiva de TÊNIS OU BASQUETE,
enviada em um grupo de Telegram. Primeiro identifique o ESPORTE: tênis tem 2
JOGADORES individuais (nome próprio de pessoa, ex: "Djokovic", "Alcaraz");
basquete tem 2 TIMES (nome de franquia/cidade, ex: "Los Angeles Lakers",
"Boston Celtics", "Flamengo"), frequentemente citado junto de uma liga (NBA,
NBB, Euroliga, NCAA, FIBA). Depois identifique se é uma aposta SIMPLES (1
confronto só) ou uma MÚLTIPLA/COMBINADA (várias seleções de jogos diferentes
combinadas numa odd só — geralmente um app de apostas mostra isso como uma
lista de "Nome - Nome" com um mercado e uma sub-odd cada, seguido de uma odd
total bem maior que qualquer sub-odd individual). Sinais de múltipla:
cabeçalho tipo "N SELEÇÕES", "SIMPLES • N" (esse "SIMPLES" ali é o TIPO de
aposta de cada perna individual, não quer dizer que a aposta toda tem 1 jogo
só), ou texto "+N seleções mais".

IMPORTANTE — nem toda mensagem do grupo é uma tip nova. Mensagens como
"Zheng está pago! Cash", "Merida está pago!", avisos de resultado (green/red),
resumos do dia (ex: "12 Greens, 6 Reds"), ou comentários soltos ("Faltam buse
e zheng pra gente fechar hoje") citam nomes de jogadores mas NÃO são uma aposta
nova — não têm imagem de bet-slip nenhuma, só texto solto. Se não houver
imagem anexada E o texto não citar uma odd numérica explícita, NÃO invente
jogador1/mercado a partir de um nome solto no texto: devolva jogador1 null e
mercado null (tipo_aposta "simples", odd null se não achar). Só preencha
jogador1/mercado a partir de texto puro (sem imagem) quando houver uma odd
numérica clara acompanhando o nome (ex: "Hurkacz odd: 2.85").

Responda SOMENTE com um JSON válido, sem markdown, sem comentários, no formato
exato:

{
  "esporte": "tenis" ou "basquete",

  "tipo_aposta": "simples" ou "multipla",

  // Preencha isto SE tipo_aposta == "simples" (senão deixe tudo null):
  // "jogador2" pode ficar null mesmo em tipo_aposta="simples" — é comum o
  // tipster citar só o favorito (ex: "Hurkacz odd: 2.85"), sem o
  // adversário; jogador1 nesse caso é o único nome citado.
  "jogador1": "nome do primeiro tenista OU time, ou o único citado (ou null se não achar nenhum)",
  "jogador2": "nome do segundo tenista OU time (ou null se só um foi citado, ou se não achar)",
  "torneio": "nome do torneio/circuito/liga, ex: 'ATP Us Open' ou 'NBA' (ou null)",
  // mercado: qualquer mercado de aposta de tênis OU basquete, ex: "Vencedor
  // da partida", "Vencedor do 2º set", "Over 22.5 games", "Total de aces
  // mais de 8.5", "Dupla falta", "Vencer sem perder set", "Placar exato
  // 2-0", "Tie-break", "Handicap de games -3.5" (tênis); "Handicap Lakers
  // -5.5", "Mais de 215.5 pontos", "Menos de 210.5 pontos", "Vencedor da
  // partida" (basquete) etc — extraia o mercado como ele aparece no
  // print/legenda, não se limite aos exemplos acima.
  // Se só o nome do jogador/time + a odd forem citados, sem mercado
  // explícito (ex: "Hurkacz odd: 2.85"), assuma "Vencedor da partida" (é o
  // mercado implícito mais comum nesse caso).
  "mercado": "mercado da aposta (ou null só se genuinamente não der pra inferir)",

  // Preencha isto SE tipo_aposta == "multipla" (senão deixe null/[]):
  // "selecoes": só o jogador FAVORECIDO de cada perna (quem a aposta diz que
  // vence/cobre aquele mercado) — 1 nome por seleção, na ordem que aparecem.
  // Se algumas seleções estiverem ocultas atrás de um "+N seleções mais" (ou
  // similar) e você não conseguir ver o nome delas, NÃO invente: deixe
  // "selecoes" só com as que você consegue ler de verdade na imagem, e marque
  // "selecoes_ocultas": true.
  "selecoes": ["Sobrenome1", "Sobrenome2"],
  "selecoes_ocultas": false,

  // Sempre preencha isto, nos dois casos:
  "odd": 1.85  // número decimal da odd TOTAL da aposta (ou null se não achar)
}

Texto adicional (legenda da mensagem, pode estar vazio) que complementa a imagem:
---
{legenda}
---
"""


def _get_gemini_client():
    from google import genai  # import tardio: só carrega se OCR_ENGINE=gemini

    if not settings.GEMINI_API_KEY:
        raise RuntimeError(
            "OCR_ENGINE=gemini mas GEMINI_API_KEY não está definido no .env. "
            "Gere uma chave grátis em https://aistudio.google.com/apikey"
        )
    return genai.Client(api_key=settings.GEMINI_API_KEY)


def extract_with_gemini(image_path: Optional[str], caption_text: str) -> ExtractedBet:
    """Envia a imagem (se houver) + legenda para o Gemini e pede JSON estruturado."""
    from google.genai import types

    client = _get_gemini_client()
    # .replace() em vez de .format(): o prompt tem um JSON de exemplo cheio
    # de chaves { } literais, que .format() tentaria interpretar como
    # placeholders — causou um KeyError em produção (bug pré-existente,
    # nunca tinha sido exercitado antes por acaso; o JSON de exemplo ficou
    # maior/com comentários "//" depois da mudança de detecção de múltipla,
    # o que tornou o erro consistente).
    prompt = _GEMINI_PROMPT.replace("{legenda}", caption_text or "(sem legenda)")

    contents: list = [prompt]
    if image_path:
        with open(image_path, "rb") as f:
            image_bytes = f.read()
        mime = "image/png" if image_path.lower().endswith("png") else "image/jpeg"
        contents.append(types.Part.from_bytes(data=image_bytes, mime_type=mime))

    response = client.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )

    try:
        data = json.loads(response.text)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Gemini não devolveu JSON válido: %r", getattr(response, "text", None))
        return ExtractedBet(texto_bruto=caption_text, confianca=0.0)

    odd = data.get("odd")
    try:
        odd = float(odd) if odd is not None else None
    except (TypeError, ValueError):
        odd = None

    # Fallback "tenis" se o Gemini não preencher (ou vier algo inesperado) —
    # não é garantido que o modelo sempre siga o contrato à risca.
    esporte = data.get("esporte")
    if esporte not in (Esporte.TENIS.value, Esporte.BASQUETE.value):
        esporte = Esporte.TENIS.value

    if data.get("tipo_aposta") == "multipla":
        selecoes = [s for s in (data.get("selecoes") or []) if s]
        selecoes_ocultas = bool(data.get("selecoes_ocultas"))
        # Se há seleções que o Gemini não conseguiu ler (escondidas atrás de
        # "+N seleções mais"), não fingimos saber quem são — vira uma
        # múltipla "genérica", sem lista de jogadores (ver notifier.py).
        if selecoes_ocultas:
            selecoes = []
        return ExtractedBet(
            odd=odd,
            texto_bruto=caption_text,
            tipo_aposta="multipla",
            selecoes=selecoes,
            confianca=0.7 if (odd is not None and selecoes) else 0.4,
            esporte=esporte,
        )

    jogador1 = data.get("jogador1")
    jogador2 = data.get("jogador2")
    mercado = data.get("mercado")

    # Cinto de segurança determinístico (além da instrução no prompt, que
    # LLMs às vezes não seguem à risca): mensagens sem imagem E sem odd
    # explícita citando um nome de jogador não são confiavelmente uma tip
    # nova — bug real visto em produção com "Zheng está pago! Cash" (aviso
    # de resultado, não bet-slip) virando jogador1="Zheng", mercado=
    # "Vencedor da partida" fabricados do nada. Sem imagem pra confirmar e
    # sem odd, descarta.
    if not image_path and odd is None:
        jogador1 = None
        jogador2 = None
        mercado = None

    if jogador1 and jogador2:
        confianca = 0.9
    elif jogador1:  # só o favorito, sem adversário — ainda válido, ver models.ExtractedBet.valido
        confianca = 0.6
    else:
        confianca = 0.3

    bet = ExtractedBet(
        jogador1=jogador1,
        jogador2=jogador2,
        torneio=data.get("torneio"),
        mercado=mercado,
        odd=odd,
        texto_bruto=caption_text,
        confianca=confianca,
        esporte=esporte,
    )
    return bet


# ==============================================================================
# 3) PARSER DE TEXTO LIVRE (usado após o EasyOCR, e também como fallback)
# ==============================================================================

# Tipsters costumam escrever em um destes formatos:
#   "Djokovic x Alcaraz"          |  "Djokovic vs Alcaraz"
#   "Mercado: Vencedor do jogo"   |  "Odd: 1.85"                | "@1.85"
#   "ATP Us Open" / "WTA 250 ..." | "Torneio: Roland Garros"

_PLAYERS_PATTERNS = [
    # "Jogador1 x Jogador2" / "Jogador1 X Jogador2" / "Jogador1 vs Jogador2"
    # Usa [ \t] em vez de \s para NÃO atravessar quebras de linha (senão o
    # nome do 2º jogador poderia "vazar" para dentro da linha seguinte,
    # ex: um label "Torneio:" logo abaixo).
    re.compile(
        r"([A-ZÀ-Ú][\wÀ-ú.'\-]+(?:[ \t]+[A-ZÀ-Ú][\wÀ-ú.'\-]+){0,3})"
        r"[ \t]+(?:x|vs\.?|X)[ \t]+"
        r"([A-ZÀ-Ú][\wÀ-ú.'\-]+(?:[ \t]+[A-ZÀ-Ú][\wÀ-ú.'\-]+){0,3})"
    ),
]

# Card de app de apostas (Superbet/Betano/bet365): o OCR lê o nome dos dois
# jogadores em sequência, numa única linha, sem separador — ex:
# "Matteo Arnaldi James Duckworth". Cada jogador é "Nome Sobrenome" (2
# palavras capitalizadas), então casamos exatamente 2+2 palavras na linha.
# Usado só como fallback (ver _find_players_in_card_line) porque outras
# linhas do print (torneio, textos de UI) também podem ter 4 palavras
# capitalizadas em sequência.
_CARD_LINE_PATTERN = re.compile(
    r"^([A-ZÀ-Ú][\wÀ-ú.'\-]+[ \t]+[A-ZÀ-Ú][\wÀ-ú.'\-]+)"
    r"[ \t]+"
    r"([A-ZÀ-Ú][\wÀ-ú.'\-]+[ \t]+[A-ZÀ-Ú][\wÀ-ú.'\-]+)$"
)

# Linhas que claramente NÃO são o nome dos jogadores (torneio, botões de UI
# do app, rótulos de mercado) — ignoradas ao procurar a linha do card.
_CARD_LINE_IGNORE_KEYWORDS = re.compile(
    r"\b(ATP|WTA|ITF|CHALLENGER|GRAND SLAM|MASTERS|OPEN|TENIS|TÊNIS|"
    r"CRIAR APOSTA|APOSTA|COMBINADA|SIMPLES)\b",
    re.IGNORECASE,
)


def _find_players_in_card_line(texto: str) -> tuple[Optional[str], Optional[str]]:
    """Procura, linha por linha, o formato 'Nome Sobrenome Nome Sobrenome' de
    um card de app de apostas, ignorando linhas de torneio/UI (ver
    _CARD_LINE_IGNORE_KEYWORDS)."""
    for linha in texto.splitlines():
        linha = linha.strip()
        if not linha or _CARD_LINE_IGNORE_KEYWORDS.search(linha):
            continue
        m = _CARD_LINE_PATTERN.match(linha)
        if m:
            return m.group(1).strip(), m.group(2).strip()
    return None, None

_LABELLED_PATTERNS = {
    "torneio": re.compile(r"(?:torneio|tournament|campeonato)\s*[:\-]\s*(.+)", re.IGNORECASE),
    # "mercado"/"market" são rótulos inequívocos. "aposta" é ambíguo — o
    # tipster tanto escreve "Aposta: Vencedor da partida" (mercado de
    # verdade) quanto "Aposta ao vivo: Arnaldi odd: 1.72" (só o nome do
    # favorito, sem mercado explícito) — por isso "aposta" não entra aqui;
    # ver _APOSTA_FAVORITO_PATTERN/_infer_market_from_favorite mais abaixo,
    # que trata esse segundo caso.
    "mercado": re.compile(r"(?:mercado|market)\s*[:\-]\s*(.+)", re.IGNORECASE),
}

_ODD_PATTERNS = [
    re.compile(r"(?:odd|@|cota)[ \t]*[:\-]?[ \t]*(\d{1,3}[.,]\d{1,3})", re.IGNORECASE),
    re.compile(r"\b(\d\.\d{2})\b"),  # último recurso: qualquer "1.85"-like isolado
]

_TOURNAMENT_KEYWORDS = re.compile(r"\b(ATP|WTA|ITF|CHALLENGER|GRAND SLAM|MASTERS 1000|WTA 125)\b[^\n]*", re.IGNORECASE)

# O qualificador final exclui explicitamente "odd"/"@"/"cota" (não usa
# [^\n]* genérico) — sem isso, numa linha tipo "vencer um set odd: 1.85" o
# mercado capturado engolia a palavra "odd" junto, e o texto "restante"
# usado por find_favorite_only (ver parse_free_text) perdia o marcador que
# ele precisa pra reconhecer a odd, quebrando a extração do jogador.
_MARKET_KEYWORDS = re.compile(
    r"\b(vencedor(?: do jogo| da partida)?|match winner|handicap de games?|"
    r"total de games?|over\/?under|over \d+[.,]?\d*|under \d+[.,]?\d*|"
    # "vencer o 2º set" / "vencer o 2 set" / "vencer 2 set" / "vencer um set"
    # (genérico, sem número — ex: apostado antes do jogo começar, cobre
    # qualquer set) / "vencer o primeiro/segundo/terceiro set" (por extenso).
    r"vence(?:r)? (?:o |a )?(?:1|2|3|um|uma|primeiro|segundo|terceiro)[ºoa]?\s*set|"
    r"vencedor do \d\ºset|vencedor do set \d|"
    r"total de aces?|aces? (?:mais|menos) de \d+[.,]?\d*|"
    r"dupla falta|duplas? faltas?|"
    r"(?:mais|menos) de \d+[.,]?\d* games?|"
    r"placar exato|resultado exato|"
    r"vence(?:r)? sem perder set(?:s)?|vence(?:r)? sem perder \d\s*sets?|"
    r"vence(?:r)? com \d\s*sets?|"
    r"vencedor por \d\s*x\s*\d|"
    r"tie[\s\-]?break|"
    # Basquete: handicap de pontos por time e total de pontos (over/under).
    r"handicap(?: de pontos)?\s*[+-]?\s*\d+[.,]?\d*|"
    r"(?:mais|menos|over|under)\s+de\s+\d+[.,]?\d*\s*pontos?)\b(?:(?!odd\b|@|cota\b)[^\n:])*",
    re.IGNORECASE,
)

# Palavras-chave que sinalizam basquete (liga, terminologia de jogo) —
# usadas pelo parser regex local para decidir esporte quando o texto não
# vier de um motor que já classifica (Gemini). Best-effort: sem essas
# palavras, o texto é tratado como tênis (comportamento atual preservado).
_BASQUETE_KEYWORDS = re.compile(
    r"\b(NBA|NBB|EUROLIGA|EUROCUP|NCAA|FIBA|BASQUETE|BASKETBALL|QUARTO|"
    r"OVERTIME|PRORROGA[ÇC][ÃA]O|HANDICAP)\b",
    re.IGNORECASE,
)

# Total de PONTOS com linha alta = basquete. Em tênis a linha de
# "mais/menos de" é de GAMES e fica na casa das unidades ("games mais de
# 8.5", ver _GEMINI_PROMPT); no basquete é de pontos e fica perto de 200.
# 40 separa os dois com folga enorme — nenhum jogo de tênis chega perto, e
# nenhum total de basquete fica abaixo.
#
# Bug real (03/09/2026): a tip "AO VIVO! mais de 198.5 pontos odd: 1.95"
# não tinha nenhuma das palavras-chave acima, foi classificada como tênis,
# e o parser de favorito pegou a palavra "pontos" como nome do jogador
# (aposta #64 gravada como "pontos x ?", nao_encontrada).
_BASQUETE_TOTAL_PONTOS = re.compile(r"(\d{2,3}(?:[.,]\d+)?)\s*pontos", re.IGNORECASE)


def _detectar_esporte(texto: str) -> str:
    """Best-effort: basquete se o texto citar liga/terminologia de basquete
    ou um total de pontos alto, senão tênis (default — preserva o
    comportamento atual do parser)."""
    texto = texto or ""
    if _BASQUETE_KEYWORDS.search(texto):
        return Esporte.BASQUETE.value
    for bruto in _BASQUETE_TOTAL_PONTOS.findall(texto):
        try:
            if float(bruto.replace(",", ".")) >= 40:
                return Esporte.BASQUETE.value
        except ValueError:
            continue
    return Esporte.TENIS.value

# Tipsters costumam escrever só o nome do favorito antes da odd, sem citar o
# mercado explicitamente — ex: "Aposta ao vivo: Arnaldi odd: 1.72". Nesse
# caso o mercado é implícito: "esse jogador vencer a partida". Este padrão
# extrai o trecho entre "aposta ... :" e "odd/@/cota" pra tentar achar qual
# dos dois jogadores foi citado (ver _infer_market_from_favorite abaixo).
_APOSTA_FAVORITO_PATTERN = re.compile(
    r"aposta[^:\n]*:\s*(.+?)\s*(?:odd|@|cota)\b", re.IGNORECASE,
)

# Formato "só o favorito, sem adversário": o tipster cita apenas o nome do
# jogador em quem apostou, sem "x"/"vs" e sem um segundo nome — ex:
# "Thomas Faurel odd: 1.74" ou "APOSTA AO VIVO! Sonego odd: 2.20". Usado
# como último recurso (depois de _PLAYERS_PATTERNS e _find_players_in_card_line
# falharem em achar os DOIS jogadores) — ver find_favorite_only() abaixo.
# Aceita 1 a 3 palavras capitalizadas (nome próprio, ou só sobrenome) logo
# antes do rótulo da odd, ignorando ruído de prefixo (ex: "APOSTA AO VIVO!").
_FAVORITO_UNICO_PATTERN = re.compile(
    r"([A-ZÀ-Ú][\wÀ-ú.'\-]+(?:[ \t]+[A-ZÀ-Ú][\wÀ-ú.'\-]+){0,2})"
    r"[ \t]+(?:odd|@|cota)[ \t]*[:\-]?[ \t]*\d",
    re.IGNORECASE,
)

# Palavras que não são nome de jogador, mesmo capitalizadas — evita que
# _FAVORITO_UNICO_PATTERN capture o prefixo em vez do nome real (ex: em
# "APOSTA AO VIVO! Sonego odd: 2.20", sem essa lista o regex greedy pegaria
# "Sonego" corretamente só por estar mais perto da odd, mas outras frases
# podem ter ruído colado no nome — mantido como salvaguarda).
# Formato "N set [Jogador]" ou "Set N [Jogador]": o tipster põe o número do
# set ANTES do nome (em qualquer uma das duas ordens — confirmado com o
# usuário, esse tipster varia), como prefixo do mercado, em vez de
# "[Jogador] vencer o N set" (a ordem mais comum, já coberta por
# _MARKET_KEYWORDS) — ex: "Ao vivo 1 set Elmer odd: 1.90" ou "Ao vivo! Set
# 2 molcan odd: 2.35" significam "Elmer vencer o 1º set"/"molcan vencer o
# 2º set", não é status do jogo.
_SET_PREFIXO_PATTERN = re.compile(
    r"\b(?:(?P<num1>1|2|3|um|uma|primeiro|segundo|terceiro)[ºoa]?\s*sets?"
    r"|sets?\s+(?P<num2>1|2|3))\s+"
    r"([A-ZÀ-Ú][\wÀ-ú.'\-]+(?:[ \t]+[A-ZÀ-Ú][\wÀ-ú.'\-]+){0,2})"
    r"[ \t]+(?:odd|@|cota)\b",
    re.IGNORECASE,
)

_SET_NUMERO_POR_EXTENSO = {
    "um": "1", "uma": "1", "primeiro": "1",
    "segundo": "2", "terceiro": "3",
}


def _normaliza_numero_set(numero: str) -> str:
    return _SET_NUMERO_POR_EXTENSO.get(numero.lower(), numero)


_FAVORITO_IGNORE_WORDS = {
    "aposta", "ao", "vivo", "live", "odd", "cota", "green", "red", "tip",
    # Pronomes/artigos/verbos comuns que aparecem capitalizados no início
    # de frase (maiúscula de início de sentença, não nome próprio) e que o
    # regex greedy de find_favorite_only não distingue de um nome de
    # jogador de verdade — bug real visto em produção 2x: "Aquela odd 100
    # marota vai ficar pra amanhã" e "montei uma odd 100 pra quem quiser
    # sonhar" viraram jogador1="Aquela"/"montei uma" (ambos comentários
    # sobre uma múltipla futura, não uma tip com nome de jogador nenhum).
    "aquela", "aquele", "essa", "esse", "isso", "uma", "um", "montei",
    # Termos de MERCADO, não nomes de jogador. "AO VIVO! mais de 198.5
    # pontos odd: 1.95" (basquete, 03/09/2026) virou jogador1="pontos" e
    # mercado="pontos vencer a partida" — a aposta era total de pontos, sem
    # nome de jogador nenhum no texto.
    "pontos", "games", "sets", "mais", "menos", "acima", "abaixo",
    "total", "handicap", "over", "under",
}


def find_favorite_only(texto: str) -> Optional[str]:
    """Último recurso: acha o nome do jogador favorito quando o tipster cita
    só ele (sem adversário) — ver _FAVORITO_UNICO_PATTERN acima."""
    for linha in texto.splitlines():
        m = _FAVORITO_UNICO_PATTERN.search(linha)
        if not m:
            continue
        candidato = m.group(1).strip()
        # remove palavras de ruído (ex: "AO VIVO! Sonego" -> "Sonego") e
        # números soltos (ex: "1 set Elmer" -> "Elmer", já sem "set" pela
        # lista acima, mas o "1" também precisa cair fora).
        palavras = [
            p for p in candidato.split()
            if p.strip("!:.,").lower() not in _FAVORITO_IGNORE_WORDS and not p.strip("!:.,").isdigit()
        ]
        if palavras:
            return " ".join(palavras).strip("!:.,")
    return None


def _infer_market_from_favorite(texto: str, jogador1: Optional[str], jogador2: Optional[str]) -> Optional[str]:
    """Se o texto citar (por nome ou sobrenome) um dos jogadores logo antes da
    odd, sem mercado explícito, assume o mercado implícito "esse jogador
    ganhar"."""
    if not jogador1 or not jogador2:
        return None
    m = _APOSTA_FAVORITO_PATTERN.search(texto)
    if not m:
        return None
    citado = m.group(1).strip()
    if not citado:
        return None
    for jogador in (jogador1, jogador2):
        if names_match(citado, jogador, threshold=70):
            return f"{jogador} ganhar"
    return None


def parse_free_text(texto: str) -> ExtractedBet:
    """
    Heurística baseada em regex. Não é infalível — ajuste os padrões acima
    conforme o formato real dos tipsters do seu grupo (veja README, seção
    'Calibrando o extractor').
    """
    texto = texto or ""
    jogador1 = jogador2 = torneio = mercado = None
    odd: Optional[float] = None

    # Formato "N set [Jogador] odd" (número do set ANTES do nome) — ver
    # _SET_PREFIXO_PATTERN. Checado primeiro porque resolve jogador E
    # mercado juntos, evitando a ambiguidade de "1 set Elmer" ser lido como
    # nome "set Elmer" ou mercado solto sem dono.
    m_set_prefixo = _SET_PREFIXO_PATTERN.search(texto)
    if m_set_prefixo:
        numero_bruto = m_set_prefixo.group("num1") or m_set_prefixo.group("num2")
        numero = _normaliza_numero_set(numero_bruto)
        jogador1 = m_set_prefixo.group(3).strip()
        mercado = f"{jogador1} vencer o {numero}º set"

    for pattern in _PLAYERS_PATTERNS:
        m = pattern.search(texto)
        if m:
            jogador1, jogador2 = m.group(1).strip(), m.group(2).strip()
            mercado = None  # achou os 2 jogadores de verdade — descarta o palpite do set-prefixo acima
            break

    if jogador1 is None or jogador2 is None:
        j1, j2 = _find_players_in_card_line(texto)
        if j1 and j2:
            jogador1, jogador2, mercado = j1, j2, None

    for campo, pattern in _LABELLED_PATTERNS.items():
        m = pattern.search(texto)
        if m:
            valor = m.group(1).strip()
            if campo == "torneio":
                torneio = valor
            elif campo == "mercado":
                mercado = valor

    if torneio is None:
        m = _TOURNAMENT_KEYWORDS.search(texto)
        if m:
            torneio = m.group(0).strip()
            # Heurística simples: se a linha continha "Torneio - Fulano vs
            # Ciclano", corta no separador para não incluir os nomes dos
            # jogadores dentro do campo torneio.
            for sep in (" - ", " – ", ":"):
                if sep in torneio:
                    torneio = torneio.split(sep, 1)[0].strip()
                    break

    # O mercado (ex: "vencer o 2º set") precisa ser identificado ANTES de
    # tentar achar "só o favorito" (find_favorite_only): sem isso, um texto
    # como "Petkovic vencer um set odd: 1.85" faz o regex de nome-perto-da-
    # odd capturar "vencer um set" em vez de "Petkovic" — bug real visto em
    # produção. Removendo o trecho do mercado do texto antes de buscar o
    # nome, sobra só "Petkovic odd: 1.85" pro find_favorite_only.
    texto_sem_mercado = texto
    if mercado is None:
        m = _MARKET_KEYWORDS.search(texto)
        if m:
            mercado = m.group(0).strip()
            texto_sem_mercado = texto[: m.start()] + texto[m.end() :]

    # Último recurso: só o nome do favorito, sem adversário (ver
    # find_favorite_only) — o adversário é resolvido depois pelo matcher.py
    # consultando o SofaScore (find_canonical_match_by_name).
    if jogador1 is None and jogador2 is None:
        jogador1 = find_favorite_only(texto_sem_mercado)

    if mercado is None:
        mercado = _infer_market_from_favorite(texto, jogador1, jogador2)

    # Só o favorito citado, sem mercado explícito nem adversário — ex:
    # "Hurkacz odd: 2.85". Assume o mercado implícito mais comum: esse
    # jogador vencer a partida.
    if mercado is None and jogador1 and not jogador2:
        mercado = f"{jogador1} vencer a partida"

    # O mercado capturado por _MARKET_KEYWORDS vem sem o nome do jogador
    # (ex: "vencer o 2 set", "dupla falta") — quando só 1 jogador é citado
    # (sem adversário, ver find_favorite_only acima), isso deixa o card da
    # notificação com um mercado "solto", sem dizer de quem é a aposta.
    # Prefixa com o jogador nesse caso. Quando há 2 jogadores, não prefixa —
    # o card já mostra "Jogo: J1 vs J2" separadamente (ver notifier.py), não
    # precisa repetir o nome dentro do mercado. Também normaliza "o N set"
    # -> "o Nº set" (ex: "o 2 set" -> "o 2º set").
    if mercado:
        mercado = re.sub(r"\bo (\d) set\b", r"o \1º set", mercado, flags=re.IGNORECASE)
        if jogador1 and not jogador2 and jogador1.lower() not in mercado.lower():
            mercado = f"{jogador1} {mercado}"

    for pattern in _ODD_PATTERNS:
        m = pattern.search(texto)
        if m:
            try:
                odd = float(m.group(1).replace(",", "."))
                break
            except ValueError:
                continue

    confianca = 0.0
    if jogador1 and jogador2:
        confianca += 0.5
    elif jogador1:  # só o favorito, sem adversário — menos confiável que o par completo
        confianca += 0.25
    if odd is not None:
        confianca += 0.3
    if mercado:
        confianca += 0.2

    return ExtractedBet(
        jogador1=jogador1,
        jogador2=jogador2,
        torneio=torneio,
        mercado=mercado,
        odd=odd,
        texto_bruto=texto,
        confianca=round(confianca, 2),
        esporte=_detectar_esporte(texto),
    )


# ==============================================================================
# 4) FUNÇÃO PRINCIPAL — chamada pelo listener.py
# ==============================================================================

def _extract_with_easyocr_and_text(
    image_path: Optional[str], caption_text: str, timeout_s: Optional[float] = None
) -> ExtractedBet:
    """EasyOCR na imagem (se houver) combinado com a legenda, via parse_free_text.
    Usado tanto quando OCR_ENGINE=easyocr quanto como fallback de
    OCR_ENGINE=gemini quando a chamada ao Gemini falha (ver extract_bet_info).

    timeout_s: limite de tempo pro EasyOCR (carregar modelo + ler a imagem).
    Sem isso, o fallback do Gemini herdaria o mesmo custo de "1ª execução
    baixa os modelos (~200MB) e demora mais" que o modo OCR_ENGINE=easyocr
    já documenta — só que aqui rodando dentro do timeout-minutes: 4 do
    workflow (poll-listener.yml), que também precisa sobrar tempo pro resto
    do pipeline (Playwright/matcher). Sem limite, um fallback lento poderia
    estourar o timeout do job inteiro em vez de simplesmente degradar pro
    parser de só-texto. None (usado no caminho OCR_ENGINE=easyocr, onde o
    EasyOCR já É o motor escolhido, não um fallback de emergência) = sem
    limite, mesmo comportamento de sempre."""
    ocr_text = ""
    if image_path:
        try:
            if timeout_s is not None:
                from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(ocr_image_easyocr, image_path)
                    try:
                        ocr_text = future.result(timeout=timeout_s)
                    except FutureTimeoutError:
                        logger.warning(
                            "EasyOCR (fallback do Gemini) não terminou em %.0fs, seguindo só com a legenda.",
                            timeout_s,
                        )
            else:
                ocr_text = ocr_image_easyocr(image_path)
        except Exception:
            logger.exception("Falha no EasyOCR, seguindo só com a legenda.")
    texto_combinado = f"{caption_text}\n{ocr_text}".strip()
    return parse_free_text(texto_combinado)


def extract_bet_info(image_path: Optional[str], caption_text: Optional[str] = None) -> ExtractedBet:
    """
    Ponto de entrada único do módulo.

    image_path:   caminho local do print baixado pelo listener (None se a
                  mensagem só tinha texto).
    caption_text: legenda/texto da mensagem do Telegram (pode ser None/"").
    """
    caption_text = caption_text or ""

    if settings.OCR_ENGINE == "gemini":
        try:
            bet = extract_with_gemini(image_path, caption_text)
        except Exception:
            # Bug real visto em produção (03/09/2026): o Gemini caiu com 503
            # ("high demand") bem no momento de 2 tips que tinham print
            # anexado ("Kilian vencer o 2º set", "Moro canas vencer o 2º
            # set"). Cair direto para parse_free_text(caption_text) ignora a
            # imagem por completo — se o nome do jogador só aparece no print
            # (não na legenda digitada pelo tipster), o nome extraído sai
            # truncado/incompleto, e nem a Superbet nem o SofaScore
            # conseguem casar depois (mesmo os dois funcionando certo — o
            # problema é a entrada ruim, não o matching). Por isso, se há
            # imagem, tenta o EasyOCR local como 2ª linha de defesa antes de
            # cair pro parser de só-texto — mesma lógica usada quando
            # OCR_ENGINE=easyocr (ver _extract_with_easyocr_and_text).
            logger.exception("Falha ao usar Gemini, tentando EasyOCR como fallback.")
            # 90s: generoso o bastante pra baixar os modelos na 1ª chamada
            # de um runner novo (~200MB) e ler 1 imagem, mas curto o
            # bastante pra sempre sobrar tempo dentro do timeout-minutes: 4
            # do workflow — ver docstring de _extract_with_easyocr_and_text.
            bet = _extract_with_easyocr_and_text(image_path, caption_text, timeout_s=90.0)
    else:
        bet = _extract_with_easyocr_and_text(image_path, caption_text)

    # Se o texto explícito da legenda tiver labels que o OCR/print não tinha
    # (comum: tipster escreve "Odd: 1.85" na legenda, print só mostra o jogo),
    # complementamos o resultado sem sobrescrever o que já veio preenchido.
    # Pulado para múltipla: jogador1/jogador2/torneio ficam vazios de
    # propósito nesse caso (ver extract_with_gemini) — não faz sentido
    # tentar "completar" com um par de nomes que parse_free_text ache solto
    # na legenda, que não tem relação com as seleções da múltipla.
    if bet.tipo_aposta != "multipla" and caption_text and (not bet.mercado or bet.odd is None or not bet.torneio):
        extra = parse_free_text(caption_text)
        mercado_extra = extra.mercado
        # extra.mercado foi montado por parse_free_text() com o nome que
        # ELE achou na legenda sozinha (ex: "molcan", sobrenome truncado do
        # texto puro "Set 2 molcan odd..."), que pode ser mais incompleto
        # que o nome que o motor principal (Gemini, tipicamente) já achou
        # olhando a imagem inteira (ex: "Alex Molcan"). Troca o nome dentro
        # do texto do mercado pelo mais completo dos dois — bug real visto
        # em produção: card saía com "molcan vencer o 2º set" mesmo já
        # tendo "Alex Molcan" confirmado como jogador1.
        if (
            mercado_extra
            and extra.jogador1
            and bet.jogador1
            and extra.jogador1.lower() != bet.jogador1.lower()
            and mercado_extra.lower().startswith(extra.jogador1.lower())
        ):
            mercado_extra = bet.jogador1 + mercado_extra[len(extra.jogador1) :]
        bet.mercado = bet.mercado or mercado_extra
        bet.odd = bet.odd if bet.odd is not None else extra.odd
        bet.torneio = bet.torneio or extra.torneio
        bet.jogador1 = bet.jogador1 or extra.jogador1
        bet.jogador2 = bet.jogador2 or extra.jogador2
        # bet.esporte nunca fica "vazio" (sempre tem um default) — só
        # promove pra basquete se a legenda sozinha sinalizar isso e o
        # resultado principal ainda estiver no default "tenis" (evita
        # sobrescrever uma detecção de basquete já feita pelo Gemini/OCR).
        if bet.esporte == Esporte.TENIS.value and extra.esporte == Esporte.BASQUETE.value:
            bet.esporte = extra.esporte

    if bet.tipo_aposta == "multipla":
        logger.info(
            "Extração: múltipla, seleções=%s | odd=%s | confiança=%.2f",
            bet.selecoes, bet.odd, bet.confianca,
        )
    else:
        logger.info(
            "Extração: %s vs %s | torneio=%s | mercado=%s | odd=%s | confiança=%.2f",
            bet.jogador1, bet.jogador2, bet.torneio, bet.mercado, bet.odd, bet.confianca,
        )
    return bet


# ==============================================================================
# 4) AVISO DE CASH-OUT ANTECIPADO ("Fulano está pago!"/"...Cash")
# ==============================================================================
# Padrão real observado no grupo: o tipster avisa quando já deu cash-out numa
# aposta em andamento, citando só o nome do jogador + "pago"/"cash" — não é
# uma tip nova (sem mercado, sem odd, geralmente sem imagem), é a confirmação
# de que aquela aposta específica já foi resolvida como green, mesmo que o
# jogador tome a virada depois (o resultado real do jogo deixa de importar,
# ver models.ResultadoAposta.CASHOUT). Ex. reais: "Zheng está pago! Cash",
# "Merida está pago! Cash".
_CASHOUT_PATTERN = re.compile(
    r"^([A-ZÀ-Ú][\wÀ-ú.'\-]+(?:[ \t]+[A-ZÀ-Ú][\wÀ-ú.'\-]+){0,2}?)"
    r"\s+(?:est[áa]|ta|tá)?\s*pago!?\s*(?:cash)?\b",
    re.IGNORECASE,
)


def detectar_aviso_cashout(texto: str) -> Optional[str]:
    """
    Devolve o nome do jogador citado se `texto` for um aviso de cash-out
    antecipado (ex: "Zheng está pago! Cash"), ou None se não bater com esse
    padrão específico — deliberadamente estreito (só "Nome [está] pago[!]
    [Cash]" no início da mensagem) pra não confundir com outros usos da
    palavra "cash" no grupo (ex: convites promocionais mencionando
    "cashout" no meio de um texto mais longo, que não são avisos de
    resultado de uma aposta específica).
    """
    texto = (texto or "").strip()
    m = _CASHOUT_PATTERN.match(texto)
    if not m:
        return None
    return m.group(1).strip()
