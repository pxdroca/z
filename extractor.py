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
from models import ExtractedBet
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
Você recebe o print de uma dica (tip) de aposta esportiva de tênis, enviada em um
grupo de Telegram. Primeiro identifique se é uma aposta SIMPLES (1 confronto só)
ou uma MÚLTIPLA/COMBINADA (várias seleções de jogos diferentes combinadas numa
odd só — geralmente um app de apostas mostra isso como uma lista de "Nome - Nome"
com um mercado e uma sub-odd cada, seguido de uma odd total bem maior que
qualquer sub-odd individual). Sinais de múltipla: cabeçalho tipo "N SELEÇÕES",
"SIMPLES • N" (esse "SIMPLES" ali é o TIPO de aposta de cada perna individual,
não quer dizer que a aposta toda tem 1 jogo só), ou texto "+N seleções mais".

Responda SOMENTE com um JSON válido, sem markdown, sem comentários, no formato
exato:

{
  "tipo_aposta": "simples" ou "multipla",

  // Preencha isto SE tipo_aposta == "simples" (senão deixe tudo null):
  "jogador1": "nome do primeiro tenista (ou null se não achar)",
  "jogador2": "nome do segundo tenista (ou null se não achar)",
  "torneio": "nome do torneio/circuito, ex: 'ATP Us Open' (ou null)",
  "mercado": "mercado da aposta, ex: 'Vencedor da partida', 'Over 22.5 games' (ou null)",

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
    prompt = _GEMINI_PROMPT.format(legenda=caption_text or "(sem legenda)")

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
        )

    bet = ExtractedBet(
        jogador1=data.get("jogador1"),
        jogador2=data.get("jogador2"),
        torneio=data.get("torneio"),
        mercado=data.get("mercado"),
        odd=odd,
        texto_bruto=caption_text,
        confianca=0.9 if data.get("jogador1") and data.get("jogador2") else 0.3,
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

_MARKET_KEYWORDS = re.compile(
    r"\b(vencedor(?: do jogo| da partida)?|match winner|handicap de games?|"
    r"total de games?|over\/?under|over \d|under \d|"
    r"vencedor do \d\ºset|vence(?:r)? o (?:1|2|3)[ºo]?\s*set|"
    r"vencedor do set \d)\b[^\n]*",
    re.IGNORECASE,
)

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
_FAVORITO_IGNORE_WORDS = {
    "aposta", "ao", "vivo", "live", "odd", "cota", "green", "red", "tip",
}


def find_favorite_only(texto: str) -> Optional[str]:
    """Último recurso: acha o nome do jogador favorito quando o tipster cita
    só ele (sem adversário) — ver _FAVORITO_UNICO_PATTERN acima."""
    for linha in texto.splitlines():
        m = _FAVORITO_UNICO_PATTERN.search(linha)
        if not m:
            continue
        candidato = m.group(1).strip()
        # remove palavras de ruído do início (ex: "AO VIVO! Sonego" -> "Sonego")
        palavras = [p for p in candidato.split() if p.strip("!:.,").lower() not in _FAVORITO_IGNORE_WORDS]
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

    for pattern in _PLAYERS_PATTERNS:
        m = pattern.search(texto)
        if m:
            jogador1, jogador2 = m.group(1).strip(), m.group(2).strip()
            break

    if jogador1 is None or jogador2 is None:
        jogador1, jogador2 = _find_players_in_card_line(texto)

    # Último recurso: só o nome do favorito, sem adversário (ver
    # find_favorite_only) — o adversário é resolvido depois pelo matcher.py
    # consultando o SofaScore (find_canonical_match_by_name).
    if jogador1 is None and jogador2 is None:
        jogador1 = find_favorite_only(texto)

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

    if mercado is None:
        m = _MARKET_KEYWORDS.search(texto)
        if m:
            mercado = m.group(0).strip()

    if mercado is None:
        mercado = _infer_market_from_favorite(texto, jogador1, jogador2)

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
    )


# ==============================================================================
# 4) FUNÇÃO PRINCIPAL — chamada pelo listener.py
# ==============================================================================

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
            logger.exception("Falha ao usar Gemini, caindo para parser de texto puro.")
            bet = parse_free_text(caption_text)
    else:
        ocr_text = ""
        if image_path:
            try:
                ocr_text = ocr_image_easyocr(image_path)
            except Exception:
                logger.exception("Falha no EasyOCR, seguindo só com a legenda.")
        texto_combinado = f"{caption_text}\n{ocr_text}".strip()
        bet = parse_free_text(texto_combinado)

    # Se o texto explícito da legenda tiver labels que o OCR/print não tinha
    # (comum: tipster escreve "Odd: 1.85" na legenda, print só mostra o jogo),
    # complementamos o resultado sem sobrescrever o que já veio preenchido.
    # Pulado para múltipla: jogador1/jogador2/torneio ficam vazios de
    # propósito nesse caso (ver extract_with_gemini) — não faz sentido
    # tentar "completar" com um par de nomes que parse_free_text ache solto
    # na legenda, que não tem relação com as seleções da múltipla.
    if bet.tipo_aposta != "multipla" and caption_text and (not bet.mercado or bet.odd is None or not bet.torneio):
        extra = parse_free_text(caption_text)
        bet.mercado = bet.mercado or extra.mercado
        bet.odd = bet.odd if bet.odd is not None else extra.odd
        bet.torneio = bet.torneio or extra.torneio
        bet.jogador1 = bet.jogador1 or extra.jogador1
        bet.jogador2 = bet.jogador2 or extra.jogador2

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
