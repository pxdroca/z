"""
nameutils.py
============
Utilitários de comparação de nomes de tenistas, compartilhados por
sofascore_client.py e pelos adaptadores em bookmakers/. Extraído para um
módulo próprio porque tanto o cliente do SofaScore quanto cada casa de
apostas precisam da mesma lógica de "esse nome bate com aquele?".
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz
from unidecode import unidecode


def normalize_name(name: str) -> str:
    """Remove acentos, pontuação e caixa para comparar nomes com segurança."""
    name = unidecode(name or "").lower().strip()
    name = re.sub(r"[^a-z\s]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def names_match(a: str, b: str, threshold: int) -> bool:
    """
    Compara nomes de tenistas com fuzzy-matching, pois o OCR raramente
    extrai o nome idêntico ao cadastrado (acentos, sobrenome incompleto,
    abreviações como "N. Djokovic" vs "Novak Djokovic" etc).
    """
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return False
    score = max(
        fuzz.token_sort_ratio(na, nb),
        fuzz.partial_ratio(na, nb),
    )
    return score >= threshold


def pair_matches(j1: str, j2: str, c1: str, c2: str, threshold: int) -> bool:
    """Compara um par (j1, j2) contra um par candidato (c1, c2), em qualquer ordem."""
    direto = names_match(j1, c1, threshold) and names_match(j2, c2, threshold)
    invertido = names_match(j1, c2, threshold) and names_match(j2, c1, threshold)
    return direto or invertido
