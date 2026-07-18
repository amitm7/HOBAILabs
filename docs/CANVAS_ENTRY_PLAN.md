# CANVAS_ENTRY_PLAN — one creator mode (S31)

**Status:** DECIDED 2026-07-17 (owner call). Canvas is the product. Story / Brand /
Studio front doors are retired. This doc is the decision record + execution order.

---

## 1. The call

**ONE creator mode: Canvas.** Entry is a prompt box, not a form.

| Door | Fate | Why |
|---|---|---|
| **Studio** (`/studio`) | **DELETE** | Pure redundancy — canvas already calls its `shot_planner`; its talent/product library is canvas's characters/locations. Port `product_surface.py` product locks into characters. |
| **Story** (`/story`) | **BECOMES THE DEFAULT** | Not a skill — it's what happens when you just type a story (`scope=general`). Its unique bits get salvaged (§5). |
| **Brand** (`/brand`) | **BECOMES THE `Product Ad` SKILL** | The only door protecting something real: the ad-claims hard gate + CTA end-card + sponsored-disclosure burn + brand VO/music modes. The *door* dies; the *mode* lives. |

**Naming:** internal term stays **`scope`** (already the seam in `shot_planner.py` +
`canvas.html`). Do NOT introduce "skills" as a code word — it already means an agent
slash-command (Runway), a marketplace package (Higgsfield), and `.agents/skills/*/SKILL.md`
in this repo. UI copy can say whatever marketing wants; the code says `scope`.

## 2. Evidence this is safe (parity audit, 2026-07-17)

- **No pipeline fork exists to unwind.** Every door already funnels into `_run_inner`
  (`/run:2886` → `_execute_pipeline:3778`; `/api/canvas/<id>/render:1674` →
  `_canvas_render_thread:3847` → same). Consolidation is a UI/route change.
- **Canvas is ~85–90% parity and AHEAD on nine capabilities:** T13 translate, T14
  overlays, T15 library, take history, storyboard art, locations+plates, per-character
  voices (T4), degradation ledger (T1), plan QC slideshow gate, T3 suggestions.
- **Story/Brand are engine-thin** — ~12 door-specific routes vs canvas's ~50.

## 3. Entry: prompt-first (replaces the wizard)

Screen 1 is **one box and nothing else**:

```
                What are we making?
  ┌────────────────────────────────────────┐
  │ Tell me the story…                     │
  │ [+ photos]  [/scope chip]   Dev ▾  [→] │
  └────────────────────────────────────────┘
     [Story] [Product Ad] [Mood Board] [All]
     ── Recent ─────  [card] [card] [card]
```

- **Detect → declare** (the S28 pattern): infer the scope from the prompt, show it as an
  **editable chip**. Runway makes you pick; we guess and let you correct.
- **Land in the board**, plan already run. `#chat-input` is promoted from a footer field
  to the primary refine surface.
- The ~25 controls now on `canvas.html:284-376` (caption font/size/colour before you've
  seen a shot) go to: **auto-filled by T3** · **revealed per-scope** by the registry ·
  or a **settings drawer**. Nothing removed — deferred until there's something to react to.
- **Dark, per the Veristory DS** (`web/static/veristory/tokens/colors.css` is dark-primary:
  `--ink-950` page, warm `--paper-50` text). Canvas is light today; that alone is much of
  the "clumsy". Lean on `--prov-real` / provenance tiers — the DS already encodes the moat
  as a colour language. That is the differentiator; every rival is dark chrome + gradients.

## 4. Scope registry (many entries → few grammars)

`scope → {system_prompt (beat grammar), governance_flags, ui_controls, orchestration
(which stages run + where it stops), copy, landing_route}`

| UI entry | Grammar | Notes |
|---|---|---|
| Story *(default)* | narrative | exists (`_GENERAL_SYSTEM`) |
| Product Ad | product | exists (`_COMMERCE_SYSTEM`) **+ brand governance** |
| Mood Board | — stages 1–2, stop, export grid | **~₹10** vs ₹150–500/reel — the free-tier hook |
| Podcast / Education | presenter | S29 → `docs/PRESENTER_PLAN.md`. The registry mechanism itself (`shot_planner._SCOPE_SYSTEM_PROMPTS`) shipped 2026-07-18 with only `general`/`commerce` migrated — a `podcast` row is one dict entry away once S29 Phase 0 clears. |
| UGC / Testimonial | product + presenter | small delta |

**Not scopes:** *Ad Campaign* = Product Ad × N variants (a **batch flag**);
*Motion Graphics* = a different **renderer** (Remotion) — defer, it's a real build.
A scope earns a slot only with a **distinct beat grammar**; topic flavours are briefs.

## 5. Pre-flight — BLOCKING, in this order

0. **Fix the AI-disclosure label (live governance bug).** `_canvas_render_data:788`
   hardcodes `subject_name: ""` → `provenance.summarize` sets `real_person_ai=False` →
   the burn-in disclosure (`_run_inner:4268`) **never fires** and canvas reels are labeled
   *"no real person depicted"* while `canvas_run.py:782` generates *"AI likeness,
   conditioned on the real face."* **Fix = thread `subject_name`; KEEP the auto-granted
   `likeness_consent:797`** — the no-consent-gate call stands (owner decision); the label
   was the compensating control traded for it, and it is broken. Do not re-add the gate.
   *(Open question for the SaaS phase, owner's call: the no-gate decision was made for
   your own face / HOB operators. Strangers uploading other people's faces is a different
   question. Flagged, not changed.)*
1. **Browser photo upload → the `[+ photos]` affordance.** THE #1 parity blocker:
   `canvas.html:365` is a text box for a *server-side path*; `webkitdirectory` →
   `/upload-folder` exists only on `index.html:72` / `brand.html:140` / `studio.html:106`.
   Hosted, the server cannot see the user's disk — so the moat workflow (real photos →
   untouched passthrough) is Story-only in production. Engine side is already shared.
2. **Extract the posting kit** → `agents/posting_kit.py` (today inline at
   `web_app.py:2409-2453`, and it hard-refuses brand mode) → surface on canvas.
3. **Surface export / provenance / credential / performance in `canvas.js`** — the routes
   are run_id-keyed and already work for a canvas `render_id`; they are simply unsurfaced.
4. **Brand → Product Ad scope**: canvas must pass `mode="brand"` (it hardcodes
   `"story"` at `:785`) and call `brand.validate_mandatories` — whose only caller today is
   `/run:2897`. **Add a test that proves the gate fires through the canvas path**;
   `test_brand_mandatories` passes at module level with nothing calling it — a green suite
   proving nothing.
5. **Auth**: `/api/canvas/plan:460` has no `@auth.require_operator()` while `/run:2887`
   does → unauthenticated LLM spend. Matters more in SaaS.

## 6. Then retire

- Delete `index.html`, `brand.html`, `studio.html`, `web/static/main.js`, `brand.js`.
- Delete orphan routes (no UI caller in ANY door): `/hook-workshop:2493`,
  `/caption-variants:2515`, `/render-variants:2541`, `/asset-library/register:2552`,
  `/project-version:2577`. Keep `/brand-approval:2567` — wire it to the Product Ad scope.
- Fix **hardcoded navs**: `canvas.html:264` and `library.html:51` carry their own nav
  (they don't extend `_base.html`) → dead links.
- Repoint `landing.html:190/384/392` sign-in from `/story` → the canvas entry.
- **Port, don't delete, tests**: `test_core_behaviour.py:174-189` (posting kit),
  `:191-271` (story-intake / brand block), `:402-420` → canvas equivalents.

## 7. Positioning rationale (why one mode + scopes beats both rivals)

- **Runway / Higgsfield** organise by **deliverable** (Ad Campaign, Commercial, UGC) —
  easy entry, shallow depth. Runway shipped Agent Skills **2026-07-02**; we are not late.
- **galleri5** organises by **crew role** (12 agents: writing, cinematography, art
  direction, critique; episodes→scenes→shots) — deep, but demands film literacy.
- **Us:** our engine is already crew-shaped (board, cast, locations, takes) while our GTM
  is deliverable-shaped. **The scope layer is the deliverable-shaped door onto a
  crew-shaped engine** — enter with "make me an ad", get crew depth the moment you want
  it. Neither rival occupies that. This is the reason to keep the canvas AND add scopes.

## 8. Kill / reversal conditions

- **Never delete a door before** its salvage items (§5) are ported and tests pass *through
  the canvas path*. Deprecate → migrate → delete.
- The prompt box must beat the wizard on **time-to-first-reel** for a new user. If it
  doesn't after one round of fixes, keep a guided scope as the default entry.
- If Brand's gate can't be made to fire from canvas cleanly, keep `/brand` until it can —
  an unenforced ad-claims rule is worse than an extra door.

## 9. Docs-sync (hard gate, per phase)

`docs/HLD.md` (route classes, deleted modules, scope registry) · `docs/LLD.md` (registry
schema, `mode`/`subject_name` threading, new frame keys) · `GUIDE.md` +
`docs/OPERATOR_GUIDE.html` (one mode, scope chips, where the old controls went) ·
`docs/L99_ARCH_PLAN.md` (S31 row) · this doc (tick per phase).
