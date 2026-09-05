// Agrupamento das apostas na tela. Decisão do usuário: nem só por status
// da partida, nem só por resultado da aposta — uma mistura dos dois.
//
// Motivo (nas palavras dele): "encerrada não é útil (pq ou é green, red ou
// void), agendada = pendente (prefiro pendente)... o único que manteria
// seria o ao vivo, pra separar as pendentes (jogos que não começaram) dos
// jogos que já estão rolando".
//
// Então:
//   AO VIVO   -> partida rolando agora (status da PARTIDA), independente
//                do resultado ainda não decidido
//   GREEN     -> resultado green ou cashout (mesmo par usado nas contas)
//   RED       -> resultado red
//   PENDENTES -> ainda sem resultado e não está ao vivo (agendada, ou
//                encerrada que o pipeline ainda não conferiu)
//   VOID      -> resultado void
//
// Nenhum dado é filtrado aqui: toda aposta cai em exatamente um grupo.
import type { Bet } from "./types";

// Ordem de exibição no painel: primeiro o que ainda está em jogo (ao vivo,
// depois pendente — o que exige atenção agora), e só então o que já foi
// decidido (green, red, void). Antes green/red vinham antes de pendentes,
// o que empurrava as apostas ainda abertas pro meio da tela.
export const GRUPOS = ["ao_vivo", "pendentes", "green", "red", "void"] as const;
export type GrupoId = (typeof GRUPOS)[number];

export const GRUPO_LABEL: Record<GrupoId, string> = {
  ao_vivo: "Ao vivo",
  green: "Green",
  red: "Red",
  pendentes: "Pendentes",
  void: "Void",
};

/** Mensagem mostrada quando o grupo está vazio (Pendentes e Void seguem
 *  visíveis mesmo vazios — pedido do usuário). */
export const GRUPO_VAZIO: Record<GrupoId, string> = {
  ao_vivo: "Nenhuma aposta ao vivo no momento.",
  green: "Nenhuma aposta green no momento.",
  red: "Nenhuma aposta red no momento.",
  pendentes: "Nenhuma aposta pendente no momento.",
  void: "Nenhuma aposta void no momento.",
};

export function grupoDaBet(bet: Bet): GrupoId {
  if (bet.status === "ao_vivo") return "ao_vivo";
  if (bet.resultado === "green" || bet.resultado === "cashout") return "green";
  if (bet.resultado === "red") return "red";
  if (bet.resultado === "void") return "void";
  return "pendentes";
}

/** Grupos sempre renderizados, mesmo vazios (os outros somem se não têm
 *  nenhuma aposta, pra não poluir a tela com seções mortas). */
const SEMPRE_VISIVEIS: GrupoId[] = ["pendentes", "void"];

export interface Grupo {
  id: GrupoId;
  apostas: Bet[];
}

/**
 * Agrupa mantendo a ordem de GRUPOS (ao vivo primeiro — o que exige
 * atenção agora), e dentro de cada grupo a ordem cronológica que a lista
 * recebida já traz.
 */
export function agruparBets(apostas: Bet[]): Grupo[] {
  const mapa = new Map<GrupoId, Bet[]>();
  for (const g of GRUPOS) mapa.set(g, []);
  for (const bet of apostas) mapa.get(grupoDaBet(bet))!.push(bet);

  return GRUPOS.map((id) => ({ id, apostas: mapa.get(id)! })).filter(
    (g) => g.apostas.length > 0 || SEMPRE_VISIVEIS.includes(g.id)
  );
}

/** Contagem por grupo — usada nas tabs do segmented control. */
export function contarPorGrupo(apostas: Bet[]): Record<GrupoId, number> {
  const contagem = { ao_vivo: 0, green: 0, red: 0, pendentes: 0, void: 0 };
  for (const bet of apostas) contagem[grupoDaBet(bet)] += 1;
  return contagem;
}
