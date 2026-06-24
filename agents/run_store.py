"""Restart-safe run metadata store.

Run metadata is one row per run; logs are append-only rows so long renders do not
rewrite an ever-growing JSON blob on every print().
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
from pathlib import Path

_DB_PATH = Path(os.environ.get(
    "HOB_RUNS_DB",
    str(Path(tempfile.gettempdir()) / "hob_runs.db"),
)).expanduser()
_LOCAL = threading.local()


def _conn() -> sqlite3.Connection:
    con = getattr(_LOCAL, "con", None)
    if con is None:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(_DB_PATH), timeout=30, check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        legacy_runs = _rename_legacy_table(con, "runs", {"run_id", "payload_json"})
        con.execute(
            "CREATE TABLE IF NOT EXISTS runs ("
            "run_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'running', "
            "payload_json TEXT NOT NULL DEFAULT '{}', run_dir TEXT NOT NULL DEFAULT '', "
            "output_path TEXT NOT NULL DEFAULT '', edit_list_path TEXT NOT NULL DEFAULT '', "
            "error TEXT NOT NULL DEFAULT '', "
            "performance_views INTEGER, performance_likes INTEGER, "
            "performance_note TEXT NOT NULL DEFAULT '', "
            "performance_by TEXT NOT NULL DEFAULT '', "
            "updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now')))"
        )
        # Back-compat: add the post-publish feedback columns to DBs created before this
        # change. Guarded by _columns() and wrapped because each threading.local
        # connection re-runs _conn(); two fresh connections can race the ADD COLUMN.
        for _col, _ddl in (
            ("performance_views", "INTEGER"),
            ("performance_likes", "INTEGER"),
            ("performance_note", "TEXT NOT NULL DEFAULT ''"),
            ("performance_by", "TEXT NOT NULL DEFAULT ''"),
        ):
            if _col not in _columns(con, "runs"):
                try:
                    con.execute(f"ALTER TABLE runs ADD COLUMN {_col} {_ddl}")
                except sqlite3.OperationalError:
                    pass  # another connection added it first
        con.execute(
            "CREATE TABLE IF NOT EXISTS run_logs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, line TEXT NOT NULL, "
            "created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')))"
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_run_logs_run ON run_logs(run_id, id)")
        _migrate_legacy_runs(con, legacy_runs)
        con.commit()
        _LOCAL.con = con
    return con


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in con.execute(f"PRAGMA table_info({table})")}


def _rename_legacy_table(con: sqlite3.Connection, table: str, required: set[str]) -> str:
    cols = _columns(con, table)
    if not cols or required.issubset(cols):
        return ""
    backup = f"{table}_kv_legacy"
    if _columns(con, backup):
        backup = f"{backup}_{os.getpid()}"
    con.execute(f"ALTER TABLE {table} RENAME TO {backup}")
    return backup


def _migrate_legacy_runs(con: sqlite3.Connection, table: str) -> None:
    if not table:
        return
    try:
        rows = con.execute(f"SELECT key, value FROM {table}").fetchall()
    except sqlite3.Error:
        return
    for row in rows:
        try:
            data = json.loads(row["value"] or "{}")
        except Exception:
            data = {}
        con.execute(
            "INSERT OR IGNORE INTO runs(run_id, status, payload_json, run_dir, output_path, edit_list_path, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                row["key"],
                data.get("status", "unknown"),
                json.dumps(data.get("payload", {}), sort_keys=True),
                data.get("run_dir", ""),
                data.get("output_path", ""),
                data.get("edit_list_path", ""),
                data.get("error", ""),
            ),
        )
        for line in data.get("log", []) if isinstance(data.get("log", []), list) else []:
            con.execute("INSERT INTO run_logs(run_id, line) VALUES (?, ?)", (row["key"], str(line)))


def save(run_id: str, **fields) -> None:
    existing = _load_meta(run_id) or {}
    data = {
        "status": fields.get("status", existing.get("status", "running")),
        "payload": fields.get("payload", existing.get("payload", {})),
        "run_dir": fields.get("run_dir", existing.get("run_dir", "")),
        "output_path": fields.get("output_path", existing.get("output_path", "")),
        "edit_list_path": fields.get("edit_list_path", existing.get("edit_list_path", "")),
        "error": fields.get("error", existing.get("error", "")),
        "performance_views": fields.get("performance_views", existing.get("performance_views")),
        "performance_likes": fields.get("performance_likes", existing.get("performance_likes")),
        "performance_note": fields.get("performance_note", existing.get("performance_note", "")),
        "performance_by": fields.get("performance_by", existing.get("performance_by", "")),
    }
    _conn().execute(
        "INSERT INTO runs(run_id, status, payload_json, run_dir, output_path, edit_list_path, error, "
        "performance_views, performance_likes, performance_note, performance_by, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%s','now')) "
        "ON CONFLICT(run_id) DO UPDATE SET "
        "status=excluded.status, payload_json=excluded.payload_json, run_dir=excluded.run_dir, "
        "output_path=excluded.output_path, edit_list_path=excluded.edit_list_path, "
        "error=excluded.error, performance_views=excluded.performance_views, "
        "performance_likes=excluded.performance_likes, performance_note=excluded.performance_note, "
        "performance_by=excluded.performance_by, updated_at=strftime('%s','now')",
        (
            run_id,
            data["status"],
            json.dumps(data["payload"], sort_keys=True),
            data["run_dir"],
            data["output_path"],
            data["edit_list_path"],
            data["error"],
            data["performance_views"],
            data["performance_likes"],
            data["performance_note"],
            data["performance_by"],
        ),
    )
    _conn().commit()


def _load_meta(run_id: str) -> dict | None:
    row = _conn().execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except Exception:
        payload = {}
    return {
        "status": row["status"],
        "payload": payload,
        "run_dir": row["run_dir"],
        "output_path": row["output_path"],
        "edit_list_path": row["edit_list_path"],
        "error": row["error"],
        "performance_views": row["performance_views"],
        "performance_likes": row["performance_likes"],
        "performance_note": row["performance_note"],
        "performance_by": row["performance_by"],
    }


def load(run_id: str) -> dict | None:
    meta = _load_meta(run_id)
    if not meta:
        return None
    logs = [
        r["line"] for r in _conn().execute(
            "SELECT line FROM run_logs WHERE run_id=? ORDER BY id ASC LIMIT 2000",
            (run_id,),
        )
    ]
    return {**meta, "log": logs, "events": []}


def list_performance(limit: int = 100) -> list[dict]:
    """Runs that have a logged performance signal, best-performing first.

    Completes the feedback loop (Gap #3): the capture stub writes performance_*,
    this is the read/aggregation path so operators can see what actually performed
    and later correlate it against the run payload.
    """
    rows = _conn().execute(
        "SELECT run_id, status, performance_views, performance_likes, "
        "performance_note, performance_by, updated_at FROM runs "
        "WHERE performance_views IS NOT NULL OR performance_likes IS NOT NULL "
        "OR performance_note != '' "
        "ORDER BY COALESCE(performance_views, 0) DESC, COALESCE(performance_likes, 0) DESC "
        "LIMIT ?",
        (limit,),
    )
    return [dict(r) for r in rows]


def performance_summary() -> dict:
    """Roll-up across all runs with a logged result."""
    row = _conn().execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(performance_views), 0) AS views, "
        "COALESCE(SUM(performance_likes), 0) AS likes FROM runs "
        "WHERE performance_views IS NOT NULL OR performance_likes IS NOT NULL "
        "OR performance_note != ''"
    ).fetchone()
    return {"runs_with_data": row["n"], "total_views": row["views"], "total_likes": row["likes"]}


def append_log(run_id: str, line: str) -> None:
    _conn().execute(
        "INSERT INTO run_logs(run_id, line) VALUES (?, ?)",
        (run_id, line),
    )
    _conn().commit()
