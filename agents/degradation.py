"""Degradation Ledger (T1, docs/L99_ARCH_PLAN.md).

The engine's graceful-degradation rule ("never hard-fail — fall back") is correct,
but a fallback that fires silently is how a silent reel ships as done ✓ (the
Suno-credits incident) and how identity conditioning silently vanished from six
shots (the fal content-policy incident). This module gives every best-effort site
one line to SAY what degraded, and gives the operator a quality receipt per render.

Severity taxonomy (red-teamed: ≤3 alert classes, expand only on evidence):
  info  — quality-neutral substitution (premium→fallback model succeeded)
  warn  — output likely differs from intent (identity ref lost, QC gate skipped)
  alert — the operator MUST see this before judging the output (no audio, stage dead)

Usage:
    degradation.bind(run_id)          # render thread start (re-entrant safe)
    degradation.report("identity", "warn", "nano_banana_edit rejected; shot f07 has no face ref")
    events = degradation.drain(run_id)  # render thread end → persist to state/UI

Single-tenant pragmatism (documented in the plan): binding is a process-level
current-run pointer with an explicit-run override; two truly concurrent renders
may cross-attribute info-level events. Acceptable for the single-operator app;
the multi-tenant fix rides SCALE_PLAN.
"""
from __future__ import annotations

import threading
import time

_GUARD = threading.Lock()
_EVENTS: dict[str, list[dict]] = {}     # run_id → events
_CURRENT: list[str] = []                 # bind stack (last = active)

SEVERITIES = ("info", "warn", "alert")


def bind(run_id: str) -> None:
    """Mark run_id as the active render for subsequent report() calls."""
    if not run_id:
        return
    with _GUARD:
        _CURRENT.append(run_id)
        _EVENTS.setdefault(run_id, [])


def unbind(run_id: str = "") -> None:
    with _GUARD:
        if run_id and run_id in _CURRENT:
            _CURRENT.remove(run_id)
        elif _CURRENT:
            _CURRENT.pop()


def report(step: str, severity: str, msg: str, *, run_id: str = "",
           frame_id: str = "") -> None:
    """Record one degradation event (and print it, so it also lands in run logs).
    Never raises — a ledger failure must not break a render. Pass `frame_id`
    where the event concerns one shot so the decision log / credential can
    attribute it (events without one stay run-level, as before)."""
    try:
        sev = severity if severity in SEVERITIES else "warn"
        with _GUARD:
            rid = run_id or (_CURRENT[-1] if _CURRENT else "")
            if rid:
                ev = {"step": step, "severity": sev, "msg": str(msg)[:300], "ts": int(time.time())}
                if frame_id:
                    ev["frame_id"] = frame_id
                _EVENTS.setdefault(rid, []).append(ev)
        icon = {"info": "ℹ", "warn": "⚠", "alert": "🔴"}[sev]
        print(f"[Degradation] {icon} {step}: {msg}")
    except Exception:
        pass


# ── Decision log (PROVENANCE_PLAN Slice B) ─────────────────────────────────────
# Companion channel to the degradation events: degradations say what went WRONG;
# decisions say what was CHOSEN per shot (model routed, cache hit, fallback), so
# the operator can debug a render and the Content Credential can attest which
# model actually produced each frame. Same in-memory bind/drain lifecycle.
_DECISIONS: dict[str, list[dict]] = {}   # run_id → decision rows


def decision(stage: str, frame_id: str, model: str, *, fell_back_from: str = "",
             reason: str = "", cached: bool = False, run_id: str = "") -> None:
    """Record one per-shot decision row. Never raises."""
    try:
        with _GUARD:
            rid = run_id or (_CURRENT[-1] if _CURRENT else "")
            if rid:
                row = {"stage": stage, "frame_id": frame_id, "model": model,
                       "ts": int(time.time())}
                if fell_back_from:
                    row["fell_back_from"] = fell_back_from
                if reason:
                    row["reason"] = str(reason)[:200]
                if cached:
                    row["cached"] = True
                _DECISIONS.setdefault(rid, []).append(row)
    except Exception:
        pass


def drain_decisions(run_id: str) -> list[dict]:
    """Return and clear the run's decision rows (call once at render end)."""
    with _GUARD:
        return _DECISIONS.pop(run_id, [])


def drain(run_id: str) -> list[dict]:
    """Return and clear the run's events (call once at render end)."""
    with _GUARD:
        return _EVENTS.pop(run_id, [])


def peek(run_id: str) -> list[dict]:
    with _GUARD:
        return list(_EVENTS.get(run_id, []))
