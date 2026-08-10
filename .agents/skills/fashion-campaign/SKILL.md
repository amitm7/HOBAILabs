---
name: fashion-campaign
description: >
  How to author a fashion campaign image set from a product/reference photo in
  HOBAILabs — the prompt spec behind `tools/shoot_bakeoff.py`, `shoot_campaign.py`,
  `shoot_batch.py`. Invoke before writing or editing any garment image prompt, adding
  a shot code, a stylist pack, or a persona, and before hand-writing a campaign brief
  for a SKU. Covers product fidelity, model/persona lock, styling slots, environment,
  pose philosophy, camera language, the 6-shot set and the anchor-continuity rule.
metadata:
  type: project
---

# Fashion campaign prompt generation

The product/reference image is the source of truth. Build the campaign around the
garment while preserving its visual identity. Everything below is either already
code (follow it, don't re-write it) or the spec that code implements.

## Where each rule already lives

| Concern | Owner | Never do instead |
|---|---|---|
| Model identity | `PERSONA` in `shoot_bakeoff.py` + face pool via `shoot_persona.pick()` | describe a new face per shot |
| Environment / styling / genre | `config/shoot_stylists.json`, selected by `_pick_stylist()` (pack = pov, locations, light, styling, energy, avoid) | hardcode a location in Python |
| Pose + craft | `CRAFT`, `SHOTS[shot]` | ask for "a natural pose" |
| Framing | `COVERAGE`, `CROP_RANGE`, `_framing()` | let the model choose the crop |
| Styling slots | `STYLING_SLOTS[hero]` | free-form "styled with accessories" |
| Skin/eye realism | `REALISM` | "photorealistic, 8k, masterpiece" |
| Set continuity | `ANCHOR_CLAUSE` + image conditioning | repeating styling words per frame |
| Model choice / cost | `agents/model_router.py`, `config/pricing.json` | a literal model id or price |

A new genre, location, styling language or persona is a **JSON/config edit**. Only a
new *mechanism* is Python.

## 1. Product first

Preserve from the reference: colour, pattern/stripe/print, silhouette, neckline and
collar, sleeves and cuffs, buttons, seams, pockets, proportions, visible branding or
text, fabric appearance, construction. Do not redesign, recolour, simplify, add or
remove garment features.

Describe the product as `[garment type] + [colour] + [pattern/construction] +
[key design details]` — this is what `_garment_brief()` reads off the tag, and
`_hero_type()` classifies into `top | bottom | dress | set | footwear`.

References are usually a **headless display mannequin**. The `MANNEQUIN` clause is
mandatory — without it the generator faithfully copies plastic limbs into the frame.

## 2. Campaign direction

Every campaign declares: theme, creative concept, fashion positioning, environment,
time of day, lighting, mood. When the user asks for a new environment, change the
complete visual world — architecture, surfaces, vegetation, props, light behaviour,
atmosphere — and keep the product untouched.

Global hard rule: **no legible text anywhere** — no shop names, signage lettering,
menus, printed words, logos. Architecture and props only. "No legible text" is not
"no interesting environment": the stage is an L1 asset amortised over hundreds of
SKUs, so it should be the most crafted element in the frame, not the emptiest.

## 3. Model specification

One fictional adult model per campaign. Specify age, height, build, hair, eyes, face
character. Same identity across every shot in the campaign; a different campaign may
use a substantially different model.

Lock it with an **image**, not words: `shoot_persona.pick(brand, sku)` returns a face
from the pool and it is passed as reference 1 with `PERSONA_REF_CLAUSE`. Words make
every generation invent a face from the model's own beauty prior; an image gives it
something to be faithful to.

Age: state it as an adult and keep body language photographic. Anatomical wording
next to a stated age trips provider prompt filters (live 422 on `body.prompt`,
nano-banana). Normalise to spec-sheet / craft vocabulary, never body description.

### Audience guard — non-negotiable

`_audience()` runs before any generation and **parks** the SKU unless it is clearly adult
womenswear. Kidswear generation is out of scope (owner, 2026-08-10), so this is a guard,
not a route: `audience != adult`, or an apparent minor in the input photos, means the job
stops and is flagged — it never falls through to the model path. Ambiguity resolves
**downward**: `unclear` parks. Detection failure also parks, failing safe.

Do not "improve" this by adding a fallback that lets an unclear SKU proceed.

## 4. Hair and makeup

Hair: length, texture, parting, volume, movement, finish. Makeup: complexion, blush,
bronzer, eyeshadow, liner/mascara, brows, lips, highlight — matched to the campaign.
Skin must still read as skin: `REALISM` bans retouching, smoothing, waxy sheen,
glassy eyes and doll symmetry.

## 5. Styling

Fill the hero's supporting slots from `STYLING_SLOTS[hero]`, plus the pack's
`styling` line. Restrained luxury; supporting pieces exist to make the hero *read*,
never to compete with or cover it. A layer over a hero top, or trousers under a hero
dress, is a styling failure, not a generation failure.

Two rules that are load-bearing because they were learned from failures:

- **Bottom hero → a fitted CROP TOP or cropped tank, hem above the natural waist, midriff
  bare.** The waistband, rise and hip line *are* the product for jeans and trousers. A
  tucked tee still buries them; a long-sleeved or hip-length top hides the sale entirely.
- **Dress hero → footwear only.** No trousers, leggings or skirt under or over it, no
  jacket covering it. Stating what to *add* was not enough — the generator put khaki
  trousers under a burgundy midi dress until the rule named what was forbidden.

Negative constraints do work that positive ones don't. This is the same reason every
stylist pack carries an `avoid` list.

## 6. Environment

Location type, architecture, surfaces and materials, vegetation, background objects,
lifestyle elements, depth treatment, atmosphere. It must reinforce the garment and be
photographically plausible. `_pick_stylist()` chooses the pack by longest-keyword match on the product description
(falling back to `everyday`), then picks one of its `locations` by SKU hash — so a
collection is coherent without every SKU looking identical, and the same SKU always
returns the same location on a re-shoot.

## 7. Time and lighting

Always explicit: direction, softness, warmth, rim, bounce, shadows, reflections.
Never "good lighting". The pack's `light` line is the default.

## 8. Pose philosophy — natural + raw + flare

Core requirement, and the one most often lost. Combine natural pose, raw candid
moment, editorial flare, movement, asymmetry, controlled body language.

Avoid: identical hand placement, rigid mannequin stance, stiff shoulders, generic
stand-and-smile, repetition across the set.

Prefer: one knee softly bent; weight on one hip; walking mid-stride; turning while
walking; looking away; adjusting sunglasses; brushing hair back; bag held loosely;
gripping a cuff; adjusting a collar; sitting asymmetrically; torso rotation; looking
back over a shoulder; laughing toward someone out of frame; fabric caught in breeze.

Per set: at least 2–3 shots with noticeable movement, at least one candid/raw, at
least one with clear editorial flare. Hands are ACTIVE with soft fingers — never
limp, never flat palms, never claw-like.

Pose and camera are **spec, not prose**. An LLM asked for "a pose" converges on the
same three across a whole catalogue — that is why `SHOTS` enumerates per shot code.

## 9. The shot set

Six frames, 4:5 — **seven for a co-ord set**, which gets a second detail on its other
piece. The list comes from `SHOT_LIST[hero]`, never from a CLI default. Repo shot codes
in brackets.

- **Front** [`front_1`] — tight product crop, garment dominates. The visible body range is **per product type** via `CROP_RANGE`: top → neck to mid-thigh · bottom → midriff to hem · dress → neck to hem · footwear → knee to floor. Head is usually out of frame. 85mm close, f/4.
- **Alternate front** [`front_2`] — full length, whole figure, relaxed three-quarter stance, gaze to camera. 85mm from further back, f/2.8.
- **Side** [`side`] — 45° profile, never a flat 90. Walking the camera line, torso turned back. Silhouette and drape are the subject. 85mm f/2.0.
- **Back** [`back`] — walking away, glancing back. Hair swept forward so the back construction reads. Soft rim light. 105mm.
- **Lifestyle** [`lifestyle`] — wide environmental, model small in frame, living in the scene not presenting. 35mm.
- **Detail** [`detail`, plus `detail2` for sets] — extreme macro, head and most of the body out of frame. What it points at is set by `DETAIL_FOCUS[hero]`: bottom → waistband, closure, fly, belt loops, pocket openings, topstitching, weave · top → neckline, shoulder seam, cuff, knit surface · dress → neckline, armhole, principal seam · footwear → upper, sole edge, stitching, grain. 100mm macro. Only details visible or reasonably inferable from the reference.

- **Packshot** [`packshot`] — the marketplace primary image, now in EVERY product type's
  shot list. Plain seamless light-grey backdrop, even studio light, no props, generous
  margin, silhouette never cropped. 105mm. It deliberately ignores the stylist's location
  (`PACKSHOT_STAGE` overrides it) — a listing image has no scenery by definition.

### Destinations

`DESTINATION[shot]` routes each frame into `<SKU>/photoshoot/d2c/` or
`.../marketplace/`. A destination is a shot set, not a second pipeline: the same persona
and the same garment serve both, so the listing image and the lifestyle image show the same
woman — something a real photoshoot has to work at.

The differentiator between frames is the **crop**, not only the pose — an advisory
framing table produced three near-identical standing shots. `CROP_RANGE` and
`COVERAGE` are load-bearing.

### Generate four, derive two

Only `GENERATED_SHOTS` (`front_2`, `back`, `side`, `lifestyle`) are paid generations.
`front_1`, `detail` and `detail2` are **cropped out of the `front_2` anchor** by
`_derive()`, using body landmarks located once per anchor by `_landmarks()`.

This is not an optimisation, it is the only thing that works. Conditioned on a full-body
anchor, the generator returns another full-body frame however emphatically the prompt
demands a macro — proven across three separate runs on denim and swimwear. Cropping hits
the coverage table exactly, cannot drift, and guarantees the tight frame shows the same
garment as the full frame because it *is* the full frame. It also drops a SKU from
$0.36 to $0.24.

`DERIVE_BAND[hero][shot]` names the landmark span to keep. `_landmarks()` validates that
the vertical order is monotonic and falls back to `DEFAULT_LANDMARKS` if not — a
plausible-but-wrong landmark set would crop the wrong body part, which is worse than
using a generic frame.

Do not "fix" a soft detail shot by strengthening its prompt. Fix the band.

## 10. Camera language

35mm lifestyle/environmental · 50mm movement/editorial street · 85mm hero
front/side · 100mm macro fabric and construction · 105mm rear and packshot. Shallow
depth of field where it earns it. Realistic terminology, no technical keyword dump.

### Delivery

`_deliver()` guarantees 4:5, a 2K long edge and a 300 DPI tag on every frame. Under target
it upscales with **`aura_sr` — faithful super-resolution that recovers detail without
inventing any**. A *creative* upscaler (clarity) must never touch a product frame: §13.3
says improving the garment is a defect. If aura_sr is unavailable it falls back to Lanczos,
reports the degradation, and never fails delivery. `SHOOT_UPSCALE=auto|aura|off`.

Be honest about what each gives you: aura_sr recovers real detail; Lanczos only reaches the
pixel count.

## 11. Composition

4:5 vertical commercial framing (`_crop_45()` enforces delivery). Full body when
garment length matters, controlled negative space, realistic perspective, clear
garment visibility. Movement shots keep directional space and do not clip hands or
feet.

## 12. Quality bar

Premium campaign; realistic anatomy and hands; realistic fabric physics and drape;
natural skin texture; realistic hair movement; photographic depth; coherent lighting;
commercially usable composition; sophisticated grading. Useful language: *premium
commercial fashion photography, luxury editorial colour grading, cinematic shallow
depth of field, realistic fabric movement, natural skin texture, authentic candid
expression*. Do not keyword-stuff.

## 13. Prompt order

This is the order `_prompt()` actually assembles — read it before inserting a clause,
because position changes what the generator weights:

```
1  WHO          PERSONA_REF_CLAUSE (face from the pool) — or PERSONA text if no pool
2  GARMENT      reference-fidelity clause + the tag-read product description
3  MANNEQUIN    "the mannequin is not the subject"
4  STYLING      STYLING_SLOTS[hero]
5  SETTING      the stylist pack's chosen location
6  NO-TEXT      the global no-legible-text rule
7  ART DIRECTION pack pov + light + styling + energy + avoid
8  CRAFT        posture, hands, framing discipline, commercial register
9  SHOT         SHOTS[shot] — pose and lens for this frame
10 FRAMING      _framing(hero, shot) — crop range, coverage, per-shot rules
11 REALISM      skin and eye realism bans
12 FINISH       medium-format / fabric-detail closer
```

Add a clause inside the owning constant, don't rebuild the assembly.

## 14. Consistency — anchor, don't repeat

Within a campaign: same model, same hero garment and colour and construction, same
core styling, same environment, same lighting language. Only pose, camera angle,
distance, action and in-environment position vary.

Consistency comes from **image conditioning, not repeated words**. Frame 1 is the
anchor, generated from the references; frames 2..N are generated from
`[anchor] + [garment refs]` under `ANCHOR_CLAUSE`. "Tan leather sandals" names a
category — repeated per frame it yields different sandals every time; conditioning on
the anchor yields the *same* sandals because the model is looking at them.

### The quality gate

`_accept(score)` decides ship-or-re-roll for every generated frame. It rejects on, in order:

1. **Integrity** — `usable` false: mannequin parts, incomplete human, incomplete outfit.
   This outranks everything; a perfect garment on a plastic leg is still unusable.
   "Incomplete outfit" is judged per product type via `_OUTFIT_RULE[hero]`, because being
   dressed means different things: a bare midriff is a DEFECT on a dress and the INTENDED
   styling on jeans. Without the hero passed in, the gate re-rolled correct bottom-wear
   frames — a wasted generation on every such SKU.
2. **Legible text** anywhere in frame.
3. **Fidelity floor** — colour, texture and construction must each reach
   `FIDELITY_FLOOR` (3). Inclusive: exactly 3 passes.

A rejected frame is re-rolled with the reason appended to its prompt, up to
`MAX_ATTEMPTS` (3 = 1 + 2 re-rolls), then written anyway and **parked**: frame status
`REVIEW`, SKU status `REVIEW`, the shot listed in `parked`, and `qc.reject_reason`
recorded in `_campaign.json`. Parked frames still ship to disk — a human decides, the
batch never blocks.

Derived frames are not gated: they are crops of an already-accepted anchor.

**Spend caps.** `--cap-sku` (default $1.00) stops re-rolling that SKU; `--cap-run` stops
the batch cleanly between SKUs. The ledger keeps everything finished, so a capped run
resumes by re-running the same command.

**The audience guard runs before any spend** and returns `NEEDS_INPUT` with $0 cost for
anything not clearly adult womenswear.

`shoot_campaign._drift()` scores the set against the anchor on `same_person`,
`same_location`, `same_hero`, `same_styling`, `pose_changed`. A styling score of 2
means different shoes of the same colour — treat below 4 as a failed set.

## 15. Pose variation matrix

Front — confident asymmetric stance. Alternate front — walking / turning / dynamic.
Side — lean / profile / torso twist. Back — walking away plus look-back. Lifestyle —
sitting / interacting / candid. Detail — hands interacting with the garment.

## 16. Written brief output format

When the deliverable is a written campaign brief rather than a run, emit exactly:

```
# Campaign Theme
## [Campaign Name]
(1–2 paragraphs of creative direction)

# Model Specification
**Ethnicity/Background:** … **Age:** … **Height:** … **Weight:** …
**Body Type:** … **Hair:** … **Eyes:** … **Face:** …

# Hair & Makeup      (### Hair / ### Makeup)
# Styling
# Environment
# Time of Day
# Lighting

# FRONT SHOT (4:5)              ### Prompt  <complete prompt>
# ALTERNATE FRONT SHOT (4:5)    ### Prompt  <complete prompt>
# SIDE SHOT (4:5)               ### Prompt  <complete prompt>
# BACK SHOT (4:5)               ### Prompt  <complete prompt>
# LIFESTYLE SHOT (4:5)          ### Prompt  <complete prompt>
# DETAILED SHOT (4:5)           ### Prompt  <complete prompt>
```

## 17. Before delivering

Product matches the reference; colour preserved; construction preserved · same model
across all six · model explicitly adult · styling complements, never covers · stylist
pack chosen and its `avoid` list respected · environment specific and visually rich,
and fully changed when a change was asked for · lighting explicit · front shot shows
the product · alternate front meaningfully different · side reads silhouette · back
reads rear fit and drape · lifestyle candid and believable · detail on real
construction · poses not repetitive · one raw/candid, one editorial-flare, 2–3 with
movement · hands and anatomy described naturally · fabric movement realistic · every
frame 4:5 · premium editorial language present, no keyword stuffing · no mannequin
artefacts · no legible text · no product redesign introduced.

Then, for a real run: `--dry-run` / no `--go` first to see the estimate, cheap model
for iteration, premium for the client render. Prices come from `config/pricing.json`.
