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

/**
 * Série de valores para os sparklines dos cards de estatística — dados
 * REAIS derivados das apostas já carregadas (nada sintético), na ordem
 * cronológica em que foram resolvidas.
 *
 * Só apostas já decididas entram (green/cashout/red): pendentes e void
 * não movem o resultado, então incluí-las achataria a curva com pontos
 * repetidos sem significado.
 */
export interface SeriesSparkline {
  /** Saldo acumulado de unidades ao longo do tempo. */
  unidades: number[];
  /** Nº acumulado de greens (inclui cashout, como no resto das contas). */
  green: number[];
  /** Nº acumulado de reds. */
  red: number[];
  /** Taxa de acerto acumulada (%) após cada aposta decidida. */
  taxaAcerto: number[];
  /** Odd de cada aposta decidida, na ordem — mostra a dispersão das odds. */
  odds: number[];
}

export function calcularSeries(apostas: Bet[]): SeriesSparkline {
  const decididas = apostas
    .filter((b) => b.resultado === "green" || b.resultado === "cashout" || b.resultado === "red")
    .slice()
    .sort((a, b) => {
      const ta = a.data_hora ? new Date(a.data_hora).getTime() : 0;
      const tb = b.data_hora ? new Date(b.data_hora).getTime() : 0;
      return ta - tb;
    });

  const unidades: number[] = [];
  const green: number[] = [];
  const red: number[] = [];
  const taxaAcerto: number[] = [];
  const odds: number[] = [];

  let saldo = 0;
  let nGreen = 0;
  let nRed = 0;

  for (const bet of decididas) {
    const ehGreen = bet.resultado === "green" || bet.resultado === "cashout";
    if (ehGreen) {
      nGreen += 1;
      if (bet.odd !== null) saldo += bet.unidades * (bet.odd - 1);
    } else {
      nRed += 1;
      saldo -= bet.unidades;
    }
    unidades.push(saldo);
    green.push(nGreen);
    red.push(nRed);
    taxaAcerto.push((nGreen / (nGreen + nRed)) * 100);
    if (bet.odd !== null) odds.push(bet.odd);
  }

  return { unidades, green, red, taxaAcerto, odds };
}
