# S30_ADOPTION_PLAN.md — galleri5-teardown adoption track

> **Status: PARTIAL (2026-07-14) — Phases 1 + 3 SHIPPED + verified, Phase 2 seam SHIPPED
> (Final-Cut mix ticketed), Phase 4 lives on the continuity roadmap.** Decision record: `COMPETITOR_GALLERI5_TEARDOWN.md §10`
> (ledger row S30). Chosen option D: selective adoption, S28-ordered, rent-first.
> Verified: compile clean; 9/9 offline checks (clause invariance, propagation isolation,
> cache-hash participation, variant keying, degrade-to-no-op, route registration); live
> LLM smoke derived the same 2 locations galleri5 did on the Yamraj beats and deduped
> f1/f3. NOT live-verified (blocked): a paid plate render + a live MMAudio call — the
> fal account is balance-locked (403); the endpoint slug routed, so it looks valid.

## Locked decisions

1. **One engine, no fork.** Location anchoring mirrors the SHIPPED character-sheet
   machinery (`derive_characters` / `set_character` / `character-portrait` route) at the
   location level — same state shape, same propagation-onto-frames pattern, same
   spend-gate pattern. No new pipeline; new frame keys only.
2. **Plate discipline (stolen from the teardown, §10.4 item 5):** location plates are
   generated EMPTY — no people, negative space at center for characters, lighting
   headroom — so characters composite/condition in later without fighting baked-in subjects.
3. **Identity beats place.** `edit_image` takes ONE reference; when a shot has both a
   character ref and a location plate, the FACE ref wins (S19/S20 lessons). The location
   rides every prompt as an INVARIANT clause (T11 phrasing). Plate-as-second-ref is the
   D5 multi-ref follow-up, not v1.
4. **Redo ≠ cache hit (rule 12):** plate generation takes `variant`; 0 = reuse, else fresh.
5. **SFX is rented** (MMAudio-class video→audio) through the `config/models.json` +
   `pricing.json` seams, opt-in with a visible 💰 estimate (cost discipline) — never
   auto-spend like galleri5's wallet-drain flaw.

## Phases

| Phase | What | Status |
|---|---|---|
| **1** | **Location anchoring**: `derive_locations` (LLM reasoning-tier, tags `frames[].location_id`, degrades to no-op) · `set_location` (propagates `location_clause` invariant + `location_ref_path` plate onto that location's frames) · `generate_location_plate` (empty-plate prompt, cached, variant-aware) · routes `/locations` (derive, free) `/location` (set) `/location-plate` (paid, spend-gated) · 🏞 Locations panel in canvas UI mirroring the Cast sheet | ✅ SHIPPED 2026-07-14 (live plate render pending fal top-up) |
| **2** | **SFX/atmosphere seam**: `models.json` "mmaudio" + `pricing.json` "sfx" + `agents/sfx.py` (video→audio, content-hash cached, '' on failure + degradation.report) — SHIPPED. **Ticketed remainder:** Final-Cut mix — add the per-clip SFX WAV as a low stem under VO/music in the mixed-mode filter graph (`assembler.py:294-370` pattern), opt-in checkbox + 💰 `pricing.sfx_cost()×clips` estimate in the canvas audio bar; needs its own ffmpeg + silence-probe verify loop (S1 lesson) and a live MMAudio call to verify the endpoint schema once fal is topped up. **Do these WITH the mix, not before** (deferred 2026-07-17 after an L99 review: `generate_sfx` and `pricing.sfx_cost` have **0 callers** and `mmaudio` sits in no routing lane, so fixing them now polishes unreachable code and makes SFX *look* shipped): (a) cache via the shared `cache_store.BlobCache` instead of a local file, or every redeploy re-buys tracks the S3 read-through already paid for (`clip_builder`/`lipsync_coordinator` are the pattern); (b) resolve the endpoint through `model_router.model_field` instead of reading `models.json` directly + hardcoding the slug (CLAUDE.md §3 seam rule), and give `mmaudio` a routing/fallbacks lane; (c) delete `pricing.sfx_cost` in favour of the generic `pricing.model_cost('mmaudio')` — it duplicates the `pricing_key` resolver and would silently go stale on a vendor swap | ✅ seam SHIPPED · 🚧 mix ticketed (carries the 3 seam fixes) |
| **3** | **Per-asset review states** — `frame.review_status` ∈ {needs_review, approved, production_ready, rejected} via `edit_frame` (validated, **metadata-only: invalidates nothing** — `edit_frame` now tracks `timing_changed` separately so a verdict can't retrigger video); chip on the Key Frame cell (`.rv-*` scoped styles) + Review select in the Inspector (rides the existing `.edit` plumbing, no new route). **Sticky action bar: already satisfied** — the canvas ships a sticky bottom bar (`.cv2-bottom`, z-10) + per-shot `.inspector-actions`; adding a second context bar would duplicate surface, so this item is closed as covered, not built. | ✅ SHIPPED 2026-07-14 |
| **4** | **Beat-synced cutting/motion** (continuity P1/P2 — music beat grid → cut points + push-ins) | 📋 separate plan (reel-continuity roadmap) |
| — | **Infinite canvas** — deliberately deferred until locations/characters are first-class graph nodes; rented lib (react-flow vs tldraw license-check) | 📋 coupled to asset-graph work |
| **5** | **Style Context** (their "Contexts", decoded in teardown §10.4b): per-brand/world distilled artifact (look clauses, tone, caption defaults, voice roles, ref images, negatives) + Plan-time picker filling `set_world`/captions/voices + optional capture→synthesize pass (vision tier over dropped-in refs). Near-term: SERIES consistency across canvases; later: Veristory multi-client brand kits. Hard rule: fills-and-suggests only (T3 ✨), operator brief wins — never their silent style override. | 📋 ticketed — build as the front door of GAP #6 (asset library), after the RDS cutover |

## Non-goals (refused, §10.4)

House style-token that overrides the operator's brief (look token stays subordinate);
single-frontal-only identity; 16:9 defaults; wrapping galleri5 as a dependency.

## Data-model delta (Phase 1)

- `state["locations"]`: `[{id, label, description, time_of_day, plate_path, source}]`
- `frames[].location_id` — tag from `derive_locations` (like `speaker_id`)
- `frames[].location_clause` — invariant art-direction text, injected into generation
  prompts (participates in the content-hash → edits regenerate)
- `frames[].location_ref_path` — the plate; v1 reserved for D5 multi-ref conditioning

## Open questions for the owner (defaults chosen, override anytime)

1. **Plate spend default** — plates cost one image each (2–4/story, dev tier ≈ pennies).
   Default: operator clicks per plate or "Generate all plates" (same as faces). OK?
2. **SFX opt-in vs auto** — default OPT-IN (checkbox + estimate at Final Cut), unlike
   galleri5's always-on. Flip to default-on later if operators always tick it?
3. **SFX provider** — MMAudio-class on fal vs ElevenLabs SFX; chosen by config, first
   wired: fal (already a vendor). Confirm.
4. **Look token UI** (Phase 3+) — today `set_world` (style/setting) already IS our look
   token, subordinate to the operator. Proposal: auto-SUGGEST a world style from the
   brief at plan time (editable, never forced). OK?
