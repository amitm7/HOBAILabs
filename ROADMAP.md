# HOBAILabs AI Reel Platform — Product Roadmap

**Last revised:** 2026-06-09  
**Stack:** Python + FFmpeg + Kling AI + OpenAI + ElevenLabs + Suno AI  
**Current state:** Single-story pipeline working end-to-end (script → Kling clips → ASS captions → assembled Reel). Web UI live at `web_app.py`.  
**Companion backlog:** [docs/PRODUCT_IDEAS.md](docs/PRODUCT_IDEAS.md) — consolidated creative + strategic wishlist (per-frame layout engine, text/callouts, timing, edit surface, story→script intake, multi-language, asset library, Brand B2).

---

## Priority Levels

| Level | Meaning |
|---|---|
| **P0** | Launch blocker — platform does not go live without this |
| **P1** | First sprint after launch — high trust/quality impact |
| **P2** | Next quarter — differentiated features |
| **P3** | Backlog — valuable but not time-sensitive |

---

## P0 — Launch Blockers

### #1 · Pipeline Hardening
**What:** End-to-end reliability for the current single-story flow.  
**Scope:**
- Short-clip extension for raw video inputs (correctness bug — see #4 notes)
- Kling poll timeout + graceful Ken Burns fallback on any API error
- Assembly failure recovery (partial renders don't corrupt output dir)
- Dry-run mode for testing without spending Kling credits

**Done when:** 10 consecutive stories render without manual intervention.

---

### #2 · Face Consistency ← ARCHITECTURE CORRECTED
**The original plan said:** "Pass reference_images in Kling v3 API — 2-day implementation."  
**What's actually true:** `image2video` has no `reference_images` param. Kling's face-reference feature is Element Binding (`elements[]` payload, `character_orientation: "video"`) — it stabilizes a face *within one clip's motion*, but does **not** make Frame 1's Flux face match Frame 5's Flux face. Cross-frame consistency is an **image-generation problem**, not an animation problem. Kling faithfully animates whatever face it's handed.

**The real fix is at the stills layer, before Kling ever sees the image.**

**Age-segmented architecture (keep exactly as designed):**
- `adult_present_day` frames → reference-based generation (needs consistent face)
- `child_memory` frames → style/mood generation (face drift is fine; it's a different era)
- This segmentation is correct and should not change.

**Implementation — fork test, not a checkbox:**

**Test A (the one that actually solves the problem):**  
Generate 3 `adult_present_day` stills from one reference photo using InstantID or Flux-Redux (fal.ai).  
→ Are the faces consistent across all 3 stills?  
→ If yes: proceed to Test B.  
→ If InstantID endpoint is not live or Test A fails: go to Fallback V1 below.

**Test B (secondary lock):**  
Feed two consistent stills into Kling with Element Binding payload.  
→ Does Kling preserve the face during motion, or drift within the clip?

**Explicit V1 Fallback (ship this if Test A fails):**  
Generate the portrait image **once**, reuse the same still across all present-day frames with different Ken Burns / Kling motion prompts. Face is guaranteed consistent because it's literally the same image. Different motion = different feel per frame. This is a principled v1, not a compromise — name it explicitly in UI copy ("uses your reference photo across all scenes").

**Infrastructure note:** Kling Element Binding requires reference image URLs, not base64. You need a hosting step — either a temp S3 upload or a small signed-URL helper. Not zero-vendor; ~1 day of infra work.

**Done when:** Test A passes and same-face stills are generated across 3+ present-day frames, OR V1 Fallback ships with explicit UX language.

---

### #3 · Content Safety — Two Distinct Gates
**The original plan said:** "OpenAI Moderation API for content safety."  
**What's accurate:** OpenAI Moderation is text-tuned. It catches NSFW policy violations, not visual quality failures. Your actual production risk is deformed/uncanny faces — four eyes, melted ears, missing hands — which pass moderation clean.

**Two separate gates, not one:**

**Gate A — Safety floor (keep as planned):**  
OpenAI Moderation API on the script/prompt text before any generation starts. Blocks clearly prohibited content. Fast and cheap.

**Gate B — Quality gate (add this, dovetails with #2):**  
After image generation, before Kling submission:
```python
# face_sanity_check(image_path) → bool
# Uses OpenCV or MediaPipe face detection
# Pass criteria: exactly 1 face detected, not blurry, aspect ratio sane
# Fail → regenerate (max 2 retries) or flag for human review
```
This catches what moderation never will. And since you're already doing face detection work for #2 (InstantID), the face detector is not extra infrastructure — it's the same tool used twice.

**Do not rely on moderation to catch quality failures.** They are different problems.

**Done when:** Both gates are in the generation loop; quality failures trigger retry before reaching assembly.

---

## P1 — First Sprint After Launch

### #4 · Raw Video: Three Correctness Fixes
**Current behavior of `_video_trim` (clip_builder.py:121):**
- Always starts at second 0 (`-ss 0` hardcoded)
- No loop/extend for clips shorter than frame duration — output just comes up short
- Blind center-crop to 9:16 — subject can drift out of frame on landscape videos
- Original audio always dropped (music/VO takes over)

**Three fixes, in priority order:**

**4a — Short-clip extension (correctness bug, should be P0):**  
A video shorter than frame duration currently produces a short clip. Apply the same `_extend_clip` freeze-frame logic already used for images.  
```python
if video_duration < duration:
    _freeze_extend(trimmed_clip, duration, output_path)
```

**4b — Trim controls (user trust):**  
Add `video_start_sec` and `video_end_sec` per-frame fields to the script parser. Default to `0` and frame duration. Replace hardcoded `-ss 0` with these values. Optionally: expose in/out sliders in the web UI.

**4c — Face-aware crop (quality):**  
Replace blind `scale...increase + crop` with MediaPipe face detection → center the crop window on the detected face. Same smart-reframe logic needed for #12 (multi-platform export) — implement once, reuse.

---

### #5 · User Image Editing Prompts (New Feature)
**What the user wants:** Natural-language refinement of any frame's image before Kling animation.  
Examples: "add thunderstorm", "more trees on the left", "make the sky bluer", "evening light".

**Where it fits in the pipeline:**  
Image-gen step → **[optional edit pass]** → Kling animation → assembly  
This is a pre-Kling refinement, not post-processing. You edit the still, then animate the edited result.

**Implementation:**  
Use OpenAI `images.edit` endpoint (gpt-image-1):
```python
# prompt-only edit (no mask required for global changes)
response = openai.images.edit(
    model="gpt-image-1",
    image=open(source_image, "rb"),
    prompt=user_edit_prompt,   # "add thunderstorm in the background"
    size="1024x1536",
    response_format="b64_json",
)
```

**V1 scope:** Prompt-only, no mask. Global edits work well ("add thunderstorm"). Spatial edits ("add trees on the left") work but with less precision — that's acceptable for v1.  
**V2 scope:** Mask-based inpainting for precise spatial edits.

**Script format extension:**  
Add optional `edit:` line per frame:
```
Frame 3
Caption text
edit: add heavy rain and grey storm clouds
[note: present-day scene, somber mood]
```

**Done when:** `edit:` directive is parsed and applied between image-gen and clip-build for any frame that specifies it.

---

### #6 · Pricing Config (pricing.json)
**Promoted from P2.** Bundled with #7 cost estimator.  
**Why it matters:** Kling, Suno, and ElevenLabs prices change. Hardcoded costs in the estimator will silently go wrong and erode client trust precisely when the estimator is most relied upon.

```json
{
  "kling": { "standard_5s_usd": 0.08, "pro_5s_usd": 0.14 },
  "openai_image": { "gpt_image_1_per_image_usd": 0.04 },
  "elevenlabs": { "chars_per_dollar": 25000 },
  "suno": { "song_usd": 0.05 },
  "updated": "2026-06-04"
}
```

File lives at `config/pricing.json`. Estimator reads it at runtime. When a vendor changes prices, update one file — no code change required.

---

### #7 · Cost Estimator
**What:** Before each render, show the user a cost breakdown and get confirmation.  
**Reads from:** `pricing.json` (#6).  
**Inputs:** Number of frames, which frames use Kling vs Ken Burns, clip duration, voice-over length, music generation.  
**Output:**
```
Estimated render cost:
  8 × Kling clips (5s, standard)   $0.64
  1 × GPT image generation         $0.16
  1 × Suno music                   $0.05
  ElevenLabs voice (2,400 chars)   $0.10
  ─────────────────────────────────────
  Total                            $0.95

Proceed? [Y/n]
```

**Done when:** CLI and web UI both show pre-render estimate; render aborts if user declines.

---

## P2 — Next Quarter

### #8 · Lip Sync (Raw Video Path)
**Why this matters for raw video:** Today, a user uploading 30 seconds of themselves talking gets: first N seconds trimmed, muted, captions overlaid. Their voice is thrown away. That's the most valuable authenticity signal in the footage.

**The right path:**  
`raw video (subject talking)` → ElevenLabs voiceover → Hedra / SyncLabs lip-sync → subject appears to narrate their own story in a polished voice.

This is your most differentiated feature. Raw video is the precondition for it. Fix the raw video handling (#4) first so the footage actually reaches this stage properly.

**Vendor options:**
- **Hedra** — highest quality lip sync, portrait-optimized
- **SyncLabs** — good API, faster turnaround
- **Wav2Lip** (self-hosted) — free, lower quality, no vendor dependency

**V1:** SyncLabs API integration. User uploads portrait video + ElevenLabs-generated audio → synced video clip returned.

---

### #9 · Multi-Platform Export + Smart Reframe
**What:** Same story, exported to Instagram Reels (9:16), YouTube Shorts (9:16), LinkedIn (1:1 or 4:5), and horizontal cuts (16:9) with face-aware reframe per format.

**Shares infrastructure with:** #4c (face-aware crop) — implement the face-centering logic once in a `smart_crop(image, target_ratio)` utility and call it from both.

**Platform specs:**
```python
PLATFORM_SPECS = {
    "instagram_reel": {"w": 1080, "h": 1920, "fps": 30},
    "youtube_short":  {"w": 1080, "h": 1920, "fps": 60},
    "linkedin_video": {"w": 1080, "h": 1350, "fps": 30},  # 4:5
    "facebook_reel":  {"w": 1080, "h": 1920, "fps": 30},
}
```

---

### #10 · Beat-Synced Cuts
**What:** Align scene cut points to the nearest beat in the background music. Makes the reel feel professionally edited even with AI-assembled footage.

**Stack:** `librosa` (beat detection) → cut offsets adjusted before assembly.

```python
beat_times = librosa.beat.beat_track(y=y, sr=sr)[1]
# Snap each segment cut to nearest beat_time
```

**Dependency:** Music must be generated (#6 Suno) before assembly, not after. Check ordering in `run_caption.py`.

---

### #11 · Batch Production Mode
**What:** Process multiple stories in one command — read a folder of `story_01.txt`, `story_02.txt`, etc., render all in parallel (respecting Kling's 4-parallel limit), output to numbered subfolders.

**Scope:**
- Queue manager wrapping existing `run_caption.py` logic
- Per-story cost estimation before batch starts
- Resume from checkpoint (skip already-rendered stories)

---

### #15 · Smart Coverage — Video Matching + Multi-Shot  ← NEW
**Context:** Single-image *content* matching already shipped — `agents/image_matcher.py`
uses GPT-4o (via the pluggable `agents/llm.py`) to read names/text in photos and place
the right photo on each beat (opt-in "🤖 Smart-match images"). This phase raises
*coverage* quality: use video as a first-class source and, optionally, cover a beat
with multiple shots.

**Do in priority order — 1 & 2 are high-ROI / low-cost / small; 3 & 4 are bigger and opt-in.**

**1 — Video matching (do first):** Extract 2-3 keyframes per clip (ffmpeg), describe them
via the existing `llm.chat` vision path (cached by file content hash), and match videos
exactly like photos. Makes videos first-class in Smart-match (today they fall back to
positional order). Reuses `describe_images` / `assign_images`.

**2 — Prefer real footage:** When a beat matches a real video clip, anchor on the footage
over an AI-animated still — higher quality *and* lower cost (no animation credits). Implemented
as a bias in the assignment prompt + the pipeline already plays matched videos as footage.

**3 — Multi-shot coverage (opt-in, cost-aware):** Score every (beat, media) pair, take
**top-K above a quality floor**, with **no duplicates across beats** (global assignment),
**gated by availability** (only multi-shot a beat that has ≥2 strong matches). Cover a beat
with e.g. a real clip + 1-2 still B-roll. *Replaces the earlier fixed-"80% match" idea, which
is dropped — a fixed threshold is the wrong mechanism; use continuous CLIP scores + top-K.*

**4 — Editorial polish:** Order sub-shots (wide→close), enforce a min sub-shot duration,
let one caption span the sub-shots, give each sub-shot its own subtle motion. Prevents the
"gallery slideshow" feel.

**Dependencies:** Multi-shot scoring uses CLIP embeddings (credit-funded on AWS GPU — see the
Bedrock/AWS work and P3 #12). **Cost:** ~2× video credits on multi-shot beats → opt-in.
**Priority note:** below P0 #2 (face consistency) and per-shot motion quality — this is polish,
not the core quality lever.
**Status:** steps 1-2 implemented; 3-4 deferred (need CLIP + assembler sub-clip support).

---

## P3 — Backlog

### #12 · CLIP Semantic Media Matching
**Note:** Single-image semantic matching is now partially delivered via the GPT-4o
matcher (#15). CLIP remains the path for cheap, at-scale, *score-based* matching (the
top-K multi-shot selection in #15) — run it credit-funded on AWS GPU.
**What:** Given a script segment's text, use OpenAI CLIP to score semantic similarity against all available images/videos in the asset library. Auto-assign the most relevant asset per frame instead of relying on manual asset-to-frame mapping.

**Replaces:** Current numbered-filename convention (`01_papa_auto.jpg` = Frame 1).  
**Keeps:** Numbered convention as override — if an asset is explicitly numbered, that assignment wins.

---

### #13 · Visual Enhancement Models
**What:** Optional quality pass on AI-generated or low-quality user images before Kling.

| Problem | Tool |
|---|---|
| Dark / flat lighting | Zero-DCE or `ffmpeg eq=brightness=0.1` |
| Low resolution | Real-ESRGAN (2× or 4×) |
| Shaky footage | FFmpeg `vidstabdetect` (two-pass) |

**Rule:** Only apply if scene quality score < 7.0. Never over-process good footage.

---

### #14 · ElevenLabs Voice-Over Mode
Already partially built in `agents/tts_generator.py`. When activated, voice is generated per-frame alongside (or instead of) on-screen captions.  
**Must use:** `eleven_multilingual_v2` model, Rachel voice (`21m00Tcm4TlvDq8ikWAM`), or a cloned voice per brand.  
**Trigger:** `--voiceover` flag on `run_caption.py`.

---

## Known Technical Constraints

| Item | Constraint | Impact |
|---|---|---|
| Kling parallel limit | 4 tasks max (Standard plan) | Frames 5–N fall back to Ken Burns during large renders |
| Kling clip cache | `~/.hob_cache/kling_clips/` keyed on MD5(bytes+prompt+duration) | Portrait-cropping changes cache key — expected |
| Kling Element Binding | Requires image URLs not base64; only 1 element supported; `character_orientation: "video"` required | ~1 day infra for URL hosting; not zero-vendor |
| InstantID / Flux-Redux | fal.ai endpoint availability must be verified before committing P0 #2 path | Fork test A is the gate |
| Suno API | Third-party wrapper at `api.sunoapi.org`; `callBackUrl` required; audio at `data.response.sunoData[0].audioUrl` | Not official Suno — monitor for breakage |
| `.env` | Contains all API keys — never commit | `load_dotenv(override=True)` at top of every script |

---

## What Ships in V1

A story renders correctly when:
- [ ] Face consistent across adult present-day frames (via V1 Fallback or InstantID)
- [ ] Content safety gate clears on script text
- [ ] Quality gate passes on all generated images (1 face, not distorted)
- [ ] Raw video clips start at correct timestamp, extend if short
- [ ] Cost estimate shown and approved before render starts
- [ ] ASS captions render correctly (Baskerville Italic, bottom-center, 38-char wrap)
- [ ] Music at 25% volume, fades last 3 seconds
- [ ] 9:16 portrait output, no black bars
