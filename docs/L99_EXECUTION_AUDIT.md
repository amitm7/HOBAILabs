# L99 Execution Audit — planned vs. actually executed

**Created:** 2026-07-03 · **Method:** 3 parallel code-vs-doc audit agents over 15 planning
artifacts + independent verification of contested items + git/test/infra checks.
**Role lens:** Project Designer. **This is a CHECK artifact** — it measures the gap between
what the project's plans promise and what the code actually does.

---

## 1. Aggregate scorecard

| Audit slice | Items | Shipped | Partial | Not-built |
|---|---|---|---|---|
| Backlogs/roadmap (GAP_BACKLOG, ROADMAP, PRODUCT_IDEAS, WORK_PLAN) | 57 | 27 | 12 | 18 |
| Mode/scale plans (MODE3, BRAND, SCALE, AGENTIC_CANVAS, PARITY_BACKLOG, UI_REDESIGN) | 54 | 33* | 11* | 10* |
| Feature/review plans (AI_FICTION, CEO_LIKENESS, CHAR_RETRIEVAL, STORY_REVIEW, INFRA, LADDER, MARKET_FIT) | 45 | 26 | 7 | 11 |
| This week (L99_ARCH_PLAN B1/B2/T1/T2/T4/T11/T12 + canvas fixes + Remotion spike) | ~25 | ~23 | 2 (T6 wiring, T14/T13 planned) | — |
| **TOTAL (approx, deduplicated)** | **~170** | **~105 (62%)** | **~30 (18%)** | **~35 (20%)** |

\* corrected — see §2; the agent under-counted because the parity backlog doc is stale.

**Verdict:** execution maturity ~60–65%. The *moat* work (real-media ladder, governance,
consent, cost gates, canvas, cast/identity) is largely SHIPPED. The gaps cluster in three
places: safety-enforcement follow-through (CEO-likeness fixes), routed-but-never-wired
modules, and P2 "someday" features correctly deferred.

## 2. Corrections to the raw audit (verified against code this session)

`CANVAS_STORY_PARITY_BACKLOG.md` is stale enough that it misled the auditor. These items
are marked NOT-BUILT there but are **SHIPPED** (verified by direct read/edit this week):
per-shot **re-match** (`/api/canvas/<id>/rematch`), **match-confidence flags**
(`/check-matches` + `match_flag` + ⚠️ UI), **storyboard pencil-art** (`/storyboard-art`,
parallel panels, cached), **caption styling controls** (canvas caption bar: on/off,
position, font, size, color, lines), **per-shot duration edit** (+ `/redistribute`),
**orientation picker** (9:16/16:9/1:1). Still genuinely not built from that doc: camera-move
dropdown vocabulary, canvas model picker, B-1 re-derive-prompt-on-reroll, hover match
descriptions. → **The doc must be updated or retired; a stale backlog is disinformation.**

## 3. RED TEAM — the findings that matter (Project Designer lens)

### 🔴 F1 · The week's work is UNCOMMITTED (top severity, not in any doc)
14 modified files (+529/−100) + `agents/degradation.py` + 3 new plan docs sit in the
working tree on `main`, zero commits. Everything from the silent-audio root-cause to the
degradation ledger is one `git checkout .` or disk failure from gone. **Execution isn't
"done" until committed.** Action: commit (or branch+commit) immediately; owner's call.

### 🔴 F2 · Safety enforcement promised but not wired (CEO_LIKENESS §5.1–5.3, 0/5 shipped)
The plan that green-lights CEO voice/face content lists five pre-ship fixes; **none are
enforced in code**:
- **5.1** provenance label is a JSON sidecar, **never burned into the MP4** — an AI-voiced
  CEO reel would publish with zero visible disclosure;
- **5.2** brand announcer script goes to TTS **without `safety.moderate_*`** — this
  contradicts CLAUDE.md §5's stated invariant ("AI-drafted copy must pass moderate_*");
- **5.3** `[edit:]` on a real face regenerates it but leaves `photo_spec` = REAL — a
  synthetic face labeled authentic, violating the core real-media invariant.
These are cheap (S/S/M) and gate ALL CEO-likeness content. If CEO content is on the menu,
they're P0; if not, mark the plan BLOCKED-BY-DESIGN so no one ships against it.

### 🟠 F3 · Built-but-never-routed: the dead quality gate (STORY_REVIEW P2)
`story_review.contract_validate()` exists, has 8 checks, has passing unit tests — and is
**called from zero routes**. The gate the plan was written for never fires. Worst gap
class: cost paid, value zero. Wiring it into /story-intake + preview is S effort.

### 🟠 F4 · Pipeline order contradicts its own plan (CHAR_RETRIEVAL C1)
CLI runs smart_match **before** cast detection (plan says after) → matcher is role-blind
on first pass; the ranker workaround only helps post-hoc, and old description-cache
entries predate relationship info. Fix: reorder + bump description cache version (S).
(Canvas flow is fine — cast now derives at Plan time.)

### 🟠 F5 · Placeholder endpoints masquerading as features
`/hook-workshop` returns `confidence: "placeholder"`; `/render-variants` returns payload
descriptors, not renders; `/caption-variants` translates text but the "dubbing" story
(GAP #7 / STR-4) was never built. These are honest scaffolds in code but read as shipped
capabilities in doc summaries. Label them PILOT/SCAFFOLD in docs, or finish them (T13
covers the language half properly).

### 🟡 F6 · Documentation drift, in both directions
- **BRAND_PLAN** header says "execution NOT started" — B1.1–B1.8 are substantially shipped
  (stale-safe, but erodes doc trust).
- **CLAUDE.md §9** says the feedback loop is "being produced by a cloud PR" — it's shipped.
- **GAP_BACKLOG / ROADMAP / PRODUCT_IDEAS** untouched since 06-24 while major landings
  happened; **WORK_PLAN** since 06-10.
- **PARITY_BACKLOG** actively wrong (§2).
Rule to adopt: every plan doc carries a dated `Status:` header line, updated in the same
unit of work as the code (the docs-sync gate already demands this — enforce it for plan
docs too, not just HLD/LLD/guides).

### 🟡 F7 · Verification asymmetry
56/56 unit tests pass, but coverage concentrates on money/rights/text logic. Zero coverage:
canvas JS (~1.4k lines), pipeline end-to-end, UI regressions (found by hand-screenshotting
this week). No CI gate — tests only run when someone remembers. T10 (screenshot harness)
+ a pre-commit pytest hook are the cheap fixes.

### 🟡 F8 · Repo hygiene — RESOLVED 2026-07-03
- `memory.md`: **KEPT** — on inspection it's an intentional compatibility pointer to
  CLAUDE.md (the agent's original "remove" call was wrong; corrected here).
- `SKILL.md`: **MOVED** → `docs/reference/AI_REEL_SKILL_REFERENCE.md` (generic unmapped
  template; even referenced a FastAPI architecture this repo doesn't have).
- `build-feature` skill (both copies, kept in sync): **UPDATED** with conventions 12–16
  (variant/redo cache semantics, degradation-report duty, plan-doc Status headers,
  canvas theme-scope rule, `_canvas_mutate` job-write rule).
- 17 plan docs with overlapping items and no single index — L99_ARCH_PLAN §0 ledger is
  now the de-facto index; keep it that way.

## 4. Consolidated NOT-BUILT list (deduplicated, decision per item)

| Item (sources) | Verdict | Rationale |
|---|---|---|
| CEO-likeness fixes 5.1/5.2/5.3 | **SCHEDULE if CEO content planned, else mark BLOCKED** | Safety-relevant, cheap |
| story_review route + UI gate (P2/P3) | **SCHEDULE (S)** | Module already built+tested |
| C1 cast-before-match reorder + cache bump | **SCHEDULE (S)** | Moat quality, CLI path |
| Language/dubbing (STR-4, GAP#7) | **SCHEDULED → T13** | Plan exists (FRAME_COMPOSER_PLAN B) |
| Take history / regen affordance | **SCHEDULED → T5** | Already queued |
| Remotion caption wiring | **SCHEDULED → T6** | Spike proven |
| Multi-platform export / smart reframe (ROADMAP#9) | **DEFER** | No current demand signal |
| Batch production mode (ROADMAP#11) | **DEFER** | Single-operator reality |
| CLIP embeddings / top-K (P4, C5, STR-7) | **DEFER** | LLM matcher adequate at current scale |
| Cache eviction/TTL (P5) | **DEFER, add disk-usage check to ops** | Not yet painful |
| Redis/durable queue (INFRA Phase 1) | **DEFER to SCALE trigger** | Per its own trigger condition |
| Virality scoring (STR-5, GAP#9) | **DEFER or wire to external predictor** | Placeholder is misleading (F5) |
| S3 artifact backup (T0.1/T0.6) | **SCHEDULE (ops, S)** | Data-loss exposure grows with usage |
| LoRA-per-character (P3), Agent Room (P6), Kafka | **KEEP DEFERRED** | Correctly parked |
| Camera-move dropdown, canvas model picker, hover descriptions | **BACKLOG (small UX)** | Low impact now |

## 5. Proposed next actions (in order)
1. **A0 — Commit the working tree** (owner decision on message/branch). Everything else is moot if this is lost.
2. **A1 — Doc-truth pass (S):** fix BRAND_PLAN header, CLAUDE.md §9 feedback-loop line, retire/refresh PARITY_BACKLOG, delete `memory.md`, relocate `SKILL.md`, add `Status:` headers to plan docs.
3. **A2 — Route the dead gate (S):** wire `contract_validate` into story-intake/preview + a warnings panel (finishes STORY_REVIEW P2/P3).
4. **A3 — C1 reorder + description-cache version bump (S).**
5. **A4 — CEO-likeness decision:** owner picks SCHEDULE (build 5.2→5.3→5.1) or BLOCKED (annotate plan, no CEO content until then).
6. Then resume the feature queue: T3 → T5 → T13 → T14 → T6 (per L99_ARCH_PLAN P1).

## 6. Red-team of THIS audit (self-check)
- Agent evidence is grep-based — a symbol existing ≠ feature working end-to-end; "SHIPPED"
  here means "code present and plausibly wired," not "verified live" except where this
  session verified directly. Items marked shipped by agents but never exercised (e.g.
  synclabs/hedra lipsync, auth JWT flow, Postgres cutover) carry residual risk — flagged,
  not cleared.
- The three agents didn't cross-talk; dedup in §4 is mine. Some overlap classification
  (STR-7 vs P4 vs C5) is a judgment call.
- Recency bias check: this audit intentionally counterweights the last-72h focus by
  covering all historical plans; the C1/story-review findings pre-date this week.
