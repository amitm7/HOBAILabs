---
name: build-feature
description: >
  How to build and verify a feature in the HOBAILabs story→reel pipeline.
  Invoke before implementing any change to agents/, web_app.py, run_caption.py,
  the web UI, or config/. Encodes the architecture rules, the pluggable LLM/model/
  pricing seams, the safety/caching/cost conventions, and the mandatory
  compile→offline→live verify loop. Read this first so every build is consistent.
metadata:
  type: project
---

# Building a feature in HOBAILabs

The repo turns a plain-text story + assets into a 9:16 captioned MP4. Two entry
points — `web_app.py` (Flask UI) and `run_caption.py` (CLI) — build the SAME
`frames[]` list and call the SAME `agents/*` stages. Read `docs/HLD.md` and
`docs/LLD.md` once before a non-trivial change.

## Non-negotiable architecture rules

1. **One engine, many front doors.** New surfaces (e.g. a `/brand` page) are a
   mode flag + extra inputs into the SAME `_run_inner` / `agents/*`. Never fork
   the pipeline, never duplicate `_run_inner`, never copy `main.js` into a second
   file — share a mode-aware script. (See `docs/BRAND_PLAN.md` for the pattern.)
2. **Everything flows through the `frame` dict.** Stages read keys and write keys
   (see HLD §4). Add a field rather than threading new parameters everywhere.
3. **Vendor-pluggable on three independent axes — go through the seams:**
   - reasoning/vision/LLM → `agents/llm.py` `chat(...)` (NEVER call OpenAI/boto3/
     genai directly). Use `model_tier="reasoning"|"vision"|"fast"`; pass
     `json_schema=` for structured output; `fast` tier for bulk/QC calls.
   - image/video model choice → `agents/model_router.py` + `config/models.json`.
   - costs → `agents/pricing.py` + `config/pricing.json` (never hardcode a price).
   Adding a vendor/model/price is a CONFIG edit, not Python.
4. **Graceful degradation everywhere.** Every external call is wrapped so one
   failure falls back (premium→fallback→Kling→Ken Burns; lip-sync→still;
   smart-match→positional; any LLM pass→prior behaviour). A render must rarely
   hard-fail. New passes must be safe no-ops on error.
5. **Content-hash caches at every paid step** (`agents/cache_store.py` BlobCache,
   `agents/_kv.py`). Re-rendering the same input must not re-spend. If output
   semantics change, bump the cache-key version so stale entries miss.
6. **Safety lives in the generation layer, not the entry point.** Gate A
   (`safety.moderate_*`), Gate B (`safety.check_face_sanity`), Gate B2
   (`safety.critique_image`) run inside `agents/image_generator` so every caller
   (CLI, web, future) inherits them. Don't re-implement gates per route.
7. **Cost is server-truth.** `pricing.estimate()` is the single estimator; the UI
   calls `POST /api/estimate` and renders the result — do NOT reimplement pricing
   or routing in JS.
8. **No hardcoded sample content.** Subject is OPTIONAL; when absent, infer from
   the story. Never reintroduce stock defaults ("Surabhi", "Assamese woman", a
   fixed era/region). Infer setting/era/age/gender from the beat.
9. **Captions/timeline use effective timecodes.** Crossfade overlaps clips by
   `TRANSITION_DUR`; use `assembler.frame_timecodes(...)` for captions, lipsync
   adelay, ducking, voiceover slots — never raw cumulative durations.
10. **Fonts must be installed for libass.** New caption fonts: drop the TTF in
    `deploy/fonts/`, install via the Dockerfile + `fc-cache`, add the family name
    to the dropdown. Flag licensing (Montserrat OFL = bundle; Satoshi = drop-in).
11. **Docs are part of the feature — not optional follow-up.** A change is NOT
    done until the affected docs are updated in the SAME unit of work. This is a
    hard gate, equal to the verify loop. See the "Docs to keep in sync" table.

## Docs to keep in sync (update when the relevant thing changes)

| Doc | Update it when… |
|---|---|
| `docs/HLD.md` | Architecture changes: new module/agent, new front door/route class, new pipeline stage, a new cross-cutting concern, a new external service, or a decision worth recording in §6. |
| `docs/LLD.md` | Module internals change: a new/renamed function or its signature, a new `frame` dict key, a new cache + its key/invalidation rule, a new route in the `web_app.py` table, or a new "sharp edge" future-me must know. |
| `docs/OPERATOR_GUIDE.html` | Any USER-VISIBLE behaviour/UX change: a new button/field/toggle, a changed default, a new mode, a workflow change. Add it to the TOC too. |
| `GUIDE.md` | Same trigger as OPERATOR_GUIDE — keep the markdown user-guide aligned. |
| `docs/<NAME>_PLAN.md` | When a planned phase ships or scope shifts: tick the item, note what landed, move deferred work. |
| `config/*.json` comments / `.env.example` | When a new env var, model, price, or voice role is introduced. |

Rule of thumb: if you added a **route**, update LLD's route table. If you added a
**function/frame-key/cache**, update LLD. If you changed **architecture**, update
HLD. If a **user can see or click it**, update OPERATOR_GUIDE + GUIDE. Don't
hand-wave "docs later" — stale design docs are worse than none.

## Environment

- Interpreter: **`~/.pyenv/versions/3.12.3/bin/python3.12`** (flask/openai/etc.
  are NOT in the bare `python3`). Always run scripts/tests with it.
- Secrets in `.env` (load with `load_dotenv(".env", override=True)` in test
  scripts run from the repo root). Bedrock uses IAM, not a key.
- Hosted: single container (`Dockerfile`), gunicorn `-w 1 --threads 8`; in-memory
  run state keyed by `run_id`. Durable jobs/DB are future (SCALE_PLAN).

## Plan-first for big features

A multi-module feature gets a `docs/<NAME>_PLAN.md` BEFORE code (see SCALE_PLAN,
BRAND_PLAN, WORK_PLAN): locked decisions, data-model deltas, hard rules, phased
tickets, non-goals. Confirm scope/phasing with the user before executing.

## Build loop (per change)

1. Read the enclosing function(s) and callers/callees before editing.
2. Implement at the right altitude — generalize a mechanism over adding a special
   case; prefer a config/seam edit to new branching.
3. Keep new code degradable (try/except → fallback) and cached if it spends.
4. **Sync the docs (rule 11).** Walk the "Docs to keep in sync" table and update
   every doc the change touches — HLD (architecture), LLD (module internals/
   routes/frame-keys/caches), OPERATOR_GUIDE.html + GUIDE.md (user-visible UX),
   the relevant `_PLAN.md`. This is part of the change, not a later chore.

## Verify loop (always, before declaring done)

```
# 1. Compile every touched file
~/.pyenv/versions/3.12.3/bin/python3.12 -m py_compile <files...>
node --check web/static/main.js          # if JS touched
python3 -c "import json; json.load(open('config/<f>.json'))"   # if config touched

# 2. Offline logic tests (pure functions, no network) — assert the new behaviour
# 3. Live smoke IF a key is present (guard on os.environ.get("OPENAI_API_KEY") etc.)
#    — exercise the real LLM/render path on a tiny input
# 4. ffmpeg smoke for assembler/clip changes (build a lavfi test clip, probe duration)
# 5. End-to-end via Flask test client for route changes (web_app.app.test_client())
```

Report results honestly: if a step was skipped (no key), say so. Don't claim
"verified" without running the loop.

## Don'ts

- Don't `git commit`/push unless explicitly asked; if you must, branch off `main`
  first and end commit messages with the required Co-Authored-By line.
- Don't fork the pipeline or the UI for a new format — add a mode/template.
- Don't let AI author regulated ad claims (brand mode): copy is brand-supplied,
  verbatim, placed by the creator (BRAND_PLAN §5).
- Don't serve arbitrary filesystem paths — `/media` and asset paths are confined
  to `RUNS_DIR` / `ASSETS_BROWSE_ROOT` (`web_app._path_allowed`).
