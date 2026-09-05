"""
config.py
=========
Ponto único de leitura das variáveis de ambiente (.env). Todos os outros
módulos importam as constantes deste arquivo em vez de chamar os.getenv()
espalhado pelo código — assim, se um nome de variável mudar, só se ajusta
aqui.

Uso:
    from config import settings
    print(settings.DATABASE_URL)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Carrega o .env que estiver na raiz do projeto (não sobrescreve variáveis
# que já existam no ambiente, ex: em produção/Docker).
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _get_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _get_url(name: str, default: str) -> str:
    """Lê uma URL do ambiente já sem espaço em branco nas pontas.

    Um `\\r` sobrando (arquivo .env salvo com fim de linha do Windows, ou
    valor colado com quebra) entra no valor e vai colado na URL até o
    painel — caso real: o link da Betano gravado como
    "https://betano.bet.br/sport/tenis/\\r\\n" nas apostas de 04/09/2026.
    """
    val = os.getenv(name)
    return default if val is None else (val.strip() or default)


def _get_int(name: str, default: int | None) -> int | None:
    val = os.getenv(name)
    if val is None or val == "":
        return default
    try:
        return int(val)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # --- Telegram userbot (Telethon) ---
    TELEGRAM_API_ID: int = field(default_factory=lambda: _get_int("TELEGRAM_API_ID", 0))
    TELEGRAM_API_HASH: str = field(default_factory=lambda: os.getenv("TELEGRAM_API_HASH", ""))
    TELEGRAM_SESSION_NAME: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_SESSION_NAME", "tennis_monitor_session")
    )
    # Sessão em produção (GitHub Actions): StringSession gerada uma vez,
    # localmente, via generate_session_string.py. Se vazia, listener.py usa
    # o arquivo .session local (TELEGRAM_SESSION_NAME) — modo de uso local.
    TELEGRAM_SESSION_STRING: str = field(default_factory=lambda: os.getenv("TELEGRAM_SESSION_STRING", ""))
    TELEGRAM_SOURCE_CHAT: str = field(default_factory=lambda: os.getenv("TELEGRAM_SOURCE_CHAT", ""))

    # --- Telegram Bot API (notifier) ---
    TELEGRAM_BOT_TOKEN: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    TELEGRAM_BOT_CHAT_ID: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_CHAT_ID", ""))

    # --- OCR / extractor ---
    OCR_ENGINE: str = field(default_factory=lambda: os.getenv("OCR_ENGINE", "easyocr").lower())
    GEMINI_API_KEY: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    # gemini-2.5-flash foi descontinuado para novas API keys (erro 404 "no
    # longer available to new users" — confirmado ao vivo em 02/09/2026).
    # gemini-flash-latest sempre aponta pro modelo estável mais recente da
    # linha "flash", evitando esse tipo de quebra silenciosa de novo no
    # futuro (o pipeline cai pro parser de regex sem avisar quando o Gemini
    # falha — ver extract_bet_info em extractor.py).
    GEMINI_MODEL: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-flash-latest"))

    # --- Groq: 2º LLM, backup do Gemini (ver extractor.extract_with_groq) ---
    # O Gemini cai com 503 ("high demand") em rajadas, e isso é do MODELO,
    # não da conta — uma segunda chave Google não cobriria. Provedor
    # separado falha de forma independente. Vazio = sem backup (a cadeia
    # segue Gemini -> EasyOCR -> texto puro, como antes).
    GROQ_API_KEY: str = field(default_factory=lambda: os.getenv("GROQ_API_KEY", ""))
    # Qwen 3.8 27B: dos modelos que a conta grátis lista, é o único com
    # VISÃO confirmada ao vivo (04/09/2026) — testado com um print de tip
    # real, descreveu a imagem corretamente. Os demais disponíveis
    # (groq/compound, openai/gpt-oss-*) rejeitam conteúdo multimodal com
    # "messages[0].content must be a string".
    #
    # Se a Groq mudar o catálogo, GET /openai/v1/models lista o que a
    # conta enxerga — não confie em nome de modelo sem checar lá.
    GROQ_MODEL: str = field(
        default_factory=lambda: (os.getenv("GROQ_MODEL", "").strip() or "qwen/qwen3.8-27b")
    )
    # Curto de propósito: é um fallback dentro do ciclo de 5 min do
    # workflow, e o EasyOCR ainda vem depois se isto também falhar.
    GROQ_TIMEOUT_S: int = field(default_factory=lambda: _get_int("GROQ_TIMEOUT_S", 45) or 45)

    # --- OpenRouter: backup do Gemini que cobre PRINT ----------------------
    # O Groq não serve pra imagem: o tier grátis dele limita a 7.000 tokens
    # de ENTRADA por minuto e um print de tip sozinho passa disso. O
    # OpenRouter não tem teto de tokens/minuto no free tier — são 20
    # requisições/minuto e 50/dia, sem cartão — e os modelos gratuitos com
    # visão têm contexto de 262k a 1M tokens (o maior print do histórico
    # custa ~47k). Chave em https://openrouter.ai/keys
    OPENROUTER_API_KEY: str = field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY", "").strip())
    # Gemma 4 31B: modelo de visão do Google servido de graça aqui, 262k de
    # contexto. O catálogo gratuito do OpenRouter ROTACIONA (modelos viram
    # pagos e outros entram), então se este sumir, GET /api/v1/models lista
    # o que está livre — procure os ids terminados em ":free" que aceitam
    # "image" em input_modalities.
    OPENROUTER_MODEL: str = field(
        default_factory=lambda: (os.getenv("OPENROUTER_MODEL", "").strip() or "google/gemma-4-31b-it:free")
    )
    # Mais folgado que o do Groq: modelo gratuito costuma ter fila, e aqui
    # ele é a última chance antes do EasyOCR.
    OPENROUTER_TIMEOUT_S: int = field(default_factory=lambda: _get_int("OPENROUTER_TIMEOUT_S", 70) or 70)

    # --- Banco de dados / mídia ---
    # Connection string do Postgres (Supabase) — usada tanto local quanto
    # em produção (GitHub Actions). Obrigatória: database.py não tem mais
    # fallback SQLite. O painel Next.js tem a sua própria (com pooler).
    DATABASE_URL: str = field(default_factory=lambda: os.getenv("DATABASE_URL", ""))
    MEDIA_DIR: str = field(default_factory=lambda: os.getenv("MEDIA_DIR", "media"))

    # --- Matcher: fonte do confronto oficial (SofaScore) ---
    SUPERBET_FUZZY_THRESHOLD: int = field(default_factory=lambda: _get_int("SUPERBET_FUZZY_THRESHOLD", 80) or 80)
    PLAYWRIGHT_HEADLESS: bool = field(default_factory=lambda: _get_bool("PLAYWRIGHT_HEADLESS", True))

    # --- Casas de apostas habilitadas (bookmakers/) ---
    BOOKMAKERS: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            b.strip().lower() for b in os.getenv("BOOKMAKERS", "superbet,betano,bet365").split(",") if b.strip()
        )
    )

    # Confirmadas ao vivo (celular, rede no Brasil) em 31/08/2026 — ver
    # README "Calibrando os adaptadores". Domínio/path real é .bet.br, não
    # .com como a pesquisa inicial (feita de fora do Brasil) tinha sugerido.
    SUPERBET_BASE_URL: str = field(
        default_factory=lambda: _get_url("SUPERBET_BASE_URL", "https://superbet.bet.br/apostas/tenis")
    )
    # Confirmado ao vivo em 03/09/2026: GET retorna 200, título "Apostar em
    # Basquete Online | Odds Basquetebol | Superbet", com cards de evento
    # reais (mesmos seletores e2e-event-* da listagem de tênis).
    SUPERBET_BASKETBALL_URL: str = field(
        default_factory=lambda: _get_url("SUPERBET_BASKETBALL_URL", "https://superbet.bet.br/apostas/basquete")
    )
    BETANO_BASE_URL: str = field(default_factory=lambda: _get_url("BETANO_BASE_URL", "https://betano.bet.br"))
    # Confirmado ao vivo em 31/08/2026 (ver bookmakers/betano.py) — é de
    # fato a seção de tênis (título "Apostas Tênis"), sem bloqueio.
    BETANO_TENNIS_PATH: str = field(default_factory=lambda: _get_url("BETANO_TENNIS_PATH", "/sport/tenis/"))
    # TODO calibrar ao vivo: nunca confirmado contra o site real (mesmo aviso
    # do path de tênis acima, mas sem nenhuma validação ainda).
    BETANO_BASKETBALL_PATH: str = field(
        default_factory=lambda: _get_url("BETANO_BASKETBALL_PATH", "/sport/basquetebol/")
    )
    BET365_BASE_URL: str = field(default_factory=lambda: _get_url("BET365_BASE_URL", "https://www.bet365.bet.br"))
    # Confirmado ao vivo, mas é uma rota com hash interno (#/AS/B13/K^5/) —
    # sabidamente instável entre sessões/contas (ver aviso em bookmakers/bet365.py).
    BET365_TENNIS_PATH: str = field(
        default_factory=lambda: _get_url("BET365_TENNIS_PATH", "/?lng=33#/AS/B13/K%5E5/")
    )
    # TODO calibrar ao vivo: hash interno de basquete não confirmado (mesmo
    # aviso de instabilidade do path de tênis acima, e sem validação nenhuma).
    BET365_BASKETBALL_PATH: str = field(
        default_factory=lambda: _get_url("BET365_BASKETBALL_PATH", "/?lng=33#/AS/B18/")
    )

    # --- Diversos ---
    LOG_LEVEL: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())
    TIMEZONE: str = field(default_factory=lambda: os.getenv("TIMEZONE", "America/Cuiaba"))

    def resolve_path(self, relative: str) -> Path:
        """Resolve um caminho relativo (ex: MEDIA_DIR) em relação à raiz do projeto."""
        p = Path(relative)
        return p if p.is_absolute() else (BASE_DIR / p)


settings = Settings()

# Garante que as pastas usadas pelo projeto existam — importante também em
# produção (GitHub Actions), onde o runner é uma checkout limpa a cada
# execução e "logs/" não existe até alguém criar (listener.py/score_updater.py
# esperam que já exista antes de configurar o FileHandler do logging).
settings.resolve_path(settings.MEDIA_DIR).mkdir(parents=True, exist_ok=True)
settings.resolve_path("logs").mkdir(parents=True, exist_ok=True)
