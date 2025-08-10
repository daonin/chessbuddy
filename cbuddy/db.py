from __future__ import annotations
import contextlib
from typing import Iterator, Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, Connection

from .config import AppConfig

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        cfg = AppConfig()
        _engine = create_engine(cfg.database_url, future=True)
    return _engine


@contextlib.contextmanager
def get_connection() -> Iterator[Connection]:
    engine = get_engine()
    with engine.begin() as conn:  # transactional
        yield conn


def fetch_one(conn: Connection, sql: str, **params: Any) -> dict[str, Any] | None:
    row = conn.execute(text(sql), params).mappings().fetchone()
    return dict(row) if row is not None else None


def fetch_all(conn: Connection, sql: str, **params: Any) -> list[dict[str, Any]]:
    rows = conn.execute(text(sql), params).mappings().fetchall()
    return [dict(r) for r in rows]


def execute(conn: Connection, sql: str, **params: Any) -> None:
    conn.execute(text(sql), params)
