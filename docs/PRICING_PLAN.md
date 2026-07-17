# PRICING_PLAN — credit model, ladder, admin portal (S32)

**Status:** RESEARCHED + PROPOSED (2026-07-17). Not decided, not built. This is the
decision record for the **SaaS** product (Veristory), not HOB's internal tooling.
Nothing here ships before the concierge pilot (§7) returns real numbers.

---

## 1. The competitor, decoded from their own code

galleri5 AI Studio's shipped JS bundle hardcodes **`PRICE_PER_CREDIT = 1`** — flat ₹1/credit
at every scale, no volume discount (Starter ₹2,500 = 2,500 cr; Growth ₹40,000 = 40,000 cr,
exactly linear). Top-up packs: 500 / 1,000 / 2,500 / 5,000. Their per-model menu (credits = ₹):

| Model | Their price | Our COGS (`config/pricing.json`) | Implied markup |
|---|---|---|---|
| Seedream | ₹20 | ~₹1 | **~20×** |
| Nano Banana | ₹15 | ~₹5 | ~3× |
| Nano Banana Pro | ₹22–40 | ~₹5 | 4–8× |
| Flux 2 Pro | ₹12 | ~₹4 | ~3× |
| Remove-BG | ₹3 | — | — |

Video credit rates are computed **server-side** (`estimateModelCredits`) and not published.
Positioning: *"India's #1 Cinematic AI Studio"*, launched at the India AI Impact Summit
(Feb 2026) by Collective Artists Network; proof points Mahabharat (Star Plus) + Chiranjeevi
Hanuman (IMAX). **No press coverage mentions pricing** — it exists only on-site.

**Read:** ₹2,500 buys ~125 images or a handful of clips and **no finished output**. It is a
taste tier; real video work forces top-ups at ₹1/credit.

## 2. Global benchmarks (11 platforms, July 2026)

- **Median mid-tier effective price:** **$0.01–0.04/image**, **$0.30–0.37 per 5s video**.
  Premium routes (Veo 3.x, 4K Kling, Gen-4.5) are a separate band at ~$1.50–2.60/5s.
- **Markup norm: 2–4× raw model cost** → **50–60% gross margin** (Bessemer/a16z AI-app
  benchmark; inference is 20–23% of spend per ICONIQ). Classic SaaS 80–90% does not apply.
- **Expiry is near-unanimous:** subscription credits die at cycle end (Runway Std/Pro,
  Kling, Hedra, Higgsfield, Pika, Krea, LTX, OpenArt — whose *own FAQ* contradicts its
  "rollover" marketing). Exceptions: HeyGen (+1 month), Leonardo (3-month Rollover Bank),
  Freepik/Magnific (1-year pool). **Purchased packs live longer everywhere** (Kling 2 yrs,
  Hedra/OpenArt persist, Krea/Higgsfield 90 days). galleri5's "never expire" is genuinely
  rare — and OpenArt sells add-on credits at ~$3/1,000, far under its in-plan rate, so
  **discounting packs is normal practice galleri5 has left on the table**.
- **Modal team shape:** per-seat fee + **one shared workspace credit pool**, ~2-seat
  minimum (Freepik $55/seat min 2; Runway pooled; Leonardo ~$24/seat, 3-seat min).
  Anti-pattern to avoid: HeyGen Business, where extra seats add **no** credits.
- **The cheap-image / expensive-video gradient is universal and steep:** Runway's own Apps
  run $0.05/image (Reshoot Product) vs $3.20/video (Add Dialogue) — **64×**.

## 3. India mechanics (hard constraints)

- **RBI e-mandate: recurring debits above ₹15,000 need AFA/OTP every cycle.** Category
  exemptions (MF/insurance) do NOT cover SaaS. → galleri5's ₹40,000 Growth **cannot
  silently auto-renew**. Any self-serve tier must sit **≤ ₹15,000/mo**. This is a
  mechanical advantage, not just a price cut.
- **GST 18%** on SaaS (OIDAR). B2B buyers reclaim it as input credit — surface it.
- **Stripe India is invite-only** → Razorpay. Subscriptions via UPI Autopay; **credit packs
  as one-time payment links** (no mandate needed).
- **The ₹499–₹999 rung is vacant for *finished output*.** Nothing credible sits between
  Canva Pro (₹499, no real video-gen) and the ₹2,000–3,000 AI-video band (invideo ~₹2,075,
  Steve AI ₹2,399, galleri5 ₹2,500). **Caveat:** Freepik→**Magnific geo-prices India** —
  Premium ₹910–1,340/mo, Premium+ ₹2,250–3,000 with **unlimited generations on 8 models
  incl. Kling 2.5 video**. On *raw generations* that rung is not vacant. It is vacant only
  for finished, edited output — which is why §4 sells reels, never credits.
- Annual discount norm: **25–30%**.

## 4. Proposal — same unit, three deliberate deviations

**Adopt 1 credit = ₹1** (galleri5 has already educated the market; matching the unit makes
"more for less" legible) and a **fixed credit menu per action × model**, derived
line-by-line from `config/pricing.json` at ~3× COGS. The 💰 estimator already computes
exactly this — the menu is a config seam, not Python.

| Tier | ₹/mo ex-GST | Credits | Position |
|---|---|---|---|
| Free | 0 | **unlimited mood boards + 1 finished reel** | see §5 |
| Creator | **999** | 1,200 | the vacant rung — 60% under their entry |
| Pro | **2,499** | 3,500 | their Starter's price, **+40% credits** |
| Studio | **12,499** | 20,000 + 5 pooled seats | **auto-renews silently; their ₹40k can't** |

- **Deviation 1 — break their linearity.** More credits per rupee as tiers rise; packs
  discounted (₹0.90/cr at 5k, ₹0.85 at 10k) and **never expiring**.
- **Deviation 2 — expiry as marketing.** Credits never expire while subscribed; packs never
  expire; 90-day grace after cancellation. ⚠️ Never-expiring credits are a permanent
  deferred-revenue liability (Ind AS 115 / ASC 606 — breakage only recognisable when
  consumption becomes "remote"). **Agree the policy with a CA before launch**; the fallback
  is roll-over-once-then-expire.
- **Deviation 3 — denominate in reels, not generations.** They sell ingredients (₹15/image).
  We say *"a finished reel ≈ 400–500 credits"*. **This is the whole strategy:** on raw
  generations Magnific gives near-unlimited images + Kling video for ~₹2,250 — nobody wins a
  ₹/generation war against unlimited bundles. Do not fight there.

**COGS reference** (from `config/pricing.json`): dev reel **₹120–180** · prod reel
**₹300–450** · podcast episode **₹900–2,200** · **mood board ~₹10** · storyboard panel ~₹1.

**The line:** *galleri5 sells 2,500 credits of raw generations for ₹2,500. We sell five
finished, captioned, scored reels for ₹999.* Same currency, different unit of value.

## 5. The free tier (this is the wedge)

**Not "300 credits"** — that doesn't even cover one reel, and credit-count comparisons are
a game we lose to a competitor subsidised by Collective Artists Network. Instead:
**unlimited mood boards + 1 finished reel** ≈ **₹150–200 COGS/user**, with a *shareable
artifact* as the hook (see `docs/CANVAS_ENTRY_PLAN.md` §4 — Mood Board = canvas stages 1–2,
stopped and exported; ~50× cheaper than a reel). Runway gives pre-production artifacts away
**free nowhere** (125 one-time credits, no free tier). Don't compete on credits; compete on
artifacts.

## 6. Stack

**Razorpay** (payments) + **own Postgres double-entry ledger** + **config-driven credit
menu** + **Zoho Billing** (GST invoices only — its wallet semantics are shallow). Graduate
to **Flexprice** (open-source, India-built, native Razorpay, credit wallets w/
expiry+rollover) or **Lago** (mature OSS, prepaid wallets; Razorpay connector unconfirmed)
when scale hurts. Chargebee/Metronome/Orb are enterprise-priced — premature.

### Admin portal MVP
1. Immutable per-workspace ledger: every debit w/ `run_id`, action, model, units, credits,
   acting user, **idempotency key**, grant source.
2. Wallet buckets (subscription / purchased / promo) + expiry + FIFO burn order.
3. Manual grants/adjustments w/ mandatory reason code + audit trail; promo flag (contra-revenue).
4. Usage analytics **+ margin view** (credits burned vs vendor cost — `pricing.json` knows both).
5. Plan overrides / per-workspace rates / spend caps / trial extension.
6. Abuse: velocity flags, trial-farming detection, one-click freeze.
7. **Void/refund a failed generation** (auto-refund failed renders; admin override).
8. CSV export for deferred-revenue accounting.

### Engineering pitfalls (with fixes)
- **Concurrent debits.** Naive read-check-write provably loses updates. → append-only
  double-entry, debit inside a DB transaction w/ row lock (or optimistic lock + bounded
  retry), **idempotency key per generation**, non-negative balance as a **DB constraint**.
- **Long async renders → negative balances.** → **hold-then-settle**: reserve estimated
  credits at job start, settle actuals on completion, release on failure. *We already have
  this shape* — `governance.reserve_spend` / `release_reservation`, just denominated in ₹.
- Define burn order (FIFO by expiry, promo before paid) up front — it changes rev-rec.
- Cost-model before fixing the menu; revisit quarterly (credits re-rate without contract change).

## 7. Sequence — do NOT build billing first

1. **Concierge pilot, zero code:** 5–10 creators, ₹999/₹2,499 via Razorpay payment link,
   provision by hand. The existing per-run cost ledger measures true COGS/reel.
2. **Validate two assumptions:** real COGS per finished reel, and willingness-to-pay at ₹999.
3. Only then build accounts → ledger → self-serve.

**Blocking gaps before taking money** (see also `docs/CANVAS_ENTRY_PLAN.md`):
- 🔴 **No tenancy.** `run_store` is one namespace; `list_canvases` returns every run to
  whoever is logged in. Pilot workaround: one instance per customer.
- 🔴 **Likeness abuse.** Strangers uploading *other people's* faces. Minimum: rights
  attestation logged per upload + working disclosure label (S31 pre-flight #0 — **fixed
  2026-07-17**) + weekly human review.
- 🟡 Vendor commercial terms (Suno/ElevenLabs/fal plan-dependent) — verify before invoicing.
- 🟡 Per-account spend containment (gates are per-*run* today).
- 🟡 ToS / privacy / refunds / GST invoicing / content-ownership clause.

## 8. Reversal conditions

- Pilot COGS per finished reel **> 2×** §4's estimates (likely culprit: re-roll behaviour)
  → re-price or cap re-rolls before public launch.
- fal pricing moves > 2× → the whole COGS advantage re-derives.
- galleri5 drops ₹1/credit or ships a free finished-output tier → re-read; their pricing is
  ecosystem-subsidised and *not* cost-derived (their own Growth tier implies a credit rate
  ~16× better than Starter — marketing, not economics).
- **Do not raise on this.** Pre-revenue, pre-retention, solo, in the category investors
  read as thin-moat. Every pilot datapoint multiplies the same pitch. Trigger to revisit:
  paying users who **renew** + one repeatable channel.
