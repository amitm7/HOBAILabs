"""
Circuit breaker — resilience for external vendor calls (docs/INFRA_RESILIENCE_REVIEW.md).

A slow or down vendor (fal, an image/video model) would otherwise block a worker thread
for its full timeout on EVERY request. The breaker trips a per-key circuit after K
consecutive failures and then fails FAST (raises immediately) for a cooldown window, so
the caller's existing fallback chain (premium→fallback→Kling→Ken Burns) kicks in without
paying the timeout each time. On the next call after cooldown it half-opens: one trial;
success closes it, failure re-opens.

In-process only (per worker) — no Redis/queue. That's the right scope until we split
workers (SCALE_PLAN). Keyed by an arbitrary string (we use the fal endpoint), so one down
model doesn't trip the others. Best-effort + thread-safe.
"""
from __future__ import annotations

import threading
import time

FAIL_THRESHOLD = 3      # consecutive failures before the circuit opens
COOLDOWN_SEC = 60       # how long it stays open (fail-fast) before a trial call

_STATE: dict[str, dict] = {}     # key -> {"failures": int, "open_until": float}
_LOCK = threading.Lock()


class CircuitOpen(Exception):
    """Raised instead of calling a vendor whose circuit is currently open."""


def call(key: str, fn, *, threshold: int = FAIL_THRESHOLD, cooldown: int = COOLDOWN_SEC):
    """Run `fn()` under the per-`key` breaker. Raises CircuitOpen (without calling fn) if
    the circuit is open; otherwise runs fn and records success/failure. Re-raises fn's own
    exception on failure so callers see the real error when the circuit is closed."""
    now = time.time()
    with _LOCK:
        st = _STATE.setdefault(key, {"failures": 0, "open_until": 0.0})
        if st["open_until"] > now:
            raise CircuitOpen(f"{key}: circuit open, ~{int(st['open_until'] - now)}s left")
    try:
        result = fn()
    except Exception:
        with _LOCK:
            st = _STATE.setdefault(key, {"failures": 0, "open_until": 0.0})
            st["failures"] += 1
            if st["failures"] >= threshold:
                st["open_until"] = time.time() + cooldown
                st["failures"] = 0
                print(f"[Circuit] {key} OPEN for {cooldown}s "
                      f"(after {threshold} consecutive failures) — failing fast to fallback")
        raise
    else:
        with _LOCK:
            _STATE.setdefault(key, {"failures": 0, "open_until": 0.0})
            _STATE[key]["failures"] = 0
            _STATE[key]["open_until"] = 0.0
        return result


def is_open(key: str) -> bool:
    with _LOCK:
        st = _STATE.get(key)
        return bool(st and st["open_until"] > time.time())


def reset(key: str | None = None) -> None:
    """Clear one key (or all) — for tests / manual recovery."""
    with _LOCK:
        if key is None:
            _STATE.clear()
        else:
            _STATE.pop(key, None)
