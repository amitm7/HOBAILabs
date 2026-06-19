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
            "error TEXT NOT NULL DEFAULT '', updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now')))"
        )
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
    }
    _conn().execute(
        "INSERT INTO runs(run_id, status, payload_json, run_dir, output_path, edit_list_path, error, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, strftime('%s','now')) "
        "ON CONFLICT(run_id) DO UPDATE SET "
        "status=excluded.status, payload_json=excluded.payload_json, run_dir=excluded.run_dir, "
        "output_path=excluded.output_path, edit_list_path=excluded.edit_list_path, "
        "error=excluded.error, updated_at=strftime('%s','now')",
        (
            run_id,
            data["status"],
            json.dumps(data["payload"], sort_keys=True),
            data["run_dir"],
            data["output_path"],
            data["edit_list_path"],
            data["error"],
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


def append_log(run_id: str, line: str) -> None:
    _conn().execute(
        "INSERT INTO run_logs(run_id, line) VALUES (?, ?)",
        (run_id, line),
    )
    _conn().commit()
