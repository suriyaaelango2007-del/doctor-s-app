from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import get_settings

_pool: ConnectionPool | None = None


def open_pool(conninfo: str | None = None) -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo or get_settings().database_url,
            min_size=1,
            max_size=10,
            # prepare_threshold=None keeps us compatible with Supabase's
            # transaction-mode pooler (pgbouncer), which can't do prepared statements.
            # TCP keepalives stop NATs/poolers from silently dropping idle connections.
            kwargs={
                "row_factory": dict_row,
                "prepare_threshold": None,
                "keepalives": 1,
                "keepalives_idle": 30,
                "keepalives_interval": 10,
                "keepalives_count": 3,
            },
            # Supabase's pooler closes idle connections: test each one before use and
            # retire idle/old ones ourselves, so a request never gets a dead connection.
            check=ConnectionPool.check_connection,
            max_idle=120,
            max_lifetime=1800,
            open=True,
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def transaction() -> Iterator[psycopg.Connection]:
    """Yield a connection inside a transaction; commits on success, rolls back on error."""
    pool = open_pool()
    with pool.connection() as conn:
        with conn.transaction():
            yield conn
