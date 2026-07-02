# Director Canvas V2 Productization Plan

**Created:** 2026-07-02
**Branch:** `canvas-director-polish`
**Status:** plan-first productization pass before UI implementation; red-team mitigations incorporated
**Companion:** `docs/AGENTIC_CANVAS_PLAN.md`, `docs/COMPETITOR_GALLERI5_TEARDOWN.md`, `docs/REAL_MEDIA_QUALITY_LADDER.md`

**Landed on this branch (2026-07-02) — V2 matrix bug-fix pass:**
- `[hidden] { display:none !important }` — the flex-based inspector / chat / render
  panels were ignoring the `hidden` attribute (always visible on an empty canvas).
- ＋New no longer crashes (`#rail` was removed with the old stage rail; JS still wrote to it).
- Photo picker moved into the inspector (its old absolute positioning pinned it to the
  page bottom, and it targeted a `data-stage` attribute no cell carried). Cells now carry
  `data-stage` for targeting (shimmer feedback + tooling).
- Script view works again inside the V2 shell (board *viewport* toggles, `.cv-script`
  styles restored); Storyboard button simplified to "fill missing panels" (the sketch
  now lives permanently in the Storyboard column — no phantom "Exit storyboard" mode).
- Restored CSS the V2 rewrite dropped but still referenced: cost banner, character
  sheet, fidelity-suggest chip, saved badge, AI-story-mode tool hiding (`body.story-ai`),
  sketch filter, arrowheads.
- Per-run media caches (`stillsCache`/`clipsCache`/`assetCache`/active shot) reset on
  run switch — resuming a different canvas no longer shows the previous run's stills
  (frame ids `f01…` collide across runs).
- Audio column follows the audio-mode selector live; re-roll cache-buster URL fixed.
- **Silent-reel surfacing** (root cause of the "useless reel" incident: Suno credits
  exhausted → best-effort music skip → silent 141s reel shipped as done ✓):
  `audio_warning` on the canvas state + ffmpeg output QC probe + red 🔇 board strip +
  "done — ⚠ NO AUDIO" render status. See LLD `/api/canvas/<run_id>/render` row.
- **Head-to-head quality pass (the six-dimension CEO review):**
  - *Asset QC gate:* `image_matcher.exif_upright` normalizes EXIF-rotated real photos
    at ingest (match-photos / rematch / real attach; originals untouched, upright copies
    in `<run>/upright/`); the 🔎 Review vision pass now also flags rotated pixels,
    watermarks/camera-brand overlays, and unreadable full-frame documents.
  - *Duration:* UI target-length default Auto → **~60s** (operator keeps every option
    incl. Auto — default-only change; no server cap).
  - *Captions:* canvas default Montserrat-24/3-line → **Baskerville 52 / 2-line**
    (the engine's own storytelling style; Baskerville added to the font dropdown).
  - *Pacing:* covered by the 60s default (planner redistributes durations to target)
    + the restored music bed (beat-aware cutting). Remaining P2: beat→asset ambient
    recreate ladder for text↔frame mismatches (Review flags them today).

## 1. Verdict

HOBAILabs already has most of the hard canvas capabilities: stage gates, per-stage
cost, storyboard art, keyframes, video, final render, re-roll, real-media handling,
provenance, consent, and save/resume.

The next win is not another backend feature. The next win is making the existing
capability feel like a controlled production workspace.

**Canvas V2 should be a real-media-first production board for one reel, not a
freeform Miro clone and not a forced synthetic pipeline.** The workflow is mostly
linear, so the UI should make progression, approval, cost, and authenticity visible
at a glance. Real footage must feel locked, preserved, and privileged.

This plan is **productization for the internal/agency workflow**, not the SaaS
foundation by itself. A polished single-tenant canvas improves trust, demo quality,
and editor control; it does not replace tenancy, account boundaries, billing/credits,
durable shared storage, monitoring, or support/admin tooling.

## 2. Product Goal

When an operator opens `/canvas`, they should instantly know:

1. What story is being made.
2. What shots exist.
3. Which stage each shot is in.
4. Which shots are real media, restored real media, AI symbolic, or AI likeness.
5. What is approved, rejected, rendering, failed, or risky.
6. What it costs to run the next paid action.
7. What is ready for Final Cut.

If those seven answers are visible, the product becomes sellable. If they are hidden
behind scattered controls, the product still feels like an internal tool.

## 3. Design Direction

Use a **stage-column board with real-media fast-tracks**:

```text
Brief / Script | Storyboard | Keyframes | Audio | Video | Final Cut
Shot 01        | panel      | still     | voice | clip  | timeline
Shot 02        | panel      | still     | voice | clip  | timeline
Shot 03        | panel      | still     | voice | clip  | timeline
```

This beats arbitrary node dragging because HOBAILabs has a clear production sequence.
Editors need confidence and speed more than spatial freedom.

Important nuance: the board is a review model, not a demand that every shot pass
through every synthetic stage. Real-media shots can show bypassed/locked cells for
Storyboard and Keyframes where those steps are redundant. The UI must say "this
real footage is preserved" instead of implying "generate something here."

## 4. V2 Layout

### Top Bar

- Product identity: `Director Canvas`
- Current run title / saved status / resume
- Global readiness: `3/6 stages approved`
- Total estimate and spend-cap state
- Primary CTA: `Render approved` / `Final Cut`

### Left Panel

- Brief
- Story type: Real story / AI story
- Scope, length, quality
- Script review toggle
- Media folder and character controls
- World/context controls

### Center Canvas

- Horizontally scrollable stage columns.
- Vertically stacked shot rows.
- Sticky stage headers.
- Shot cards inside each stage cell.
- Empty cells show the next required action.
- Paid stage cells show cost + ETA before spend.
- Real-media cells can be `LOCKED REAL`, `BYPASSED`, or `RESTORE AVAILABLE`
  instead of presenting synthetic generation as the default.

### Right Inspector

Selecting any shot opens an inspector with:

- Caption / script line
- Source media
- Shot grammar
- Image prompt
- Motion prompt
- Fidelity ladder
- Provenance / authenticity status
- Consent state
- Cost breakdown for available actions
- Replace, restore, upscale, re-roll, AI source controls
- History: original real media -> restored/upscaled/generated -> clip

### Bottom Timeline

- Compact final reel strip.
- One tile per shot with approved status.
- Static clip thumbnails as each video lands.
- Finished output player once Final Cut is done.

## 5. Red-Team Corrections

These risks are accepted as real product constraints, not polish opinions.

### 5a. Protect the real-media moat

Risk: a uniform Script -> Storyboard -> Keyframes -> Video board can accidentally
teach operators that real footage should be regenerated.

Mitigation:
- Real shots get an explicit preserved path: `REAL LOCKED`, `RESTORE`, `UPSCALE`,
  `VIDEO`, `FINAL CUT`.
- Storyboard and Keyframe cells for real shots can show `bypassed: real media used`
  instead of a generation CTA.
- AI replacement controls stay visible but secondary and labeled as an exception.
- `AI likeness` is never presented as the normal fix for a poor real shot.

### 5b. Prevent browser video overload

Risk: 10-20 rows across Video and Final Cut columns can create dozens of active
`<video autoplay loop>` elements.

Mitigation:
- Board cells use static thumbnails by default.
- Video previews play only in the right inspector, the final output player, or on
  intentional hover/focus.
- Offscreen cells must not mount active video elements.
- The bottom timeline uses still thumbnails with a play indicator.

### 5c. Preserve financial safety under rapid clicks

Risk: double-clicks or parallel dispatches can create confusing spend/reservation
behavior even if the backend is server-truth.

Mitigation:
- Disable the clicked paid action synchronously before awaiting the network.
- Lock the active stage column while dispatch is in flight.
- Show an in-flight state tied to the stage/run id.
- Ignore repeated clicks client-side, while relying on backend reservation as the
  real guard.
- Any cost banner shown during edits is marked stale until the server returns the
  new state.
- While any paid stage is running, edits that would invalidate that stage or an
  upstream dependency are blocked or require an explicit "stop/discard generated
  work" confirmation. The UI must not show a generating spinner on a stage that a
  local edit has just invalidated.

Important correction: client-side locks are not financial safety. They are only
operator feedback and defense-in-depth. The real control must live server-side:
paid routes must be idempotent or job-locked, and spend reservation/check/dispatch
must be atomic.

### 5d. Avoid inspector save floods

Risk: moving controls to the inspector can flood `/api/canvas/<id>/frame` if saves
fire on keystroke debounce.

Mitigation:
- Text fields save on blur, Enter, or explicit `Apply changes`.
- Do not save on every keystroke.
- Dirty fields show a local unsaved marker.
- Applying changes disables downstream stage actions until the server returns the
  invalidated state.
- Switching from one selected shot to another with dirty inspector fields prompts
  `Apply / Discard / Stay`. No silent loss, no surprise auto-save flood.

### 5e. Keep UI state and engine state from drifting

Risk: the board view adds local UI concepts (selected shot, dirty inspector,
in-flight column, stale cost banner, thumbnail playback state) that can drift from
server state, especially while `/progress` SSE and `/rendered` polling update the
same run.

Mitigation:
- Define a small client state model before layout work:
  - `selectedFrameId`
  - `dirtyFields`
  - `savingFrameId`
  - `inFlightStageId`
  - `staleCost`
  - `renderedMedia`
- Server responses remain authoritative. SSE/poll updates may refresh media and
  status, but they must not overwrite dirty inspector fields.
- Any server response with a newer canvas state clears `staleCost`; local edits set it.
- Re-render functions must preserve selection and dirty state unless the selected
  frame no longer exists.

### 5f. Avoid two-dimensional scrolling hell

Risk: a large matrix can be worse than the current card board if it requires both
horizontal and vertical hunting.

Mitigation:
- Fixed-height rows and fixed-width stage columns.
- Sticky stage headers.
- Sticky shot labels on the left edge of the board.
- Single scroll container for the matrix.
- Mobile/tablet uses stage tabs or stacked shot details, not a tiny matrix.
- Zoom/pan is implemented as viewport scale classes or layout modes, not arbitrary
  CSS transforms over live controls unless hit-testing is verified.

### 5g. Do not break the working board during layout refactor

Risk: current `canvas.js` is template-string/event-delegation heavy. A large DOM
reshape can silently break picker, upload, re-roll, restore, fidelity, storyboard,
and render reveal actions.

Mitigation:
- Phase 1 starts by separating render helpers (`renderShotSummary`,
  `renderInspector`, `renderStageHeader`, `renderTimeline`) while keeping endpoint
  behavior unchanged.
- Preserve data attributes used by event delegation (`data-frame`, action classes)
  or update the delegated selectors in the same patch.
- Avoid introducing drag/drop in the first slice.
- Keep the old board behavior recoverable during implementation until the new shell
  passes route smoke tests.

## 6. No-Regrets Foundation Before Polish

Two pieces sit at the intersection of canvas quality and SaaS readiness. They should
be treated as higher leverage than a full board rewrite:

1. **Atomic server-side spend gate.**
   - Paid canvas routes must be idempotent or protected by a server-side active-job
     lock.
   - Spend-cap check, reservation, and dispatch must be one atomic server-side path.
   - A double-click must not create duplicate paid jobs or bypass the cap.
   - UI disable states are required for clarity but are not the source of truth.

2. **Auth coverage for library routes.**
   - `/api/talents` and `/api/products` create/delete reusable identity assets and
     should be operator-gated before multi-operator or SaaS-like use.
   - This is a tenancy prerequisite, not merely a polish item.

These are mandatory on every path: internal production, agency workflow, or future
SaaS. Do them before choosing between a full board rewrite and deeper SaaS
foundation work.

## 7. UX Rules

1. **Every expensive action names cost and ETA before spend.**
   Example: `Generate keyframes · $0.24 · ~3m`.

2. **Every authenticity state is visible on the card.**
   Use badges:
   - `REAL`
   - `RESTORED`
   - `AI SYMBOLIC`
   - `AI LIKENESS`
   - `CONSENT NEEDED`
   - `CONSENTED`

3. **Storyboard Review is the emotional center.**
   The first strong product moment should be: brief -> storyboard panels -> operator
   approves creative direction before paid video.

4. **The board defaults to control, not decoration.**
   Dense, calm, operational. No hero layout, no marketing shell, no decorative canvas.

5. **Zoom/pan is useful but secondary.**
   Add viewport controls after the stage board has meaning:
   - zoom in/out
   - fit to board
   - zoom to selected shot
   - drag-to-pan
   - mini-map only if shot count makes it necessary

6. **Old scattered controls move into the inspector.**
   Cards should not become control junk drawers. They should summarize; inspector edits.

7. **Videos are not ambient decoration.**
   The board shows thumbnails; playback is intentional.

8. **Real-media bypass is a first-class state.**
   A bypassed synthetic stage is success, not absence.

## 8. Preservation Checklist Before Rewrite

Before any substantial `canvas.html` / `canvas.js` rewrite, preserve and smoke-test:

- Plan a new canvas.
- Resume a saved canvas.
- Delete a saved canvas.
- Script view toggle.
- Storyboard view/render.
- Media folder match.
- Character detection and character save.
- Per-shot photo picker.
- Upload/attach real photo.
- Upload/attach AI reference face.
- Re-match one shot.
- Replace with AI generic.
- Revert back to real.
- Fidelity ladder selection.
- Fidelity suggestion.
- Restore.
- Re-create ambient.
- Upscale.
- Re-roll one shot.
- Keyframes stage.
- Video stage.
- Final Cut/render.
- SSE progress and per-shot reveal.
- Rendered media survives reload.
- Cost banner and spend-cap warning.
- No board autoplay video explosion.
- No POST-per-keystroke inspector flood.
- No duplicate paid dispatch from rapid clicks.

## 9. Implementation Phases

### Phase 1 — Board Shell, State Model, And Inspector

Goal: make the existing workflow feel like a product without changing backend semantics
or weakening authenticity.

Scope:
- Introduce the client state model (`selectedFrameId`, `dirtyFields`,
  `inFlightStageId`, `staleCost`) before moving controls.
- Split the current board rendering into smaller helpers so the DOM can change
  without breaking every action.
- Replace the current rail + grid composition with a board shell:
  - left setup panel
  - center stage board
  - right inspector
  - bottom render/timeline panel
- Keep existing endpoints unchanged.
- Keep existing card actions working.
- Selecting a shot sets inspector state.
- Move verbose per-shot controls from card body into inspector where practical.
- Add stage readiness summary.
- Save inspector text edits on blur/Enter/explicit Apply, not on keystroke.
- Prompt on selection changes when inspector fields are dirty.
- Add synchronous client locks for paid stage dispatch buttons.
- Keep board media as static thumbnails; inspector owns active video playback.

Files:
- `web/templates/canvas.html`
- `web/static/canvas.js`
- `web/static/style.css` only if shared variables/utilities are needed
- `GUIDE.md`
- `docs/OPERATOR_GUIDE.html`
- `docs/LLD.md`
- this plan doc

Acceptance:
- Existing canvas plan/resume still works.
- Existing stage rail actions still work, even if visually moved.
- A shot can be selected and edited from the inspector.
- Cards show thumbnail, caption, source badge, stage status, and core actions.
- Real-media shots show locked/bypassed states instead of synthetic CTAs where relevant.
- Rapid double-clicks do not dispatch duplicate client requests.
- Typing in the inspector does not create a POST-per-keystroke flood.
- SSE/poll updates do not overwrite unsaved inspector edits.
- Existing picker/upload/re-roll/restore/upscale/fidelity/render reveal actions still work.
- No backend behavior changes.

### Phase 2 — Real-Media-Aware Stage Matrix

Goal: make progression obvious.

Scope:
- Render columns for `Script`, `Storyboard`, `Keyframes`, `Audio`, `Video`, `Final Cut`.
- Render each shot as a row across columns.
- Show stage-specific artifact in each cell:
  - Script: caption
  - Storyboard: sketch panel + motion arrow
  - Keyframes: still
  - Audio: voice/music status
  - Video: clip
  - Final Cut: timeline inclusion
- Add sticky stage headers.
- Add sticky shot labels.
- Constrain row heights so columns align.
- Add per-stage approve/generate affordances at column header.
- Add bypassed/locked cell states for real-media shots.

Acceptance:
- Operator can scan one shot horizontally from script to final.
- Operator can scan one stage vertically across all shots.
- Matrix rows remain aligned at 6-20 shots.
- Paid actions remain server-truth and gated.
- Real footage is visually privileged, not pushed toward AI generation.

### Phase 3 — Viewport Polish

Goal: make it feel like a real canvas without becoming a toy graph editor.

Scope:
- Add zoom controls: `-`, `+`, `Fit`, `100%`.
- Add drag-to-pan for the board viewport.
- Add `focus selected` behavior.
- Persist zoom locally per browser.
- Add mobile/tablet fallback: single-column stage tabs instead of tiny canvas.
- Verify hitboxes and text readability after any zoom mode.

Acceptance:
- Desktop board is comfortable for 6-20 shots.
- Mobile does not overlap or crush controls.
- Zoom never makes text unreadable inside buttons/cards.
- Zoom/pan does not break click registration.

### Phase 4 — Review And Risk Layer

Goal: make HOB's authenticity moat visible.

Scope:
- Add a board-level risk summary:
  - real shots
  - AI symbolic shots
  - AI likeness shots
  - consent missing
  - spend cap warning
- Add per-shot warning strip in inspector.
- Add provenance popover/summary.
- Add "show only risky shots" filter.

Acceptance:
- A producer can audit the reel before spending.
- Consent/provenance is not hidden in docs.

### Phase 5 — Final-Cut Confidence

Goal: make the last step feel finished.

Scope:
- Bottom timeline with clip tiles.
- Per-shot status in final order.
- Output player integrated into the workspace.
- Timeline tiles use static thumbnails by default; active playback only on selected
  tile or final output.
- Download/export actions grouped with final output.
- Optional post-publish performance capture entry point.

Acceptance:
- Operator can finish, review, download/export, and log performance from one workspace.

## 10. Deferred

Do not build these first:

- Arbitrary node graph wiring.
- Drag-to-reorder stages.
- Model Garden clone.
- Agent Room theatre.
- Complex multi-project dashboard.
- A separate canvas backend.
- Marketplace/SaaS packaging.

These can come later if the core production board proves useful.

## 11. Engineering Constraints

- Keep one engine, many front doors.
- Do not fork `_run_inner`.
- Do not duplicate pricing logic in JavaScript.
- Do not change spend/consent semantics in the UI-only phases.
- Keep route changes out of Phase 1 unless a missing read-only field blocks the UI.
- Preserve current offline tests.
- Add focused tests only when backend behavior changes.
- Client locks are UX guards, not the source of financial truth; backend spend
  reservations remain authoritative.
- Do not mount many autoplay videos in the board.
- Do not use keystroke autosave for inspector text fields.
- Do not make a real-media row look incomplete just because it bypassed synthetic
  generation.
- Do not represent the canvas work as SaaS readiness. It is productization. SaaS
  readiness requires tenancy, billing/credits, auth coverage, durable storage,
  monitoring, support/admin tools, and permission boundaries.

## 12. First Implementation Slice

Recommended immediate slice after this plan is approved:

1. Verify/fix atomic server-side paid-stage dispatch and auth-gate reusable library routes.
2. Add a three-pane canvas shell.
3. Keep the current brief/settings controls but place them in the left panel.
4. Keep stage rail behavior but visually promote it into the board header.
5. Render existing shot cards in the center board with tighter summaries.
6. Add selected-shot inspector using current board data.
7. Show real-media rows with explicit preserved/bypassed states.
8. Replace board autoplay videos with static thumbnails; play in inspector only.
9. Add client-side paid-action locks.
10. Use blur/Enter/Apply for inspector saves.
11. Keep all existing actions functional.
12. Update docs and run:

```bash
~/.pyenv/versions/3.12.3/bin/python3.12 -m py_compile web_app.py
node --check web/static/canvas.js
~/.pyenv/versions/3.12.3/bin/python3.12 -m pytest -q
```

This is the least risky way to make the product feel dramatically more polished.
