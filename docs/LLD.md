# HOBAILabs — Low-Level Design (LLD)

**Revision:** 2026-06-20 · current `main` branch
**Companion:** [HLD.md](HLD.md) — system context, flow, decisions
**Audience:** engineers modifying the pipeline. File references are clickable.

---

## 0. Module Map

```
run_caption.py            CLI orchestrator (the canonical pipeline)
main.py                   Legacy voiceover pipeline (segmenter→TTS→match→assemble) — not maintained
web_app.py                Flask server: parse/preview/estimate/run/progress(SSE)/download/brand

agents/
  script_parser.py        text → frames[]  (Format A/B, annotations, auto-match)
  image_matcher.py        opt-in LLM content match (describe → assign); SQLite cache
  scene_intelligence.py   LLM director: treatment pass + per-frame scene design + vision-grounded motion
  llm.py                  pluggable chat()/vision brain (OpenAI|Anthropic|Bedrock|Gemini), 3 tiers + JSON schema
  shot_planner.py         Studio mode: brief (+scope/talent/product) → frames[] (cached, schema, fallback)
  canvas_run.py           Director Canvas orchestrator: staged state machine over the shared agents.
                          Sequences + gates only — cost via pricing.estimate (sliced per stage),
                          real-vs-AI via model_router; NEVER re-implements cost/routing/render. State
                          stored inside the run payload (run_store). new_canvas/run_stage/approve/
                          invalidate_from/public_state; PaidStageDispatch signals render reuse.
  model_router.py         shot → model id (pure logic over config/models.json)
  image_generator.py      ai_portrait/ai_symbolic → still (flux|openai|fal backends); prompt-hash cache
  image_editor.py         [edit:] pass on a still (gpt-image)
  safety.py               Gate A (moderation) + Gate B (face sanity) + Gate B2 (vision critique)
  cast.py                 multi-speaker detection + voice resolution per frame
  brand.py                brief extraction, mandatories gate, PIL CTA card, disclosure
  balances.py             live per-vendor credit/balance probes (read-only, concurrent)
  watermark.py            HOB IP/property watermark resolver (config/watermarks.json → PNG)
  fcpxml.py               editor hand-off: build_fcpxml (importable timeline) + build_srt
  layout.py               LAY-0 layout seam; text-card preset renders to stills
  governance.py           F1/F2 consent + spend gate; append-only cost_events ledger,
                          reserve→release→settle spend model, startup stale-reservation sweep
  growth.py               STR-2 LLM story→Format B draft + STR-3b/4/5 helpers
                          (editable drafts/descriptors, no auto-spend)
  run_store.py            F3 restart-safe run payload/status/log bridge; performance_* (Gap #3)
  db.py                   Gap #2 storage switch: SQLite default / Postgres via HOB_DB_URL
  auth.py                 Gap #1 operator identity: operators table, HS256 JWT, require_operator, CLI
  provenance.py           Gap #5 authenticity tiers (real / ai_symbolic / real-person AI)
  product_surface.py      SQLite stand-ins for assets, approvals, project versions,
                          + Studio identity library (talents, products) CRUD
  suggestions.py          fast-tier batch → camera/edit/note chips per frame
  coverage.py             multi-shot B-roll: LLM vision assign + duration split
  lipsync_coordinator.py  audio → CDN → Hedra/SyncLabs → lipsync_clip_path
    hedra.py / synclabs.py    vendor clients
    tts_generator.py          ElevenLabs/OpenAI TTS; frame-exact padding/trim; per-speaker voice
  clip_builder.py         still/video → animated clip (kenburns|kling|higgsfield|fal)
    higgsfield.py / fal_video.py / fal_client.py   vendor clients
  caption_writer.py       frames → ASS subtitle file (uses effective_timecodes)
  beat_track.py           music → onset/beat times (ffmpeg + numpy, no librosa; graceful [])
  assembler.py            clips → normalize → concat/xfade → captions → music → voiceover
                          → apply_brand_overlay (brand post-pass);
                          P1 beat-aware per-junction overlaps (cut on beat / dissolve off-beat)
  pricing.py              whole-pipeline cost estimate (config/pricing.json); multi-shot aware
  style_exemplars.py      opt-in in-context house-style injection (USE_EXEMPLARS=1)
  _kv.py                  thread-safe SQLite KVStore (WAL mode, per-key writes)
  cache_store.py          BlobCache abstraction (local FS + optional S3 read-through)

config/
  models.json             model catalog + routing
  pricing.json            per-model costs
  llm.json                provider + model per tier (reasoning/vision/fast)
  voices.json             role → ElevenLabs voice_id map (narrator/gender/age defaults)

deploy/fonts/
  Montserrat-Regular.ttf, Montserrat-Italic.ttf  (OFL; installed via Dockerfile + fc-cache)

~/.hob_cache/
  kling_clips/            animation clips (BlobCache; S3-backed when enabled)
  scene_designs/          per-frame JSON (MD5-keyed)
  shot_plans/             Studio brief→frames plans (MD5 of brief+scope+talent+product)
  image_descriptions.db   SQLite WAL (image content-hash keyed)
  lipsync_clips/          finished lip-sync clips (BlobCache)
  lipsync_audio/          ElevenLabs TTS segments (BlobCache)
```

---

## 1. `script_parser.py` — text → `frames[]`

**Entry:** `parse_frame_script(script_path, assets_dir, max_frame_dur=9.0, smart_match=False)`
→ [agents/script_parser.py:154](../agents/script_parser.py#L154)

- **Format detection:** presence of `visual:` → Format A; else Format B (HOB format).
- **Format B parsing** ([script_parser.py:56](../agents/script_parser.py#L56)): split on `Caption:`
  (Instagram text, dropped), strip leading `Reels`, split body on `\bFrame\s*\d+\b`.
  Frame **numbers are positional, not authoritative** — re-indexed `f01..fNN`.
- **Annotations** (regex, case-insensitive, removed from the on-screen caption):
  `[note:]`→`director_note`, `[photo:]`→`photo_spec`, `[edit:]`→`edit_prompt`,
  `[camera:]`/`[motion:]`→`motion_override` (camera wins if both),
  `[lipsync: yes|true|1]`→`lipsync`, `[voice:]`→`voice_override`,
  `[speaker:]`→`speaker_id` (optional manual override of LLM-detected speaker),
  `[model:]`→both overrides, `[imgmodel:]`/`[vidmodel:]`→single-step override,
  `[duration:]`, `[start:]`→`video_start_sec`, `[end:]`→`video_end_sec`.
- **Duration auto-calc** `_frame_duration` ([:26](../agents/script_parser.py#L26)):
  `0 words → 2.5s` (silent); else `max(3.5, min(max_dur, words/2.0))`. `max_frame_dur`
  is 5.0 in Dev. `[duration:]` overrides.
- **Auto-match for unpinned frames** ([:191](../agents/script_parser.py#L191)):
  1. If `smart_match`, call `image_matcher.smart_match` (fills what it can).
  2. Positional fallback: sorted source files, skipping already-used and **derived
     artifacts** (`_edited`, `_portrait`, `ai_*`, `_5s`, … — see `_DERIVED_MARKERS`
     [:180](../agents/script_parser.py#L180)) so a leftover edited copy can't shift alignment.

**Gotcha:** the parser exposes the matched filename back into `photo_spec` so the UI
can show/override it; downstream `_is_real_media` treats a non-`ai_` `photo_spec` as real.

---

## 2. `image_matcher.py` — opt-in content matching

**Entry:** `smart_match(frames, assets_dir, is_source_media) -> bool`
→ [agents/image_matcher.py:229](../agents/image_matcher.py#L229)

Two LLM stages, both cached, both safe (return `False`/positional on any failure):

1. **`describe_images(paths)`** ([:131](../agents/image_matcher.py#L131)) — one fast-tier
   vision call per file → 1–2 sentence description **including any text/names visible**.
   Cached **forever** by image content hash in `~/.hob_cache/image_descriptions.db` (SQLite WAL).
   **Videos** are sampled into up to 3 keyframes (`ffmpeg` at 15/50/85%) and described
   as `(real video clip) …` ([:82](../agents/image_matcher.py#L82)).
2. **`assign_images(frames, descriptions)`** ([:175](../agents/image_matcher.py#L175))
   — one reasoning call returning `{frame_id: image_number}`. Prompt biases toward
   names/text matches and **prefers real video clips over stills** for equal fit.

Only fills frames with no `photo_spec`, no `ai_*`, excludes pinned files; never
touches the animation stage.

---

## 3. `scene_intelligence.py` — the director

### 3a. Treatment pass
**Entry:** `design_treatment(frames, subject_name="", subject_description="", extra_context="") -> dict`

Called once before per-frame design. Returns a whole-reel plan:
`{arc, visual_motif, shot_size_rhythm, opening_hook, closing_resolution}`.
The treatment dict is fed as `extra_context` into every subsequent `design_scene()` call
so individual frames stay thematically consistent.

Strict JSON enforced via `_TREATMENT_SCHEMA`. On LLM error, returns an empty dict (non-fatal).

### 3b. Per-frame scene design
**Entry:** `design_all_scenes(frames, subject_name="", subject_description="", extra_context="")`
→ [agents/scene_intelligence.py:189](../agents/scene_intelligence.py#L189)

- Per-frame `design_scene()` picks one of three system prompts by `visual_type`:
  `symbolic` (objects, no people), `contextual` (age/era-accurate portrait), or `portrait`/default.
- Returns strict JSON via `_SCENE_SCHEMA`:
  `{emotion, scene_description, image_prompt, motion_prompt, camera_angle}`.
- **Subject is always optional.** When `subject_name/description` are blank, system prompts
  say "infer from the story itself" — no hardcoded defaults.
- **`has_real_photo`** flag tells the director to design *motion only* and skip the image prompt.
- **Caching:** `MD5(caption, note, visual_type, subject_name, subject_description, has_real_photo)` → JSON in `~/.hob_cache/scene_designs/`.
- **Parallelism:** `ThreadPoolExecutor(max_workers=min(n,10))`. Silent frames get a canned slow-zoom-out scene.
- **Fallback:** on LLM error, a generic photoreal prompt + slow push-in is returned (never raises).

### 3c. Vision-grounded motion
**Entry:** `ground_motion_prompt(frame) -> str` / `ground_all_motions(frames) -> None`

Called *after* stills are generated (stage 3b1 in HLD flow). Opens the actual generated still
with a vision LLM call and rewrites `frame["scene"]["motion_prompt"]` to be visually accurate
for the specific image — instead of a generic motion prompt written before the image existed.

### 3d. JSON schema enforcement
`_SCENE_SCHEMA` and `_TREATMENT_SCHEMA` are passed to `llm.chat(json_schema=...)`.
OpenAI uses structured outputs (`response_format`); Bedrock/Gemini get a schema directive injected
into the system prompt. This eliminates partial-JSON parse failures.

---

## 4. `llm.py` — the pluggable brain

**Entry:** `chat(messages, *, json_mode, json_schema, max_tokens, temperature, model_tier) -> str`
→ [agents/llm.py:105](../agents/llm.py#L105)

- **Provider** from `LLM_PROVIDER` env or `config/llm.json` (`openai` default).
- **Model** from `LLM_<TIER>_MODEL` env, else `config[provider][tier]`, else falls back to `reasoning`.
  `model_tier` is `"reasoning"` | `"vision"` | `"fast"`.
- **`fast` tier** (`gpt-4o-mini`, `claude-haiku-4-5`, `gemini-2.5-flash-lite`) used for
  batch low-stakes calls: image descriptions, suggestion chips, image content match.
- **`json_schema` param:** OpenAI → `response_format={type:"json_schema", json_schema:{...}}`
  (strict structured outputs); Bedrock/Gemini → schema injected as a system directive.
- **Message format is provider-neutral:** `content` is a string or a list of
  `{type:text}` / `{type:image, path|data_uri}` parts. Each backend translates:
  - **OpenAI** ([:121](../agents/llm.py#L121)) — `image_url` data-URIs.
  - **Anthropic (direct API)** (`_anthropic_chat`) — top-level `system`, typed text/image (base64) blocks, `ANTHROPIC_API_KEY`. Independent of Bedrock/Marketplace; the working Claude path when Bedrock isn't entitled. Bare model ids (no `us.*`/version suffix). `temperature` auto-dropped for Opus 4.7/4.8/Fable (they 400 on sampling params).
  - **Bedrock Converse** — system blocks separated; images as raw bytes; IAM auth. Versioned `us.*` inference-profile ids required; needs a Marketplace agreement (account-gated).
  - **Gemini** — `system_instruction` + PIL images.
- **`json_loads_lenient`** strips ```` ```json ```` fences and slices outer braces. JSON enforcement: OpenAI strict structured outputs; Anthropic/Bedrock/Gemini get a schema/JSON directive injected into the system prompt.
- Singletons: `_openai_client()`, `_anthropic_client()`, and `_bedrock_client()` are cached so the heavy SDK init happens once per process.

**config/llm.json tiers:**
```json
{
  "openai":    {"reasoning":"gpt-4.1", "vision":"gpt-4o", "fast":"gpt-4o-mini"},
  "anthropic": {"reasoning":"claude-sonnet-4-6", "vision":"claude-sonnet-4-6", "fast":"claude-haiku-4-5"},
  "bedrock":   {"reasoning":"us.anthropic.claude-sonnet-4-6", "fast":"us.anthropic.claude-haiku-4-5-20251001-v1:0"},
  "gemini":    {"reasoning":"gemini-2.5-flash", "fast":"gemini-2.5-flash-lite"}
}
```

---

## 5. `model_router.py` — shot → model id

**Entry:** `select_model(kind, shot, cost_tier="draft", override="") -> str`
→ [agents/model_router.py:104](../agents/model_router.py#L104). Pure logic + JSON read, unit-testable, no API calls.

Resolution order:
1. **Valid override wins** — `override` must be a real model of the right `kind`.
2. **Image step + real media → `PASSTHROUGH`** (`_is_real_media`/`_is_video_source`).
3. **Route by shot type + tier:** `config.routing[kind][shot_type][tier]` → first id in `models`.
4. **Fallback** to `config.defaults[kind]`.

**Shot classification:**
- `cost_tier_from_quality`: `dev|draft|preview → draft`, else `premium`.
- Image (`_image_shot_type`): `ai_symbolic→object`, else `face`.
- Video (`_video_shot_type`): `lipsync→dialogue`, real→`real`, `ai_symbolic→landscape`, hero/index-0→`hero`, else `face`.

---

## 6. `cast.py` — multi-speaker detection + voice resolution

**Entry:** `detect_cast(frames, narrator_name, narrator_description) -> list[dict]`
**Secondary:** `apply_cast(frames, cast, narrator_name, narrator_description) -> None`
→ [agents/cast.py](../agents/cast.py)

### Cast detection
One LLM reasoning call on the full script text. Returns a list of cast members:
```python
{"id": "narrator", "name": "...", "gender": "female", "age_bracket": "adult", "description": "..."}
{"id": "son",      "name": "...", "gender": "male",   "age_bracket": "child", "description": "..."}
```
`NARRATOR_ID = "narrator"` is always present.

`apply_cast()` assigns `frame["speaker_id"]` per frame based on the LLM output.
A `[speaker:]` script annotation overrides the detected speaker.

### Voice resolution
**`voice_for_frame(frame, default_voice_id, voice_map) -> str`**

Priority chain (first non-empty wins):
1. `frame["voice_override"]` — explicit `[voice:]` annotation in the script
2. `voice_map[speaker_id]` — operator selection in the Cast voices UI panel
3. `voices.json roles[speaker_id]` — if speaker_id matches a role key
4. `voices.json roles[gender_age_bracket]` — e.g. `female_adult`, `child`
5. `default_voice_id` — the global voice picker

**`subject_descriptor(frame, narrator_description) -> str`**
Returns a visual description of who should be on screen for this frame — `narrator_description`
for narrator frames, or the cast member's description for quoted-speaker frames. Used by
`image_generator` as the subject in AI portrait prompts (never hardcoded).

**config/voices.json:**
```json
{"roles": {"narrator": "", "male_adult": "", "female_adult": "", "child": "", "elderly_male": "", "elderly_female": ""}}
```
All values empty by default — fill with your ElevenLabs voice IDs for role-based defaults.

---

## 6b. `balances.py` — live vendor credit probes

**Entry:** `all_balances() -> list[dict]` → [agents/balances.py](../agents/balances.py)

Read-only, concurrent (`ThreadPoolExecutor`), never raises — surfaced at `GET /balances`
and a "💳 AI Credits" panel on both story and brand pages. Each probe returns
`{vendor, label, status, balance, unit, detail}` where `status` ∈
`ok | no_key | unsupported | error`; results sort `ok → error → no_key → unsupported`.

**What actually returns a live number (verified against the live `.env`):**

| Vendor | Status | How |
|---|---|---|
| **ElevenLabs** | ✅ ok | `GET /v1/user/subscription` (`xi-api-key`) → `character_limit − character_count` |
| **Kling** | ✅ ok | `GET /account/costs` (JWT, 30-day window) → sum `resource_pack_subscribe_infos[].remaining_quantity` |
| **Suno (sunoapi.org)** | ✅ ok | `GET /api/v1/generate/credit` (Bearer) → credits |
| **fal.ai** | ✅ ok | `GET rest.alpha.fal.ai/billing/user_balance` (`Key`) → USD *(alpha endpoint, best-effort)* |
| **Higgsfield** | — unsupported | no public REST balance path on `platform.higgsfield.ai` (probes 3 candidates, then degrades) |
| **OpenAI / Gemini / Bedrock / Hedra / SyncLabs** | — unsupported | no usable balance API (documented reason per vendor) |

Only vendors whose key is configured appear. Run standalone: `python -m agents.balances`.
**Sharp edge:** the Kling `/account/costs` and fal `rest.alpha.fal.ai` endpoints are
plan/alpha-dependent — if a vendor changes them, the probe degrades to `error`/`unsupported`,
never breaks the page. Pair with [governance.py](../agents/governance.py) ledger for the
"will this render need a recharge?" pre-flight.

## 7. `brand.py` — brief extraction, mandatories, CTA card

→ [agents/brand.py](../agents/brand.py)

### `extract_brief(text: str) -> dict`
Parse-only LLM call. System prompt says "copy verbatim — do NOT rephrase or summarise."
Returns `{name, product, objective, key_message, cta_text, cta_url, tagline}`.
Only empty fields in the UI are filled; operator edits always win.

### `validate_mandatories(frames, brand) -> list[str]`
Hard-block gate called at the **top of `/run`** before any spend. Returns a list of
failure strings; empty list = proceed. Checks:
- `brand["logo_path"]` is set and non-empty
- `brand["cta_text"]` is set and non-empty
- At least one frame has `product_beat = True`
- If `vo_mode == "brand_audio"` → `vo_audio_path` must be set
- If `music_mode == "brand_audio"` → `music_audio_path` must be set

### `build_cta_card(brand, out_path, width, height) -> str`
PIL-generated end card: brand colour background, logo image, CTA text (Montserrat preferred),
CTA URL. Written to `out_path` and appended as the final frame in `_generate_stills()`.

### `apply_brand_overlay(in_path, out_path, disclosure_text, logo_path, logo_corner, disclosure_secs) -> str`
Post-pass on the assembled MP4. Uses `ffmpeg drawtext` to burn
`"Paid partnership with {brand_name}"` into the first N seconds. Optionally composites
a corner logo bug via `ffmpeg overlay` filter. Isolated function — does not touch the
4 existing assembly branches.

### `disclosure_text(brand) -> str`
Returns `"Paid partnership with {name}"` or `"Paid partnership with the brand"` fallback.

---

## 7b. Studio mode (MODE3) — `shot_planner.py` + identity library

→ [agents/shot_planner.py](../agents/shot_planner.py), [agents/product_surface.py](../agents/product_surface.py). See [MODE3_PLAN.md](MODE3_PLAN.md).

Studio mode is the third front door (`/studio`), a mode-hook over the shared
engine (like brand). The user types a brief; `shot_planner.plan()` expands it to
the SAME `frames[]` schema the other doors produce, so `_build_frames_from_payload`
→ `_generate_stills` → `clip_builder` → `assembler` run unchanged.

### `shot_planner.plan(brief, *, scope, talent, product, mood) -> list[dict]`
- One reasoning-tier LLM call (`json_schema=_PLAN_SCHEMA`), cached at
  `~/.hob_cache/shot_plans/<md5>.json` (key = brief+scope+talent+product).
- `scope="commerce"` → one locked subject × N camera setups (intro→…→product
  hero→final), product beats flagged. `scope="general"` → emotional beats.
- Graceful fallback to a sentence/line split so a failure never blocks the user.
- `DEFAULT_NEGATIVE` is the per-shot negative prompt prefilled on each frame.

### Identity library (`product_surface.py`)
- `register_talent / get_talent / list_talents / delete_talent` — a reusable face;
  the reference image is stored in the `assets` table (kind="talent").
- `register_product / get_product / list_products / delete_product` — a reusable
  product (reference image + `specs`).
- Reference resolution happens in `web_app._build_frames_from_payload`:
  `talent_id` (+`uses_talent`) → `frame["talent_ref_path"]` (used by
  `generate_contextual_image(reference_path=…)` for the identity-edit lock);
  `product_id` on a product beat → the real product image becomes the i2v start
  frame (passthrough, never regenerated).

### New `frame` keys
`talent_id`, `product_id`, `talent_ref_path`, `negative_prompt`, `continuity_lock`,
`uses_talent`, `studio_scope` (payload). `continuity_lock` is appended to the
image prompt in `_generate_stills`; `negative_prompt` is threaded into
`clip_builder._kling_submit` (falls back to `DEFAULT_KLING_NEGATIVE`).

### Character face (Story/Brand) — `character_ref_path`
Optional user-supplied face that locks a speaker's AI portraits to one chosen face
(no asset folder needed). Payload: `character_refs` `{speaker_id: server_path}` +
`character_ref_consent` (bool). `_build_frames_from_payload` honors `character_refs`
**only when `character_ref_consent` is true** (the face may be a real person — §5
AI-likeness consent gate) and sets `frame["character_ref_path"]` per speaker. In
`_generate_stills` the identity-anchor precedence is **`talent_ref_path` →
`character_ref_path` → first-portrait `face_ref`**; the chosen ref is passed to
`generate_contextual_image(reference_path=…)` so every portrait of that speaker
reference-edits to the same face. Degradable: missing/invalid path falls through.

### Governance (MODE3_PLAN §2)
AI may draft on-screen lines in Studio; all frame text still passes Gate A
(`safety.moderate_frames` in `_generate_stills`). Regulated ad claims / CTA /
disclosure remain user-supplied verbatim only when running **brand** mode
(`mode=="brand"`); Studio is not brand mode.

---

## 8. `suggestions.py` — AI suggestion chips

**Entry:** `suggest_for_frames(frames, max_each=3) -> None`
→ [agents/suggestions.py](../agents/suggestions.py)

One batched **fast-tier** LLM call at parse time. For each frame returns up to
`max_each` suggestions each for:
- `camera` — camera motion ideas (e.g. "dolly in", "crane up")
- `edit` — image edit ideas (e.g. "add soft fog", "warmer golden light")
- `note` — director note ideas (e.g. "head high, direct gaze, warm backlight")

Results stored in `frame["suggestions"]`. UI renders them as clickable chips below
the relevant input boxes. Clicking a chip fills the box but leaves it fully editable —
not locked.

**Vision-grounded per-frame suggestion** — `suggest_from_image(image_path, caption, options) -> {camera, note}`.
The chips above are *text-only* (no image exists at parse). Once a still exists, this
**looks at the actual frame** (fast-tier vision call) and returns the single best camera
move (snapped to `CAMERA_MOVES`, the UI dropdown vocabulary) + one director note.
**Triggered** per-frame from the UI (`POST /suggest-frame`, not automatic) and **cached**
by `MD5(image bytes + caption)` in `~/.hob_cache/frame_suggestions.db`, so re-clicks never
re-pay. Image edits are deliberately NOT suggested (operator's creative call). Returns `{}`
on any failure → UI no-op. The route validates the client-sent still path via `_path_allowed`.

UI: **camera is a dropdown** (pre-selected to the AI's auto pick from `auto_director`), and
the **✨ Suggest from image** button (post-Preview, in the frame-iter row) sets that dropdown
+ offers the note as an apply-or-discard chip.

---

## 9. `safety.py` — safety gates

→ [agents/safety.py](../agents/safety.py)

| Gate | Function | When | Blocks |
|---|---|---|---|
| **A** | `moderate_frames(frames)` / `moderate_script(text)` | Before scene design | Harmful/policy-violating content |
| **B** | `check_face_sanity(path)` | After image gen (≤2 retries) | Deformed face, bad dimensions, file < 10 KB |
| **B2** | `critique_image(image_path, frame_id, prompt) -> bool` | After stills pass, before motion | Blank/abstract/empty image when prompt required a real subject |
| **Brand** | `critique_brand(image_path, frame_id, brand) -> bool` | After stills pass on brand runs | Visual conflicts with brand safety requirements |

Gate B2 uses a vision LLM call. Prompt: "Does this image match the description? Flag if
blank/abstract/empty when a real subject was expected." Returns `True` if OK.

Gate A is **non-blocking** on API error (logged, render continues). Gate B triggers
up to 2 retries (deleting the bad file) then accepts the last result.

---

## 10. `image_generator.py` — still generation

→ [agents/image_generator.py](../agents/image_generator.py)

**Backends:** `flux`→fal `flux-2-pro`, `openai`→`gpt-image-2`, `fal`→endpoint from catalog.

**`generate_contextual_image(model_id, prompt, out_path, reference_path=None) -> str`**
- `reference_path=` enables face-consistency: the first AI portrait per speaker_id becomes
  the identity reference; subsequent portrait frames use GPT image-edit on that reference
  so the same face appears across scenes/ages.

**Prompt-hash disk reuse:** output filename is `ai_portrait_{frame_id}_{prompt_hash}.jpg`
where `_prompt_hash(model_id, prompt)` is `MD5(model_id + "|" + prompt)[:12]`.
Changing the prompt → new filename → re-generates. Changing only the frame → can reuse.
Contrast with the old scheme (`ai_portrait_{fid}.jpg`) which reused across prompt changes.

**Gate B + B2 in `_generate_image_checked()`:**
- Runs Gate B sanity check after generation.
- On failure: delete file, retry (≤2), then accept last result.
- Gate B2 critique also runs here; result logged but non-blocking.

**Fallback subject descriptor:** if `subject_description` is empty, uses
`cast.subject_descriptor(frame, narrator_description)` — never a hardcoded sample name.

---

## 11. `coverage.py` — multi-shot B-roll

**Entry:** `assign_coverage(frames, assets_dir, max_extra=2) -> int`
→ [agents/coverage.py](../agents/coverage.py)

- One fast-tier LLM vision call per frame: "which other images also fit this beat as B-roll?"
- Sets `frame["extra_media"]` with spare photo paths.
- `split_durations(total, n, min_each=2.5)` evenly splits a beat across N sub-shots.
- `expand_assignment(base, frame)` guards against lipsync/short-beats; else splits duration and assigns gentle camera moves (`slow_pan`, `subtle_zoom`).
- `expand_all(assignments, frames)` expands all 1:1 aligned frames → sub-shots (`f02_1`, `f02_2`, …).

Web UI: `multi_shot` checkbox; CLI: `--multi-shot`. Covered frames count extras in `pricing.py`.

---

## 12. `lipsync_coordinator.py` — talking faces

**Entry:** `run_lipsync_pass(frames, temp_dir, default_voice_id, voice_map=None) -> frames`
→ [agents/lipsync_coordinator.py:223](../agents/lipsync_coordinator.py#L223)

Two parallel phases (submit, then poll).

**Per frame `_submit_one`** ([:93](../agents/lipsync_coordinator.py#L93)):
1. Guard: needs caption + existing visual + a voice_id, else clear `lipsync`.
2. **Audio** via ElevenLabs (`tts_generator.generate_single_tts`), cached by
   `MD5(caption, voice_id)` in `~/.hob_cache/lipsync_audio/` (BlobCache).
   Per-speaker voice via `cast.voice_for_frame()`.
3. **Duration flip:** `frame["duration"] = audio_dur` — audio now drives timing.
4. **Clip cache** by `MD5(media bytes + audio bytes)` in `~/.hob_cache/lipsync_clips/` (BlobCache).
5. **Upload** media+audio to Higgsfield CDN (`_upload_for_lipsync`).
6. **Vendor route:** video source + `SYNCLABS_API_KEY` → SyncLabs; else Hedra; else clear `lipsync`.

**`_poll_one`** ([:192](../agents/lipsync_coordinator.py#L192)) downloads, caches, sets `lipsync_clip_path`.
**Any failure clears `lipsync`** → normal animation fallback.

### `tts_generator.py`
- `generate_voiceover_track(frames, voice_map, ...)` — full concatenated TTS track;
  per-speaker voice selection; prosody continuity broken (silence pad) when voice changes.
- Emotion → ElevenLabs `voice_settings` (stability, similarity_boost, style).
- `_fit_seg(path, target_dur)` — `ffmpeg apad=whole_dur=<seconds>` ensures frame-exact sync.

---

## 13. `clip_builder.py` — animation engine

**Entry:** `build_clips(assignments, temp_dir, w, h, fps, force_5s, kling_mode, provider, on_clip_ready=None)`
→ [agents/clip_builder.py:548](../agents/clip_builder.py#L548)

**`on_clip_ready(segment_id, clip_path)`** — optional callback fired the moment
each clip finishes (Phase 1 immediate clips AND Phase 2 polled clips). Used by the
web UI for progressive reveal. Called from worker threads; any exception it raises
is swallowed. Default `None` keeps the CLI path unchanged.

**`_resolve_model_id`** treats `model_id == "kenburns"` as a per-frame Ken Burns
sentinel (returns `""`) — this is how the approval gate forces an unapproved frame
to the free path without touching the global provider.

**Two-phase, per the provider-parallel-limit problem:**

- **Phase 1 `_build_one_clip`** ([:418](../agents/clip_builder.py#L418)):
  - HEIC→JPEG (`sips`); `prepare_image` fixes EXIF + **face-aware portrait crop** (OpenCV largest-face center; blind center-crop fallback).
  - **`clip_ready` bypass** — a finished lip-sync clip is copied straight through.
  - Resolve `model_id` (router value or legacy provider via `_resolve_model_id`), look up `backend`. **Clip cache** check first (`_model_cache_key`, namespaced per model, legacy keys preserved for kling/higgsfield).
  - Backend `higgsfield`/`fal`/`kling` → stash `_*_deferred` fields, return `pending`.
  - No model → **Ken Burns** immediately; raw video → **`_video_trim`**.
- **Phase 2** ([:539](../agents/clip_builder.py#L539)) — `poll_one` in a `ThreadPoolExecutor` capped at `min(max_concurrent)`:
  - **Submit inside capped pool**, retry-on-limit (Kling 429/1303 → wait 15s ×8; Higgsfield "concurrent" → wait 20s ×6).
  - Poll → download → **`_fit_clip_to_duration()`** (trims/extends before caching) → store.
  - Any exception → Ken Burns fallback for that frame only. Order restored by `segment_id`.

**`_fit_clip_to_duration(path, target_dur)`** — trims over-long clips; freeze-extends short clips.
Cache keys version-bumped (`hf2_` for Higgsfield, `|v2|` for fal) after this fix so old wrong-duration entries don't hit.

**Kling motion prompt (`_kling_motion_prompt`):** pure motion language only. Caption text was
previously injected (bug); now removed. Negative prompt includes `morphing faces, extra limbs, flickering`.

**Kling specifics:** JWT auth (`_kling_jwt`, HS256, 30-min exp); base64 image;
`_kling_camera_control` maps English → Kling structured `camera_control`; no-camera retry on 4xx.

---

## 14. `caption_writer.py` + `assembler.py`

### `caption_writer.py`
`generate_frame_srt(frames, srt_path, ..., caption_style=None, timecodes=None)` — writes an **ASS** file.
When `timecodes` is provided (list of `(start_ms, end_ms)` tuples from `effective_timecodes()`),
those are used directly instead of cumulative durations. Assembler prefers `.ass` over `.srt`.

**`caption_style` keys:** `font`, `size`, `color`, `position` (global default),
`max_lines` (global default; 0 = unlimited), `enabled` (read in `web_app`, not here —
when False the caller skips `generate_frame_srt` entirely and passes `srt_path=None`).

**Per-frame overrides** (`frame["caption_position"]`, `frame["caption_max_lines"]`,
blank = use the global default) are applied as **inline ASS tags per Dialogue line**,
not via the style header:
- Position → `{\anN}` alignment tag + the per-Dialogue `MarginV` field (`_ALIGNMENT`/`_MARGIN_V`).
- Line cap + auto-shrink → `_fit_caption(text, base_size, max_lines)`: wraps into ≤ `max_lines`
  lines, shrinking the font (down to ~60% of base) until it fits, emitting `{\fsN}` when shrunk.
  `max_lines<=0` → no cap, base size (legacy behaviour). At the size floor it force-wraps into
  exactly `max_lines` (longer lines beat overflow).
The header `Main` style still carries font/colour/italic and the global position; per-line tags
override it. Silent frames (no caption) emit no Dialogue line.
- **Safe-zone default:** bottom captions use a raised `MarginV` (~320px on the
  1080x1920 ASS canvas) to clear Instagram/Reels UI chrome.
- **Keyword highlight:** operator-authored `==word or phrase==` spans are converted
  to inline ASS colour tags. AI does not choose highlighted words.

### `assembler.py` — beat-aware cutting (P1)
`effective_timecodes(durations, transition, overlaps=None)` is the **single source of
truth** for timeline positions (captions, lipsync `adelay`, ducking). `overlaps` =
per-junction crossfade duration (len n-1); `None` → uniform `TRANSITION_DUR` (legacy,
byte-identical). The rhythm seam:
- `beat_track.beat_times(music)` → onset/beat timestamps (ffmpeg decode → numpy
  energy-onset peaks; returns `[]` on any failure → graceful uniform fallback).
- `transition_plan(durations, beats)` → a cut point within `near` (0.25s) of a beat
  becomes a ~0.06s xfade (reads as a **hard cut ON the beat**), else the 0.4s dissolve.
- `beat_overlaps(clips, music_path, transition)` is the entry point (`None` unless a
  music bed + detected beats + ≥2 clips). **`web_app`/`run_caption` compute it ONCE and
  pass the same list to BOTH `frame_timecodes` and `assemble_caption_only`** — so cuts
  and captions can never desync. `_build_video_with_transitions(clips, out, overlaps)`
  uses the same list for the xfade durations/offsets. Scope: music-bed reels
  (upload/generate) only — voiceover/brand stay uniform (don't beat-cut narration).

### `layout.py` — LAY-0 pilot seam

`frame["layout"] = {"preset": "text_card", ...}` renders a full-bleed text-card
still through `agents/layout.py`. The current preset is intentionally narrow:
it proves one real case before future half/split/PIP/overlay presets extend the
same module.

### `assembler.py` — key functions

**`effective_timecodes(durations, transition="crossfade") -> list[tuple[float,float]]`**
Accounts for 0.4s crossfade overlap per junction:
```
offset[i] = sum(durations[:i]) - 0.4 * i   (crossfade)
           = sum(durations[:i])              (hard cut)
```
Used by captions, voiceover adelay, ducking windows, and lip-sync audio positioning.
All audio timing must go through this function or it drifts.

**`frame_timecodes(frames, clips, transition) -> list[tuple]`**
Computes effective timecodes for the actual assembled clip durations.

**`assemble_caption_only(clips, srt_path, out_path, ..., bg_music_path=None)`** ([:296](../agents/assembler.py#L296)):
- Normalize every clip (`_normalize_clip` [:65](../agents/assembler.py#L65)) to one resolution / pixel-format / color range (tv) / fps / SAR.
- Transition: `crossfade` chains `xfade` with effective offset; `none` → stream-copy concat.
- Burn captions via `subtitles=` filter; music looped at 25%, duck to 10% under lip-sync.
- **`bg_music_path`** for VO-over-ducked-music: voiceover at full volume, music at 18%.
- **Lip-sync mixed mode** (`_assemble_with_lipsync` [:177](../agents/assembler.py#L177)): strip embedded audio from lip-sync clips, re-extract each lip-sync track, position with `adelay` at effective timecode, duck music to 10% inside lip-sync windows.

**`apply_brand_overlay(in_path, out_path, disclosure_text, logo_path, logo_corner, disclosure_secs, watermark_path, width, height) -> str`**
Single overlay post-pass called in `_run_inner` after assembly. Composites, in order:
**(1) IP watermark** — `watermark_path` full-frame PNG (its alpha shows the video through),
scaled to `width×height`, whole duration — applied in **both** story and brand modes;
**(2) advertiser logo** corner bug (brand only); **(3) disclosure** drawtext top-centre (brand only).
No-op copy when none requested. `_run_inner` runs this pass when `is_brand OR watermark_path`,
so it's encoded **once**. `_brand_font_arg()` prefers Montserrat via `fc-query`.

**IP watermark resolution** ([watermark.py](../agents/watermark.py)): `watermark_for(ip)` maps the
selected IP → `deploy/watermarks/<file>` via `config/watermarks.json`, returning `""` (graceful
no-op) for an unknown IP or a missing PNG. This is HOB's own property branding (HOB Originals,
The HOB Show, …) — **distinct** from the brand-collab advertiser logo.

---

## 15. `web_app.py` — Flask surface

→ [web_app.py](../web_app.py). Server-rendered UI + JSON/SSE endpoints.

**UI shell (2026 redesign).** All three mode pages extend Jinja [`web/templates/_base.html`](../web/templates/_base.html):
shared header (Story / Studio / Brand tabs), step wizard (`.step-panel` + `#step-nav`),
sticky action bar (`#preview-btn`, `#run-btn`, `#cost-chip`), preview panel (phone frame +
`#output-panel`), and settings drawer (`#balances-card`, `#ip`). Step navigation lives in
[`web/static/shell.js`](../web/static/shell.js); pipeline logic stays in
[`web/static/main.js`](../web/static/main.js) with mode hooks in `brand.js` / `studio.js`.

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Story mode UI shell |
| `/brand` | GET | Brand / Ad mode UI shell |
| `/studio` | GET | Studio mode UI shell (prompt → reel + identity library) |
| `/api/talents` | GET/POST | List talents / register a talent (name + descriptor + reference photo) |
| `/api/talents/<id>` | DELETE | Delete a talent |
| `/api/products` | GET/POST | List products / register a product (name + specs JSON + reference photo) |
| `/api/products/<id>` | DELETE | Delete a product |
| `/api/studio/plan` | POST | `shot_planner.plan(brief, scope, talent, product)` → editable `frames[]` |
| `/canvas` | GET | Director Canvas board page (`canvas.html` + `canvas.js`) |
| `/api/canvas/plan` | POST | `canvas_run.new_canvas(brief)` → new canvas (runs free Script stage); returns `run_id` + `public_state`. State persisted in the run payload via `run_store` |
| `/api/canvas/<run_id>/state` | GET | Current board state (`canvas_run.public_state`) |
| `/api/canvas/<run_id>/advance` | POST (operator) | Run a stage. Free stages execute in-process; **paid stages return a per-stage cost + `check_spend_cap` result *before* any spend** (the anti-wallet-drain), then dispatch to the render pipeline. 409 if the stage is locked |
| `/api/canvas/<run_id>/approve` | POST (operator) | Approve a finished stage → unlocks the next stage's Generate |
| `/api/canvas/<run_id>/frame` | POST (operator) | Edit one shot's text/prompt from the board (`canvas_run.edit_frame`: caption/director_note/motion/negative/image_prompt); cascade-invalidates downstream |
| `/api/canvas/<run_id>/chat` | POST (operator) | Natural-language command box (`canvas_run.chat`): refine + re-plan shots via `shot_planner` (reuses the brain); resets downstream |
| `/api/canvas/<run_id>/match-photos` | POST (operator) | **The moat for real people:** clears `ai_portrait` on talent shots, then `image_matcher.smart_match` content-matches a folder of the operator's REAL photos/videos to the shots → real passthrough (untouched), AI fills the rest. Sets `state["assets_dir"]` (threaded into the render). Validated `_path_allowed`. Avoids generating a synthetic likeness of a named real person. |
| `/api/canvas/<run_id>/asset` | POST (operator) | Attach an uploaded image to shot(s) (`canvas_run.attach_asset`): **real** (non-AI `photo_spec` → `model_router` PASSTHROUGH, the moat), **reference** (real face → AI likeness, kept `ai_portrait`+`character_ref_path`), or **scene**. `all_talent` applies to every people-shot. Path validated via `_path_allowed`; image first uploaded through `/upload-photo` |
| `/api/canvas/<run_id>/keyframes` | POST (operator) | Render the **cheap stills only** (reuses `_execute_preview`); lets the operator review/re-roll before committing to video. Sets `render_phase="keyframes"`. The later full render shares the run dir → reuses these stills (content-hash cache, no re-spend) |
| `/api/canvas/<run_id>/render` | POST (operator) | Render the board into a reel via `_canvas_render_thread` → generates a music bed (Suno, best-effort) so the engine's **beat-aware cutting** snaps cuts to the beat (anti-slideshow, P1). **Suno-independent:** sets `beat_grid_bpm` (`_canvas_tempo_bpm` from mood) so `assembler.beat_overlaps(fallback_bpm=)` cuts on a synthetic tempo grid even with no music. **Audio options** (body): `music_type` = generate (Suno) / upload (`music_path`, validated `_path_allowed`; song uploaded via `/upload-photo`) / voiceover (`voice_id` from `/voices`; sets `beat_grid_bpm=0` for gentle cuts) / none. Then dispatches `_execute_pipeline`. **Reuses the Key Frames render dir** so stills are cached. Sets `render_phase="full"`. Same governance gates as `/run`. (`/rendered` reconciles paid stage chips by `render_phase` so Key Frames-done ≠ Video-done) |
| `/api/canvas/<run_id>/rendered` | POST (operator) | Per-shot rendered media read from the render dir (survives reloads) + reconciles paid stage statuses to the render's real status (so the rail can't stick on 'generating') |
| `/api/canvas/<run_id>/reroll` | POST (operator) | Re-roll ONE shot: regenerate its still (`_generate_stills` force) + clip (`build_clips`), write into the render dir. Single-frame spend gate. Same path as `/redo-still`+`/redo-motion` |
| `/api/canvas/list` | GET | Recent saved canvases (`run_store.list_canvases`) for the resume picker |
| `/parse-script` | POST | `parse_frame_script` → frame cards + cast + suggestion chips |
| `/suggest-frame` | POST | Vision-grounded per-frame suggestion (`suggest_from_image`) → best camera + director note; validates the still path, cached, no-op on failure |
| `/preview` , `/preview-result/<run_id>` | POST/GET | Generate stills only (fast iteration); brand-safe critique if `is_brand` |
| `/api/estimate` | POST | Server-side cost estimate (no client-side cost logic) |
| `/pricing` , `/models` , `/voices` | GET | Expose `pricing.json`, `model_router.catalog()`, ElevenLabs voices |
| `/balances` | GET | Live AI-vendor credit/balance probe (`agents/balances.all_balances()`); read-only, each vendor degrades independently |
| `/ips` | GET | HOB IP/property list for the watermark dropdown (`agents/watermark.list_ips()`); flags which IPs have a PNG present |
| `/generate-music` | POST | Suno generation; accepts `captions` + `mood` for auto-brief |
| `/extract-brief` | POST | `brand.extract_brief()` — parse-only, verbatim field extraction |
| `/browse-dirs` | GET | List folders + media count under `ASSETS_BROWSE_ROOT` |
| `/run` | POST | Kick off `_execute_pipeline` in a thread; mandatories hard-block for brand |
| `/progress/<run_id>` | GET (SSE) | Stream the run log. **Logging is thread-routed:** a single process-global `_TeeStdout` (installed once) routes `print()` to the log of the run bound to the current thread via `_thread_run` (threading.local), set/cleared in `_execute_pipeline`/`_execute_preview`. Replaced per-run `contextlib.redirect_stdout`, which set process-global `sys.stdout` and cross-wired logs when a preview + render (or threaded requests under `--threads 8`) overlapped. |
| `/output/<run_id>` , `/download/<run_id>` | GET | Stream / download the MP4 |
| `/clip/<run_id>/<frame_id>` | GET | Serve ONE finished clip for progressive reveal during a render |
| `/redo-still` | POST | Regenerate the still for ONE frame synchronously (per-frame redo); cache-aware, `force_regen_ids` busts that frame's cached still |
| `/redo-motion` | POST | Rebuild one frame's motion from the approved still; returns a refreshed clip preview |
| `/export/<run_id>` | GET | Editor hand-off zip: **`timeline.fcpxml`** (importable timeline for Premiere/Resolve/FCP), **`captions.srt`** (standard subs), `clips/`, `output.mp4`, `edit_list.json`. FCPXML/SRT written in `_run_inner` via `agents/fcpxml.py`; clips back-to-back (crossfades flattened), captions kept separate so a caption quirk can't break the clip-timeline import. |
| `/performance/<run_id>` | POST | Post-publish feedback (Gap #3): `{views, likes, note}` for a finished run → `run_store.save()` writes `runs.performance_views`/`performance_likes` (nullable INT) / `performance_note` (TEXT) / `performance_by` (verified operator). 404 on unknown run; `get_json(silent=True)` + int coercion + note cap so a bad body never 500s; upsert. **Gated by `auth.require_operator`.** |
| `/performance` | GET | Completed feedback loop (Gap #3): leaderboard (`run_store.list_performance()`, best-performing first) + roll-up summary. Gated. |
| `/provenance/<run_id>` | GET | Authenticity/provenance summary (Gap #5): real vs ai_symbolic vs AI-likeness-of-a-real-person, from the per-run `provenance.json` artifact (else recomputed from the stored payload via `agents/provenance.py`). |
| `/login` , `/logout` , `/me` | POST / POST / GET | Operator auth (Gap #1): `authenticate()` → HS256 JWT in an httpOnly cookie; `/me` reports the current operator. Seed operators with `python -m agents.auth add-operator`. |

**Auth (Gap #1).** Money/rights routes — `/run`, `/preview`, `/retry/<id>`, `/performance*`, `/project-version`, `/api/canvas/<id>/{advance,approve,frame,chat,asset,keyframes,render,rendered,reroll}`, and `/brand-approval` (requires the `approver` role) — are wrapped by `agents/auth.require_operator(*roles)`, which validates the cookie/Bearer JWT and injects the *verified* `operator` (handlers no longer trust a client-supplied `operator_id`). `HOB_AUTH_DISABLED=1` bypasses for local dev; `HOB_AUTH_SECRET` signs tokens in prod.

**Storage (Gap #2).** `agents/db.py` selects SQLite (default) or Postgres from `HOB_DB_URL`; new stores (`auth`) route through it dialect-neutrally. The legacy per-store SQLite bridges migrate onto it for the RDS cutover (SCALE_PLAN Phase 2).

**Likeness consent (Gap #4).** `agents/governance.{likeness_modalities,validate_likeness_consent,record_likeness_consent}` gate AI face/voice of a *named real person* on `/run`, recorded against `consent_records.{face,voice}` and the verified operator.

**Vendor fallbacks (Gap #8).** `config/models.json` `fallbacks` + `model_router.{candidates,run_with_fallback}` give each generation axis an independent cross-vendor failover chain.
| `/posting-kit` | POST | Story-mode-only posting kit: IG caption seed, hashtags, cover-frame id |
| `/story-intake` | POST | STR-2 LLM-assisted raw story/notes/transcript → editable Format B frame draft + `frames_meta`; falls back to deterministic draft if LLM is unavailable; behind commercial governance |
| `/hook-workshop` | POST | STR-5 draft scaffold: low-cost opener candidates with no fake score/predictor, behind commercial governance |
| `/caption-variants` | POST | STR-4 draft scaffold: language-variant placeholders only until real LLM/operator translation is wired, behind commercial governance |
| `/render-variants` | POST | STR-3b pilot: governed rerender payload descriptors for multi-format/cutdown work |
| `/retry/<run_id>` | POST | Re-dispatch a stored run payload; content-hash caches provide resume semantics |
| `/asset-library/register` | POST | Register an allowed local asset in the lightweight asset library |
| `/brand-approval` | POST | Store a lightweight brand approval/audit record |
| `/project-version` | POST | Store a lightweight project/reel version record |
| `/guide` | GET | Serve `docs/OPERATOR_GUIDE.html` |
| `/media` | GET | Serve asset thumbnails (validates path via `_path_allowed`) |
| `/upload-photo` | POST | Accept file upload; save to session assets dir |

### Editor iteration features (per-frame redo, progressive reveal, approval gate)

These three share the frame-card UI and the `_run_inner` path, so both story and
brand modes get them automatically.

- **Per-frame redo** (`/redo-still`): the UI sends one frame's current settings.
  Before calling `_build_frames_from_payload`, the route strips `visual_path` from
  the frame payload whenever `photo_spec` is `ai_portrait`/`ai_symbolic`/`uploaded`
  **or** the existing `visual_path` basename starts with `ai_portrait_`/`ai_symbolic_`
  (catches "auto" frames that fell through to AI generation). This prevents the old
  cached file silently overriding the current photo_spec.
  `_generate_stills(..., force_regen_ids={fid})` then deletes that frame's cached
  `ai_*_{fid}_*.jpg` and, **after** `design_all_scenes` completes, stamps a
  millisecond `_redo_seed` onto `frame["scene"]` so even an unchanged director note
  produces a different prompt hash → genuinely different image each time.
  Spend is reserved for **the single frame only** (not the full payload).
  Returns `{frame_id, path, is_video, exists}`. Synchronous — no thread, no SSE.
- **Per-frame motion redo** (`/redo-motion`): the UI sends one frame's current settings
  after a still has been approved. Spend is reserved for **the single frame only** —
  not the full payload (which would sum all frames' animation costs and falsely block
  the redo). Deletes the matching BlobCache key for that frame's clip so the next
  `build_clips` call re-generates it, then returns the new clip path. Synchronous.
- **Retry** (`/retry/<run_id>`): re-loads the stored payload from `run_store` and
  re-dispatches it through the normal `_run_inner` path. Since 2026-06-20 this route
  runs the standard `governance.reserve_spend` check before dispatch — previously it
  bypassed spend governance entirely.
- **Progressive reveal**: `build_clips(..., on_clip_ready=cb)` fires `cb(segment_id,
  clip_path)` the instant each clip lands (cached, Ken Burns, or polled). In
  `_run_inner` the callback copies the clip into `run_dir/clip_{frame_id}.mp4`
  (sub-shots `f02_1…` map to parent `f02`, first one wins) and appends a typed
  SSE event `{"type":"clip_ready","frame_id","url":"/clip/<run_id>/<frame_id>"}`.
  The `/progress` generator drains a separate `events` list alongside `log`.
- **Approval gate**: the UI sends `approved_frame_ids` (or `null` = all approved).
  In `_run_inner`, `_video_model_for(f)` returns the sentinel `"kenburns"` for
  unapproved non-lipsync frames; `clip_builder._resolve_model_id` maps
  `"kenburns"` → `""` → free Ken Burns. `pricing.estimate(..., approved_ids=...)`
  mirrors this so the quote drops for rejected frames.

Run-state additions: `_runs[run_id]` gains `"clips": {frame_id: path}` and
`"events": [typed dicts]` alongside `log`/`status`/`output_path`.

**Thread-safe log capture (sharp edge):** `print()` from a run thread is captured
by a single process-global `_TeeStdout` (installed once at import) that routes each
line to the run bound to the **current thread** via `_thread_run` (a
`threading.local`). `_execute_pipeline`/`_execute_preview` set `_thread_run.run_id`
and clear it in a `finally` (pooled threads are reused — must not leak the binding).
This replaced per-run `contextlib.redirect_stdout`, which set the global
`sys.stdout` and cross-wired logs when a preview and a render (or any overlapping
threaded requests under gunicorn `--threads 8`) ran concurrently. Per-run
`status`/`stills`/`clips` were always correct (set by `run_id`, not via stdout) —
only the streamed log was affected.

**Security:** `/media` validates all paths against `RUNS_DIR` and `ASSETS_BROWSE_ROOT` via
`_path_allowed()`. Only image/video MIME types allowed. `ASSETS_BROWSE_ROOT` env var scopes
the server folder browser.

**`_build_frames_from_payload`** ([web_app.py:314](../web_app.py#L314)) converts UI frame
cards into `frames[]`; carries `speaker_id`, `product_beat` from the payload.
Calls `apply_cast()` if `parsedCast` was provided, else `detect_cast()`.

**`_generate_stills`** ([web_app.py:576](../web_app.py#L576)) shared by preview + run:
- Skips AI generation for `product_beat=True` frames (real asset used).
- Appends PIL CTA end-card for brand runs via `brand.build_cta_card()`.
- Gate B2 vision critique on each AI-generated still.
- `extra_context=brand_mod.brand_scene_context(brand)` for visual context in scene design.

**`_run_inner`** ([web_app.py:775](../web_app.py#L775)) runs the full pipeline.
Brand runs: `_brand_audio()` helper resolves `(vo_track, bg_music_track, is_voiceover)`;
`apply_brand_overlay()` post-pass on the finished MP4.

**Subject handling:** `subject_name = (data.get("subject_name") or "").strip()` — no default.
Empty string means "infer from the story".

---

## 16. Caching Summary (invalidation rules)

| Cache | Key | Location | Invalidated by |
|---|---|---|---|
| Animation clips | `MD5(image bytes + motion + duration)`, model-namespaced | `~/.hob_cache/kling_clips/` *(BlobCache; S3-backed when enabled)* | changing image, motion, duration, or model |
| Scene designs | `MD5(caption, note, type, subject, desc, has_photo)` | `~/.hob_cache/scene_designs/` | changing any of those |
| Image descriptions | image content hash | `~/.hob_cache/image_descriptions.db` *(SQLite, WAL mode)* | file content change (rename safe) |
| Lip-sync clips | `MD5(media bytes + audio bytes)` | `~/.hob_cache/lipsync_clips/` *(BlobCache; S3-backed)* | media or audio change |
| Lip-sync audio | `MD5(caption + voice_id)` | `~/.hob_cache/lipsync_audio/` *(BlobCache; S3-backed)* | caption or voice change |
| Generated stills | `ai_portrait_{fid}_{prompt_hash}.jpg`, ≥ 50 KB | the **asset folder** | changing the prompt (new hash) or file < 50 KB |

**Note on still cache:** the `{prompt_hash}` suffix means changing the scene design prompt
(e.g. after editing the director note) auto-busts the still and regenerates. The old scheme
(`ai_portrait_{fid}.jpg`) silently reused stale images when prompts changed.

**Redo-still cache clearing:** `/redo-still` strips `visual_path` from the frame
payload for AI-type and uploaded frames before calling `_build_frames_from_payload`,
so the priority rule (`payload_visual` beats `photo_spec`) cannot silently reuse the
old file. File deletion via `force_regen_ids` then ensures even a cache-hit filename
produces a fresh request. This clearing only happens in the redo route — the normal
render path deliberately reuses approved results without re-paying.

---

## 17. Concurrency & Failure Matrix

| Stage | Pool | Cap | On failure |
|---|---|---|---|
| Scene design | ThreadPool | 10 | generic fallback scene (no raise) |
| Image gen | serial per frame | — | fallback model → Gate B retry ×2 → accept last |
| Lip-sync | ThreadPool ×2 phases | 6 submit / N poll | clear `lipsync` → animate normally |
| Clip build | ThreadPool | `min(model max_concurrent)` | retry-on-limit; else Ken Burns for that frame |
| Assembly | single ffmpeg | — | raises (whole render fails; temp kept) |
| Brand overlay | single ffmpeg post-pass | — | raises (logged; main MP4 already done) |

**Whole-pipeline failure** ([run_caption.py:343](../run_caption.py#L343)) preserves the
temp dir for debugging; success cleans it unless `--keep-temp`.

---

## 18. Extension Recipes

- **Add a video/image model:** add a `models` entry (with `backend`, `pricing_key`, `fal_endpoint` if fal-hosted, `max_concurrent`) in `config/models.json`, list it under `routing[kind][shot_type][tier]`, add price to `config/pricing.json`. No Python change for fal-hosted models.
- **Swap the reasoning/vision/fast LLM:** set `LLM_PROVIDER` (and keys) or edit `config/llm.json`. Callers are untouched.
- **New frame annotation:** add a regex in `_parse_format_b`, a clean-up `re.sub`, and a key on the frame dict; consume it in the relevant stage.
- **New shot type:** extend `_image_shot_type`/`_video_shot_type` and add a `routing` entry.
- **New voice role:** add a key to `config/voices.json roles` and fill the ElevenLabs voice ID.
- **Progressive reveal for a new clip backend:** nothing extra — `build_clips` calls `on_clip_ready` for every finished clip regardless of backend.
- **Brand mode B2 (kinetic graphics):** hook into `apply_brand_overlay()` as an additional post-pass; all other assembly branches remain untouched.

---

## 19. Known Sharp Edges (read before editing)

- Generated stills land in the **user's asset folder**; the parser's `_DERIVED_MARKERS` filter stops them corrupting positional auto-match. Touch both together.
- `pricing.estimate` must keep mirroring `model_router.select_model` exactly, or quoted cost diverges from billed cost.
- Clip-cache keys for `kling`/`higgsfield` are **deliberately legacy-formatted** ([clip_builder.py:59](../agents/clip_builder.py#L59)) so previously-paid clips still hit — do not "clean up" those token formats.
- Still-cache key includes a **prompt hash** — the old `ai_portrait_{fid}.jpg` filename scheme is gone. Any code that assumes the old filename will miss the cache.
- Lip-sync uploads user media to an **external CDN**; treat as a privacy boundary.
- `subject_name` and `subject_description` are **always optional** — never default to a sample name. Any fallback must use `cast.subject_descriptor()` which derives from the story.
- `brand.extract_brief()` is **parse-only** — it must never rephrase, infer, or generate claims. If you add LLM creativity to it, you break the "AI never writes ad copy" guarantee.
- `effective_timecodes()` must be used for **all** audio timing in the brand overlay path (voiceover adelay, ducking windows). Using raw cumulative durations causes drift at crossfade junctions.
- The approval gate uses the `"kenburns"` string as a per-frame `model_id` sentinel. `web_app._video_model_for()`, `clip_builder._resolve_model_id()`, and `pricing.estimate(approved_ids=…)` must stay in lockstep — change one and the quote diverges from the render. Lipsync frames are never auto-rejected (their audio drives the clip).
- Progressive-reveal sub-shot mapping is `segment_id.split("_")[0]` → parent `frame_id`; only the first sub-shot per frame reveals (later ones are ignored for display). Frame ids must not themselves contain `_` or the mapping breaks.
- **`/redo-still` on a video-matched frame auto-generates an AI portrait.** A `.mov`/`.mp4`/etc. `visual_path` has no meaningful "still" to redo — so the route always clears `visual_path` when the extension is a video type, regardless of `photo_spec`. The frame then falls through to `generate_contextual_image` just like any other AI-generate frame. This is correct: if the user wants to keep the video they should not click Redo Still.
- **`_redo_seed` must be injected AFTER `design_all_scenes`.** `design_all_scenes` does
  `f["scene"] = scene` (full dict replacement) for every frame inside a futures loop.
  Any value injected into `f["scene"]` before that call is silently lost. The seed is
  stamped in a second pass immediately after `design_all_scenes` returns, so it
  survives into the prompt-hash computation in `image_generator.py`.
- **Redo-still/redo-motion estimate ONE frame, not all frames.** `_estimate_payload_cost`
  sums over `data.get("frames", [])`. Passing the full payload to a per-frame redo
  inflates the reservation by N× and will falsely trigger the spend cap. Always pass
  `{**data, "frames": [frame_payload]}` to the estimator in these routes.
- **Every spend route must call `reserve_spend` before dispatch** — including `/retry`.
  Content-hash caches do not prevent overspend if the cache is cold; governance must
  be applied at the route level regardless of cache hit probability.
- **Spend governance is reserve→release→settle, never a single write.** `governance.reserve_spend()` (`BEGIN IMMEDIATE`, serialized across SQLite connections) holds the estimate *before* dispatch; `release_reservation()` must be called on BOTH the success and failure paths of every spend route (it's idempotent), then `record_cost_event()` settles the actual. The `cost_events` table is **append-only** — never read-modify-write a JSON blob, or the cap races under concurrent operators. A killed process can orphan a reservation; `sweep_stale_reservations(ttl_seconds=HOB_RESERVATION_TTL_SEC, default 7200)` runs once on web startup to release holds older than the TTL so a crash can't permanently inflate a cap.
- macOS assumptions: `sips` (HEIC), Baskerville font dir. Cloud/Linux needs alternatives; Montserrat is bundled and safe.
- `main.py` is the **legacy** voiceover pipeline; maintained path is `run_caption.py` / `web_app.py`.
