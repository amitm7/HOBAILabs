# HOBAILabs — Storage & Matching Work Plan

> **Status (2026-07-03, per docs/L99_EXECUTION_AUDIT.md):** PARTIAL — P1/P2 shipped; P3 partial; P4 (CLIP) + P5 (eviction) deferred with triggers (L99_ARCH_PLAN §4b).

**Created:** 2026-06-09
**Scope:** the caching / DB / embeddings questions raised against the current
pipeline. **Companion:** [HLD.md](HLD.md) · [LLD.md](LLD.md)
**TL;DR of the analysis:** keep file caches; fix the one fragile JSON cache; make
caches survive the cloud; add CLIP embeddings only for multi-shot scoring; add a
real DB only for job state — never for blob caching; skip a vector DB until there's
a large cross-project library.

---

## Priority Summary

| # | Item | Effort | Value | When |
|---|---|---|---|---|
| **P1** | Description cache → SQLite (fix write race + growth) | S (~½ day) | High | ✅ **Done** (2026-06-09) |
| **P2** | Cloud-durable cache backend (S3 / persistent volume) | M (~2–3 days) | High | ✅ **Done** (2026-06-09) |
| **P3** | Durable job & run state (replaces in-memory `run_id`) | M (~3–4 days) | Med-High | Before multi-user |
| **P4** | CLIP embeddings + top-K multi-shot matching (Roadmap #15.3–4) | L (~1 wk) | Med | After P1–P2; needs GPU credits |
| **P5** | Cache eviction / TTL (cron + index) | S (~½ day) | Low-Med | When disk/S3 cost shows up |
| — | Vector database | — | — | **Deferred** — only if a large shared media library appears |

Each phase is independently shippable. **Do P1 first** — it's small, removes a real
correctness bug, and lays the SQLite groundwork P3/P5 reuse.

---

## P1 · Description Cache → SQLite

**Problem.** [image_matcher.py:55](../agents/image_matcher.py#L55) rewrites the
*entire* `~/.hob_cache/image_descriptions.json` on every change. Under the matcher's
thread pool this is a read-modify-write race (lost entries, last-writer-wins) and the
file grows unbounded.

**Goal.** Atomic, concurrency-safe, per-key writes; no full-file rewrite.

**Approach.** Replace the JSON file with a SQLite table behind the *same* two
functions — no caller changes.
```
descriptions(hash TEXT PRIMARY KEY, description TEXT, kind TEXT, created_at INTEGER)
```
- `sqlite3` with `PRAGMA journal_mode=WAL` (safe concurrent readers + one writer).
- `INSERT OR REPLACE` per key inside `describe_images` instead of `_save_cache`.
- **One-time migration:** on first run, if the old JSON exists, import it then rename
  to `.bak`.

**Files.** [agents/image_matcher.py](../agents/image_matcher.py) (`_load_cache`,
`_save_cache`, `describe_images`); optional new `agents/_kv.py` tiny SQLite helper
(reused by P3/P5).

**Risk.** Low. Pure internal swap; behaviour identical on cache hit/miss.

**Acceptance.**
- [ ] Concurrent describe of N images yields N rows, zero lost entries (test with a
      thread pool).
- [ ] Old JSON auto-imported once; subsequent runs read SQLite.
- [ ] `smart_match` end-to-end unchanged for a sample story.

---

## P2 · Cloud-Durable Cache Backend

**Problem.** All `~/.hob_cache/*` (paid Kling/fal/lip-sync clips, stills) lives
*inside the container*. With the existing [Dockerfile](../Dockerfile) / [deploy/](../deploy/),
every redeploy wipes the cache and a second instance shares nothing — defeating the
"never re-spend credits" principle.

**Goal.** Paid artifacts survive restarts and are shared across instances.

**Approach — pick by deployment shape:**
- **Single instance:** mount a **persistent volume** at `~/.hob_cache` (zero code).
  Smallest possible change — do this first if you're on one box.
- **Multi-instance / serverless:** introduce a **cache backend abstraction** with a
  local-FS impl (today) and an **S3/object-store** impl. The existing seams are already
  the right shape: `_cache_lookup` / `_cache_store`
  ([clip_builder.py:36](../agents/clip_builder.py#L36)) and the scene/lip-sync cache
  helpers. Add `agents/cache_store.py` with `get(key)->path|None` / `put(key, path)`,
  selected by env (`HOB_CACHE_BACKEND=fs|s3`). Local FS stays the default.
- Keep a **local read-through cache** in front of S3 so a warm container still hits disk.

**Files.** new `agents/cache_store.py`; wire `clip_builder`, `scene_intelligence`,
`lipsync_coordinator`. Stills (in the asset folder) are out of scope here — they're
user-folder artifacts, not `~/.hob_cache`.

**Risk.** Medium — must preserve the **legacy clip-cache key formats**
([clip_builder.py:59](../agents/clip_builder.py#L59)) so previously-paid clips still
hit. Object keys = existing MD5 hashes (already content-addressed → ideal for S3).

**Acceptance.**
- [ ] Redeploy/restart → cached clips still hit (no re-billing) — verified by log
      `[Cache] ✓ … reused`.
- [ ] Two instances share one cache.
- [ ] `HOB_CACHE_BACKEND=fs` reproduces today's behaviour exactly.

---

## P3 · Durable Job & Run State

**Problem.** Web runs are tracked in **in-memory `run_id` state** in
[web_app.py](../web_app.py); a restart loses in-flight runs and there's no history.
This is the one place "use a DB" is genuinely correct — and it is **not caching.**

**Goal.** Runs survive restarts; basic history/status queryable; foundation for
multi-user.

**Approach.** Start with **SQLite** (same dependency as P1); migrate to Postgres only
when multi-writer/horizontal scale demands it.
```
runs(run_id PK, status, created_at, finished_at, output_path, cost_estimate, error)
run_logs(run_id FK, ts, line)        -- optional; or keep logs on disk
```
- Persist on submit/transition; SSE `/progress` reads from the table (or tails a
  per-run log file referenced by the row).
- Keep the in-memory layer as a fast cache in front of the table.

**Files.** [web_app.py](../web_app.py) (`_LogCapture`, `_execute_pipeline`,
`/run`, `/progress`, `/output`, `/download`); reuse `agents/_kv.py`/a small DAL.

**Risk.** Medium — touches the request lifecycle. Ship behind the existing endpoints
with identical contracts.

**Acceptance.**
- [ ] Kill + restart the server mid-render → run status is recoverable (at least marked
      `interrupted`), not lost.
- [ ] `GET` history of recent runs.
- [ ] CLI path unaffected.

---

## P4 · CLIP Embeddings + Top-K Multi-Shot Matching

**Problem / opportunity.** The LLM matcher returns a *hard* assignment and can't give
**continuous scores**, which Roadmap **#15.3–4** needs for top-K multi-shot coverage
(cover one beat with a real clip + 1–2 B-roll stills). It also pays an LLM call per run.

**Goal.** Add fast, cheap, score-based matching that **complements** (not replaces)
the LLM matcher.

**Design notes (carry forward):**
- **Embeddings ≠ vector DB.** Encode media + beat text with CLIP; score with **cosine
  in numpy** (or **FAISS flat**). At folder scale (≤ a few hundred items) this is
  microseconds — **no vector database**.
- **Cache embeddings on disk** as `.npy` keyed by content hash (same pattern as the
  description cache) — encode each media item once, forever.
- **Keep the LLM matcher** for its unique strength: reading **text/names inside images**
  (e.g. a "Nima Denzongpa" caption → that beat), which CLIP is weak at. Use CLIP for
  scores + thresholding, LLM for literal-text/emotional tie-breaks.
- **Global assignment:** top-K above a quality floor, **no duplicates across beats**,
  gated by availability (only multi-shot a beat with ≥2 strong matches) — exactly as
  Roadmap #15.3 specifies (drops the old fixed-"80%" idea).
- **Compute:** CLIP runs **credit-funded on AWS GPU** per the roadmap; cache makes
  re-runs free.

**Dependencies.** Needs P1/P2 patterns (hash-keyed cache, durable store) and assembler
**sub-clip support** for multi-shot beats (per ROADMAP #15 status: "needs CLIP +
assembler sub-clip support").

**Files.** new `agents/clip_embeddings.py` (encode+cache+score); extend
[image_matcher.py](../agents/image_matcher.py) assignment to consume scores;
`assembler.py` sub-clip sequencing (wide→close, min sub-shot duration, one caption
spanning sub-shots — #15.4 editorial polish).

**Risk.** Medium-High — model/infra dependency; opt-in and cost-aware (~2× video
credits on multi-shot beats), so gate behind a flag.

**Acceptance.**
- [ ] Embeddings computed once per media item, cached, reused free on re-run.
- [ ] Score-based assignment matches or beats LLM-only on a labelled sample.
- [ ] Multi-shot beat renders ≥2 sub-clips under one caption; off by default.

---

## P5 · Cache Eviction / TTL

**Problem.** No cache ever expires; disk (or S3) grows forever.

**Goal.** Bounded cache cost without re-billing hot content.

**Approach.** A cleanup job (cron / scheduled task), **not** a DB-as-cache:
- Track `last_accessed` (touch on hit) in a small SQLite index (reuse P1 helper).
- Evict least-recently-used blobs past a size or age budget.
- Run via the existing scheduler tooling.

**Files.** `agents/cache_store.py` (touch-on-hit), new `scripts/cache_gc.py`.

**Risk.** Low. Conservative defaults; dry-run mode first.

**Acceptance.**
- [ ] LRU eviction respects a configurable size cap.
- [ ] Hot story re-renders still hit cache after a GC pass.

---

## Deferred · Vector Database

**Not planned.** Justified only by a **large, persistent, cross-project media library**
(thousands+ items) queried as a shared corpus — none exists today. If it ever does:
prefer **pgvector** on the Postgres introduced for P3 (one datastore, not a new
service) over a dedicated vector DB. Revisit only when P4's in-memory/FAISS scoring
measurably struggles.

---

## Suggested Sequence

```
P1 (SQLite desc cache)  ─┐
                         ├─► P3 (job state, reuses SQLite helper)
P2 (durable cache) ──────┘
        │
        └─► P4 (CLIP multi-shot)  ──►  P5 (eviction)
```

- **Week 1:** P1, then P2 (volume mount or S3 adapter depending on deploy).
- **Week 2:** P3 if multi-user/restart-resilience is near-term.
- **Later, GPU-credit-gated:** P4, then P5.
- **Vector DB:** only if/when a shared library materialises.

**Guiding rule (unchanged):** filesystem/object storage for blobs, SQLite→Postgres for
*state*, embeddings for *scoring*, vector DB for *large persistent corpora only*. Match
the tool to the data, not the hype.
</content>
