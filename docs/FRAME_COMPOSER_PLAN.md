# Frame Composer & Language Versions — Plan

**Created:** 2026-07-03 · **Status:** PLAN (red-teamed below, nothing built)
**Owner ask:** (A) per-frame *static image overlays* before Final Cut — speaker insets,
memory/flashback panels, comic-style emotion/thinking devices, sized & placed by the
operator; (B) a *proper* way to make the story in a chosen language.
**Ledger:** S24 (overlays) · S25 (language) in `docs/L99_ARCH_PLAN.md` → tickets **T14** / **T13**.

---

## Part A — Frame Composer (T14): per-shot image overlays

### A1. What it is
A per-shot **Overlays** section in the Shot Inspector. Each overlay = one static image
composited over that shot's clip: a speaker's portrait chip while they talk, a
polaroid-style memory inset during a flashback line, a comic thought-bubble with an
image inside it, or a plain sticker/glyph (💭 thinking, ⚡ emphasis) with no image at all.
Zero model spend — pure PIL + ffmpeg.

### A2. Data model (rule 2: everything flows through the frame dict)
```
frame["overlays"] = [{
  "id": "ov1",
  "kind":  "speaker" | "memory" | "thought" | "sticker",
  "image": "<path>" | "@char:<char_id>"     # @char resolves to that character's portrait
  "style": "chip" | "polaroid" | "rounded" | "bubble",   # PIL pre-styled frame w/ alpha
  "pos":   "tl|tc|tr|ml|mr|bl|bc|br",       # 9-grid minus center (title-safe margins baked in)
  "size":  "s" | "m" | "l",                  # relative to frame width (~18% / 26% / 36%)
  "when":  "all" | "first-half" | "second-half",
}]
```
Relative sizing/9-grid (not freeform x/y) keeps every composite title-safe and
consistent across 9:16 / 16:9 / 1:1 — same spec renders correctly in any orientation.

### A3. Rendering pipeline (no fork)
1. **Style pass (PIL):** overlay image → styled PNG with alpha (rounded mask / white
   polaroid border + slight rotation / bubble with tail pointing toward frame center /
   circular chip with ring). Cached by content-hash(image + style + size).
2. **Composite pass (ffmpeg):** applied **per-clip, before concat** — clip-local time
   (0..dur) so `when` timing never fights crossfade timecode drift (rule 9 avoided
   entirely, by construction). `overlay=…:enable='between(t,a,b)'` per overlay.
   Output cached by hash(clip + overlays-json): **changing an overlay never re-renders
   the base clip** — it re-composites on top of the cached clip (seconds, not minutes).
3. Assembly then consumes the composited clip exactly as today. IP watermark/brand
   post-pass stays on top (unchanged).

### A4. UX
- Inspector → **Overlays**: [+ Add] → source picker (upload / any character's portrait /
  folder asset via existing picker / built-in sticker set) → style preset → 9-grid
  position → S/M/L → when. Max **2 overlays per shot** (soft limit — see red-team).
- **Instant preview:** a `/api/canvas/<id>/overlay-preview` endpoint composites the
  overlay onto the shot's still (PIL only, free) and returns the image — review-first,
  before any clip pass.
- **One-click speaker chips:** board-level toggle "🗣 Show speaker on dialogue shots" —
  auto-adds a `kind=speaker, image=@char:<speaker_id>` chip to every non-narrator
  spoken shot (pairs with T4 per-character voices: hear them AND see them).
- v2 (after T6 Remotion lands): same overlay schema optionally rendered by a Remotion
  template instead of ffmpeg — animated pop-in bubbles, breathing chips. Schema is
  designed once; the renderer is a seam (`static` now, `animated` later).

### A5. Governance
Overlay images are operator-supplied or already-governed assets (character portraits,
matched folder media) — existing real-media/likeness rules apply unchanged. No AI
copywriting involved (comic devices are images/glyphs, not generated claims).

### A6. Tickets
- **T14a** (M): schema + PIL style pass + per-clip composite + cache + degradation-ledger
  entry on composite failure (falls back to the un-overlaid clip, `warn`).
- **T14b** (S-M): Inspector UI + still preview endpoint + speaker-chip auto toggle.
- **T14c** (S, after T6): Remotion animated renderer behind the same schema.

---

## Part B — Language Versions (T13): the story in the operator's language

### B1. Principle
**Author once (English internals), review translated, render per language.** Image
prompts stay English (generation models are strongest there); captions + voiceover are
the language-bearing layer — and they're cheap to re-do, which makes language versions
a *repurposing multiplier*: same clips, same music, new captions+VO ≈ near-free reel
per extra language.

### B2. Two supported flows
1. **Language-first authoring:** canvas gets a **Language** setting (hi/en/mr/pa/bn —
   the catalogue already in `agents/languages.py`). Planner prompt gains one rule:
   *"caption lines in <language>; image prompts, shot grammar and motion in English."*
   The 📄 Script view is then natively in that language.
2. **Version-after-render (the repurpose flow):** on a finished reel — **"🌐 Make
   language version"** → captions translated via the existing `/caption-variants`
   endpoint (LLM, free) → **📄 Script opens in the target language for review/edit
   (mandatory gate — no render until the operator has seen the translation)** → render
   reuses the cached clips/music; only captions burn + VO regenerate. Output lands as
   `output_<lang>.mp4` beside the original.

### B3. What must be built
- **Fonts (T13a, S):** Devanagari/Gurmukhi/Bengali glyphs don't exist in Baskerville.
  Bundle **Noto Serif Devanagari / Gurmukhi / Bengali** (OFL — rule-10 ritual: TTF in
  `deploy/fonts/`, Dockerfile + fc-cache, dropdown). `caption_writer` gains a
  language→font map (config) so the house serif style switches script automatically;
  operator font choice still wins.
- **Flow + UI (T13b, M):** `state["language"]`; sidebar Language select; the
  version-after-render button + translated-script review gate; translation cached per
  (text, lang); VO passes `lang` into `cast.voice_for_frame` (already implemented).
- **Voices (T13c, config-only):** fill `config/voices.json → language_voices` with
  ElevenLabs multilingual voice IDs per role (needs owner's account picks — Hindi-native
  narrator, male/female speaker voices). Per-character `voice_id` (T4) still wins.

### B4. Ledger tie-ins (T1)
- Translated VO lines run **longer** than English (Hindi ≈ +15–25%); the slot-fitter
  trims overflow → **`warn: "VO line trimmed in <lang> on fNN"`** in the render report,
  and the translation prompt requests a matched syllable budget to prevent most of it.
- Missing language voice → `info: "no <lang>-native voice configured — using default"`.

---

## Red team

- **A/clutter:** operators will over-decorate → amateur output. → Max 2 overlays/shot
  (soft), presets only (no freeform styling), title-safe 9-grid only. The style tokens
  (radius/shadow/border) come from ONE set so every overlay looks like the same product.
- **A/occlusion:** a chip over the subject's face ruins the shot. → Default position
  corners; preview-first; (v2) a cheap face-box check that warns when an overlay
  overlaps a detected face — ledger `warn`, not a block.
- **A/cache poison:** overlay edits must not invalidate clip caches. → Compose-on-top
  keyed separately (A3.2); verified by asserting base-clip cache hits after overlay edits.
- **A/scope creep → mini-editor:** freeform drag, rotation, text overlays, animations =
  a timeline editor we said we wouldn't build. → v1 is presets-only; text stays in the
  caption system; animation waits for the T6 seam. Non-goal recorded.
- **B/mistranslation:** a wrong Hindi line in a published HOB reel is a trust wound. →
  The translated-script review gate is **mandatory**, not optional; operator edits are
  what render. (Same review-first principle as keyframes-before-video.)
- **B/name & honorific handling:** "Pawanputra Hanuman", "Papa" must survive translation.
  → Translation prompt pins proper nouns + honorifics; Script review catches the rest.
- **B/font fallback lies:** missing glyphs render as tofu (□□) silently. → T13a ships a
  glyph smoke-test (render a sample line per language via libass, assert non-blank
  pixels) in the verify loop; failure = ledger `alert`.
- **B/cost illusion:** "near-free" is true for clips/music but VO regenerates per
  language (ElevenLabs cost × languages) — surface per-language cost in the 💰 estimate
  before the version render.

## Sequencing (recommendation)
T3 (auto-fill) → T5 (take history) → **T13a+b (language)** → **T14a+b (composer v1)** →
T6 (Remotion) → T14c (animated overlays) → T13c whenever owner picks voice IDs.
Rationale: language multiplies every reel's value (repurposing) and is smaller; the
composer's animated half gets better after the Remotion seam exists anyway.

## Non-goals
No freeform drag/rotate editor, no AI-generated overlay copy, no auto-fan-out across
all languages (operator picks each), no RTL languages in this pass, no lip-sync
re-timing for translated VO (slots pad/trim as today).

## Verify criteria
- **T14:** overlay-preview matches final composite (pixel-diff tolerance); base-clip
  cache hit after overlay edit; composite failure falls back cleanly + ledger `warn`;
  speaker-chip toggle adds/removes chips idempotently.
- **T13:** glyph smoke-test per language; Hindi end-to-end (translate → review → render)
  with VO audibly Hindi + captions in Devanagari serif; clip cache hits on the version
  render (no re-spend on video); ledger warns on trimmed VO lines.
