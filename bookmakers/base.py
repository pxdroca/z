"""
bookmakers/base.py
===================
Classe base para os adaptadores de casas de apostas. Concentra:

  - Abertura de um navegador Playwright "disfarçado" (stealth), pra reduzir
    a chance de ser barrado como bot logo na entrada (ver `tf-playwright-
    stealth` no requirements.txt). Isso NÃO garante que vai passar por
    proteções mais fortes (Cloudflare/Akamai/PerimeterX) — é best-effort.
  - Navegação com retry + atraso aleatório (comportamento menos robótico,
    e também mais educado com o servidor do outro lado).
  - O contrato comum: `get_link()` sempre tenta o link exato primeiro e,
    se falhar por qualquer motivo, cai para o link aproximado (fallback) —
    nunca lança exceção pro chamador, nunca deixa a aposta sem link algum.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from playwright.sync_api import Browser, Page, sync_playwright

try:
    from playwright_stealth import stealth_sync
except ImportError:  # pacote não instalado — segue sem stealth (degradação graciosa)
    stealth_sync = None

from config import settings

logger = logging.getLogger(__name__)

REALISTIC_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
)


@dataclass
class BookmakerLink:
    nome: str            # nome de exibição, ex: "Superbet"
    url: str
    exato: bool = False  # True = achamos a partida específica; False = link aproximado (torneio/dia)


class BookmakerAdapter:
    """Subclasse isso para cada casa de apostas (veja superbet.py, betano.py, bet365.py)."""

    slug: str = "base"
    display_name: str = "Base"
    base_url: str = ""

    def __init__(self) -> None:
        self.timeout_ms = 25_000

    # ------------------------------------------------------------------
    # Infraestrutura comum (navegador stealth, retry, delay)
    # ------------------------------------------------------------------

    def _new_stealth_page(self, browser: Browser) -> tuple:
        context = browser.new_context(
            user_agent=REALISTIC_USER_AGENT,
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            viewport={"width": 1366, "height": 768},
            extra_http_headers={"Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"},
        )
        page = context.new_page()
        if stealth_sync is not None:
            try:
                stealth_sync(page)
            except Exception:
                logger.debug("%s: stealth_sync falhou, seguindo sem ele.", self.slug)
        return context, page

    def _safe_goto(self, page: Page, url: str, retries: int = 2) -> bool:
        for tentativa in range(retries + 1):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                # espera "humana" antes de interagir/ler a página
                page.wait_for_timeout(random.randint(1200, 2600))
                return True
            except Exception:
                logger.warning("%s: tentativa %d/%d falhou ao abrir %s", self.slug, tentativa + 1, retries + 1, url)
                time.sleep(random.uniform(1.5, 3.0))
        return False

    def _run_with_browser(self, fn):
        """
        Abre um Chromium headless, aplica stealth, chama fn(page) e sempre
        fecha o navegador no final (mesmo se fn levantar exceção).
        """
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=settings.PLAYWRIGHT_HEADLESS)
            try:
                context, page = self._new_stealth_page(browser)
                try:
                    return fn(page)
                finally:
                    context.close()
            finally:
                browser.close()

    # ------------------------------------------------------------------
    # Contrato que cada casa implementa
    # ------------------------------------------------------------------

    def build_fallback_link(self, torneio: Optional[str], data_hora: Optional[datetime]) -> str:
        """Link aproximado (torneio/dia) — sempre deve funcionar, sem depender de scraping."""
        raise NotImplementedError

    def find_exact_link(
        self, jogador1: str, jogador2: str, torneio: Optional[str], data_hora: Optional[datetime]
    ) -> Optional[str]:
        """
        Tenta achar a URL exata da partida nesta casa. Pode devolver None
        (não achou) — implementado por cada subclasse. Qualquer exceção
        aqui dentro é tratada por get_link(), então não precisa se preocupar
        em nunca lançar erro.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Ponto de entrada usado pelo matcher.py
    # ------------------------------------------------------------------

    def get_link(
        self, jogador1: str, jogador2: str, torneio: Optional[str], data_hora: Optional[datetime]
    ) -> BookmakerLink:
        fallback_url = self.build_fallback_link(torneio, data_hora)

        try:
            exato = self.find_exact_link(jogador1, jogador2, torneio, data_hora)
        except Exception:
            logger.exception("%s: erro ao tentar link exato, usando fallback.", self.slug)
            exato = None

        if exato:
            return BookmakerLink(nome=self.display_name, url=exato, exato=True)

        logger.info("%s: usando link aproximado (torneio/dia) para %s x %s", self.slug, jogador1, jogador2)
        return BookmakerLink(nome=self.display_name, url=fallback_url, exato=False)
