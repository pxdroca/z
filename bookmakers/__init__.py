"""
bookmakers/
===========
Cada casa de apostas (Superbet, Betano, bet365...) tem seu próprio módulo
aqui dentro, implementando `BookmakerAdapter` (definido em `base.py`).

Isso existe porque, ao investigar, ficou claro que não existe UM site que
já agrega odds + link direto das 3 casas de forma confiável e sem bloqueio
anti-bot (testamos: SofaScore mostra odds só de um parceiro fixo, o
OddsAgora bloqueia navegador automatizado, a Betano devolve 403 até em
requisição simples). Então a estratégia é: cada casa tem seu próprio
adaptador, que tenta achar o link EXATO da partida com um navegador headless
"disfarçado" (stealth) e, se não conseguir, cai automaticamente para um link
"aproximado" (a página do torneio/dia na própria casa) — o pipeline nunca
quebra, na pior das hipóteses você toca 1-2 vezes a mais dentro do app.

⚠️ Aviso importante: tentar contornar proteções anti-bot pode violar os
Termos de Uso das casas de apostas. Use por sua conta e risco, para uso
pessoal, com moderação (não hospede isso rodando em loop agressivo nem em
alta frequência) — veja o README, seção "Scraping e Termos de Uso".
"""

from .base import BookmakerAdapter, BookmakerLink
from .bet365 import Bet365Adapter
from .betano import BetanoAdapter
from .superbet import SuperbetAdapter

REGISTRY: dict[str, type[BookmakerAdapter]] = {
    "superbet": SuperbetAdapter,
    "betano": BetanoAdapter,
    "bet365": Bet365Adapter,
}

__all__ = ["BookmakerAdapter", "BookmakerLink", "REGISTRY"]
