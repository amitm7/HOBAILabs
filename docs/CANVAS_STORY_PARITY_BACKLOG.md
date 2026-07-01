# Canvas ↔ Story parity — control delta + mismatch handling

> Planning artifact (no code). Two lists: **A.** controls Story mode has that the Canvas
> lacks, each as a todo with effort; **B.** how to handle a frame whose suggested content
> doesn't match the image it chose/created. Date: 2026-07-01.
>
> **Key leverage:** the canvas RENDER path already honours `caption_style`, per-frame
> `caption_position`/`caption_max_lines`, and `orientation` (`_canvas_render_data` +
> `_build_frames_from_payload`). So most caption/format items are **UI-only** — wire a
> control to a field the engine already reads. Marked ⚙️UI-only vs 🔧plumbing.

## A. Control delta (Story has → Canvas lacks)

### A1. Caption styling suite — the headline gap ⚙️UI-only (render already supports)
Story exposes a full caption panel; the Canvas burns a fixed default (`Montserrat/24/bottom`).
| # | Control | Story id | Canvas today | Effort |
|---|---|---|---|:--:|
| 1 | Caption **on/off** | `caption-enabled` | always on | S |
| 2 | Caption **position** (top/mid/bottom) | `caption-position` | fixed bottom | S |
| 3 | Caption **font** (Montserrat/Satoshi/Baskerville/Arial) | `caption-font` | fixed Montserrat | S |
| 4 | Caption **size** (pt) | `caption-size` | fixed 24 | S |
| 5 | Caption **color** | `caption-color` | fixed | S |
| 6 | **Max lines** per caption | `caption-max-lines` | unset | S |
| 7 | **Per-shot** caption position override | frame `caption_position` | none | S |
→ **Plan:** one "Captions" bar in the canvas (mirror Story's panel) writing into the
`caption_style` the render already consumes + a per-card position override. ~½ day, no new plumbing.

### A2. Per-shot frame controls Story has, Canvas lacks
| # | Control | Story | Canvas today | Effort | Notes |
|---|---|---|---|:--:|---|
| 8 | **Per-shot duration** edit + **redistribute** to target | duration input + `redistribute-btn` | shows duration, can't edit | M | Canvas has a global length selector, not per-shot |
| 9 | **Structured camera dropdown** (CAMERA_MOVES vocab) | `camera-select` | free-text `motion_override` | S | Canvas is free-text; dropdown = fewer typos + valid tokens |
| 10 | **Director note** field per shot | `director-note-row` | not on card (image_prompt only) | S | Minor — image_prompt overlaps |
| 11 | **Add image in frame** (per-frame upload) | per-frame Upload | ✅ **HAVE** (Replace: 📎 Real / 🎭 AI face / 🖼 Pick) | — | Already covered; arguably richer than Story |

### A3. Global / format controls Story has, Canvas lacks
| # | Control | Story id | Canvas today | Effort | Notes |
|---|---|---|---|:--:|---|
| 12 | **Orientation** (9:16 / 16:9 / 1:1) | `orientation` | fixed 9:16 | S ⚙️ | render takes `orientation`; UI-only |
| 13 | **Image / video model** picker + **kling mode** | `image-model`,`video-model`,`kling-mode` | `auto` only | M | power-user control; auto is a fine default |
| 14 | **Transition** style | `transition` | beat-aware auto | S | canvas' auto is arguably better; expose for control |
| 15 | **Watermark / IP** | `ip` | none | S | brand/IP overlay on the reel |
| 16 | **Multi-shot coverage** toggle | `multi-shot` | none surfaced | M | more angles per beat |
| 17 | **Social posting kit** (caption seed + cover frame) | `posting-*` | none | M | delivery extra |
| 18 | **Live vendor balances** panel | `balances-*` | cost banner only | S | nice-to-have |

**Suggested priority (parity value ÷ effort):** A1 (captions) → #9 camera dropdown → #12
orientation → #8 per-shot duration → #13 model picker → #15 IP → the rest.

## B. Frame ↔ image mismatch — what & how to handle

**Symptom:** a shot's suggested beat/caption doesn't match the image it auto-matched (wrong
real photo) or AI-generated (wrong content).

### B1. What ALREADY handles it (built this session)
- **Wrong auto-matched real photo →** `🖼 Pick` (gallery of your folder, 1-click swap) or
  `📎 Real` (upload the right one). Fixed the root cause too: the matcher now reads what the
  shot *depicts* (scene description), not just the caption.
- **Wrong AI-generated image →** `↻ re-roll` (regenerate), or edit the **Image prompt** on
  the card then re-roll, or `🤖 AI` / `🎭 AI face` to change the source, or `🎬 Re-create`
  (ambient).

### B2. Gaps worth adding (the todo)
| # | Gap | Proposed handling | Effort |
|---|---|---|:--:|
| B-1 | Re-roll of an AI shot reuses the SAME prompt → can mismatch again | On re-roll, **regenerate the image_prompt** from the (edited) caption first, so the new image tracks the beat — not just a reseed | S |
| B-2 | No **per-shot re-match** | "Re-match this shot" = run `smart_match` for one frame against the folder (pick the best-fitting photo automatically) instead of manual Pick | S |
| B-3 | Operator can't see **why** it matched | Show the matched image's one-line description on hover (we already generate it in `describe_images`) so a mismatch is explainable | S |
| B-4 | Mismatch is only caught by eye | Optional **match-confidence flag**: a cheap vision check "does this image fit this beat?" → flag low-confidence shots for review (like ⚡ fidelity-suggest, but for content-fit) | M |

**Cheapest high-value pair:** B-1 (re-roll re-derives the prompt) + B-2 (per-shot re-match).
Together they turn "the image is wrong" into a one-click fix for both AI and real shots.

## Non-goals for now
- Don't fork any of this — every item writes into fields the shared engine already reads
  (`caption_style`, `orientation`, `photo_spec`, `image_prompt`); it's UI surfacing, not a
  second pipeline (build-feature rule #1).
