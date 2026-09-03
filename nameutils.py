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


def search_variants(nome: str) -> list[str]:
    """Variações de um nome pra tentar em busca textual, da mais específica
    pra mais genérica e sem repetir.

    Motivo (03/09/2026): o tipster escreveu "Alexandra muller" onde o
    jogador é "Alexandre Muller". A busca da Superbet é textual e não
    tolera o erro — "Alexandra muller" devolve 0 resultados —, mas só o
    sobrenome ("Muller") devolve o confronto certo. Buscar o nome inteiro
    e, se falhar, cair pro sobrenome recupera o erro de digitação sem
    afrouxar o limiar de comparação, que é o que causaria falso positivo.

    Só o sobrenome, nunca só o primeiro nome: testando "Alexandra muller",
    a variante "alexandra" trouxe "Alexandra Eala" e o fuzzy aceitou (o
    primeiro nome bate inteiro) — jogadora errada, confronto errado. Em
    tênis o sobrenome é o que discrimina; primeiro nome sozinho é ruído.
    """
    nome = (nome or "").strip()
    if not nome:
        return []

    partes = [p for p in normalize_name(nome).split() if len(p) > 2]
    variantes = [nome]
    if len(partes) > 1:
        variantes.append(partes[-1])   # sobrenome

    vistos: set[str] = set()
    unicas = []
    for v in variantes:
        chave = normalize_name(v)
        if chave and chave not in vistos:
            vistos.add(chave)
            unicas.append(v)
    return unicas
