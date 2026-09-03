// Port de models.py — mesmos valores de enum, mesmos nomes de campo (snake_case,
// batendo com as colunas do Postgres) para o port do database.py em bets.ts
// não precisar de nenhuma camada de tradução.

export const BET_STATUS_VALUES = [
  "nao_encontrada",
  "agendada",
  "ao_vivo",
  "encerrada",
  "erro_extracao",
] as const;
export type BetStatus = (typeof BET_STATUS_VALUES)[number];

/**
 * Status que aparecem no painel.
 *
 * "erro_extracao" fica de fora: são mensagens do grupo que não eram tip
 * nenhuma (comentário do tipster, link, "Ao vivo!", "Apostas em aberto
 * estão fixadas"). O listener as grava com jogador "?" só pra auditoria e
 * pra não reprocessar a mesma mensagem — não informam nada ao usuário e
 * só poluíam a tela. Continuam no banco, apenas não são listadas.
 */
export const STATUS_VISIVEIS: BetStatus[] = ["nao_encontrada", "agendada", "ao_vivo", "encerrada"];

export const RESULTADO_APOSTA_VALUES = [
  "pendente",
  "green",
  "red",
  "void",
  "cashout",
] as const;
export type ResultadoAposta = (typeof RESULTADO_APOSTA_VALUES)[number];

export const TIPO_APOSTA_VALUES = ["simples", "multipla"] as const;
export type TipoAposta = (typeof TIPO_APOSTA_VALUES)[number];

export const ESPORTE_VALUES = ["tenis", "basquete"] as const;
export type Esporte = (typeof ESPORTE_VALUES)[number];

export interface BookmakerLink {
  nome: string;
  url: string;
  exato: boolean;
}

export interface Bet {
  id: number;
  jogador1: string;
  jogador2: string;
  torneio: string | null;
  mercado: string | null;
  odd: number | null;
  data_hora: string | null; // ISO string (timestamptz)
  links: Record<string, BookmakerLink>;
  status: BetStatus;
  fonte_texto: string | null;
  mensagem_id: number | null;
  criado_em: string;
  sofascore_event_id: number | null;
  placar_final: string | null;
  vencedor_partida: string | null;
  unidades: number;
  resultado: ResultadoAposta;
  tipo_aposta: TipoAposta;
  selecoes: string[];
  esporte: Esporte;
  // Computado no servidor (port de Bet.jogo em models.py) — nunca persistido.
  jogo: string;
}
