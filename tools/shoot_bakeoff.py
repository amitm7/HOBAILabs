"""
S34 bake-off — which edit model holds a real garment best?

Runs ONE front shot per (SKU x model) from the brand's own mannequin photos, then
scores every output with the vision seam against the mannequin reference on the axes
the brief calls non-negotiable: colour, texture, construction, and stray text.

Deliberately no failover: a model that fails must SHOW as failed, not be silently
covered by the next one in the chain (that is what a bake-off is for).

Usage:
    python tools/shoot_bakeoff.py                     # dry run — prints the cost, spends nothing
    python tools/shoot_bakeoff.py --go
    python tools/shoot_bakeoff.py --go --sku 443340024_black --models nano_banana_edit

Reads SKU folders shaped like the agency's:  <SKU>/<SKU>_MODEL*.jpg + _SWATCH + _WASHTAG + TAG
See docs/PRODUCT_SHOOT_PLAN.md §13.4.
"""

import argparse
import hashlib
import json
import os
import re
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Only load .env when this file is RUN as a script. Imported as a library (agents/shoot.py
# does this lazily inside a request) an override=True load would silently re-apply the
# on-disk .env over the server's own environment mid-request — which is how a production
# HOB_AUTH_DISABLED could be resurrected from a file nobody meant to deploy.
if __name__ == "__main__":
    load_dotenv(".env", override=True)

from PIL import Image  # noqa: E402

INBOX = "/Users/amitmishra/Desktop/PhotoShoot"
OUT_DIR = os.path.join(os.path.dirname(__file__), "_bakeoff_out")
CANDIDATES = ["nano_banana_edit", "seedream_edit", "flux_kontext"]

# The brand's locked persona spec (docs/PRODUCT_SHOOT_PLAN.md §5) — identical for every
# model so the ONLY variable is the generator.
# Age raised from the brief's 19 to 25 (owner-flagged direction, 2026-08-10). Two reasons:
# an aspirational commercial register reads better on a mid-20s model, and allure language
# beside a stated teenage age reliably trips vendor content filters — we already ate a 422 on
# far milder wording. Change here if the brand insists, and expect refusals.
# A CASTING CALL, not one woman. The old spec pinned every variable — age, height, weight,
# waist, hair length, shade, eye colour, face shape — so twelve minted faces came back as the
# same person twelve times. Variation has to be given somewhere to exist. Brand identity lives
# in the LOCKED axes (nationality, age band, register); the ranges are what make thirty
# different women. Keep the wording spec-sheet, never anatomical — that is what trips filters.
PERSONA = ("a female fashion model aged 22 to 30, of European, American, Russian/Slavic, "
           "Chinese or Korean background, 5 feet 6 to 5 feet 11, build ranging from slim to "
           "softly athletic with varied natural proportions, hair and eye colour appropriate "
           "to her background, varied face shapes and bone structure, varied skin tone, "
           "minimal natural makeup with skin left looking like real skin")

# Rich, not bland. Round 1 banned signage and got visually empty backdrops with it — the rule
# is "no LEGIBLE TEXT", not "no interesting environment". A stage is an L1 asset amortised over
# hundreds of SKUs, so it should be the most crafted element, not the least.
STAGE = ("a sunlit European boutique courtyard: weathered stone archways with real depth behind "
         "the model, wrought-iron balconies, terracotta planters with olive and bougainvillea, "
         "a café table with linen, worn stone paving, layered background falling away into soft "
         "bokeh. Rich materials, warm directional late-morning light, deep shadows. "
         "CRITICAL: no legible text anywhere — no shop names, no signage lettering, no menus, "
         "no printed words, no logos. Architecture and props only")

# Round 2's shot direction was "model standing naturally facing camera" — and that is exactly
# what came back: symmetrical, feet parallel, arms limp, dead-centre. A passport photo.
# These frames are meant to read as a professional model directed by a professional
# photographer, so pose and camera are SPEC, not prose. Enumerated per shot code, because an
# LLM asked for "a pose" converges on the same three across a whole catalogue.
# Anatomical wording next to a stated age tripped nano-banana's prompt filter (422 on
# body.prompt, round 3, blue SKU). Same language expressed photographically passes — the plan
# §5.2 rule, learned live: normalise to spec-sheet/craft vocabulary, never body description.
CRAFT = ("Shot by a leading fashion photographer, directed by an experienced editorial model. "
         "Stance is deliberate and asymmetric — a relaxed three-quarter stance with the weight "
         "carried on the rear foot, front foot eased forward, upper body turned about 15 degrees "
         "off the camera axis, head angled slightly toward the key light. Hands are ACTIVE and "
         "relaxed with soft fingers: never limp at the sides, never flat palms, never claw-like. "
         "Subject placed slightly off-centre on a third with clean headroom. Garment reads "
         "crisply, with no bunching that hides the cut. "
         "The register is aspirational premium fashion — magnetic screen presence, a direct "
         "self-assured gaze, an elongated confident line through the body, the poise of a top "
         "editorial model who knows the camera. Alluring in the way a luxury campaign is "
         "alluring: through confidence, posture and light, never through exposure. Keep it "
         "tasteful and commercial — clothing sits correctly on the body at all times.")

# The AI tell is the face, not the clothes: glassy oversaturated irises, poreless waxy skin and
# doll-like symmetry read as fake before anything else does. Ask for photographed skin.
REALISM = ("Absolute photographic realism in the face and skin: true skin texture with visible "
           "pores, fine surface detail and natural tonal variation; subtle natural asymmetry "
           "between the two sides of the face; realistic eyes with detailed iris fibres, natural "
           "limbal ring and a single true-to-life catchlight. NO beauty retouching, NO skin "
           "smoothing or airbrushing, NO plastic or waxy sheen, NO glassy or oversaturated eyes, "
           "NO doll-like perfect symmetry, NO heavy cosmetic makeup look. This must read as an "
           "unretouched photograph of a real person taken on a real camera.")

# Styling is decided ONCE per campaign and carried by the ANCHOR IMAGE, not by repeating
# words. "tan leather sandals" describes a category — repeated per frame it yields different
# sandals every time. Conditioning on the anchor yields the same sandals because the model is
# looking at them. Only pose/camera vary per shot (plus declared lifestyle props).
ANCHOR_CLAUSE = (
    "IMAGE 1 is the locked campaign anchor for this shoot. Keep EVERYTHING in it identical: "
    "the same woman with the same face, hair and skin, the same location and lighting, the "
    "same hero garment, and the SAME supporting clothes, the SAME footwear and the SAME "
    "accessories — identical items, not similar ones. The remaining reference images show the "
    "real hero garment. Change ONLY the pose and the camera angle described below.")

PACKSHOT_STAGE = ("a plain seamless light-grey studio backdrop with an even soft sweep, no "
                  "props, no furniture, no scenery, no horizon line, nothing but the backdrop "
                  "and a subtle contact shadow under the feet")

SHOTS = {
    # Brief pages 6-7. The differentiator between these frames is the CROP, not the pose —
    # round 5 shipped front/alt_front/side as three near-identical full-length standing shots
    # because the framing table was advisory in the prompt instead of load-bearing.
    "front_1": ("TIGHT PRODUCT CROP — this is NOT a full-length shot. The frame runs from the "
                "neckline down; the head is cropped at or just above the chin so the garment "
                "dominates the frame. Model square to camera, weight on the back leg, one hand "
                "relaxed at the hip. 85mm lens close in, f/4, garment sharp edge to edge."),
    "front_2": ("FULL-LENGTH shot — the whole figure from the top of the head to below the feet, "
                "with clean headroom and floor beneath. Relaxed three-quarter stance, weight on "
                "the rear foot, gaze direct to camera. 85mm lens from further back, f/2.8."),
    "side":    ("FULL-LENGTH profile at 45 degrees — never a flat 90. Model walking naturally "
                "along the camera line, torso turned slightly back, arms swinging softly. The "
                "SILHOUETTE and drape are the subject. 85mm lens, f/2.0."),
    "back":    ("FULL-LENGTH rear view. Model walking away, glancing back over the shoulder. "
                "Hair swept FORWARD over one shoulder so the garment's back construction reads "
                "unobstructed. Backlit with a soft rim light. 105mm lens."),
    "lifestyle": ("WIDE environmental frame — the model is SMALL in the picture and the location "
                  "fills it. She is living in the scene, not presenting: mid-step, gaze off "
                  "camera, carrying a simple unbranded bag. 35mm lens, plenty of air around her."),
    "detail":  ("EXTREME MACRO of the garment itself. NOT a full-length shot and NOT a portrait: "
                "the head, and most of the body, are OUT OF FRAME. The garment fills the picture "
                "edge to edge so the fabric weave, stitching and hardware are clearly legible. "
                "100mm macro lens, f/5.6, razor-sharp on the textile, background fully defocused."),
    "detail2": ("Second EXTREME CLOSE-UP on the OTHER piece of the set — if the first showed the "
                "upper garment, this shows the lower one. Same macro treatment, head out of frame."),
    "packshot": ("Marketplace primary image. Full-length, centred, plain seamless light-grey "
                 "studio backdrop, even soft studio light, no props, no scenery, generous margin, "
                 "silhouette never cropped. 105mm lens, flat clean catalogue look."),
}

# The brief's per-product-type framing table (pages 6-7): how much canvas the model+garment
# fill, and what body range is visible. Data, so a client can override it without code.
COVERAGE = {                      # brief: how much canvas model+garment fill
    "front_1": 0.70, "front_2": 0.70, "side": 0.70, "back": 0.70,
    "lifestyle": 0.40, "detail": 0.90, "detail2": 0.90, "packshot": 0.75,
}

# Brief pages 6-7, per product type. The visible body range per shot — this is the table that
# stops every frame being the same photograph.
CROP_RANGE = {
    "top":      {"front_1": "from the neck down to mid-thigh",
                 "detail":  "from the neck down to the upper thigh"},
    "bottom":   {"front_1": "from the midriff down to the hem of the garment",
                 "detail":  "from the waist down to the calf"},
    "dress":    {"front_1": "from the neck down to the hem of the dress",
                 "detail":  "from the neck down to the midriff"},
    "set":      {"front_1": "from the neck down to the hem",
                 "detail":  "the UPPER piece, from the neck to the midriff",
                 "detail2": "the LOWER piece, from the waist to the hem"},
    "swim":     {"front_1": "from the neck down to mid-thigh, both swimwear pieces fully in frame",
                 "detail":  "the cup, strap, tie or clasp detail and the fabric surface"},
    "footwear": {"front_1": "from the knee down to the floor",
                 "detail":  "from the ankle down to the floor"},
}

# Default shot list per product type. Sets get TWO detail shots (brief page 7, items 24-25).
SHOT_LIST = {
    # Six D2C frames + a marketplace packshot. Sets get a second detail (brief pages 6-7, 24-25).
    "top":      ["front_1", "front_2", "back", "side", "lifestyle", "detail", "packshot"],
    "bottom":   ["front_1", "front_2", "back", "side", "lifestyle", "detail", "packshot"],
    "dress":    ["front_1", "front_2", "back", "side", "lifestyle", "detail", "packshot"],
    "footwear": ["front_1", "front_2", "back", "side", "lifestyle", "detail", "packshot"],
    "swim":     ["front_1", "front_2", "back", "side", "lifestyle", "detail", "packshot"],
    "set":      ["front_1", "front_2", "back", "side", "lifestyle", "detail", "detail2",
                 "packshot"],
}



# What the macro actually points at, per product type — a jeans detail is the waistband and
# hardware, a dress detail is the neckline and seam.
DETAIL_FOCUS = {
    "bottom":   "the WAISTBAND and front rise: the button or closure, the fly, belt loops, "
                "front pocket openings, topstitching and the denim weave and wash",
    "top":      "the neckline, shoulder seam and cuff finishing, plus the knit or weave surface",
    "dress":    "the neckline, armhole finishing, the principal seam line and the fabric surface",
    "set":      "the upper piece's neckline and hem finishing and its fabric surface",
    "swim":     "the strap, tie, clasp or ring hardware, the seam and edge binding, and the "
                "fabric surface and sheen",
    "footwear": "the upper, sole edge, stitching and material grain",
}


# ── derive-by-crop ──────────────────────────────────────────────────────────
# Three separate runs proved the tight frames cannot be prompted: conditioned on a
# full-body anchor, the generator returns another full-body shot no matter what the words
# say. So the tight frames are CROPPED from the full-length frame instead — which hits the
# coverage table exactly, cannot drift, guarantees the crop shows the same garment as the
# full shot, and removes two paid generations per SKU.
GENERATED_SHOTS = ("front_2", "back", "side", "lifestyle", "packshot")

# Which destination folder each shot is delivered into. A destination is a shot set, not a
# second pipeline (plan §9): the same persona and garment serve both, so the listing image and
# the lifestyle image show the same woman.
DESTINATION = {
    "front_1": "d2c", "front_2": "d2c", "back": "d2c", "side": "d2c",
    "lifestyle": "d2c", "detail": "d2c", "detail2": "d2c",
    "packshot": "marketplace",
}

# Which vertical band of the body each derived frame keeps, as (start, end) landmark names
# plus padding as a fraction of image height.
DERIVE_BAND = {
    "top":      {"front_1": ("neck", "mid_thigh", 0.02), "detail":  ("neck", "chest", 0.03)},
    "bottom":   {"front_1": ("waist", "ankle", 0.02),     "detail":  ("waist", "hip", 0.04)},
    "dress":    {"front_1": ("neck", "ankle", 0.02),      "detail":  ("neck", "chest", 0.03)},
    "set":      {"front_1": ("neck", "ankle", 0.02),      "detail":  ("neck", "waist", 0.03),
                 "detail2": ("waist", "knee", 0.03)},
    "swim":     {"front_1": ("neck", "mid_thigh", 0.02),  "detail":  ("neck", "chest", 0.03)},
    "footwear": {"front_1": ("knee", "ankle", 0.02),      "detail":  ("ankle", "ankle", 0.05)},
}

# A typical full-length 4:5 fashion frame, used when vision landmarking fails or returns
# something implausible. Fractions of image height; body_* are fractions of width.
DEFAULT_LANDMARKS = {"neck": 0.13, "chest": 0.22, "waist": 0.38, "hip": 0.46,
                     "mid_thigh": 0.56, "knee": 0.68, "ankle": 0.93,
                     "body_left": 0.30, "body_right": 0.70}

LANDMARK_SCHEMA = {"name": "landmarks", "schema": {
    "type": "object", "additionalProperties": False,
    "properties": {k: {"type": "number", "minimum": 0, "maximum": 1}
                   for k in DEFAULT_LANDMARKS},
    "required": list(DEFAULT_LANDMARKS)}}


def _landmarks(path: str) -> dict:
    """Locate body landmarks in a full-length frame, as fractions of the image.

    Validated hard: the vertical order must be monotonic and the body box non-degenerate.
    A plausible-looking but wrong landmark set would silently produce crops of the wrong
    body part, which is worse than using the defaults."""
    from agents import llm
    try:
        raw = llm.chat([{"role": "user", "content": [
            {"type": "text", "text":
             "This is a full-length photograph of a standing model. Give the vertical position "
             "of each landmark as a fraction of image HEIGHT (0.0 = very top, 1.0 = very "
             "bottom): neck (base of the neck), chest, waist (narrowest point), hip, mid_thigh, "
             "knee, ankle. Also body_left and body_right as fractions of image WIDTH marking "
             "the left and right edges of the model's body including arms. Numbers only."},
            {"type": "image", "path": path}]}],
            model_tier="fast", max_tokens=250, json_schema=LANDMARK_SCHEMA)
        lm = json.loads(re.sub(r"^```\w*|```$", "", raw.strip(), flags=re.M))
    except Exception:
        return dict(DEFAULT_LANDMARKS)

    order = ["neck", "chest", "waist", "hip", "mid_thigh", "knee", "ankle"]
    ys = [lm.get(k) for k in order]
    if any(y is None for y in ys) or any(b <= a for a, b in zip(ys, ys[1:])):
        return dict(DEFAULT_LANDMARKS)
    if not (0 <= lm.get("body_left", 1) < lm.get("body_right", 0) <= 1):
        lm["body_left"], lm["body_right"] = (DEFAULT_LANDMARKS["body_left"],
                                             DEFAULT_LANDMARKS["body_right"])
    return lm


def _derive(src: str, out: str, hero: str, shot: str, lm: dict) -> str:
    """Crop a tight frame out of the full-length one, at exactly 4:5."""
    band = DERIVE_BAND.get(hero, DERIVE_BAND["top"]).get(shot)
    if not band:
        raise ValueError(f"no derive band for {hero}/{shot}")
    start, end, pad = band
    with Image.open(src) as im:
        W, H = im.size
        y0 = max(0.0, lm[start] - pad) * H
        y1 = min(1.0, lm[end] + pad) * H
        if y1 - y0 < H * 0.08:                       # degenerate band (e.g. ankle..ankle)
            mid = (y0 + y1) / 2
            y0, y1 = mid - H * 0.06, mid + H * 0.06
        h = y1 - y0
        w = h * 0.8                                  # 4:5
        cx = (lm["body_left"] + lm["body_right"]) / 2 * W
        if w > W:                                    # band too tall for the frame — fit width
            w, h = W, W / 0.8
        x0 = min(max(0.0, cx - w / 2), W - w)
        y0 = min(max(0.0, y0), H - h)
        im.crop((round(x0), round(y0), round(x0 + w), round(y0 + h))).save(out, quality=95)
    return _deliver(out)


def _framing(hero: str, shot: str) -> str:
    """Coverage + crop range + the brief's two standing rules."""
    bits = []
    rng = CROP_RANGE.get(hero, {}).get(shot)
    if rng:
        bits.append(f"FRAMING IS CRITICAL — the frame shows {rng}, and nothing beyond it.")
    bits.append(f"The model and garment together fill about "
                f"{int(COVERAGE.get(shot, 0.7) * 100)} percent of the frame.")
    if hero == "bottom" and shot != "lifestyle":
        # Brief: "when shooting bottom, upper wear tucked in all shots except lifestyle"
        bits.append("The upper garment is TUCKED IN so the waistline and fit of the hero "
                    "bottom read clearly.")
    if shot == "back":
        bits.append("Hair is styled FORWARD over the shoulders, never covering the back of "
                    "the garment.")
    if shot in ("detail", "detail2"):
        focus = DETAIL_FOCUS.get(hero, DETAIL_FOCUS["top"])
        bits.append(f"The macro is centred on {focus}. No face, no full body — only the garment.")
    if shot == "lifestyle":
        bits.append("The model occupies well under half the picture: seated at a table, leaning "
                    "on a wall or mid-step through the space, doing something ordinary. The "
                    "location fills the rest of the frame.")
    return " ".join(bits)


def _anchor_prompt(shot: str, hero: str = "top") -> str:
    """Frames 2..N: inherit the anchor, change only pose, camera and framing."""
    return (f"{ANCHOR_CLAUSE} {CRAFT} {SHOTS[shot]} {_framing(hero, shot)} {REALISM} "
            f"Photorealistic premium fashion campaign photograph, same shoot, same day.")

# Round 1's single biggest defect: given a mannequin photo and "reproduce exactly", seedream
# faithfully copied the mannequin's plastic legs into the output. The model was obeying.
MANNEQUIN = ("The reference photographs show the garment on a HEADLESS DISPLAY MANNEQUIN. "
             "The mannequin is NOT the subject and must not appear: no plastic limbs, no "
             "mannequin torso, no display stand, no hanging tags or price tickets. Render a "
             "real, complete, living human being wearing the garment — fully formed arms, "
             "hands and legs, natural skin everywhere.")

# Which body regions the stylist MUST fill, by hero type (plan §8). Round 1 omitted these and a
# top rendered with no bottoms at all.
STYLING_SLOTS = {
    # Supporting pieces exist to make the hero READ, never to compete with it or cover it.
    # Round 5 put a high-neck long-sleeve top over jeans (hiding the waist and the whole line
    # of the garment) and added trousers under a dress. Both are styling failures, not
    # generation failures — the slot instructions were too permissive.
    "top":      "The hero garment is a TOP. Add ONE plain unbranded bottom in a quiet neutral "
                "tone — simple tailored trousers, straight jeans or a plain skirt — plus simple "
                "footwear. Nothing else. NO jacket, NO cardigan, NO scarf, NO layer of any kind "
                "over the hero: the hero top must be fully visible from shoulder to hem.",
    "bottom":   "The hero garment is a BOTTOM. Add ONE plain unbranded FITTED CROP TOP or "
                "CROPPED TANK — sleeveless or very short-sleeved, simple scoop or square "
                "neckline, hem ending ABOVE the natural waist so the midriff is bare and the "
                "waistband, rise, hip line and full leg of the hero garment are completely "
                "unobstructed. This is not optional: the waistband and the fit through the "
                "waist and hip ARE the product. Do NOT use a long-sleeved, high-necked, "
                "oversized, untucked or hip-length top — any of those hide what is being sold. "
                "Plus simple footwear. Nothing else.",
    "dress":    "The hero garment is a DRESS and it is the ENTIRE outfit. Add ONLY simple "
                "unbranded footwear. Absolutely NO trousers, NO leggings, NO jeans, NO skirt "
                "under or over it, NO jacket or cardigan covering it. At most one small piece "
                "of fine jewellery.",
    "set":      "The hero is a CO-ORD SET — every piece in the references is hero and all of "
                "them are worn together exactly as shown, nothing added between or over them. "
                "Add ONLY simple footwear.",
    "swim":     "The hero garment is SWIMWEAR and it is the ENTIRE outfit. The model wears the "
                "swimwear and NOTHING ELSE — exactly as many pieces as the references show and "
                "no more. Add NO other clothing of any kind on any frame: no trousers, no "
                "jeans, no shorts, no skirt, no leggings, no shirt, no cover-up, no kaftan, "
                "no sarong, no dress, no jacket, no towel worn as a garment. At most bare feet "
                "or simple flat sandals and sunglasses. Tasteful, editorial and commercially "
                "publishable — the garment sits correctly on the body in every frame.",
    "footwear": "The hero garment is FOOTWEAR. Dress the model in a plain unbranded outfit that "
                "leaves the legs and feet clearly visible — no long hems, no wide flares "
                "covering the shoe — and frame so the footwear reads sharply.",
}


IMG_EXT = (".jpg", ".jpeg", ".png", ".webp")
# Tags/labels/swatches carry metadata (composition, size) but are NOT garment references —
# feeding a close-up of a care label to the generator produces a photo of a care label.
META_PAT = r"WASHTAG|TAG|SWATCH|LABEL|BARCODE"


def _refs(sku_dir: str) -> list[str]:
    """Reference slots in priority order: front, back, fabric macro.

    Two-stage on purpose. Stage 1 matches the agency's `*_MODEL1.jpg` convention. Stage 2 is
    the fallback that actually makes this survive real folders: ANY images present, in sorted
    order. A bikini SKU shipped as `images.jpeg / images (1).jpeg` returned zero references and
    silently parked as NEEDS_INPUT — the loose match was documented but never built.
    """
    files = [f for f in sorted(os.listdir(sku_dir))
             if f.lower().endswith(IMG_EXT)
             and os.path.isfile(os.path.join(sku_dir, f))
             and not re.search(META_PAT, f, re.I)]

    def pick(*patterns):
        for pat in patterns:
            for f in files:
                if re.search(pat, f, re.I):
                    return os.path.join(sku_dir, f)
        return None

    picks = [pick(r"MODEL1\.", r"_MODEL\.", r"front"),      # front
             pick(r"MODEL3\.", r"back"),                      # back
             pick(r"MODEL5\.", r"fabric", r"macro", r"detail", r"close")]  # fabric macro
    out = [p for p in picks if p]

    if not out:                       # nothing matched a convention — take what is there
        out = [os.path.join(sku_dir, f) for f in files[:3]]
    return out


def _garment_brief(sku_dir: str) -> str:
    """Describe the product from the GARMENT PHOTO cross-checked against its tags.

    Tags alone are unreliable: the blue jeans SKU's tag read as 'Sweater: 100% polyester',
    which mis-slotted the styling AND would now route to the wrong stylist pack. The garment
    photo is authoritative for WHAT the item is; the tags are authoritative for fabric
    composition and size, which no photo can show. Ask for both, and say which wins.
    """
    from agents import llm
    files = sorted(os.listdir(sku_dir))
    tags = [os.path.join(sku_dir, f) for f in files
            if re.search(r"WASHTAG|TAG", f, re.I) and f.lower().endswith((".jpg", ".png"))]
    front = _refs(sku_dir)[:1]
    parts = [{"type": "text", "text":
              "IMAGE 1 is a photo of the actual product on a mannequin. The remaining images "
              "are its care label and price tag.\n"
              "Identify the product from IMAGE 1 — what you SEE is authoritative for what the "
              "garment is (a photo of jeans is jeans, whatever the label says). Use the tags "
              "only for fabric composition and category wording.\n"
              "Reply with ONE line: product type then composition, e.g. "
              "'wide-leg denim jeans: 98% cotton 2% elastane' or "
              "'co-ord set: knit top 100% polyester + dress 95% polyester 5% elastane'. "
              "No other words."}]
    parts += [{"type": "image", "path": p} for p in front + tags[:2]]
    try:
        return llm.chat([{"role": "user", "content": parts}], model_tier="fast",
                        max_tokens=80).strip()
    except Exception as e:
        print(f"    [tag read failed: {str(e)[:60]}]")
        return ""


AUDIENCE_SCHEMA = {"name": "audience", "schema": {
    "type": "object", "additionalProperties": False,
    "properties": {
        "audience":        {"type": "string", "enum": ["adult", "kids", "unclear"]},
        "person_present":  {"type": "boolean"},
        "apparent_minor":  {"type": "boolean"},
        "why":             {"type": "string"},
    },
    "required": ["audience", "person_present", "apparent_minor", "why"]}}


def _audience(sku_dir: str, brief: str) -> dict:
    """Safety guard (plan §7). Kidswear generation is OUT OF SCOPE (owner, 2026-08-09), so this
    does not route to a kids pipeline — it PARKS anything that is not clearly adult womenswear.
    Cheap, and it stops a childrenswear SKU quietly becoming a synthetic child. Ambiguity
    resolves DOWNWARD: 'unclear' parks."""
    from agents import llm
    refs = _refs(sku_dir)[:2]
    parts = [{"type": "text", "text":
              f"Product description: {brief or 'unknown'}\n"
              "These photographs show a garment for sale.\n"
              "  audience       — 'kids' if this is childrenswear or infantwear (child-sized "
              "proportions, child mannequin, juvenile styling/motifs), 'adult' if clearly adult, "
              "'unclear' if you cannot tell.\n"
              "  person_present — is a REAL human being wearing or holding the garment?\n"
              "  apparent_minor — if a person is present, do they appear to be under 18?\n"
              "  why            — one short sentence."},
             *[{"type": "image", "path": p} for p in refs]]
    try:
        raw = llm.chat([{"role": "user", "content": parts}], model_tier="fast",
                       max_tokens=200, json_schema=AUDIENCE_SCHEMA)
        d = json.loads(re.sub(r"^```\w*|```$", "", raw.strip(), flags=re.M))
    except Exception as e:
        # Failing safe means the no-person path, not the model path.
        return {"audience": "unclear", "person_present": False, "apparent_minor": False,
                "blocked": True,
                "why": f"audience detection failed ({str(e)[:40]}) — parked, failing safe"}
    d["blocked"] = (d["audience"] != "adult" or d["apparent_minor"])
    return d


_STYLISTS = None


def _load_stylists() -> dict:
    global _STYLISTS
    if _STYLISTS is None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "config", "shoot_stylists.json")) as f:
            _STYLISTS = json.load(f)["stylists"]
    return _STYLISTS


def _pick_stylist(brief: str, sku: str = "") -> tuple[str, dict, str]:
    """Choose the genre pack, then one of its 3 locations by SKU hash.

    Keyword-first over the tag-read description. Longest keyword wins so 'sports bra' beats
    'bra' and 'shirt formal' beats 'shirt' — otherwise the first-listed pack would swallow
    everything. Falls back to 'everyday', never to nothing.
    """
    packs = _load_stylists()
    text = (brief or "").lower()
    best, best_len = "everyday", 0
    for sid, pack in packs.items():
        for kw in pack.get("match", []):
            if kw in text and len(kw) > best_len:
                best, best_len = sid, len(kw)
    pack = packs[best]
    locs = pack["locations"]
    idx = int(hashlib.sha1((sku or brief or "x").encode()).hexdigest(), 16) % len(locs)
    return best, pack, locs[idx]


def _hero_type(brief: str) -> str:
    """Map the tag-read product description onto a styling-slot key."""
    b = (brief or "").lower()
    if any(w in b for w in ("bikini", "swimsuit", "swimwear", "swim ", "monokini",
                            "tankini", "one-piece", "beachwear", "bathing suit")):
        return "swim"
    if "co-ord" in b or "coord" in b or " set" in b:                  return "set"
    if "shoe" in b or "footwear" in b or "sandal" in b:               return "footwear"
    if "dress" in b and "top" not in b:                               return "dress"
    if any(w in b for w in ("jean", "trouser", "pant", "skirt", "short", "bottom")):
        return "bottom"
    return "top"


PERSONA_REF_CLAUSE = (
    "IMAGE 1 is the MODEL for this shoot. Use that exact woman — her face, bone structure, "
    "skin texture and tone, eye colour, hair colour and length — without alteration. Do not "
    "beautify, smooth or restyle her. She is a real person being photographed. The remaining "
    "reference images show the GARMENT she is wearing.")


def _prompt(brief: str, hero: str, shot: str = "front", pack: dict | None = None,
            location: str = "", persona_ref: bool = False) -> str:
    garment = ("The reference images show ONE real garment: the FIRST is the front, the SECOND "
               "is the back, the THIRD is a macro of the actual fabric. Reproduce that garment "
               "EXACTLY — identical colour, identical knit/weave texture and surface, identical "
               "cut, length, neckline, sleeve length, hem and drape. Do not restyle, do not "
               "tuck, do not change the fit, do not smooth the fabric. Treat the garment's "
               "texture and shape as strictly as you would treat a person's face.")
    if brief:
        garment += f" The product is: {brief}."
    # A packshot has no location: plain seamless studio backdrop, no props, no scenery.
    stage = (PACKSHOT_STAGE if shot == "packshot" else (location or STAGE))
    extra = ""
    if pack:
        # Genre direction: where this apparel belongs, how it is styled, how the model carries
        # it, and what is simply wrong for the category.
        extra = (f" ART DIRECTION — {pack['label']}: {pack['pov']} "
                 f"Lighting: {pack['light']}. Style the model with: {pack['styling']}. "
                 f"Energy: {pack['energy']}. Do NOT include: {pack['avoid']}.")
    who = PERSONA_REF_CLAUSE if persona_ref else f"Dress {PERSONA} in this exact garment."
    if persona_ref:
        # The face comes from the image; the words only describe the garment and the scene.
        garment = garment.replace("the FIRST is the front", "the SECOND is the garment front")
    return (f"{who} {garment} {MANNEQUIN} {STYLING_SLOTS[hero]} "
            f"Setting: {stage}. "
            f"NO legible text, signage or lettering anywhere in the frame.{extra} "
            f"{CRAFT} {SHOTS[shot]} {_framing(hero, shot)} {REALISM} "
            f"Photorealistic premium fashion campaign photograph, shot on medium format, "
            f"natural skin texture and real fabric detail.")


# What "fully dressed" means depends on the product. A bare midriff is a DEFECT on a dress and
# the INTENDED styling on jeans — the gate re-rolled a correct frame until it knew the
# difference, which would have cost a wasted generation on every bottom-wear SKU.
_OUTFIT_RULE = {
    "top":      "    This is a TOP. A bottom garment must be present. A bare midriff is fine.",
    "bottom":   "    This is a BOTTOM (jeans/trousers/skirt). The model wears a CROPPED top and "
                "a BARE MIDRIFF ON PURPOSE — that is correct styling, not a missing garment. "
                "Only mark false if the bottom garment itself is missing.",
    "dress":    "    This is a DRESS covering torso and legs. No bare midriff should appear.",
    "set":      "    This is a CO-ORD SET; both pieces must be worn as shown in the references.",
    "swim":     "    This is SWIMWEAR. Bare midriff, arms, legs and back are ALL correct and "
                "expected. Only mark false if a swimwear piece shown in the references is "
                "missing.",
    "footwear": "    This is FOOTWEAR. The model must be otherwise dressed and wearing shoes.",
}

SCORE_SCHEMA = {"name": "garment_qc", "schema": {
    "type": "object", "additionalProperties": False,
    "properties": {
        # Subject integrity is scored FIRST and separately — round 1 proved the garment axes
        # structurally cannot see this failure (a perfect garment on a plastic mannequin leg).
        "human_complete":    {"type": "boolean"},
        "mannequin_visible": {"type": "boolean"},
        "outfit_complete":   {"type": "boolean"},
        "integrity_note":    {"type": "string"},
        "colour":       {"type": "integer", "minimum": 1, "maximum": 5},
        "texture":      {"type": "integer", "minimum": 1, "maximum": 5},
        "construction": {"type": "integer", "minimum": 1, "maximum": 5},
        "environment":  {"type": "integer", "minimum": 1, "maximum": 5},
        "craft":        {"type": "integer", "minimum": 1, "maximum": 5},
        "face_realism": {"type": "integer", "minimum": 1, "maximum": 5},
        "text_in_frame": {"type": "boolean"},
        "worst_flaw":   {"type": "string"},
    },
    "required": ["human_complete", "mannequin_visible", "outfit_complete", "integrity_note",
                 "colour", "texture", "construction", "environment", "craft",
                 "face_realism", "text_in_frame", "worst_flaw"]}}


def _score(generated: str, ref_front: str, ref_macro: str | None, hero: str = "") -> dict:
    from agents import llm
    parts = [{"type": "text", "text":
              "IMAGE 1 is an AI-generated photo of a model wearing a garment. IMAGE 2 is the "
              "REAL garment on a display mannequin. IMAGE 3 (if present) is a macro of the "
              "REAL fabric.\n\n"
              "FIRST inspect image 1 for subject integrity. Be literal and suspicious, and look "
              "at the WHOLE body — especially below the hemline:\n"
              "  human_complete    — true ONLY if the subject is one complete plausible human: "
              "two natural arms, two natural hands, two natural legs, real skin throughout.\n"
              "  mannequin_visible — true if ANY part of the body looks like a plastic or matte "
              "display mannequin (smooth featureless limbs, moulding seams, joint lines, scuffs, "
              "an off-white plastic sheen), or if a display stand or hanging tag appears. "
              "A mannequin leg is a FAILURE even when the garment above it is perfect.\n"
              "  outfit_complete   — true ONLY if no body region is left bare where a garment "
              "BELONGS. Judge this against the product type below, not against a generic idea "
              "of being covered.\n"
              f"{_OUTFIT_RULE.get(hero, _OUTFIT_RULE['top'])}\n"
              "  integrity_note    — what is wrong with the body or outfit, else 'ok'.\n\n"
              "THEN score fidelity to the REAL garment, 1=totally different, 5=indistinguishable:\n"
              "  colour       — hue, shade, tone\n"
              "  texture      — knit/weave, surface, sheen, fabric character\n"
              "  construction — cut, length, neckline, sleeve length, hem, fit, drape\n"
              "  environment  — backdrop quality: 5 = rich editorial depth, materials and light; "
              "1 = flat, empty or generic\n"
              "  face_realism — does the FACE read as a photographed human? 5 = real skin "
              "texture with pores and tonal variation, natural asymmetry, believable eyes with "
              "iris detail. 1 = poreless waxy airbrushed skin, glassy oversaturated eyes, "
              "doll-like symmetry, obvious AI face.\n"
              "  craft        — does this look like a professional fashion campaign shot by a "
              "real photographer with a directed model? 5 = deliberate asymmetric posture, "
              "weight on one leg, active expressive hands, body angled off-axis, considered "
              "framing and lens compression. 1 = symmetrical catalogue snapshot, feet parallel, "
              "arms hanging limp, dead-centre, flat straight-on angle.\n"
              "Score the garment ONLY — do not soften these because the body is wrong, and do "
              "not inflate them because the body is right.\n"
              "text_in_frame: true if ANY legible lettering or signage appears in image 1.\n"
              "worst_flaw: the single biggest problem, under 15 words."},
             {"type": "image", "path": generated},
             {"type": "image", "path": ref_front}]
    if ref_macro:
        parts.append({"type": "image", "path": ref_macro})
    try:
        raw = llm.chat([{"role": "user", "content": parts}], model_tier="vision",
                       max_tokens=400, json_schema=SCORE_SCHEMA)
        s = json.loads(re.sub(r"^```\w*|```$", "", raw.strip(), flags=re.M))
    except Exception as e:
        return {"colour": 0, "texture": 0, "construction": 0, "environment": 0, "craft": 0, "face_realism": 0,
                "face_realism": 0, "text_in_frame": False, "usable": False,
                "worst_flaw": f"score failed: {str(e)[:50]}"}
    # A frame failing integrity is unusable no matter how well the garment itself scored.
    s["usable"] = bool(s["human_complete"] and s["outfit_complete"]
                       and not s["mannequin_visible"])
    return s


def _generate(model_id: str, refs: list[str], prompt: str, out_path: str) -> str:
    """One paid call. No failover on purpose."""
    from agents import fal_client, model_router
    endpoint = model_router.model_field(model_id, "fal_endpoint")
    ref_key = model_router.model_field(model_id, "ref_input") or "image_urls"
    uris = [fal_client.file_to_data_uri(p) for p in refs]
    args = {"prompt": prompt, "num_images": 1, "output_format": "jpeg", "sync_mode": True,
            "aspect_ratio": "4:5"}
    args[ref_key] = uris if ref_key == "image_urls" else uris[0]
    result = fal_client.run_sync(endpoint, args, timeout=240)
    url = fal_client.extract_media_url(result, keys=("images", "image"))
    if not url:
        raise RuntimeError(f"no image returned: {str(result)[:140]}")
    saved = fal_client.download_media(url, out_path)
    return _deliver(_crop_45(saved))


UPSCALE = os.environ.get("SHOOT_UPSCALE", "auto").lower()   # auto | aura | off


def _deliver(path: str, min_long_edge: int = 2048, dpi: int = 300) -> str:
    """Brief's technical guideline: 2K+ resolution, 300 DPI, 4:5.

    Two paths, and the choice matters for what "2K" actually means:

      · aura_sr  — FAITHFUL super-resolution. Recovers real detail without inventing any,
        which is the only acceptable kind here: plan §13.3 says improving the garment is a
        defect, so a *creative* upscaler (clarity) must never touch a product frame.
        Costs ~$0.02/image.
      · Lanczos  — pure interpolation. Hits the pixel count but adds no detail; honest
        fallback, and what runs if aura_sr is unavailable or SHOOT_UPSCALE=off.

    SHOOT_UPSCALE: auto (aura when the frame is under target, else skip) | aura | off.
    DPI is only metadata — pixel count is what makes a file printable.
    """
    with Image.open(path) as im:
        w, h = im.size
    needs = max(w, h) < min_long_edge

    if needs and UPSCALE in ("auto", "aura"):
        try:
            from agents import upscaler
            up = upscaler.upscale_file(path, os.path.dirname(path) or ".", creative=False)
            if up and os.path.exists(up) and os.path.abspath(up) != os.path.abspath(path):
                os.replace(up, path)
        except Exception as e:                       # never fail delivery on an upscale
            print(f"    [upscale error, falling back: {str(e)[:70]}]")
            from agents import degradation
            degradation.report("shoot_upscale", "info",
                               f"aura_sr unavailable ({str(e)[:80]}) — interpolated instead")
        # Re-MEASURE rather than trusting the return value. upscaler.upscale_file degrades
        # gracefully by returning the ORIGINAL path on a timeout, which is indistinguishable
        # from success — a frame once shipped at 896x1120, under the 2K spec, because the
        # returned path looked like a win. Only the pixels prove it worked.
        with Image.open(path) as im:
            needs = max(im.size) < min_long_edge

    with Image.open(path) as im:
        w, h = im.size
        if needs and max(w, h) < min_long_edge:
            scale = min_long_edge / max(w, h)
            im = im.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
        im.save(path, quality=95, dpi=(dpi, dpi))
    return path


def _crop_45(path: str) -> str:
    """Exact 4:5 on delivery. The edit endpoints cannot be told a precise aspect —
    nano-banana honours `aspect_ratio` only approximately (896x1152 = 0.778) and seedream
    ignores it entirely (2048x2048), so the frame is centre-cropped here instead
    (tools/shoot_probe.py, LLD §0 image_editor note). Width is the anchor: cropping height
    keeps the full garment width, which is what the coverage rules are written against."""
    with Image.open(path) as im:
        w, h = im.size
        target_h = round(w * 5 / 4)
        if abs(w / h - 0.8) < 0.005:
            return path
        if target_h <= h:                       # too tall → trim height, keep full width
            top = (h - target_h) // 2
            box = (0, top, w, top + target_h)
        else:                                   # too wide → trim width
            target_w = round(h * 4 / 5)
            left = (w - target_w) // 2
            box = (left, 0, left + target_w, h)
        im.crop(box).save(path, quality=95)
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", action="store_true")
    ap.add_argument("--inbox", default=INBOX)
    ap.add_argument("--sku", action="append")
    ap.add_argument("--models", default=",".join(CANDIDATES))
    a = ap.parse_args()

    from agents import model_router, pricing
    prices = pricing.load()["image_gen"]
    models = [m.strip() for m in a.models.split(",") if m.strip()]
    skus = a.sku or sorted(d for d in os.listdir(a.inbox)
                           if os.path.isdir(os.path.join(a.inbox, d)))

    def cost(m):
        key = (model_router.model_field(m, "pricing_key") or "").split(".")[-1]
        return prices.get(key, 0.05)

    total = sum(cost(m) for m in models) * len(skus)
    print(f"SKUs   : {len(skus)}  {', '.join(skus)}")
    print(f"models : {', '.join(f'{m} (${cost(m):.3f})' for m in models)}")
    print(f"total  : ~${total:.2f} generation + {len(skus) * (len(models) + 1)} vision calls")
    if not a.go:
        print("\ndry run — nothing spent. Re-run with --go.")
        return 0

    os.makedirs(OUT_DIR, exist_ok=True)
    rows = []
    for sku in skus:
        sku_dir = os.path.join(a.inbox, sku)
        refs = _refs(sku_dir)
        if len(refs) < 1:
            print(f"{sku}: no usable reference images — skipped")
            continue
        print(f"\n── {sku}  ({len(refs)} refs: {', '.join(os.path.basename(r) for r in refs)})")
        brief = _garment_brief(sku_dir)
        hero = _hero_type(brief)
        print(f"    tag says: {brief or '(unread)'}   → hero slot: {hero}")
        prompt = _prompt(brief, hero)
        for m in models:
            out = os.path.join(OUT_DIR, f"{sku}__{m}.jpg")
            try:
                need = 1 if (model_router.model_field(m, "ref_input") == "image_url") else len(refs)
                _generate(m, refs[:need], prompt, out)
                with Image.open(out) as im:
                    size = "x".join(map(str, im.size))
                s = _score(out, refs[0], refs[2] if len(refs) > 2 else None)
                s.update(sku=sku, model=m, size=size, ok=True)
                flag = "OK   " if s.get("usable") else "UNUSABLE"
                print(f"    {m:<18} {size:<11} {flag} "
                      f"col {s['colour']} tex {s['texture']} con {s['construction']} "
                      f"env {s['environment']} craft {s['craft']} face {s['face_realism']}"
                      f"{'  TEXT!' if s['text_in_frame'] else ''}  "
                      f"{(s.get('integrity_note') if not s.get('usable') else s['worst_flaw'])[:42]}")
            except Exception as e:
                s = {"sku": sku, "model": m, "ok": False, "usable": False,
                     "worst_flaw": str(e)[:110], "colour": 0, "texture": 0,
                     "construction": 0, "environment": 0, "craft": 0, "face_realism": 0,
                     "text_in_frame": False}
                print(f"    {m:<18} FAILED  {s['worst_flaw']}")
            rows.append(s)

    print(f"\n{'model':<18} {'ran':>4} {'usable':>7} {'colour':>7} {'texture':>8} "
          f"{'constr':>7} {'env':>5} {'craft':>6} {'face':>5} {'text':>5}")
    print("─" * 80)
    for m in models:
        rs = [r for r in rows if r["model"] == m and r.get("ok")]
        if not rs:
            print(f"{m:<18} {0:>4}       —       —        —       —     —      —     —")
            continue
        # Garment means are computed over USABLE frames only. Round 1's headline numbers were
        # inflated by frames that scored well on cloth while shipping a mannequin leg.
        us = [r for r in rs if r.get("usable")]
        if not us:
            print(f"{m:<18} {len(rs):>4} {0:>7}       —       —        —       —     —     —")
            continue
        c = sum(r["colour"] for r in us) / len(us)
        t = sum(r["texture"] for r in us) / len(us)
        k = sum(r["construction"] for r in us) / len(us)
        e = sum(r["environment"] for r in us) / len(us)
        cr = sum(r.get("craft", 0) for r in us) / len(us)
        fr = sum(r.get("face_realism", 0) for r in us) / len(us)
        print(f"{m:<18} {len(rs):>4} {len(us):>7} {c:>7.2f} {t:>8.2f} {k:>7.2f} {e:>5.2f} "
              f"{cr:>6.2f} {fr:>5.2f} {sum(1 for r in us if r['text_in_frame']):>5}")
    print("\n'usable' = complete human, no mannequin parts, fully dressed. "
          "Garment scores average USABLE frames only.")

    with open(os.path.join(OUT_DIR, "scorecard.json"), "w") as f:
        json.dump(rows, f, indent=2)
    print(f"\nimages + scorecard.json in {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
