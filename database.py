"""
database.py
===========
Camada de acesso ao Postgres (Neon). Único módulo que executa SQL —
listener.py, matcher.py etc. só chamam estas funções.

Postgres (hoje Supabase) foi escolhido na migração pra nuvem gratuita porque
persiste entre execuções (diferente de storage efêmero de PaaS grátis) e é
acessível tanto pelos workflows do GitHub Actions (que escrevem, via conexão
direta na 5432) quanto pelo painel Next.js na Vercel (que lê/edita, via
pooler na 6543) — bastando compartilhar a connection string.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator, Optional

import psycopg
from psycopg.rows import dict_row

from config import settings
from models import Bet, BetStatus, ResultadoAposta

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bets (
    id                   SERIAL PRIMARY KEY,
    jogador1             TEXT NOT NULL,
    jogador2             TEXT NOT NULL,
    torneio              TEXT,
    mercado              TEXT,
    odd                  REAL,
    data_hora            TIMESTAMPTZ,           -- pode ser NULL até o matcher achar o jogo
    links_json           TEXT DEFAULT '{}',     -- JSON: {slug: {"nome":..,"url":..,"exato":bool}} por casa de apostas
    status               TEXT NOT NULL DEFAULT 'nao_encontrada',
    fonte_texto          TEXT DEFAULT '',
    mensagem_id          BIGINT,                 -- id da msg do Telegram (único POR CHAT, ver idx_bets_chat_mensagem)
    chat_id              BIGINT,                 -- chat do Telegram de origem — o grupo é recriado todo dia
    criado_em            TIMESTAMPTZ NOT NULL DEFAULT now(),
    sofascore_event_id   BIGINT,                 -- usado por score_updater.py para acompanhar o placar
    placar_final         TEXT,                   -- ex: "6-4, 6-3", preenchido quando a partida termina
    vencedor_partida     TEXT,                   -- nome do jogador vencedor, preenchido junto com placar_final
    unidades             REAL DEFAULT 1.0,       -- stake em unidades — fixo em 1.0 (tipster não indica stake)
    resultado            TEXT DEFAULT 'pendente' -- se A APOSTA ganhou: pendente/green/red/void (manual, no painel)
);

CREATE INDEX IF NOT EXISTS idx_bets_status ON bets(status);
CREATE INDEX IF NOT EXISTS idx_bets_data_hora ON bets(data_hora);

-- Estado pequeno e persistente entre execuções do listener no GitHub
-- Actions (o runner não tem disco persistente entre runs) — hoje só guarda
-- o último message.id do Telegram já processado, pra saber de onde
-- continuar o poll na próxima execução.
CREATE TABLE IF NOT EXISTS sync_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# Colunas adicionadas depois da v1 do schema — para bancos já existentes,
# _ensure_columns() faz o ALTER TABLE idempotente na inicialização, já que
# CREATE TABLE IF NOT EXISTS não altera uma tabela que já existe.
_EXTRA_COLUMNS = {
    "sofascore_event_id": "BIGINT",
    "placar_final": "TEXT",
    "vencedor_partida": "TEXT",
    "unidades": "REAL DEFAULT 1.0",
    "resultado": "TEXT DEFAULT 'pendente'",
    # tipo_aposta: 'simples' (1 confronto, o caso original) ou 'multipla'
    # (várias seleções combinadas numa odd só — ver extractor.py). Quando
    # multipla, jogador1/jogador2 viram só um resumo textual (não há um
    # confronto único pra confirmar no SofaScore/casas de apostas) e a
    # lista real de seleções fica em selecoes_json.
    "tipo_aposta": "TEXT DEFAULT 'simples'",
    "selecoes_json": "TEXT",
    # esporte: 'tenis' ou 'basquete' (ver models.Esporte) — determina qual
    # lógica de matching/conferência de resultado se aplica. Default 'tenis'
    # cobre as linhas existentes, todas anteriores ao suporte a basquete.
    "esporte": "TEXT DEFAULT 'tenis'",
    # chat_id: ver models.Bet.chat_id e _migrar_unicidade_da_mensagem.
    "chat_id": "BIGINT",
}


def _migrar_unicidade_da_mensagem(conn: psycopg.Connection) -> None:
    """Troca a unicidade GLOBAL de `mensagem_id` pela unicidade por CHAT.

    Bug real que isto corrige: o grupo de tips é recriado todo dia, e cada
    grupo novo é um chat do Telegram diferente, com numeração de mensagens
    própria (reinicia perto de 1) — o mesmo motivo que já obrigou a
    guardar o `last_message_id` do listener por chat (ver
    listener.poll_new_messages). Com `mensagem_id BIGINT UNIQUE` global, a
    mensagem 98 do grupo de HOJE era considerada "já processada" só porque
    a mensagem 98 do grupo de ONTEM já estava no banco — e como o pipeline
    grava uma linha para praticamente toda mensagem do grupo (as tips viram
    apostas, o resto vira linha `erro_extracao` de auditoria), no dia
    seguinte quase toda tip nova era engolida em silêncio (log de debug).

    Idempotente: pode rodar em todo init_db().
    """
    # 1. Remove a constraint UNIQUE antiga (só a que é exatamente
    #    (mensagem_id) — não mexe em nenhuma outra). O nome é gerado pelo
    #    Postgres, então é descoberto pelo catálogo em vez de chutado.
    cur = conn.execute(
        """
        SELECT tc.constraint_name
          FROM information_schema.table_constraints tc
          JOIN information_schema.key_column_usage kcu
            ON tc.constraint_name = kcu.constraint_name
           AND tc.table_schema = kcu.table_schema
         WHERE tc.table_name = 'bets'
           AND tc.constraint_type = 'UNIQUE'
         GROUP BY tc.constraint_name
        HAVING COUNT(*) = 1 AND MIN(kcu.column_name) = 'mensagem_id'
        """
    )
    for row in cur.fetchall():
        nome = row["constraint_name"]
        conn.execute(f'ALTER TABLE bets DROP CONSTRAINT "{nome}"')
        logger.info("Unicidade global de mensagem_id removida (constraint %s).", nome)

    # 2. Cria a unicidade correta: (chat_id, mensagem_id). No Postgres,
    #    NULLs são distintos entre si num índice único, então as linhas
    #    antigas (chat_id NULL) e as apostas manuais (mensagem_id NULL) não
    #    conflitam entre si — que é o comportamento desejado.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_bets_chat_mensagem ON bets(chat_id, mensagem_id)"
    )


def _ensure_columns(conn: psycopg.Connection) -> None:
    cur = conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'bets'"
    )
    existentes = {row["column_name"] for row in cur.fetchall()}
    for coluna, tipo in _EXTRA_COLUMNS.items():
        if coluna not in existentes:
            conn.execute(f"ALTER TABLE bets ADD COLUMN {coluna} {tipo}")
            logger.info("Coluna '%s' adicionada à tabela bets.", coluna)


@contextmanager
def get_connection() -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(settings.DATABASE_URL, row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Cria as tabelas (idempotente). Chame uma vez no início de cada processo."""
    with get_connection() as conn:
        conn.execute(_SCHEMA)
        _ensure_columns(conn)
        # Depois de _ensure_columns: a migração usa a coluna chat_id, que
        # em bancos antigos só existe a partir dali.
        _migrar_unicidade_da_mensagem(conn)
    logger.info("Banco de dados pronto (Postgres/Neon)")


def _row_to_bet(row: dict) -> Bet:
    data_hora = row["data_hora"]  # já vem como datetime (com tz) do psycopg
    criado_em = row["criado_em"]
    try:
        links = json.loads(row["links_json"]) if row["links_json"] else {}
    except (json.JSONDecodeError, TypeError):
        links = {}
    try:
        selecoes = json.loads(row["selecoes_json"]) if row.get("selecoes_json") else []
    except (json.JSONDecodeError, TypeError):
        selecoes = []
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
        chat_id=row.get("chat_id"),
        criado_em=criado_em,
        sofascore_event_id=row["sofascore_event_id"],
        placar_final=row["placar_final"],
        vencedor_partida=row["vencedor_partida"],
        unidades=row["unidades"] if row["unidades"] is not None else 1.0,
        resultado=row["resultado"] or ResultadoAposta.PENDENTE.value,
        tipo_aposta=row.get("tipo_aposta") or "simples",
        selecoes=selecoes,
        esporte=row.get("esporte") or "tenis",
    )


def bet_exists_for_message(mensagem_id: int, chat_id: Optional[int] = None) -> bool:
    """Evita reprocessar a mesma mensagem do Telegram (ex: se o listener
    reiniciar, ou num backfill que se sobreponha ao poll).

    A comparação é SEMPRE por (chat_id, mensagem_id): o id da mensagem só é
    único dentro de um chat, e o grupo de tips é recriado todo dia — ver
    _migrar_unicidade_da_mensagem para o bug que isso corrige.
    `IS NOT DISTINCT FROM` trata NULL = NULL (linhas antigas, anteriores à
    coluna chat_id, e execuções locais sem chat conhecido)."""
    with get_connection() as conn:
        cur = conn.execute(
            "SELECT 1 FROM bets WHERE mensagem_id = %s AND chat_id IS NOT DISTINCT FROM %s",
            (mensagem_id, chat_id),
        )
        return cur.fetchone() is not None


def insert_bet(bet: Bet) -> int:
    """Insere uma nova aposta e devolve o id gerado."""
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO bets (jogador1, jogador2, torneio, mercado, odd,
                               data_hora, links_json, status, fonte_texto, mensagem_id, chat_id,
                               sofascore_event_id, unidades, resultado, tipo_aposta, selecoes_json,
                               esporte)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
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
                bet.chat_id,
                bet.sofascore_event_id,
                bet.unidades,
                bet.resultado,
                bet.tipo_aposta,
                json.dumps(bet.selecoes, ensure_ascii=False) if bet.selecoes else None,
                bet.esporte,
            ),
        )
        new_id = cur.fetchone()["id"]
    logger.info("Aposta #%s salva: %s vs %s", new_id, bet.jogador1, bet.jogador2)
    return int(new_id)


def update_match_info(
    bet_id: int,
    data_hora: Optional[datetime],
    links: dict,
    status: str,
    sofascore_event_id: Optional[int] = None,
    jogador1: Optional[str] = None,
    jogador2: Optional[str] = None,
    torneio: Optional[str] = None,
) -> None:
    """Chamado pelo listener.py (achou o confronto na 1ª tentativa) e por
    score_updater.py::_retentar_bet_nao_encontrada (achou no retry) depois
    do matcher.py confirmar (ou não) o confronto e os links.

    sofascore_event_id: bug real corrigido aqui — score_updater.py retentava
    apostas "não encontrada" e atualizava data_hora/links/status com sucesso,
    mas nunca persistia o event_id do match encontrado no retry. Sem isso, a
    aposta nunca aparece em list_trackable_bets() (exige sofascore_event_id
    IS NOT NULL) e fica "ao_vivo"/"agendada" para sempre, sem o
    score_updater.py nunca mais conseguir consultar o placar/resultado dela
    — encontrada uma vez, nunca mais rastreada depois.

    jogador1/jogador2/torneio: opcionais, só passados pelo retry (o
    listener.py na 1ª tentativa já grava isso via insert_bet) — sem eles o
    retry deixava os nomes crus do OCR/tipster (ex: "Michael zheng", "?")
    em vez dos nomes oficiais do SofaScore, mesmo já tendo confirmado o
    confronto certo."""
    with get_connection() as conn:
        if jogador1 is not None:
            conn.execute(
                """UPDATE bets
                      SET data_hora = %s, links_json = %s, status = %s, sofascore_event_id = %s,
                          jogador1 = %s, jogador2 = %s, torneio = COALESCE(%s, torneio)
                    WHERE id = %s""",
                (
                    data_hora.isoformat() if data_hora else None,
                    json.dumps(links, ensure_ascii=False),
                    status,
                    sofascore_event_id,
                    jogador1,
                    jogador2 or "?",
                    torneio,
                    bet_id,
                ),
            )
        else:
            conn.execute(
                "UPDATE bets SET data_hora = %s, links_json = %s, status = %s, sofascore_event_id = %s WHERE id = %s",
                (
                    data_hora.isoformat() if data_hora else None,
                    json.dumps(links, ensure_ascii=False),
                    status,
                    sofascore_event_id,
                    bet_id,
                ),
            )


def update_status(bet_id: int, status: str) -> None:
    """Usado pelo app.py quando o usuário marca uma aposta como encerrada/ganha/perdida etc."""
    with get_connection() as conn:
        conn.execute("UPDATE bets SET status = %s WHERE id = %s", (status, bet_id))


def update_resultado(bet_id: int, resultado: str) -> None:
    """Usado pelo app.py quando o usuário marca se A APOSTA (não o jogo) ganhou —
    pendente/green/red/void. Independente de `status`, que é sobre o jogo."""
    with get_connection() as conn:
        conn.execute("UPDATE bets SET resultado = %s WHERE id = %s", (resultado, bet_id))


def get_bet(bet_id: int) -> Optional[Bet]:
    with get_connection() as conn:
        cur = conn.execute("SELECT * FROM bets WHERE id = %s", (bet_id,))
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
        query += " AND status = %s"
        params.append(status)
    if date_from:
        query += " AND (data_hora IS NULL OR date(data_hora) >= date(%s))"
        params.append(date_from)
    if date_to:
        query += " AND (data_hora IS NULL OR date(data_hora) <= date(%s))"
        params.append(date_to)

    # order_by é sempre um literal fixo vindo do código (nunca input externo)
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

    TIMESTAMPTZ do Postgres é timezone-aware nativamente, então comparar
    contra now() aqui não sofre do bug de fuso horário que existia com o
    datetime('now') do SQLite (que era UTC, enquanto data_hora era salva em
    hora local).
    """
    with get_connection() as conn:
        cur = conn.execute(
            """
            UPDATE bets
               SET status = %s
             WHERE status = %s
               AND data_hora IS NOT NULL
               AND data_hora <= now()
            """,
            (BetStatus.AO_VIVO.value, BetStatus.AGENDADA.value),
        )
        return cur.rowcount


def list_apostas_ativas() -> list[Bet]:
    """
    Apostas ainda em jogo (agendada/ao_vivo), independente de ter
    sofascore_event_id — usado por listener.py pra casar um aviso de
    cash-out antecipado do tipster ("Fulano está pago!"/"...Cash") com a
    aposta daquele jogador, por nome (ver nameutils.names_match), já que
    esse aviso não cita o event_id nem o mercado, só o nome."""
    with get_connection() as conn:
        cur = conn.execute(
            "SELECT * FROM bets WHERE status IN (%s, %s)",
            (BetStatus.AGENDADA.value, BetStatus.AO_VIVO.value),
        )
        rows = cur.fetchall()
    return [_row_to_bet(r) for r in rows]


def list_trackable_bets() -> list[Bet]:
    """
    Apostas que score_updater.py deve acompanhar: ainda não encerradas
    (agendada/ao_vivo).

    Não exige mais `sofascore_event_id IS NOT NULL`: desde que o matcher
    passou a confirmar confrontos também pelo 365scores e pela Superbet
    (ver matcher.find_match), existem apostas legitimamente confirmadas
    SEM event_id do SofaScore. Exigir o event_id aqui deixaria justamente
    essas apostas sem nenhum acompanhamento de placar — elas são
    consultadas por NOME no 365scores (ver score_updater._consultar_status).
    """
    with get_connection() as conn:
        cur = conn.execute(
            "SELECT * FROM bets WHERE status IN (%s, %s)",
            (BetStatus.AGENDADA.value, BetStatus.AO_VIVO.value),
        )
        rows = cur.fetchall()
    return [_row_to_bet(r) for r in rows]


def list_unmatched_bets(desde: Optional[datetime] = None) -> list[Bet]:
    """
    Apostas que não acharam o confronto oficial na primeira tentativa (ex:
    a fonte de dados ainda não tinha listado a partida quando a tip chegou).
    Usado por score_updater.py para tentar de novo o matcher.py::find_match()
    periodicamente — necessário desde que listener.py passou a rodar em
    lote (polling) em vez de processar cada mensagem na hora: uma tentativa
    falha não tem mais uma "próxima mensagem" natural que a reprocesse.

    `desde` limita a apostas criadas a partir desse instante, e é o que
    impede a fila de retry de crescer para sempre. Sem esse corte, toda
    aposta não encontrada de TODOS os dias anteriores era retentada a cada
    ciclo — e como o matcher só procura o jogo de hoje em diante, uma
    aposta cujo jogo já passou NUNCA pode ser encontrada: ela ficava na
    fila eternamente, somando com as do dia seguinte, deixando cada ciclo
    mais lento até estourar o timeout do workflow (10 min) e matar o ciclo
    no meio — inclusive antes de chegar nas apostas do dia.
    """
    query = "SELECT * FROM bets WHERE status = %s"
    params: list = [BetStatus.NAO_ENCONTRADA.value]
    if desde is not None:
        query += " AND criado_em >= %s"
        params.append(desde)
    query += " ORDER BY criado_em DESC"

    with get_connection() as conn:
        cur = conn.execute(query, params)
        rows = cur.fetchall()
    return [_row_to_bet(r) for r in rows]


def update_score_result(
    bet_id: int,
    status: str,
    placar_final: Optional[str] = None,
    vencedor_partida: Optional[str] = None,
    resultado: Optional[str] = None,
) -> None:
    """Usado por score_updater.py para promover o status (ex: agendada -> ao_vivo)
    e, quando a partida termina, gravar o placar final e o vencedor junto.

    `resultado` (green/red) é opcional: só é passado quando
    resultado_checker.checar_resultado() conseguiu interpretar o mercado com
    segurança (ver score_updater.py) — se None, o campo `resultado` no banco
    fica como já estava (pendente até o usuário confirmar manualmente)."""
    with get_connection() as conn:
        if resultado is not None:
            conn.execute(
                "UPDATE bets SET status = %s, placar_final = %s, vencedor_partida = %s, resultado = %s WHERE id = %s",
                (status, placar_final, vencedor_partida, resultado, bet_id),
            )
        else:
            conn.execute(
                "UPDATE bets SET status = %s, placar_final = %s, vencedor_partida = %s WHERE id = %s",
                (status, placar_final, vencedor_partida, bet_id),
            )


def get_sync_state(key: str) -> Optional[str]:
    """Lê um valor pequeno e persistente (ex: 'last_message_id') — usado pelo
    listener.py no modo --poll-once, já que o runner do GitHub Actions não
    tem disco persistente entre execuções."""
    with get_connection() as conn:
        cur = conn.execute("SELECT value FROM sync_state WHERE key = %s", (key,))
        row = cur.fetchone()
        return row["value"] if row else None


def set_sync_state(key: str, value: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO sync_state (key, value) VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """,
            (key, value),
        )
