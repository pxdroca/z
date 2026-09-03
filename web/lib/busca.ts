// Busca client-side sobre as apostas já carregadas — não altera a query
// do servidor (a rota GET /api/bets segue igual, filtrando só por status
// e período). Reaproveita normalizeName de nameutils pra ignorar acentos
// e caixa, mesma normalização já usada no matching de nomes do pipeline.
import { normalizeName } from "./nameutils";
import type { Bet } from "./types";

/** Campos onde a busca procura — os textos que o usuário reconhece num card. */
function textoBuscavel(bet: Bet): string {
  return [bet.jogo, bet.jogador1, bet.jogador2, bet.torneio, bet.mercado].filter(Boolean).join(" ");
}

export function filtrarPorBusca(apostas: Bet[], termo: string): Bet[] {
  const alvo = normalizeName(termo);
  if (!alvo) return apostas;
  // Todos os termos precisam aparecer (busca "E", não "OU") — digitar
  // "paolini set" acha só o que tem os dois, não tudo que tem "set".
  const termos = alvo.split(" ").filter(Boolean);
  return apostas.filter((bet) => {
    const texto = normalizeName(textoBuscavel(bet));
    return termos.every((t) => texto.includes(t));
  });
}
