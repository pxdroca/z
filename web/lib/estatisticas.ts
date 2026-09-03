// Port de _calcular_estatisticas() em app.py — mesma lógica, mesmos números.
import type { Bet } from "./types";

export interface Estatisticas {
  green: number;
  red: number;
  void: number;
  pendente: number;
  taxaAcerto: number | null;
  unidadesLiquidas: number;
  roi: number | null;
}

export function calcularEstatisticas(apostas: Bet[]): Estatisticas {
  // Cashout entra junto com green (lucro pela odd cheia) por simplicidade —
  // mesma decisão consciente do app.py original (ver comentário lá).
  const green = apostas.filter((b) => b.resultado === "green" || b.resultado === "cashout");
  const red = apostas.filter((b) => b.resultado === "red");
  const voidBets = apostas.filter((b) => b.resultado === "void");
  const pendente = apostas.filter((b) => b.resultado === "pendente");

  const decididas = green.length + red.length;
  const taxaAcerto = decididas > 0 ? (green.length / decididas) * 100 : null;

  const lucroGreen = green.reduce((soma, b) => (b.odd !== null ? soma + b.unidades * (b.odd - 1) : soma), 0);
  const perdaRed = red.reduce((soma, b) => soma + b.unidades, 0);
  const unidadesLiquidas = lucroGreen - perdaRed;

  const unidadesApostadas = green.reduce((s, b) => s + b.unidades, 0) + red.reduce((s, b) => s + b.unidades, 0);
  const roi = unidadesApostadas > 0 ? (unidadesLiquidas / unidadesApostadas) * 100 : null;

  return {
    green: green.length,
    red: red.length,
    void: voidBets.length,
    pendente: pendente.length,
    taxaAcerto,
    unidadesLiquidas,
    roi,
  };
}

export function calcularOddMedia(apostas: Bet[]): string {
  const comOdd = apostas.filter((b) => b.odd !== null && b.odd > 0);
  if (comOdd.length === 0) return "—";
  const soma = comOdd.reduce((s, b) => s + (b.odd as number), 0);
  return (soma / comOdd.length).toFixed(2);
}
