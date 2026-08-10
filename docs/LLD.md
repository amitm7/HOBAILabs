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
  image_editor.py         reference-conditioned identity edit — PLUGGABLE (config routing.identity): nano_banana_edit (fal, default) -> gpt_image_edit fallback; env IDENTITY_MODEL; per-endpoint circuit-broken. S34 probe facts (tools/shoot_probe.py, 2026-08-08): nano-banana/edit DOES honour the full image_urls array (multi-reference conditioning verified); `aspect_ratio` is honoured approximately (1024x1024 -> 896x1152, ratio 0.778 for "4:5") while `image_size` is IGNORED — so an exact 4:5 frame needs a post-generation crop, not an argument. seedream_edit ignores aspect_ratio entirely and returns 2048x2048. gpt_image_edit still passes only the FIRST reference.
  safety.py               Gate A (moderation) + Gate B (face sanity) + Gate B2 (vision critique) + Gate B3 (likeness vs real reference)
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
                          Storage contract: DEFAULT_RUNS_DIR = ~/.hob_runs (NEVER the temp
                          dir); _conn() probes + reconnects on a dead connection.
  db.py                   Gap #2 storage switch: SQLite default / Postgres via HOB_DB_URL
  auth.py                 Gap #1 operator identity: operators table, HS256 JWT, require_operator, CLI
  provenance.py           Gap #5 authenticity tiers (real / ai_symbolic / real-person AI)
                          + per-frame rows (classify_frames) feeding the C2PA credential
  content_credential.py   C2PA signing: embeds the provenance truth into output.mp4
                          (extractable: stdlib + c2pa-python only — see PROVENANCE_PLAN)
  plan_qc.py              slideshow-risk scorer (Item 8): pure/deterministic 6-dimension
                          score of the shot plan BEFORE spend; advisory plan_review gate
  source_media_review.py  pre-generation hash+probe of every real source file →
                          source_media_review.json (the "never AI-regenerated" evidence)
  ../schemas/             provenance.schema.json — the credential's data contract
                          (jsonschema-validated at finalize; HOB_SCHEMA_STRICT=1 raises)
  product_surface.py      SQLite stand-ins for assets, approvals, project versions,
                          + Studio identity library (talents, products) CRUD
  suggestions.py          fast-tier batch → camera/edit/note chips per frame
  coverage.py             multi-shot B-roll: LLM vision assign + duration split
  lipsync_coordinator.py  audio → CDN → Hedra/SyncLabs → lipsync_clip_path
    hedra.py / synclabs.py    vendor clients
    tts_generator.py          ElevenLabs/OpenAI TTS; frame-exact padding/trim; per-speaker voice
  clip_builder.py         still/video → animated clip (kenburns|kling|higgsfield|fal)
    higgsfield.py / fal_video.py / fal_client.py   vendor clients
  restore.py              non-gen ffmpeg cleanup (ladder rung 1) + quality_score + _probe_resolution
  upscaler.py             generative upscale (fal): aura_sr=faithful (real), clarity=creative (AI)
  caption_writer.py       frames → ASS subtitle file (uses effective_timecodes)
  music_generator.py      pluggable music engine (config/music.json): lyria (Gemini interactions)|suno; provider dispatch + graceful fallback; instrumental beds vs vocal songs
  beat_track.py           music → onset/beat times (ffmpeg + numpy, no librosa; graceful [])
  shoot.py                S34 product-photoshoot SEAM — the one entry point web_app uses (scan/run_sku/status/personas). Implementation still lives in tools/shoot_{bakeoff,campaign,batch,persona}.py and is loaded by path; `ponytail:` documented interim indirection so web_app never imports from tools/. When the approach settles, move the bodies here and delete the loader — no caller changes.
  sfx.py                  S30 Phase 2 SFX/atmosphere seam: generate_sfx(video, prompt, out, variant) → clip-synced foley/atmosphere WAV via the models.json "mmaudio" entry (fal video→audio); content-hash cached (video 1MB fingerprint+size+prompt+variant, rule 12); '' on ANY failure + degradation.report("sfx","info") — callers mix nothing. Creates the output dir BEFORE the paid call (neither download_media nor ffmpeg makes parents, so a missing run subdir used to fail the write *after* fal billed, and the bare except swallowed it → paid, no track). Final-Cut mix stem = ticketed follow-up (S30 plan Phase 2; S1 lesson: audio-mix changes get their own verify loop)
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
   — one reasoning call returning `{frame_id: image_number}`. **Relationship/role-aware
   (fixes "brother"→mother+daughter):** each frame line carries **`person` (speaker role +
   gender + age, e.g. "brother male adult")** + `depicts` (storyboard scene_description) +
   `emotion` + caption. Priority: **① PERSON match (strongest — reject gender/age
   mismatches)** → ② visible names/text → ③ depicted subject → ④ tone. This needs the cast:
   **`smart_match` runs `cast.detect_cast` BEFORE matching** ([C1](../agents/image_matcher.py))
   if frames aren't tagged (`detect_cast` is now idempotent via a `_cast_detected` marker so
   it's not re-run). Image descriptions are **relationship-aware** (`_DESCRIBE_PROMPT` asks
   for per-person gender/age + implied relationship; cache-key prefixed `_DESC_VERSION` so a
   schema bump regenerates). Still imperfect on abstract beats → per-shot **photo picker**
   (`/api/canvas/<id>/assets`) + planned per-shot re-match.

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
- **Parallelism:** `ThreadPoolExecutor(max_workers=min(n,10))`. Silent frames (no
  caption — legitimate for pure-visual/wordless beats) skip the LLM call and get a
  canned `emotion/motion_prompt/camera_angle` (still image), but as of 2026-07-18
  **`image_prompt` is `director_note`** when present, not `""`. Before this fix
  the description was silently discarded even when rich — a shot planned as "the
  fist glows" or "the leap" reached the storyboard sketch (and the real render,
  same `scene.image_prompt` field) with an EMPTY scene, producing unrelated
  generic/hallucinated content (found live: a story's silent beats came back as
  an unrelated teenager/fish/tower sketch). A caption-less frame with no
  director_note either still gets `image_prompt=""` — genuinely nothing to draw.
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
  `{type:text}` / `{type:image, path|data_uri}` parts, normalised by `_norm_content`
  ([:143](../agents/llm.py#L143)) before any backend sees it. It also accepts the
  OpenAI-shaped `{type:"image_url", image_url:{url}}` part and rewrites it to
  `{type:"image", path|data_uri}`, so callers may use either shape.
  **An unknown part type RAISES `ValueError`** (2026-08-08). *Sharp edge it fixes:* every
  backend loops `if type=="text" … elif type=="image"`, so an unrecognised part previously
  hit neither branch and was silently dropped — the vision call then answered from the text
  alone and returned a confident wrong answer instead of failing (rule 13: degradation must
  never be silent). Found by `tools/shoot_probe.py`, which passed `image_url` parts and got
  "NEITHER" for an image plainly showing both shapes. All in-repo callers already used the
  `{type:"image", path}` shape, so no shipped gate was affected.
  Each backend then translates:
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

**Sharp edge — video vendor concentration + the Kling outage (2026-07-17).** Lane
contents live in `config/models.json`, but the *why* belongs here. Five of six video
models (`seedance`, `veo`, `hailuo` on fal; `kling_std`, `kling_pro` on Kling) sit on
just **two** vendors, and on 2026-07-17 both were unpayable at once — fal balance-locked,
and **Kling refusing every request**: `POST /v1/videos/image2video` → `429 {"code":1102,
"message":"Account balance not enough"}`.

Two facts about Kling that are not obvious from the code and cost a day to learn:
1. **The Kling API Platform is prepaid-packs-only.** There is no pay-as-you-go tier. A
   dead pack does not mean "worse rate" — it means **no generation at all**.
2. **Top-up is not self-serve.** The dashboard offers only "Contact us for purchase", so
   a lapsed pack is a sales cycle, not a checkout. Plan replenishment ahead.

`higgsfield` is the only third video vendor (own account, own credit), which is why it now
**leads every draft lane and most premium lanes**, and why `defaults.video` was moved off
`kling_std` — the last-resort default pointed at the one vendor that refuses everything,
turning the safety net into a guaranteed failure. Kling is demoted, never deleted: buy a
pack and moving the ids back up the lists re-activates it with no code change.

Best-fit is now restored as the hack's own revert note prescribed (`landscape→hailuo`,
`hero→seedance`, `dialogue→veo`) — the 2026-07-02 "kling_pro leads every lane to burn a
prepaid balance" hack outlived its balance by 13 days and was making every premium shot
open with a doomed 429.

Note `candidates()` appends `fallbacks_for(primary)`, so `real` and `dialogue` can reach
`higgsfield` as a late hop even though it is deliberately not their lead (it re-crops its
input, and has no audio/lip-sync — `dialogue` leads `veo` for its native audio). That is a
reported degradation (`run_with_fallback` → `degradation.report`), not a silent substitution.

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

### speaker_id vs. visual_subject_id (2026-07-18)
Two separate frame keys, both set by `detect_cast`/`apply_cast`/`_apply`:
- **`speaker_id`** — whose VOICE reads this beat (drives `voice_for_frame`, lip-sync).
- **`visual_subject_id`** — who is DEPICTED on screen (drives face-lock continuity
  and the image prompt's subject description). Defaults to `speaker_id` when unset
  or equal — the overwhelmingly common first-person case (the narrator tells their
  own story: same person speaks and is shown) is unaffected either way.

They diverge for **third-person narration** about a recurring character who is
rarely directly quoted (e.g. a mythological/fictional protagonist): `speaker_id`
correctly stays `narrator` (an unseen storyteller reads the prose) while
`visual_subject_id` is the character, so their face still locks consistently
across every shot. Before this split, `detect_cast`'s prompt only reasoned about
"who's speaking" — a narrated-about, rarely-quoted protagonist was never
recognized as a distinct cast member, every one of their shots silently fell back
to the narrator (who has no face), and identity/age drifted shot to shot with no
error surfaced (found live, 2026-07-18, on a third-person mythological brief —
not yet assigned an L99_ARCH_PLAN ledger row). Downstream consumers redirected
to prefer `visual_subject_id` (all default to `speaker_id` when unset):
`canvas_run.set_character` (frame matching — an uploaded reference for a
narrated-about character now propagates to ALL their shots, not zero/one),
`web_app._build_frames_from_payload` → `character_ref_path` resolution and the D1
`first_portrait_by_speaker` auto-reuse lock, and
`scene_intelligence.design_all_scenes`'s gate for calling `subject_descriptor` at
all (previously gated on speaker≠narrator, which is exactly backwards for this
case — fixed to gate on visual_subject≠narrator).
Companion fields mirroring `speaker_label/gender/age_bracket`:
`visual_subject_label/gender/age_bracket`.

### Voice resolution
**`voice_for_frame(frame, default_voice_id, voice_map) -> str`**

Priority chain (first non-empty wins):
1. `frame["voice_override"]` — explicit `[voice:]` annotation in the script
2. `voice_map[speaker_id]` — operator selection in the Cast voices UI panel
3. `voices.json roles[speaker_id]` — if speaker_id matches a role key
4. `voices.json roles[gender_age_bracket]` — e.g. `female_adult`, `child`
5. `default_voice_id` — the global voice picker

Deliberately still keyed on `speaker_id`, not `visual_subject_id` — audio must stay
with whoever is actually talking (the narrator), not the on-screen subject.

**`subject_descriptor(frame, narrator_description) -> str`**
Returns a visual description of who should be on screen for this frame — `narrator_description`
for narrator-visual-subject frames, or the visual subject's own descriptor otherwise (falls
back to the older speaker_* fields for frames predating the split above). Used by
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
| **Lyria 3 (Gemini)** | ⚠️ quota | `POST generativelanguage.googleapis.com/v1beta/interactions` (`x-goog-api-key`, model `lyria-3-pro-preview`/`-clip-preview`, base64 audio out). Wired + endpoint verified; current key project 429s ('not enough quota') for the preview models → enable billing/Lyria quota, then flip `config/music.json` provider to `lyria`. Falls back to Suno meanwhile. |
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
- **Scope registry** (`_SCOPE_SYSTEM_PROMPTS`, S29 Phase 1a / S31): a `scope →
  system_prompt` dict is the single source both validation and prompt-pick read,
  so adding a scope (e.g. the S29 `podcast` scope) is one dict entry, not two
  branches that can drift — see the `orientation`/`_orient_wh` bug this pattern
  was adopted to avoid a repeat of (§ canvas settings route above). Today it
  holds only `general`/`commerce`; `podcast` is not added — still gated behind
  the golden-face spike in `docs/PRESENTER_PLAN.md` Phase 0.
- **COMPILE mode (2026-07-19, root-cause fix):** a brief with ≥2 `FRAME n` /
  `SCENE n` / `SHOT n` markers is an AUTHORED shot list — `_compile_frames()`
  parses it deterministically (zero LLM, zero spend) instead of re-inventing it:
  one dialogue line = one shot (caption + `speaker_id` verbatim — kills the
  attribution class where a line written `SUGRIVA:` rendered as Rama); camera/
  lighting/composition lines survive verbatim in `director_note`; `[VISUALS:]` →
  note, `[Sound:]`/`Sound:` → **`audio_intent`** (first shot of the block);
  delivery notes `(ancient, gentle)` → **`voice_direction`**; `Shot n (a–bs)`
  timings → durations; shot_size/motion from keyword maps on the shot's own
  direction. Preamble before the first marker is DIRECTION, not a scene: its
  text rides every shot as a `[style]` note-line, and `Reference <Name>` lines
  register never-speaking characters (identity lock for a silent antagonist).
  Vocatives inside dialogue ("…, Hanuman.") also register cast. Silent blocks
  infer `visual_subject_id` by mention frequency over the registered cast only
  (never arbitrary proper nouns — place names stay out). Compiled frames carry
  `_cast_detected=True` (cast LLM pass skipped — `_cast_from_frames` rebuilds
  the sheet from tags) and `compiled=True`; speaker gender/age default to
  male/adult, edited on the Character sheet. Compile failure falls OPEN to the
  LLM planner; unstructured prose briefs use the LLM path unchanged. New frame
  keys: `audio_intent`, `voice_direction`, `compiled` (audio_intent is the
  future SFX driver; voice_direction awaits TTS style support).
- **Sketch-as-composition + face extraction (2026-07-20, probe-verified):**
  ⑪ once the storyboard stage is APPROVED, each shot's `storyboard_art` sketch
  rides the multi-image edit path (`image_editor.edit_image` now accepts a LIST
  → nano-banana `image_urls`) as a BINDING composition reference alongside the
  face ref — live probe: framing/blocking transferred exactly, zero pencil
  bleed, identity held. Flag: `_canvas_render_data.sketch_composition` →
  `_generate_stills(sketch_composition=)`; sketch hash joins the still cache
  key; unapproved storyboard = free composition (old behaviour); real/passthrough
  untouched. ⑫ `image_matcher.extract_face_ref(path, out, prefer)` — attaching
  a multi-person photo as a character ref now auto-crops the RIGHT face
  (baby/child characters take the smallest, adults the largest; margin 0.6;
  best-effort → full photo kept on failure); original preserved as
  `ref_full_path` (CHARACTER_ATTRS); generated `charref_*` portraits skipped
  (clean full-body by design). New route `/use-locked-face` + Inspector triad
  (🎭 Their face / 👤 Other face… / 👻 Generic) replaced the ambiguous pair.
- **Hindi-first authoring (2026-07-19):** ⑨ compile mode parses INDIC dialogue —
  `_SPEAKER_LINE_INDIC_RE` accepts a Devanagari/Bengali/Gurmukhi speaker header
  (no upper case exists, so the quoted-dialogue requirement is the guard) and
  `_slug` keeps Indic word chars incl. matras (यमराज stayed यमराज, was यमर_ज →
  falsy → narrator). ⑩ `languages.detect_language(texts)` — script-block
  detection (≥20% of letters; Devanagari→hi, shared with Marathi — operator
  override wins) — wired in `_run_inner` BEFORE caption_style/VO so an authored-
  Hindi story automatically gets the Devanagari caption font (Noto, T13), the
  native-hi voice table (`voices.json language_voices.hi`), and the Sarvam
  provider seam. Devanagari verified visually through the Remotion overlay.
- **2026-07-19 batch 2 (rematch day):** ⑥ **TTS provider seam** — `config/tts.json`
  routes voice-over PER LANGUAGE (`sarvam_tts.provider_for_lang`): hi/mr/bn/pa →
  `agents/sarvam_tts.py` (api.sarvam.ai, `SARVAM_API_KEY`, bulbul — VERIFY ids at
  first paid run), everything else ElevenLabs (still the only voice-clone path).
  Missing key/vendor failure degrades per-frame to ElevenLabs + `voice` ledger
  info — never a dead track. ⑦ **Compile grammar (delta B, deterministic):**
  keyword-less shots get cinematic coverage instead of a wall of "medium" —
  first shot of an authored block = wide establishing, dialogue alternates
  medium↔close-up, promoted visual cues = detail insert; an authored camera note
  always wins. ⑧ **3-stem mixer SHIPPED (same day):** `assembler._clip_ambience_track` builds
  one full-length AMBIENCE stem from clip-NATIVE audio (r2v clips generate
  sound WITH the video — event-synced by construction; previously stripped by
  `-an` at normalize) plus per-shot `ambience_path` SFX, each positioned at its
  clip's EFFECTIVE start (rule #9), gain 0.5. Mixed as a universal POST-step
  after whichever audio branch ran (`-c:v copy` — no re-encode); no ambience →
  exact no-op; failure degrades with an `audio` ledger info. `_run_inner` runs
  the S30 SFX pass before assembly: an authored `[Sound:]` cue (`audio_intent`,
  now on assignments) → `sfx.generate_sfx` (mmaudio) ONLY for clips WITHOUT
  native audio — never double-layered. Lipsync assembly path unchanged.
- **2026-07-19 batch (post-A/B adoption):** ① `remotion_overlay.render_overlay`
  takes `width/height` (CLI `--width/--height`, dims join the overlay cache key) —
  the portrait-default overlay on a landscape reel composited captions ~1700px
  down, i.e. rendered fine but OFF-SCREEN (the "captions missing" reel). ②
  `generate_character_portrait` adopts the galleri5 canonical-sheet recipe
  (single figure, FULL body head-to-feet, anti-grid/turnaround negatives,
  photoreal texture; flat-even lighting kept deliberately — S30). ③
  `generate_location_plate` gains `lighting_arc` (plate anticipates the story's
  key light moment; canvas passes the reel `mood`) + `orientation` (was
  hardcoded 9:16). ④ Compile mode promotes `[VISUALS:]` cues ≥60 chars to their
  OWN ordered silent shots (subject inferred from the cue; shorter cues stay
  note garnish) — the galleri5 23-vs-19 breakdown gap; Yamraj script now
  compiles 29 shots. ⑤ `/api/canvas/<id>/keyframes` HARD-gates on unlocked
  faces: 409 naming the characters when an on-screen character has no
  `ref_path`/frame ref (narrator + real/passthrough excluded); `force: true` is
  the explicit escape; the UI confirm sends it.
- **kie_video.py — HappyHorse R2V backend (2026-07-19, the A/B response):**
  `agents/kie_video.py` drives Kie.ai's jobs API (`createTask`/`recordInfo`,
  Bearer `KIE_API_KEY`) for `models.json` entries with `backend: "kie"` +
  `kie_model` slug — first entry `happyhorse_r2v` (`happyhorse-1-1/reference-to-video`,
  slug live-verified free via `tools/kie_probe.py`). Reference-to-video: up to 9
  `reference_image` URLs define subject IDENTITY (not first frame); the prompt may
  address them as `[Image 1]`… Native audio, 3–15s, 720/1080p. `clip_builder` adds
  a `kie` deferred-pool branch: refs = `character_ref_path` + `location_ref_path` +
  the still (hosted to public URLs via Higgsfield's uploader), and the refs join
  the clip cache key (changed ref → regenerate). Assignments + the
  `_build_frames_from_payload` rebuild now thread `character_ref_path`/
  `location_ref_path`. Routed FIRST in premium face/hero/dialogue lanes; falls
  back higgsfield→seedance→kling_pro (i2v from the still — identity conditioning
  lost, render survives). Pricing `kie.happyhorse_r2v_5s_usd` (estimate — VERIFY
  credits conversion).
- **Payload-rebuild field drop (fixed 2026-07-19):** `_build_frames_from_payload`
  REBUILDS frame dicts field-by-field, silently dropping anything unlisted —
  `location_clause`/`location_ref_path` (S30 anchoring — canvas plates never
  conditioned renders through this path), `character_appearance`, and the
  compile-mode `audio_intent`/`voice_direction` were all being lost. All five now
  whitelisted. When adding a frame key consumed at render time, ADD IT THERE.
- **Orientation threading (fixed 2026-07-19, A/B-test-surfaced):** frames now carry
  `orientation` (set in `_build_frames_from_payload` from the reel setting);
  `image_generator._SIZES` maps it per backend (fal `image_size` / openai `size` /
  prompt prose) — stills were hardcoded 9:16 so a 16:9 reel got portrait stills,
  center-cropped at assembly. `safety.check_face_sanity(…, orientation=)` now
  validates against the REQUESTED orientation (was portrait-only → every correct
  landscape still burned 3 QC retries). Orientation joins the still cache hash
  only when non-portrait (`|or:landscape`), so all pre-existing portrait caches
  stay valid. Location-plate generation is still orientation-blind (follow-up).
  Also `assembler.apply_brand_overlay`: on ffmpeg builds without drawtext
  (Homebrew default), the disclosure now renders via a PIL PNG + plain `overlay`
  instead of silently shipping an UNLABELED reel; any overlay-pass failure now
  fires `degradation.report("provenance", "alert", …)` — a lost disclosure is a
  governance gap, not a cosmetic one.
- **Sharp edge (fixed 2026-07-18):** Auto-length's token budget was 130 tok/shot ×
  a flat 40-shot guess (5200 total) — sized for short narration captions. A dense
  cinematic-dialogue brief (full spoken-line captions, ~30-40 beats) silently
  truncated the LLM response mid-JSON; `llm.json_loads_lenient`'s bracket-slice
  recovery can't fix a genuinely cut-off response (no real closing bracket exists),
  so it throws and `plan()` falls back to an 8-shot generic sentence-split — which
  is what then trips `story_review`'s slideshow warnings (uniform framing/duration),
  making a token-budget bug look like a content problem. Now 220 tok/shot, 16000
  ceiling. Verified live on a 31-beat dialogue script that truncated every time
  under the old budget.
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

### Location anchor (S30 Phase 1) — `location_id` / `location_clause` / `location_ref_path`
The place-level mirror of the character keys. `canvas_run.derive_locations` tags
`frame["location_id"]`; `set_location` propagates `location_clause` (the invariant
setting text from `_location_clause` — T11 phrasing) and `location_ref_path` (the
generated plate). `generate_contextual_image` APPENDS the clause to the prompt (after
the appearance clause, before the world clause) — part of the content-hash, so a
location edit regenerates its shots. **Sharp edge:** the plate ref is carried but NOT
yet conditioned on — `edit_image` takes one reference and the FACE wins (identity
beats place); plate-as-second-ref lands with D5 multi-ref.

**Cached** (`~/.hob_cache/locations/<md5>.json`, same convention as `scene_intelligence`
— "cached after first run, then $0"). Keyed on the **beat list only**, deliberately *not*
on the existing location ids even though they ride the prompt: keying on them would miss
on every re-derive (ids only exist from the 2nd click on) — precisely the case the cache
exists for. A hit replays the same locations, so id stability comes free; the reuse-ids
instruction only has to earn its keep on a miss (= the story really changed). Only a
*usable* result is cached (never a degraded empty). Tests must isolate `LOCATION_CACHE_DIR`
(autouse fixture in `tests/test_canvas_run.py`) — it is real global disk state.

**Re-derive contract** (hardened 2026-07-17 — these were live bugs):
- **Id stability is a money question.** The existing location ids are passed INTO the
  prompt with a reuse instruction; the merge then matches `id` → normalised `label`, and
  a predecessor can be claimed only once. A churned id used to orphan the operator's
  edited description and their **paid** `plate_path`. A dropped location that still
  carries a paid plate is reported to the degradation ledger (`warn`), never binned
  silently; its plate file stays on disk.
- **Tags are cleared, not just overwritten.** A frame whose location is dropped/renamed/
  unassigned has `location_id` + `location_clause` + `location_ref_path` popped — a
  leftover clause keeps riding the prompt (and the still's cache key) while being
  invisible on the Locations sheet.
- **`derive_locations` invalidates like `set_location`.** Anchoring moves the cache key,
  so if the clause/plate map actually changed and keyframes are `done|approved|
  generating`, it calls `invalidate_from(state, "keyframes")`. An unchanged re-derive
  invalidates nothing (no needless re-render).
- `scene` may be present-but-`None`; the beats scan sits outside the try, so it uses
  `(f.get("scene") or {})` — `dict.get`'s default does not apply to a present `None`.

### Canvas edit-story + UX declutter (S31 red-team)
- **`canvas_run.replan_brief(state, brief, ...)`** replaces the WHOLE brief and re-plans
  in place (vs `chat()` which appends a refinement); both share `_replan_from_brief`,
  which resets every downstream stage (`invalidate_from(state, "script")`) so stills for
  changed/removed shots don't linger showing the old story. Route:
  `POST /api/canvas/<id>/replan` (operator-gated — planner call). UI: the **✎ Edit story**
  top-bar button returns to the entry page with the brief intact; **Plan** becomes
  **Re-plan** and confirms before dropping generated work. Without this the entry box was
  unreachable after planning — the only way back (`＋ New`) wiped the story.
- **Stage bar is the pipeline only.** The 🎵 Music / ✂️ Beat cut / 🔊 SFX "extras" were
  removed: Music/Beat were buttons that only scrolled to the left-rail controls that own
  them, SFX was permanently disabled (mix stem ticketed).
- **Inspector — disclosure, not deletion.** A route-level audit found the inspector is
  ~26 DISTINCT controls, NOT duplicates: New-still/Re-roll/Re-create are different
  operations (still / still+clip / identity-safe), and the five real-media controls are
  different sources — each is the only path in its state. So the fix is grouping:
  **Replace asset** and **Overlays** (comic devices) are now collapsed `<details.insp-group>`;
  core edit fields + primary Shot Actions stay visible. Two real fixes rode along: (1) the
  6 overlay `<select>`s shared class `.fid-sel` with the fidelity select, and the change
  handler matches `.fid-sel` first → it would dispatch overlay values into `setFidelity`
  (benign no-op today, latent footgun); they now use `.ov-field`. (2) The one TRUE
  duplicate — the fidelity "recreate" rung — is removed (the 🎬 Re-create button has a
  superset condition), keeping the select for its only-path "restore"/"passthrough" rungs.
  `.insp-group` deliberately has NO `overflow:hidden` (that + flex-shrink is what clipped
  the left-rail accordion).
- **Left rail is a collapsible accordion.** Each section is a `<details class="cv-sec">`:
  World / Audio / Captions (output settings) collapsed by default; Story tools + the
  conditional Brand/Publish panels open when shown. `details.cv-sec` MUST carry
  `flex:0 0 auto` — `.cv2-left` is a flex column, so without it a details shrinks below
  its content and `overflow:hidden` clips the body (only the summary showed). A control
  inventory found 58 fixed controls at first sight (~134 with cast/locations/inspector);
  collapsing the settings takes ~16 of them one click away. Remaining declutter (the
  regenerate-shot triad, the real-media-assign routes, the fidelity-vs-buttons overlap)
  is tracked in `docs/CANVAS_ENTRY_PLAN.md` — deferred because each risks removing the
  only path to a capability and overlaps the inspector.
- **Nav close bug.** `.nav-menu`/`.nav-backdrop` carry an author `display`, which beat the
  UA `[hidden]{display:none}`, so closing the hamburger flipped `.hidden` but left the
  panel on screen (backdrop vanished → half-closed). Fixed with an explicit
  `[hidden]{display:none!important}` reset — same trap as the canvas page-section split.

### S31 pre-flight — what made Canvas able to replace the doors (`docs/CANVAS_ENTRY_PLAN.md` §5)
- **Real photos from a browser** (#1, THE blocker). `canvas.html` had a text box for a
  *server-side* path; a hosted server cannot see the user's disk, so the moat workflow
  (real photos → untouched passthrough) was Story-door-only in production even though
  every engine-side piece was already shared. Canvas now posts to the same
  `/upload-folder` route (batched at 40 MB, `session_id = run_id` so media lands in that
  canvas's assets dir) and fills `assets_dir` for `match-photos`.
- **Posting kit** (#2) → `agents/posting_kit.py`. Was inline in the Story route layer, so
  only `/story` could produce it. `build(frames, caption, cover_frame_id, mode)` raises
  `BrandCopyRefused` for brand runs (BRAND_PLAN §5 — a hashtag ranker is harmless on a
  story, not on an ad). Free/deterministic: no LLM, no spend. Routes: `/posting-kit`
  (Story) + `/api/canvas/<id>/posting-kit`.
- **Publish surfaces** (#3). `/export`, `/provenance`, `/credential`, `POST /performance`
  are run_id-keyed and always worked for a canvas `render_id` — only `main.js` surfaced
  them. Canvas's Publish panel now does (shown once `render_id` exists).
- **Brand gate** (#4). `web_app._canvas_mode(state)` derives `"brand"` from
  `state["brand"]` instead of the hardcoded `"story"`; `_canvas_brand_gate(state)` runs
  `brand.validate_mandatories` on **every paid canvas route** (`keyframes`, `video`,
  `render`, `render-language`) *before* the spend reservation. Brand mode is **opt-in**
  (`canvas_run.set_brand`; clearing the fields returns the run to a story) — otherwise
  every canvas would fail mandatories it has no UI to satisfy. Copy is stored verbatim,
  never generated. A test asserts each paid route arms the gate — the module-level
  `test_brand_mandatories` passes with *nothing calling it*, which is how this leaked.
- **Auth** (#5). `/api/canvas/plan` now carries `@auth.require_operator()` like `/run`.

### Test isolation (`tests/conftest.py`)
`HOB_RUNS_DB` / `HOB_RUNS_DIR` are pointed at a temp dir **before any test imports
run_store** (it resolves the env at import time). The suite was writing permanent
`unit-<pid>` / `perf-<pid>` rows into the operator's real archive — 229 of 281 rows in
one DB — and since `list_performance` is `LIMIT 100`, ~100 accumulated `perf-*` rows made
the feedback-loop test fail forever on a machine where nothing was wrong.

### Run storage — where the archive lives, and staying alive (`agents/run_store.py`)
**Both paths default to `~/.hob_runs`** (`DEFAULT_RUNS_DIR`; DB at `hob_runs.db` inside
it) and must share one durable filesystem. Override with `HOB_RUNS_DIR` /
`HOB_RUNS_DB`. They previously defaulted under `tempfile.gettempdir()`, which **follows
`TMPDIR`** — on a machine whose `TMPDIR` pointed at an external volume, the entire story
archive (2.4 GB / 49 runs, in the wild) sat on removable storage inside a directory the
OS may erase, and SQLite's WAL locking there raised hard `disk I/O error`s. A regression
test asserts the default is never inside the temp dir; `web_app._warn_if_archive_left_behind`
shouts at boot if a legacy temp archive exists while the live dir is empty (changing the
default silently repoints a deploy at an empty directory).

**`_conn()` self-heals.** It probes the cached per-thread connection (`SELECT 1`) and
reopens on failure. The cache was permanent, so one transient error poisoned the
connection for the life of the process and every later request 500'd until a restart —
the Library *"Couldn't load stories: Unexpected token '<'"* outage (a `disk I/O error`
became Flask's HTML error page, which the client's `.json()` choked on). Not
SQLite-specific: a pooled Postgres connection dies the same way, so the probe survives
the `agents/db.py` (`HOB_DB_URL`) migration.

### Canvas provenance subject — `_canvas_subject_name(state)` (web_app.py, S31 pre-flight)
A character carrying a real `ref_path` (or any frame with `character_ref_path` from
`attach_asset` mode=`reference`) means a **real person's face is being AI-generated** —
both paths stamp those shots `photo_spec="ai_portrait"`. `provenance.summarize` only sets
`real_person_ai` — and `_run_inner` only burns the on-screen disclosure — when
`subject_name` is non-empty, so `_canvas_render_data`'s hardcoded `""` silently labeled
every such reel *"no real person depicted"*. It now names those characters (falling back
to `"unnamed subject"` when a real ref exists but nobody is named), and returns `""` for a
fully synthetic cast so mythology/fiction correctly stays symbolic. **`likeness_consent`
stays auto-granted** — the canvas no-consent-gate call stands (owner decision); the label
is the compensating control it was traded for. *Open:* the label reads "— consented" under
an auto-grant (`provenance.py`, shared with Story where consent IS collected).

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
| **B3** | `check_likeness(image_path, reference_path, frame_id, min_similarity=6) -> bool` | Inside `_generate_image_checked` when a `likeness_ref` is set (ref-conditioned stills + ref-derived portraits) | Generated face that is NOT the reference person (fails on `same_person=false` or similarity < 6/10) |
| **Brand** | `critique_brand(image_path, frame_id, brand) -> bool` | After stills pass on brand runs | Visual conflicts with brand safety requirements |

Gate B2 uses a vision LLM call. Prompt: "Does this image match the description? Flag if
blank/abstract/empty when a real subject was expected." Returns `True` if OK.

Gate A is **non-blocking** on API error (logged, render continues). Gate B triggers
up to 2 retries (deleting the bad file) then accepts the last result.

Gate B3 (likeness chain, 2026-07-20) is the gate B2 could never be: B2 judges the
image against the *prompt text*, so a total stranger passed QC while looking nothing
like the reference person. B3 sends BOTH images (generated + real reference) to the
fast-tier vision LLM with a strict likeness-judge prompt — facial identity only
(face shape/eyes/nose/mouth/skin tone/age), ignoring pose/lighting/outfit/style;
too-small/turned-away faces pass. JSON verdict `{same_person, similarity 0-10,
reason}`; blocks below 6/10 → retry. Degrades open on API failure; disable with
`HOB_LIKENESS_QC=0`.

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

**Gate B + B2 + B3 in `_generate_image_checked()`:**
- Runs Gate B sanity check after generation.
- On failure: delete file, retry (≤2), then accept last result.
- Gate B2 critique also runs here; result logged but non-blocking.
- New param `likeness_ref: str = ""` — when set, Gate B3 (`safety.check_likeness`)
  must ALSO pass each attempt: the generated face is compared to that real reference
  photo, and a stranger's face triggers a retry like any other Gate B failure.
  `generate_contextual_image` passes `likeness_ref=reference_path` whenever it
  generates through the identity path (ref-conditioned stills), and the
  ref-derived canonical portrait passes its own source photo.

**`generate_character_portrait(..., reference_path="")` (likeness chain, 2026-07-20):**
when the character has a REAL photo, the canonical sheet is derived FROM it via the
identity path (`edit_image(reference_path, <canonical recipe + identity-preservation
clause>)`) instead of being invented from sheet-attribute text — same flat-lit
full-body recipe, but the face is the person's. The identity clause forbids
beautify/de-age/stylize. The photo's hash joins the cache key (`|ref:<md5>`), so
swapping the photo re-derives the sheet; Gate B3 is armed on the sheet itself
(`likeness_ref=reference_path`). No photo → text path, byte-for-byte unchanged.

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
| `/` | GET | Veristory marketing landing page (`landing.html`) — the public front door. Static brand surface, no pipeline access. Styled solely by the Veristory design system (`web/static/veristory/styles.css` → `tokens/*.css`), not by `style.css`. The app-shell `:root` tokens in `style.css` are value-swapped to the same palette (token names unchanged; `--on-accent`, `--seal`, `--seal-soft`, `--font-mono` added). |
| `/story` | GET | Story mode UI shell (the operator app's home; formerly served at `/`) |
| `/landing` | GET | 301 → `/` (legacy alias from when the landing first shipped there) |
| `/brand` | GET | Brand / Ad mode UI shell |
| `/studio` | GET | Studio mode UI shell (prompt → reel + identity library) |
| `/api/talents` | GET/POST | List talents / register a talent (name + descriptor + reference photo) |
| `/api/talents/<id>` | DELETE | Delete a talent |
| `/api/products` | GET/POST | List products / register a product (name + specs JSON + reference photo) |
| `/api/products/<id>` | DELETE | Delete a product |
| `/api/studio/plan` | POST | `shot_planner.plan(brief, scope, talent, product)` → editable `frames[]` |
| `/shoot` | GET | **S34 Product Photoshoot** page (`shoot.html`, standalone like canvas — not the wizard shell). Folder-driven: scan an inbox, see which SKU folders the ledger still owes, run them, review frames grouped by destination (`d2c` / `marketplace`) with parked frames ringed amber. |
| `/api/shoot/config` | GET | Unauthenticated, read-only: the `ASSETS_BROWSE_ROOT` this server accepts, a suggested inbox under it, and the guest limit. The page pre-fills the folder field from it — without it the operator types a path valid on their own machine, gets a bare "Path not allowed", and has nothing to act on. |
| `/api/shoot/scan` | POST | `{inbox}` → SKU folders + per-folder `pending` (ledger truth, so the page and the CLI agree) + cost estimate + pool size. `@safe_paths("inbox")`. |
| `/api/shoot/run` | POST | `{inbox, sku, force?, cap?}` → shoots ONE SKU, returns its manifest. **One SKU per request on purpose**: the browser drives the loop, so there is no background job state, no polling and nothing to lose when the page closes — the ledger already records what finished. `sku` is validated as a folder NAME (no separators) on top of `@safe_paths("inbox")`. |
| `/api/shoot/retry` | POST | `{inbox, sku, shot, model?, cap?}` → re-shoots ONE frame of an already-shot SKU, reusing its stored campaign decisions and its existing anchor. Redoing a whole SKU to fix one parked frame throws away five good generations, which is why this is separate from `/run`. **Retrying `front_2` is refused** — every other frame is conditioned on the anchor, so replacing it silently orphans the set. Derived frames (`front_1`, `detail`, `detail2`) re-crop for free. |
| `/api/shoot/campaign` | POST | `{inbox, sku}` → the stored `_campaign.json`, so results survive a reload. |
| `/api/shoot/personas/mint` | POST | `{brand?, count?}` → mints the casting pool (spends). |
| `/api/shoot/personas/cull` | POST | `{brand?, ids[], dropped}` → drop/restore faces. A dropped face is never cast again. |
| `/api/shoot/status` | GET | The `shoot_jobs` ledger + total spend. |
| `/api/shoot/personas` | GET | The brand's casting pool for the review strip. |
| `/canvas` | GET | Director Canvas page (`canvas.html` + `canvas.js`). **S33 unified canvas (2026-07-19):** page 2 is ONE pannable/zoomable surface — nine absolutely-positioned zones (`#zone-script/cast/loc/board/sketch/frames/video/audio/out`) on `#cv-world`, moved by a single transform (`cvApply`: translate+scale; drag/wheel pan, ⌘-wheel zoom-to-cursor, ⇧1 fit, jumplist `cvGlideTo` — an oversized zone glides to its HEAD at readable scale instead of confetti-fit). `cvLayout()` is a deterministic measure-and-place pass run at the end of every `render()` (zones are content-sized; the film column 04–08 stacks at one X so shot columns align — lanes share the board's 232+26px pitch). **Stage lanes (S33.2):** `renderLanes(board)` (called from `renderBoard`) fills `#lane-sketch/frames/video/audio`; lane cells carry `data-frame` + `.lane-cell`, route through `handleBoardOrInspectorClick` → the SAME Shot Inspector (now a docked overlay: `#insp-close` / Esc via `closeInspector`); `.video-poster` markup reused so hover-play works unchanged; selection ring syncs across board/timeline/lanes (`selectFrame`). Audio Setup controls moved statically from the settings drawer into `#zone-audio` (ids unchanged). The S31 stage view (`#stage-view`, `sv-*`) and the 📄 Script/board toggle were DELETED — the script panel renders permanently in zone 01 on every `render()` (skipped while focused or during T13 lang review, which now glides to the zone instead of hiding the board). Governance chrome (stage bar, cost banner, timeline, chat) stays docked, never pannable. **Docked action bar (2026-07-20):** `#settings-gear` (relabeled "⚙ Audio & Captions") + `#render-btn` moved out of the crowded `.cv-top` top-right into `#cv-dock` — absolute bottom-center of `.cv-canvas` (z-32, clear of the left nav-hint and right inspector), `:has()`-hidden when both buttons are hidden, in the `CV_NO_PAN` list; ids unchanged so all canvas.js show/disable/relabel ("Render reel"→"Final Cut") logic is untouched. |
| `/api/canvas/plan` | POST | `canvas_run.new_canvas(brief, …, story_type)` → new canvas (runs free Script stage); returns `run_id` + `public_state`. **`story_type`** = `real` (HOB — match/passthrough real media) \| `ai` (fiction — everything generated; UI hides the real-media folder tools, characters defined on the sheet). A mode flag into the shared engine (no fork); invalid → `real`. **Characters-first (ALL story types — auto-fill slice 1, was AI-only):** Plan runs `derive_characters` (free cast detection; tags `frames[].speaker_id`) so the sheet is populated before any spend — for real stories it's the anchor list for photo matching + consent; the board auto-renders it, a bulk "🎨 Generate all faces" drives `/character-portrait` per unlocked character (sequential, spend-gated each), and the Key Frames Generate button soft-warns (`confirm`) when non-narrator characters lack `ref_path` — warn-not-block, narrator excluded (voice-only). **Slideshow-risk gate (Item 8):** `plan_qc.score_plan(frames)` — 6 structural dimensions (repetition, motion/duration monotony, static ratio, caption wall, coverage), advisory only: warnings appended to `plan_review` as "Slideshow risk — …" with fixes, `{total, risk}` exposed as `public_state.plan_qc`, high risk also ledgered (`degradation.report("plan","warn",…)`). **Auto-fill carve-outs (owner):** music, per-shot image assignment, and per-shot emotion are never auto-filled; `plan_suggestions` now also returns a reel-level `mood`, consumed by the canvas `#mood` input (✨-fills only while empty; saved via `/settings` `mood` key, capped 60 chars, absent-key never clobbers; exposed as `public_state.mood`; threads into re-plans + the render payload). |
| `/api/canvas/<run_id>/state` | GET | Current board state (`canvas_run.public_state`) |
| `/api/canvas/<run_id>/advance` | POST (operator) | Run a stage. Free stages execute in-process; **paid stages return a per-stage cost + `check_spend_cap` result *before* any spend** (the anti-wallet-drain), then dispatch to the render pipeline. 409 if the stage is locked |
| `/api/canvas/<run_id>/approve` | POST (operator) | Approve a finished stage → unlocks the next stage's Generate |
| `/api/canvas/<run_id>/frame` | POST (operator) | Edit one shot's text/prompt from the board (`canvas_run.edit_frame`: caption/director_note/motion/negative/image_prompt); cascade-invalidates downstream. Cascade tiers: visual edit → storyboard, `duration` → video only (`timing_changed`), **`review_status`** (S30 P3 per-asset QA verdict ∈ `REVIEW_STATES`: needs_review/approved/production_ready/rejected) → **invalidates NOTHING** (pure metadata; board carries it as `review`, UI renders `.rv-*` chips + an Inspector select) |
| `/api/canvas/<run_id>/chat` | POST (operator) | Natural-language command box (`canvas_run.chat`): refine + re-plan shots via `shot_planner` (reuses the brain); resets downstream |
| `/api/canvas/<run_id>/match-photos` | POST (operator) | **The moat for real people:** clears `ai_portrait` on talent shots, then `image_matcher.smart_match` content-matches a folder of the operator's REAL photos/videos to the shots → real passthrough (untouched), AI fills the rest. Sets `state["assets_dir"]` (threaded into the render). Validated `_path_allowed`. Avoids generating a synthetic likeness of a named real person. |
| `/api/canvas/<run_id>/recreate` | POST (operator) | **Re-create ambient (ladder rung 3):** opt-in per-shot — generate a cinematic version of a NON-person scene *inspired from* the real footage (`image_generator.recreate_ambient` → `image_editor.edit_image`). **Identity-safe, triple-guarded:** rejects `uses_talent` shots, and refuses any reference where `safety.face_count` (Haar) **or** `safety.has_person` (vision LLM — catches angled/partial faces Haar misses) sees a person. Video refs: a frame is extracted first. Output labeled `ai_symbolic` + `recreated_from_real`. |
| `/api/canvas/<run_id>/settings` | POST (operator) | Persist **caption style** (on/off · font · size · color · position · `max_lines`=1/2-line) + **orientation** (9:16 portrait / 16:9 landscape / 1:1 square) on the canvas state. The engine already burns `caption_style` (`caption_writer` ASS) and `_orient_wh` sizes the reel; this just stores the operator's choice. Changing orientation **invalidates rendered stills** (they're generated at that aspect). Fonts list only INSTALLED families (libass) — **Montserrat + Satoshi** today. Also persists **`transition`** (crossfade/cut/dissolve/fade) and **`ip`** (HOB watermark, from `/ips` → `watermark.watermark_for`) — both already consumed by the render. |
| `/api/canvas/<run_id>/world` | POST (operator) | **World/Context (P2):** set a global art-direction `style` + `setting` (`canvas_run.set_world`) — stamped onto every frame as `world_style` and injected into generation (`image_generator._inject_world`, contextual + symbolic paths; storyboard stays pencil), so the whole reel shares one look and world (same palace/forest, one style — the consistency fiction needs). Cascade-invalidates keyframes. |
| `/api/canvas/<run_id>/storyboard-art` | POST (operator) | **Storyboard view (#6, the comic-board):** renders a **pencil-sketch panel per shot** (`image_generator.generate_storyboard_panel` — graphite sketch of framing/blocking/camera-move + blue motion arrows). A **planning artifact**: loose sketch, NOT photoreal, NOT a likeness, never used in the reel. Cheap draft model (`seedream` ~10s/panel), content-hash cached, **rendered in parallel** (ThreadPoolExecutor, 5 workers) via `_track_job` (orphan-recovered on restart). Spend-gated up front (`pricing.storyboard_cost × shots`). Progress on `public_state.sketching/sketch_done/sketch_total`; per-shot path on `board[].storyboard_art`. |
| `/api/canvas/<run_id>/upscale` | POST (operator) | **Generative upscale (final-render quality lift)** of one shot's still via `agents/upscaler.py` (fal.ai). **Routed by asset kind so the moat holds:** a REAL shot → **faithful** super-res (`aura_sr`, no invented detail → a real face stays exact); an AI shot → **creative** (`clarity_upscaler`, adds detail). Real photos already >3072px are **skipped** (already high-res for a 9:16 reel — no spend). Spend-gated (`pricing.upscale_cost`), output cached, degrades to the original on failure. Sets `frame["upscaled"]`; `orig_visual` preserved for revert. |
| `/api/canvas/<run_id>/restore` | POST (operator) | **Restore (Reality–Fidelity ladder rung 1):** non-generative cleanup of the matched REAL footage via `agents/restore.py` (ffmpeg: upscale + denoise + contrast-adaptive sharpen + grade; video adds 2-pass vidstab + deflicker). Same identity/claims — **zero authenticity cost**. Threaded; progress on `public_state.restoring/restore_done/restore_total`; cascade-invalidates downstream. See `docs/REAL_MEDIA_QUALITY_LADDER.md`. |
| `/api/canvas/<run_id>/asset` | POST (operator) | Attach an uploaded image to shot(s) (`canvas_run.attach_asset`): **real** (non-AI `photo_spec` → `model_router` PASSTHROUGH, the moat), **reference** (real face → AI likeness, kept `ai_portrait`+`character_ref_path`), or **scene**. `all_talent` applies to every people-shot. Path validated via `_path_allowed`; image first uploaded through `/upload-photo` |
| `/api/canvas/<run_id>/keyframes` | POST (operator) | Render the **cheap stills only** (reuses `_execute_preview`); lets the operator review/re-roll before committing to video. Sets `render_phase="keyframes"`. The later full render shares the run dir → reuses these stills (content-hash cache, no re-spend) |
| `/api/canvas/<run_id>/video` | POST (operator) | **Video stage** — animate the approved Key Frames into clips ONLY (`_run_inner` with `stop_after="clips"`); gated behind Key Frames approval. Clips persist in the run dir + content-hash cache so Final Cut reuses them (no re-spend). Per-shot reveal via `clip_ready`. |
| `/api/canvas/<run_id>/render` | POST (operator) | **Final Cut** — gated behind **Video** approval; reuses the cached stills AND clips, only does audio + assembly. Render the board into a reel via `_canvas_render_thread` → generates a music bed (Suno, best-effort) so the engine's **beat-aware cutting** snaps cuts to the beat (anti-slideshow, P1). **Suno-independent:** sets `beat_grid_bpm` (`_canvas_tempo_bpm` from mood) so `assembler.beat_overlaps(fallback_bpm=)` cuts on a synthetic tempo grid even with no music. **Audio options** (body): `music_type` = generate (Suno) / upload (`music_path`, validated `_path_allowed`; song uploaded via `/upload-photo`) / voiceover (`voice_id` from `/voices`; sets `beat_grid_bpm=0` for gentle cuts; `bg_music_path` = bed played looped + ducked UNDER the narration via the assembler's brand-mode VO-over-bed mix — operator-supplied (validated `_path_allowed`) **or, since 2026-07-19, auto-generated**: `_canvas_render_thread` now generates the same Suno bed for VO renders too and routes it to `bg_music_path` (never `music_path`, which carries the narration track in VO mode) — previously VO mode skipped bed generation entirely and narration played over dead silence; a bed failure degrades to VO-only with a `warn` (not the no-soundtrack `alert`) since the story still has its narration) / none. Then dispatches `_execute_pipeline`. **Reuses the Key Frames render dir** so stills are cached. Sets `render_phase="full"`. Same governance gates as `/run`. (`/rendered` reconciles paid stage chips by `render_phase` so Key Frames-done ≠ Video-done). **Silent-output surfacing (the Suno-credits incident):** a music-generation failure sets `state["audio_warning"]` via `_set_canvas_audio_warning` (cleared when a new Final Cut starts and on music success), and after `_execute_pipeline` an output QC probe (`_output_is_silent` — ffmpeg volumedetect, `max_volume < -60 dB`, best-effort/no false alarms) re-checks the finished file whenever `music_type != none`. Exposed as `public_state.audio_warning`; the board shows a red 🔇 strip and the render status reads "done — ⚠ NO AUDIO" instead of "done ✓". |
| `/api/canvas/<run_id>/rendered` | POST (operator) | Per-shot rendered media read from the render dir (survives reloads) + reconciles paid stage statuses to the render's real status (so the rail can't stick on 'generating'). **Reconcile NEVER downgrades an `approved` stage** — it only unsticks `generating`→`done`; a late poll must not reset an operator-approved Key Frames back to `done` (that silently un-approved it and 409'd the Video stage forever). |
| *(asset-QC gate)* | — | `image_matcher.exif_upright(frames, out_dir)` — called by `match-photos`, `rematch` and `asset` (mode=real): any matched real IMAGE with EXIF orientation ≠ upright gets a pixel-rotated copy in `RUNS_DIR/<canvas_id>/upright/` and `visual_path` repointed (original never modified — real-media preservation). Complements `clip_builder`'s pre-Kling transpose so Ken Burns/assembly/board paths agree. `check-matches` vision prompt also flags rotated pixels / watermarks / unreadable full-frame documents → `match_flag`. Canvas caption default is the engine's storytelling style (`_CANVAS_CAPTION_DEFAULT`: Baskerville 52, 2 lines); UI target-length default is 60s (default-only; operator keeps Auto). |
| *(L99 hardening pass, 2026-07-03)* | — | **Degradation Ledger (T1):** `agents/degradation.py` — `bind(run_id)`/`report(step, severity∈info\|warn\|alert, msg)`/`drain()`; instrumented at `model_router.run_with_fallback` (all vendor failovers), `image_editor.edit_image` (identity chain; total failure = alert), safety Gate A/B2 skips, canvas music failure + output-silence QC. Persisted as `state["render_report"]` (full render + keyframes stage), exposed via `public_state.render_report`, rendered as the 🧾 panel. **State safety (T2):** per-run RLock + `state["rev"]` + `_canvas_mutate(run_id, apply_fn)` (atomic re-load→narrow-merge→save); storyboard/restore/check jobs write per-frame merges, never their stale copy. **Media-type sniffing (B1):** `llm._image_bytes_and_format` reads magic bytes (generators write PNG into .jpg paths; wrong declared type made strict vision APIs reject → Gate B2 silently dead). **Identity phrasing (B2):** ref-edit prompt uses character-consistency language (the "EXACT person's face" wording tripped fal's content checker → 422 → silent identity loss). **Consistency (T11):** `species` character attr; `_character_appearance` phrases wardrobe/anatomy as invariants; portrait = three-quarter/full-outfit framing; ref-prompt demands same outfit. **Motion (T12):** physics negatives in `DEFAULT_KLING_NEGATIVE` + keyword-routed `_MOTION_PRESETS` (strain/kneel/walk/rise) in `_kling_motion_prompt`; `motion_override` wins. **Voices (T4):** `voice_id` character attr → `_canvas_render_data.voice_map` → `generate_voiceover_track(..., voice_map)` → `cast.voice_for_frame` per spoken frame. |
| `/api/canvas/<run_id>/translate` · `/translation` · `/render-language` | POST (operator) | **T13 language versions:** translate all captions (LLM, free) → `state["translations"][lang]` (master captions untouched) → operator review-edits per line (MANDATORY gate) → render-language dispatches a FRESH render_id with deep-copied frames + reviewed captions + `data["language"]` (caption font auto-switches via `languages.font_for_language`; VO voice via `cast.voice_for_frame(lang=…)`); clips/stills reuse content-hash caches; `state["language_renders"][lang]` records the version. |
| `/library` + `/api/library/<canvas_id>` | GET | **T15 Story Asset Library:** per-story read-only asset listing (characters/storyboard/keyframes/clips/audio/final cuts incl. language versions), path-confined, skips small/fresh (half-written) files; served via `/media`; zip via existing `/export/<render_id>`. |
| `/api/canvas/<run_id>/takes` · `/take-restore` · `/restill` | GET/POST (operator) | **T5:** re-roll archives the prior clip to `takes/` (ns-stamped, pruned to 4, `_archive_take`); takes listed newest-first; restore copies a take back (reversible — current gets archived too). `/restill` regenerates ONLY the still from the edited prompt (image-cost gate, `force_regen_ids`), updates state via `_canvas_mutate`. |
| `/api/canvas/<run_id>/overlays` · `/overlay-preview` · `/speaker-chips` | POST (operator) | **T14 Frame Composer:** ≤2 preset overlays per shot (`agents/overlays.py`: PIL style pass chip/polaroid/rounded/bubble/sticker → alpha PNG; ffmpeg per-clip composite, clip-local `enable=` timing, audio preserved; cache key = clip content + resolved spec so the base clip is never re-rendered). Preview = free PIL composite on the still. speaker-chips = bulk chip on every non-narrator spoken shot with a locked portrait. Composite failure → un-overlaid clip + ledger warn. |
| *(caption engine seam)* | — | **T6:** `config/captions.json` engine `libass|remotion` (+ per-reel `caption_style.engine` from canvas settings). `agents/remotion_overlay.py`: props from the SAME frames+`frame_timecodes` data as the .ass builder (hero = `hero_line` or short line ending !/…), rendered via tools/remotion-captions (`--pixel-format=yuva444p10le --image-format=png` — alpha verified by ffprobe or it raises), props-hash cached; composited post-assembly before the watermark/provenance pass. ANY failure → libass burn + ledger warn (render fail post-assembly → alert + captionless copy so the reel still ships). |
| `/api/canvas/<run_id>/reroll` | POST (operator) | Re-roll ONE shot: regenerate its still (`_generate_stills` force) + clip (`build_clips`), write into the render dir. Single-frame spend gate. Same path as `/redo-still`+`/redo-motion` |
| `/api/canvas/<run_id>/assets` | GET (operator) | List the operator's media folder (`state["assets_dir"]`) → `[{path,name,is_video}]` for the **per-shot photo picker** (thumbnails served by `/media`). Auto-match is never perfect on abstract beats; the picker lets the operator swap any shot to the right real photo in two clicks (→ `/asset` mode=`real`) instead of re-matching everything. Validated `_path_allowed` |
| `/api/canvas/<run_id>/ai-source` | POST (operator) | **AI escape hatch** for a matched real photo the operator dislikes that Restore can't fix (bad *content*, not quality): `canvas_run.set_ai_generic` replaces the shot with a **fully AI-generated** image (no real footage, no face ref) — identity-safe generic figure/scene from the shot's own prompt. Original real media preserved as `orig_visual` for undo. (AI-likeness-from-an-uploaded-face goes through `/asset` mode=`reference`, which sets `ai_likeness` + the 'AI · likeness' label; **operator decision: not consent-gated, but always labeled + flagged in `/provenance`**.) |
| `/api/canvas/<run_id>/fidelity-suggest` | POST (operator) | **Reality–Fidelity auto-suggest (ladder rung 1d):** `canvas_run.score_fidelity` scores every REAL shot via `restore.quality_score` (ffprobe resolution + OpenCV Laplacian-variance sharpness) and stores a recommended rung on each frame (`fidelity_suggested`/`fidelity_reason`/`quality_score`). Read-only — **no spend, no media change**. Person shots are never pushed past Restore. Degrades to 'unknown→passthrough' without OpenCV/ffprobe |
| `/api/canvas/<run_id>/fidelity` | POST (operator) | Set a shot's rung. **Restore/Re-create are dispatched by the UI to the verified `/restore`+`/recreate` routes** (reused untouched); this route handles **Passthrough** (`canvas_run.revert_passthrough`) — drop the override and restore the shot to its **untouched original** real media (preserved as `orig_visual`). Cascade-invalidates downstream |
| `/api/canvas/<run_id>/characters` | POST (operator) | **Cast detection (Characters stage):** `canvas_run.derive_characters` runs `cast.detect_cast(frames)` to surface the REAL people in the story (narrator + named speakers), stored on `state["characters"]` (`{id,name,gender,age,consent,ref_path}`). Idempotent; safe to re-run. Returns `public_state.characters` |
| `/api/canvas/<run_id>/character` | POST (operator) | Update one **story-level character** (`canvas_run.set_character`): real reference photo + consent AND appearance **`attrs`** (role/name/gender/age/skin_tone/hair/clothing/source). Propagates to every shot whose `visual_subject_id==char_id` (falls back to `speaker_id` for older frames — see §6 speaker_id vs. visual_subject_id): `character_ref_path` (face identity) + a `character_appearance` clause (`_character_appearance`) that `image_generator.generate_contextual_image` injects into the prompt — so the character's look stays consistent across the reel, including a narrated-about (rarely-quoted) protagonist. Per-frame overrides still win. Path validated `_path_allowed`. |
| `/api/canvas/<run_id>/character-portrait` | POST (operator) | **P1 character-sheet-first:** generate a CANONICAL portrait for one character from its sheet attributes + the world style (`image_generator.generate_character_portrait`, face-strong model + QC gates), set it as that character's `ref_path`, and link it to their shots (`set_character`) so every shot conditions on the SAME face via the pluggable identity path. AI/fiction characters (generated → no real-person consent gate). Spend-gated. **Likeness chain (2026-07-20):** if the character already holds a REAL photo (any `ref_path`/`ref_full_path` that isn't a generated `charref_*` sheet — the uploaded ref or its face crop), the route passes it as `reference_path` → the sheet is derived FROM the person's face (identity path + Gate B3), and the real photo is preserved on `ref_full_path` after `ref_path` becomes the `charref_*` sheet, so "↻ New face" re-rolls keep the identity source. |
| `/api/canvas/<run_id>/locations` | POST (operator) | **S30 Phase 1 location anchoring (the S28 "character sheet for places"):** `canvas_run.derive_locations` — one reasoning-tier LLM pass (`_LOCATION_SCHEMA`) → the story's distinct places at slugline granularity (deduped, 1–4 typical), stored on `state["locations"]` (`{id,label,description,time_of_day,plate_path,source}`) and tagging `frames[].location_id` (the place-level `speaker_id`). Re-derive merges operator work by id. Degrades to a **no-op** (frames unanchored + `degradation.report("plan","info",…)`). Free. |
| `/api/canvas/<run_id>/location` | POST (operator) | Update one location's `attrs` (label/description/time_of_day) — `canvas_run.set_location` re-propagates the **invariant clause** (`_location_clause`, T11 phrasing: "this EXACT location… same geometry, same light direction") onto every frame tagged with it as `frames[].location_clause`; `image_generator.generate_contextual_image` appends it to the prompt (part of the cache-hash → edits regenerate stills). Also stamps `frames[].location_ref_path` (the plate) — **reserved for the D5 multi-ref follow-up**; the FACE ref wins today's single-ref edit path (identity beats place, S19/S20). Cascade-invalidates keyframes. |
| `/api/canvas/<run_id>/location-plate` | POST (operator) | Generate the CANONICAL plate for one location (`image_generator.generate_location_plate`) — **plate discipline** (teardown §10.4 item 5): EMPTY environment, no people, negative space at center for characters to occupy, lighting headroom. Checked generation path on purpose (Gate B passes no-face images by design; Gate B2 catches baked-in text/era drift). Applies just-typed `attrs` first (same lesson as the portrait route); `variant` obeys rule 12 (0 = reuse, else fresh). Cache: `locations/locplate_<loc>_<md5(model|loc_v<variant>|prompt)>.jpg`. Spend-gated at `pricing.image_cost("flux")`. |
| `/api/canvas/<run_id>/rematch` | POST (operator) | **Per-shot re-match (C6):** clear ONE shot's media + re-run the role-aware `image_matcher.smart_match` → auto-pick the best-fitting photo for just this beat (vs manual 🖼 Pick). Needs a matched folder; cascade-invalidates keyframes. |
| `/api/canvas/list` | GET | Recent saved canvases (`run_store.list_canvases`) for the resume picker — each entry carries **title + updated_at + shots + story_type** so same-brief sessions (separate `run_id`s, not real duplicates) are distinguishable by time/shots/mode in the label |
| `/api/canvas/<run_id>/delete` | POST (operator) | Delete a saved canvas (`run_store.delete_canvas` — removes the run row + logs). Powers the resume picker's 🗑 to clean up old/duplicate sessions. |
| `/api/canvas/<run_id>/budget` | GET | Whole-reel cost up front (`sum(state.costs)`) + spend-cap status (`governance._spend_cap` / `check_spend_cap`) → the cost banner. Fast (no live vendor probe). Parity with galleri5's credit warning, backed by our hard per-stage gate |
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
| `/export/<run_id>` | GET | Editor hand-off zip: **`timeline.fcpxml`** (importable timeline for Premiere/Resolve/FCP), **`captions.srt`** (standard subs), `clips/`, `output.mp4` (C2PA-signed when signing succeeded), `edit_list.json`, `provenance.json`, `content_credential.json`, `source_media_review.json`, `decisions.jsonl`. FCPXML/SRT written in `_run_inner` via `agents/fcpxml.py`; clips back-to-back (crossfades flattened), captions kept separate so a caption quirk can't break the clip-timeline import. |
| `/performance/<run_id>` | POST | Post-publish feedback (Gap #3): `{views, likes, note}` for a finished run → `run_store.save()` writes `runs.performance_views`/`performance_likes` (nullable INT) / `performance_note` (TEXT) / `performance_by` (verified operator). 404 on unknown run; `get_json(silent=True)` + int coercion + note cap so a bad body never 500s; upsert. **Gated by `auth.require_operator`.** |
| `/performance` | GET | Completed feedback loop (Gap #3): leaderboard (`run_store.list_performance()`, best-performing first) + roll-up summary. Gated. |
| `/provenance/<run_id>` | GET | Authenticity/provenance summary (Gap #5): real vs ai_symbolic vs AI-likeness-of-a-real-person, from the per-run `provenance.json` artifact (else recomputed from the stored payload via `agents/provenance.py`). Since the C2PA slice, the artifact carries a per-frame `frames` array (tier/face/voice/duration + effective start/end + real-source basename) — written provisionally at dispatch, **rewritten at finalize** in `_run_inner` from the RESOLVED frames + `frame_timecodes`. |
| `/credential/<run_id>` | GET | The reel's embedded **Content Credential (C2PA)** summary: validation state, assertion labels, per-frame provenance the signature attests. Reads `content_credential.json`, else reads the credential straight off the signed `output.mp4` via `content_credential.read_credential`. 404 if the reel was never signed. |
| `/login` , `/logout` , `/me` | POST / POST / GET | Operator auth (Gap #1): `authenticate()` → HS256 JWT in an httpOnly cookie; `/me` reports the current operator. Seed operators with `python -m agents.auth add-operator`. |

**Auth (Gap #1).** Money/rights routes — `/run`, `/preview`, `/retry/<id>`, `/performance*`, `/project-version`, `/api/canvas/<id>/{advance,approve,frame,chat,asset,keyframes,video,render,rendered,reroll,match-photos,assets,rematch,restore,recreate,upscale,storyboard-art,settings,world,redistribute,character-portrait,characters,character,fidelity,fidelity-suggest,check-matches,ai-source,delete}`, and `/brand-approval` (requires the `approver` role) — are wrapped by `agents/auth.require_operator(*roles)`, which validates the cookie/Bearer JWT and injects the *verified* `operator` (handlers no longer trust a client-supplied `operator_id`). `HOB_AUTH_DISABLED=1` bypasses for local dev; `HOB_AUTH_SECRET` signs tokens in prod.

**Storage (Gap #2).** `agents/db.py` selects SQLite (default) or Postgres from `HOB_DB_URL`; new stores (`auth`) route through it dialect-neutrally. The legacy per-store SQLite bridges migrate onto it for the RDS cutover (SCALE_PLAN Phase 2).

**Likeness consent (Gap #4).** `agents/governance.{likeness_modalities,validate_likeness_consent,record_likeness_consent}` gate AI face/voice of a *named real person* on `/run`, recorded against `consent_records.{face,voice}` and the verified operator.

**Content Credentials (C2PA — PROVENANCE_PLAN Slice A).** `agents/content_credential.py`:
`sign_reel(mp4, prov_summary, consent) -> {ok, signed_path, issuer, tsa} | {ok:False, error}`,
`read_credential(mp4)`, `build_manifest(prov, consent)` (pure). Called at the tail of
`_run_inner` right after the provenance finalize-rewrite; **best-effort** — any failure →
`degradation.report("provenance","warn",…)` and the reel ships unsigned (never a failed
render). `provenance.classify_frames(frames, frame_times)` supplies the per-frame rows
(source = **basename only** — rows are embedded verbatim in the public credential, so local
paths must not leak). Signer material: `HOB_C2PA_CERT`/`HOB_C2PA_KEY` (PEM chain + PKCS#8
key), else a self-signed dev CA+leaf chain is generated once via openssl into `HOB_C2PA_DIR`
(structurally Valid, untrusted issuer). Sharp edges baked into the module (from the
2026-07-07 de-risk): package is `c2pa-python` not `c2pa`; key MUST be PKCS#8; cert MUST be a
2-cert chain (bare self-signed leaf rejected); `ta_url` MUST be a real RFC-3161 TSA — so
**signing makes one outbound HTTP call** (fallback list `HOB_C2PA_TSA`, kill-switch
`HOB_C2PA_DISABLED=1`). Artifacts: signed `output.mp4` (replaced atomically via temp +
`os.replace`) + `content_credential.json`; both in the export zip; served by `/credential`.

**Slice B (evidence layer, same plan).** (a) **Decision log:** `degradation.decision(stage,
frame_id, model, …)` + `drain_decisions(run_id)` (companion channel to the ledger;
`report()` also takes `frame_id=`). Image decisions recorded at the `select_model` site in
`_generate_stills`; video truth harvested at finalize from the clip items (`_model_id`,
`cached`, and the new `_fallback:"kenburns"` marker set in `clip_builder`'s poll-failure
branch). Written per-run as `decisions.jsonl`; per-frame models/fallbacks are merged into
`provenance.json`'s `frames` rows (segment `f03_2` → frame `f03`; PASSTHROUGH skipped) so
the credential attests *which model made each shot*. Caveat: the image row is the routed
*selection* — a cross-vendor fallback inside `_generate_image` is still an axis-level event.
(b) **Source-media review:** `source_media_review.write_review(run_dir, frames)` runs in
`_run_inner` right before `_generate_stills` — sha256 + ffprobe/PIL probe of every real
`visual_path` → `source_media_review.json` (basenames only). Best-effort: probe errors →
ledger warn, never a block. (c) **Schema gate:** `schemas/provenance.schema.json` validated
at finalize (invalid → ledger warn; `HOB_SCHEMA_STRICT=1` raises — used by tests). The
schema REJECTS absolute paths in `frames[].source` (privacy leak = validation failure).
(d) **Consent evidence:** `governance.consent_evidence(data)` aggregates the
`consent_records` rows (face/voice grants, confirmed_by, confirmed_at, `record_ids` as
**strings** — c2pa's CBOR encoder mangles small-int arrays into byte strings) and replaces
the Slice-A payload stub in the credential's consent assertion; falls back to the payload
grant (`source:"payload"`) when no DB rows exist. All three artifacts ship in the export zip.

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
| `/media` | GET | Serve asset thumbnails (validates path via `_path_allowed`; `@safe_paths("path", source="args")` enforces it at the boundary) |
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

**`safe_paths(*fields, source="json", required=False)`** ([web_app.py](../web_app.py)) — the
route-boundary choke point for filesystem safety (L99 "one seam, enforced"). A route declares
which request fields carry a client-supplied path; any present value resolving outside
`RUNS_DIR` / `ASSETS_BROWSE_ROOT` is rejected with **403 before the handler runs**, so a new
route can't silently skip the check. `source="json"` reads the body, `"args"` the query string.
Fail-closed on a disallowed path; absent/empty fields are skipped unless `required=True` (→400).
Additive — it sits *below* `@auth.require_operator()` and leaves existing inline `_path_allowed`
checks (friendlier route-specific errors) in place as defense-in-depth. Applied so far to `/media`
(`source="args"`) and `/api/canvas/<run_id>/render` (`music_path`, `bg_music_path`); the
convention (build-feature rule 17) is to add it to every new route that consumes a client path.
Note `_path_allowed` uses `Path.resolve()`, which follows symlinks — a symlink inside an allowed
root pointing outside is rejected because the *resolved target* is checked (no symlink escape).

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
