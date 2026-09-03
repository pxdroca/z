// Cliente Postgres compartilhado — singleton em escopo de módulo, reaproveitado
// entre invocações "quentes" da função serverless da Vercel (padrão recomendado
// pra evitar abrir uma conexão nova a cada request).
//
// Usa a connection string COM POOLER do Supabase (porta 6543, PgBouncer em modo
// transaction) via DATABASE_URL_POOLED — variável de ambiente própria desta
// app, separada da DATABASE_URL usada pelos workflows do GitHub Actions
// (listener.py/score_updater.py), que continua na conexão direta (porta 5432)
// sem nenhuma mudança. Funções serverless podem invocar concorrentemente
// (múltiplos usuários/abas ao mesmo tempo), o que esgotaria rápido o limite de
// conexões diretas do Postgres — o pooler existe exatamente para isso.
import { Pool } from "pg";

declare global {
  var _pgPool: Pool | undefined;
}

function createPool(): Pool {
  const connectionString = process.env.DATABASE_URL_POOLED;
  if (!connectionString) {
    throw new Error(
      "DATABASE_URL_POOLED não configurado. Use a connection string com pooler do Supabase " +
        "(painel Supabase -> Settings -> Database -> Connection pooling, porta 6543)."
    );
  }
  return new Pool({ connectionString, ssl: { rejectUnauthorized: false } });
}

export function getPool(): Pool {
  if (!global._pgPool) {
    global._pgPool = createPool();
  }
  return global._pgPool;
}
