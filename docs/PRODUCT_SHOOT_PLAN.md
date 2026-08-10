# S34 — AI Product Photoshoot (fashion SKU → campaign stills)

> Mannequin photos of a fashion SKU in, a multi-shot campaign out — same face on every frame,
> styled, checked, cropped, named and filed, from a watched folder, with a human involved twice.
> **Status: MVP COMPLETE (2026-08-10)** — browser front door `/shoot` shipped.
> Previously IN-PROGRESS (2026-08-09) — P0 substantially done against 4 real agency SKUs.
> Shipped: `tools/shoot_probe.py` (multi-reference + aspect facts), `tools/shoot_bakeoff.py`
> (4 rounds, model + prompt scorecard), `tools/shoot_campaign.py` (multi-frame campaign +
> drift scoring), `config/shoot_stylists.json` (12 genre packs), `seedream_edit` in
> `config/models.json`, and an `agents/llm.py` fix for silently dropped content parts.
> ❓ marks a decision still open.
>
> **Where quality stands (nano_banana_edit, 4 SKUs, ~$2 total spend):**
> subject integrity 8/8 usable · colour 4.50 · construction 4.25 · environment 5.00 ·
> craft 3.75 · **texture 3.50** · **face realism 3.00**. Campaign continuity over a 6-frame
> set: location 5.00, hero 5.00, person 4.80, styling 4.00.
>
> **The two open quality gaps, both structural rather than promptable:**
> 1. **Texture** — the coarse open knit renders smooth on every model tested. This is what the
>    virtual-try-on round is for (§13.4): transplant garment pixels rather than re-imagine them.
> 2. **Face realism** — asking explicitly for pores, asymmetry and iris detail did NOT work;
>    all 8 frames still read poreless and glassy-eyed. Prompting is the wrong layer. The fix is
>    the **L0 persona pool** (§5): mint faces once, curate hard, condition every SKU on the
>    chosen images. Face quality becomes one-time curation instead of a per-frame lottery.
>
> **Prompt invariants learned live** (all now in `tools/shoot_bakeoff.py`): the mannequin is not
> the subject; hero/styling slots must fill every body region; "no legible text" ≠ "no interesting
> environment"; pose and camera are spec, not prose; anatomical wording next to a stated age trips
> vendor content filters — use photographic vocabulary; and **the anchor cannot pass on what it
> does not show** (shoes hidden in the anchor were re-invented in later frames).
>
> **Tag reads are not authoritative for product type.** The blue SKU's tag read as
> "Sweater: 100% polyester" when the garment is jeans — which mis-slotted styling AND routed to
> the wrong stylist pack. Fixed by cross-reading the garment photo with the tags: the photo wins
> on what the item *is*, the tag wins on composition and size.
>
> **Round-1 findings (2026-08-08, 4 real agency SKUs, $0.27):**
> nano_banana_edit 4/4 ran, seedream_edit 3/4 (one content-policy refusal), flux_kontext 0/4
> (rejects `aspect_ratio:"4:5"` as a literal). **The scorecard is NOT trustworthy** — the QC
> scorer rated a human/mannequin chimera with no bottoms `construction 5/5`. Three prompt-level
> defects found, all mine, none the models': (a) no "the reference is a mannequin, not the
> subject" instruction, so seedream copied the mannequin's plastic legs into the output;
> (b) no hero/styling slot rules, so a top rendered with no bottoms at all; (c) the anti-text
> stage rule over-corrected into visually bland backgrounds. See §12 for the new
> `subject_integrity` QC axis these produced.

---

## 1. Verdict — where this lives

**Same repo. New vertical, not a new project, not a fork of the reel pipeline.**

`agents/shoot.py` + front door `/shoot`. It may call `agents/*` seams (router, cache, pricing,
upscaler, safety, provenance, auth, degradation, source-media review) and **must not** import
`web_app` internals or the reel pipeline. That boundary keeps a later extraction a `git mv`.

`ponytail:` one module, not a package — split when a file gets hard to read, not before.

Why not a new project: ~70% of what this needs already runs here —

| Need | Exists | File |
|---|---|---|
| Generation conditioned on several references | `edit_image(list_of_refs, prompt, out)` | `agents/image_editor.py:43` |
| **Reusable person library** (→ Persona pool) | `register_talent` / `get_talent` | `agents/product_surface.py:262` |
| **Location anchoring + empty plates** (→ Stage pool) | S30 Locations sheet, shipped | `docs/S30_ADOPTION_PLAN.md` |
| **"Same person?" check + re-roll** | Gate B3 likeness QC | `agents/image_generator.py` |
| Per-image critique + re-roll | Gate B2 vision QC | `agents/image_generator.py` |
| Reject unusable input before spend | source media review | `agents/source_media_review.py` |
| Moderate operator-supplied text | `safety.moderate_*` | `agents/safety.py` |
| Upscale that invents nothing | `upscale_file(creative=False)` → `aura_sr` | `agents/upscaler.py:46` |
| Vendor failover, routing, concurrency caps | `model_router` + `fallbacks` | `agents/model_router.py` |
| Cost estimate before spend, spend caps | `pricing.py`, governance gates | `agents/pricing.py` |
| Free re-runs | prompt + ref hashing | `agents/cache_store.py` |
| Silent-failure ledger | `degradation.report()` | `agents/degradation.py` |
| White-label login, per-brand scoping | JWT operators + `owner` | `agents/auth.py` |
| Signed provenance | C2PA content credentials | `agents/content_credential.py` |

Separate vertical rather than a fourth reel mode: different unit of work, different frame,
unattended at hundreds of SKUs/day, no video or audio.

❓ Same repo (recommended) vs its own product entity — branding/billing, not technical.

---

## 2. Domain model

```
Brand        → one config: persona_spec (locked), wardrobe, stage pool, destinations,
                aspect + DPI, spend caps, naming template
PersonaSpec  → the brand's model brief, locked at onboarding
Persona      → one synthetic model on-spec, stored as IMAGES (face sheet). Pool of N. §5
Stage        → a location × time-of-day. Pool of 3 per collection. Time of day rides
                with the stage — it is never a per-SKU decision.
SKU          → 1..N input photos in one folder (front / side / back / mannequin / detail)
Audience     → adult | kids — routes the whole shot set. §7
Detection    → {category, product_type, audience, silhouette, genre, colours, fabric,
                has_text_logo, presentation, person_present, apparent_minor,
                multi_product, folded, set_pair, clutter, input_quality}
Campaign     → persona + stage + pose set + styling + N shot specs, locked together
Shot         → {code, destination, aspect, coverage%, crop_range, ref_slots, pose,
                hero_emphasis, rule flags, derive_from}
Destination  → d2c | marketplace
Job          → (brand, sku, input_hash, config_version) → one ledger row
```

---

## 3. Pipeline

```
ingest → detect → cast → stylise → shoot → QC → deliver
```

**Ingest** — hash the SKU folder, dedupe against the ledger, register photos by content SHA, and
run `source_media_review` on them. A blurry input burns a whole campaign's budget before anyone
looks; below the floor → `NEEDS_INPUT`, never generated.
`ponytail:` triage is one existing call inside ingest, not its own stage.

**Detect** — ONE vision call. No YOLO, no rembg, no segmentation model: category, silhouette,
multi-product, folded, clutter and logo-presence are judgement calls one model makes in a single
pass, and clutter is handled by conditioning rather than matting because the scene is re-rendered
anyway. It also decides **audience** and flags whether a real person — specifically an apparent
minor — is in the input. That is a hard routing gate (§7), not advisory.

`presentation` (flat-lay / mannequin / on-body) is **recorded, not branched on** — the reference
slots are the same today. Branch when the bake-off proves flat-lay needs different handling.

Ambiguity is a **job state** `NEEDS_INPUT`, not a modal dialog: web mode asks, batch mode parks and
moves on. One code path. Audience ambiguity resolves **downward** — uncertain means the no-person
path, never the model path.

**Cast** — assign the persona and the stage deterministically from the pools (§5).

**Stylise** — §4. **Shoot / QC / Deliver** — §6, §8, §9.

---

## 4. Stylist — constrain the choice, don't trust the taste

The LLM is genuinely strong at writing a coherent campaign brief from a garment, at colour
harmony, and at internally consistent outfit choices. It is weak in four ways that matter:
it has no feedback signal, so it produces *plausible* rather than *performant* styling; its priors
are Western editorial; brand taste is not derivable from one garment photo; and — the sneaky one —
**four hundred independent calls converge**, so the catalogue reads as AI-made because the
distribution is narrow, not because any single frame is bad.

So the design shrinks the choice space until a mediocre choice is impossible:

1. **Choose-from, not invent.** The stylist selects from enumerated brand-approved options rather
   than free-writing. Constrained selection is far more reliable than open generation, and it makes
   the result auditable and repeatable.
2. **Derive what's derivable.** Supporting-garment colour computes from the hero's detected
   dominant colour (neutral or complementary, lower saturation than the hero) — arithmetic, not
   taste. Which slot to fill comes from `product_type`. Tuck-in is a rule. Time of day rides with
   the stage. The LLM handles only what is left.
3. **Genre stylist packs — `config/shoot_stylists.json`. SHIPPED 2026-08-09.**
   Twelve packs: everyday, formal, ethnic_indian, festive, activewear, sportswear, loungewear,
   denim, swim_resort, winterwear, streetwear, luxury. Each carries the genre's **point of view**,
   a **3-location stage pool**, lighting, styling vocabulary, pose energy, and an explicit
   `avoid` list. Selection is longest-keyword-match over the product description, falling back to
   `everyday`; the stage is then picked from the pool by SKU hash, so a collection is coherent
   without every SKU looking identical.

   *This reverses the ponytail-audit call.* The audit cut the registry as YAGNI — "one pack for
   one brand; a registry lands when a second theme exists." An agency serving many brands across
   many categories **is** that second theme, and the requirement is real: gym wear photographed in
   a Mediterranean courtyard is simply wrong direction. The `avoid` list is what makes a pack
   worth more than a prompt — it encodes what is *wrong* for a genre, which no amount of positive
   description supplies.

   Verified live: 13/13 test descriptions routed to the intended pack; the denim SKU produced a
   raw-brick industrial loft while the co-ord SKU produced a Mediterranean courtyard, from the
   same code path.

   Per-brand wardrobe overrides (specific approved footwear/accessories) still land at onboarding
   — the pack is the genre default, the brand pack is the override.
4. **Anti-repetition is code, not prompt.** Deterministic rotation by SKU hash across the
   enumerated sets — the same mechanism assigning personas and stages. Never ask the model for
   variety; it cannot see across calls.

The spec is shown **editable before any spend**. The stylist produces a good default, not a final
answer.

**Test it before building the shooter.** In P0, run the stylist over 20 SKUs, text only, no images,
and put the specs in front of whoever owns the brand's look: *would you shoot this?* Accept rate is
the answer. Costs zero generation spend and runs in minutes.

---

## 5. Persona — one spec per brand, one face per SKU

```
persona_spec (locked at onboarding)  →  mint N face sheets, one batch, offline
                                     →  persona = pool[ sha1(sku) % N ]
                                     →  one SKU = one face on every frame of it
```

Faces repeat freely across SKUs; global uniqueness is not required. Within a SKU the face never
moves. Assignment is deterministic, so re-shooting a SKU next season returns the same person.

**Why a pre-minted pool, not a face minted per SKU**: N face sheets ever instead of one per SKU;
reproducible next season (mint-on-arrival cannot be — the vendor's model moved underneath you);
the base cache stays finite; and **the brand approves its faces once, in one sitting**, instead of
an unreviewed synthetic person going live every few minutes.

**Pool size is a catalogue question.** Base cardinality is `N × stages × poses` = `50 × 3 × 2` =
300 per collection, so reuse is `SKUs / 300`: 3,000 SKUs → ~10×; 1,500 → ~5×; 300 → ~1×, meaning
the cache never warms. Default N = 50, but it belongs in brand config with a rule of thumb
**N ≈ min(50, SKUs / 10)** — a 500-SKU brand is better served by 20 faces.

### 5.1 The identity contract

1. **The persona is images, not prose.** A face sheet survives a vendor updating or deprecating a
   model; a paragraph regenerates a different person.
2. **An invariant clause** — face geometry, hair, eye colour, skin tone, undertone, texture,
   proportion — injected verbatim into every prompt. Same mechanism as the S30 location clause.
3. **Model locked per campaign, not per shot.** Every vendor renders faces differently. This is the
   one place "let the AI pick the best model per frame" is the wrong instinct.
4. **Likeness QC re-rolls a stranger.** Gate B3 already does this — point it at the face sheet.

**Skin texture** breaks quietly: creative upscalers and beautify passes invent pores on one frame
and smooth them on the next. Faithful upscale only (`aura_sr`, never `clarity`), no beautify, ever.

**What "100% consistent" honestly means.** A generative model cannot be guaranteed not to drift.
Three things can be:

- **Derived frames are the same pixels** — `front_1` is a crop of `front_2`, `detail` a crop of the
  front. Identical by construction. A second reason to derive rather than regenerate.
- **Every generated frame inherits one L2 base image**, byte-identical across the set.
- **Nothing ships without passing the likeness gate.**

The promise is *every delivered frame passed the identity check* — not *the model never drifts*.

### 5.2 Spec governance — checked at onboarding, not at 3am

The persona spec is operator-written text sent to an external vendor on every generation. Validated
**once, where a human is present**:

- **Moderation pass** (`safety.moderate_*`), body descriptors normalised to neutral spec-sheet
  language. Operational, not editorial: this repo already documents a vendor answering a flagged
  phrase with `422 content_policy_violation` and falling back **with no face conditioning at all**.
  One unlucky word ships hundreds of campaigns with drifting faces and no error.
- **Age floor** — below 18 rejected at ingest; the declared age is stored and recorded.
- **Provenance** — the pool is synthetic, no real likeness, so the consent gate does not apply.
  Persona ID and spec version are recorded; frames are C2PA-signed **at delivery, batched**
  (per-frame signing makes an outbound timestamp call per image — 1,800/day).

---

## 6. The asset ladder

| Layer | What | Key | Minted |
|---|---|---|---|
| **L0 Persona pool** | N face sheets, all on-spec | `sha(spec + variant_seed + model + version)` | Once at onboarding |
| **L1 Stage pool** | Empty plates — 3 locations × time-of-day | `sha(location_spec + tod + model)` | Once per collection — **P2** |
| **L2 Base** | The SKU's persona in its stage, per pose — the anchor | `sha(persona + stage + pose + model)` | Once per combination |
| **L3 Shot** | The SKU worn, styled, framed | `sha(base + sku_input_hash + shot_code + styling + rules_version)` | Per SKU |

`ponytail:` L1 exists only to build L2 and pays off at 50 personas × 3 stages. For a single-SKU P1
it is a paid generation with zero reuse — generate the base directly; add L1 in P2 with the pool.

**What it saves**, honestly: no persona or stage established per SKU; the anchor becomes a cache hit
once the pool is warm; and re-rolls collapse, because identity drift causes most of them and a
re-roll is a wasted paid frame.

**Biggest single win is cropping, not caching.** Generate 4, derive 2 — a third fewer paid frames
*and* the two front shots match by construction. ~7–8 paid generations per SKU naive → ~4.

**Stage assignment**: `pool[sha1(sku) % 3]`, 2 pose sets. `ponytail:` no adjacency constraint — it
needs listing order the pipeline never sees, and the hash already scatters.

---

## 7. Audience — kidswear never touches the model path

**The system never generates a synthetic child.** Three independent reasons, any one sufficient:
vendors restrict minors in generated imagery and the refusal is *silent* in this stack, so the
failure mode is unusable frames and no error; AI imagery of minors carries heightened,
jurisdiction-varying legal scrutiny regardless of intent; and a brand publishing AI-generated
children is a headline, not a support ticket.

```
audience = adult  → L0 Persona → L2 Base → L3 garment on model
audience = kids   → NO L0, NO L2.  L1 becomes a surface / backdrop plate.
                    shots: ghost_packshot · front_flat · back_flat · styled_flat · detail
```

This is what kidswear already does without models — **ghost mannequin** (garment on a child form,
form removed so it holds its 3D shape) and **styled flat-lay**. Both are edits of the real garment
photo, not generation of a person, which is this repo's real-media rule where it matters most.
Marketplaces want a plain-background primary image anyway, so the ghost-mannequin packshot **is**
the listing image. Kidswear skips two layers, so it costs *less* — ~3 paid frames.

**Two hard gates, at ingest:**

1. **One age threshold, no case-by-case judgement.** Below the brand's declared adult floor → the
   no-person path. Covers the 14–16 gray zone by construction.
2. **A real person in the input is passthrough only.** A licensed, guardian-consented child model —
   the brand's responsibility, recorded as an asset flag — is enhanced and cropped, never
   regenerated. Applies to *any* on-body input, not only kidswear.

---

## 8. Hero vs styling

> **AI never invents the hero. AI may invent the styling. Styling is unbranded and recorded.**
> (mirrors BRAND_PLAN §5, which forbids AI-written brand claims)

Two real risks it closes: an invented item picking up a visible logo (trademark), and a customer
believing the styling is part of what they bought (returns).

- **Hero** — always conditioned on the real product photos, sharpest and best-lit, strongest
  contrast against the background. `hero_emphasis` is a per-shot directive.
- **Styling** — palette derived from the hero, never competing in saturation, prompted unbranded,
  QC-checked for stray logos.
- **Recorded** — `_campaign.json` lists hero and styling separately.

| Hero | Styling added | Rule interaction |
|---|---|---|
| Top | bottom, footwear, accessories, optional outerwear | hero untucked unless the theme says otherwise |
| Bottom | top, footwear, accessories | **top tucked in on every shot except lifestyle** |
| Dress | footwear, accessories, optional outerwear | outerwear must not cover the hero silhouette |
| Set / nightsuit | footwear, accessories only | both pieces are hero; two detail shots |
| Footwear | the whole outfit | framing drops to lower body |

---

## 9. Destinations, shots and output spec

A destination is a stage plus a shot-rule set — not a second pipeline.

| | D2C site | Marketplace |
|---|---|---|
| Stage | the collection's location pool | `studio_seamless` — another L1 plate |
| Shots | front_1, front_2, back, side, lifestyle, detail | **packshot**, front, back, side, detail |
| Framing | editorial, per the coverage table | centred, generous margin, silhouette never cropped |
| Aspect | 4:5 default, configurable | per-platform, configurable |

Frame accounting per SKU: **generate** front_2 (full), back, side, lifestyle; **derive**
front_1 (crop of front_2) and detail (crop, or a real macro crop of the product photo).

Coverage percentages (70 / 40 / 90), crop ranges and the tuck-in rule vary by
`(product_type, audience)` — rows in `config/shoot.json`.

**4:5 at 2K, 300 DPI.** Generate near 2K, upscale faithfully to ≥ 2048×2560, write the DPI tag.
DPI is metadata; pixels are quality. Extra aspects derive by crop where the coverage rule survives,
regenerate only where it does not.

---

## 10. Model selection

Selection happens **once per campaign** — identity requires one model for the whole set — from the
detection result and brand policy. Three independent backends:

| Backend | Models | Key |
|---|---|---|
| fal | `nano_banana_edit`, `flux_kontext`, Seedream edit, a VTON endpoint | `FAL_API_KEY` |
| Google direct | Gemini image edit — **no fal in the path** | `GEMINI_API_KEY` |
| OpenAI | `gpt-image-1` edit | `OPENAI_API_KEY` |

Honest caveat: fal's Nano Banana and Gemini-direct are the *same underlying model* — that pair
survives an account problem, not a model problem. Real diversity means alternating families, as
`config/models.json` already does for video.

`ponytail:` the bake-off's output is a winner plus a fallback, expressed as routing lanes in
`config/models.json` — not a runtime scorecard with a threshold solver over four rows. The scorecard
is the human-readable bake-off result. Revisit when there are enough models that choosing is
non-obvious.

`ponytail:` the multi-reference upgrade to `gpt_image_edit` waits — it is the third fallback behind
two live vendors. Do it when both fail, not on the path to the bake-off.

---

## 11. Automation

### 11.1 Folder contract

```
<SHOOT_INBOX>/<BRAND>/.../<SKU_CODE>/
    front.jpg  side.jpg  back.jpg  mannequin.jpg  detail.jpg
    output/
        d2c/          <SKU>_front1_v1.jpg  … 6 frames
        marketplace/  <SKU>_packshot_v1.jpg … 5 frames
        _campaign.json   persona · stage · hero · styling · model+version · QC · cost
```

File **role is detected, not dictated** — loose name match first, vision fallback second. A brand
shipping `IMG_2841.jpg` still works; filename conventions are the first thing to break in a real
supplier folder.

`ponytail:` no mandated `<COLLECTION>` level (read it from the path if present), no
`_contact_sheet.jpg` (the output folder *is* the contact sheet), no per-SKU `shoot.json` override —
the brief rules out per-product prompting for MVP.

**Google Drive**: no API integration. Mount it (Drive for Desktop / `rclone mount`), point
`SHOOT_INBOX` at the path. Swap to the Drive API only if this must run headless with no mount.

### 11.2 Ledger

```sql
shoot_jobs(job_id TEXT PK, brand TEXT, sku TEXT, input_hash TEXT, config_version TEXT,
           persona_id TEXT, stage_id TEXT, status TEXT, campaign_id TEXT,
           attempts INT, cost_usd REAL, error TEXT, created_at INT, updated_at INT)
UNIQUE(brand, sku, input_hash, config_version)
-- PENDING · RUNNING · NEEDS_INPUT · REVIEW · DONE · FAILED
```

`input_hash = sha256(sorted per-file sha256)`. Re-running the tree is a no-op; a replaced photo or a
bumped `config_version` re-queues that SKU alone. State lives in the table, so a crashed batch
resumes. `ponytail:` no `PARTIAL` status — it means the same as `REVIEW`, and per-frame verdicts
already live in `_campaign.json`.

### 11.3 The loop

Poll the inbox → claim `PENDING` → run → write back → repeat. Parallel across SKUs, sequential
within one. `ponytail:` in-process thread pool + polling; add a broker and inotify the day one box
cannot keep up.

### 11.4 Where the human is

Decided: **ship the good frames, park the bad one.** A frame failing QC re-rolls twice with the
reason appended, then parks in `REVIEW` while the rest of the SKU ships.

A human touches this **twice**:

1. **Brand onboarding** — approve the spec, the minted persona pool, the wardrobe and the stages.
   One sitting, per brand.
2. **The review queue** — only frames that failed twice, each with reason codes and the product
   photo it failed against.

Everything else runs unattended, under a per-SKU and per-day spend cap so one pathological SKU
cannot eat the budget in re-rolls, with an end-of-run digest.

**Auto-ship rate is the north-star metric** — literally the fraction of the customer's manual review
that disappeared.

`ponytail:` `auto_ship_threshold` is a P2 knob. P1 is one SKU with a human watching the verdicts.

---

## 12. QC axes

Per generated frame, one vision call against the product photos and the rules:

- **Subject integrity** *(added after round 1 — see the Status note)* — is this a complete,
  plausible human wearing a complete outfit? No mannequin parts, no missing or duplicated
  limbs, nothing left bare that the styling slots require. **This axis exists because the other
  four structurally cannot catch its failures:** round 1 produced a frame whose garment was
  rendered faithfully — correct colour, texture, cut — on a model with a *plastic mannequin
  lower body and no bottoms*. Garment-fidelity scoring rated it near-perfect, correctly, because
  the garment *was* near-perfect. The image was still unusable. Score this first and fail fast;
  there is no point scoring drape on a frame that ships a shop fixture.
- **Garment fidelity** — colour, pattern and stripe direction, print placement, logo spelling and
  legibility, silhouette, fabric, closures.
- **Rule compliance** — coverage, crop range, tuck-in, hair forward on back shots, aspect.
- **Continuity** — same face as the base, same location as the base.
- **Styling** — no stray branding on invented items, season/physics coherence.
- **Environment quality** — is the backdrop editorially rich (depth, architecture, materials,
  light) rather than merely text-free? Round 1's anti-signage rule over-corrected into bland
  stages. Since a stage is an L1 asset amortised over hundreds of SKUs, it costs a fraction of
  a cent per frame and should be the **most** crafted element in the system, not the least.

**Prompt invariants the QC gate assumes** (all three added after round 1):

1. *"The reference shows the garment on a headless display mannequin. The mannequin is not the
   subject — render a real human wearing the garment. No mannequin parts anywhere."* Without
   this, "reproduce the garment exactly" is obeyed too literally and the fixture comes through.
2. The hero/styling slot rules from §8, so no body region is left undefined.
3. No *legible text* in the environment — which is not the same as no interesting environment.

---

## 13. Quality risks, ranked

1. **Text and logos on the garment** — highest; every edit model fails here. QC reads rendered text
   back; re-roll; and ❓**the detail shot as a real macro crop** — zero hallucination on the frame
   buyers zoom into. Recommended.
2. **Pattern geometry** — stripe direction, panel alignment, placement-print position.
3. **Fabric drape and fit honesty** — improving the garment is a defect, not a feature.
4. **Generation strategy unsettled — settle it by test.** Path A: edit model with product photos as
   references. Path B: base + purpose-built virtual try-on, which preserves garment pixels far
   better. **P0 = a 20–30 image bake-off on three SKUs** (plain, heavily printed, text-logo) scored
   on colour ΔE, pattern integrity, text legibility, identity hold, drape, cost, latency.
5. **Ghost-mannequin quality** — its own bake-off rung; mannequin removal + inner-neck
   reconstruction is a different task from garment transfer.
6. **Input quality** — flat-lay harder than mannequin, harder than on-body. Caught at ingest.
7. **Hands, shoes, jewellery** — the classic tells.

---

## 14. Still open ❓

1. **Which marketplaces first** — each needs an image-spec profile.
2. **Persona exclusivity** — may brand A's face appear in brand B's catalogue?
3. **Size and body fidelity** — XL on a size-0 model misleads and drives returns. Size-linked body
   variants?
4. **Visible AI disclosure** — C2PA is invisible; a visible label is a brand-facing call.
5. **Pre-release confidentiality** — per-brand storage isolation plus a written answer on each
   vendor's data-retention/training policy. Can disqualify a vendor *after* the bake-off picks it.
6. **Versioning** — does a re-run overwrite `_v1` or add `_v2`?
7. **Target cost per delivered image** — sets the re-roll budget, spend caps and pricing.
8. **Detail shot: real macro crop (recommended) or generated?**
9. **Does the brand hold licensed real-child photography?** If not, ghost mannequin is the only
   kidswear path.
10. **Flat-lay for adult basics too?** Same rows in the shot table — cheap to allow.

---

## 15. Phasing

- **P0 — Measure before building** (2–3 days): 4:5 + configurable sizes, upscale+DPI chain,
  Gemini-direct backend, the model bake-off, and the **text-only stylist eval** (20 SKUs, accept
  rate, zero generation spend). *Gate: garment fidelity holds on the text-logo SKU.*
- **P1 — MVP web flow**: `/shoot` — upload → detect → cast → editable campaign spec → anchored
  frames → visible QC verdicts → gallery + ZIP. One SKU, fashion only, no payments, no bulk.
- **P2 — Batch + automation**: folder contract, ledger, worker loop, L1 stage pool, autonomy dial,
  spend caps, write-back, review queue, digest.
- **P3 — Multi-tenant**: brand onboarding (spec → pool mint → approval), wardrobes, destination
  profiles, white-label login, per-brand cost reporting.
- **Later**: seasonal/festival themes, categories beyond fashion, mobile, payments, video.

## 16. Non-goals

Payments, mobile app, non-fashion categories, video output, training a custom model.

## 17. Follow-up

Once P1 stabilises, capture this as `.agents/skills/product-shoot/SKILL.md` — the build+verify loop
for this vertical, the way `build-feature` covers the reel pipeline.
