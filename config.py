"""
config.py
=========
Ponto único de leitura das variáveis de ambiente (.env). Todos os outros
módulos importam as constantes deste arquivo em vez de chamar os.getenv()
espalhado pelo código — assim, se um nome de variável mudar, só se ajusta
aqui.

Uso:
    from config import settings
    print(settings.DB_PATH)
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
    TELEGRAM_SOURCE_CHAT: str = field(default_factory=lambda: os.getenv("TELEGRAM_SOURCE_CHAT", ""))

    # --- Telegram Bot API (notifier) ---
    TELEGRAM_BOT_TOKEN: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    TELEGRAM_BOT_CHAT_ID: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_CHAT_ID", ""))

    # --- OCR / extractor ---
    OCR_ENGINE: str = field(default_factory=lambda: os.getenv("OCR_ENGINE", "easyocr").lower())
    GEMINI_API_KEY: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    GEMINI_MODEL: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))

    # --- Banco de dados / mídia ---
    DB_PATH: str = field(default_factory=lambda: os.getenv("DB_PATH", "data/apostas.db"))
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
        default_factory=lambda: os.getenv("SUPERBET_BASE_URL", "https://superbet.bet.br/apostas/tenis")
    )
    BETANO_BASE_URL: str = field(default_factory=lambda: os.getenv("BETANO_BASE_URL", "https://betano.bet.br"))
    # Confirmado ao vivo em 31/08/2026 (ver bookmakers/betano.py) — é de
    # fato a seção de tênis (título "Apostas Tênis"), sem bloqueio.
    BETANO_TENNIS_PATH: str = field(default_factory=lambda: os.getenv("BETANO_TENNIS_PATH", "/sport/tenis/"))
    BET365_BASE_URL: str = field(default_factory=lambda: os.getenv("BET365_BASE_URL", "https://www.bet365.bet.br"))
    # Confirmado ao vivo, mas é uma rota com hash interno (#/AS/B13/K^5/) —
    # sabidamente instável entre sessões/contas (ver aviso em bookmakers/bet365.py).
    BET365_TENNIS_PATH: str = field(
        default_factory=lambda: os.getenv("BET365_TENNIS_PATH", "/?lng=33#/AS/B13/K%5E5/")
    )

    # --- Diversos ---
    LOG_LEVEL: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())
    TIMEZONE: str = field(default_factory=lambda: os.getenv("TIMEZONE", "America/Cuiaba"))

    def resolve_path(self, relative: str) -> Path:
        """Resolve um caminho relativo (ex: DB_PATH) em relação à raiz do projeto."""
        p = Path(relative)
        return p if p.is_absolute() else (BASE_DIR / p)


settings = Settings()

# Garante que as pastas usadas pelo projeto existam.
settings.resolve_path(settings.MEDIA_DIR).mkdir(parents=True, exist_ok=True)
settings.resolve_path(settings.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
