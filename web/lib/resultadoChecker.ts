// Port de resultado_checker.py — mesma cobertura de mercados (vencedor da
// partida, vencer o Nº set, vencer um set genérico, vencer sem perder set
// para tênis; moneyline, handicap de pontos e total de pontos para
// basquete), mesmos regex, mesma lógica de decisão. Ver o original para o
// porquê de cada padrão e o que fica deliberadamente de fora (aces, games,
// dupla falta, placar exato, tie-break, mercados de jogador individual).

import { namesMatch } from "./nameutils";
import type { Bet, Esporte, ResultadoAposta } from "./types";

const THRESHOLD_NOME = 80;

const PADRAO_PARTIDA = /^(.+?)\s+(?:vencer a partida|vencedor da partida|ganhar)$/i;
const PADRAO_SET_ESPECIFICO = /^(.+?)\s+vencer o (\d)º set$/i;
const PADRAO_SET_GENERICO = /^(.+?)\s+vencer um set$/i;
const PADRAO_SEM_PERDER_SET = /^(.+?)\s+vencer sem perder set(?:s)?$/i;

// Basquete: "Lakers -5.5" / "Handicap Celtics +3.5" / "Handicap: Lakers -5.5"
const PADRAO_HANDICAP = /^(?:handicap\s*(?:de pontos)?\s*[:\-]?\s*)?(.+?)\s+([+-]\s?\d+(?:[.,]\d+)?)$/i;
// Basquete: "Mais de 215.5 pontos" / "Menos de 210.5" / "Over 215.5" / "Under 210.5 pontos"
const PADRAO_TOTAL_PONTOS = /^(mais|menos|over|under)\s+de\s+(\d+(?:[.,]\d+)?)\s*(?:pontos?)?$/i;

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
  evt: EventStatusLike,
  esporte: Esporte = "tenis"
): ResultadoAposta | null {
  if (!mercado || evt.status !== "finished") return null;
  const m0 = mercado.trim();

  if (esporte === "basquete") return checarResultadoBasquete(m0, jogador1, jogador2, evt);
  return checarResultadoTenis(m0, jogador1, jogador2, evt);
}

/** Vencedor da partida — mesmo texto de mercado e mesma lógica para tênis e
 * basquete (compara com evt.vencedor, agnóstico de esporte). */
function checarMoneyline(
  mercado: string,
  jogador1: string | null,
  jogador2: string | null,
  evt: EventStatusLike
): ResultadoAposta | null {
  const m = mercado.match(PADRAO_PARTIDA);
  if (!m) return null;
  const lado = nomeBate(m[1].trim(), jogador1, jogador2);
  if (lado === null || evt.vencedor === null) return null;
  const venceu = (lado === "jogador1" && evt.vencedor === "home") || (lado === "jogador2" && evt.vencedor === "away");
  return venceu ? "green" : "red";
}

function checarResultadoTenis(
  m0: string,
  jogador1: string | null,
  jogador2: string | null,
  evt: EventStatusLike
): ResultadoAposta | null {
  if (PADRAO_PARTIDA.test(m0)) {
    return checarMoneyline(m0, jogador1, jogador2, evt);
  }

  let m = m0.match(PADRAO_SET_ESPECIFICO);
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

/** Cobre moneyline, handicap de pontos por time e total de pontos
 * (over/under). Mercados de jogador individual (pontos/rebotes/
 * assistências) não são cobertos — evt.sets só tem placar por período, sem
 * estatística de jogador — caem em null (pendente, conferência manual). */
function checarResultadoBasquete(
  m0: string,
  jogador1: string | null,
  jogador2: string | null,
  evt: EventStatusLike
): ResultadoAposta | null {
  if (PADRAO_PARTIDA.test(m0)) {
    return checarMoneyline(m0, jogador1, jogador2, evt);
  }

  if (evt.sets.length === 0) return null;

  let m = m0.match(PADRAO_HANDICAP);
  if (m) {
    const lado = nomeBate(m[1].trim(), jogador1, jogador2);
    if (lado === null) return null;
    const linha = Number(m[2].replace(/\s/g, "").replace(",", "."));
    if (Number.isNaN(linha)) return null;
    const pontosJ1 = evt.sets.reduce((soma, [h]) => soma + h, 0);
    const pontosJ2 = evt.sets.reduce((soma, [, a]) => soma + a, 0);
    const [pontosLado, pontosAdversario] = lado === "jogador1" ? [pontosJ1, pontosJ2] : [pontosJ2, pontosJ1];
    const margemAjustada = pontosLado - pontosAdversario + linha;
    if (margemAjustada === 0) return "void"; // push: só acontece com handicap inteiro
    return margemAjustada > 0 ? "green" : "red";
  }

  m = m0.match(PADRAO_TOTAL_PONTOS);
  if (m) {
    const direcao = m[1].trim().toLowerCase();
    const linha = Number(m[2].replace(",", "."));
    if (Number.isNaN(linha)) return null;
    const total = evt.sets.reduce((soma, [h, a]) => soma + h + a, 0);
    if (total === linha) return "void"; // push: só acontece com linha inteira
    const acima = total > linha;
    const querMais = direcao === "mais" || direcao === "over";
    const venceu = querMais ? acima : !acima;
    return venceu ? "green" : "red";
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
  return checarResultado(bet.mercado, bet.jogador1, bet.jogador2, evt, bet.esporte ?? "tenis");
}
