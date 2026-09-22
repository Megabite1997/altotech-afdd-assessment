"""Database access. Explicit SQL over psycopg3 with a shared pool."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import settings

log = logging.getLogger(__name__)

_pool: ConnectionPool | None = None


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            settings.database_url,
            min_size=settings.db_pool_min,
            max_size=settings.db_pool_max,
            # Every timestamp the platform reads or writes is UTC. Pinning the
            # session timezone keeps API payloads, logs and the CLI consistent
            # regardless of the host's locale.
            kwargs={"row_factory": dict_row, "options": "-c timezone=UTC"},
            open=True,
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close(timeout=1.0)
        _pool = None


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    with pool().connection() as conn:
        yield conn


@contextmanager
def cursor(commit: bool = True) -> Iterator[psycopg.Cursor]:
    with pool().connection() as conn:
        with conn.cursor() as cur:
            yield cur
        if commit:
            conn.commit()


def query(sql: str, params: Any = None) -> list[dict]:
    with cursor(commit=False) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def query_one(sql: str, params: Any = None) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Any = None) -> int:
    with cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def migrate() -> list[str]:
    """Apply every .sql file in migrations/ in filename order."""
    applied: list[str] = []
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    with psycopg.connect(settings.database_url) as conn:
        for path in files:
            log.info("applying migration %s", path.name)
            with conn.cursor() as cur:
                cur.execute(path.read_text())
            conn.commit()
            applied.append(path.name)
    return applied


def wait_for_db(timeout: float = 90.0, interval: float = 1.5) -> None:
    import time

    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            with psycopg.connect(settings.database_url, connect_timeout=3) as conn:
                conn.execute("SELECT 1")
            return
        except Exception as exc:  # pragma: no cover - startup race
            last = exc
            time.sleep(interval)
    raise RuntimeError(f"database not reachable within {timeout}s: {last}")
