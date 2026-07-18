"""Verify the Kie.ai key + discover the HappyHorse R2V model slug — ZERO COST.

A createTask with a wrong model slug fails with a clear error before any credit
is spent, so we probe candidate slugs until one is accepted, then IMMEDIATELY
report (we do NOT let the accepted task run unless --generate is passed).

Run:  ~/.pyenv/versions/3.12.3/bin/python3.12 tools/kie_probe.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=True)

import requests

KEY = os.environ.get("KIE_API_KEY", "")
if not KEY:
    sys.exit("KIE_API_KEY not in .env")
H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}

CANDIDATES = [
    "happyhorse-1-1/reference-to-video",
    "alibaba/happyhorse-1-1-r2v",
    "happyhorse/v1-1-r2v",
    "happyhorse-1-1-r2v",
    "alibaba/happyhorse-1.1-r2v",
]

# A deliberately incomplete input: if the MODEL is unknown we get a model error;
# if the model is KNOWN we get an input-validation error (which also teaches us
# the exact required field names) — either way, nothing generates, nothing spends.
probe_input = {"prompt": ""}

for slug in CANDIDATES:
    r = requests.post("https://api.kie.ai/api/v1/jobs/createTask", headers=H,
                      json={"model": slug, "input": probe_input}, timeout=30)
    body = r.text[:400]
    print(f"\n=== {slug} ===\nHTTP {r.status_code}: {body}")
    try:
        j = r.json()
        code, msg = j.get("code"), (j.get("msg") or j.get("message") or "")
        if r.ok and (j.get("data") or {}).get("taskId"):
            print(f"!! task actually created: {j['data']['taskId']} — check it didn't run")
    except Exception:
        pass
