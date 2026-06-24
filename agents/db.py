"""Config-driven storage backend — SQLite by default, Postgres-ready.

One switch governs where durable state lives:

    HOB_DB_URL unset / "sqlite[...]"   -> local SQLite files (default; runs anywhere)
    HOB_DB_URL=postgresql://user:pw@host/db -> Postgres (production, e.g. RDS)

The goal (per docs/SCALE_PLAN.md Phase 2: "one Postgres serves everything") is that
swapping durable storage is a *config* change, not a code change. New stores (auth,
consent v2) route every connection + DDL through here so they are dialect-neutral;
the older per-store SQLite bridges (run_store, governance) migrate onto this layer
incrementally — the schema shapes are already forward-compatible.

Design notes:
- `backend()` is decided once from the env URL.
- `connect(name)` returns a DB-API connection with **mapping rows** in both backends
  (sqlite3.Row / psycopg2 RealDictCursor), so call sites read `row["col"]` uniformly.
- SQL is written with `?` placeholders and the portable fragments below; `adapt()`
  rewrites them for Postgres. Keep new SQL inside this vocabulary so both paths work.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse

_URL = os.environ.get("HOB_DB_URL", "").strip()
_LOCAL = threading.local()


def backend() -> str:
    """'postgres' when HOB_DB_URL points at Postgres, else 'sqlite' (default)."""
    if _URL and urlparse(_URL).scheme in ("postgres", "postgresql"):
        return "postgres"
    return "sqlite"


def is_postgres() -> bool:
    return backend() == "postgres"


# ── Portable SQL fragments (differ by dialect) ─────────────────────────────────
# Use these in DDL so the same store definition works on both backends.
def autoinc_pk() -> str:
    return "BIGSERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"


def now_ts() -> str:
    """SQL expression yielding an integer unix timestamp 'now'."""
    return "EXTRACT(EPOCH FROM now())::bigint" if is_postgres() else "strftime('%s','now')"


def adapt(sql: str) -> str:
    """Rewrite `?` placeholders to `%s` for Postgres; no-op for SQLite."""
    return sql.replace("?", "%s") if is_postgres() else sql


def _sqlite_path(name: str) -> Path:
    """Per-logical-store SQLite file. HOB_DB_DIR overrides the temp default."""
    base = os.environ.get("HOB_DB_DIR") or tempfile.gettempdir()
    # Honour an explicit sqlite file URL of the form sqlite:///abs/path.db for `name`-less callers.
    if _URL.startswith("sqlite") and ":///" in _URL:
        return Path(_URL.split(":///", 1)[1]).expanduser()
    return Path(base).expanduser() / f"hob_{name}.db"


def connect(name: str):
    """Return a cached, thread-local connection for logical store `name`.

    SQLite: a WAL file per store. Postgres: a shared connection to HOB_DB_URL
    (tables are uniquely named, so one database holds every store).
    """
    cache = getattr(_LOCAL, "conns", None)
    if cache is None:
        cache = _LOCAL.conns = {}
    key = "_pg" if is_postgres() else name
    con = cache.get(key)
    if con is not None:
        return con

    if is_postgres():
        import psycopg2  # lazy: only needed in the Postgres deployment
        import psycopg2.extras
        con = psycopg2.connect(_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        con.autocommit = False
    else:
        path = _sqlite_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
    cache[key] = con
    return con
