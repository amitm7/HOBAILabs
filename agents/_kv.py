"""
Tiny SQLite-backed key → text store. Concurrency-safe (WAL), per-key writes.

Shared helper for content-hash caches that hold small TEXT values — the image
description cache today; reusable for run/job metadata and cache indexes later
(see docs/WORK_PLAN.md P3/P5). Replaces the whole-file-rewrite JSON pattern,
which raced under the matcher's thread pool and grew unbounded.

NOT for large binary blobs (clips, audio, stills) — those belong on the
filesystem / object store (see agents/cache_store.py).
"""

import sqlite3
import threading
from pathlib import Path


class KVStore:
    """A minimal, thread-safe key→string store backed by one SQLite file.

    WAL journal mode allows concurrent readers alongside a single writer, so the
    per-key `set()` is safe to call from multiple worker threads. One connection
    is opened per thread (sqlite3 connections are not shareable across threads).
    """

    def __init__(self, db_path, table: str = "kv"):
        self.db_path = str(db_path)
        self.table = "".join(c for c in table if c.isalnum() or c == "_") or "kv"
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self._conn() as con:
            con.execute(
                f"CREATE TABLE IF NOT EXISTS {self.table} ("
                "k TEXT PRIMARY KEY, v TEXT NOT NULL, "
                "updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now')))"
            )

    def _conn(self) -> sqlite3.Connection:
        con = getattr(self._local, "con", None)
        if con is None:
            con = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=NORMAL")
            self._local.con = con
        return con

    def get(self, key: str) -> str | None:
        row = self._conn().execute(
            f"SELECT v FROM {self.table} WHERE k=?", (key,)
        ).fetchone()
        return row[0] if row else None

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None

    def set(self, key: str, value: str) -> None:
        con = self._conn()
        with con:  # transaction: commit on success, rollback on error
            con.execute(
                f"INSERT OR REPLACE INTO {self.table}(k, v, updated_at) "
                "VALUES(?, ?, strftime('%s','now'))",
                (key, value),
            )

    def __len__(self) -> int:
        return self._conn().execute(
            f"SELECT COUNT(*) FROM {self.table}"
        ).fetchone()[0]
