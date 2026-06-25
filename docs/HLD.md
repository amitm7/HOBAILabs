# HOBAILabs — High-Level Design (HLD)

**Product:** AI story-to-reel & brand-ad pipeline (Instagram Reels / YouTube Shorts)
**Revision:** 2026-06-18 · current `main` branch
**Companion docs:** [LLD.md](LLD.md) — module-level internals · [SCALE_PLAN.md](SCALE_PLAN.md) — multi-user scale phasing · [BRAND_PLAN.md](BRAND_PLAN.md) — brand mode spec
**Source of truth for behaviour:** [OPERATOR_GUIDE.html](OPERATOR_GUIDE.html) (user view) · [GUIDE.md](../GUIDE.md) · [ROADMAP.md](../ROADMAP.md) (forward plan) · [MARKET_FIT_REVIEW.md](MARKET_FIT_REVIEW.md) + [GAP_BACKLOG.md](GAP_BACKLOG.md) (strategic review & gaps)

---

## 1. Purpose & Scope

HOBAILabs turns a **plain-text story script + a folder of photos/videos** into a
finished, captioned, scored **9:16 MP4** — for two distinct use cases sharing one engine:

| Mode | Entry point | Audience |
|---|---|---|
| **Story / creator mode** | `/` | HOB team creating personal story reels |
| **Brand / Ad mode (B1)** | `/brand` | Creator + brand partners doing paid collabs |
| **Studio mode (MODE3)** | `/studio` | Prompt → full reel with a reusable Talent/Product identity library; commerce + general scopes |

The defining product principle is **realism preservation + test-cheap/finish-expensive**:
real user media is never AI-regenerated; per-shot routing uses cheap models in Dev and
premium in Production.

**In scope (today):**
- Single-story render via Web UI or CLI
- Per-shot model routing; AI image generation; image→video animation across 4 providers
- Lip-sync; content/quality safety gates (Gate A/B/B2); cost estimation; aggressive caching
- Multi-shot B-roll coverage; voiceover mode with frame-exact audio sync; secure folder uploads
- Treatment pass (whole-reel arc plan) feeding per-frame scene design
- Vision-grounded motion (look at actual still before writing Kling prompt)
- Multi-speaker cast detection — per-line speaker identity, face, and voice selection
- Suggestion chips (camera / image-edit / director note per frame)
- Brand / Ad mode (B1): brief extraction, brand kit, product beats, mandatories hard-block, CTA end-card, disclosure, VO-over-ducked-music
- Font options: Montserrat (bundled), Satoshi (drop-in when licensed)
- Governed roadmap thin slices: AI story→editable frame draft intake, caption
  safe-zone, keyword highlights, read-only timeline, story-mode posting kit,
  text-card layout preset, lightweight editor export, redo-motion clip refresh,
  consent/spend governance, restart-safe run metadata, and SQLite stand-ins for
  asset/approval/version records.

**Out of scope (today):** multi-tenant accounts, persistent job DB, batch/queue
production, beat-synced cuts, kinetic motion-graphics (B2), multi-platform export,
CLIP-based scoring — these are on the roadmap (§9).

---

## 2. System Context (C4 Level 1)

```
                         ┌──────────────────────────────────────────────────┐
                         │              HOBAILabs                            │
   ┌─────────┐  script + │                                                  │  ┌──────────────┐
   │ Creator │  assets   │  Web UI (Flask)  ──┐                              │  │  LLM brain   │
   │ (browser│──────────►│  /  story mode     ├─► Render Pipeline ──────────┼─►│ OpenAI /     │
   │  / CLI) │           │  /brand ad mode  ──┘   (agents/*)                │  │ Bedrock /    │
   └─────────┘           │                                                  │  │ Gemini       │
        ▲                │  Config: models.json, pricing.json,              │  └──────────────┘
        │  9:16 MP4      │          llm.json, voices.json                   │  ┌──────────────┐
        └────────────────┤  Cache: ~/.hob_cache/*                           │  │ Image gen    │
                         │                                                  │  │ Flux/fal/GPT │
                         └──────────────────────────────────────────────────┘  ├──────────────┤
                                                                                │ Video: Kling │
   External services (all optional/degradable):                                 │ Higgsfield   │
   · Image: fal.ai (Flux, Seedream, Nano Banana), OpenAI gpt-image              │ fal Seedance │
   · Video: Kling AI, Higgsfield, fal (Seedance/Veo/Hailuo)                    │ Veo / Hailuo │
   · Lip-sync: Hedra (photo), SyncLabs (video)                                 ├──────────────┤
   · Voice: ElevenLabs · Music: Suno · Safety: OpenAI Moderation               │ Voice/Music/ │
   · CDN for lip-sync uploads: Higgsfield CDN                                  │ Lipsync APIs │
                                                                                └──────────────┘
```

**Key context properties**

- **Two front doors, one engine.** `/` (story) and `/brand` (ad) both build the same
  `frames[]` data model and call the same `agents/*` stages. Brand mode adds a thin
  mode-hook layer (brand payload, product-beat flag, mandatories gate, post-pass).
- **Vendor-pluggable on three independent axes:** reasoning/vision/fast LLM
  (`config/llm.json`), image model, video model (`config/models.json`).
- **Graceful degradation everywhere.** Any external failure falls back to a cheaper
  path (premium model → fallback → Kling → Ken Burns). A render rarely hard-fails.

---

## 3. Containers / Runtime Pieces (C4 Level 2)

| Container | Tech | Responsibility |
|---|---|---|
| **Web UI** | Flask + server-rendered templates + SSE | Parse script, preview stills, estimate cost, stream progress logs, serve/download output and lightweight editor exports. Per-`run_id` state is mirrored to a lightweight run store. |
| **Brand UI layer** | `brand.html` + `brand.js` (hooks into `main.js`) | Additional brief panel, brand-kit uploads, mandatories checklist, product-beat toggles. No fork of the engine. |
| **Studio UI layer** | `studio.html` + `studio.js` (hooks into `main.js`) | Brief→shots planner, reusable Talent/Product identity library, per-shot talent/product/negative/continuity controls. Same hook pattern as brand; no fork. |
| **Shot planner** | `agents/shot_planner.py` | One cached LLM call: brief (+scope, +locked talent/product) → editable `frames[]`. Graceful sentence-split fallback. |
| **Identity library** | `agents/product_surface.py` (`talents`, `products`) | SQLite-backed reusable Talent (face) + Product (ref + specs) assets, locked across shots/runs. |
| **CLI** | `argparse` + `run_caption.py` | Headless render; dry-run cost plan; all flags the UI exposes. |
| **Render pipeline** | `agents/*` Python modules | The actual work: parse → treatment → scene-design → cast → assign visuals → edit → lip-sync → animate → caption → assemble → (brand post-pass). |
| **Cast module** | `agents/cast.py` | Detect speakers per frame, build cast list, resolve voice priority per speaker. |
| **Brand module** | `agents/brand.py` | Extract brief (parse-only), validate mandatories, build PIL CTA card, burn disclosure text via ffmpeg. |
| **Suggestions module** | `agents/suggestions.py` | Batch fast-tier LLM call at parse time → camera/edit/note chips per frame. |
| **Governance modules** | `agents/governance.py`, `agents/run_store.py`, `agents/product_surface.py` | Thin SQLite bridges for consent/spend gates, restart-safe run metadata, asset records, approval records, and version records before the full DB lands. |
| **Model router** | `agents/model_router.py` + `config/models.json` | Pure logic: maps each shot to a model id given shot type + cost tier + overrides. |
| **LLM brain** | `agents/llm.py` + `config/llm.json` | Single entry point for every reasoning/vision/fast call; OpenAI / Anthropic (direct API) / Bedrock / Gemini backends; JSON schema enforcement. Anthropic-direct is the working Claude path when Bedrock isn't entitled (Marketplace-gated). |
| **Cost engine** | `agents/pricing.py` + `config/pricing.json` | Single source of cost figures; whole-pipeline estimate (multi-shot aware). |
| **Caches** | `~/.hob_cache/*` (filesystem) | Clips, scene designs, image descriptions, lip-sync clips+audio, generated stills (in asset folder, prompt-hash keyed). |

No database, no message broker, no auth layer today. State for a web render lives
in process memory keyed by `run_id`; durable artifacts are files on disk and the
content-hash caches.

---

## 4. The Core Data Model — the `frame` dict

Everything flows through a list of mutable `frame` dicts. Each stage reads some
keys and writes others.

```python
frame = {
  # ── identity / text (from script_parser) ──
  "frame_id": "f03",           "caption": "Bedridden, I lost my hair…",
  "duration": 7.5,             "director_note": "...",

  # ── source selection (parser → matcher → pipeline) ──
  "photo_spec": "06_family.jpg" | "ai_portrait" | "ai_symbolic" | "",
  "visual_path": "/abs/path.jpg",       # resolved real/generated still or video
  "image_model_override": "", "video_model_override": "",
  "video_start_sec": 0.0, "video_end_sec": None,

  # ── direction (scene_intelligence) ──
  "scene": { "emotion", "scene_description", "image_prompt",
             "motion_prompt", "camera_angle" },
  "motion_override": "crane up",        # [camera:]/[motion:] beats GPT
  "edit_prompt": "add frost on the window",

  # ── cast / speaker (cast.py) ──
  "speaker_id": "narrator" | "son" | ...,   # detected by LLM or [speaker:] annotation
  "cast": [{ "id", "name", "gender", "age_bracket", "description", "voice_id" }],

  # ── lip-sync (lipsync_coordinator) ──
  "lipsync": True, "voice_override": "<11labs id>",
  "lipsync_clip_path": "/tmp/.../lipsync_f03.mp4",

  # ── brand mode extras ──
  "product_beat": False,       # real product shot — skip AI gen if True
  "suggestions": { "camera": [...], "edit": [...], "note": [...] },

  # ── studio mode extras (MODE3) ──
  "talent_id": "tal_…",        # locked reusable face (product_surface.talents)
  "product_id": "prd_…",       # locked reusable product (product_surface.products)
  "talent_ref_path": "/abs/…", # resolved talent reference → identity-edit lock
  "negative_prompt": "...",    # per-shot Kling negative (default if blank)
  "continuity_lock": "...",    # outfit/styling that must not change (→ image prompt)

  # ── per-frame caption overrides (blank = use global caption_style) ──
  "caption_position": "top",   # bottom|middle|top — overrides global default
  "caption_max_lines": "2",    # cap + auto-shrink — overrides global default

  # ── treatment (scene_intelligence.design_treatment) ──
  # Stored on frames[0]["_treatment"] as a whole-reel plan
}
```

**Pipeline = a sequence of transformations over `frames[]`.** Stages are mostly
order-dependent (each depends on keys the previous wrote) but internally
**parallel** (per-frame work runs in `ThreadPoolExecutor`s).

---

## 5. End-to-End Flow

```
 SCRIPT (.txt) + ASSETS (folder)
        │
        ▼
 [Gate A] moderate_script()  ──► OpenAI Moderation (policy floor, non-blocking on API error)
        │
        ▼
 0. detect_cast() / apply_cast()      LLM identifies who speaks each line (narrator vs quoted
        │                             speakers); builds cast[] with gender/age/voice.
        │                             Voice priority: [voice:] override > voice_map[speaker_id]
        │                             > role map (gender/age) > global default.
        ▼
 1. parse_frame_script()              Format A/B → frames[]; auto-duration; sort-order match.
        │                             (opt-in smart_match: LLM reads photo content → assigns)
        │                             suggest_for_frames() → camera/edit/note chips per frame.
        ▼
 2. design_treatment()                Whole-reel plan: arc, visual motif, shot-size rhythm.
        │                             Stored on frames[0]["_treatment"]; fed to every scene call.
        ▼
 2b. design_all_scenes()              LLM director per frame → emotion + image_prompt + motion.
        │                             (parallel · disk-cached by MD5 of inputs)
        │                             For real-photo frames: motion only (skip image_prompt).
        ▼
 3. visual assignment (per frame)     router.select_model("image", …):
        │   ├─ real photo/video  → PASSTHROUGH (kept as-is — realism)
        │   ├─ product_beat      → PASSTHROUGH (real product asset, never AI-generated)
        │   ├─ ai_portrait       → generate_contextual_image() [+Gate B sanity, ≤2 retries]
        │   │                       (reference_path= for face-consistency across frames)
        │   ├─ ai_symbolic       → generate_symbolic_image() [+Gate B]
        │   └─ fallback          → speaker-aware subject descriptor (no hardcoded defaults)
        ▼
 3b1. ground_all_motions()            Vision-grounded motion: look at the actual generated still,
        │                             then rewrite the Kling motion prompt to be visually accurate.
        ▼
 3b2. edit pass                       edit_image() between gen and animation (opt-in, per frame)
        ▼
 3b3. [Gate B2] critique_image()      Vision LLM verifies the still matches its prompt
        │                             (no blank/abstract image when a real subject was expected).
        ▼
 3c. lip-sync pass                    run_lipsync_pass(): 11Labs audio → CDN upload →
        │                             Hedra (photo) / SyncLabs (video); duration flips to audio.
        │                             Per-speaker voice_for_frame() from cast module.
        ▼
 4. build assignments[]               motion = override or scene.motion_prompt
        │                             model_id = router.select_model("video", …)
        ▼
 5. build_clips()                     per shot: cache → Ken Burns | Kling | Higgsfield | fal
        │                             _fit_clip_to_duration() trims/extends before caching.
        │                             effective_timecodes() accounts for 0.4s crossfade overlap.
        ▼
 6. generate_frame_srt()              ASS captions timed to effective timecodes
        ▼
 7. generate_voiceover_track (opt)    ElevenLabs/OpenAI TTS; per-speaker voice selection;
        │                             prosody continuity broken when voice changes.
        │                             Frame-exact padding/trim; adelay per timecode.
        ▼
 8. assemble_caption_only()           normalize → xfade/hard-cut concat → captions burn →
        │                             music (25%, duck to 10% under lip-sync) →
        │                             voiceover (VO-over-ducked-music at 18%) → 9:16 MP4
        ▼
 8b. apply_brand_overlay() [brand]    Burn disclosure text (ffmpeg drawtext) + logo corner bug.
        │                             CTA end-card (PIL-generated) appended as last frame.
        ▼
 OUTPUT .mp4
```

**Dry-run** short-circuits after stage 2b: it prints the per-frame plan and a
`pricing.estimate()` breakdown, spending nothing.

**Brand mandatories hard-block** fires at the *start* of `/run` (before any spend):
`validate_mandatories()` → 400 + JSON checklist if logo, CTA, or product beat are missing.

---

## 6. Key Architectural Decisions & Rationale

| Decision | Why | Where |
|---|---|---|
| **Per-shot model router, not one global provider** | Different shots want different models; cost discipline (cheap in Dev) should be automatic. | `model_router.py`, `config/models.json` |
| **Real media is `PASSTHROUGH` at the image step** | Preserving the real subject is the product's biggest realism advantage; AI regen would destroy it. | `model_router._is_real_media` |
| **Product beats are also `PASSTHROUGH`** | A brand's product must never be AI-generated or modified. | `web_app._generate_stills` product-beat guard |
| **LLM behind one `chat()` brain with 3 tiers** | `reasoning`/`vision`/`fast` decouples the caller from provider; fast tier used for batch calls (suggestions, image descriptions) to cut cost. | `agents/llm.py`, `config/llm.json` |
| **JSON schema enforcement on LLM outputs** | Strict structured outputs (OpenAI) / directive injection (Bedrock/Gemini) eliminate parse failures on scene/treatment/cast calls. | `llm.py json_schema param`, `scene_intelligence._SCENE_SCHEMA` |
| **Treatment pass before per-frame design** | A film director plans the whole reel arc before shooting individual scenes; treatment keeps motif + shot-size rhythm consistent. | `scene_intelligence.design_treatment()` |
| **Vision-grounded motion** | Writing motion prompts without seeing the still produces generic motions; looking at the actual image first makes prompts scene-specific. | `scene_intelligence.ground_motion_prompt()` |
| **Cast detection by LLM, not script markup** | Most scripts don't annotate speakers; the LLM detects who says each line from context (quoted speech, pronouns, names). | `agents/cast.py detect_cast()` |
| **Voice priority chain** | Explicit `[voice:]` override > voice_map UI selection > role map (gender/age) > global default — maximum flexibility without breaking simple cases. | `cast.voice_for_frame()` |
| **Subject is always optional, never defaulted** | Hardcoding a sample person name/description was leaking test data into real renders; the story always knows who is on screen. | Subject fields, `scene_intelligence` system prompts |
| **Config-driven catalog + pricing** | Vendors change slugs/prices monthly; edits should be JSON, not Python. | `config/models.json`, `config/pricing.json` |
| **Content-hash caches at every expensive step** | Re-renders of the same story must not re-spend credits. Stills use a prompt-hash filename so changing the prompt busts the file-reuse check. | `~/.hob_cache/*`, `image_generator._prompt_hash()` |
| **Deferred submit + capped parallel poll** | Providers have hard parallel limits (Kling 4, Veo 2); over-submitting caused 429s. | `clip_builder.build_clips` |
| **Two + two safety gates** | Gate A (text moderation) ≠ Gate B (face sanity check) ≠ Gate B2 (vision critique) ≠ Brand gate. Each catches a different failure class. | `agents/safety.py` |
| **Brand mode as mode-hooks, not a fork** | Same engine, same `frames[]`, same clip builder. Brand-only behaviour is a thin additive layer (payload merge, product-beat guard, post-pass). | `brand.js` hooks, `web_app is_brand` flag |
| **AI never writes ad claims** | `brand.extract_brief()` is parse-only with explicit "verbatim copy" instruction; all on-screen/spoken text comes from fields the operator fills. | `agents/brand.extract_brief()` |
| **Mandatories hard-block before any spend** | Prevent a render that would be legally unusable (no logo, no disclosure). | `brand.validate_mandatories()` in `/run` |
| **Effective-timecodes for audio sync** | Raw cumulative durations ignore the 0.4s crossfade overlap per junction; all audio (captions, voiceover, lipsync adelay, ducking windows) must use effective timecodes or they drift. | `assembler.effective_timecodes()` |
| **Editor iteration loop, not a one-shot black box** | Editors rejected the "wait for the whole video, often unhappy with a few frames" model. Three features turn it into a collaborative draft tool: per-frame redo (regenerate one still), progressive reveal (see each clip as it lands), approval gate (animate only approved frames; rest = free Ken Burns). | `/redo-still`, `build_clips(on_clip_ready=…)`, `approved_frame_ids` |
| **Everything degrades, never crashes** | Creators lose trust on a hard failure far more than on a cheaper fallback. | every stage's `try/except` |

---

## 7. Cross-Cutting Concerns

- **Cost control.** Single `pricing.estimate()` walks `frames[]` and mirrors the
  router's actual model choices so the UI/CLI estimate matches billing. Dev caps clips
  at 5s. `/api/estimate` server-side endpoint returns a structured breakdown (no
  client-side cost logic).
- **Upfront credit visibility.** `agents/balances.py` probes each vendor's live
  credit balance (read-only, concurrent, degrades per-vendor) and surfaces it at
  `GET /balances` + a "💳 AI Credits" panel, so an operator sees a low/empty wallet
  before a run instead of mid-render. Live numbers come from ElevenLabs, Kling, Suno,
  and fal today; OpenAI/Gemini/Higgsfield/Hedra/SyncLabs expose no usable balance API
  and are labelled accordingly. Complements the per-project spend ledger in `governance.py`.
- **Caching.** Clip cache keyed by `MD5(image bytes + motion + duration)`, namespaced
  per model. Still cache: `ai_portrait_{fid}_{prompt_hash}.jpg` in the asset folder —
  changing the prompt busts the cache. Scene designs, image descriptions, lip-sync
  clips/audio each have their own content-hash cache.
- **Concurrency.** I/O-bound stages run in thread pools, throttled to the strictest
  `max_concurrent` among models in flight.
- **Failure isolation.** A per-frame failure falls back for that frame only; the
  render continues. Temp dir preserved on whole-pipeline failure for debugging.
- **Security/secrets.** All API keys in `.env`; `/media` path-containment via
  `_path_allowed()`; `ASSETS_BROWSE_ROOT` env var scopes server folder browser.
- **Font bundling.** Montserrat OFL TTFs bundled in `deploy/fonts/`, installed via
  Dockerfile + `fc-cache`. Satoshi is unlisted (commercial license) — drop-in only.
- **IP/property watermarking.** Every reel can be tagged with one HOB IP (HOB Originals,
  The HOB Show, …); its full-frame transparent PNG is composited over the whole video in
  both modes via the single `apply_brand_overlay` post-pass. Registry: `config/watermarks.json`
  → `deploy/watermarks/*.png` (`agents/watermark.py`). This is HOB's own property branding,
  separate from the brand-collab advertiser logo. Degrades to no-op if the PNG isn't present.

---

## 8. Risks & Constraints

| Risk / Constraint | Impact | Mitigation (today) |
|---|---|---|
| fal.ai endpoint slugs + prices marked `VERIFY` | A Production render could hit a dead/mis-priced endpoint | Dev is safe; OPERATOR_GUIDE warns to verify before paid runs |
| Provider parallel limits (Kling 4, Veo 2, Higgsfield 4) | 429s, dropped frames | Capped pool + retry-on-limit instead of fallback |
| Lip-sync media uploaded to external CDN | Private user media leaves the box | Documented; opt-in feature; self-hosted signed URLs on roadmap |
| In-memory web run state (no DB) | Server restart loses in-flight runs; no horizontal scale | Acceptable for single-operator use; durable job store is SCALE_PLAN Phase 0 |
| Suno via third-party wrapper (`api.sunoapi.org`) | Unofficial, may break | Music is optional; isolated in `music_generator` |
| macOS-specific bits (`sips` HEIC, Baskerville font path) | Reduced portability off macOS | Dockerfile exists; Montserrat bundled; HEIC needs Linux path |
| No automated tests | Regressions land silently | Router/pricing/cast are pure functions — prime unit-test candidates |
| Brand: "Paid partnership" label is in-video only | Instagram requires the native label too | OPERATOR_GUIDE documents manual step; cannot be automated |

---

## 9. Roadmap Alignment

From [ROADMAP.md](../ROADMAP.md) and [SCALE_PLAN.md](SCALE_PLAN.md):

- **P0 — Pipeline hardening.** Landed: Gate A/B + B2, face-consistency reference path,
  prompt-hash stills cache, multi-speaker cast, treatment pass, vision-grounded motion,
  suggestion chips, brand mode B1, server-side /api/estimate, effective-timecodes audio sync.
- **P1 — Raw video correctness, image edits, pricing.** Landed.
- **P2 — Lip-sync, multi-shot B-roll, voiceover mode, smart coverage.** Landed.
- **B2 (deferred) — Kinetic motion-graphics layer:** animated kinetic typography,
  word-by-word VO sync, badges/stickers/price callouts, product PIP overlay. Planned in
  [BRAND_PLAN.md](BRAND_PLAN.md) §B2.
- **SCALE_PLAN Phase 0 — Durability:** persistent RUNS_DIR, runs DB table, re-dispatch/resume,
  cost ledger, error reporting, backups. Not yet started.
- **SCALE_PLAN Phase 1 — Queue:** job broker, multi-worker, signed media URLs.
- **P3 — CLIP score-based matching.** Pending; GPU-side work.

---

## 10. Glossary

- **Frame / beat** — one shot in the reel; one `frame` dict.
- **Shot type** — router classification (`face`, `object`, `real`, `landscape`, `hero`, `dialogue`) selecting a model.
- **Cost tier** — `draft` (Dev, cheap) vs `premium` (Production).
- **PASSTHROUGH** — sentinel meaning "real media, skip image generation." Also applies to product beats in brand mode.
- **Gate A / B / B2** — script moderation / face sanity / vision critique. Each catches a different failure class.
- **Brand gate** — mandatories hard-block fired before any spend.
- **clip_ready** — a frame whose lip-sync clip is finished and bypasses animation.
- **Treatment** — whole-reel arc plan (emotional arc, visual motif, shot-size rhythm) produced before per-frame scene design.
- **Cast** — list of speakers identified by LLM per story; each has id, gender, age, and voice priority.
- **Product beat** — a frame marked to use real brand product/logo assets, never AI-generated.
- **Effective timecodes** — cumulative timecodes accounting for 0.4s crossfade overlap per junction; used for all audio alignment.
- **Fast tier** — the third LLM model tier (e.g. `gpt-4o-mini`, `claude-haiku`) used for batch low-stakes calls (suggestions, image descriptions).
- **Per-frame redo** — regenerate the still for a single frame (`/redo-still`) without re-running the pipeline; cache-busted for that frame only.
- **Progressive reveal** — frame cards show each animated clip the moment it finishes, via a `build_clips` callback + typed SSE `clip_ready` events.
- **Approval gate** — editor ticks ✓/✗ per frame after previewing stills; only approved frames get paid animation, the rest fall back to free Ken Burns. The cost estimate mirrors the selection.
