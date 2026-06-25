# HOBAILabs — Deep Technical Reference

**Audience:** CTO and engineers onboarding to or reviewing the pipeline.
**Companion deck:** `docs/HOBAILabs_CTO_Deck.html` (strategic, slide form).
**Canonical sources of truth:** `docs/HLD.md` · `docs/LLD.md` · `GUIDE.md` · `docs/OPERATOR_GUIDE.html`.
**Status:** synthesized from `main` (HLD rev 2026-06-18, LLD rev 2026-06-20).

> This document is a consolidated, narrative reference. Where it and the canonical
> `HLD.md` / `LLD.md` ever disagree, the canonical docs win — they are updated in the
> same unit of work as code (the docs-sync gate).

---

## 1. What HOBAILabs is

HOBAILabs turns a **plain-text story script + a folder of photos/videos** into a finished,
captioned, scored **9:16 MP4**. It serves three use cases on **one shared engine**:

| Mode | Entry point | Audience |
|---|---|---|
| **Story / creator** | `/` | HOB team creating personal story reels |
| **Brand / Ad (B1)** | `/brand` | Creator + brand partners doing paid collabs |
| **Studio (MODE3)** | `/studio` | Prompt → full reel with a reusable Talent/Product identity library |

The defining product principle is **realism preservation + test-cheap / finish-expensive**:
real user media is never AI-regenerated, and per-shot routing uses cheap models in Dev and
premium models in Production.

**Deployment.** Hosted on **creative.kevat.ai**, AWS **ap-south-1**. The LLM "brain" is
pluggable via `config/llm.json`; production runs **Bedrock Sonnet 4.6**. Secrets live in a
gitignored `.env`; only `.env.example` is tracked.

**Hard architecture rules.** (1) Never fork the pipeline — modes are flags into the shared
`_run_inner` + `agents/*`. (2) Use the pluggable seams (`config/*.json`), don't hardcode.
(3) Read `.agents/skills/build-feature/SKILL.md` before changing the engine.

---

## 2. System context

The engine is a **thin orchestrator** over pluggable, individually-degradable vendors. It
owns the *direction* (the taste layer); every heavy capability is rented behind a seam.

```
 Creator (browser / CLI)
      │  script + assets
      ▼
 ┌──────────────────────────────────────────────┐        ┌───────────────────┐
 │ HOBAILabs                                     │        │ LLM brain         │
 │  Web UI (Flask) — /  /brand  /studio          │──chat──▶ OpenAI / Anthropic │
 │  Render pipeline (agents/*)                   │        │ Bedrock / Gemini  │
 │  Config: models / pricing / llm / voices /    │        ├───────────────────┤
 │          watermarks .json                     │        │ Image: Flux/fal/  │
 │  Cache: ~/.hob_cache/*                        │        │        gpt-image  │
 └──────────────────────────────────────────────┘        │ Video: Kling /    │
      │  9:16 MP4                                         │        Higgsfield/ │
      ▼                                                   │        fal        │
 Output                                                   │ Lip-sync / Voice /│
                                                          │ Music / Safety    │
                                                          └───────────────────┘
```

**Three independent vendor axes**, each a JSON edit away from swapping:

- reasoning / vision / fast **LLM** → `config/llm.json`
- **image** model → `config/models.json`
- **video** model → `config/models.json`

**Graceful degradation everywhere.** Any external failure falls to a cheaper path
(premium → fallback → Kling → free Ken Burns). A render rarely hard-fails — creators lose
trust on a crash far more than on a cheaper fallback.

---

## 3. Containers / runtime pieces

| Container | Tech | Responsibility |
|---|---|---|
| **Web UI** | Flask + server-rendered templates + SSE | Parse script, preview stills, estimate cost, stream progress, serve/download output. Per-`run_id` state mirrored to a lightweight run store. |
| **Brand UI layer** | `brand.html` + `brand.js` (hooks into `main.js`) | Brief panel, brand-kit uploads, mandatories checklist, product-beat toggles. No engine fork. |
| **Studio UI layer** | `studio.html` + `studio.js` | Brief→shots planner; reusable Talent/Product identity library; per-shot talent/product/negative/continuity controls. |
| **CLI** | `argparse` + `run_caption.py` | Headless render; dry-run cost plan; all flags the UI exposes. **This is the canonical pipeline.** |
| **Render pipeline** | `agents/*` Python modules | parse → treatment → scene-design → cast → assign visuals → edit → lip-sync → animate → caption → assemble → (brand post-pass). |
| **Model router** | `agents/model_router.py` + `config/models.json` | Pure logic: shot → model id given shot type + cost tier + overrides. |
| **LLM brain** | `agents/llm.py` + `config/llm.json` | Single `chat()`/vision entry point; OpenAI / Anthropic-direct / Bedrock / Gemini; 3 tiers; JSON schema enforcement. |
| **Cost engine** | `agents/pricing.py` + `config/pricing.json` | Single source of cost truth; whole-pipeline, multi-shot-aware estimate. |
| **Caches** | `~/.hob_cache/*` (FS, optional S3) | Clips, scene designs, image descriptions, lip-sync clips+audio, generated stills. |

**No database, no message broker, no auth in the render hot path.** Web-render state lives in
process memory keyed by `run_id`; durable artifacts are files on disk and content-hash caches.
(Operator JWT auth *does* gate money/rights routes — see §11.)

---

## 4. The core data model — the `frame` dict

Everything flows through a list of mutable `frame` dicts. Each stage reads some keys and
writes others. **The pipeline is a sequence of transforms over `frames[]`.** Stages are
order-dependent (each depends on keys the previous wrote) but internally parallel (per-frame
work runs in `ThreadPoolExecutor`s).

```python
frame = {
  # identity / text (script_parser)
  "frame_id": "f03", "caption": "Bedridden, I lost my hair…",
  "duration": 7.5, "director_note": "...",

  # source selection (parser → matcher → pipeline)
  "photo_spec": "06_family.jpg" | "ai_portrait" | "ai_symbolic" | "",
  "visual_path": "/abs/path.jpg",
  "image_model_override": "", "video_model_override": "",
  "video_start_sec": 0.0, "video_end_sec": None,

  # direction (scene_intelligence)
  "scene": {"emotion","scene_description","image_prompt","motion_prompt","camera_angle"},
  "motion_override": "crane up",      # [camera:]/[motion:] beats GPT
  "edit_prompt": "add frost on the window",

  # cast / speaker (cast.py)
  "speaker_id": "narrator" | "son" | ...,
  "cast": [{"id","name","gender","age_bracket","description","voice_id"}],

  # lip-sync (lipsync_coordinator)
  "lipsync": True, "voice_override": "<11labs id>",
  "lipsync_clip_path": "/tmp/.../lipsync_f03.mp4",

  # brand mode extras
  "product_beat": False,              # real product shot — skip AI gen
  "suggestions": {"camera":[...], "edit":[...], "note":[...]},

  # studio mode extras (MODE3)
  "talent_id": "tal_…", "product_id": "prd_…",
  "talent_ref_path": "/abs/…", "negative_prompt": "...", "continuity_lock": "...",

  # per-frame caption overrides (blank = global caption_style)
  "caption_position": "top", "caption_max_lines": "2",

  # treatment — stored on frames[0]["_treatment"] as a whole-reel plan
}
```

**Why one fat, mutable dict:** it lets every agent be a near-pure transform with no shared
service. Adding a feature is typically *parse a new annotation → set a key → consume it
downstream* (see §13, Extension recipes).

---

## 5. End-to-end flow

```
 SCRIPT (.txt) + ASSETS (folder)
   │
   ▼ [Gate A] moderate_script()            OpenAI Moderation (policy floor, non-blocking on API error)
   ▼ 0. detect_cast() / apply_cast()       LLM identifies who speaks each line; builds cast[] with gender/age/voice.
   ▼ 1. parse_frame_script()               Format A/B → frames[]; auto-duration; sort-order match; suggestion chips.
   ▼ 2. design_treatment()                 Whole-reel plan: arc, motif, shot-size rhythm → frames[0]["_treatment"].
   ▼ 2b. design_all_scenes()               LLM director per frame → emotion + image_prompt + motion (parallel, cached).
   ▼ 3. visual assignment (per frame):
        ├─ real photo/video → PASSTHROUGH (realism)
        ├─ product_beat     → PASSTHROUGH (real product, never AI)
        ├─ ai_portrait      → generate_contextual_image() [+Gate B, ≤2 retries, reference_path for face consistency]
        ├─ ai_symbolic      → generate_symbolic_image() [+Gate B]
        └─ fallback         → speaker-aware subject descriptor (no hardcoded defaults)
   ▼ 3b1. ground_all_motions()             Vision-grounded motion: look at the real still, rewrite the Kling prompt.
   ▼ 3b2. edit pass                        edit_image() between gen and animation (opt-in, per frame).
   ▼ 3b3. [Gate B2] critique_image()       Vision LLM verifies the still matches its prompt.
   ▼ 3c. run_lipsync_pass()                11Labs audio → CDN upload → Hedra (photo) / SyncLabs (video); duration→audio.
   ▼ 4. build assignments[]                motion = override or scene.motion_prompt; model_id = router.select_model("video").
   ▼ 5. build_clips()                      per shot: cache → Ken Burns | Kling | Higgsfield | fal; fit to duration.
   ▼ 6. generate_frame_srt()               ASS captions timed to effective timecodes.
   ▼ 7. generate_voiceover_track (opt)     ElevenLabs/OpenAI TTS; per-speaker voice; frame-exact padding.
   ▼ 8. assemble_caption_only()            normalize → xfade/concat → captions → music (duck) → VO → 9:16 MP4.
   ▼ 8b. apply_brand_overlay() [brand]     Disclosure drawtext + logo bug + IP watermark; CTA end-card appended.
   ▼ OUTPUT .mp4
```

**Dry-run** short-circuits after stage 2b: prints the per-frame plan + `pricing.estimate()`,
spending nothing. **Brand mandatories hard-block** fires at the *start* of `/run` (before any
spend): `validate_mandatories()` → 400 + JSON checklist if logo, CTA, or product beat are missing.

---

## 6. Module map

```
run_caption.py        CLI orchestrator (canonical pipeline)
main.py               LEGACY voiceover pipeline — not maintained
web_app.py            Flask: parse/preview/estimate/run/progress(SSE)/download/brand

agents/
  script_parser.py        text → frames[] (Format A/B, annotations, auto-match)
  image_matcher.py        opt-in LLM content match (describe → assign); SQLite cache
  scene_intelligence.py   LLM director: treatment + per-frame scene design + vision-grounded motion
  llm.py                  pluggable chat()/vision brain (OpenAI|Anthropic|Bedrock|Gemini), 3 tiers + JSON schema
  shot_planner.py         Studio: brief (+scope/talent/product) → frames[] (cached, schema, fallback)
  model_router.py         shot → model id (pure logic over config/models.json)
  image_generator.py      ai_portrait/ai_symbolic → still (flux|openai|fal); prompt-hash cache
  image_editor.py         [edit:] pass on a still (gpt-image)
  safety.py               Gate A (moderation) + Gate B (face sanity) + Gate B2 (vision critique)
  cast.py                 multi-speaker detection + voice resolution per frame
  brand.py                brief extraction, mandatories gate, PIL CTA card, disclosure
  balances.py             live per-vendor credit probes (read-only, concurrent)
  watermark.py            HOB IP/property watermark resolver
  fcpxml.py               editor hand-off: build_fcpxml + build_srt
  layout.py               LAY-0 layout seam; text-card preset
  governance.py           consent + spend gate; append-only cost ledger; reserve→release→settle
  growth.py               LLM story→Format B draft + helpers (no auto-spend)
  run_store.py            restart-safe run payload/status/log; performance_* feedback
  db.py                   storage switch: SQLite default / Postgres via HOB_DB_URL
  auth.py                 operator identity: operators table, HS256 JWT, require_operator
  provenance.py           authenticity tiers (real / ai_symbolic / real-person AI)
  product_surface.py      SQLite stand-ins for assets/approvals/versions + Studio identity library
  suggestions.py          fast-tier batch → camera/edit/note chips per frame
  coverage.py             multi-shot B-roll: LLM vision assign + duration split
  lipsync_coordinator.py  audio → CDN → Hedra/SyncLabs → lipsync_clip_path
    hedra.py / synclabs.py / tts_generator.py
  clip_builder.py         still/video → animated clip (kenburns|kling|higgsfield|fal)
    higgsfield.py / fal_video.py / fal_client.py
  caption_writer.py       frames → ASS subtitle file (effective_timecodes)
  assembler.py            clips → normalize → concat/xfade → captions → music → voiceover → brand overlay
  pricing.py              whole-pipeline cost estimate (multi-shot aware)
  style_exemplars.py      opt-in in-context house-style injection
  _kv.py / cache_store.py thread-safe SQLite KVStore; BlobCache (local FS + optional S3)

config/  models.json · pricing.json · llm.json · voices.json · watermarks.json
~/.hob_cache/  kling_clips/ scene_designs/ shot_plans/ image_descriptions.db lipsync_clips/ lipsync_audio/
```

---

## 7. The director brain (`scene_intelligence.py`)

The taste layer — the part worth building deeply.

**7a. Treatment pass** — `design_treatment(frames, …) -> dict`. One call *before* per-frame
design, returning a whole-reel plan `{arc, visual_motif, shot_size_rhythm, opening_hook,
closing_resolution}`. Fed as `extra_context` into every later `design_scene()` so frames stay
thematically consistent. Strict JSON via `_TREATMENT_SCHEMA`; empty dict on error (non-fatal).

**7b. Per-frame scene design** — `design_all_scenes(frames, …)`. Per frame, one of three system
prompts by `visual_type` (`symbolic` / `contextual` / `portrait`). Returns strict JSON
`{emotion, scene_description, image_prompt, motion_prompt, camera_angle}`. Subject is **always
optional** — blank means "infer from the story." A `has_real_photo` flag tells the director to
design *motion only*. Cached by `MD5(caption, note, visual_type, subject_name, subject_description,
has_real_photo)`. Parallel via `ThreadPoolExecutor(min(n,10))`. Generic fallback on error — never raises.

**7c. Vision-grounded motion** — `ground_motion_prompt(frame)` / `ground_all_motions(frames)`.
Runs *after* stills exist: opens the actual generated image with a vision call and rewrites
`frame["scene"]["motion_prompt"]` to be visually accurate for that specific image, instead of a
generic prompt written before the image existed.

**7d. JSON schema enforcement** — `_SCENE_SCHEMA` / `_TREATMENT_SCHEMA` passed to
`llm.chat(json_schema=…)`. OpenAI uses structured outputs (`response_format`); Bedrock/Gemini get
a schema directive injected into the system prompt. Eliminates partial-JSON parse failures.

---

## 8. The pluggable brain (`llm.py`)

**Entry:** `chat(messages, *, json_mode, json_schema, max_tokens, temperature, model_tier) -> str`.

- **Provider** from `LLM_PROVIDER` env or `config/llm.json` (`openai` default).
- **Model** from `LLM_<TIER>_MODEL` env, else `config[provider][tier]`, else falls back to `reasoning`.
  `model_tier ∈ {reasoning, vision, fast}`.
- **fast tier** (`gpt-4o-mini`, `claude-haiku-4-5`, `gemini-2.5-flash-lite`) used for batch
  low-stakes calls: image descriptions, suggestion chips, content match.
- **Provider-neutral messages:** `content` is a string or a list of `{type:text}` / `{type:image,
  path|data_uri}` parts. Each backend translates:
  - **OpenAI** — `image_url` data-URIs; strict structured outputs.
  - **Anthropic (direct API)** — top-level `system`, typed text/image (base64) blocks,
    `ANTHROPIC_API_KEY`. Independent of Bedrock/Marketplace; the working Claude path when Bedrock
    isn't entitled. Bare model ids; `temperature` auto-dropped for Opus 4.7/4.8/Fable (they 400 on sampling).
  - **Bedrock Converse** — system blocks separated; images as raw bytes; IAM auth. Versioned
    `us.*` inference-profile ids; needs a Marketplace agreement (account-gated).
  - **Gemini** — `system_instruction` + PIL images.
- Singleton clients cached so heavy SDK init happens once per process.

```json
// config/llm.json tiers
{
  "openai":    {"reasoning":"gpt-4.1", "vision":"gpt-4o", "fast":"gpt-4o-mini"},
  "anthropic": {"reasoning":"claude-sonnet-4-6", "vision":"claude-sonnet-4-6", "fast":"claude-haiku-4-5"},
  "bedrock":   {"reasoning":"us.anthropic.claude-sonnet-4-6", "fast":"us.anthropic.claude-haiku-4-5-20251001-v1:0"},
  "gemini":    {"reasoning":"gemini-2.5-flash", "fast":"gemini-2.5-flash-lite"}
}
```

---

## 9. The model router (`model_router.py`)

**Entry:** `select_model(kind, shot, cost_tier="draft", override="") -> str`. Pure logic + JSON
read, unit-testable, no API calls. Resolution order:

1. **Valid override wins** — must be a real model of the right `kind`.
2. **Image step + real media → `PASSTHROUGH`** (`_is_real_media` / `_is_video_source`).
3. **Route by shot type + tier:** `config.routing[kind][shot_type][tier]` → first id in `models`.
4. **Fallback** to `config.defaults[kind]`.

**Shot classification:** `cost_tier_from_quality` maps `dev|draft|preview → draft`, else
`premium`. Image: `ai_symbolic → object`, else `face`. Video: `lipsync → dialogue`, real → `real`,
`ai_symbolic → landscape`, hero/index-0 → `hero`, else `face`.

> **Sharp edge:** `pricing.estimate` must keep mirroring `select_model` exactly, or quoted cost
> diverges from billed cost.

---

## 10. Cast, voice, brand, studio

**Cast (`cast.py`).** `detect_cast(frames, …)` — one reasoning call on the full script → cast
members `{id, name, gender, age_bracket, description}` (NARRATOR_ID always present). `apply_cast()`
sets `frame["speaker_id"]`; a `[speaker:]` annotation overrides. **Voice priority chain** (first
non-empty wins): `[voice:]` override → `voice_map[speaker_id]` (UI) → `voices.json roles[speaker_id]`
→ `roles[gender_age_bracket]` → global default. `subject_descriptor()` returns who is on screen —
never a hardcoded sample name.

**Brand (`brand.py`).** `extract_brief()` is **parse-only** ("copy verbatim — do NOT rephrase"):
returns `{name, product, objective, key_message, cta_text, cta_url, tagline}`; only empty UI fields
are filled, operator edits win. `validate_mandatories()` hard-blocks at the top of `/run` (logo, CTA,
≥1 product beat, brand-audio paths if selected). `build_cta_card()` PIL end-card.
`apply_brand_overlay()` burns `"Paid partnership with {brand}"` via ffmpeg drawtext + optional corner
logo — isolated post-pass.

**Studio / MODE3 (`shot_planner.py` + `product_surface.py`).** Third front door, a mode-hook like
brand. `plan(brief, *, scope, talent, product, mood)` — one reasoning call (`_PLAN_SCHEMA`), cached
at `~/.hob_cache/shot_plans/<md5>`. `scope="commerce"` → one locked subject × N camera setups with
product beats flagged; `scope="general"` → emotional beats; graceful sentence-split fallback. The
**identity library** stores reusable Talent (face) and Product (ref + specs); `talent_id` →
`talent_ref_path` used by `generate_contextual_image(reference_path=…)` for the identity-edit lock;
`product_id` on a product beat makes the real product image the i2v start frame (passthrough).
A separate **`character_ref_path`** (Story/Brand) locks a speaker's portraits to a user-supplied
face — honored **only when `character_ref_consent` is true** (the face may be a real person).

---

## 11. Safety &amp; governance

**Gates (`safety.py`)** — each catches a different failure class:

| Gate | Function | When | Blocks |
|---|---|---|---|
| **A** | `moderate_frames` / `moderate_script` | Before scene design | Harmful / policy-violating content (non-blocking on API error) |
| **B** | `check_face_sanity` | After image gen (≤2 retries) | Deformed face, bad dimensions, file < 10 KB |
| **B2** | `critique_image` | After stills, before motion | Blank/abstract/empty when a real subject was expected |
| **Brand** | `critique_brand` | After stills on brand runs | Visual conflicts with brand safety requirements |

**Governance invariants (CLAUDE.md §5):**

- Real media and product beats are **never** AI-regenerated.
- AI **never writes brand ad claims** — on-screen/spoken copy is operator-supplied verbatim and
  must pass `safety.moderate_*`, shown editable before any spend.
- AI likeness of a **named real person** (face/voice) requires recorded consent
  (`governance.validate_likeness_consent` / `record_likeness_consent`).
- Consent + spend-cap gates must pass before paid / external / real-person renders.

**Operator identity (`auth.py`).** Money/rights routes — `/run`, `/preview`, `/retry`,
`/performance*`, `/project-version`, `/brand-approval` (needs `approver` role) — are wrapped by
`require_operator(*roles)`, which validates the cookie/Bearer HS256 JWT and injects the *verified*
operator (handlers no longer trust a client-supplied id). `HOB_AUTH_DISABLED=1` bypasses for local dev.

**Spend governance (`governance.py`).** **reserve → release → settle**, never a single write.
`reserve_spend()` (`BEGIN IMMEDIATE`, serialized) holds the estimate *before* dispatch;
`release_reservation()` runs on **both** success and failure paths (idempotent); `record_cost_event()`
settles the actual into an **append-only** `cost_events` ledger. `sweep_stale_reservations()` runs once
on web startup so a killed process can't permanently inflate a cap.

**Provenance (`provenance.py`).** Authenticity tiers — real / ai_symbolic / AI-likeness-of-a-real-
person — surfaced at `/provenance/<run_id>`.

---

## 12. Cross-cutting concerns

**Cost.** A single `pricing.estimate()` walks `frames[]` and mirrors the router's actual choices so
UI/CLI estimate matches billing. Dev caps clips at 5s. `/api/estimate` returns a structured
breakdown — no client-side cost logic.

**Upfront credit visibility (`balances.py`).** Read-only, concurrent per-vendor probes surface a
low wallet *before* a run. Live numbers from ElevenLabs (`/v1/user/subscription`), Kling
(`/account/costs`), Suno (`/api/v1/generate/credit`), fal (`rest.alpha.fal.ai/billing/user_balance`);
OpenAI/Gemini/Bedrock/Higgsfield/Hedra/SyncLabs expose no usable balance API and are labelled so.
Endpoints are plan/alpha-dependent and degrade to `error`/`unsupported`, never break the page.

**Caching (invalidation rules):**

| Cache | Key | Location | Busted by |
|---|---|---|---|
| Animation clips | `MD5(image + motion + duration)`, model-namespaced | `~/.hob_cache/kling_clips/` (BlobCache, S3-opt) | image / motion / duration / model change |
| Scene designs | `MD5(caption, note, type, subject, desc, has_photo)` | `~/.hob_cache/scene_designs/` | any of those |
| Image descriptions | image content hash | `image_descriptions.db` (SQLite WAL) | file content change (rename-safe) |
| Lip-sync clips | `MD5(media + audio)` | `lipsync_clips/` | media or audio change |
| Lip-sync audio | `MD5(caption + voice_id)` | `lipsync_audio/` | caption or voice change |
| Generated stills | `ai_portrait_{fid}_{prompt_hash}.jpg`, ≥ 50 KB | the **asset folder** | prompt change (new hash) |

**Concurrency &amp; failure matrix:**

| Stage | Pool | Cap | On failure |
|---|---|---|---|
| Scene design | ThreadPool | 10 | generic fallback scene (no raise) |
| Image gen | serial / frame | — | fallback model → Gate B retry ×2 → accept last |
| Lip-sync | ThreadPool ×2 | 6 submit / N poll | clear `lipsync` → animate normally |
| Clip build | ThreadPool | `min(model max_concurrent)` | retry-on-limit; else Ken Burns for that frame |
| Assembly | single ffmpeg | — | raises (render fails; temp dir kept) |
| Brand overlay | single ffmpeg post-pass | — | raises (logged; main MP4 already done) |

**Editor iteration loop** (story + brand both, via the shared frame-card UI / `_run_inner`):
per-frame **redo** (`/redo-still`, `/redo-motion`, single-frame spend only), **progressive reveal**
(`build_clips(on_clip_ready=…)` + typed SSE `clip_ready` events), and an **approval gate** (only
approved frames get paid animation; the rest fall back to free Ken Burns via the `"kenburns"`
`model_id` sentinel — `pricing.estimate(approved_ids=…)` mirrors it).

**Security / secrets.** Keys in `.env`; `/media` path-containment via `_path_allowed()`;
`ASSETS_BROWSE_ROOT` scopes the server folder browser. **IP watermarking:** every reel can be tagged
with one HOB property (full-frame transparent PNG composited over the whole video in both modes via
`apply_brand_overlay`) — distinct from the brand-collab advertiser logo; no-op if the PNG is absent.

---

## 13. Extension recipes &amp; sharp edges

**Recipes (the seams in action):**

- **Add a video/image model:** add a `models` entry (`backend`, `pricing_key`, `fal_endpoint`,
  `max_concurrent`) in `config/models.json`, list under `routing[kind][shot_type][tier]`, add price
  to `config/pricing.json`. No Python change for fal-hosted models.
- **Swap an LLM tier:** set `LLM_PROVIDER` (+ keys) or edit `config/llm.json`. Callers untouched.
- **New frame annotation:** add a regex in `_parse_format_b`, a clean-up `re.sub`, and a frame key;
  consume it downstream.
- **New voice role:** add a key to `config/voices.json roles` + fill the ElevenLabs id.

**Sharp edges (read before editing):**

- Generated stills land in the **user's asset folder**; the parser's `_DERIVED_MARKERS` filter stops
  them corrupting positional auto-match. Touch both together.
- `pricing.estimate` must mirror `model_router.select_model` exactly.
- `kling`/`higgsfield` clip-cache keys are **deliberately legacy-formatted** so previously-paid clips
  still hit — do not "clean up" those token formats.
- Still-cache key includes a **prompt hash**; the old `ai_portrait_{fid}.jpg` scheme is gone.
- Lip-sync uploads user media to an **external CDN** — treat as a privacy boundary.
- `subject_name` / `subject_description` are **always optional** — never default to a sample name;
  fallbacks must use `cast.subject_descriptor()`.
- `brand.extract_brief()` is **parse-only** — adding LLM creativity breaks the "AI never writes ad
  copy" guarantee.
- `effective_timecodes()` must be used for **all** audio timing (captions, voiceover adelay, ducking,
  lip-sync) or audio drifts at the 0.4s crossfade junctions.
- The approval gate's `"kenburns"` sentinel must stay in lockstep across `web_app._video_model_for()`,
  `clip_builder._resolve_model_id()`, and `pricing.estimate(approved_ids=…)`.
- `_redo_seed` must be injected **after** `design_all_scenes` (which replaces `f["scene"]` wholesale).
- Every spend route must call `reserve_spend` before dispatch — including `/retry`.
- macOS assumptions: `sips` (HEIC), Baskerville font dir — Linux needs alternatives; Montserrat bundled.
- `main.py` is the **legacy** voiceover pipeline; the maintained path is `run_caption.py` / `web_app.py`.

---

## 14. Risks, constraints &amp; roadmap

**Risks / constraints (today):**

| Risk | Impact | Mitigation |
|---|---|---|
| fal.ai slugs + prices marked `VERIFY` | Paid render could hit a dead/mis-priced endpoint | Dev safe; OPERATOR_GUIDE warns to verify before paid runs |
| Provider parallel limits (Kling 4, Veo 2, Higgsfield 4) | 429s, dropped frames | Capped pool + retry-on-limit |
| Lip-sync media to external CDN | Private media leaves the box | Documented, opt-in; self-hosted signed URLs on roadmap |
| In-memory web run state (no DB) | Restart loses in-flight runs; no horizontal scale | Acceptable single-operator; durable store = SCALE_PLAN Phase 0 |
| No automated tests | Regressions land silently | Router/pricing/cast are pure functions — prime unit-test candidates |
| macOS-specific bits | Reduced portability off macOS | Dockerfile exists; Montserrat bundled |

**Roadmap alignment.** **Landed:** Gates A/B/B2, face-consistency reference path, prompt-hash stills
cache, multi-speaker cast, treatment pass, vision-grounded motion, suggestion chips, brand mode B1,
server-side `/api/estimate`, effective-timecodes audio sync, lip-sync, multi-shot B-roll, voiceover
mode, Studio mode (WIP on `main`). **Next:** SCALE Phase 0 (durable RUNS_DIR + runs DB, resume, cost
ledger, backups), SCALE Phase 1 (job queue, multi-worker, signed media URLs), B2 (kinetic
motion-graphics: kinetic typography, badges, price callouts, product PIP), P3 (CLIP score-based
matching, GPU-side).

---

## 15. Glossary

- **Frame / beat** — one shot in the reel; one `frame` dict.
- **Shot type** — router classification (`face`, `object`, `real`, `landscape`, `hero`, `dialogue`).
- **Cost tier** — `draft` (Dev, cheap) vs `premium` (Production).
- **PASSTHROUGH** — sentinel: "real media, skip image generation"; also brand product beats.
- **Gate A / B / B2 / Brand** — script moderation / face sanity / vision critique / mandatories block.
- **Treatment** — whole-reel arc plan produced before per-frame design.
- **Effective timecodes** — cumulative timecodes accounting for the 0.4s crossfade overlap per junction.
- **Fast tier** — third LLM tier for batch low-stakes calls.
- **Per-frame redo / progressive reveal / approval gate** — the editor iteration loop.

---

*Generated as a consolidated reference. Keep the canonical `HLD.md` / `LLD.md` authoritative; update
this doc when they move (docs-sync gate, build-feature rule 11).*
