# Remotion Caption/Overlay Layer — Plan

**Created:** 2026-07-02
**Status:** planned — spike first, then phased integration
**Why:** the six-dimension head-to-head review (Surbhi story) showed our biggest
remaining *craft* gap is typographic: the winning reference used animated hero
lines, word-level emphasis and typographic hierarchy. libass (our caption burner)
tops out at static styled text. Remotion (React → video, rendered headless)
does kinetic typography natively.

## 1. Locked decisions

1. **Overlay track, not a pipeline fork.** Remotion renders a *transparent*
   caption/motion-graphics video (ProRes 4444 alpha or VP9 alpha) from the SAME
   caption + timecode data `caption_writer` already receives
   (`assembler.frame_timecodes` — rule 9). ffmpeg composites it over the
   assembled reel in one extra pass. Nothing upstream changes.
2. **New pluggable seam:** `config/captions.json` → `{"engine": "libass" | "remotion", ...}`
   (same pattern as `config/music.json`). Default stays `libass`.
3. **Graceful degradation (rule 4):** ANY Remotion failure (node missing, Chromium
   crash, timeout) falls back to the libass path silently — a render must never
   die because of the overlay layer. The fallback is logged + surfaced like the
   audio warning (no silent quality downgrades — the Suno lesson).
4. **LLM emits PROPS, never code.** We hand-write a small vetted template library;
   the LLM's only job is a JSON props payload (which line is the hero line, which
   words get emphasis, colors, timing). No generated React/TS is ever executed.
5. **Prod-tier by default.** Overlay rendering ≈ real-time (headless Chromium);
   dev-tier renders keep libass so the cheap loop stays fast. Operator can force
   either via the caption settings.
6. **Cache like everything paid/slow:** overlay renders are content-hash cached
   (`BlobCache("remotion_overlays", ext=".mov")`) keyed on (props JSON, template
   version, W×H, duration).

## 2. Template library (phase 1 set)

| Template | What it does | Props |
|---|---|---|
| `hero-line` | One line scales in with a soft reveal, holds, fades — the "I got selected…" treatment | text, start, end, accent color |
| `story-line` | Standard 1–2 line serif caption, word-group fade-in | text, start, end, position |
| `word-highlight` | Karaoke-style word emphasis on chosen words | text, emphasis[], timings |
| `end-card` | Brand/IP end card with logo + handle | ip id, duration |

Which caption gets which template: a `fast`-tier LLM pass over the script marks
at most 1–2 hero lines per reel (the emotional peaks) + emphasis words; everything
else is `story-line`. Deterministic JSON schema output; cached.

## 3. Architecture

```
caption data + timecodes ──► remotion_overlay.py ──node──► render (templates/, props.json)
                                    │                            │
                                    │   transparent overlay.mov ◄┘
                                    ▼
assembled reel ── ffmpeg overlay ──► final reel     (fallback: libass .ass burn as today)
```

- `tools/remotion-captions/` — the Node/Remotion project (templates, package.json).
  Kept OUT of the Python package; talked to only via CLI (`npx remotion render`).
- `agents/remotion_overlay.py` — the seam: `render_overlay(captions, timecodes,
  w, h, props) -> path | None` (None → caller falls back to libass).
- `agents/assembler.py` — where `_subtitle_filter` is applied today, branch:
  overlay file present → `overlay` filter; else subtitles filter (unchanged).
- Dockerfile: Node 20 + Chromium deps layer (~1 GB — accepted cost, prod image only
  if we split images later; SCALE_PLAN concern, not this feature's).

## 4. Phases

- **P0 — Spike (this branch): ✅ DONE 2026-07-02.** `tools/remotion-captions` scaffolded
  (hero-line + story-line templates, zod props schema, duration from props via
  `calculateMetadata`); 10s transparent overlay rendered and ffmpeg-composited over the
  Surbhi reel — serif story lines + gold-accent hero line verified on real footage.
  **Render-command gotchas (P1 must use these):**
  `npx remotion render src/index.ts CaptionOverlay out/overlay.mov --codec=prores
  --prores-profile=4444 --pixel-format=yuva444p10le --image-format=png --muted
  --props=props.json` — WITHOUT the explicit `--pixel-format` + PNG image format the
  ProRes comes out `yuv422p12le` (NO alpha) and composites as opaque black.
  Composite: `[base][ov]overlay=0:0:shortest=1`. ~10s overlay rendered in ~45s locally.
- **P1 — Seam + fallback:** `config/captions.json`, `agents/remotion_overlay.py`,
  assembler branch + cache + failure surfacing; UI: caption style gets an
  "Animated (Remotion)" engine option. Docs per rule 11.
- **P2 — Hero-line intelligence:** the LLM pass that picks hero lines/emphasis;
  end-card template wired to the IP/watermark config.

## 5. Non-goals

- LLM-generated Remotion code (safety: arbitrary code execution — never).
- Replacing libass (it stays the default + the fallback forever).
- Motion-graphics scenes/transitions between shots (separate discussion; this is
  the caption/overlay layer only).
- Dev-tier animated captions (cost/latency — dev loop stays fast).

## 6. Risks

| Risk | Mitigation |
|---|---|
| Node/Chromium missing on host | `render_overlay` returns None → libass fallback + a visible warning |
| Render time blows up on long reels | per-reel overlay is one pass; cache; prod-only default |
| Docker image bloat | isolate in a build stage; revisit under SCALE_PLAN if image split needed |
| Font licensing | Baskerville is macOS-system; bundle Libre Baskerville (OFL) in the template |
