# L99 Architecture Review & Hardening Plan

**Created:** 2026-07-03 · **Method:** L99 (research → OODA → artifact → red-team plan → build+verify → red-team diff → docs-sync)
**Scope:** Director Canvas + engine, after the 2026-07-01..03 incident/fix cycle (silent reel → six-dimension quality pass → operator field-testing).
**Status:** PLAN — red-teamed below; nothing here is built until each ticket goes through its own build+verify loop.

---

## 0. Suggestion ledger (owner inputs, last 3 sessions — nothing dropped)

| # | Suggestion / observation | Status |
|---|---|---|
| S1 | Silent reel = useless (CEO review) | ✅ shipped — root cause (Suno credits) + `audio_warning` + output silence probe |
| S2 | 141s too long; keep duration control in user's hands | ✅ shipped — default ~60s, ALL options kept, no server cap |
| S3 | Captions tiny/unreadable | ✅ shipped — Baskerville 52 / 2-line defaults |
| S4 | Rotated photo / watermark / newspaper full-frame | ✅ shipped — EXIF-upright at ingest + Review vision flags |
| S5 | Kling default (burn credits), auto later | ✅ shipped — config-only; revert recipe documented |
| S6 | VO + mild background music together | ✅ shipped — bed ducked under narration, one render |
| S7 | galleri5: characters-first for AI stories | ✅ shipped — auto-derive cast at Plan + 🎨 all faces + soft gate |
| S8 | Character portrait too small / no expand | ✅ shipped — 76px + click-to-full-size |
| S9 | No redo on generated face / cache returned old image | ✅ shipped — ↻ New face + cache `variant` + attrs sent along |
| S10 | Cast button "did nothing" | ✅ shipped — scroll + flash feedback |
| S11 | Shot-level image can't be changed (shot 14) | ⚠ partial — works via prompt-edit→Re-roll, but flow is non-obvious → **T5** |
| S12 | Auto-fill settings from the story, operator edits | 📋 planned → **T3** |
| S13 | Per-character voices; lip-sync for cinematic dialogue | 📋 planned → **T4** (voices) / **T7** (selective lip-sync) |
| S14 | Text↔frame mismatch (CEO review, P2 ladder) | 📋 planned → **T8** |
| S15 | Runway-style iteration: don't lose good takes | 📋 planned → **T5** (take history) |
| S16 | Remotion caption layer (spike verified) | 📋 planned → **T6** (P1 wiring per REMOTION_CAPTIONS_PLAN) |
| S17 | Compare vs galleri5 Mahabharat trailer | 🎯 pending the Hanuman build — measured after P1 tickets |
| S18 | Hanuman run: "captions didn't appear" | ✅ root-caused — captions WERE burned (bottom, defaults) but operator's pre-Plan settings (position=Middle etc.) were **silently dropped** (saveSettings early-returns with no run). Fixed: Plan now pushes the pre-set UI settings onto the new canvas. Legibility-vs-benchmark gap remains → **T6** |
| S19 | Two characters locked, but CLOTHES keep changing between shots | 📋 → **T11** — canonical portrait is head-and-shoulders, so identity conditioning anchors the FACE only; clothing exists only as a text clause per shot → drifts |
| S20 | Hanuman's tail morphs (snake ↔ monkey ↔ divine) between shots | 📋 → **T11** — no species/anatomy attribute propagates ("vanara with a long monkey tail" must ride every prompt), and no anatomy-consistency QC flag |
| S21 | Bhima "slipping" clip broken — stuck floating mid-air | 📋 → **T12** — effort/action beats need motion-prompt presets + physics negatives; today the motion prompt under-specifies and Kling invents |
| S22 | *(found in S18's run logs, invisible to operator)* identity model `nano_banana_edit` hit a fal **content-policy rejection** on some shots → fell back → those shots rendered **without face conditioning** — directly worsening S19/S20 drift, silently | 📋 evidence for **T1** (ledger would have shown "identity ref failed on N shots"); also: soften the identity-edit prompt phrasing that trips the checker |
| S23 | *(same logs)* Safety **Gate B2 vision QC silently skipped** — jpeg/png media-type mismatch in the API call (image is PNG, declared JPEG) | ✅ fixed (B1) |
| S24 | Per-frame **static image overlays** before Final Cut — speaker insets, memory/flashback panels, comic thought/emotion devices, operator-sized/placed | 📋 planned → **T14** — see `docs/FRAME_COMPOSER_PLAN.md` (red-teamed; presets-only v1, PIL+ffmpeg, zero model spend) |
| S25 | Proper **language choice** for the story (Hindi etc.) | 📋 planned → **T13** — see `docs/FRAME_COMPOSER_PLAN.md` Part B (author-in-language + version-after-render flows; fonts, voices, mandatory translated-script review gate) |

---

## 1. OBSERVE — what the last 72h actually revealed

Every defect found in field-testing traces to one of **four systemic patterns**, not four random bugs:

1. **Fail-silent degradation.** The engine's (correct) "never hard-fail" philosophy had no surfacing duty. The silent reel was not an audio bug — it was a *reporting* bug: a fallback fired and nobody was told. `audio_warning` fixed one instance; the pattern remains for every other best-effort step (identity ref failed→generic face, premium model→fallback, restore skipped, smart-match→positional…).
2. **Cache semantics ≠ UX semantics.** Content-hash caches guarantee "same input, no re-spend." Operators expect "Generate = new sample." The portrait-redo bug (S9) is one instance; ANY generate-affordance without an explicit `variant/force` convention will reproduce it (re-roll already has force; portraits didn't).
3. **Theme-context leakage.** Canvas is a light page on a dark-theme global stylesheet. Three whole bug families (invisible inputs, dead `hidden` attributes, ballooning buttons) were this one architectural gap, patched with targeted overrides — whack-a-mole until the page owns a scoped theme.
4. **Full-re-render UI with piecemeal focus guards.** `render()` rebuilds innerHTML wholesale; background polls race operator typing. We guarded `#world-style` and `#characters` individually — the *invariant* ("a poll must never clobber a focused input") is not enforced anywhere centrally.

Also observed (not yet incident-causing): **canvas state read-modify-write races.** Threaded jobs (restore/check/sketch polls, render thread) and route handlers all do `_canvas_load → mutate → _canvas_save` with no versioning; last-writer-wins. Single-process + GIL + short critical sections has kept collisions rare, but the characters/attrs writes now interleave with portrait generation and background check jobs — the window is real and grows with every async feature.

## 2. ORIENT — architecture review (the PhD-hat findings)

**Sound and worth protecting:**
- **The seams.** Models/pricing/LLM/music/voices as config, router + fallback chains: the Kling-first switch was a config edit that survived red-team scrutiny — that is the seam design *working*.
- **Cost governance.** reserve→release→record around every paid call, server-truth estimates. Better than either benchmarked competitor.
- **One engine, many doors.** Canvas reuses `_run_inner`/agents wholesale; the bed-under-VO feature reused the brand-mode mix rather than inventing one. The anti-fork rule is holding.
- **Content-hash caching** at paid steps (with the semantics caveat above).

**Structural weaknesses, in severity order:**
- **W1 — No degradation ledger** (pattern 1). Highest-leverage fix in the codebase: a per-render `report[]` of `{step, severity, what_degraded, why}` accumulated engine-side, persisted with the run, rendered in the output panel. Generalizes `audio_warning`; turns the graceful-degradation philosophy from a liability (silent quality loss) into a feature (honest quality receipt). Also the natural seat for the QC probes (silence, duration, caption contrast).
- **W2 — State concurrency** (observed race). Needs a per-run write mutex + a `rev` counter now (single-process), and is the named blocker for any multi-worker future (SCALE_PLAN already owns the distributed version).
- **W3 — Cache/redo convention gap** (pattern 2). One-line rule to encode in build-feature SKILL: *every operator-facing Generate must accept `variant`; default 0 = cache-friendly, explicit redo = fresh key.*
- **W4 — Frontend architecture debt** (patterns 3+4). `canvas.js` ≈1.4k lines of string templates + wholesale re-render; page-level theme overrides. Not urgent to rewrite (it ships), but each new feature pays a growing tax. Contain via: scoped theme block (done piecemeal → consolidate), a single `safeRender()` that skips subtree rebuilds containing `document.activeElement`, and no new global CSS dependencies.
- **W5 — Verification gap.** No automated UI check; today's headless-Chrome screenshot loop found real bugs in minutes — codify it (T10) rather than re-derive it each session.
- **Not audited this pass (flagged, not cleared):** auth/`require_operator` strength, `/media` + `_path_allowed` traversal hardening, secrets handling beyond .env, tests/ coverage. A security-review pass is a separate loop.

## 3. DECIDE — the plan

### P0 — systemic trust (do before the galleri5 head-to-head)
- ✅ **B1 · Gate B2 mime bug (S23)** — SHIPPED 2026-07-03: `llm._image_bytes_and_format` sniffs magic bytes (PNG/JPEG/WebP/GIF) instead of trusting the extension. *Verified offline (PNG-in-.jpg declares png) + LIVE (vision call on mislabeled file accepted, correct answer).*
- ✅ **B2 · Identity-prompt softening (S22)** — SHIPPED: "keep this EXACT person's face/identity" → character-consistency phrasing. *Verified LIVE on the previously-rejecting endpoint (nano_banana_edit) with the actual Bhima portrait — accepted, on-character result.*
- ✅ **T1 · Degradation Ledger** — SHIPPED: `agents/degradation.py` (bind/report/drain, info|warn|alert), instrumented chokepoints (`model_router.run_with_fallback`, `image_editor.edit_image`, safety Gate A/B2 skips, canvas music + output-silence QC); persisted as `state["render_report"]` (full render + keyframes stage), exposed via `public_state`, rendered as the 🧾 "Render report" panel. *Verified by forced-failure test: 8 events, correct tiers, persisted to public_state.*
- ✅ **T2 · Canvas state safety** — SHIPPED: per-run RLock + `rev` counter + `_canvas_mutate` (atomic re-load→narrow-merge→save); all three long-running jobs (storyboard/restore/check) converted from stale-object saves to fresh merges. *Verified: 3 concurrent writers × 126 writes, zero lost updates, operator edits survived.*

### P1 — operator velocity & the Hanuman/Mahabharat bar
- **T3 · Plan-time auto-fill (S12).** Plan LLM additionally returns `{world_style, world_setting, target_seconds?, narrator_profile}`; UI fills **only empty fields**, marked "✨ suggested — edit freely". Never overwrites operator input. *(S)*
- **T4 · Per-character voices (S13a).** Voice dropdown per cast row (from `/voices` + `config/voices.json` roles); `speaker_id`-aware `generate_voiceover_track` picks each line's voice; narrator default unchanged. Real-person voice *cloning* stays behind existing likeness governance — stock voices only here. *(M)*
- **T5 · Shot iteration honesty (S11+S15).** (a) Inspector button "↻ Regenerate still from prompt" (explicit, replaces the hidden prompt-edit→Re-roll dance); (b) **take history**: keep last N=4 stills/clips per shot, thumbnail strip in inspector, click to restore (no re-spend). *(M)*
- **T6 · Remotion caption engine P1 (S16).** Wire per REMOTION_CAPTIONS_PLAN: `config/captions.json` seam, overlay render + composite, libass auto-fallback, prod-tier default-off. *(M)*

### P1b — consistency fidelity (from the Hanuman field test, S19–S21)
- ✅ **T11 · Outfit + anatomy lock** — SHIPPED (a+b): canonical portrait now three-quarter framing with the full outfit visible; new `species` attribute; `_character_appearance` phrases wardrobe + anatomy as INVARIANTS ("this exact anatomy in every shot", "always wearing … same outfit in every scene"); identity ref-prompt demands "SAME outfit and wardrobe as the reference". Cast sheet gained the species field. *(c) anatomy-consistency Review flag deferred — Gate B2 (re-armed by B1) covers prompt-vs-image QC meanwhile.* *Verified offline: appearance clause asserts invariants.*
- ✅ **T12 · Motion presets for action beats** — SHIPPED: physics negatives in `DEFAULT_KLING_NEGATIVE` (floating/hovering/sliding/unnatural gravity) + keyword-routed presets (strain/kneel/walk/rise-divine); operator `motion_override` always wins; ambient default preserved. *Verified offline: routing, override precedence, default intact.*
- ✅ **T4 · Per-character voices** — SHIPPED (pulled forward from P1): `voice_id` on the character sheet (dropdown per cast row, stock voices only), `_canvas_render_data` builds `voice_map`, VO track resolves per-frame via the pre-existing `cast.voice_for_frame`. *Verified offline: bhima/hanuman voices resolve, unassigned → narrator.*

### P1c — repurposing & enrichment (S24/S25, planned 2026-07-03)
- **T13 · Language versions** *(M + config)* — language-first authoring AND
  version-after-render repurposing (same clips/music, new captions+VO); Noto serif
  fonts per script; mandatory translated-script review gate. `docs/FRAME_COMPOSER_PLAN.md` Part B.
- **T14 · Frame Composer** *(M)* — per-shot static overlays (speaker chip / memory
  polaroid / thought bubble / sticker), presets-only, PIL+ffmpeg compose-on-top with
  separate cache; speaker-chip auto toggle; Remotion-animated variant after T6.
  `docs/FRAME_COMPOSER_PLAN.md` Part A.

### P2 — cinematic ceiling
- **T7 · Selective lip-sync (S13b).** Per-shot "🎤 Dialogue" toggle (max 3/reel, spend-gated) → routes that shot down the dialogue lane (Veo) with the character's voice line; assembler lipsync path already exists. *(L)*
- **T8 · Beat→asset ladder (S14).** Auto ambient-recreate when Review flags a mismatch and no better real asset exists (existing ladder plan). *(L)*
- **T9 · Canvas theme scoping.** Consolidate the light-theme overrides into one `.cv2-scope` block with design tokens; delete the whack-a-mole rules. *(S-M)*
- **T10 · Visual smoke harness.** Script the headless-Chrome screenshot set (empty canvas, planned canvas, inspector open, script view) + pixel-diff vs goldens; run before "done". *(S)*

### Non-goals (explicit)
- No `_run_inner` refactor, no frontend framework rewrite, no multi-tenant/SaaS build (separate plan when owner decides), no LLM-generated Remotion code (templates + props only), no automatic spend without operator action.

## 4. RED TEAM — attacking this plan

- **T1 noise risk:** a ledger that flags every fallback trains operators to ignore it (alarm fatigue — the *opposite* failure of silent degradation). → Severity taxonomy is load-bearing: `info` (collapsed, e.g. "premium→fallback model"), `warn` (visible line, e.g. "identity ref failed on 2 shots"), `alert` (red banner, e.g. "no audio"). Ship with ≤3 `alert` classes; expand only on evidence.
- **T2 over-engineering risk:** full CAS/versioning in a single-process app is ceremony; per-run mutex + rev is enough *now*. → Scoped: mutex now, distributed lock explicitly deferred to SCALE_PLAN. Red-team accepts.
- **T2 residual:** threaded jobs already hold state objects across await-points (load once, save at end) — a mutex around save doesn't fix stale-object overwrites. → Tickets must convert jobs to *narrow* re-load→merge→save at write time (the `merge_progress()` note), not just lock the save.
- **T3 wrong-suggestion risk:** a bad auto-filled world style silently degrades every shot; operator may not notice it was a guess. → Provenance chip on filled fields + suggestions never survive into the render unless the operator generated with them visible (they're in the editable fields — visible by construction). Accept.
- **T4 scope creep:** "voices per character" invites voice *cloning* of real people → likeness governance question. → Explicitly stock-voices-only in this ticket; cloning stays a governed, separate feature. Accept with that fence.
- **T5 storage risk:** take history × 40 shots × prod renders = disk growth. → N=4 LRU per shot, dev-tier files dominate (small), prod takes pruned on Final Cut approval. Accept.
- **T6 known costs:** +~1GB Docker layer, ~real-time overlay render — already documented in the Remotion plan; fallback keeps reels shippable. Accept.
- **T7 uncanny/cost risk (highest of any ticket):** lip-synced mythological faces can *lower* perceived quality below the no-lipsync baseline while costing the most. → Hard cap (3 shots), dev-tier preview mandatory before prod lip-sync, and a kill criterion: if 2 consecutive stories ship 0 lip-sync shots after preview, deprioritize T7. 
- **Plan-level bias check:** this plan over-weights last-72h incidents (recency bias) and under-weights unaudited areas (auth, path traversal, test coverage). → Explicitly scheduled as a separate security-review loop rather than pretending this pass cleared them.
- **Sequencing check:** is P0 really before Hanuman? T2 yes (more async features = growing race window). T1 arguably parallel — Hanuman can start on Dev tier today; T1 must land before the *CEO-facing* comparison render. → Sequencing: T2 → (Hanuman Dev iterations ∥ T1) → T3/T4/T5 → Hanuman Prod + comparison.

## 5. Verify criteria (per ticket, summarized)
Every ticket: compile/`node --check` → offline unit (the new logic in isolation) → live smoke on 7860 → headless-Chrome visual check (T10 harness once it exists) → docs-sync (LLD + guides + this plan's checkboxes) — then red-team the diff.
