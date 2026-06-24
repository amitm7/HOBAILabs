# MODE3_PLAN — Studio Mode (prompt → full reel)

Status: **in progress** (Phase P1–P5 landing in one unit of work).
Owner: HOBAILabs. Front door: `/studio`. Engine: the SAME `_run_inner` + `agents/*`.

Studio Mode is the third front door (after Story `/` and Brand `/brand`). The user
types a free-text brief and gets a full reel, while keeping every existing control
(model routing, cost estimate, safety gates, captions, brand mandatories). It is a
**mode flag + extra inputs into the shared engine** — never a pipeline fork
(build-feature rule #1).

## 1. Locked decisions

- **Generation = Path A**: text → LLM expands into the existing `frames[]` → your
  image models (identity-locked) → existing **image-to-video** → assembler. No new
  video vendor. True text-to-video (Veo/Sora t2v) is a **non-goal** here (deferred).
- **Identity Library**: reusable **Talent** (face) and **Product** reference assets,
  saved once and reusable across shots and future runs.
- **Two sub-scopes**, selectable in the UI:
  - `commerce` — single subject × N camera setups, product-locked (jewelry/fashion ads).
  - `general` — emotional beats, like Story mode.
- **AI may write** dialogue/captions (editable) — bounded by §2.
- **Per-shot `negative_prompt` + `continuity_lock`** are first-class, editable, with defaults.

## 2. Governance reconciliation (HARD RULE)

The user chose "AI writes dialogue," but BRAND_PLAN §5 forbids AI authoring regulated
ad claims. Reconciliation, enforced in code:

- AI **may** draft dialogue/voiceover/captions in Studio. All AI-written copy passes
  through `safety.moderate_*` (Gate A) and is shown editable before any spend.
- When commerce sub-scope runs with brand mandatories ON, **regulated claims, CTA
  text, and the sponsorship disclosure stay user-supplied verbatim** (reuse brand
  layer). AI may suggest a draft but cannot ship those fields unedited.

## 3. Data-model deltas

`agents/product_surface.py` (SQLite stand-in; mechanical Postgres migration later):

- `talents(id, name, ref_sha256→assets, descriptor, created_at)`
- `products(id, name, ref_sha256→assets, specs_json, created_at)`
- Reference images stored via existing `register_asset(kind="talent"|"product")`.

New `frame` dict keys (add fields, don't thread params — rule #2):
`talent_id`, `product_id`, `talent_ref_path`, `negative_prompt`, `continuity_lock`,
`studio_scope`.

## 4. Modules touched

- **`agents/product_surface.py`** — Talent/Product tables + CRUD (P1).
- **`agents/shot_planner.py` (new)** — cached, schema-validated reasoning call: brief
  (+scope, +talent/product) → `frames[]`. Graceful fallback to a sentence split (P2).
- **`web_app.py`** — `_build_frames_from_payload` reads the new keys + resolves
  talent/product references; `_generate_stills` passes the talent reference into
  `generate_contextual_image` (the reference-edit identity lock already exists);
  product beats use the real product image (passthrough); new routes (P3/P4).
- **`agents/clip_builder.py`** — per-shot `negative_prompt` threaded into Kling
  (default fallback unchanged) (P3).
- **`web/templates/studio.html` + `web/static/studio.js` (new)** — mode companion
  (mirrors the brand.html/brand.js pattern); shared `main.js` engine (P4).

## 5. Cost / cache / safety (through the seams)

- Cost stays server-truth: `pricing.estimate()` + `POST /api/estimate` (rule #7).
- Caches: brief→shot-list cached by brief hash; stills cache key already folds the
  reference-image bytes (identity lock participates) — see `image_generator`.
- Safety gates inherited (run inside `image_generator`) — rule #6.

## 6. Phases

- **P1** — Talent/Product library + CRUD. ✅
- **P2** — `shot_planner.py` (both scopes), cached + schema + fallback. ✅
- **P3** — reference-locked stills + per-shot negative/continuity + product passthrough. ✅
- **P4** — `/studio` route + `_run_inner` studio branch + UI. ✅
- **P5** — governance enforcement + docs sync + verify loop. ✅

## 7. Non-goals

True text-to-video vendor (Path B/C), multi-image product-on-model reference,
real-time preview, new TTS languages, auto-publishing.

## 8. Sharp edges

1. **Cross-shot identity consistency** with i2v — mitigated by the existing
   reference-edit path (`generate_contextual_image(reference_path=…)`).
2. **Product micro-detail fidelity** — mitigated by product-hero passthrough (real
   product image as the i2v start frame; never regenerated).
3. **Multi-image reference** (model wearing the product) needs a multi-ref model
   (e.g. nano_banana) — deferred.
