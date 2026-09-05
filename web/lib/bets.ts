// Port de database.py — mesmas queries SQL (mesma tabela `bets`, já criada e
// mantida pelo lado Python via init_db()/psycopg; esta app NUNCA cria/altera
// o schema, só lê e faz 2 updates estreitos).
import { getPool } from "./db";
import { checarResultadoDeBet } from "./resultadoChecker";
import type { Bet, BetStatus, ResultadoAposta } from "./types";

interface BetRow {
  id: number;
  jogador1: string;
  jogador2: string;
  torneio: string | null;
  mercado: string | null;
  odd: number | null;
  data_hora: Date | null;
  links_json: string | null;
  status: string;
  fonte_texto: string | null;
  mensagem_id: number | null;
  criado_em: Date;
  sofascore_event_id: number | null;
  placar_final: string | null;
  vencedor_partida: string | null;
  unidades: number | null;
  resultado: string | null;
  tipo_aposta: string | null;
  selecoes_json: string | null;
  esporte: string | null;
}

// Port de models.Bet.jogo (property) — mesma lógica: múltipla mostra as
// seleções (ou o fallback), simples mostra "jogador1 vs jogador2".
function computeJogo(bet: Omit<Bet, "jogo">): string {
  if (bet.tipo_aposta === "multipla") {
    if (bet.selecoes.length > 0) return bet.selecoes.join(", ");
    return bet.jogador1 || "Múltipla (detalhes no print original)";
  }
  return `${bet.jogador1} vs ${bet.jogador2}`;
}

function rowToBet(row: BetRow): Bet {
  let links: Record<string, Bet["links"][string]> = {};
  try {
    links = row.links_json ? JSON.parse(row.links_json) : {};
  } catch {
    links = {};
  }
  let selecoes: string[] = [];
  try {
    selecoes = row.selecoes_json ? JSON.parse(row.selecoes_json) : [];
  } catch {
    selecoes = [];
  }

  const semJogo: Omit<Bet, "jogo"> = {
    id: row.id,
    jogador1: row.jogador1,
    jogador2: row.jogador2,
    torneio: row.torneio,
    mercado: row.mercado,
    odd: row.odd,
    data_hora: row.data_hora ? row.data_hora.toISOString() : null,
    links,
    status: row.status as BetStatus,
    fonte_texto: row.fonte_texto ?? "",
    mensagem_id: row.mensagem_id,
    criado_em: row.criado_em.toISOString(),
    sofascore_event_id: row.sofascore_event_id,
    placar_final: row.placar_final,
    vencedor_partida: row.vencedor_partida,
    unidades: row.unidades ?? 1.0,
    resultado: (row.resultado ?? "pendente") as ResultadoAposta,
    tipo_aposta: (row.tipo_aposta ?? "simples") as Bet["tipo_aposta"],
    selecoes,
    esporte: (row.esporte ?? "tenis") as Bet["esporte"],
  };
  return { ...semJogo, jogo: computeJogo(semJogo) };
}

export interface ListBetsFiltro {
  status?: BetStatus[];
  dateFrom?: string; // 'YYYY-MM-DD'
  dateTo?: string; // 'YYYY-MM-DD'
}

/**
 * Port de list_bets() — mas filtrando por vários status numa query só
 * (status IN (...)) em vez de N queries deduplicadas no cliente, como o
 * app.py fazia (ver plano de migração). data_hora NULL sempre passa no
 * filtro de data, mesmo comportamento do original.
 */
export async function listBets(filtro: ListBetsFiltro): Promise<Bet[]> {
  const pool = getPool();
  const condicoes: string[] = ["1=1"];
  const params: unknown[] = [];

  if (filtro.status && filtro.status.length > 0) {
    params.push(filtro.status);
    condicoes.push(`status = ANY($${params.length}::text[])`);
  }
  if (filtro.dateFrom) {
    params.push(filtro.dateFrom);
    condicoes.push(`(data_hora IS NULL OR date(data_hora) >= date($${params.length}))`);
  }
  if (filtro.dateTo) {
    params.push(filtro.dateTo);
    condicoes.push(`(data_hora IS NULL OR date(data_hora) <= date($${params.length}))`);
  }

  const query = `SELECT * FROM bets WHERE ${condicoes.join(" AND ")} ORDER BY data_hora ASC NULLS LAST`;
  const { rows } = await pool.query<BetRow>(query, params);
  return rows.map(rowToBet);
}

export async function getBet(id: number): Promise<Bet | null> {
  const pool = getPool();
  const { rows } = await pool.query<BetRow>("SELECT * FROM bets WHERE id = $1", [id]);
  return rows[0] ? rowToBet(rows[0]) : null;
}

/** Campos aceitos ao criar uma aposta manualmente pelo painel. */
export interface NovaBetManual {
  jogador1: string;
  jogador2: string;
  mercado: string | null;
  odd: number | null;
  unidades: number;
  esporte: Bet["esporte"];
  torneio: string | null;
  data_hora: string | null;
  status: BetStatus;
  resultado: ResultadoAposta;
}

/**
 * Insere uma aposta criada à mão no painel (rota POST /api/bets).
 *
 * Existe porque o pipeline automático não cobre todo jogo: o SofaScore só
 * expõe os torneios que aparecem no endpoint de jogos do dia, e ligas fora
 * dele (ex: NBL Blitz, a pré-temporada australiana de basquete — caso real
 * de 03/09/2026) nunca são confirmadas, deixando a aposta presa em
 * "nao_encontrada" sem nenhuma forma de corrigir pela interface. Antes
 * disso, a única escrita possível pelo painel era editar o resultado.
 *
 * mensagem_id fica NULL de propósito: é a marca de "não veio do Telegram".
 * A coluna é UNIQUE, mas o Postgres permite vários NULL num índice único,
 * então várias apostas manuais coexistem sem conflito — e a idempotência
 * do listener (que casa por mensagem_id) continua intacta.
 */
export async function insertBetManual(dados: NovaBetManual): Promise<Bet> {
  const { rows } = await getPool().query<BetRow>(
    `INSERT INTO bets (jogador1, jogador2, torneio, mercado, odd, data_hora,
                       links_json, status, fonte_texto, mensagem_id,
                       unidades, resultado, tipo_aposta, esporte)
     VALUES ($1, $2, $3, $4, $5, $6, '{}', $7, $8, NULL, $9, $10, 'simples', $11)
     RETURNING *`,
    [
      dados.jogador1,
      dados.jogador2,
      dados.torneio,
      dados.mercado,
      dados.odd,
      dados.data_hora,
      dados.status,
      "Aposta lançada manualmente no painel.",
      dados.unidades,
      dados.resultado,
      dados.esporte,
    ]
  );
  return rowToBet(rows[0]);
}

/** Campos que o painel deixa editar numa aposta já existente.
 *
 *  Fora daqui de propósito: `id`, `mensagem_id`/`chat_id` (a identidade da
 *  mensagem do Telegram, que garante a idempotência do listener),
 *  `criado_em` e `fonte_texto` — mexer neles quebraria o rastro de como a
 *  aposta chegou, sem nenhum ganho pra quem corrige um erro na tela. */
export interface CamposEditaveis {
  jogador1?: string;
  /** NOT NULL no banco — vazio é "", nunca null (ver a rota PATCH). */
  jogador2?: string;
  torneio?: string | null;
  mercado?: string | null;
  odd?: number | null;
  unidades?: number;
  data_hora?: string | null;
  esporte?: string;
  status?: BetStatus;
  resultado?: ResultadoAposta;
  placar_final?: string | null;
  vencedor_partida?: string | null;
  sofascore_event_id?: number | null;
}

/** Colunas aceitas no UPDATE, para montar a query só com o que veio. */
const COLUNAS_EDITAVEIS = [
  "jogador1", "jogador2", "torneio", "mercado", "odd", "unidades",
  "data_hora", "esporte", "status", "resultado", "placar_final",
  "vencedor_partida", "sofascore_event_id",
] as const;

/**
 * Atualiza qualquer subconjunto dos campos editáveis de uma aposta.
 *
 * Existe porque o painel só permitia mexer em status e resultado — para
 * corrigir um confronto errado, um horário ou uma odd era preciso ir ao
 * banco à mão. Com isto, quem vê o erro na tela conserta ali mesmo.
 *
 * Monta SET dinâmico, mas SEMPRE com placeholders numerados e com o nome
 * da coluna vindo de COLUNAS_EDITAVEIS (lista fixa no código) — nada do
 * que o cliente manda entra na string SQL.
 */
export async function updateBetCampos(id: number, campos: CamposEditaveis): Promise<void> {
  const sets: string[] = [];
  const valores: unknown[] = [];

  for (const coluna of COLUNAS_EDITAVEIS) {
    const valor = (campos as Record<string, unknown>)[coluna];
    if (valor === undefined) continue;   // não enviado = não muda
    valores.push(valor);
    sets.push(`${coluna} = $${valores.length}`);
  }

  if (sets.length === 0) return;

  valores.push(id);
  await getPool().query(
    `UPDATE bets SET ${sets.join(", ")} WHERE id = $${valores.length}`,
    valores
  );
}

/** Port de update_status() — usado pela rota PATCH /api/bets/:id. */
export async function updateStatus(id: number, status: BetStatus): Promise<void> {
  await getPool().query("UPDATE bets SET status = $1 WHERE id = $2", [status, id]);
}

/** Port de update_resultado() — usado pela rota PATCH /api/bets/:id e pelo auto-conferir. */
export async function updateResultado(id: number, resultado: ResultadoAposta): Promise<void> {
  await getPool().query("UPDATE bets SET resultado = $1 WHERE id = $2", [resultado, id]);
}

/**
 * Port de auto_promote_ao_vivo() — chamado no início de todo GET /api/bets,
 * mesma posição/efeito que tinha no app.py (rodava a cada rerun do Streamlit).
 */
export async function autoPromoteAoVivo(): Promise<number> {
  const { rowCount } = await getPool().query(
    `UPDATE bets SET status = 'ao_vivo' WHERE status = 'agendada' AND data_hora IS NOT NULL AND data_hora <= now()`
  );
  return rowCount ?? 0;
}

/**
 * Port de app.py::_autoconferir_resultado — roda depois do SELECT, só nas
 * bets já retornadas com status=encerrada e resultado=pendente (mesmo
 * escopo "por render" do original, não a tabela inteira). Muta o array
 * recebido in-place (resultado) para o response já sair com o valor
 * corrigido, sem precisar de um segundo round-trip ao banco.
 */
export async function autoConferirResultados(bets: Bet[]): Promise<void> {
  for (const bet of bets) {
    if (bet.status === "encerrada" && bet.resultado === "pendente") {
      const resultado = checarResultadoDeBet(bet);
      if (resultado) {
        await updateResultado(bet.id, resultado);
        bet.resultado = resultado;
      }
    }
  }
}
