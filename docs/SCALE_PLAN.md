# HOBAILabs — Scale Plan (single-operator tool → multi-user product)

> **Status (2026-07-03, per docs/L99_EXECUTION_AUDIT.md):** PARTIAL — T0.2/T0.3/T0.4 shipped; OPEN: T0.1 S3 artifact copy, T0.5 Sentry, T0.6 backups (ops track in L99_ARCH_PLAN §4b).

**Created:** 2026-06-12
**Companion:** [HLD.md](HLD.md) · [LLD.md](LLD.md) · [WORK_PLAN.md](WORK_PLAN.md) (storage/matching)
**Scope:** durable job orchestration, product surface (projects/library/brand kits/
templates/export), reliability & ops, reusable asset/data layer — and the gating
prerequisite none of those work without: **identity + spend control**.

**Guiding principles**
1. **The caches are the checkpoint system.** Every expensive step is already
   content-hash cached (clips, scene designs, stills, lip-sync, descriptions).
   "Resume" = re-dispatch the same payload; paid work hits cache. Do NOT build a
   step-function state machine.
2. **One Postgres serves everything** (jobs, projects, ledger, assets). Blobs go
   to S3. No vector DB, no new datastore per feature (see WORK_PLAN's guiding rule).
3. **Sequence by dependency, not ambition.** Auth gates tenancy; the runs table
   gates observability; the worker split gates zero-downtime. Each phase ships alone.

---

## 0. Review verdict on the four pillars

| Pillar | Verdict | Correction |
|---|---|---|
| 1. Durable job orchestration | Right problem, over-shaped | Runs table + stored payload + re-dispatch first; queue tech (SQS/RQ) only at Phase 1; resumability via existing caches, not a workflow engine |
| 2. Projects / brand kits / templates / collab / export | The moat, but 5 products in one bullet | Slice by retention÷effort: projects+versions → multi-format → brand kits → templates (promote exemplars) → collab last |
| 3. Reliability & ops | Correct, cheaper than it sounds | Data durability this week (volume + S3 + RDS); zero-downtime falls out of the web/worker split; measure before promising an SLA |
| 4. Reusable assets & data | Yes — already half-built | Formalize the existing content-hash caches into an `assets` table; add consent/rights flags; replace the lip-sync CDN with first-party signed URLs |

**Missing prerequisite:** authN/Z + per-user budget caps. `session_id` today is a
client-chosen UUID (guessable); "who ran what / what it cost / fairness" do not
exist without identity, and money-spending jobs need a spend guard before
multi-user anything.

**Hidden engineering risk:** provider concurrency caps (Kling 4, Veo 2,
Higgsfield 4) are per API *account* but enforced by an in-process pool in
`clip_builder.build_clips`. Two workers = over-cap 429s. The worker split
requires a **distributed semaphore** (Redis) keyed per provider.

---

## 1. Target data model (one Postgres; introduced incrementally)

```
users          (id PK, email UNIQUE, created_at, plan, monthly_budget_usd)
projects       (id PK, user_id FK, name, brand_kit_id FK NULL, created_at)
brand_kits     (id PK, user_id FK, name, caption_style JSONB, logo_s3_key,
                voice_id, music_brief, palette JSONB)
reels          (id PK, project_id FK, name, script TEXT, created_at)
reel_versions  (id PK, reel_id FK, n, payload JSONB,        -- full /run payload snapshot
                output_s3_key, duration_sec, created_at)    -- re-render ≈ free via caches
runs           (id PK, reel_version_id FK NULL, user_id FK, status,        -- queued|running|done|error|interrupted
                started_at, finished_at, error TEXT, worker_id,
                idempotency_key)                            -- sha256(payload) — dedupe double-submits
run_logs       (run_id FK, ts, line)                        -- or S3 log file per run
cost_events    (id PK, run_id FK, user_id FK, ts, vendor,   -- kling|fal|openai|11labs|hedra|suno|…
                item,                                        -- clip|image|edit|scene|tts|lipsync|music
                usd NUMERIC, cached BOOL, frame_id)
assets         (sha256 PK, user_id FK, s3_key, kind,         -- photo|video|music|generated_still|clip
                description TEXT, embedding VECTOR NULL,     -- pgvector later (WORK_PLAN P4)
                consent_flag, uploaded_at, deleted_at NULL)
templates      (id PK, user_id FK NULL,                      -- NULL = house template
                exemplar JSONB, defaults JSONB, name)        -- promoted from exemplars/
```

Notes
- `reel_versions.payload` is the exact `/run` JSON — version history and
  reproducible re-renders for free; diffing two versions = diffing two JSONs.
- `cost_events` is written at the paid call sites (the `pricing.py` helpers and
  cache-store hits are the choke points); estimates stay in `pricing.estimate()`,
  actuals live here. `cached=true` rows record money *saved* — a marketing number.
- `assets.sha256` matches the hashes the pipeline already computes everywhere;
  the SQLite description cache migrates in as the `description` column.

---

## 2. Phases

```
Phase 0  Stop losing data + see what's happening        (~1-2 wk, no auth needed)
Phase 1  Identity → worker split → spend control        (~3-4 wk)
Phase 2  Product surface: projects, export, brand kits, asset library  (~4-6 wk)
Phase 3  Templates, collab, blue/green, CLIP matching   (ongoing)
```

### Phase 0 — durability + observability (tickets)

**T0.1 — Move run artifacts off tmpfs.**
`RUNS_DIR` → `HOB_RUNS_DIR` env (default `/var/hob/runs`), mounted persistent
volume in [deploy/](../deploy/). Outputs additionally copied to S3
(`runs/{run_id}/output.mp4`) reusing the `cache_store` S3 pattern.
*Accept:* reboot the instance mid-day → all finished outputs still downloadable.

**T0.2 — Runs table (WORK_PLAN P3, expanded).**
SQLite via the existing `agents/_kv.py` patterns now, schema forward-compatible
with the Postgres model above. Persist: status transitions, the full request
payload, error + traceback. `_LogCapture` appends to `run_logs` (or a per-run
file referenced by the row) instead of memory-only.
*Accept:* kill -9 the server mid-render → restart shows the run as
`interrupted` with its logs; `GET /runs` lists history.

**T0.3 — Re-dispatch = resume.**
"Retry" button/endpoint re-runs the stored payload. No new pipeline code —
verify the cache layer makes the second run cheap (it already should).
*Accept:* interrupt a 10-frame render after clips; retry completes paying ~$0
for already-generated clips (verified via T0.4 ledger).

**T0.4 — Cost ledger.**
`cost_events` writes at each paid call site + at cache hits (`cached=true`,
usd=what it *would* have cost). Surface per-run actuals next to the estimate in
the UI and in `GET /runs`.
*Accept:* a finished run shows estimate vs actual within one screen; a cached
re-run shows ≥90% `cached` rows.

**T0.5 — Error reporting + minimal metrics.**
Sentry (or equivalent) on web + pipeline exceptions; counters for runs
started/succeeded/failed and render wall-time. This is the data an SLA gets
written from later — measure ≥1 month before promising numbers.
*Accept:* a forced pipeline exception appears in Sentry with run_id context.

**T0.6 — Backups.**
Volume snapshots + S3 versioning on outputs/cache bucket; document restore in
deploy/README.
*Accept:* restore drill recovers a deleted run output.

### Phase 1 — identity, worker split, spend control

- **T1.1 Auth:** email magic-link or a managed provider (Clerk/Cognito).
  Sessions become server-issued; `/media`, `/runs`, assets scoped to the user.
- **T1.2 Postgres:** migrate T0.2 schema (mechanical — same shape).
- **T1.3 Worker extraction:** `_run_inner` already takes `(run_id, data, run_dir)`
  and is entry-point agnostic — move it behind a queue consumer. Start with a
  Postgres-backed queue or RQ on the same box; SQS when a second worker exists.
  Web tier becomes stateless (SSE reads run_logs from DB).
- **T1.4 Distributed provider semaphores:** Redis `INCR/EXPIRE` per provider
  replacing the in-process `min(max_concurrent)` pool cap — REQUIRED before
  running a second worker. Per-tenant fairness = round-robin pickup by user_id.
- **T1.5 Budget caps:** before dispatch, sum this month's `cost_events.usd` for
  the user against `monthly_budget_usd`; block with a clear message. This is the
  first multi-user feature, not the last.
- **T1.6 Completion notifications:** email/webhook on done|error — queue-world
  users won't keep an SSE tab open.

### Phase 2 — product surface (in retention-per-effort order)

- **T2.1 Projects + version history** (S): reels/reel_versions over the runs
  table; "duplicate & edit" any version; re-render old versions ≈ free.
- **T2.2 Multi-format export** (S): assembler/router are width×height
  parametrized already (`orientation`); add 1:1 — mainly caption margins/PlayRes
  in `caption_writer` + router aspect. One render per format, same stills/clips
  cache shared across formats where durations match.
- **T2.3 Brand kits** (M): named preset = `caption_style` + voice_id + music
  brief + palette hint + logo; logo = one overlay filter in the assembler.
- **T2.4 Asset library** (M): `assets` table over S3; dedupe by the hashes the
  pipeline already computes; migrate the description cache in; **consent/rights
  flag + hard delete** per asset (real people's family photos — deletion is a
  product feature); replace the Higgsfield CDN hop for lip-sync uploads with
  first-party S3 signed URLs (closes the HLD §8 risk).

### Phase 3 — leverage

- **T3.1 Templates = exemplars, promoted** (don't build a parallel concept:
  template = `exemplar.json` + default settings, user-ownable).
- **T3.2 Collaboration/roles** (depends on T1.1; multiplies every surface — last).
- **T3.3 Blue/green deploys** (nearly free post-T1.3: stateless web behind ALB,
  workers drain on SIGTERM).
- **T3.4 CLIP-scored matching** (WORK_PLAN P4; embeddings live in `assets`).

---

## 3. Explicit non-goals (for now)

- Workflow engines / step functions (caches + re-dispatch cover resume).
- SQS/Kafka before a second concurrent tenant exists.
- Vector DB (pgvector on the same Postgres if/when P4 needs it).
- Public SLA before one month of measured success-rate/latency data.
- Kubernetes — two EC2 roles (web, worker) behind an ALB carries this a long way.
