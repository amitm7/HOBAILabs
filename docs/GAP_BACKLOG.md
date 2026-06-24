# HOBAILabs — Gap Backlog (missing capabilities register)

**Created:** 2026-06-24
**Companion:** [MARKET_FIT_REVIEW.md](MARKET_FIT_REVIEW.md) · [PRODUCT_IDEAS.md](PRODUCT_IDEAS.md) · [ROADMAP.md](../ROADMAP.md)
**Status:** prioritized register — informs roadmap, not a build spec

The prioritized "missing capabilities" that fall out of the
[Market-Fit Review](MARKET_FIT_REVIEW.md). This is the *floor-and-moat* register —
distinct from [PRODUCT_IDEAS.md](PRODUCT_IDEAS.md) (the creative/strategic wishlist).
The governing filter (from the review): **value is inversely proportional to how visible
the AI is on a real human** — a gap that hardens the commercial floor or protects
authenticity outranks a gap that adds creative surface.

Size: **S** ≤ a few days · **M** ~1–2 wk · **L** = a subsystem.
Priority: **P0** commercial floor · **P1** near-term · **P2** post-floor.

### Status (2026-06-24) — all P0 + P1 landed

| # | Gap | Priority | Status |
|---|-----|----------|--------|
| 1 | Operator auth / identity | P0 | ✅ shipped — `agents/auth.py`, JWT + roles, money/rights routes gated |
| 2 | Durable DB (Postgres) | P0 | ✅ shipped (config-driven) — `agents/db.py`, `HOB_DB_URL` switch; SQLite default, Postgres-ready. Migrating the legacy per-store SQLite bridges (run_store/governance) onto it is the mechanical follow-on for the RDS cutover. |
| 3 | Performance feedback loop | P1 | ✅ shipped — capture + read path (`GET /performance` leaderboard + summary) |
| 4 | Consent tied to subject + face/voice | P0 | ✅ shipped — `governance` likeness gate on `/run` |
| 5 | Authenticity labeling / provenance | P1 | ✅ shipped — `agents/provenance.py`, per-run `provenance.json`, UI badge, export |
| 8 | Vendor fallbacks on every axis | P1 | ✅ shipped — `config/models.json` fallback chains + `model_router.run_with_fallback` |
| 6 | Asset library / memory | P2 | ⬜ remaining (needs the RDS cutover, GAP #2) |
| 7 | Real dubbing / translation | P2 | ⬜ remaining |
| 9 | Real virality scoring on hooks | P2 | ⬜ remaining (candidate provider: higgsfield MCP `virality_predictor`) |

The original register and its rationale follow.

| # | Gap | Why it matters for HOB | Size | Priority | Acceptance ("done") | Cross-link |
|---|-----|------------------------|------|----------|---------------------|------------|
| 1 | **Operator auth / identity** | No auth today — anyone reaching the server can run, spend, and act as HOB. The commercial floor. | M | **P0** | Authenticated operator identity gates every run/spend/approval; actions attributable to a person. | [PRODUCT_IDEAS §Build deeply #6](PRODUCT_IDEAS.md) (governance hardening) |
| 2 | **Durable DB (Postgres) over SQLite stand-ins** | `run_store`, consent, approvals are hardened SQLite thin slices on `/tmp`; not durable multi-operator storage. | L | **P0** | Runs/consent/approvals on a durable shared DB; survives restarts and serves concurrent operators. | [SCALE_PLAN.md Phase 2](SCALE_PLAN.md) |
| 3 | **Performance feedback loop** | The platform optimizes output but is blind to what performed after publish. *Most-cited gap.* | M | **P1** | **Stub landed** — `runs.{performance_views,performance_likes,performance_note}` captured on the output panel via `POST /performance/<run_id>`; future work aggregates/correlates against the run payload. | [MARKET_FIT_REVIEW §2 Act](MARKET_FIT_REVIEW.md) |
| 4 | **Consent record tied to subject + face/voice** | Authenticity moat: synthetic real-person face/voice must be a consented, labeled exception, never a default. | M | **P0** | A consent record binds subject identity to the specific face/voice use before any real-person AI render; gate blocks otherwise. | [MARKET_FIT_REVIEW §4](MARKET_FIT_REVIEW.md) · [PRODUCT_IDEAS §Build deeply #6](PRODUCT_IDEAS.md) |
| 5 | **Authenticity labeling / provenance** | Trust depends on the audience knowing what is real vs. AI; provenance protects the brand and the subject. | S | **P1** | Output carries a provenance/authenticity label distinguishing real footage from AI-on-real-person. | [MARKET_FIT_REVIEW §4](MARKET_FIT_REVIEW.md) |
| 6 | **Asset library / memory** | Reusable HOB asset intelligence (stills, clips, voices, music, brand kits) compounds into a real moat over HOB volume. | L | **P2** | Generated + real assets stored, tagged, and matched to story beats; each reel gets cheaper and more on-brand. | [PRODUCT_IDEAS STR-7](PRODUCT_IDEAS.md) (needs the DB, GAP #2) |
| 7 | **Real dubbing / translation** | `/caption-variants` returns a `draft_scaffold` placeholder, not real language variants; ~5× reach for an Indian audience left on the table. | M/L | **P2** | One story → real regional voice + translated captions from already-rendered work, behind the commercial gate. | [PRODUCT_IDEAS STR-4](PRODUCT_IDEAS.md) |
| 8 | **Vendor fallbacks on every axis** | Single-provider dependence on most generation/voice axes; one outage or price move stalls production. | S/M | **P1** | Each generation/voice axis has a configured fallback that degrades independently. | [MARKET_FIT_REVIEW §3 (vendor concentration)](MARKET_FIT_REVIEW.md) |
| 9 | **Real virality scoring on hooks** | `virality_predictor` is referenced in docs but **not wired** into any code path; hook scoring is faked-by-omission today. | M | **P2** | `/hook-workshop` scores openers with a real predictor before full spend (candidate provider: higgsfield MCP `virality_predictor`); no fabricated scores. | [PRODUCT_IDEAS STR-5](PRODUCT_IDEAS.md) |

---

**On gap #3 (the one shipped here):** the stub is deliberately the *seed*, not the
product — structured numeric signal (`views`, `likes`) plus an optional free-text note,
stored on the existing run row so it can later be sorted and correlated against the run
payload. There is no aggregation, ranking, or auto-ingest yet; that is the P1 follow-up.
