# HOBAILabs — Brand / Ad Mode Plan

**Created:** 2026-06-12
**Companion:** [HLD.md](HLD.md) · [LLD.md](LLD.md) · [SCALE_PLAN.md](SCALE_PLAN.md)
**Status:** decisions locked (this doc), execution NOT started.

Brand mode turns the storytelling engine into a **branded-ad / collaboration** tool
without forking it: one engine, a second front door (`/brand`), and a brand layer
on top. Reference ad format (operator-described): a Tata Sampurna cooking-oil
spot — multiple realistic shots, background music, an **announcer VO over the
music** making a regulated claim, demo shots, product hero.

---

## 1. Guiding principles

1. **Separate front door, shared engine room.** A `/brand` page and a mode-aware
   single `main.js`; the SAME `agents/*`, `/run`, `_run_inner`, cache, cost ledger,
   safety gates. No second pipeline, no `main.js` fork.
2. **The AI never writes ad copy or claims.** All on-screen and spoken copy —
   especially regulated claims — is **brand-supplied and placed by the creator,
   verbatim.** This removes claim-invention risk by design (ASCI/legal).
3. **Brand assets are real-only.** Logo and product are never AI-generated.
4. **Nothing ships money-spending without passing the mandatories gate.**

## 2. Storytelling vs Brand — the set view

- **A ∩ B (reuse 100%):** script→beats, hook discipline, treatment/arc, scene
  intelligence, real-media passthrough + AI gap-fill, cast/speakers + voices,
  captions, camera motion, transitions, music, voiceover, lip-sync, mood, pacing,
  quality tiers, cost estimate, caching, safety A/B, assembly.
- **A \ B (storytelling-only):** pure emotional payoff, full creative freedom, no
  mandatories, no disclosure, no third-party approval.
- **B \ A (brand-only — the new layer):** campaign brief, brand kit as constraint,
  real-only product/logo, mandatories (logo/CTA/disclosure/product beat), sponsored
  disclosure, brand-safety, brand voice/tone, VO-over-music, separate announcer
  script, regulated-claims handling, approval/versions, multi-format deliverables.

## 3. Locked decisions

| # | Decision | Choice |
|---|---|---|
| 1 | Production model | **Montage** (not AI-presenter UGC — that's a separate future path) |
| 2 | UI | Separate `/brand` page; one mode-aware `main.js` (no fork) |
| 3 | Brief input | **Structured fields + optional paste-the-brief → LLM extract pre-fills** |
| 4 | Mandatories | **Hard-block render** until disclosure + CTA + logo + ≥1 product beat |
| 5 | Music rights | **Per-project toggle** (AI music vs brand-supplied) |
| 6 | VO production | **Per-project toggle** — AI announcer for drafts, brand audio for final |
| 7 | Approval (v1) | Deliverable + per-session re-renders; true versioning later (needs DB) |
| 8 | Aesthetic | **Full kinetic / performance-ad** → build the motion-graphics overlay layer |
| 9 | Copy & claims | **Brand supplies ALL copy; AI writes none; creator places it verbatim** |
| 10 | Product on screen | **Full-frame product beats in v1; PIP/pack-shot overlay in B2** |

**Secondary defaults (veto-able):** disclosure = burned-in "Paid partnership with
{brand}" first ~3s (+ remind operator to set IG's native label); logo = on CTA
end-card always + optional corner bug (default off); CTA = auto-appended ~3s
end-card; format = 9:16 v1 (multi-format export = fast-follow); brand kit stored
per-render until the DB lands.

## 4. Data model (additive)

`/run` (and `/preview`) gain `mode: "brand"` + a `brand` block:
```
brand = {
  name, product, objective, key_message,
  cta_text, cta_url, tagline,
  colors: [hex...], font, logo_path,
  product_assets: [paths],          # real-only
  announcer_script,                 # separate from captions
  vo_mode: "ai_draft" | "brand_audio",  vo_audio_path,
  music_mode: "ai" | "brand_audio",      music_audio_path,
  disclosure: true,
  mandatories: {logo, cta, disclosure, product_beat},  # all must pass
  overlays: [ {frame_id, type: text|badge|sticker|pip, text, style,
               anim, position, t_in, t_out} ],          # B2 graphics layer
}
```
Per-frame additions: `product_beat: bool`, `real_only: bool` (product/logo frames),
and (B2) attached `overlays`.

## 5. Hard rules (non-negotiable)

- **No AI-authored ad copy/claims.** Captions/claims/CTA come from `brand.*` verbatim.
- **Real-only brand assets.** `product_beat`/logo frames skip generation entirely.
- **Mandatories gate runs BEFORE spend** — `/run` validates synchronously and
  returns the missing list (HTTP 4xx); UI shows the checklist; no render starts.
- **Brand-safety gate** (extends Gate B2 vision critique): no competitor logos,
  accurate product depiction, no fabricated brand elements.
- **Disclosure always present** (burned-in) when `mode == brand`.

## 6. Phasing

### Phase B1 — shippable branded ad (no kinetic layer yet)
A complete Tata-style ad end-to-end; on-screen claims shown as **bold static
callouts** (placed per beat) as the bridge until B2.

- **B1.1** `agents/brand.py` — brief model, paste→fields extract, mandatories
  validation, treatment/brand context, disclosure text. *(LLM-light, unit-testable.)*
- **B1.2** Real-only enforcement in the stills/clip path for product/logo beats.
- **B1.3** Brand-safety: extend `safety.critique_image` with brand context.
- **B1.4** Assembler: CTA end-card, optional corner logo bug, burned-in disclosure,
  **VO mixed over ducked music** (extend the existing ducking path).
- **B1.5** Treatment + scene design receive the brief (weave product, mark beat).
- **B1.6** `web_app`: `/brand` route, `mode` in `/run`/`preview`, **hard-block
  mandatories** validation, per-project music/VO, `/extract-brief` endpoint.
- **B1.7** UI: `brand.html` + mode-aware `main.js` (brief panel, brand kit upload,
  product-beat toggle, static callout field per beat, mandatories checklist).
- **B1.8** Guides + verify (compile, offline, live smoke, ffmpeg smoke).

### Phase B2 — kinetic graphics / motion-graphics layer (the big subsystem)
- Timed overlay elements (text, badge, sticker, price callout, product PIP) with
  in/out animations and **word-by-word sync to the VO**.
- Brand-styled (kit colors/font), per-beat **placement UI / overlay timeline**.
- Renders via an ffmpeg overlay/`drawtext`/ASS-animation compositor pass.

## 7. Non-goals (now)

- AI-presenter / spokesperson UGC ads (separate generation path).
- AI-written claims or copy of any kind.
- Saved brand kits, in-app brand approval/sign-off, batch multi-format export
  (all gated on the SCALE_PLAN DB + versioning work).
- Kubernetes / queue changes (unchanged from SCALE_PLAN).

## 8. Open confirmation before execution

- Green-light **B1 first** (recommended) vs **B1 + B2 together**.
- Confirm the §3 secondary defaults (disclosure style, logo bug default, 9:16-only v1).
