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
            kwargs={"row_factory": dict_row, "prepare_threshold": None},
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
