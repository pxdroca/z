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
import time
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

IMPORTANTE 2 — print de CONVERSA não é tip, mesmo tendo bet-slip dentro.
O tipster às vezes manda o screenshot de um chat (dele com outra pessoa, ou
de outro grupo) em que aparece a aposta de TERCEIROS. Sinais de que o print
é uma conversa, e não a tip dele:

  - @ de outra pessoa, nome de usuário, "Imin"/"2h"/"agora" (marcador de
    tempo de mensagem), balões de chat;
  - valores em dinheiro que não são a praxe do tipster: "APOSTA 230,00 R$",
    "PRÊMIO 920,00 R$", "ODDS TOTAIS";
  - várias apostas diferentes empilhadas, às vezes em LADOS OPOSTOS do
    mesmo jogo (uma no time A, outra no handicap do time B) — a tip dele é
    sempre UMA seleção;
  - texto de comentário em volta ("Odd 5.15 pra acordar forrado? Vc é
    bizarro").

Nesses casos devolva jogador1, jogador2 e mercado null: é conversa sobre
aposta, não uma aposta nova. Caso real (04/09/2026): um print assim virou
uma tip no "Tianjin Pioneers", que era justamente o ADVERSÁRIO do time em
que o tipster havia apostado.

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
  // explícito (ex: "Hurkacz odd: 2.85"), assuma que a aposta é nesse
  // jogador vencer — e ESCREVA O NOME DELE no mercado:
  // "Hurkacz vencer a partida", NÃO apenas "Vencedor da partida".
  //
  // Isso vale principalmente quando o print mostra os DOIS jogadores (card
  // de jogo ao vivo, com odds dos dois lados) e a legenda cita só um: o
  // mercado é sobre O JOGADOR CITADO NA LEGENDA, mesmo que ele apareça em
  // segundo no card. Um mercado só "Vencedor da partida" não diz de quem é
  // a aposta e é inútil pra conferir o resultado depois.
  //
  // A legenda manda no nome, mas o print manda na grafia: o tipster
  // escreve de memória e erra (ex: legenda "Putinseva", print
  // "Yulia Putintseva" — é a mesma pessoa). Use a grafia do print em
  // jogador1/jogador2 e no mercado.
  //
  // A ODD também identifica o lado: num card com "1  1.33" e "2  3.30", a
  // odd 3.30 da legenda é a do jogador da coluna 2. Use isso pra confirmar
  // em qual dos dois a aposta foi feita.
  "mercado": "mercado da aposta (ou null só se genuinamente não der pra inferir)",

  // Preencha isto SE tipo_aposta == "multipla" (senão deixe null/[]):
  // "selecoes": só o jogador FAVORECIDO de cada perna (quem a aposta diz que
  // vence/cobre aquele mercado) — 1 nome por seleção, na ordem que aparecem.
  //
  // Use SÓ O SOBRENOME, nunca o nome completo e nunca o confronto. A lista
  // é o título da aposta no painel e na imagem, onde o espaço é uma linha
  // só: "Wild, Faurel" cabe e diz quem tem que ganhar; "Thiago Seyboth
  // Wild vs Juan Pablo Varillas (1.37), ..." é cortado no meio e, pior,
  // não deixa claro em qual dos dois lados foi a aposta.
  //
  // Nem confronto, nem sub-odd, nem mercado dentro do nome: o confronto de
  // cada perna não caberia, a sub-odd não é a odd da aposta (essa vai em
  // "odd") e o mercado vai em "mercado".
  //
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


# Quantas vezes insistir no Gemini antes de desistir, e quanto esperar
# entre as tentativas.
#
# Bug real (02/09/2026, 12 ocorrências no log): o Gemini devolveu
# "503 UNAVAILABLE — This model is currently experiencing high demand.
# Spikes in demand are usually temporary. Please try again later." e a
# extração caía na hora pro parser de regex, que lê muito menos (fica sem
# jogadores, e a tip nasce erro_extracao). O próprio erro diz que é
# temporário e pede pra tentar de novo — era só o que faltava fazer.
#
# 3 tentativas com 2s/6s de espera: cabe folgado no ciclo de 5 min do
# workflow e cobre o pico curto, que é o padrão observado.
_GEMINI_TENTATIVAS = 3
_GEMINI_ESPERAS = (2.0, 6.0)

# Erros que vale retentar: sobrecarga do modelo (503), limite de taxa
# (429) e falha interna (500). Um 400/401/403 é problema nosso
# (chave inválida, prompt malformado) e retentar só perderia tempo.
_GEMINI_STATUS_RETENTAVEIS = (429, 500, 503)


def _gerar_com_retry(client, types, contents):
    """Chama o Gemini insistindo enquanto o erro for temporário.

    Ver _GEMINI_TENTATIVAS para o motivo (503 de pico de demanda).
    Erros não-retentáveis sobem na primeira vez — quem chama já trata
    caindo pro parser de texto.
    """
    from google.genai import errors as genai_errors

    ultimo: Exception | None = None
    for tentativa in range(1, _GEMINI_TENTATIVAS + 1):
        try:
            return client.models.generate_content(
                model=settings.GEMINI_MODEL,
                contents=contents,
                config=types.GenerateContentConfig(response_mime_type="application/json"),
            )
        except genai_errors.APIError as exc:
            if exc.code not in _GEMINI_STATUS_RETENTAVEIS or tentativa == _GEMINI_TENTATIVAS:
                raise
            espera = _GEMINI_ESPERAS[min(tentativa - 1, len(_GEMINI_ESPERAS) - 1)]
            logger.warning(
                "Gemini devolveu %s (tentativa %d/%d) — nova tentativa em %.0fs.",
                exc.code, tentativa, _GEMINI_TENTATIVAS, espera,
            )
            ultimo = exc
            time.sleep(espera)

    # Inalcançável (o loop retorna ou levanta), mas deixa explícito.
    raise ultimo if ultimo else RuntimeError("Gemini: falha sem exceção registrada")


# Padrões que denunciam que a seleção veio como confronto/sub-odd em vez
# de só o nome do favorito: " vs ", " x ", "(1.37)".
_SELECAO_SUJA = re.compile(r"\s+(?:vs?|x)\s+|\(", re.IGNORECASE)


def _so_o_sobrenome(selecao: str) -> str:
    """Reduz uma seleção de múltipla ao sobrenome do favorito.

    A lista de seleções é o TÍTULO da aposta no painel e na imagem, onde só
    cabe uma linha. O prompt já pede sobrenome, mas isto é a garantia
    determinística — e também conserta seleção antiga já gravada.

    Corta em " vs "/" x " (fica o favorito, que vem primeiro na perna),
    remove sub-odd entre parênteses e devolve a última palavra do nome.
    """
    texto = (selecao or "").strip()
    if not texto:
        return ""
    # "Wild vs Varillas (1.37)" -> "Wild"
    texto = _SELECAO_SUJA.split(texto)[0].strip()
    partes = [p for p in texto.split() if p]
    return partes[-1] if partes else ""


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

    response = _gerar_com_retry(client, types, contents)

    try:
        data = json.loads(response.text)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Gemini não devolveu JSON válido: %r", getattr(response, "text", None))
        return ExtractedBet(texto_bruto=caption_text, confianca=0.0)

    return _bet_do_json_do_llm(data, caption_text, image_path)


def _bet_do_json_do_llm(
    data: dict, caption_text: str, image_path: Optional[str]
) -> ExtractedBet:
    """Converte o JSON devolvido por um LLM (Gemini ou Groq) em ExtractedBet.

    Compartilhado pelos dois provedores: ambos respondem no mesmo contrato
    (_GEMINI_PROMPT), então a validação, os cintos de segurança e a
    normalização precisam ser idênticos — senão o resultado passaria a
    depender de qual provedor atendeu, que é exatamente o tipo de
    inconsistência difícil de rastrear depois.
    """
    odd = data.get("odd")
    try:
        odd = float(odd) if odd is not None else None
    except (TypeError, ValueError):
        odd = None

    # Fallback "tenis" se o LLM não preencher (ou vier algo inesperado) —
    # não é garantido que o modelo sempre siga o contrato à risca.
    esporte = data.get("esporte")
    if esporte not in (Esporte.TENIS.value, Esporte.BASQUETE.value):
        esporte = Esporte.TENIS.value

    if data.get("tipo_aposta") == "multipla":
        selecoes = [_so_o_sobrenome(s) for s in (data.get("selecoes") or []) if s]
        selecoes = [s for s in selecoes if s]
        selecoes_ocultas = bool(data.get("selecoes_ocultas"))
        # Se há seleções que o LLM não conseguiu ler (escondidas atrás de
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

    # Anúncio de venda de vaga (cita odd de verdade, então o guard acima
    # não pega) — mesmo tratamento do parser de texto. Ver parece_anuncio.
    #
    # Só sem imagem: um print de bet-slip legítimo pode vir com legenda
    # promocional ("...última chance"), e aí quem manda são os nomes lidos
    # do print, não a propaganda da legenda.
    if not image_path and parece_anuncio(caption_text) and (jogador1 or jogador2 or mercado):
        logger.info(
            "LLM: texto parece anúncio de venda de vaga — descartando %r x %r / %r",
            jogador1, jogador2, mercado,
        )
        jogador1 = jogador2 = mercado = None

    # Sinal do handicap colado no número, igual ao parser de texto — o
    # LLM copia a grafia do print, e a Superbet escreve o "-" separado.
    if mercado:
        mercado = _normalizar_handicap(mercado)

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
# 2b) GROQ — segundo LLM, backup do Gemini
# ==============================================================================
#
# Existe porque o Gemini cai com 503 ("This model is currently experiencing
# high demand") em rajadas, e nessas janelas a extração desabava pro parser
# de regex, que lê muito menos — a tip nascia sem jogadores. O EasyOCR local
# ajuda, mas lê o print como texto cru: não entende qual dos dois jogadores
# é o da aposta, nem separa múltipla de simples.
#
# Groq e não outra chave do Gemini: o 503 é do modelo, não da conta, então
# uma segunda chave Google não cobriria justamente o erro observado.
# Provedor separado = falha independente.

def extract_with_groq(image_path: Optional[str], caption_text: str) -> ExtractedBet:
    """Mesma tarefa de extract_with_gemini, no Groq (API compatível com OpenAI).

    Usa o MESMO prompt e o mesmo parser de resposta (_bet_do_json_do_llm),
    pra que trocar de provedor não mude o resultado — só quem atendeu.
    """
    import base64
    from urllib import request as urlrequest

    if not settings.GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY não definido — sem backup do Gemini.")

    prompt = _GEMINI_PROMPT.replace("{legenda}", caption_text or "(sem legenda)")

    # Formato de mensagens da API de chat (compatível com OpenAI): o texto
    # e a imagem vão como "partes" do mesmo turno do usuário.
    partes: list = [{"type": "text", "text": prompt}]
    if image_path:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        mime = "image/png" if image_path.lower().endswith("png") else "image/jpeg"
        partes.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})

    corpo = json.dumps({
        "model": settings.GROQ_MODEL,
        "messages": [{"role": "user", "content": partes}],
        "response_format": {"type": "json_object"},
        "temperature": 0,
    }).encode()

    req = urlrequest.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=corpo,
        headers={
            "Authorization": f"Bearer {settings.GROQ_API_KEY}",
            "Content-Type": "application/json",
        },
    )
    # urllib e não a lib da Groq: é uma chamada HTTP só, e evita mais uma
    # dependência no requirements (o runner do GitHub Actions instala tudo
    # a cada execução).
    with urlrequest.urlopen(req, timeout=settings.GROQ_TIMEOUT_S) as resp:
        resposta = json.loads(resp.read().decode())

    try:
        texto = resposta["choices"][0]["message"]["content"]
        data = json.loads(texto)
    except (KeyError, IndexError, json.JSONDecodeError, TypeError):
        logger.warning("Groq não devolveu JSON válido: %r", resposta)
        return ExtractedBet(texto_bruto=caption_text, confianca=0.0)

    return _bet_do_json_do_llm(data, caption_text, image_path)


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


# "handicap - 20.5" -> "handicap -20.5": cola o sinal no número.
#
# Com o sinal separado o mercado fica ambíguo pra quem lê ("handicap -
# 20.5 França" parece usar o "-" como separador entre a palavra e o
# número, não como sinal do handicap) e também pro resultado_checker, que
# tem que adivinhar. Colado, "-20.5" é inequivocamente a linha.
_HANDICAP_SINAL_SOLTO = re.compile(
    r"\b(handicap(?:\s+de\s+pontos)?)\s*([+-])\s+(\d)", re.IGNORECASE,
)


def _normalizar_handicap(mercado: str) -> str:
    """Cola o "+"/"-" no número do handicap (ver _HANDICAP_SINAL_SOLTO)."""
    return _HANDICAP_SINAL_SOLTO.sub(r"\1 \2\3", mercado)


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


# Sufixo de nome de franquia de basquete ("Hawks", "36ers", "Lakers"...).
# É o que permite achar os dois times numa linha suja de OCR sem cair em
# texto de UI: exige o sufixo nos DOIS lados do confronto.
#
# Bug real (03/09/2026): a tip de basquete "AO VIVO! mais de 198.5 pontos"
# vinha com um print onde o OCR leu certinho
# "Q1 * 9' Illawara Hawks Adelaide 36ers", mas nenhum padrão pegava os
# times: _CARD_LINE_PATTERN exige a linha inteira em 2+2 palavras
# capitalizadas, e aqui há prefixo de placar ("Q1 * 9'") e um nome que
# começa com dígito ("36ers"). A aposta ficou sem confronto.
_TIME_BASQUETE_SUFIXOS = (
    r"(?:[0-9]*ers|Hawks|Lakers|Celtics|Bulls|Heat|Nets|Knicks|Suns|Kings|"
    r"Jazz|Magic|Pacers|Pistons|Raptors|Rockets|Spurs|Thunder|Wizards|"
    r"Warriors|Clippers|Grizzlies|Hornets|Mavericks|Nuggets|Pelicans|"
    r"Timberwolves|Trail Blazers|Blazers|Bucks|Cavaliers|Wildcats|Tigers|"
    r"Eagles|Bears|Wolves|Giants|Kangaroos|Bullets|Breakers|Phoenix|Taipans|"
    r"Crocodiles|United|Cairns|Sixers)"
)

# "<Cidade> <Sufixo> <Cidade> <Sufixo>" em qualquer lugar da linha (o OCR
# põe placar/tempo antes). Cidade = 1-2 palavras capitalizadas.
_BASQUETE_CONFRONTO_PATTERN = re.compile(
    r"((?:[A-ZÀ-Ú][\wÀ-ú.'\-]+[ \t]+){1,2}" + _TIME_BASQUETE_SUFIXOS + r")"
    r"[ \t]+"
    r"((?:[A-ZÀ-Ú][\wÀ-ú.'\-]+[ \t]+){1,2}" + _TIME_BASQUETE_SUFIXOS + r")",
    re.IGNORECASE,
)


# Seleção nacional com marcador de gênero: "Hungria (F)", "França (F)",
# "Coreia do Sul (F)", "Brasil (M)". Não tem sufixo de franquia, então o
# padrão acima nunca casa — e como são jogos de seleção (FIBA, olimpíada),
# o "(F)"/"(M)" é justamente o que os torna reconhecíveis sem abrir a
# porta pra texto de UI.
#
# Bug real (04/09/2026): a tip "Handicap - 20.5 França odd: 1.87" veio com
# um print onde o OCR leu "Hungria (F)" e "França (F)" em linhas
# separadas (placar "5 : 4" no meio) — a aposta #121 nasceu com
# jogador1/jogador2 = "?" mesmo com os dois nomes legíveis no texto.
_SELECAO_PATTERN = re.compile(
    r"^([A-ZÀ-Ú][\wÀ-ú.'\-]*(?:[ \t]+(?:de[ \t]+|do[ \t]+|da[ \t]+)?[A-ZÀ-Ú][\wÀ-ú.'\-]*){0,2})"
    r"[ \t]*\((F|M)\)$",
)


def _find_national_teams(texto: str) -> tuple[Optional[str], Optional[str]]:
    """Acha duas seleções nacionais, uma por linha.

    O OCR desses prints põe o placar entre os dois nomes ("Hungria (F)" /
    "5 : 4" / "França (F)"), então não dá pra exigir os dois na mesma
    linha como _find_basketball_teams faz. Pega as duas PRIMEIRAS linhas
    que são só "<País> (F|M)": a repetição em maiúsculas mais abaixo (a
    tabela de odds) traz os mesmos times, e parar nas duas primeiras
    mantém a ordem casa/visitante que o card mostra no topo.
    """
    achados: list[str] = []
    for linha in texto.splitlines():
        linha = " ".join(linha.split())
        m = _SELECAO_PATTERN.match(linha)
        if not m:
            continue
        nome = m.group(0).strip()
        # Case-insensitive na comparação: o mesmo confronto aparece de novo
        # em CAIXA ALTA na tabela de odds ("HUNGRIA (F)"), e sem isso o
        # segundo time viria a ser a repetição do primeiro.
        if any(nome.casefold() == a.casefold() for a in achados):
            continue
        achados.append(nome)
        if len(achados) == 2:
            return achados[0], achados[1]
    return None, None


def _find_basketball_teams(texto: str) -> tuple[Optional[str], Optional[str]]:
    """Acha os dois times de basquete no texto do OCR.

    Separado de _find_players_in_card_line porque nome de franquia não
    segue o formato "Nome Sobrenome" de tenista: tem cidade composta
    ("Los Angeles Lakers") e sufixo que pode começar com dígito
    ("Adelaide 36ers"). Exigir o sufixo de franquia nos dois lados é o que
    evita casar com texto de UI do app.
    """
    for linha in texto.splitlines():
        linha = " ".join(linha.split())
        if not linha:
            continue
        m = _BASQUETE_CONFRONTO_PATTERN.search(linha)
        if m:
            return m.group(1).strip(), m.group(2).strip()

    # Franquia não achada: pode ser jogo de seleção ("Hungria (F)").
    return _find_national_teams(texto)

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


# Sinais de que o print é uma CONVERSA sobre aposta (screenshot de chat,
# bet-slip de outra pessoa), não a tip do tipster. Ver o bloco
# "IMPORTANTE 2" em _GEMINI_PROMPT para os exemplos reais.
#
# Bug real (04/09/2026): a mensagem 245 era um screenshot de outra pessoa
# comentando a tip ("Odd 5.15 pra acordar forrado? Vc é bizarro"), com as
# apostas DELA dentro — inclusive uma no Tianjin Pioneers, o ADVERSÁRIO do
# time em que o tipster apostou. Virou uma aposta no painel.
#
# "aposta N,NN R$" / "prêmio" / "odds totais" são de bet-slip de terceiro:
# o tipster nunca manda valor em dinheiro, só a odd.
_CONVERSA_PATTERN = re.compile(
    r"aposta\s+[\d.,]+\s*R\$|pr[êe]mio\s+[\d.,]+|odds?\s+totais",
    re.IGNORECASE,
)


def parece_print_de_conversa(texto: Optional[str]) -> bool:
    """O texto tem cara de screenshot de conversa com aposta de terceiros?"""
    return bool(texto and _CONVERSA_PATTERN.search(texto))


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
    # Verbos/pronomes de abertura de frase que sobraram. "Tem uma odd 3.10
    # agora rolando no meu grupo..." (04/09/2026) virou jogador1="Tem" e
    # mercado="Tem vencer a partida", e gerou notificação — era anúncio de
    # venda de vaga, não tip.
    "tem", "temos", "teremos", "vai", "vamos", "hoje", "amanha", "amanhã",
    "agora", "ainda", "so", "só", "outra", "outro", "nova", "novo",
    "ultima", "última", "ultimo", "último", "chance", "vaga", "vagas",
}

# Anúncio comercial: venda de vaga/acesso ao grupo, não tip.
#
# Esses textos citam uma odd de verdade ("uma odd 3.10 agora rolando"),
# então o guard de "sem odd não é tip" não pega — e o regex de favorito
# pesca a primeira palavra da frase como nome de jogador. Bug real
# (#128, 04/09/2026): "Tem uma odd 3.10 agora rolando no meu grupo que ao
# meu ver é MUITO DIFÍCIL de furar / Vou fechar as vagas em meia hora /
# Última chance" foi notificada como aposta.
_ANUNCIO_PATTERN = re.compile(
    r"fechar\s+as\s+vagas|"
    r"[úu]ltima\s+chance|"
    r"(?:no|do|meu)\s+meu\s+grupo|"
    r"rolando\s+no\s+meu\s+grupo|"
    r"ap[óo]s\s+o\s+pagamento|"
    r"link\s+pro\s+grupo|"
    r"suporte:",
    re.IGNORECASE,
)


def parece_anuncio(texto: Optional[str]) -> bool:
    """O texto é propaganda de venda de vaga, não uma tip? Ver _ANUNCIO_PATTERN."""
    return bool(texto and _ANUNCIO_PATTERN.search(texto))


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


def qual_jogador_o_tipster_citou(
    texto: str, jogador1: Optional[str], jogador2: Optional[str]
) -> Optional[str]:
    """Dos dois jogadores do confronto, qual o tipster nomeou na legenda?

    Serve pro caso em que o print traz os DOIS jogadores (card de jogo ao
    vivo, por exemplo) e a legenda diz em qual deles a aposta foi feita.
    Sem isso o mercado saía genérico ("Vencedor da partida") ou, pior,
    com o jogador errado — bug real (03/09/2026): a tip
    "Ao vivo! Putinseva odd: 3.30" virou "Qinwen Zheng vencer a partida",
    a adversária, porque na falta de um dono explícito o código pegava o
    primeiro nome do card.

    Usa fuzzy (names_match) de propósito: o tipster escreve o nome de
    memória e erra com frequência — aqui "Putinseva" sem o segundo "t"
    contra "Yulia Putintseva". Exige um vencedor ÚNICO: se o nome citado
    casa com os dois lados, é ambíguo e devolve None em vez de chutar.
    """
    if not jogador1 or not jogador2:
        return None
    citado = find_favorite_only(texto)
    if not citado:
        return None
    casam = [j for j in (jogador1, jogador2) if names_match(citado, j, threshold=80)]
    return casam[0] if len(casam) == 1 else None


def parse_free_text(texto: str) -> ExtractedBet:
    """
    Heurística baseada em regex. Não é infalível — ajuste os padrões acima
    conforme o formato real dos tipsters do seu grupo (veja README, seção
    'Calibrando o extractor').
    """
    texto = texto or ""
    jogador1 = jogador2 = torneio = mercado = None
    odd: Optional[float] = None
    # Calculado aqui (e não só no return) porque a busca dos times de
    # basquete depende dele — ver _find_basketball_teams abaixo.
    esporte_detectado = _detectar_esporte(texto)

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

    # Basquete: nome de franquia não cabe no formato "Nome Sobrenome" que os
    # padrões acima esperam (ver _find_basketball_teams). Só roda quando o
    # esporte já foi detectado como basquete, pra não arriscar casar um
    # confronto de tênis com a lista de sufixos de time.
    if (jogador1 is None or jogador2 is None) and esporte_detectado == Esporte.BASQUETE.value:
        j1, j2 = _find_basketball_teams(texto)
        if j1 and j2:
            jogador1, jogador2 = j1, j2
            # Preserva o mercado: numa aposta de total de pontos ele não
            # depende de time nenhum ("mais de 198.5 pontos"), e sobrescrever
            # com None perderia a única informação útil da tip.

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

    # Dois jogadores no print E a legenda dizendo em qual deles é a aposta
    # (ex: print do card ao vivo + "Ao vivo! Putinseva odd: 3.30"). Sem
    # este ramo o mercado virava genérico ou nomeava a adversária.
    if mercado is None and jogador1 and jogador2:
        citado = qual_jogador_o_tipster_citou(texto_sem_mercado, jogador1, jogador2)
        if citado:
            mercado = f"{citado} vencer a partida"

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
        mercado = _normalizar_handicap(mercado)
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

    # Cinto de segurança: nome de jogador SEM odd não é tip.
    #
    # O mesmo guard já existia em extract_with_gemini, mas faltava aqui —
    # e este parser é quem roda quando o Gemini estoura a cota diária.
    # Bug real (03/09/2026): "Ah, também teremos aquela odd 100 diária."
    # (mensagem de fim de dia do tipster) virou
    # jogador1="também teremos" / mercado="também teremos vencer a
    # partida" e gerou notificação no Telegram. Note que a odd NÃO foi
    # extraída — "odd 100" sem casas decimais não casa nenhum padrão, e é
    # justamente isso que denuncia que o texto é conversa, não aposta.
    #
    # jogador1 vindo de find_favorite_only é heurístico (pega o que estiver
    # antes da odd); sem uma odd de verdade não há o que ancorá-lo, então
    # o certo é descartar em vez de inventar um confronto. Só vale pra
    # texto puro: com imagem, quem manda são os nomes lidos do print.
    if odd is None and jogador1 and not jogador2 and not _MARKET_KEYWORDS.search(texto):
        logger.debug(
            "Texto sem odd e sem mercado explícito (%r) — descartando jogador1=%r "
            "(provável mensagem de conversa, não tip).", texto[:80], jogador1,
        )
        jogador1 = None
        mercado = None

    # Print de CONVERSA (screenshot de chat com bet-slip de terceiros):
    # descarta o confronto inteiro. Ver parece_print_de_conversa — o texto
    # traz valores em dinheiro e apostas de outra pessoa, às vezes no lado
    # OPOSTO do jogo em que o tipster apostou.
    if parece_print_de_conversa(texto) and (jogador1 or jogador2 or mercado):
        logger.info(
            "Texto parece print de conversa (bet-slip de terceiros) — descartando "
            "confronto/mercado extraídos: %r x %r / %r",
            jogador1, jogador2, mercado,
        )
        jogador1 = None
        jogador2 = None
        mercado = None

    # Anúncio de venda de vaga: cita uma odd de verdade, então o guard de
    # "sem odd não é tip" não pega. Ver parece_anuncio.
    if parece_anuncio(texto) and (jogador1 or jogador2 or mercado):
        logger.info(
            "Texto parece anúncio de venda de vaga — descartando confronto/mercado "
            "extraídos: %r x %r / %r", jogador1, jogador2, mercado,
        )
        jogador1 = None
        jogador2 = None
        mercado = None

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
        esporte=esporte_detectado,
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


def _extrair_com_fallbacks(image_path: Optional[str], caption_text: str) -> ExtractedBet:
    """Cadeia de fallback quando o Gemini falha: Groq -> EasyOCR -> texto puro.

    O Groq vem primeiro porque é outro LLM: entende o print como a tip que
    ele é (qual dos dois jogadores é o da aposta, múltipla vs simples), o
    que nem o EasyOCR nem o parser de regex conseguem — eles leem o print
    como texto cru. Ver extract_with_groq.

    Se o Groq não estiver configurado (GROQ_API_KEY vazio) ou também
    falhar, cai no EasyOCR local como antes — nunca levanta pro chamador.
    """
    if settings.GROQ_API_KEY:
        try:
            bet = extract_with_groq(image_path, caption_text)
            if bet.valido or bet.odd is not None:
                logger.info("Groq atendeu no lugar do Gemini.")
                return bet
            logger.warning("Groq respondeu, mas sem dado aproveitável — seguindo pro EasyOCR.")
        except Exception:
            logger.exception("Falha no Groq (backup do Gemini), seguindo pro EasyOCR.")

    # 90s: generoso o bastante pra baixar os modelos na 1ª chamada de um
    # runner novo (~200MB) e ler 1 imagem, mas curto o bastante pra sempre
    # sobrar tempo dentro do timeout-minutes: 4 do workflow — ver docstring
    # de _extract_with_easyocr_and_text.
    return _extract_with_easyocr_and_text(image_path, caption_text, timeout_s=90.0)


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
            logger.exception("Falha ao usar Gemini, tentando os fallbacks.")
            bet = _extrair_com_fallbacks(image_path, caption_text)
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
