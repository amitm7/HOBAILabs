"""Commercial governance: consent/rights and spend controls.

SQLite bridge for the F1/F2 gate. Money and consent are append-only rows, not
JSON blobs, so concurrent operators cannot overwrite each other's events.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

_DB_PATH = Path(os.environ.get(
    "HOB_GOVERNANCE_DB",
    str(Path(tempfile.gettempdir()) / "hob_governance.db"),
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
        legacy_cost = _rename_legacy_table(con, "cost_events", {"project_key", "event_type"})
        legacy_consent = _rename_legacy_table(con, "consent_records", {"subject_key", "project_key"})
        con.execute(
            "CREATE TABLE IF NOT EXISTS cost_events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "project_key TEXT NOT NULL, run_id TEXT NOT NULL DEFAULT '', "
            "vendor TEXT NOT NULL DEFAULT '', item TEXT NOT NULL, "
            "usd REAL NOT NULL DEFAULT 0, cached INTEGER NOT NULL DEFAULT 0, "
            "frame_id TEXT NOT NULL DEFAULT '', event_type TEXT NOT NULL, "
            "created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')))"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_cost_events_project "
            "ON cost_events(project_key, created_at)"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS consent_records ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "subject_key TEXT NOT NULL, project_key TEXT NOT NULL, "
            "mode TEXT NOT NULL, confirmed_by TEXT NOT NULL DEFAULT '', "
            "payload_json TEXT NOT NULL DEFAULT '{}', "
            "created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')))"
        )
        # Gap #4: bind consent to the specific AI-likeness modality (face / voice)
        # of a named real person, not just a generic rights checkbox.
        for _col in ("face", "voice"):
            if _col not in _columns(con, "consent_records"):
                try:
                    con.execute(f"ALTER TABLE consent_records ADD COLUMN {_col} INTEGER NOT NULL DEFAULT 0")
                except sqlite3.OperationalError:
                    pass
        _migrate_legacy_cost(con, legacy_cost)
        _migrate_legacy_consent(con, legacy_consent)
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


def _migrate_legacy_cost(con: sqlite3.Connection, table: str) -> None:
    if not table:
        return
    try:
        rows = con.execute(f"SELECT key, value FROM {table}").fetchall()
    except sqlite3.Error:
        return
    for row in rows:
        try:
            events = json.loads(row["value"] or "[]")
        except Exception:
            events = []
        for event in events if isinstance(events, list) else []:
            con.execute(
                "INSERT INTO cost_events(project_key, item, usd, cached, frame_id, event_type) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    row["key"],
                    event.get("item", "legacy_estimate"),
                    float(event.get("usd", 0.0) or 0.0),
                    int(bool(event.get("cached"))),
                    event.get("frame_id", ""),
                    event.get("event_type", "legacy_estimate"),
                ),
            )


def _migrate_legacy_consent(con: sqlite3.Connection, table: str) -> None:
    if not table:
        return
    try:
        rows = con.execute(f"SELECT key, value FROM {table}").fetchall()
    except sqlite3.Error:
        return
    for row in rows:
        con.execute(
            "INSERT INTO consent_records(subject_key, project_key, mode, confirmed_by, payload_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (row["key"], "legacy", "story", "", row["value"] or "{}"),
        )


def project_key(data: dict) -> str:
    return (data.get("project_id") or data.get("session_id") or "default").strip()


def validate_consent(data: dict) -> list[str]:
    """Return missing consent/rights checklist items. Pure: no writes."""
    missing = []
    is_brand = data.get("mode") == "brand"
    consent_required = bool(data.get("consent_required") or is_brand)
    if not consent_required:
        return missing
    brand = data.get("brand") or {}
    if is_brand and not brand.get("rights_confirmed"):
        missing.append("Confirm consent / likeness / content rights for all real people and brand assets")
    return missing


def record_consent(data: dict, *, confirmed_by: str = "") -> int | None:
    """Append an explicit consent artifact after validation passes."""
    subject_key = (data.get("subject_name") or data.get("session_id") or "").strip()
    brand = data.get("brand") or {}
    if not subject_key or not (brand.get("rights_confirmed") or data.get("rights_confirmed")):
        return None
    cur = _conn().execute(
        "INSERT INTO consent_records(subject_key, project_key, mode, confirmed_by, payload_json) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            subject_key,
            project_key(data),
            data.get("mode", "story"),
            confirmed_by or data.get("operator_id", ""),
            json.dumps({"subject": subject_key, "mode": data.get("mode", "story")}, sort_keys=True),
        ),
    )
    _conn().commit()
    return int(cur.lastrowid)


def likeness_modalities(data: dict) -> dict:
    """Which AI-likeness modalities a render uses on a *named real person*.

    The authenticity moat (MARKET_FIT_REVIEW §4): AI rendering of a real person's
    face or voice must be a consented, labeled exception. `ai_symbolic` / real
    footage need nothing here.
      face  -> any frame is an AI portrait (`photo_spec == "ai_portrait"`)
      voice -> a cloned/synthetic voice of the subject (voice_clone or lip-synced)
    """
    subject = (data.get("subject_name") or "").strip()
    if not subject:
        return {"subject": "", "face": False, "voice": False}
    frames = data.get("frames") or []
    face = any((f.get("photo_spec") or "").strip() == "ai_portrait" for f in frames)
    voice = bool(data.get("voice_clone")) or any(f.get("lipsync") for f in frames)
    return {"subject": subject, "face": face, "voice": voice}


def _recorded_likeness(subject: str, project: str) -> dict:
    """Modalities already consented for this subject+project (DB truth)."""
    rows = _conn().execute(
        "SELECT face, voice FROM consent_records WHERE subject_key=? AND project_key=?",
        (subject, project),
    ).fetchall()
    return {
        "face": any(r["face"] for r in rows),
        "voice": any(r["voice"] for r in rows),
    }


def validate_likeness_consent(data: dict) -> list[str]:
    """Block messages for AI face/voice use of a real person lacking consent.

    Satisfied either by a prior recorded grant or by a grant supplied in this
    request (`data["likeness_consent"] = {"face": true, "voice": true}`). Pure read.
    """
    need = likeness_modalities(data)
    if not need["subject"] or not (need["face"] or need["voice"]):
        return []
    recorded = _recorded_likeness(need["subject"], project_key(data))
    granted = data.get("likeness_consent") or {}
    missing = []
    for modality in ("face", "voice"):
        if need[modality] and not recorded[modality] and not granted.get(modality):
            missing.append(
                f"Record consent for AI {modality} likeness of '{need['subject']}' "
                f"(this attacks the authenticity moat — see MARKET_FIT_REVIEW §4)"
            )
    return missing


def record_likeness_consent(data: dict, *, confirmed_by: str = "") -> int | None:
    """Persist a face/voice consent grant supplied in the request. Idempotent-ish:
    only writes the modalities both *needed* and *granted* in this payload."""
    need = likeness_modalities(data)
    granted = data.get("likeness_consent") or {}
    face = int(bool(need["face"] and granted.get("face")))
    voice = int(bool(need["voice"] and granted.get("voice")))
    if not need["subject"] or not (face or voice):
        return None
    cur = _conn().execute(
        "INSERT INTO consent_records(subject_key, project_key, mode, confirmed_by, payload_json, face, voice) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            need["subject"], project_key(data), data.get("mode", "story"),
            confirmed_by or data.get("operator_id", ""),
            json.dumps({"subject": need["subject"], "face": bool(face), "voice": bool(voice)}, sort_keys=True),
            face, voice,
        ),
    )
    _conn().commit()
    return int(cur.lastrowid)


def ledger_total(key: str) -> float:
    row = _conn().execute(
        "SELECT COALESCE(SUM(usd), 0) AS total FROM cost_events WHERE project_key=?",
        (key,),
    ).fetchone()
    return round(float(row["total"] if row else 0.0), 4)


def record_cost_event(key: str, *, item: str, usd: float, cached: bool = False,
                      frame_id: str = "", run_id: str = "", vendor: str = "",
                      event_type: str = "estimate") -> int:
    cur = _conn().execute(
        "INSERT INTO cost_events(project_key, run_id, vendor, item, usd, cached, frame_id, event_type) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (key, run_id, vendor, item, round(float(usd), 4), int(bool(cached)), frame_id, event_type),
    )
    _conn().commit()
    return int(cur.lastrowid)


def check_spend_cap(data: dict, estimate_usd: float) -> list[str]:
    """Return a blocking message when estimated spend would cross the project cap."""
    key = project_key(data)
    cap = _spend_cap(data)
    if cap <= 0:
        return []
    current = ledger_total(key)
    if current + float(estimate_usd or 0) > cap:
        return [f"Spend cap exceeded for {key}: ${current:.2f} used/reserved + ${estimate_usd:.2f} estimate > ${cap:.2f} cap"]
    return []


def _spend_cap(data: dict) -> float:
    raw_cap = data.get("spend_cap_usd") or os.environ.get("HOB_PROJECT_SPEND_CAP_USD", "25")
    try:
        return float(raw_cap)
    except (TypeError, ValueError):
        return 0.0


def reserve_spend(data: dict, estimate_usd: float, *, run_id: str = "") -> list[str]:
    """Atomically reserve estimated spend before dispatch."""
    key = project_key(data)
    cap = _spend_cap(data)
    if cap <= 0:
        return []
    con = _conn()
    estimate = round(float(estimate_usd or 0), 4)
    with con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT COALESCE(SUM(usd), 0) AS total FROM cost_events WHERE project_key=?",
            (key,),
        ).fetchone()
        current = float(row["total"] if row else 0.0)
        if current + estimate > cap:
            con.rollback()
            return [f"Spend cap exceeded for {key}: ${current:.2f} used/reserved + ${estimate:.2f} estimate > ${cap:.2f} cap"]
        con.execute(
            "INSERT INTO cost_events(project_key, run_id, item, usd, event_type) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, run_id, "reserved_estimate_usd", estimate, "reservation"),
        )
    return []


def release_reservation(data: dict, *, run_id: str = "", reason: str = "release") -> None:
    """Release any active reservation for a run by appending a negative event."""
    key = project_key(data)
    row = _conn().execute(
        "SELECT COALESCE(SUM(usd), 0) AS total FROM cost_events "
        "WHERE project_key=? AND run_id=? AND event_type IN ('reservation', 'reservation_release')",
        (key, run_id),
    ).fetchone()
    reserved = float(row["total"] if row else 0.0)
    if reserved:
        record_cost_event(
            key,
            item=f"reservation_{reason}",
            usd=-reserved,
            run_id=run_id,
            event_type="reservation_release",
        )


def sweep_stale_reservations(ttl_seconds: int = 7200) -> int:
    """Release reservations whose run never settled (e.g. the process was killed
    mid-render), so a crashed run cannot permanently inflate a project's spend
    cap. A reservation is "stale" when its run still has a positive net hold and
    its newest reservation event is older than `ttl_seconds`.

    Idempotent: once released, a run's net hold is 0 and it is skipped. Safe to
    call on every server startup. Returns the count of runs released.
    """
    cutoff = int(time.time()) - max(0, ttl_seconds)
    rows = _conn().execute(
        "SELECT project_key, run_id, COALESCE(SUM(usd), 0) AS net, "
        "MAX(created_at) AS last_at FROM cost_events "
        "WHERE event_type IN ('reservation', 'reservation_release') AND run_id != '' "
        "GROUP BY project_key, run_id "
        "HAVING net > 0.0001 AND last_at < ?",
        (cutoff,),
    ).fetchall()
    for row in rows:
        record_cost_event(
            row["project_key"],
            item="reservation_stale_sweep",
            usd=-float(row["net"]),
            run_id=row["run_id"],
            event_type="reservation_release",
        )
    return len(rows)
