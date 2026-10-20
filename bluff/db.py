"""Database access: connection pool, transactions and migrations.

Only the Streamlit server process uses this module. The connection string is
read from Streamlit secrets or the DATABASE_URL environment variable and is
never sent to a browser.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def resolve_database_url(explicit: Optional[str] = None) -> str:
    if explicit:
        return explicit
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    try:  # Streamlit secrets, if running inside Streamlit
        import streamlit as st

        url = st.secrets.get("DATABASE_URL")
    except Exception:  # noqa: BLE001 - no secrets file / not in Streamlit
        url = None
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not configured. Set it in .streamlit/secrets.toml or the environment.")
    return url


class Database:
    """A small wrapper around a psycopg connection pool."""

    def __init__(self, dsn: str, min_size: int = 1, max_size: int = 8) -> None:
        self.pool = ConnectionPool(
            dsn,
            min_size=min_size,
            max_size=max_size,
            # prepare_threshold=None keeps us compatible with PgBouncer transaction pooling.
            kwargs={"autocommit": True, "prepare_threshold": None, "row_factory": dict_row},
            open=True,
        )

    @contextmanager
    def tx(self) -> Iterator[psycopg.Connection]:
        """One atomic transaction; commits on success, rolls back on any exception."""
        with self.pool.connection() as conn:
            with conn.transaction():
                yield conn

    @contextmanager
    def read(self) -> Iterator[psycopg.Connection]:
        """A read-only snapshot (REPEATABLE READ) so a view is internally consistent."""
        with self.pool.connection() as conn:
            conn.execute("begin isolation level repeatable read read only")
            try:
                yield conn
            finally:
                conn.execute("rollback")

    def close(self) -> None:
        self.pool.close()


def apply_migrations(dsn: str) -> list[str]:
    """Apply migrations/*.sql in order, recording each in bluff.schema_migrations."""
    applied: list[str] = []
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("create schema if not exists bluff")
        conn.execute("create table if not exists bluff.schema_migrations "
                     "(filename text primary key, applied_at timestamptz not null default now())")
        done = {r[0] for r in conn.execute("select filename from bluff.schema_migrations")}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in done:
                continue
            with conn.transaction():
                conn.execute(path.read_text())
                conn.execute("insert into bluff.schema_migrations (filename) values (%s)", (path.name,))
            applied.append(path.name)
    return applied
