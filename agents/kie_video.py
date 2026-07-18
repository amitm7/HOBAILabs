"""
Kie.ai jobs-API video backend — HappyHorse R2V (reference-to-video) and any
future Kie-hosted model.

Why this exists (A/B verdict 2026-07-19, docs/COMPETITOR_GALLERI5_TEARDOWN.md):
galleri5's identity/location/motion win traces to ONE architectural difference —
their video model (`alibaba/happy-horse-v1-1-r2v`, Kie: HappyHorse 1.1 R2V)
generates video DIRECTLY from 1–9 reference images that define subject identity
(not the first frame), with native audio. Our ref→still→i2v chain bleeds
fidelity at each hop. This adapter closes that gap as a RENTED seam: the model
is a `config/models.json` entry (backend "kie", `kie_model` slug), never a fork.

API contract (docs.kie.ai — verified 2026-07-19):
  POST https://api.kie.ai/api/v1/jobs/createTask   {"model": ..., "input": {...}}
  GET  https://api.kie.ai/api/v1/jobs/recordInfo?taskId=...
  Auth: Bearer KIE_API_KEY. States: waiting|queuing|generating|success|fail.
  Result: data.resultJson (JSON STRING) → resultUrls[0]. URLs expire ~24h.

Reference images must be PUBLIC URLs — local paths are hosted via Higgsfield's
CloudFront uploader (already a dependency, key present). Best-effort per ref:
a failed upload drops that ref rather than the shot.
"""
from __future__ import annotations

import json
import os
import time

import requests

from agents import model_router

KIE_BASE = "https://api.kie.ai/api/v1/jobs"

# The exact Kie market slug for HappyHorse 1.1 R2V is config (models.json
# `kie_model`) — Kie's market slugs aren't in their public docs; the live probe
# in tools/kie_probe.py discovers/verifies it once per account. Never hardcode
# it here: a slug change is a config edit (build-feature rule 3).


def _headers() -> dict:
    key = os.environ.get("KIE_API_KEY", "")
    if not key:
        raise RuntimeError("KIE_API_KEY not set — add it to .env")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def host_refs(paths: list[str]) -> list[str]:
    """Local reference images → public URLs (Higgsfield CloudFront uploader).
    Best-effort per ref: one failed upload drops that ref, never the shot."""
    urls = []
    for p in paths:
        if not p or not os.path.exists(p):
            continue
        try:
            from agents.higgsfield import _upload_image
            urls.append(_upload_image(p))
        except Exception as e:
            print(f"[Kie] ref hosting failed for {os.path.basename(p)} ({e}) — ref dropped")
    return urls


def submit(model_id: str, ref_urls: list[str], prompt: str, *,
           duration: int = 5, aspect_ratio: str = "16:9",
           resolution: str = "720p") -> str:
    """Create a generation task; returns the Kie taskId."""
    kie_model = model_router.model_field(model_id, "kie_model")
    if not kie_model:
        raise RuntimeError(f"no kie_model configured for '{model_id}' in config/models.json")
    # Contract per docs.kie.ai/market/happyhorse-1-1/reference-to-video
    # (verified 2026-07-19): `reference_image` = up to 9 URLs defining identity;
    # the prompt may address them as "[Image 1]", "[Image 2]", … in order.
    payload = {
        "model": kie_model,
        "input": {
            "prompt":          prompt,
            "reference_image": ref_urls[:9],
            "duration":        max(3, min(15, int(duration))),
            "aspect_ratio":    aspect_ratio,
            "resolution":      resolution,
        },
    }
    r = requests.post(f"{KIE_BASE}/createTask", headers=_headers(),
                      json=payload, timeout=60)
    if not r.ok:
        raise RuntimeError(f"Kie createTask failed {r.status_code}: {r.text[:300]}")
    data = r.json().get("data") or {}
    task_id = data.get("taskId") or r.json().get("taskId")
    if not task_id:
        raise RuntimeError(f"Kie createTask returned no taskId: {r.text[:300]}")
    print(f"[Kie] Submitted → {task_id} ({kie_model}, {duration}s {resolution} {aspect_ratio}, "
          f"{len(ref_urls)} ref(s))")
    return task_id


def poll_and_download(task_id: str, out_path: str, *, timeout: int = 900,
                      poll_sec: int = 5) -> str:
    """Poll recordInfo until success/fail; download resultUrls[0] to out_path."""
    start = time.time()
    while True:
        r = requests.get(f"{KIE_BASE}/recordInfo", headers=_headers(),
                         params={"taskId": task_id}, timeout=30)
        if not r.ok:
            raise RuntimeError(f"Kie recordInfo failed {r.status_code}: {r.text[:200]}")
        data = r.json().get("data") or {}
        state = (data.get("state") or "").lower()
        if state == "success":
            rj = data.get("resultJson") or "{}"
            if isinstance(rj, str):
                rj = json.loads(rj or "{}")
            urls = rj.get("resultUrls") or []
            if not urls:
                raise RuntimeError(f"Kie task {task_id} succeeded but returned no resultUrls")
            resp = requests.get(urls[0], timeout=300)
            resp.raise_for_status()
            with open(out_path, "wb") as f:
                f.write(resp.content)
            print(f"[Kie] ✓ {task_id[:12]}… → {out_path}")
            return out_path
        if state == "fail":
            raise RuntimeError(f"Kie task failed: {data.get('failMsg') or data.get('failCode')}")
        waited = time.time() - start
        if waited > timeout:
            raise RuntimeError(f"Kie task {task_id} timed out after {int(waited)}s")
        if int(waited) % 30 < poll_sec:
            print(f"[Kie] {task_id[:12]}… {state or 'pending'} ({int(waited)}s)…")
        time.sleep(poll_sec)
