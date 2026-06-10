# HOBAILabs — Low-Level Design (LLD)

**Revision:** 2026-06-09 · branch `feat/pipeline-expansion-roadmap`
**Companion:** [HLD.md](HLD.md) — system context, flow, decisions
**Audience:** engineers modifying the pipeline. File references are clickable.

---

## 0. Module Map

```
run_caption.py            CLI orchestrator (the canonical pipeline)
main.py                   Legacy voiceover pipeline (segmenter→TTS→match→assemble)
web_app.py                Flask server: parse/preview/estimate/run/progress(SSE)/download

agents/
  script_parser.py        text → frames[]  (Format A/B, annotations, auto-match)
  image_matcher.py        opt-in LLM content match (describe → assign)
  scene_intelligence.py   LLM director per frame → scene{} (cached)
  llm.py                  pluggable chat()/vision brain (OpenAI|Bedrock|Gemini)
  model_router.py         shot → model id (pure logic over config/models.json)
  image_generator.py      ai_portrait/ai_symbolic → still (flux|openai|fal backends)
  image_editor.py         [edit:] pass on a still (gpt-image)
  safety.py               Gate A (moderation) + Gate B (face sanity)
  lipsync_coordinator.py  audio → CDN → Hedra/SyncLabs → lipsync_clip_path
    hedra.py / synclabs.py    vendor clients
    tts_generator.py          ElevenLabs audio
  clip_builder.py         still/video → animated clip (kenburns|kling|higgsfield|fal)
    higgsfield.py / fal_video.py / fal_client.py   vendor clients
  caption_writer.py       frames → ASS subtitle file
  assembler.py            clips → normalize → concat/xfade → captions → music → mp4
  pricing.py              whole-pipeline cost estimate (config/pricing.json)
  style_exemplars.py      opt-in in-context house-style injection (USE_EXEMPLARS=1)

config/  models.json (catalog+routing) · pricing.json (costs) · llm.json (provider)
~/.hob_cache/  kling_clips · scene_designs · image_descriptions.json · lipsync_clips · lipsync_audio
```

---

## 1. `script_parser.py` — text → `frames[]`

**Entry:** `parse_frame_script(script_path, assets_dir, max_frame_dur=9.0, smart_match=False)`
→ [agents/script_parser.py:154](../agents/script_parser.py#L154)

- **Format detection:** presence of `visual:` → Format A; else Format B (the HOB format).
- **Format B parsing** ([script_parser.py:56](../agents/script_parser.py#L56)): split on `Caption:`
  (Instagram text, dropped), strip leading `Reels`, split body on `\bFrame\s*\d+\b`.
  Frame **numbers are positional, not authoritative** — re-indexed `f01..fNN`.
- **Annotations** (regex, case-insensitive, removed from the on-screen caption):
  `[note:]`→`director_note`, `[photo:]`→`photo_spec`, `[edit:]`→`edit_prompt`,
  `[camera:]`/`[motion:]`→`motion_override` (camera wins if both),
  `[lipsync: yes|true|1]`→`lipsync`, `[voice:]`→`voice_override`,
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

1. **`describe_images(paths)`** ([:131](../agents/image_matcher.py#L131)) — one
   vision call per file → 1–2 sentence description **including any text/names visible**.
   Cached **forever** by image content hash in `~/.hob_cache/image_descriptions.json`.
   **Videos** are sampled into up to 3 keyframes (`ffmpeg` at 15/50/85%) and described
   as `(real video clip) …` ([:82](../agents/image_matcher.py#L82)).
2. **`assign_images(frames, descriptions)`** ([:175](../agents/image_matcher.py#L175))
   — one reasoning call returning `{frame_id: image_number}`. Prompt biases toward
   names/text matches and **prefers real video clips over stills** for equal fit.

Only fills frames with no `photo_spec`, no `ai_*`, excludes pinned files; never
touches the animation stage. `style_exemplars.matching_examples()` is prepended when enabled.

---

## 3. `scene_intelligence.py` — the director

**Entry:** `design_all_scenes(frames, subject_name, subject_description="")`
→ [agents/scene_intelligence.py:189](../agents/scene_intelligence.py#L189)

- Per-frame `design_scene()` picks one of three system prompts by `visual_type`:
  `symbolic` (objects, no people), `contextual` (age/era-accurate portrait), or
  `portrait`/default. Returns strict JSON:
  `{emotion, scene_description, image_prompt, motion_prompt, camera_angle}`.
- **`visual_type` derivation** ([:214](../agents/scene_intelligence.py#L214)):
  `ai_symbolic→symbolic`, `ai_portrait→contextual`, else `portrait`.
- **`has_real_photo`** flag tells the director to design *motion only* and skip the
  image prompt (the real photo is kept).
- **Caching** ([:23](../agents/scene_intelligence.py#L23)): `MD5(caption, note,
  visual_type, subject_name, subject_description, has_real_photo)` → JSON in
  `~/.hob_cache/scene_designs/`. Changing any of those re-runs GPT.
- **Parallelism:** `ThreadPoolExecutor(max_workers=min(n,10))`; results merged back,
  order restored. Silent frames get a canned slow-zoom-out scene with no image prompt.
- **Fallback:** on LLM error, a generic photoreal prompt + slow push-in is returned
  (never raises). `style_exemplars` preamble/examples appended to system prompt when on.

> Note: prompt copy references "gpt-image-2" / "Kling v3" and an Assamese subject —
> these are prompt-engineering details, not hard dependencies; the brain is provider-agnostic.

---

## 4. `llm.py` — the pluggable brain

**Entry:** `chat(messages, *, json_mode, max_tokens, temperature, model_tier) -> str`
→ [agents/llm.py:105](../agents/llm.py#L105)

- **Provider** from `LLM_PROVIDER` env or `config/llm.json` (`openai` default).
- **Model** from `LLM_<TIER>_MODEL` env, else `config[provider][tier]`, else `reasoning`.
  `model_tier` is `"reasoning"` or `"vision"`.
- **Message format is provider-neutral:** `content` is a string or a list of
  `{type:text}` / `{type:image, path|data_uri}` parts. Each backend translates:
  - **OpenAI** ([:121](../agents/llm.py#L121)) — `image_url` data-URIs; `response_format`
    for json_mode.
  - **Bedrock Converse** ([:151](../agents/llm.py#L151)) — system blocks separated;
    images as raw bytes; json_mode emulated via a system directive (Claude has no
    `response_format`). IAM auth, no API key.
  - **Gemini** ([:195](../agents/llm.py#L195)) — system_instruction + PIL images;
    `response_mime_type` for json.
- **`json_loads_lenient`** strips ```` ```json ```` fences and slices outer braces.

**Axis boundary:** only reasoning/vision is routed here. **Image generation and
audio are separate axes** (image via `model_router`+`image_generator`, audio via
`tts_generator`) — do not route them through `llm.py`.

---

## 5. `model_router.py` — shot → model id

**Entry:** `select_model(kind, shot, cost_tier="draft", override="") -> str`
→ [agents/model_router.py:104](../agents/model_router.py#L104). Pure logic + JSON read,
**unit-testable, no API calls.**

Resolution order:
1. **Valid override wins** — `override` must be a real model of the right `kind`
   (`_valid_override` [:99](../agents/model_router.py#L99)); a wrong-kind id is ignored.
2. **Image step + real media → `PASSTHROUGH`** (`_is_real_media`/`_is_video_source`).
3. **Route by shot type + tier:** `config.routing[kind][shot_type][tier]` → first id
   that exists in `models`.
4. **Fallback** to `config.defaults[kind]`.

**Shot classification** (uses metadata the pipeline already has):
- `cost_tier_from_quality`: `dev|draft|preview → draft`, else `premium`.
- Image (`_image_shot_type`): `ai_symbolic→object`, else `face`.
- Video (`_video_shot_type`): `lipsync→dialogue`, real→`real`, `ai_symbolic→landscape`,
  hero/index-0→`hero`, else `face`.

**`config/models.json`** holds, per model: `kind`, `backend` (`flux|openai|fal|
kling|higgsfield`), `tier`, `fal_endpoint`, `kling_mode`, `max_concurrent`,
`pricing_key`. `routing` maps `shot_type→tier→[ordered ids]`. **Add a model =
add a `models` entry + list it in `routing`; no code change.**

---

## 6. Visual assignment (in `run_caption.py`)

Stage 3 loop → [run_caption.py:219](../run_caption.py#L219):
- `cost_tier` from quality; per-frame `image_model_override`/`video_model_override`
  default to the global `--image-model`/`--video-model` (unless `auto`).
- `select_model("image", …)` → `mid` (`""` for PASSTHROUGH). Then by `photo_spec`:
  - **real photo pin** → use file if it exists, else AI-portrait fallback;
  - **`ai_portrait`** → `generate_contextual_image` (or **reuse `first_portrait_path`**
    under `--face-lock`);
  - **`ai_symbolic`** → `generate_symbolic_image`;
  - **no source** → contextual fallback.
- Each generation goes through **`_generate_with_sanity_check`**
  ([run_caption.py:29](../run_caption.py#L29)): run → Gate B → up to 2 retries
  (deleting the bad file) → accept last result.

### `image_generator.py` backends
→ [agents/image_generator.py](../agents/image_generator.py)
- `_generate_with_model` dispatches on `backend`: `flux`→`_flux_generate` (fal
  `flux-2-pro`, `sync_mode`), `openai`→`_openai_generate` (`gpt-image-2`, 1024×1536),
  `fal`→`_fal_image_generate` (endpoint from catalog).
- **`_generate_image(model, prompt, out, fallback)`** wraps with a fallback model
  (default `gpt_image`) so one flaky provider never breaks a render.
- **Disk reuse:** `ai_portrait_<fid>.jpg` / `ai_symbolic_<fid>.jpg` written into the
  **asset folder**; reused if present and ≥ 50 KB (`_image_cached`). This is why the
  derived-marker filter in the parser matters.

### `safety.py`
→ [agents/safety.py](../agents/safety.py)
- **Gate A `moderate_script`** — OpenAI Moderation on first 4 000 chars; raises
  `ValueError` if flagged; **non-blocking** if the API errors.
- **Gate B `check_face_sanity`** — file exists & > 10 KB; PIL-openable & portrait
  (h > w); optional OpenCV Haar cascade rejects **> 3 faces** (deformed). No-face is
  *not* a failure (symbolic frames). cv2 absent → PIL checks only.

---

## 7. `lipsync_coordinator.py` — talking faces

**Entry:** `run_lipsync_pass(frames, temp_dir, default_voice_id) -> frames`
→ [agents/lipsync_coordinator.py:223](../agents/lipsync_coordinator.py#L223)

Two parallel phases (submit, then poll), mirroring the clip builder.

**Per frame `_submit_one`** ([:93](../agents/lipsync_coordinator.py#L93)):
1. Guard: needs caption + existing visual + a voice_id, else clear `lipsync`.
2. **Audio** via ElevenLabs (`tts_generator.generate_single_tts`), cached by
   `MD5(caption, voice_id)` in `~/.hob_cache/lipsync_audio/`.
3. **Duration flip:** `frame["duration"] = audio_dur` — audio now drives timing.
4. **Clip cache** by `MD5(media bytes + audio bytes)` in `~/.hob_cache/lipsync_clips/`.
5. **Upload** media+audio to **Higgsfield CDN** (`_upload_for_lipsync`).
6. **Vendor route:** video source + `SYNCLABS_API_KEY` → SyncLabs; else
   `HEDRA_API_KEY` → Hedra; else clear `lipsync`.

**`_poll_one`** ([:192](../agents/lipsync_coordinator.py#L192)) downloads, caches,
sets `lipsync_clip_path`. **Any failure clears `lipsync`** → the frame falls through
to normal animation (Ken Burns). The finished clip later bypasses animation via the
`clip_ready` path in the clip builder.

CLI `--lipsync` ([run_caption.py:287](../run_caption.py#L287)) auto-flags all
video-source frames; `[lipsync: yes]` flags any single frame.

---

## 8. `clip_builder.py` — animation engine

**Entry:** `build_clips(assignments, temp_dir, w, h, fps, force_5s, kling_mode, provider)`
→ [agents/clip_builder.py:517](../agents/clip_builder.py#L517)

**Two-phase, per the provider-parallel-limit problem:**

- **Phase 1 `_build_one_clip`** ([:418](../agents/clip_builder.py#L418)) — runs for
  every assignment, classifies and *defers* AI jobs rather than submitting inline:
  - HEIC→JPEG (`sips`); `prepare_image` fixes EXIF + **face-aware portrait crop**
    (OpenCV largest-face center; blind center-crop fallback) ([:124](../agents/clip_builder.py#L124)).
  - **`clip_ready` bypass** — a finished lip-sync clip is copied straight through.
  - Resolve `model_id` (router value or legacy provider via `_resolve_model_id`),
    look up `backend`. **Clip cache** check first (`_model_cache_key`, namespaced per
    model, legacy keys preserved for kling/higgsfield so paid clips still hit).
  - Backend `higgsfield`/`fal`/`kling` → stash `_*_deferred` fields, return `pending`.
  - No model → **Ken Burns** immediately; raw video → **`_video_trim`**.
- **Phase 2** ([:539](../agents/clip_builder.py#L539)) — `poll_one` in a
  `ThreadPoolExecutor` capped at `min(max_concurrent)` of the models in flight:
  - **Submit happens inside the capped pool**, with retry-on-limit (Kling 429/1303 →
    wait 15 s ×8; Higgsfield "concurrent" → wait 20 s ×6) instead of dropping to Ken Burns.
  - Poll → download → length-fix (`_extend_clip` freeze-frame for 5s-only models;
    `_video_trim`+extend for Dev 5s cap) → store in clip cache.
  - **Any exception → Ken Burns fallback** for that frame only.
  - Order restored at the end by `segment_id`.

**Kling specifics:** JWT auth (`_kling_jwt`, HS256, 30-min exp); base64 image;
`_kling_camera_control` maps plain-English motion → Kling structured `camera_control`
(zoom/vertical/tilt/roll axes + named turn moves), with a **no-camera retry on 4xx**
(not billed) so a rejected camera move still renders via the text prompt.

**Raw video `_video_trim`** ([:191](../agents/clip_builder.py#L191)): `-ss start_sec`,
trims to `min(duration, available)`, and **freeze-extends** if the source is shorter
than the frame (roadmap #4a). Scale-to-fill + center-crop to target, audio dropped.

---

## 9. `caption_writer.py` + `assembler.py`

- **Captions:** `generate_frame_srt(frames, srt_path)` writes an **ASS** file timed to
  cumulative frame durations (Baskerville Italic, bottom-center per GUIDE). Assembler
  prefers `.ass` over `.srt`.
- **`assemble_caption_only`** ([agents/assembler.py:296](../agents/assembler.py#L296)):
  - **Normalize every clip** (`_normalize_clip` [:65](../agents/assembler.py#L65)) to one
    resolution / pixel-format / **color range (tv)** / fps / SAR — prevents xfade frame
    drops from `pc`-range JPEGs and resolution-mismatch crashes (e.g. Higgsfield 768×1168).
    Target = largest-area clip.
  - Transition: `crossfade` chains `xfade` with running offsets
    (`offset += dur - 0.4`); `none` → `_concat_clips_hard` (normalize + stream-copy concat).
  - Burn captions via `subtitles=` filter; music looped at **25%**, 3 s fade-out.
  - **Lip-sync mixed mode** (`_assemble_with_lipsync` [:177](../agents/assembler.py#L177)):
    strip embedded audio from lip-sync clips for the video concat, re-extract each
    lip-sync track, position with `adelay` at its timecode, mix at 100%, and **duck
    music to 10% inside lip-sync windows** (25% elsewhere) via a `volume=expr` between() gate.
- `assemble()` ([:364](../agents/assembler.py#L364)) is the **legacy voiceover** path
  (used by `main.py`): concatenated TTS track + music at 15%.

---

## 10. `pricing.py` — cost engine

**Entry:** `estimate(frames, kling_mode, force_5s, music_type, voice_chars, provider,
skip_scene_ai, cost_tier, image_model, video_model) -> dict`
→ [agents/pricing.py:108](../agents/pricing.py#L108)

- Walks `frames[]` and **mirrors the router's actual choices** (`select_model` for
  both image and video) so the estimate matches billing. Categories: scene, images,
  edits, animation (labelled with the set of models used), lipsync + lipsync_audio,
  music, voice.
- `model_cost` ([:92](../agents/pricing.py#L92)) resolves each model's `pricing_key`
  in `config/pricing.json`; **video bills per 5 s block** (`ceil(dur/5)`), image once.
- Lip-sync frames count vendor cost (`synclabs` per-second / `hedra` per-gen) + their
  caption chars as ElevenLabs audio, and **skip animation**.
- `_FALLBACK` dict guards a missing/broken pricing file.

The CLI dry-run ([run_caption.py:131](../run_caption.py#L131)) prints a per-frame plan
plus this breakdown and exits without spending.

---

## 11. `web_app.py` — Flask surface

→ [web_app.py](../web_app.py). Server-rendered UI + JSON/SSE endpoints, per-`run_id`
in-memory state, `_LogCapture` redirecting stdout into an SSE stream.

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | UI shell |
| `/parse-script` | POST | `parse_frame_script` → frame cards + matched-photo count |
| `/preview` , `/preview-result/<run_id>` | POST/GET | generate stills only (fast iteration) |
| `/pricing` , `/models` , `/voices` | GET | expose `pricing.json`, `model_router.catalog()`, ElevenLabs voices |
| `/generate-music` | POST | Suno generation |
| `/run` | POST | kick off `_execute_pipeline` in a thread |
| `/progress/<run_id>` | GET (SSE) | stream the run log |
| `/output/<run_id>` , `/download/<run_id>` | GET | stream / download the MP4 |
| `/media` , `/upload-photo` | GET/POST | serve asset thumbnails, accept uploads |

`_build_frames_from_payload` ([web_app.py:314](../web_app.py#L314)) converts UI frame
cards into the same `frames[]` the parser produces; `_run_inner`
([web_app.py:501](../web_app.py#L501)) reuses the exact router + clip-builder path as the
CLI (the two entry points share the engine).

---

## 12. Caching Summary (invalidation rules)

| Cache | Key | Location | Invalidated by |
|---|---|---|---|
| Animation clips | `MD5(image bytes + motion + duration)`, model-namespaced | `~/.hob_cache/kling_clips/` *(BlobCache; S3-backed when enabled)* | changing image, motion, duration, or model |
| Scene designs | `MD5(caption, note, type, subject, desc, has_photo)` | `~/.hob_cache/scene_designs/` | changing any of those |
| Image descriptions | image content hash | `~/.hob_cache/image_descriptions.db` *(SQLite, WAL)* | file content change (rename safe) |
| Lip-sync clips | `MD5(media bytes + audio bytes)` | `~/.hob_cache/lipsync_clips/` *(BlobCache; S3-backed)* | media or audio change |
| Lip-sync audio | `MD5(caption + voice_id)` | `~/.hob_cache/lipsync_audio/` *(BlobCache; S3-backed)* | caption or voice change |
| Generated stills | filename `ai_*_<fid>.jpg`, ≥ 50 KB | the **asset folder** | delete file or size < 50 KB |

**Storage backends** (P1/P2 shipped): small text caches use `agents/_kv.py`
(SQLite, per-key writes under WAL — replaces the raced whole-file JSON). Blob
caches (clips, lip-sync clips/audio) go through `agents/cache_store.py`
`BlobCache`: local FS by default, optional S3 read-through via
`HOB_CACHE_BACKEND=s3` (+ `HOB_CACHE_S3_BUCKET`) so paid artifacts survive
container redeploys. Object keys are the same content hashes, so switching
backends preserves cache identity. Scene designs remain per-file JSON (cheap to
regenerate — deliberately not migrated).

---

## 13. Concurrency & Failure Matrix

| Stage | Pool | Cap | On failure |
|---|---|---|---|
| Scene design | ThreadPool | 10 | generic fallback scene (no raise) |
| Image gen | serial per frame | — | fallback model → Gate B retry ×2 → accept last |
| Lip-sync | ThreadPool ×2 phases | 6 submit / N poll | clear `lipsync` → animate normally |
| Clip build | ThreadPool | `min(model max_concurrent)` | retry-on-limit; else Ken Burns for that frame |
| Assembly | single ffmpeg | — | raises (whole render fails; temp kept) |

**Whole-pipeline failure** ([run_caption.py:343](../run_caption.py#L343)) preserves the
temp dir for debugging; success cleans it unless `--keep-temp`.

---

## 14. Extension Recipes

- **Add a video/image model:** add a `models` entry (with `backend`, `pricing_key`,
  `fal_endpoint` if fal-hosted, `max_concurrent`) in `config/models.json`, list its id
  under the relevant `routing[kind][shot_type][tier]`, add its price to
  `config/pricing.json`. No Python change for fal-hosted models.
- **Swap the reasoning/vision LLM:** set `LLM_PROVIDER` (and keys) or edit
  `config/llm.json`. Callers are untouched.
- **New annotation:** add a regex in `_parse_format_b`, a clean-up `re.sub`, and a key
  on the frame dict; consume it in the relevant stage.
- **New shot type:** extend `_image_shot_type`/`_video_shot_type` and add a `routing`
  entry for it.

---

## 15. Known Sharp Edges (read before editing)

- Generated stills land in the **user's asset folder**; the parser's `_DERIVED_MARKERS`
  filter is what stops them from corrupting positional auto-match. Touch both together.
- `pricing.estimate` must keep mirroring `model_router.select_model` exactly, or the
  quoted cost diverges from the billed cost.
- Clip-cache keys for `kling`/`higgsfield` are **deliberately legacy-formatted**
  ([clip_builder.py:59](../agents/clip_builder.py#L59)) so previously-paid clips still
  hit — do not "clean up" those token formats.
- Lip-sync uploads user media to an **external CDN**; treat as a privacy boundary.
- macOS assumptions: `sips` (HEIC), Baskerville font dir. Cloud/Linux needs alternatives.
- `main.py` is the **legacy** voiceover pipeline; the maintained path is
  `run_caption.py` / `web_app.py`. Don't confuse the two `build_clips`/`assemble` callers.
</content>
