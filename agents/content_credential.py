"""Content Credentials (C2PA) — sign a finished reel with its provenance truth.

Embeds a cryptographically-signed C2PA manifest into the output MP4, carrying the
per-frame provenance rows (real / ai_symbolic / ai_portrait, face/voice likeness,
timecodes) from `agents.provenance` plus the consent record. This is the emit side
of the trust layer: we already *compute* what's real vs AI per shot — this makes
the finished file carry that truth in the format platforms (TikTok/YouTube/Meta
surface Content Credentials) and the EU AI Act Art. 50 (from 2026-08-02) expect.

Deliberately extractable (open-source candidate — see PROVENANCE_PLAN "Strategy B"):
stdlib + `c2pa-python` only. No Flask, no other agents/* imports. Callers own
degradation reporting; every public function returns rather than raises where
practical, and `sign_reel` returns {"ok": False, "error": ...} on any failure.

Signing gotchas this module encodes (proven in the 2026-07-07 de-risk spike):
  - package is `c2pa-python` (imports as `c2pa`), NOT the broken `c2pa` sdist
  - private key must be PKCS#8 ("BEGIN PRIVATE KEY"), not SEC1
  - cert must be a 2-cert chain (leaf + CA); a bare self-signed leaf is rejected
  - ta_url must be a real RFC-3161 TSA → signing makes ONE outbound HTTP call;
    we try a fallback list and fail soft (unsigned reel, never a failed render)

Env (see .env.example):
  HOB_C2PA_CERT / HOB_C2PA_KEY  — PEM paths (chain + PKCS#8 key). Unset → a
      self-signed dev chain is generated once via openssl and cached (credential
      verifies as structurally Valid but from an untrusted issuer — fine for dev;
      production swaps in a C2PA-trust-list cert with zero code change).
  HOB_C2PA_TSA       — comma-separated RFC-3161 TSA URLs (default digicert,sectigo)
  HOB_C2PA_DISABLED  — "1" skips signing entirely (ops kill-switch)
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

PROVENANCE_ASSERTION_LABEL = "com.veristory.provenance"
CLAIM_GENERATOR = "HOBAILabs-Veristory/0.1"

_TSA_DEFAULT = "http://timestamp.digicert.com,http://timestamp.sectigo.com"

# IPTC digitalSourceType vocabulary (what c2pa.actions declares about origin).
_SRC_COMPOSITE_AI = ("http://cv.iptc.org/newscodes/digitalsourcetype/"
                     "compositeWithTrainedAlgorithmicMedia")
_SRC_COMPOSITE = "http://cv.iptc.org/newscodes/digitalsourcetype/composite"


def is_disabled() -> bool:
    return os.environ.get("HOB_C2PA_DISABLED") == "1"


# ── signer material ────────────────────────────────────────────────────────────
def _dev_dir() -> Path:
    d = Path(os.environ.get("HOB_C2PA_DIR",
                            str(Path(tempfile.gettempdir()) / "hob_c2pa_dev")))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ensure_dev_chain() -> tuple[Path, Path]:
    """Generate (once) and cache a self-signed CA + leaf chain for dev signing.

    c2pa rejects a bare self-signed leaf, so we mint a CA and sign a leaf with the
    EKU (emailProtection) + keyUsage(digitalSignature) C2PA requires, then bundle
    leaf+CA into chain.pem and convert the key to PKCS#8.
    """
    d = _dev_dir()
    chain, key = d / "chain.pem", d / "leaf_key_pk8.pem"
    if chain.exists() and key.exists():
        return chain, key

    def run(*args):
        subprocess.run(args, check=True, capture_output=True, cwd=d)

    run("openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", "ca_key.pem")
    run("openssl", "req", "-new", "-x509", "-key", "ca_key.pem", "-out", "ca.pem",
        "-days", "3650", "-subj", "/CN=Veristory Dev CA/O=Veristory",
        "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    run("openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", "leaf_key.pem")
    run("openssl", "req", "-new", "-key", "leaf_key.pem", "-out", "leaf.csr",
        "-subj", "/CN=Veristory Dev Signer/O=Veristory")
    (d / "leaf.ext").write_text(
        "keyUsage=critical,digitalSignature\nextendedKeyUsage=emailProtection\n")
    run("openssl", "x509", "-req", "-in", "leaf.csr", "-CA", "ca.pem",
        "-CAkey", "ca_key.pem", "-CAcreateserial", "-out", "leaf.pem",
        "-days", "3650", "-extfile", "leaf.ext")
    run("openssl", "pkcs8", "-topk8", "-nocrypt", "-in", "leaf_key.pem",
        "-out", str(key))
    chain.write_bytes((d / "leaf.pem").read_bytes() + (d / "ca.pem").read_bytes())
    return chain, key


def load_signer_material() -> tuple[bytes, bytes, str]:
    """(cert_chain_pem, private_key_pem, source) — env-configured or dev fallback."""
    cert_path = os.environ.get("HOB_C2PA_CERT", "")
    key_path = os.environ.get("HOB_C2PA_KEY", "")
    if cert_path and key_path:
        return Path(cert_path).read_bytes(), Path(key_path).read_bytes(), "env"
    chain, key = _ensure_dev_chain()
    return chain.read_bytes(), key.read_bytes(), "dev-self-signed"


# ── manifest ───────────────────────────────────────────────────────────────────
def build_manifest(prov: dict, consent: dict | None = None, *,
                   title: str = "reel.mp4",
                   claim_generator: str = CLAIM_GENERATOR) -> dict:
    """C2PA manifest from a provenance summary (`provenance.summarize()` shape).

    Pure function — unit-testable, no I/O. The custom assertion carries the
    per-frame truth verbatim; c2pa.actions declares AI involvement at the level
    standard verifiers read.
    """
    counts = prov.get("counts") or {}
    any_ai = (counts.get("ai_symbolic", 0) + counts.get("ai_portrait", 0)) > 0
    provenance_data = {
        "tier": prov.get("tier"),
        "label": prov.get("label"),
        "subject": prov.get("subject") or None,
        "real_person_ai": bool(prov.get("real_person_ai")),
        "counts": counts,
        "frames": prov.get("frames") or [],
        "training_mining": "notAllowed",
    }
    if consent:
        provenance_data["consent"] = consent
    return {
        "claim_generator": claim_generator,
        "title": title,
        "assertions": [
            {"label": "c2pa.actions", "data": {"actions": [{
                "action": "c2pa.created",
                "digitalSourceType": _SRC_COMPOSITE_AI if any_ai else _SRC_COMPOSITE,
            }]}},
            {"label": PROVENANCE_ASSERTION_LABEL, "data": provenance_data},
        ],
    }


# ── signing ────────────────────────────────────────────────────────────────────
def sign_reel(mp4_path: str, prov: dict, consent: dict | None = None, *,
              out_path: str = "") -> dict:
    """Embed a signed Content Credential into the reel. Never raises.

    Returns {"ok": True, "signed_path", "issuer", "tsa"} or
            {"ok": False, "error"}.
    Signs to a temp file and os.replace()s over `out_path` (default: in place),
    so a failure can never leave a half-written output.mp4.
    """
    if is_disabled():
        return {"ok": False, "error": "disabled (HOB_C2PA_DISABLED=1)"}
    try:
        from c2pa import Builder, Signer, C2paSignerInfo, C2paSigningAlg
    except Exception as e:                      # wheel missing → soft skip
        return {"ok": False, "error": f"c2pa-python unavailable ({e})"}

    src = Path(mp4_path)
    if not src.is_file():
        return {"ok": False, "error": f"no such file: {mp4_path}"}
    dest = Path(out_path or mp4_path)
    try:
        cert, key, issuer = load_signer_material()
    except Exception as e:
        return {"ok": False, "error": f"signer material ({e})"}

    manifest = build_manifest(prov, consent, title=dest.name)
    tsas = [u.strip() for u in
            os.environ.get("HOB_C2PA_TSA", _TSA_DEFAULT).split(",") if u.strip()]
    last_err = "no TSA configured"
    for tsa in tsas:                            # one outbound HTTP call per attempt
        tmp = dest.with_suffix(".signing.mp4")
        try:
            info = C2paSignerInfo(alg=C2paSigningAlg.ES256, sign_cert=cert,
                                  private_key=key, ta_url=tsa.encode())
            Builder(manifest).sign_file(str(src), str(tmp), Signer.from_info(info))
            os.replace(tmp, dest)
            return {"ok": True, "signed_path": str(dest), "issuer": issuer, "tsa": tsa}
        except Exception as e:
            last_err = str(e)
            tmp.unlink(missing_ok=True)
    return {"ok": False, "error": f"signing failed on all TSAs ({last_err})"}


def read_credential(mp4_path: str) -> dict | None:
    """Read back the embedded credential summary (verification/UI), or None."""
    try:
        from c2pa import Reader
        with Reader(mp4_path) as r:
            data = json.loads(r.json())
        m = data["manifests"][data["active_manifest"]]
        prov = next((a["data"] for a in m.get("assertions", [])
                     if a.get("label") == PROVENANCE_ASSERTION_LABEL), None)
        return {"validation_state": data.get("validation_state"),
                "claim_generator": m.get("claim_generator"),
                "assertions": [a.get("label") for a in m.get("assertions", [])],
                "provenance": prov}
    except Exception:
        return None
