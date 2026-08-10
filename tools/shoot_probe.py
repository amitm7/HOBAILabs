"""
S34 probe — answer two mechanical questions before any product code is written:

  1. Does the identity/edit endpoint actually honour MULTIPLE reference images?
     (agents/image_editor._fal_edit sends an `image_urls` array — this proves the
     endpoint reads past the first entry, which the whole persona+stage+garment
     conditioning design depends on.)
  2. How is a 4:5 frame forced on the EDIT path? `_fal_edit` sends no size argument
     today, so output aspect follows the reference. The shoot vertical needs 4:5.

Sends two synthetic reference images (one says ALPHA, one says BETA), asks the model
to use both, then reads the result back with the existing vision seam. Objective
pass/fail, no eyeballing.

Usage:
    python tools/shoot_probe.py             # dry run — prints cost, spends nothing
    python tools/shoot_probe.py --go        # actually calls fal

See docs/PRODUCT_SHOOT_PLAN.md §10.
"""

import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Only load .env when this file is RUN as a script. Imported as a library (agents/shoot.py
# does this lazily inside a request) an override=True load would silently re-apply the
# on-disk .env over the server's own environment mid-request — which is how a production
# HOB_AUTH_DISABLED could be resurrected from a file nobody meant to deploy.
if __name__ == "__main__":
    load_dotenv(".env", override=True)

from PIL import Image, ImageDraw  # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(__file__), "_probe_out")

# Each variant is one paid call: (label, extra args merged into the fal payload).
# Baseline first so a failure there tells us the endpoint itself is the problem.
VARIANTS = [
    ("baseline (no size arg)", {}),
    ("aspect_ratio 4:5",       {"aspect_ratio": "4:5"}),
    ("image_size 1664x2080",   {"image_size": {"width": 1664, "height": 2080}}),
]

PROMPT = ("Combine both reference images into a single picture: place the red triangle "
          "from the first image next to the blue circle from the second image, both fully "
          "visible on a plain background.")


def _make_refs() -> list[str]:
    """Two unmistakable references: a red TRIANGLE and a blue CIRCLE.
    Shapes, not text — PIL's default font renders ~11px on a 768px canvas, which
    no model can read, and an unreadable fixture yields a false negative."""
    os.makedirs(OUT_DIR, exist_ok=True)
    paths = []
    img = Image.new("RGB", (768, 768), (245, 245, 240))
    ImageDraw.Draw(img).polygon([(384, 130), (650, 620), (118, 620)], fill=(210, 30, 30))
    p = os.path.join(OUT_DIR, "ref_triangle.png")
    img.save(p); paths.append(p)

    img = Image.new("RGB", (768, 768), (245, 245, 240))
    ImageDraw.Draw(img).ellipse([130, 130, 638, 638], fill=(30, 70, 210))
    p = os.path.join(OUT_DIR, "ref_circle.png")
    img.save(p); paths.append(p)
    return paths


def _words_in(path: str) -> str:
    """Read the generated image back through the existing vision seam."""
    from agents import llm
    from agents.fal_client import file_to_data_uri
    try:
        return llm.chat(
            [{"role": "user", "content": [
                {"type": "text", "text": "Does this image contain a red triangle, a blue circle, "
                                         "both, or neither? Answer with just one word: "
                                         "BOTH, TRIANGLE, CIRCLE, or NEITHER."},
                {"type": "image_url", "image_url": {"url": file_to_data_uri(path)}},
            ]}],
            model_tier="vision", max_tokens=16,
        ).strip().upper()[:8]
    except Exception as e:                                    # vision is best-effort here
        return f"read-failed ({str(e)[:40]})"


def _run_variant(endpoint: str, label: str, extra: dict, refs: list[str]) -> dict:
    from agents import fal_client
    args = {"prompt": PROMPT, "num_images": 1, "output_format": "jpeg", "sync_mode": True,
            "image_urls": [fal_client.file_to_data_uri(p) for p in refs], **extra}
    try:
        result = fal_client.run_sync(endpoint, args, timeout=180)
        url = fal_client.extract_media_url(result, keys=("images", "image"))
        if not url:
            return {"label": label, "ok": False, "note": f"no image in result: {str(result)[:120]}"}
        out = os.path.join(OUT_DIR, f"out_{label.split()[0]}.jpg")
        fal_client.download_media(url, out)
        with Image.open(out) as im:
            w, h = im.size
        return {"label": label, "ok": True, "size": f"{w}x{h}",
                "ratio": round(w / h, 3), "words": _words_in(out), "path": out}
    except Exception as e:
        return {"label": label, "ok": False, "note": str(e)[:160]}


def main() -> int:
    from agents import model_router, pricing

    endpoint = model_router.model_field("nano_banana_edit", "fal_endpoint")
    per_call = pricing.load()["image_gen"].get("nano_banana_usd", 0.04)
    total = per_call * len(VARIANTS)

    print(f"endpoint : {endpoint}")
    print(f"variants : {len(VARIANTS)}  →  ~${total:.3f} "
          f"(+{len(VARIANTS)} cheap vision read-backs)")

    if "--go" not in sys.argv:
        print("\ndry run — nothing spent. Re-run with --go to call fal.")
        return 0
    if not os.environ.get("FAL_API_KEY"):
        print("FAL_API_KEY not set", file=sys.stderr)
        return 1

    refs = _make_refs()
    print(f"refs     : {', '.join(os.path.basename(p) for p in refs)}\n")

    rows = [_run_variant(endpoint, label, extra, refs) for label, extra in VARIANTS]

    print(f"\n{'variant':<26} {'result':<12} {'ratio':<7} {'words seen'}")
    print("─" * 72)
    for r in rows:
        if r["ok"]:
            print(f"{r['label']:<26} {r['size']:<12} {r['ratio']:<7} {r['words']}")
        else:
            print(f"{r['label']:<26} FAILED       -       {r['note']}")

    ok = [r for r in rows if r.get("ok")]
    both = [r for r in ok if r.get("words") == "BOTH"]
    base = next((r for r in ok if r["label"].startswith("baseline")), None)
    moved = [r for r in ok if base and abs(r["ratio"] - base["ratio"]) > 0.02]
    exact = [r for r in ok if abs(r["ratio"] - 0.8) < 0.01]

    print("\nverdict")
    print(f"  multi-reference honoured : {'YES' if both else 'NO'}"
          f"{'  (' + both[0]['label'] + ')' if both else ''}")
    if exact:
        print(f"  exact 4:5                : YES via {exact[0]['label']}")
    elif moved:
        print(f"  aspect control           : PARTIAL — {moved[0]['label']} moved output to "
              f"{moved[0]['size']} (ratio {moved[0]['ratio']}, 4:5 is 0.8)")
        print("  → request the aspect, then crop to exact 4:5 on delivery.")
    else:
        print("  aspect control           : NO — no argument changed the output size;"
              "\n    4:5 must come from the reference's own aspect or a post-crop.")
    print(f"\noutputs in {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
