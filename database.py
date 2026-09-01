"""
database.py
===========
Camada de acesso ao SQLite. Único módulo que executa SQL — listener.py,
app.py, matcher.py etc. só chamam estas funções.

SQLite foi escolhido porque é gratuito, não precisa de servidor, e é lido
tanto pelo processo do listener (que escreve) quanto pelo Streamlit (que lê),
bastando compartilhar o arquivo .db. Usamos WAL mode para permitir leitura
concorrente enquanto o listener grava.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator, Optional

from config import settings
from models import Bet, BetStatus, ResultadoAposta

logger = logging.getLogger(__name__)

DB_PATH = str(settings.resolve_path(settings.DB_PATH))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bets (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    jogador1             TEXT NOT NULL,
    jogador2             TEXT NOT NULL,
    torneio              TEXT,
    mercado              TEXT,
    odd                  REAL,
    data_hora            TEXT,                 -- ISO 8601, pode ser NULL até o matcher achar o jogo
    links_json           TEXT DEFAULT '{}',    -- JSON: {slug: {"nome":..,"url":..,"exato":bool}} por casa de apostas
    status               TEXT NOT NULL DEFAULT 'nao_encontrada',
    fonte_texto          TEXT DEFAULT '',
    mensagem_id          INTEGER UNIQUE,        -- id da msg do Telegram, evita processar 2x
    criado_em            TEXT NOT NULL DEFAULT (datetime('now')),
    sofascore_event_id   INTEGER,               -- usado por score_updater.py para acompanhar o placar
    placar_final         TEXT,                  -- ex: "6-4, 6-3", preenchido quando a partida termina
    vencedor_partida     TEXT,                  -- nome do jogador vencedor, preenchido junto com placar_final
    unidades             REAL DEFAULT 1.0,      -- stake em unidades — fixo em 1.0 (tipster não indica stake)
    resultado            TEXT DEFAULT 'pendente' -- se A APOSTA ganhou: pendente/green/red/void (manual, no painel)
);

CREATE INDEX IF NOT EXISTS idx_bets_status ON bets(status);
CREATE INDEX IF NOT EXISTS idx_bets_data_hora ON bets(data_hora);
"""

# Colunas adicionadas depois da v1 do schema — para bancos .db já existentes
# (criados antes desta mudança), _ensure_columns() faz o ALTER TABLE
# idempotente na inicialização, já que CREATE TABLE IF NOT EXISTS não altera
# uma tabela que já existe.
_EXTRA_COLUMNS = {
    "sofascore_event_id": "INTEGER",
    "placar_final": "TEXT",
    "vencedor_partida": "TEXT",
    "unidades": "REAL DEFAULT 1.0",
    "resultado": "TEXT DEFAULT 'pendente'",
}


def _ensure_columns(conn: sqlite3.Connection) -> None:
    existentes = {row["name"] for row in conn.execute("PRAGMA table_info(bets)")}
    for coluna, tipo in _EXTRA_COLUMNS.items():
        if coluna not in existentes:
            conn.execute(f"ALTER TABLE bets ADD COLUMN {coluna} {tipo}")
            logger.info("Coluna '%s' adicionada à tabela bets.", coluna)


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Cria a tabela (idempotente). Chame uma vez no início de cada processo."""
    with get_connection() as conn:
        conn.executescript(_SCHEMA)
        _ensure_columns(conn)
    logger.info("Banco de dados pronto em %s", DB_PATH)


def _row_to_bet(row: sqlite3.Row) -> Bet:
    data_hora = None
    if row["data_hora"]:
        try:
            data_hora = datetime.fromisoformat(row["data_hora"])
        except ValueError:
            data_hora = None
    criado_em = None
    if row["criado_em"]:
        try:
            criado_em = datetime.fromisoformat(row["criado_em"])
        except ValueError:
            criado_em = None
    try:
        links = json.loads(row["links_json"]) if row["links_json"] else {}
    except (json.JSONDecodeError, TypeError):
        links = {}
    return Bet(
        id=row["id"],
        jogador1=row["jogador1"],
        jogador2=row["jogador2"],
        torneio=row["torneio"],
        mercado=row["mercado"],
        odd=row["odd"],
        data_hora=data_hora,
        links=links,
        status=row["status"],
        fonte_texto=row["fonte_texto"] or "",
        mensagem_id=row["mensagem_id"],
        criado_em=criado_em,
        sofascore_event_id=row["sofascore_event_id"],
        placar_final=row["placar_final"],
        vencedor_partida=row["vencedor_partida"],
        unidades=row["unidades"] if row["unidades"] is not None else 1.0,
        resultado=row["resultado"] or ResultadoAposta.PENDENTE.value,
    )


def bet_exists_for_message(mensagem_id: int) -> bool:
    """Evita reprocessar a mesma mensagem do Telegram (ex: se o listener reiniciar)."""
    with get_connection() as conn:
        cur = conn.execute("SELECT 1 FROM bets WHERE mensagem_id = ?", (mensagem_id,))
        return cur.fetchone() is not None


def insert_bet(bet: Bet) -> int:
    """Insere uma nova aposta e devolve o id gerado."""
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO bets (jogador1, jogador2, torneio, mercado, odd,
                               data_hora, links_json, status, fonte_texto, mensagem_id,
                               sofascore_event_id, unidades, resultado)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bet.jogador1,
                bet.jogador2,
                bet.torneio,
                bet.mercado,
                bet.odd,
                bet.data_hora.isoformat() if bet.data_hora else None,
                json.dumps(bet.links, ensure_ascii=False),
                bet.status,
                bet.fonte_texto,
                bet.mensagem_id,
                bet.sofascore_event_id,
                bet.unidades,
                bet.resultado,
            ),
        )
        new_id = cur.lastrowid
    logger.info("Aposta #%s salva: %s vs %s", new_id, bet.jogador1, bet.jogador2)
    return int(new_id)


def update_match_info(bet_id: int, data_hora: Optional[datetime], links: dict, status: str) -> None:
    """Chamado pelo listener.py depois do matcher.py achar (ou não) o confronto e os links."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE bets SET data_hora = ?, links_json = ?, status = ? WHERE id = ?",
            (data_hora.isoformat() if data_hora else None, json.dumps(links, ensure_ascii=False), status, bet_id),
        )


def update_status(bet_id: int, status: str) -> None:
    """Usado pelo app.py quando o usuário marca uma aposta como encerrada/ganha/perdida etc."""
    with get_connection() as conn:
        conn.execute("UPDATE bets SET status = ? WHERE id = ?", (status, bet_id))


def update_resultado(bet_id: int, resultado: str) -> None:
    """Usado pelo app.py quando o usuário marca se A APOSTA (não o jogo) ganhou —
    pendente/green/red/void. Independente de `status`, que é sobre o jogo."""
    with get_connection() as conn:
        conn.execute("UPDATE bets SET resultado = ? WHERE id = ?", (resultado, bet_id))


def get_bet(bet_id: int) -> Optional[Bet]:
    with get_connection() as conn:
        cur = conn.execute("SELECT * FROM bets WHERE id = ?", (bet_id,))
        row = cur.fetchone()
        return _row_to_bet(row) if row else None


def list_bets(
    status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    order_by: str = "data_hora ASC",
) -> list[Bet]:
    """
    Lista apostas com filtros opcionais — usado pelo app.py.

    status:      um valor de BetStatus, ou None para todos.
    date_from/date_to: strings 'YYYY-MM-DD', filtram por data_hora. Apostas
                  sem data_hora (ex: erro de extração, confronto não
                  encontrado) não têm data pra filtrar, então sempre
                  aparecem — não devem ser escondidas por um filtro que não
                  se aplica a elas.
    """
    query = "SELECT * FROM bets WHERE 1=1"
    params: list = []

    if status:
        query += " AND status = ?"
        params.append(status)
    if date_from:
        query += " AND (data_hora IS NULL OR date(data_hora) >= date(?))"
        params.append(date_from)
    if date_to:
        query += " AND (data_hora IS NULL OR date(data_hora) <= date(?))"
        params.append(date_to)

    query += f" ORDER BY {order_by}"

    with get_connection() as conn:
        cur = conn.execute(query, params)
        rows = cur.fetchall()

    return [_row_to_bet(r) for r in rows]


def auto_promote_ao_vivo() -> int:
    """
    Marca como 'ao_vivo' toda aposta 'agendada' cujo horário já passou.
    Chamado periodicamente pelo app.py para manter o status atualizado
    sem depender de um worker separado.
    """
    with get_connection() as conn:
        cur = conn.execute(
            """
            UPDATE bets
               SET status = ?
             WHERE status = ?
               AND data_hora IS NOT NULL
               AND datetime(data_hora) <= datetime('now')
            """,
            (BetStatus.AO_VIVO.value, BetStatus.AGENDADA.value),
        )
        return cur.rowcount


def list_trackable_bets() -> list[Bet]:
    """
    Apostas que score_updater.py deve consultar no SofaScore: ainda não
    encerradas e com um sofascore_event_id conhecido (sem isso não há o que
    consultar).
    """
    with get_connection() as conn:
        cur = conn.execute(
            """
            SELECT * FROM bets
             WHERE status IN (?, ?)
               AND sofascore_event_id IS NOT NULL
            """,
            (BetStatus.AGENDADA.value, BetStatus.AO_VIVO.value),
        )
        rows = cur.fetchall()
    return [_row_to_bet(r) for r in rows]


def update_score_result(bet_id: int, status: str, placar_final: Optional[str] = None, vencedor_partida: Optional[str] = None) -> None:
    """Usado por score_updater.py para promover o status (ex: agendada -> ao_vivo)
    e, quando a partida termina, gravar o placar final e o vencedor junto."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE bets SET status = ?, placar_final = ?, vencedor_partida = ? WHERE id = ?",
            (status, placar_final, vencedor_partida, bet_id),
        )
