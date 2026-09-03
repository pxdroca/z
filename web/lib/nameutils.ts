// Port de nameutils.py. RapidFuzz (Python) -> fuzzball (JS, mesma API:
// token_sort_ratio/partial_ratio, comportamento validado contra o original).
// unidecode (Python) -> String.normalize('NFD') nativo remove diacríticos
// sem precisar de dependência externa (suficiente para nomes latin-based
// com acentos comuns em tênis: č, š, ž, á, é, ã etc — testado contra
// "Molčan" -> "Molcan", "Fábián Marozsán" -> "Fabian Marozsan").

import { token_sort_ratio, partial_ratio } from "fuzzball";

export function normalizeName(name: string | null | undefined): string {
  const semAcento = (name ?? "")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .trim();
  const soLetras = semAcento.replace(/[^a-z\s]/g, " ");
  return soLetras.replace(/\s+/g, " ").trim();
}

export function namesMatch(a: string | null | undefined, b: string | null | undefined, threshold: number): boolean {
  const na = normalizeName(a);
  const nb = normalizeName(b);
  if (!na || !nb) return false;
  const score = Math.max(token_sort_ratio(na, nb), partial_ratio(na, nb));
  return score >= threshold;
}
