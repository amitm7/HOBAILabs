# HOBAILabs — High-Level Design (HLD)

**Product:** AI story-to-reel pipeline (Instagram Reels / YouTube Shorts)
**Revision:** 2026-06-11 · derived from the `feat/pipeline-expansion-roadmap` branch
**Companion docs:** [LLD.md](LLD.md) — module-level internals · [SCALE_PLAN.md](SCALE_PLAN.md) — multi-user scale phasing
**Source of truth for behaviour:** [GUIDE.md](../GUIDE.md) (user view) · [ROADMAP.md](../ROADMAP.md) (forward plan)

---

## 1. Purpose & Scope

HOBAILabs turns a **plain-text story script + a folder of photos/videos** into a
finished, captioned, scored **9:16 MP4**. A film-director "brain" (LLM) decides,
per story beat, what is on screen, how the camera moves, and the emotional tone;
the pipeline then generates any missing visuals, animates stills into motion,
optionally makes a face speak (lip-sync), burns captions, mixes music, and
assembles the reel.

The defining product principle is **realism preservation + test-cheap/finish-expensive**:
real user media is never AI-regenerated, and a per-shot router runs cheap models
in Dev and premium models in Production.

**In scope (today):** single-story render via Web UI or CLI; per-shot model
routing; AI image gen; image→video animation across 4 video providers; lip-sync;
content/quality safety gates; cost estimation; aggressive caching; multi-shot B-roll
coverage; voiceover mode with frame-exact audio synchronization; secure folder uploads.

**Out of scope (today):** multi-tenant accounts, persistent job DB, batch/queue
production, beat-synced cuts, multi-platform export, CLIP-based scoring — these
are on the roadmap (§9).

---

## 2. System Context (C4 Level 1)

```
                         ┌──────────────────────────────────────────┐
                         │              HOBAILabs                    │
   ┌─────────┐  script + │                                          │  ┌──────────────┐
   │ Creator │  assets   │   Web UI (Flask)  ──┐                    │  │  LLM brain   │
   │ (browser│──────────►│                     ├─► Render Pipeline ─┼─►│ OpenAI /     │
   │  / CLI) │           │   CLI (run_caption) ─┘   (agents/*)      │  │ Bedrock /    │
   └─────────┘           │                                          │  │ Gemini       │
        ▲                │   Config: models.json, pricing.json,     │  └──────────────┘
        │  9:16 MP4      │           llm.json                       │  ┌──────────────┐
        └────────────────┤   Cache: ~/.hob_cache/*                  │  │ Image gen    │
                         │                                          │  │ Flux/fal/GPT │
                         └──────────────────────────────────────────┘  ├──────────────┤
                                                                        │ Video: Kling │
   External services (all optional/​degradable):                        │ Higgsfield   │
   · Image: fal.ai (Flux, Seedream, Nano Banana), OpenAI gpt-image      │ fal Seedance │
   · Video: Kling AI, Higgsfield, fal (Seedance/Veo/Hailuo)             │ Veo / Hailuo │
   · Lip-sync: Hedra (photo), SyncLabs (video)                          ├──────────────┤
   · Voice: ElevenLabs · Music: Suno · Safety: OpenAI Moderation        │ Voice/Music/ │
   · CDN for lip-sync uploads: Higgsfield CDN                           │ Lipsync APIs │
                                                                        └──────────────┘
```

**Key context properties**

- **Two entry points, one engine.** Web UI (`web_app.py`) and CLI (`run_caption.py`)
  both build the same `frames[]` data model and call the same `agents/*` stages.
- **Vendor-pluggable on three independent axes:** reasoning/vision LLM
  (`config/llm.json`), image model, video model (`config/models.json`). Swapping a
  vendor is a config edit, not a code change.
- **Graceful degradation everywhere.** Any external failure falls back to a cheaper
  path (premium model → fallback model → Kling → Ken Burns; lip-sync → animated
  still; smart-match → positional). A render rarely hard-fails.

---

## 3. Containers / Runtime Pieces (C4 Level 2)

| Container | Tech | Responsibility |
|---|---|---|
| **Web UI** | Flask + server-rendered templates + SSE | Parse script, preview stills, estimate cost, stream progress logs, serve/​download output. Per-`run_id` in-memory state. |
| **CLI** | `argparse` + `run_caption.py` | Headless render; dry-run cost plan; all the same flags as the UI exposes. |
| **Render pipeline** | `agents/*` Python modules | The actual work: parse → scene-design → assign visuals → edit → lip-sync → animate → caption → assemble. |
| **Model router** | `agents/model_router.py` + `config/models.json` | Pure logic: maps each shot to a model id given shot type + cost tier + overrides. |
| **LLM brain** | `agents/llm.py` + `config/llm.json` | Single entry point for every reasoning/vision call; OpenAI / Bedrock / Gemini backends. |
| **Cost engine** | `agents/pricing.py` + `config/pricing.json` | Single source of cost figures; whole-pipeline estimate. |
| **Caches** | `~/.hob_cache/*` (filesystem) | Clips, scene designs, image descriptions, lip-sync clips+audio, generated stills (in the asset folder). |

No database, no message broker, no auth layer today. State for a web render lives
in process memory keyed by `run_id`; durable artifacts are files on disk and the
content-hash caches.

---

## 4. The Core Data Model — the `frame` dict

Everything flows through a list of mutable `frame` dicts. Each stage reads some
keys and writes others. This is the single most important abstraction in the
system.

```
frame = {
  # ── identity / text (from script_parser) ──
  "frame_id": "f03",            "caption": "Bedridden, I lost my hair…",
  "duration": 7.5,             "director_note": "...",

  # ── source selection (parser → matcher → pipeline) ──
  "photo_spec": "06_family.jpg" | "ai_portrait" | "ai_symbolic" | "",
  "visual_path": "/abs/path.jpg",        # resolved real/generated still or video
  "image_model_override": "", "video_model_override": "",
  "video_start_sec": 0.0, "video_end_sec": None,

  # ── direction (scene_intelligence) ──
  "scene": { "emotion", "scene_description", "image_prompt",
             "motion_prompt", "camera_angle" },
  "motion_override": "crane up",         # [camera:]/[motion:] beats GPT
  "edit_prompt": "add frost on the window",

  # ── lip-sync (lipsync_coordinator) ──
  "lipsync": True, "voice_override": "<11labs id>",
  "lipsync_clip_path": "/tmp/.../lipsync_f03.mp4",   # finished, bypasses animation
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
 1. parse_frame_script()            Format A/B → frames[]; auto-duration; sort-order match
        │                           (opt-in smart_match: LLM reads photo content → assigns)
        ▼
 2. design_all_scenes()             LLM director per frame → emotion + image_prompt + motion
        │                           (parallel · disk-cached by MD5 of inputs)
        ▼
 3. visual assignment (per frame)   router.select_model("image", …):
        │   ├─ real photo/video  → PASSTHROUGH (kept as-is — realism)
        │   ├─ ai_portrait       → generate_contextual_image()  [+Gate B sanity, ≤2 retries]
        │   ├─ ai_symbolic       → generate_symbolic_image()    [+Gate B]
        │   └─ face-lock: reuse first portrait still across present-day frames
        ▼
 3b. edit pass                      edit_image() between gen and animation (opt-in, per frame)
        ▼
 3c. lip-sync pass                  run_lipsync_pass(): 11Labs audio → CDN upload →
        │                           Hedra (photo) / SyncLabs (video); duration flips to audio
        ▼
 4. build assignments[]             motion = override or scene.motion_prompt;
        │                           model_id = router.select_model("video", …)
        ▼
 5. build_clips()                   per shot: cache → Ken Burns | Kling | Higgsfield | fal
        │                           (deferred submit + capped parallel poll; clip cache reuse;
        │                            short raw video → freeze-frame extend; lip-sync clip_ready bypass)
        ▼
 6. generate_frame_srt()            ASS captions timed to frame durations
        ▼
 7. generate_voiceover_track (opt)      ElevenLabs/OpenAI TTS; frame-exact padding/trim
        │                           per segment; concatenate aligned to total duration
        ▼
 8. assemble_caption_only()             normalize → xfade/hard-cut concat → captions burn →
        │                           music (25%, duck to 10% under lip-sync) → voiceover (if is_voiceover)
        │                           → 9:16 MP4
        ▼
 OUTPUT .mp4
```

**Dry-run** short-circuits after stage 2: it prints the per-frame plan and a
`pricing.estimate()` breakdown, spending nothing.

---

## 6. Key Architectural Decisions & Rationale

| Decision | Why | Where |
|---|---|---|
| **Per-shot model router, not one global provider** | Different shots want different models; cost discipline (cheap in Dev) should be automatic, not manual. | `model_router.py`, `config/models.json` |
| **Real media is `PASSTHROUGH` at the image step** | Preserving the real subject is the product's biggest realism advantage; AI regen would destroy it. | `model_router._is_real_media` |
| **LLM behind one `chat()` brain** | Decouples reasoning/vision from a vendor; enables credit-funded Bedrock without touching callers. | `agents/llm.py` |
| **Config-driven catalog + pricing** | Vendors change slugs/prices monthly; edits should be JSON, not Python. | `config/models.json`, `config/pricing.json` |
| **Content-hash caches at every expensive step** | Re-renders of the same story must not re-spend credits. | `~/.hob_cache/*` |
| **Deferred submit + capped parallel poll** | Providers have hard parallel limits (Kling 4, Veo 2); over-submitting caused 429s and silent Ken Burns drops. | `clip_builder.build_clips` |
| **Two distinct safety gates** | Text moderation (Gate A) cannot catch deformed faces (Gate B); they are different problems. | `agents/safety.py` |
| **Everything degrades, never crashes** | Creators lose trust on a hard failure far more than on a cheaper fallback. | every stage's `try/except` |
| **Audio drives lip-sync duration** | A talking face must last exactly as long as the spoken line. | `lipsync_coordinator` duration flip |
| **Voiceover mode with frame-exact sync** | Spoken segments padded/trimmed to match each frame's duration so voice aligns to captions. | `tts_generator._fit_seg()`, `assembler is_voiceover` |
| **Multi-shot B-roll coverage** | Spare images assigned to frames via LLM vision; frames split into sub-shots with gentle motions. | `agents/coverage.py` |
| **Secure media serving** | `/media` endpoint validates all paths against allowed roots to prevent traversal attacks. | `web_app._path_allowed()` |

---

## 7. Cross-Cutting Concerns

- **Cost control.** Single `pricing.estimate()` walks `frames[]` and mirrors the
  router's actual model choices, so the UI/CLI estimate matches what will be billed.
  Dev tier caps clips at 5s; Ken Burns and real-footage frames are free to animate.
- **Caching.** Clip cache keyed by `MD5(image bytes + motion + duration)`, namespaced
  per model so switching models can't return the wrong clip; legacy Kling/Higgsfield
  keys preserved so previously-paid clips still hit. Scene designs, image
  descriptions, lip-sync clips/audio each have their own content-hash cache.
- **Concurrency.** I/O-bound stages (scene design, image gen, clip build, lip-sync)
  run in thread pools, throttled to the strictest `max_concurrent` among models in
  flight. Wall-clock for ~10 frames drops from minutes to seconds where caches hit.
- **Failure isolation.** A per-frame failure falls back for that frame only; the
  render continues. Temp dir is preserved on whole-pipeline failure for debugging.
- **Security/secrets.** All API keys in `.env`, loaded with `load_dotenv(override=True)`;
  never committed. Lip-sync uploads transit the Higgsfield CDN (external — see §8 risk).

---

## 8. Risks & Constraints

| Risk / Constraint | Impact | Mitigation (today) |
|---|---|---|
| fal.ai endpoint slugs + prices marked `VERIFY` | A Production render could hit a dead/​mis-priced endpoint | Dev is safe (Kling video; fal image falls back to gpt-image); GUIDE warns to verify before paid runs |
| Provider parallel limits (Kling 4, Veo 2, Higgsfield 4) | 429s, dropped frames | Capped pool + retry-on-limit instead of fallback |
| Lip-sync media uploaded to external CDN | Private user media leaves the box; may be cached/​indexed | Documented; opt-in feature; consider self-hosted signed URLs (roadmap) |
| In-memory web run state (no DB) | A server restart loses in-flight runs; no horizontal scale | Acceptable for single-operator use; durable job store is future work |
| Suno via third-party wrapper (`api.sunoapi.org`) | Unofficial, may break | Music is optional; isolated in `music_generator` |
| macOS-specific bits (`sips` HEIC, Baskerville font path) | Reduced portability off macOS | Dockerfile exists; fonts/HEIC need a Linux path for cloud deploy |
| No automated tests in repo | Regressions land silently | Router/pricing are pure functions — prime candidates for unit tests (recommended) |

---

## 9. Roadmap Alignment (where this is heading)

From [ROADMAP.md](../ROADMAP.md), mapped to the architecture above:

- **P0 — Pipeline hardening, face consistency, safety gates.** Largely landed:
  Gate A/B in `safety.py`, `--face-lock` V1 fallback in `run_caption.py`, raw-video
  freeze-extend + face-aware crop in `clip_builder.py`.
- **P1 — Raw video correctness, image edits, pricing+estimator.** Landed:
  `video_start_sec`, `edit_prompt`, `pricing.py`/`config/pricing.json`, dry-run.
- **P2 — Lip-sync (done), multi-shot B-roll coverage (done: LLM vision + duration split),
  voiceover mode (done: frame-exact TTS padding), smart coverage (video matching + prefer
  real footage), multi-platform export, beat-synced cuts (pending).**
- **P3 — CLIP score-based matching, visual enhancement.** Pending;
  CLIP is the planned path for top-K multi-shot scoring on AWS GPU.

The two biggest **architectural** gaps before multi-user scale: (1) a durable job
store + queue to replace in-memory `run_id` state, and (2) replacing the external
lip-sync CDN with first-party hosted signed URLs.

---

## 10. Glossary

- **Frame / beat** — one shot in the reel; one `frame` dict.
- **Shot type** — router classification (`face`, `object`, `real`, `landscape`,
  `hero`, `dialogue`) that selects a model.
- **Cost tier** — `draft` (Dev, cheap models) vs `premium` (Production).
- **PASSTHROUGH** — sentinel meaning "real media, skip image generation."
- **Gate A / Gate B** — script moderation vs generated-image sanity check.
- **clip_ready** — a frame whose lip-sync clip is already finished and bypasses animation.
</content>
</invoke>
