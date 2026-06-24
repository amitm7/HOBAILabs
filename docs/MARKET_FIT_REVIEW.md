# HOBAILabs — Market-Fit Review (OODA)

**Created:** 2026-06-24
**Companion:** [GAP_BACKLOG.md](GAP_BACKLOG.md) · [PRODUCT_IDEAS.md](PRODUCT_IDEAS.md) · [ROADMAP.md](../ROADMAP.md) · [HLD.md](HLD.md)
**Status:** strategic review — informs roadmap, not a build spec

This is a world-class-reviewer OODA pass over the platform as it stands on `main`.
It captures a verdict, the OODA loop behind it, a pros/cons read, and the one thesis
that should govern roadmap priority. The prioritized list of *missing capabilities*
that falls out of this review lives in its companion, [GAP_BACKLOG.md](GAP_BACKLOG.md).

---

## 1. Verdict

The engine is genuinely strong. HOBAILabs is **valuable as a production tool** — it
turns a story into a finished, on-brand reel at real throughput and with disciplined
spend governance. That value is real today.

It becomes **dangerous the moment it touches HOB's authenticity moat** — synthetic
portraits or voices of named real people. The governing line for every roadmap call:

> **Value is inversely proportional to how visible the AI is on a real human.**

AI on symbols, sets, motion, and assembly: high value, low risk. AI rendering a real
person's face or voice: low marginal value, existential brand risk.

The two gating risks are therefore **foundation** (it is still a prototype under
commercial use — SQLite stand-ins, no operator auth, no feedback) and **authenticity**
(the one thing that, done wrong, can't be undone).

---

## 2. OODA

### Observe — what's actually built vs. thin (grounded)
- **Three front doors** in `web_app.py`: `/` (story), `/brand` (ad), `/studio`.
- **Restart-safe runs** via `agents/run_store.py` — but on **SQLite stand-ins**
  (`/tmp/hob_runs.db`), explicitly a thin slice, not a durable multi-operator DB.
- **No operator auth / identity** — anyone reaching the server can run, spend, and act.
- **A thin test suite** — 18 test functions in `tests/test_core_behaviour.py`.
- **No post-publish feedback loop** — the platform optimizes *output* (hooks, captions,
  cost) but has **no idea what performed** after a reel ships. This is the single
  most-cited gap; a structured stub (`runs.performance_*`) lands with this review.
- Several routes are honest **bridges, not products**: `/caption-variants` returns a
  `draft_scaffold` placeholder, `/hook-workshop` is a no-score draft, `virality_predictor`
  is referenced in docs but **not wired** into any code path.

### Orient — the engine is ahead of the platform
The creative engine (director brain, shot planning, assembly, editor hand-off) is more
mature than the platform under it (identity, durable storage, governance, measurement).
The crux is **authenticity**: the moat isn't the generation — those are bought primitives —
it's HOB's real people, real footage, and the trust that comes with them.

### Decide — floor and moat before more creative surface
Stop widening the creative surface until the **commercial floor** (auth, durable DB,
consent tied to subject, provenance labels) and the **measurement loop** exist. More
creative features on an ungoverned, unmeasured prototype compound risk faster than value.

### Act — the near-term moves
1. **Operator auth / identity** — the commercial floor (GAP #1).
2. **Money/rights tests** — consent tied to subject + face/voice, provenance labels (GAP #4, #5).
3. **A feedback signal** — ship the performance-capture stub (GAP #3, landed here).
4. **Re-shoot the Lalita flagship** off AI-portraits-of-real-people and onto the
   symbolic + real-photo-animation hero path (see §4).

---

## 3. Pros / Cons

**Pros — what's genuinely strong**

| Strength | Why it matters |
|---|---|
| Right altitude | A thin, opinionated orchestration layer over bought primitives — not a me-too model wrapper. |
| Throughput | Story → finished reel end-to-end, fast enough to matter for HOB volume. |
| Cost discipline | Spend reservations + a commercial gate; test-cheap / finish-expensive is baked in. |
| Early governance | Consent ledger, approval rows, restart-safe runs exist *before* scale forced them. |
| Modular engine | Director brain / shot planner / assembly are separable and individually improvable. |
| Editor hand-off | Export to clips + edit list / FCPXML — "best first-draft machine," not a second-rate NLE. |

**Cons — what gates it**

| Risk | Why it matters |
|---|---|
| Structural authenticity tension | Synthetic real-people portraits/voices attack the one irreplaceable asset. |
| Prototype foundation under commercial use | SQLite stand-ins + no auth are fine for a prototype, not for paying use. |
| Flying blind | No feedback loop — output is optimized, performance is invisible. |
| Sameness risk | Heavy reliance on the same generation primitives trends toward a recognizable "AI look." |
| Vendor concentration | Single-provider dependence on most generation/voice axes; no fallbacks. |
| Drafts-as-products | Several routes are labelled bridges; treating them as finished oversells the platform. |

---

## 4. The authenticity thesis

`ai_portrait` and synthetic-voice of **named real people** attack HOB's one
irreplaceable asset — the authenticity of real humans the audience trusts. No amount of
fidelity makes a synthetic Lalita *more* authentic; it can only erode the real thing.

The hero path should invert visibility of the AI:

- **Make `ai_symbolic` + real-photo-animation the default hero path** — AI on symbols,
  environments, motion, and assembly, with real footage preserved untouched.
- **AI-on-a-real-person becomes a consented, labeled exception**, never the default —
  gated by an explicit consent record tied to the subject (GAP #4) and an authenticity /
  provenance label on the output (GAP #5).

This thesis is the filter for the companion [GAP_BACKLOG.md](GAP_BACKLOG.md): a gap that
hardens the floor or protects authenticity outranks a gap that adds creative surface.
