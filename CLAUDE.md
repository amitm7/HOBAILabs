# CLAUDE.md — agent context for HOBAILabs

> Auto-loaded by Claude Code for every agent (local **and** cloud). Keep it durable,
> non-secret, and short. It **points to** the canonical docs — it does not duplicate them.
> Update §9 as project state moves.

## 1. What this is
A story → reel pipeline (Instagram Reels / YouTube Shorts) for **Humans of Bombay** —
turn a raw story or frame script + photos/videos into a finished, captioned, scored 9:16
reel. Three front doors on **one shared engine**: Story `/`, Brand `/brand`, Studio `/studio`.

## 2. Deployment
Hosted on **creative.kevat.ai**. AWS **ap-south-1** (kevat.ai account). LLM "brain" is
pluggable via `config/llm.json` — production uses **Bedrock Sonnet 4.6**. No keys live in
the repo: secrets are in `.env` (gitignored); only `.env.example` is tracked.

## 3. Architecture rules (hard)
- **Never fork the pipeline.** Modes are flags into the shared `_run_inner` + `agents/*`,
  not parallel pipelines.
- **Use the pluggable seams**, don't hardcode: `config/models.json` (models + routing),
  `config/pricing.json` (costs), `config/llm.json` (LLM provider), `config/music.json`
  (music engine: lyria|suno), `config/voices.json`, `config/watermarks.json`.
- **Read `.agents/skills/build-feature/SKILL.md` first** before changing `agents/`,
  `web_app.py`, `run_caption.py`, the web UI (`web/`), or `config/`. It encodes the
  conventions, the safety/caching/cost rules, and the compile→offline→live verify loop.

## 4. Docs-sync gate (hard — build-feature rule 11)
A change is **not done** until the affected docs are updated in the **same unit of work**:
- `docs/HLD.md` — architecture (new module/agent, route class, pipeline stage, external service).
- `docs/LLD.md` — module internals (new function signature, `frame` dict key, cache + key,
  new `web_app.py` route).
- `docs/OPERATOR_GUIDE.html` **and** `GUIDE.md` — anything a user can see or click.
- The relevant `docs/*_PLAN.md` — tick shipped items, note what landed.
- `config/*.json` comments / `.env.example` — new env var, model, price, or voice role.

Stale design docs are worse than none.

## 5. Safety / governance invariants
- **Real media is never AI-regenerated.** Real photos/videos of a real person/product pass
  through untouched (real-media preservation — the core realism advantage).
- **AI never writes brand ad claims** (BRAND_PLAN §5). Brand on-screen/spoken copy is
  operator-supplied verbatim; AI-drafted copy must pass `safety.moderate_*` and be shown
  editable before any spend.
- **Consent + spend-cap gates** must pass before paid / external / real-person renders.
- **Authenticity caution (HOB-specific):** AI portraits / synthetic voices of *named real
  people* attack HOB's one irreplaceable asset. Prefer real-photo animation + `ai_symbolic`;
  treat AI-on-a-real-person as a consented, labeled exception. See `docs/MARKET_FIT_REVIEW.md`.

## 6. Cost discipline
Dev tier (cheap, 5s clips) for iteration; Production tier (premium models) for final/client
renders only. Cache aggressively. Always `--dry-run` (CLI) or check the 💰 estimate (UI)
before spending. "Test cheap, finish expensive."

## 7. Quality north-star
Rank work by **output quality (the finished reel)**, not engineering effort. Lean into
premium models for client renders. **Rent the invisible plumbing; build deeply only on the
moat** — director brain, reusable asset intelligence, brand compliance, governance.
Hold a world-class bar; give independent judgment, not sycophancy.

## 8. How to run
```bash
~/.pyenv/versions/3.12.3/bin/python3.12 web_app.py   # → http://localhost:7860
pytest tests/                                         # tests
```

## 9. Current state (keep this current)
- **UI shell redesign** landed: shared Jinja `_base.html`, step wizard + preview panel +
  sticky action bar across Story / Brand / Studio (`web/static/shell.js`, `style.css`).
- **Studio Mode** WIP landed on `main` in commit `cd0447d` (front door `/studio`,
  `agents/shot_planner.py`, talent/product identity library — see `docs/MODE3_PLAN.md`).
- The **feedback-loop stub** (`run_store.performance_*` + `POST /performance/<run_id>`) is
  SHIPPED (was previously listed as in-flight).
- **`docs/L99_EXECUTION_AUDIT.md`** (2026-07-03): full planned-vs-executed audit across all
  plan docs (~170 items, ~62% shipped) — read it before trusting any plan doc's status
  claims; PARITY_BACKLOG in particular is stale.
- Strategy artifacts in flight: `docs/MARKET_FIT_REVIEW.md` (OODA review) +
  `docs/GAP_BACKLOG.md` (prioritized missing capabilities).
- **`docs/L99_ARCH_PLAN.md`** (2026-07-03, red-teamed): hardening plan — P0 degradation
  ledger + canvas-state write safety; P1 plan-time auto-fill, per-character voices,
  shot take-history, Remotion caption wiring; P2 selective lip-sync + beat→asset ladder.
  Includes the owner-suggestion ledger (S1–S17) — check it before starting canvas work.
- **Veristory rebrand (2026-07-05):** the web presence is re-signatured to the
  **Veristory** design system (verified green + antique bronze + Hanken Grotesk /
  Cormorant Garamond / IBM Plex Mono). Tokens live in `web/static/veristory/`
  (source of truth: the claude.ai/design "Veristory Design System" project); the
  app-shell `:root` tokens in `web/static/style.css` are value-swapped to match
  (names unchanged). The marketing landing page (`landing.html`, static, no
  pipeline) is the **root `/`**; the Story front door moved to **`/story`**
  (`/landing` 301s to `/`). Remaining DS deliverables (auth, app shell, Trust
  Center) are still design-side pending.

## 10. Canonical docs (start here)
- `GUIDE.md` — full user guide (behaviour source of truth).
- `docs/HLD.md` / `docs/LLD.md` — high- / low-level design.
- `docs/PRODUCT_IDEAS.md` + `ROADMAP.md` — backlog + roadmap.
- `docs/BRAND_PLAN.md`, `docs/MODE3_PLAN.md`, `docs/SCALE_PLAN.md`, `docs/WORK_PLAN.md` — plans.
- `.agents/skills/build-feature/SKILL.md` — how to build & verify a change here.
