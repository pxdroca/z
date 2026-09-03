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
