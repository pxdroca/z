// Port de resultado_checker.py — mesma cobertura de mercados (vencedor da
// partida, vencer o Nº set, vencer um set genérico, vencer sem perder set),
// mesmos regex, mesma lógica de decisão. Ver o original para o porquê de
// cada padrão e o que fica deliberadamente de fora (aces, games, dupla
// falta, placar exato, tie-break).

import { namesMatch } from "./nameutils";
import type { Bet, ResultadoAposta } from "./types";

const THRESHOLD_NOME = 80;

const PADRAO_PARTIDA = /^(.+?)\s+(?:vencer a partida|vencedor da partida|ganhar)$/i;
const PADRAO_SET_ESPECIFICO = /^(.+?)\s+vencer o (\d)º set$/i;
const PADRAO_SET_GENERICO = /^(.+?)\s+vencer um set$/i;
const PADRAO_SEM_PERDER_SET = /^(.+?)\s+vencer sem perder set(?:s)?$/i;

type Lado = "jogador1" | "jogador2";

function nomeBate(citado: string, jogador1: string | null, jogador2: string | null): Lado | null {
  if (jogador1 && namesMatch(citado, jogador1, THRESHOLD_NOME)) return "jogador1";
  if (jogador2 && namesMatch(citado, jogador2, THRESHOLD_NOME)) return "jogador2";
  return null;
}

function parsePlacarFinal(placarFinal: string | null): [number, number][] {
  if (!placarFinal) return [];
  const sets: [number, number][] = [];
  for (const parte of placarFinal.split(",")) {
    const m = parte.match(/\s*(\d+)\s*-\s*(\d+)\s*/);
    if (m) sets.push([Number(m[1]), Number(m[2])]);
  }
  return sets;
}

interface EventStatusLike {
  status: "finished";
  vencedor: "home" | "away" | null;
  sets: [number, number][];
}

function checarResultado(
  mercado: string | null,
  jogador1: string | null,
  jogador2: string | null,
  evt: EventStatusLike
): ResultadoAposta | null {
  if (!mercado || evt.status !== "finished") return null;
  const m0 = mercado.trim();

  let m = m0.match(PADRAO_PARTIDA);
  if (m) {
    const lado = nomeBate(m[1].trim(), jogador1, jogador2);
    if (lado === null || evt.vencedor === null) return null;
    const venceu = (lado === "jogador1" && evt.vencedor === "home") || (lado === "jogador2" && evt.vencedor === "away");
    return venceu ? "green" : "red";
  }

  m = m0.match(PADRAO_SET_ESPECIFICO);
  if (m) {
    const lado = nomeBate(m[1].trim(), jogador1, jogador2);
    const indiceSet = Number(m[2]) - 1;
    if (lado === null || indiceSet < 0 || indiceSet >= evt.sets.length) return null;
    const [gamesJ1, gamesJ2] = evt.sets[indiceSet];
    if (gamesJ1 === gamesJ2) return null; // nunca deveria empatar um set de tênis, mas por segurança
    const venceu = (lado === "jogador1" && gamesJ1 > gamesJ2) || (lado === "jogador2" && gamesJ2 > gamesJ1);
    return venceu ? "green" : "red";
  }

  m = m0.match(PADRAO_SET_GENERICO);
  if (m) {
    const lado = nomeBate(m[1].trim(), jogador1, jogador2);
    if (lado === null || evt.sets.length === 0) return null;
    const ganhouAlgumSet = evt.sets.some(([gamesJ1, gamesJ2]) =>
      lado === "jogador1" ? gamesJ1 > gamesJ2 : gamesJ2 > gamesJ1
    );
    return ganhouAlgumSet ? "green" : "red";
  }

  m = m0.match(PADRAO_SEM_PERDER_SET);
  if (m) {
    const lado = nomeBate(m[1].trim(), jogador1, jogador2);
    if (lado === null || evt.vencedor === null || evt.sets.length === 0) return null;
    const venceuPartida = (lado === "jogador1" && evt.vencedor === "home") || (lado === "jogador2" && evt.vencedor === "away");
    if (!venceuPartida) return "red";
    const perdeuAlgumSet = evt.sets.some(([gamesJ1, gamesJ2]) =>
      lado === "jogador1" ? gamesJ1 < gamesJ2 : gamesJ2 < gamesJ1
    );
    return perdeuAlgumSet ? "red" : "green";
  }

  return null;
}

/**
 * Mesma interpretação de checarResultado(), mas a partir de uma Bet já
 * persistida (placar_final/vencedor_partida como string) — usado pela rota
 * GET /api/bets (auto-conferir), que não tem acesso ao EventStatus ao vivo
 * do score_updater.py, só o que já foi salvo no banco.
 */
export function checarResultadoDeBet(bet: Bet): ResultadoAposta | null {
  if (!bet.placar_final || !bet.vencedor_partida || bet.status !== "encerrada") return null;
  const sets = parsePlacarFinal(bet.placar_final);
  const vencedorLado = nomeBate(bet.vencedor_partida, bet.jogador1, bet.jogador2);
  if (vencedorLado === null) return null;
  const evt: EventStatusLike = {
    status: "finished",
    vencedor: vencedorLado === "jogador1" ? "home" : "away",
    sets,
  };
  return checarResultado(bet.mercado, bet.jogador1, bet.jogador2, evt);
}
