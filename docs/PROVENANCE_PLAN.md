# PROVENANCE_PLAN — C2PA Content Credentials & the Trust Layer

**Status:** SHIPPED (Slices A+B) · 2026-07-07 · owner: Amit — **Slice A:** per-frame
provenance rows + finalize-rewrite + C2PA signing (`agents/content_credential.py`) +
`/credential` route + export-zip artifacts. **Slice B:** decision log
(`degradation.decision` + `decisions.jsonl` + per-frame model enrichment incl. the
clip-builder Ken-Burns-fallback marker), `agents/source_media_review.py`
(pre-generation sha256+probe evidence), `schemas/provenance.schema.json` (jsonschema
gate at finalize; rejects path leaks; `HOB_SCHEMA_STRICT=1` raises), and
`governance.consent_evidence` (DB record ids/confirmed_by/confirmed_at in the
credential; **record_ids are strings** — c2pa's CBOR encoder mangles small-int arrays
into byte strings). All verified offline + full sign/read-back chain. Remaining:
live-render smoke (needs credits) and Phase-2 durability (hosted manifest).

## Context

The story-reel *generator* is commoditized (see the LTX / Open-Generative-AI / OpenMontage
reviews and the July-2026 market scan). The defensible layer is **provenance + consent +
compliance**: EU AI Act Article 50 makes machine-readable marking of AI content mandatory
from **2026-08-02** (fines to €15M / 3% turnover); platforms (TikTok, YouTube, Meta) already
surface C2PA Content Credentials. Every open-source competitor stripped this out to look
"unrestricted." HOBAILabs already tracks, per shot, what is real vs AI vs AI-likeness-of-a-
real-person (`agents/provenance.py` `classify_frame` → REAL / AI_SYMBOLIC / AI_PORTRAIT) and
records consent (`agents/governance.py`). We compute the truth but don't **emit** it in the
format the law and platforms now require.

**Goal:** sign every finished reel with a truthful, granular C2PA Content Credential built
from the provenance we already track — the one version of this no wrapper can fake, because
it requires internal per-shot provenance from the generation step.

**Moat line:** the signing is commoditized (`c2patool`, `sign-ai-media` exist — we rent it);
the *truthfulness* is the moat ("seconds 0–3 real+consented, 3–8 AI-symbolic, voice synthetic-
consented"). Rent the plumbing, build the truth.

## Locked decisions

1. **Trust anchor:** self-signed dev cert for the spike (credential present + structurally
   valid, issuer marked untrusted). Swap for a C2PA-trust-list CA cert later. No procurement
   blocking code.
2. **Slice shape:** thin vertical first — **Item 1 (per-frame provenance) + Item 6 (sign MP4)
   end-to-end**, then backfill Items 2–5.
3. **Ship target:** production increment into the live app — behind existing seams,
   degradation-safe, cached, docs-synced. Not a throwaway.

## Hard rules (inherit from build-feature)

- **Degradation-safe (rule 4/13):** signing is best-effort. Any failure →
  `degradation.report("provenance", "warn", …)` and the render still completes. A missing
  credential must never hard-fail a reel.
- **Rent the seam:** C2PA via `c2pa-python`; never hand-roll crypto. Cert/key/issuer come from
  env (`HOB_C2PA_CERT` / `HOB_C2PA_KEY` / `HOB_C2PA_ISSUER`), dev fallback = generate a self-
  signed cert in-process (mirror the `agents/auth._secret` dev-fallback pattern).
- **Backward compatible:** extend `provenance.summarize()` additively (new `frames` key);
  existing `/provenance` route, export bundle, and burn-in caption keep working.
- **Docs-sync gate (rule 11):** HLD + LLD + GUIDE/OPERATOR_GUIDE + this plan, same unit of work.

---

## Slice A — thin vertical (ships first): per-frame provenance → signed MP4

### A1 · Per-frame provenance record (Item 1)
- **`agents/provenance.py`:** add `classify_frames(frames: list[dict]) -> list[dict]`. Each row:
  `{frame_id, tier, photo_spec, real_person (bool), face (bool), voice (bool), source_path}`.
  Reuse existing `classify_frame` (`provenance.py:24`) for `tier`; `voice` from `frame["lipsync"]`.
- Extend `summarize()` (`provenance.py:36`) to include `"frames": classify_frames(frames)`
  alongside the existing aggregate keys — additive, no removals.
- **Persist at finalize, not dispatch.** Today `provenance.json` is written from the RAW
  pre-pipeline payload at `web_app.py:2818`, so it lacks resolved paths. Add a **rewrite** at
  the end of `_run_inner` (after assembly, `web_app.py:~4001+`, when frames carry resolved
  `visual_path`/`clip_path`) so the record reflects what was actually produced. Keep the early
  write as provisional.

### A6 · C2PA export (Item 6)
- **New module `agents/content_credential.py`:**
  `sign_reel(mp4_path, prov_summary, consent_records, *, cert, key, issuer) -> dict`.
  Builds a C2PA manifest and embeds it in the MP4; returns `{signed_path, manifest_path, ok}`.
  Assertions:
  - `c2pa.actions` — created + AI-generated markers (from `prov_summary["tier"]`/counts).
  - `com.veristory.provenance` (custom) — the per-frame `frames` rows.
  - training-mining consent flag (`allowed` / `notAllowed` / `constrained`).
  - consent assertion (Item 5 fills this; Slice A stubs it from `prov_summary`).
- **Cert:** `_load_or_make_cert()` — read `HOB_C2PA_*` env, else generate a self-signed cert
  in-process (dev). Document that this signs as an untrusted issuer (expected for the spike).
- **Insertion:** in the finalize path after assembly **and** after the existing silent-audio
  QC (`_canvas_render_thread` `web_app.py:~3746` / `_execute_pipeline` `:3637`). Wrap in
  try/except → `degradation.report("provenance","warn",…)`; on success write
  `content_credential.json` (human-readable) + the signed MP4 into `run_dir`.
- **Surface:** add signed MP4 + `content_credential.json` to the export ZIP
  (`web_app.py:2992`); extend `/provenance/<run_id>` (or add `/credential/<run_id>`) to return
  the credential summary.

### Deps / config
- Add `c2pa` to `requirements.txt`. **Verify early:** the Rust-backed wheel must install on
  `python:3.12-slim` (manylinux wheel expected). If MP4/BMFF embedding is flaky, fall back to a
  sidecar `.c2pa` manifest + JSON (still proves the data model) and embed later.
- `.env.example`: `HOB_C2PA_CERT`, `HOB_C2PA_KEY`, `HOB_C2PA_ISSUER`.

### Verify loop (Slice A)
1. `py_compile` provenance.py, content_credential.py, web_app.py.
2. Offline: unit-test `classify_frames` on a synthetic `frames[]` (assert per-frame tiers,
   incl. a real-video passthrough → REAL and an `ai_portrait` → AI_PORTRAIT + face=True).
3. Round-trip: build a 2s lavfi MP4, `sign_reel` with a self-signed cert, then read the
   manifest back (c2pa read / `c2patool`) → assert the three assertions are present.
4. E2E: Flask test client on a tiny render (or an existing `run_dir`) → assert signed MP4 +
   `content_credential.json` exist, `/provenance` returns `frames`, export ZIP includes both.

### Docs-sync (Slice A)
- **HLD:** new cross-cutting concern (content provenance/credentialing), new module, new
  external standard (C2PA); §6 decision record (why self-signed spike).
- **LLD:** `classify_frames`, `sign_reel` signatures; new `provenance.json` `frames` key;
  `content_credential.json` artifact; finalize-rewrite of provenance.json; `/credential` route.
- **GUIDE + OPERATOR_GUIDE:** export now includes a Content Credential; any "Verified" badge.
- **.env.example:** the three `HOB_C2PA_*` vars.

---

## Slice B — backfill (after Slice A ships)

- **Item 2 · Per-frame decision log** — give `degradation.report` an optional `frame_id`
  (`agents/degradation.py:53`); persist per-frame `{model, fell_back_from, reason, cache_hit,
  cost}` to `run_dir/decisions.jsonl` (source: `model_router.run_with_fallback`
  `model_router.py:162`, clip build keys `clip_builder.py:530`). Enriches provenance `frames`
  with the model actually used. Surface in Canvas `render_report`.
- **Item 3 · Source-media review** — `agents/source_media_review.py`: probe every asset before
  generation → `run_dir/source_media_review.json` (res/codec/duration). Insert between
  `_build_frames_from_payload` (`web_app.py:3873`) and `_generate_stills` (`:3882`). Reuse the
  ffprobe pattern in `assembler._probe_wh` (`assembler.py:161`). Block render if user supplied
  media but review never ran.
- **Item 4 · Formal schema** — `schemas/provenance.schema.json` (C2PA-assertion-shaped) +
  `frames.schema.json`; validate at finalize with `jsonschema` (add to requirements; `pydantic`
  is present-but-unused today). Fail loud in dev, warn+continue in prod.
- **Item 5 · Consent → assertion mapping** — pull rows from `governance.py` `consent_records`
  (subject, face/voice grant, confirmed_by, created_at) via a new read helper; attach to the
  credential's consent assertion with timestamps + operator. Replaces the Slice-A stub.

## Non-goals (this spike)

- Trusted CA cert / full C2PA trust-list membership (later).
- Cloud-hosted manifest durability against platform re-encode (Phase 2 — the known #1 gap).
- The provenance-stamped **clipper** (Item 7, Phase 2 — reuses this machinery on clips).
- Slideshow-risk scorer (Item 8) and floor-hardening backlog (Items 9–10+).

## De-risk result (2026-07-07) — PROVEN ✅

Ran the full round-trip in a scratch venv (`c2pa-python==0.36.0`): generated a cert chain,
built a 2s lavfi MP4, embedded a custom `com.veristory.provenance` assertion (3 per-frame rows
+ consent block + training-mining flag), read it back → `validation_state: **Valid**`, MP4
12.8KB→26.8KB. **BMFF/MP4 embedding works; no sidecar fallback needed.** Implementation gotchas
locked (bake these into `content_credential.py`):
- Package is **`c2pa-python`** (imports as `c2pa`), NOT `c2pa`.
- Private key must be **PKCS#8** (`BEGIN PRIVATE KEY`), not SEC1 (`BEGIN EC PRIVATE KEY`) →
  `openssl pkcs8 -topk8 -nocrypt`.
- Signing cert must be a **2-cert chain** (leaf + CA); a bare self-signed leaf is rejected
  ("the certificate was self-signed"). Generate a self-signed CA, sign the leaf with it.
- `ta_url` must be **non-empty bytes** = a real RFC-3161 TSA (e.g. `http://timestamp.digicert.com`).
  Empty → "Signature: empty string". **⇒ signing makes an outbound HTTPS call per render** —
  must be degradation-safe on TSA failure (offline/timeout falls back to warn + unsigned, never
  hard-fail). Consider caching / a fallback TSA list.
- Leaf cert needs EKU `emailProtection` + `keyUsage=digitalSignature`.

Still to verify (small): the wheel installs on `python:3.12-slim` in Docker (manylinux wheel
exists, expected fine) — confirm during A6.

## Red-team / risks

1. ~~`c2pa-python` MP4 embedding is the spike's real blocker~~ — **RESOLVED** (see de-risk
   result). Residual: TSA network dependency at sign time → handle degradation-safe.
2. **provenance.json written from raw pre-pipeline data** → fixed by the finalize-rewrite (A1).
3. **Re-encode strips the credential** (known industry gap) — out of scope; note it, solve in
   Phase 2 with a hosted manifest.
4. **Real-video passthrough frames** must classify as REAL even with empty `photo_spec` —
   `classify_frame` already defaults non-`ai_` specs to REAL; add a test to lock it.

## Reversal conditions

- If `c2pa-python` can't embed into MP4 on the target image → ship the sidecar-manifest variant
  for the spike; revisit embedding when we add the hosted-manifest durability layer.
- If the tool stops serving live operators (pure SaaS pivot) → Slice B's floor overlap drops in
  priority; the credential export (A) still stands as the moat deliverable.
