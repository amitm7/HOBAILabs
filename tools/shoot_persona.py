"""
S34 persona pool (L0) — mint a brand's faces ONCE, then reuse them forever.

Why this exists: asking the generator for realistic skin does not work. Round 4 requested pores,
asymmetry and iris detail explicitly and all 8 frames still came back poreless and glassy-eyed,
because with no face to be faithful to, each generation invents one from the model's own beauty
prior. Face quality is not a per-frame prompting problem — it is a one-time curation problem.

So: mint N candidate faces from the brand's locked spec, score each on photographic realism,
keep the good ones, and condition every future SKU on those images. A pool of 12 costs well
under a dollar ONCE and is then amortised across the entire catalogue, which is what makes it
affordable to be fussy.

    python tools/shoot_persona.py --mint --count 12        # generate + score candidates
    python tools/shoot_persona.py --list                   # see the pool, with scores
    python tools/shoot_persona.py --drop p03 p07           # cull the ones that read as AI
    python tools/shoot_persona.py --show 443347745_red     # which face this SKU would get

Pool lives outside the repo (brand assets are not source): ~/.hob_cache/shoot_personas/<brand>/
See docs/PRODUCT_SHOOT_PLAN.md §5.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(".env", override=True)

_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("bo", os.path.join(_here, "shoot_bakeoff.py"))
bo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bo)

ROOT = os.path.join(os.path.expanduser("~"), ".hob_cache", "shoot_personas")

# Each candidate varies only in ways the brand spec leaves open, so every face is on-spec but a
# DIFFERENT person. Without this they collapse into one face with slightly different hair.
# Each entry moves SEVERAL axes at once — face, hair, tone, age read, build. Moving one axis
# at a time produced twelve versions of one woman; the spec left too little room and the
# variants were too timid.
VARIANTS = [
    # Ethnicity is the strongest divergence lever — a 30-face mint on a single background
    # came back 26/30 slim, 22-26, blonde-or-light-brown however hard the variants pushed
    # build and hair. Spread the backgrounds and the faces actually differ.
    "Northern European, age 23, very fair skin, long straight light-blonde hair, blue eyes, round soft face, slim",
    "Korean, age 25, warm ivory skin, long straight black hair, dark brown eyes, oval face with soft jaw, slim",
    "Russian/Slavic, age 27, fair skin, shoulder-length ash-blonde hair, grey eyes, high sharp cheekbones, athletic",
    "Chinese, age 24, light warm skin, long black hair with a soft wave, dark eyes, heart-shaped face, slim",
    "American mixed heritage, age 26, medium-tan skin, long dark-brown curly hair, hazel eyes, full lips, softly athletic",
    "Mediterranean European, age 28, olive skin, long dark-brown wavy hair, brown eyes, strong straight brows, curvier proportions",
    "Korean, age 22, fair porcelain skin, shoulder-length dark-brown hair with a blunt fringe, monolid eyes, narrow face, very slim",
    "Northern European, age 29, fair freckled skin, long auburn wavy hair, green eyes, long oval face, slim",
    "Chinese, age 27, light skin, sleek shoulder-length black bob, dark eyes, wide cheekbones, athletic",
    "Russian/Slavic, age 24, very fair skin, very long platinum-blonde straight hair, pale blue eyes, angular jaw, slim",
    "American, age 25, light-tan skin, long honey-brown beachy waves, brown eyes, square jaw, athletic",
    "Eastern European, age 23, fair skin, mid-length light-brown straight hair, grey-green eyes, broad forehead, slim",
    "Korean, age 26, warm light skin, long layered dark-brown hair, dark eyes, soft rounded jaw, slim",
    "Chinese, age 23, fair skin, very long straight black hair, dark eyes, delicate small features, very slim",
    "Northern European, age 28, fair skin, short blonde pixie cut, blue eyes, strong cheekbones, athletic",
    "American mixed heritage, age 24, deeper tan skin, long black tightly waved hair, dark brown eyes, full cheeks, curvier proportions",
    "Mediterranean European, age 26, light-olive skin, shoulder-length dark wavy hair, brown eyes, aquiline nose, slim",
    "Russian/Slavic, age 22, very fair skin, long light-brown straight hair, blue-grey eyes, wide-set eyes, slim",
    "Korean, age 28, ivory skin, long straight dark-brown hair, dark eyes, defined jawline, athletic",
    "Northern European, age 25, fair skin with visible freckles, long strawberry-blonde waves, green eyes, narrow chin, slim",
    "Chinese, age 29, light warm skin, shoulder-length black hair loosely waved, dark eyes, oval face, softly athletic",
    "American, age 23, fair skin, long dark-blonde straight hair, hazel eyes, small upturned nose, slim",
    "Eastern European, age 27, fair skin, long ash-brown hair, grey eyes, high forehead, athletic",
    "Korean, age 24, warm fair skin, long black hair with curtain fringe, dark eyes, soft heart-shaped face, very slim",
    "Mediterranean European, age 22, olive skin, very long dark-brown curly hair, dark brown eyes, full lips, curvier proportions",
    "Northern European, age 30, fair skin, shoulder-length straight dark-blonde hair, blue eyes, defined cheekbones, slim",
    "Chinese, age 25, light skin, long straight black hair, dark eyes, angular cheekbones, lean athletic",
    "Russian/Slavic, age 26, fair skin, long wavy golden-blonde hair, green eyes, strong straight nose, athletic",
    "American mixed heritage, age 28, tan skin, mid-length dark-brown waves, brown eyes, rounded jaw, softly athletic",
    "Korean, age 23, fair skin, long straight black hair, dark eyes, small delicate chin, slim",
]

GENRES = ["everyday", "formal", "ethnic_indian", "festive", "activewear", "sportswear",
          "loungewear", "denim", "swim_resort", "winterwear", "streetwear", "luxury"]

FACE_SCHEMA = {"name": "face_realism", "schema": {
    "type": "object", "additionalProperties": False,
    "properties": {
        "realism":    {"type": "integer", "minimum": 1, "maximum": 5},
        "on_spec":    {"type": "integer", "minimum": 1, "maximum": 5},
        "ai_tells":   {"type": "string"},
        "build":      {"type": "string", "enum": ["slim", "athletic", "curvy"]},
        "hair":       {"type": "string"},
        "age_reads":  {"type": "integer", "minimum": 16, "maximum": 45},
        "suits":      {"type": "array", "items": {"type": "string", "enum": GENRES}},
    },
    "required": ["realism", "on_spec", "ai_tells", "build", "hair", "age_reads", "suits"]}}


def _dir(brand: str) -> str:
    d = os.path.join(ROOT, brand)
    os.makedirs(d, exist_ok=True)
    return d


def _pool_path(brand: str) -> str:
    return os.path.join(_dir(brand), "pool.json")


def spec_path(brand: str) -> str:
    return os.path.join(_dir(brand), "spec.txt")


def load_spec(brand: str, default: str) -> str:
    """A brand's casting brief, stored per brand. Two brands with the SAME ranges will
    produce lookalike pools however separately they are stored — exclusivity lives in the
    ranges differing, not in the filing."""
    p = spec_path(brand)
    return open(p).read().strip() if os.path.exists(p) else default


def load_pool(brand: str) -> list[dict]:
    p = _pool_path(brand)
    if not os.path.exists(p):
        return []
    with open(p) as f:
        return json.load(f)


def pick(brand: str, sku: str, genre: str = "") -> dict | None:
    """Cast one face for this SKU.

    Two rules, in order:
      1. CASTING — if a genre is given, only consider models tagged as suiting it. An
         athletic build sells activewear; a different face suits festive or luxury. Falls
         back to the whole pool when nothing is tagged, so casting narrows but never blocks.
      2. FIXED PER SKU — the choice is a hash of the SKU, so the same SKU always returns the
         same model, on a re-run and on a re-shoot next season.
    """
    pool = [p for p in load_pool(brand) if not p.get("dropped")]
    if not pool:
        return None
    if genre:
        suited = [p for p in pool if genre in (p.get("suits") or [])]
        if suited:
            pool = suited
    pool.sort(key=lambda p: p["id"])             # stable order regardless of pool file order
    idx = int(hashlib.sha1(sku.encode()).hexdigest(), 16) % len(pool)
    return pool[idx]


def _score_face(path: str, spec: str) -> dict:
    from agents import llm
    try:
        raw = llm.chat([{"role": "user", "content": [
            {"type": "text", "text":
             "Judge this portrait as a photograph.\n"
             "  realism  — 5 = indistinguishable from an unretouched photo of a real person: "
             "visible pores, real skin tonal variation, natural facial asymmetry, believable "
             "eyes with iris detail. 1 = obvious AI render: poreless waxy skin, glassy "
             "oversaturated eyes, doll-like symmetry, airbrushed beauty look.\n"
             f"  on_spec  — how well it matches this brief: {spec}\n"
             "  ai_tells — the specific giveaways, under 15 words.\n"
             "  build    — slim | athletic | curvy\n"
             "  hair     — colour and length in four words\n"
             "  age_reads— the age she actually looks, in years\n"
             "  suits    — which of these apparel genres this model is a natural casting "
             f"choice for (pick every one that fits, at least three): {', '.join(GENRES)}"},
            {"type": "image", "path": path}]}],
            model_tier="vision", max_tokens=200, json_schema=FACE_SCHEMA)
        return json.loads(re.sub(r"^```\w*|```$", "", raw.strip(), flags=re.M))
    except Exception as e:
        return {"realism": 0, "on_spec": 0, "ai_tells": f"scoring failed: {str(e)[:40]}",
                "build": "", "hair": "", "age_reads": 0, "suits": []}


def _mint(brand: str, spec: str, count: int, model: str) -> list[dict]:
    """Text-to-image only — see the --model note in main()."""
    from agents import fal_client, model_router
    endpoint = model_router.model_field(model, "fal_endpoint")
    d = _dir(brand)
    pool = []
    for i in range(min(count, len(VARIANTS))):
        pid = f"p{i:02d}"
        out = os.path.join(d, f"{pid}.jpg")
        prompt = (
            f"Editorial headshot and upper body of {spec}. Distinguishing features: "
            f"{VARIANTS[i]}. Neutral relaxed expression, looking straight into the lens, "
            f"plain mid-grey studio background, soft large-source daylight from one side. "
            f"{bo.REALISM} Shot on a medium format camera with an 85mm lens at f/4, "
            f"unretouched RAW frame straight from the camera, natural colour.")
        try:
            res = fal_client.run_sync(endpoint, {
                "prompt": prompt, "num_images": 1, "output_format": "jpeg",
                "sync_mode": True, "aspect_ratio": "4:5"}, timeout=180)
            url = fal_client.extract_media_url(res, keys=("images", "image"))
            if not url:
                print(f"  {pid}  no image returned")
                continue
            fal_client.download_media(url, out)
        except Exception as e:
            print(f"  {pid}  FAILED  {str(e)[:80]}")
            continue
        s = _score_face(out, spec)
        pool.append({"id": pid, "path": out, "variant": VARIANTS[i],
                     "realism": s["realism"], "on_spec": s["on_spec"],
                     "ai_tells": s["ai_tells"], "build": s.get("build", ""),
                     "hair": s.get("hair", ""), "age_reads": s.get("age_reads", 0),
                     "suits": s.get("suits", []), "dropped": False})
        print(f"  {pid}  realism {s['realism']}  {s.get('build',''):<9} "
              f"age~{s.get('age_reads','?'):<3} {s.get('hair','')[:26]:<28} "
              f"suits {len(s.get('suits', []))}")
    pool.sort(key=lambda p: (-p["realism"], -p["on_spec"]))
    return pool


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", default="default")
    ap.add_argument("--spec", default=bo.PERSONA)
    # Text-to-image, NOT an edit model: minting a face from a spec has no reference image,
    # and the /edit endpoints 422 without one.
    ap.add_argument("--model", default="nano_banana")
    ap.add_argument("--mint", action="store_true")
    ap.add_argument("--count", type=int, default=12)
    ap.add_argument("--go", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--drop", nargs="*", default=None)
    ap.add_argument("--show", default="")
    ap.add_argument("--genre", default="", help="casting filter, e.g. activewear")
    a = ap.parse_args()

    a.spec = load_spec(a.brand, a.spec)

    if a.mint:
        from agents import model_router, pricing
        key = (model_router.model_field(a.model, "pricing_key") or "").split(".")[-1]
        per = pricing.load()["image_gen"].get(key, 0.05)
        n = min(a.count, len(VARIANTS))
        print(f"brand : {a.brand}")
        print(f"spec  : {a.spec[:88]}…")
        print(f"mint  : {n} candidate faces  →  ~${per * n:.2f} ONE TIME, "
              f"amortised over the whole catalogue")
        if not a.go:
            print("\ndry run — nothing spent. Add --go to mint.")
            return 0
        print()
        pool = _mint(a.brand, a.spec, n, a.model)
        if not pool:
            print("nothing minted")
            return 1
        with open(_pool_path(a.brand), "w") as f:
            json.dump(pool, f, indent=2)
        if not os.path.exists(spec_path(a.brand)):
            with open(spec_path(a.brand), "w") as f:
                f.write(a.spec)
        keep = [p for p in pool if p["realism"] >= 4]
        print(f"\n{len(pool)} minted, {len(keep)} scored realism>=4")
        print(f"pool: {_pool_path(a.brand)}")
        print("Review the images and cull with --drop <id> <id>. "
              "The pool is the brand's faces — it is worth being fussy once.")
        return 0

    if a.drop is not None:
        pool = load_pool(a.brand)
        for p in pool:
            if p["id"] in a.drop:
                p["dropped"] = True
        with open(_pool_path(a.brand), "w") as f:
            json.dump(pool, f, indent=2)
        if not os.path.exists(spec_path(a.brand)):
            with open(spec_path(a.brand), "w") as f:
                f.write(a.spec)
        print(f"dropped {', '.join(a.drop)} — {len([p for p in pool if not p['dropped']])} left")
        return 0

    if a.show:
        p = pick(a.brand, a.show, a.genre)
        print(f"{a.show}  →  {p['id']}  realism {p['realism']}  {p['path']}" if p
              else f"no pool for brand '{a.brand}' — run --mint first")
        return 0

    pool = load_pool(a.brand)
    if not pool:
        print(f"no pool for brand '{a.brand}'. Run:  --mint --count 12 --go")
        return 0
    print(f"{'id':<5} {'real':>4} {'build':<9} {'age':>4}  {'hair':<26} suits")
    print("─" * 92)
    for p in pool:
        mark = "×" if p.get("dropped") else " "
        print(f"{p['id']:<5}{mark}{p['realism']:>4} {p.get('build',''):<9} "
              f"{p.get('age_reads',''):>4}  {p.get('hair','')[:26]:<26} "
              f"{','.join(p.get('suits', []))[:34]}")
    print(f"\n{len([p for p in pool if not p.get('dropped')])} active · {_dir(a.brand)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
