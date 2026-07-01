# Infra & Resilience Review — Kafka? Redis? Event bus? Circuit breakers?

> Artifact + red-team on whether to adopt distributed-infra patterns. Companion to
> `SCALE_PLAN.md` (which already rules on job orchestration + Redis + "no Kafka"). This
> doc adds the **red-team per technology** and the **circuit-breaker** angle SCALE_PLAN
> omits. No code — a decision record. Date: 2026-07-01.

## 0. The lens (don't skip)
This is all **invisible plumbing** (CLAUDE.md §7: *rent the invisible plumbing; build
deeply only on the moat*). The moat is the director brain + real-media passthrough +
governance — **not** the message bus. So the bar for adopting any of these is: *does a
real, present pain justify the operational cost?* Not "would a mature system have it."
Most infra ambition here is résumé-driven; match rigor to stakes.

## 1. Current reality (Observe)
- **One container**, gunicorn `-w 1 --threads 8`. In-memory `run_id → state`; renders are
  **in-process daemon threads**.
- **Durability:** SQLite/Postgres bridges (`run_store`, `governance`) already persist run
  metadata + payload. Content-hash caches (BlobCache, fs/S3) mean re-dispatch is cheap.
- **External vendors:** OpenAI, fal, Kling, Higgsfield, Veo/Seedance/Hailuo, ElevenLabs,
  Suno, Lyria (Gemini), Hedra, SyncLabs, Bedrock — **all wrapped in fallback chains**
  (premium→fallback→Kling→Ken Burns; lip-sync→still; any-LLM→prior behaviour).
- **Observed pains THIS session (the evidence base):**
  1. Server restart orphans in-flight render threads → stages stuck (band-aided with
     `_ACTIVE_RENDERS`/`_ACTIVE_JOBS` orphan-recovery — a symptom of in-memory jobs).
  2. Vendor latency spikes: seedream cold **120s+**, gpt-image **126s**, Lyria **429**.
     A single slow/hung vendor call blocks a worker thread for its full timeout.
  3. One worker → can't run a 2nd concurrent tenant without over-cap 429s on providers.

## 2. Red-team per technology (Orient → Decide)

| Tech | What it'd buy us | Red-team (why NOT / when) | Verdict |
|---|---|---|:--:|
| **Kafka** | Durable event log, replay, high-throughput multi-service streaming | We have **none** of Kafka's preconditions: no high event volume (dozens of renders/day, not thousands/sec), no multi-service topology, no need to replay an event log. Cost is enormous — brokers, partitions, consumer groups, KRaft/ZK ops — for a tool a couple of operators use. Textbook résumé-driven over-engineering. SCALE_PLAN already lists it as an **explicit non-goal**. | **NO** (revisit only if we become a multi-service platform) |
| **Redis** | (a) Shared state across workers/restarts; (b) **job queue** for long renders (kills the in-memory-thread/orphan problem *properly*); (c) **distributed per-provider semaphore** (the real blocker to a 2nd worker — SCALE_PLAN T1.4); (d) pub/sub for cross-worker SSE progress; (e) rate-limiting (Lyria 10/min, fal caps) | Justified, but **not yet** — it earns its keep the moment there's a **2nd worker or a reliability SLA**, not before. Until then a Postgres-backed queue + our caches cover re-dispatch (SCALE_PLAN Phase 0/1). Adopting Redis now = an extra always-on dependency for a single-box tool. | **YES — at Phase 1** (worker split), not today |
| **Event-based system** | Decouple producers/consumers; react to render-done, post-process, notify | We already have the *pragmatic slice* (SSE `clip_ready`/`stage_done`, per-stage reveal). The useful formalization is a **durable job queue + pub/sub** (Redis, above) — NOT event-sourcing (rebuild-state-from-log is overkill; our state is small + already persisted). | **Partial** — adopt queue+pubsub via Redis; skip event-sourcing |
| **Circuit breaker** | Stop hammering a **down/slow** vendor: trip after N consecutive failures/timeouts, fail *fast* to the fallback for a cooldown, auto-recover | Cheapest, highest near-term value, and the **one thing SCALE_PLAN misses**. Directly targets pain #2 (a hung vendor call blocking a thread for 180s). We already have *reactive* fallback per-call; a breaker adds *proactive* fail-fast + cooldown so we don't pay the timeout on every request while a vendor is down. **In-process, no new infra** (a dict of per-provider state); Redis-backed only when multi-worker. | **YES — now** (lightweight, in-process) |

## 3. The sequenced recommendation (Act)
1. **Now — Resilience hardening (days, no new infra).** Per-vendor **circuit breaker** +
   tight **timeouts** + **retry-with-jittered-backoff**, wrapping the existing client
   calls (`fal_client`, `llm`, `clip_builder`, `music_generator`, `upscaler`, lip-sync).
   Trip after K consecutive failures/timeouts → skip straight to the fallback for a
   cooldown window. Pairs with — doesn't replace — the fallback chains. **Highest ROI.**
2. **Phase 1 trigger (2nd worker OR client reliability SLA) — Redis.** Web/worker split
   with a **durable queue** (Postgres-backed or RQ) + **Redis per-provider semaphore**
   (SCALE_PLAN T1.4). This is what *properly* fixes the orphaned-render problem (durable
   jobs survive restarts; workers drain on SIGTERM) instead of the in-memory band-aid.
3. **Never (for now) — Kafka.** Explicit non-goal until a genuine multi-service,
   high-throughput streaming need exists. Don't.

**One-line summary:** *Circuit breakers now (cheap, fixes real hangs); Redis + a queue when
we split workers (the real durability fix); an event bus only in that pragmatic queue/
pub-sub form; Kafka never — until we're a different kind of company.*

## 4. Non-goals / cautions
- Don't adopt Redis "for caching" — our content-hash BlobCache (fs/S3) already covers
  paid-artifact caching better (survives redeploys, no TTL eviction of expensive renders).
- Don't build a custom breaker framework — a ~40-line per-provider breaker or a small lib
  (e.g. `pybreaker`) is enough; keep it a seam, not a platform.
- Measure before promising an SLA (SCALE_PLAN §0). The breaker gives us the failure
  signal to measure with.
