"""Durable product-surface stand-ins.

SQLite-backed versions of the future Postgres surfaces from SCALE_PLAN:
assets, brand approvals, and reel versions. These are intentionally small and
hash-keyed so migration later is mechanical.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import uuid
from pathlib import Path

_DB_PATH = Path(os.environ.get(
    "HOB_PRODUCT_DB",
    str(Path(tempfile.gettempdir()) / "hob_product_surface.db"),
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
        legacy_assets = _rename_legacy_table(con, "assets", {"sha256", "path"})
        legacy_approvals = _rename_legacy_table(con, "approvals", {"project_id", "payload_hash"})
        legacy_versions = _rename_legacy_table(con, "versions", {"project_id", "payload_hash"})
        con.execute(
            "CREATE TABLE IF NOT EXISTS assets ("
            "sha256 TEXT PRIMARY KEY, path TEXT NOT NULL, kind TEXT NOT NULL, "
            "owner TEXT NOT NULL, consent_flag INTEGER NOT NULL, deleted INTEGER NOT NULL DEFAULT 0, "
            "created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')), "
            "updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now')))"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS approvals ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, version TEXT NOT NULL, "
            "approver TEXT NOT NULL DEFAULT '', payload_hash TEXT NOT NULL, payload_json TEXT NOT NULL, "
            "created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')))"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS versions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, version TEXT NOT NULL, "
            "payload_hash TEXT NOT NULL, payload_json TEXT NOT NULL, output_path TEXT NOT NULL DEFAULT '', "
            "created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')))"
        )
        # Studio Mode identity library (MODE3_PLAN §3). Reference images themselves
        # live in the assets table (kind="talent"|"product"); these tables carry the
        # human-facing name + metadata and point at the asset by sha256.
        con.execute(
            "CREATE TABLE IF NOT EXISTS talents ("
            "id TEXT PRIMARY KEY, name TEXT NOT NULL, ref_sha256 TEXT NOT NULL, "
            "descriptor TEXT NOT NULL DEFAULT '', owner TEXT NOT NULL DEFAULT 'default', "
            "created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')))"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS products ("
            "id TEXT PRIMARY KEY, name TEXT NOT NULL, ref_sha256 TEXT NOT NULL, "
            "specs_json TEXT NOT NULL DEFAULT '{}', owner TEXT NOT NULL DEFAULT 'default', "
            "created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')))"
        )
        _migrate_legacy_assets(con, legacy_assets)
        _migrate_legacy_approvals(con, legacy_approvals)
        _migrate_legacy_versions(con, legacy_versions)
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


def _legacy_rows(con: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    if not table:
        return []
    try:
        return con.execute(f"SELECT key, value FROM {table}").fetchall()
    except sqlite3.Error:
        return []


def _migrate_legacy_assets(con: sqlite3.Connection, table: str) -> None:
    for row in _legacy_rows(con, table):
        try:
            data = json.loads(row["value"] or "{}")
        except Exception:
            data = {}
        con.execute(
            "INSERT OR IGNORE INTO assets(sha256, path, kind, owner, consent_flag, deleted) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                data.get("sha256") or row["key"],
                data.get("path", ""),
                data.get("kind", ""),
                data.get("owner", ""),
                int(bool(data.get("consent_flag"))),
                int(bool(data.get("deleted"))),
            ),
        )


def _migrate_legacy_approvals(con: sqlite3.Connection, table: str) -> None:
    for row in _legacy_rows(con, table):
        try:
            data = json.loads(row["value"] or "{}")
        except Exception:
            data = {}
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else data
        project_id = data.get("project_id") or str(row["key"]).split(":", 1)[0]
        con.execute(
            "INSERT INTO approvals(project_id, version, approver, payload_hash, payload_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                project_id,
                payload.get("version", "latest"),
                payload.get("approver", ""),
                _payload_hash(payload),
                json.dumps(payload, sort_keys=True),
            ),
        )


def _migrate_legacy_versions(con: sqlite3.Connection, table: str) -> None:
    for row in _legacy_rows(con, table):
        try:
            data = json.loads(row["value"] or "{}")
        except Exception:
            data = {}
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else data
        project_id = data.get("project_id") or str(row["key"]).split(":", 1)[0]
        con.execute(
            "INSERT INTO versions(project_id, version, payload_hash, payload_json, output_path) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                project_id,
                payload.get("version", "v1"),
                _payload_hash(payload),
                json.dumps(payload, sort_keys=True),
                data.get("output_path", ""),
            ),
        )


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def register_asset(path: str, *, kind: str = "", consent_flag: bool = False,
                   owner: str = "default") -> dict:
    digest = _sha256(path)
    record = {
        "sha256": digest,
        "path": path,
        "kind": kind or Path(path).suffix.lower().lstrip("."),
        "owner": owner,
        "consent_flag": bool(consent_flag),
        "deleted": False,
    }
    _conn().execute(
        "INSERT INTO assets(sha256, path, kind, owner, consent_flag, deleted, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 0, strftime('%s','now')) "
        "ON CONFLICT(sha256) DO UPDATE SET "
        "path=excluded.path, kind=excluded.kind, owner=excluded.owner, "
        "consent_flag=excluded.consent_flag, deleted=0, updated_at=strftime('%s','now')",
        (digest, record["path"], record["kind"], record["owner"], int(record["consent_flag"])),
    )
    _conn().commit()
    return record


def get_asset(sha256: str) -> dict | None:
    row = _conn().execute("SELECT * FROM assets WHERE sha256=?", (sha256,)).fetchone()
    if not row:
        return None
    return {
        "sha256": row["sha256"],
        "path": row["path"],
        "kind": row["kind"],
        "owner": row["owner"],
        "consent_flag": bool(row["consent_flag"]),
        "deleted": bool(row["deleted"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _payload_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def record_approval(project_id: str, payload: dict, *, approver: str = "") -> dict:
    version = payload.get("version", "latest")
    payload_hash = _payload_hash(payload)
    cur = _conn().execute(
        "INSERT INTO approvals(project_id, version, approver, payload_hash, payload_json) "
        "VALUES (?, ?, ?, ?, ?)",
        (project_id, version, approver or payload.get("approver", ""), payload_hash, json.dumps(payload, sort_keys=True)),
    )
    _conn().commit()
    record = {
        "id": int(cur.lastrowid),
        "project_id": project_id,
        "version": version,
        "approver": approver or payload.get("approver", ""),
        "payload_hash": payload_hash,
        "payload": payload,
    }
    return record


def save_version(project_id: str, payload: dict, output_path: str = "") -> dict:
    version = payload.get("version", "v1")
    payload_hash = _payload_hash(payload)
    cur = _conn().execute(
        "INSERT INTO versions(project_id, version, payload_hash, payload_json, output_path) "
        "VALUES (?, ?, ?, ?, ?)",
        (project_id, version, payload_hash, json.dumps(payload, sort_keys=True), output_path),
    )
    _conn().commit()
    record = {
        "id": int(cur.lastrowid),
        "project_id": project_id,
        "version": version,
        "payload_hash": payload_hash,
        "payload": payload,
        "output_path": output_path,
    }
    return record


# ── Studio Mode identity library (talents + products) ─────────────────────────

def _short_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def register_talent(name: str, ref_path: str, *, descriptor: str = "",
                    owner: str = "default", consent_flag: bool = False) -> dict:
    """Save a reusable Talent (a locked face reference) for Studio Mode.
    The reference image is registered in the assets table; this row points at it."""
    asset = register_asset(ref_path, kind="talent", consent_flag=consent_flag, owner=owner)
    tid = _short_id("tal")
    _conn().execute(
        "INSERT INTO talents(id, name, ref_sha256, descriptor, owner) VALUES (?, ?, ?, ?, ?)",
        (tid, name.strip() or "Untitled talent", asset["sha256"], descriptor.strip(), owner),
    )
    _conn().commit()
    return get_talent(tid)


def get_talent(talent_id: str) -> dict | None:
    row = _conn().execute("SELECT * FROM talents WHERE id=?", (talent_id,)).fetchone()
    if not row:
        return None
    asset = get_asset(row["ref_sha256"]) or {}
    return {
        "id": row["id"], "name": row["name"], "ref_sha256": row["ref_sha256"],
        "ref_path": asset.get("path", ""), "descriptor": row["descriptor"],
        "owner": row["owner"], "created_at": row["created_at"],
    }


def list_talents(owner: str = "default") -> list[dict]:
    rows = _conn().execute(
        "SELECT id FROM talents WHERE owner=? ORDER BY created_at DESC", (owner,)
    ).fetchall()
    return [t for t in (get_talent(r["id"]) for r in rows) if t]


def delete_talent(talent_id: str) -> bool:
    cur = _conn().execute("DELETE FROM talents WHERE id=?", (talent_id,))
    _conn().commit()
    return cur.rowcount > 0


def register_product(name: str, ref_path: str, *, specs: dict | None = None,
                     owner: str = "default", consent_flag: bool = False) -> dict:
    """Save a reusable Product (a locked product reference + specs) for Studio Mode."""
    asset = register_asset(ref_path, kind="product", consent_flag=consent_flag, owner=owner)
    pid = _short_id("prd")
    _conn().execute(
        "INSERT INTO products(id, name, ref_sha256, specs_json, owner) VALUES (?, ?, ?, ?, ?)",
        (pid, name.strip() or "Untitled product", asset["sha256"],
         json.dumps(specs or {}, sort_keys=True), owner),
    )
    _conn().commit()
    return get_product(pid)


def get_product(product_id: str) -> dict | None:
    row = _conn().execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    if not row:
        return None
    asset = get_asset(row["ref_sha256"]) or {}
    try:
        specs = json.loads(row["specs_json"] or "{}")
    except Exception:
        specs = {}
    return {
        "id": row["id"], "name": row["name"], "ref_sha256": row["ref_sha256"],
        "ref_path": asset.get("path", ""), "specs": specs,
        "owner": row["owner"], "created_at": row["created_at"],
    }


def list_products(owner: str = "default") -> list[dict]:
    rows = _conn().execute(
        "SELECT id FROM products WHERE owner=? ORDER BY created_at DESC", (owner,)
    ).fetchall()
    return [p for p in (get_product(r["id"]) for r in rows) if p]


def delete_product(product_id: str) -> bool:
    cur = _conn().execute("DELETE FROM products WHERE id=?", (product_id,))
    _conn().commit()
    return cur.rowcount > 0


def approval_history(project_id: str) -> list[dict]:
    return [
        {
            "id": row["id"],
            "project_id": row["project_id"],
            "version": row["version"],
            "approver": row["approver"],
            "payload_hash": row["payload_hash"],
            "created_at": row["created_at"],
        }
        for row in _conn().execute(
            "SELECT id, project_id, version, approver, payload_hash, created_at "
            "FROM approvals WHERE project_id=? ORDER BY id ASC",
            (project_id,),
        )
    ]
