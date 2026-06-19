"""
Upfront AI-vendor balance / credit probes.

Goal: before a render, show the operator how much credit is left on each paid
vendor so they can recharge proactively instead of discovering a dead key
mid-run. Vendors are wildly uneven about exposing balance, so each probe is
**best-effort and independent**:

  status = "ok"          → got a real number (balance + unit populated)
         | "no_key"      → the API key/secret isn't configured
         | "unsupported" → the vendor has no usable balance API (documented why)
         | "error"       → key present but the call failed (detail has the reason)

Every probe has a short timeout and is wrapped so one slow/dead vendor can never
hang the others — `all_balances()` runs them concurrently. This is read-only
(no spend); pairs with `agents/governance.py` (the per-project spend ledger) for
the "will this render need a recharge?" pre-flight check.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

_TIMEOUT = 10  # seconds per vendor HTTP call


def _res(vendor: str, label: str, status: str, *,
         balance=None, unit: str = "", detail: str = "") -> dict:
    return {"vendor": vendor, "label": label, "status": status,
            "balance": balance, "unit": unit, "detail": detail}


# ── ElevenLabs (voice) — SUPPORTED: character quota ───────────────────────────
def _elevenlabs() -> dict:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        return _res("elevenlabs", "ElevenLabs (voice)", "no_key")
    try:
        r = requests.get("https://api.elevenlabs.io/v1/user/subscription",
                         headers={"xi-api-key": key}, timeout=_TIMEOUT)
        r.raise_for_status()
        d = r.json()
        used = int(d.get("character_count", 0))
        lim = int(d.get("character_limit", 0))
        remaining = max(0, lim - used)
        tier = d.get("tier", "?")
        return _res("elevenlabs", "ElevenLabs (voice)", "ok",
                    balance=remaining, unit="characters",
                    detail=f"{remaining:,} of {lim:,} chars left · tier {tier}")
    except Exception as e:
        return _res("elevenlabs", "ElevenLabs (voice)", "error", detail=str(e)[:200])


# ── Suno (music) via api.sunoapi.org — SUPPORTED on the wrapper ───────────────
def _suno() -> dict:
    key = os.environ.get("SUNO_API_KEY")
    if not key:
        return _res("suno", "Suno (music)", "no_key")
    try:
        r = requests.get("https://api.sunoapi.org/api/v1/generate/credit",
                         headers={"Authorization": f"Bearer {key}"}, timeout=_TIMEOUT)
        r.raise_for_status()
        d = r.json()
        credits = d.get("data")
        if isinstance(credits, dict):  # some builds wrap it
            credits = credits.get("credits") or credits.get("credit")
        if credits is None:
            return _res("suno", "Suno (music)", "error", detail=f"unexpected shape: {str(d)[:150]}")
        return _res("suno", "Suno (music)", "ok", balance=credits, unit="credits",
                    detail=f"{credits} credits left (api.sunoapi.org)")
    except Exception as e:
        return _res("suno", "Suno (music)", "error", detail=str(e)[:200])


# ── Kling (video) — try the account costs endpoint (plan-dependent) ───────────
def _kling() -> dict:
    ak = os.environ.get("KLING_ACCESS_KEY")
    sk = os.environ.get("KLING_SECRET_KEY")
    if not (ak and sk):
        return _res("kling", "Kling (video)", "no_key")
    try:
        import jwt
        now = int(time.time())
        token = jwt.encode({"iss": ak, "exp": now + 1800, "nbf": now - 5},
                           sk, algorithm="HS256")
        # /account/costs needs a window (epoch ms); resource packs come back with
        # remaining_quantity on prepaid plans.
        end_ms = now * 1000
        start_ms = end_ms - 30 * 24 * 3600 * 1000
        r = requests.get(
            "https://api.klingai.com/account/costs",
            headers={"Authorization": f"Bearer {token}"},
            params={"start_time": start_ms, "end_time": end_ms},
            timeout=_TIMEOUT)
        r.raise_for_status()
        d = r.json()
        packs = (d.get("data") or {}).get("resource_pack_subscribe_infos") or []
        remaining = sum(float(p.get("remaining_quantity", 0)) for p in packs)
        if not packs:
            return _res("kling", "Kling (video)", "unsupported",
                        detail="account has no prepaid resource packs to report (pay-as-you-go)")
        return _res("kling", "Kling (video)", "ok", balance=remaining, unit="credits",
                    detail=f"{remaining:g} credits across {len(packs)} pack(s)")
    except Exception as e:
        return _res("kling", "Kling (video)", "error", detail=str(e)[:200])


# ── Higgsfield (video) — best-effort probe of likely account endpoints ────────
def _higgsfield() -> dict:
    kid = os.environ.get("HF_API_KEY") or os.environ.get("HIGGSFIELD_KEY_ID", "")
    sec = os.environ.get("HF_API_SECRET") or os.environ.get("HIGGSFIELD_KEY_SECRET", "")
    if not (kid and sec):
        return _res("higgsfield", "Higgsfield (video)", "no_key")
    headers = {"Authorization": f"Key {kid}:{sec}"}
    for path in ("/v1/account/balance", "/v1/balance", "/v1/account"):
        try:
            r = requests.get(f"https://platform.higgsfield.ai{path}",
                             headers=headers, timeout=_TIMEOUT)
            if r.status_code == 404:
                continue
            r.raise_for_status()
            d = r.json()
            bal = (d.get("balance") if isinstance(d, dict) else None)
            if bal is None and isinstance(d, dict):
                bal = (d.get("data") or {}).get("balance") or (d.get("data") or {}).get("credits")
            if bal is not None:
                return _res("higgsfield", "Higgsfield (video)", "ok", balance=bal,
                            unit="credits", detail=f"{bal} credits ({path})")
        except Exception:
            continue
    return _res("higgsfield", "Higgsfield (video)", "unsupported",
                detail="no public REST balance endpoint on platform.higgsfield.ai (check the dashboard)")


# ── fal.ai (image+video) — best-effort; otherwise pay-as-you-go ───────────────
def _fal() -> dict:
    key = os.environ.get("FAL_API_KEY")
    if not key:
        return _res("fal", "fal.ai (image/video)", "no_key")
    try:
        r = requests.get("https://rest.alpha.fal.ai/billing/user_balance",
                         headers={"Authorization": f"Key {key}"}, timeout=_TIMEOUT)
        if r.ok:
            d = r.json()
            bal = d if isinstance(d, (int, float)) else (d.get("balance") if isinstance(d, dict) else None)
            if bal is not None:
                return _res("fal", "fal.ai (image/video)", "ok", balance=round(float(bal), 2),
                            unit="USD", detail=f"${float(bal):.2f} balance")
    except Exception:
        pass
    return _res("fal", "fal.ai (image/video)", "unsupported",
                detail="pay-as-you-go; no stable public balance API — track via the spend ledger")


# ── Vendors with no usable balance API (documented, not probed) ───────────────
def _unsupported_static() -> list[dict]:
    out = []
    if os.environ.get("OPENAI_API_KEY"):
        out.append(_res("openai", "OpenAI (LLM/gpt-image)", "unsupported",
                        detail="programmatic billing/credit reads were removed — use the OpenAI dashboard"))
    if os.environ.get("GEMINI_API_KEY"):
        out.append(_res("gemini", "Gemini (LLM)", "unsupported",
                        detail="no balance API — billed via Google Cloud"))
    if os.environ.get("HEDRA_API_KEY"):
        out.append(_res("hedra", "Hedra (lip-sync)", "unsupported",
                        detail="no public balance API — check the Hedra dashboard"))
    if os.environ.get("SYNCLABS_API_KEY"):
        out.append(_res("synclabs", "SyncLabs (lip-sync)", "unsupported",
                        detail="no public balance API — check the sync.so dashboard"))
    if (os.environ.get("LLM_PROVIDER", "").lower() == "bedrock"):
        out.append(_res("bedrock", "AWS Bedrock (LLM)", "unsupported",
                        detail="IAM-billed via AWS — no per-key credit balance; see AWS Cost Explorer"))
    return out


_PROBES = [_elevenlabs, _suno, _kling, _higgsfield, _fal]


def all_balances() -> list[dict]:
    """Probe every vendor concurrently; never raises. Returns one row per vendor."""
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(_PROBES)) as ex:
        futures = {ex.submit(p): p for p in _PROBES}
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as e:
                results.append(_res("unknown", "unknown", "error", detail=str(e)[:200]))
    results.extend(_unsupported_static())
    # Stable order: ok first (actionable), then error, no_key, unsupported.
    rank = {"ok": 0, "error": 1, "no_key": 2, "unsupported": 3}
    results.sort(key=lambda r: (rank.get(r["status"], 9), r["label"]))
    return results


if __name__ == "__main__":   # quick CLI: `python -m agents.balances`
    from dotenv import load_dotenv
    load_dotenv(".env", override=True)
    rows = all_balances()
    ok = sum(1 for r in rows if r["status"] == "ok")
    print(f"\nAI-vendor balances — {ok}/{len(rows)} returned a live number\n" + "─" * 60)
    for r in rows:
        mark = {"ok": "✓", "error": "✗", "no_key": "·", "unsupported": "—"}.get(r["status"], "?")
        bal = f"{r['balance']} {r['unit']}" if r["status"] == "ok" else r["status"]
        print(f" {mark} {r['label']:<28} {bal}")
        if r["detail"]:
            print(f"     ↳ {r['detail']}")
