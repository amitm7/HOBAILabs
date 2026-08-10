"""
S34 consistency test — does ONE SKU hold together across a whole campaign?

Every bake-off round so far generated a single frame per SKU, so the central claim of the
design — persona, stage, styling and garment stay identical across the set — has never been
tested. This runs a real 4-frame campaign the way the pipeline will:

    frame 1 (front)  = the ANCHOR — generated from the mannequin references
    frames 2..N      = generated from [anchor] + [garment refs], pose/camera the only change

Then it scores drift: is the woman in frame 4 the same person, in the same place, wearing the
same shoes, as the woman in frame 1?

Usage:
    python tools/shoot_campaign.py                                  # dry run
    python tools/shoot_campaign.py --go --sku 443347745_red
    python tools/shoot_campaign.py --go --sku 443340024_black --shots front,alt_front,side,back
"""

import argparse
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

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "bo", os.path.join(os.path.dirname(os.path.abspath(__file__)), "shoot_bakeoff.py"))
bo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bo)                       # reuse the prompts + crop + refs, don't fork

OUT_DIR = os.path.join(os.path.dirname(__file__), "_campaign_out")

DRIFT_SCHEMA = {"name": "campaign_drift", "schema": {
    "type": "object", "additionalProperties": False,
    "properties": {
        "same_person":      {"type": "integer", "minimum": 1, "maximum": 5},
        "same_location":    {"type": "integer", "minimum": 1, "maximum": 5},
        "same_hero":        {"type": "integer", "minimum": 1, "maximum": 5},
        "same_styling":     {"type": "integer", "minimum": 1, "maximum": 5},
        "pose_changed":     {"type": "boolean"},
        "what_drifted":     {"type": "string"},
    },
    "required": ["same_person", "same_location", "same_hero", "same_styling",
                 "pose_changed", "what_drifted"]}}


def _drift(anchor: str, frame: str) -> dict:
    """Compare a later frame against the anchor. This is the campaign-continuity gate."""
    from agents import llm
    parts = [{"type": "text", "text":
              "IMAGE 1 and IMAGE 2 are two frames that are supposed to come from the SAME "
              "fashion shoot: same model, same location, same outfit, photographed minutes "
              "apart. Only the pose and camera angle should differ.\n"
              "Score 1=clearly different, 5=identical:\n"
              "  same_person   — same face, same hair colour, length and style, same skin\n"
              "  same_location — same place, same time of day, same light\n"
              "  same_hero     — the main garment is the same item: colour, cut, length, detail\n"
              "  same_styling  — the SUPPORTING items are the same OBJECTS, not merely similar "
              "ones: the same shoes, the same bottoms or top, the same jewellery. Different "
              "shoes of the same colour scores 2, not 4.\n"
              "  pose_changed  — true if the pose or camera angle genuinely differs\n"
              "  what_drifted  — the single most obvious inconsistency, under 15 words"},
             {"type": "image", "path": anchor},
             {"type": "image", "path": frame}]
    try:
        raw = llm.chat([{"role": "user", "content": parts}], model_tier="vision",
                       max_tokens=300, json_schema=DRIFT_SCHEMA)
        return json.loads(re.sub(r"^```\w*|```$", "", raw.strip(), flags=re.M))
    except Exception as e:
        return {"same_person": 0, "same_location": 0, "same_hero": 0, "same_styling": 0,
                "pose_changed": False, "what_drifted": f"scoring failed: {str(e)[:50]}"}


# ── quality gate ────────────────────────────────────────────────────────────
# Until now every frame was scored and then shipped regardless. A frame that fails the
# integrity check (mannequin parts, incomplete human, missing garment) or falls below the
# fidelity floor is re-rolled with the reason fed back into the prompt, and PARKED after
# MAX_ATTEMPTS so one pathological frame cannot spend forever.
MAX_ATTEMPTS = 3          # 1 initial + 2 re-rolls
FIDELITY_FLOOR = 3        # colour/texture/construction must each clear this


def _accept(score: dict) -> tuple[bool, str]:
    """Ship / re-roll decision for one frame. Returns (ok, reason_if_not)."""
    if not score.get("usable", False):
        return False, (score.get("integrity_note") or "subject integrity failed")
    if score.get("text_in_frame"):
        return False, "legible text or signage appeared in the frame"
    for axis in ("colour", "texture", "construction"):
        if score.get(axis, 0) < FIDELITY_FLOOR:
            return False, f"{axis} scored {score.get(axis)} (floor {FIDELITY_FLOOR}): " \
                          f"{score.get('worst_flaw', '')}"
    return True, ""


_LM_CACHE: dict = {}


def _landmarks_cached(path: str) -> dict:
    """One landmark call per anchor, reused by every derived frame."""
    if path not in _LM_CACHE:
        _LM_CACHE[path] = bo._landmarks(path)
    return _LM_CACHE[path]


def retry_shot(sku: str, inbox: str, shot: str, out_dir: str, model: str = "nano_banana_edit",
               brand: str = "default", cap: float = 0.30) -> dict:
    """Re-shoot ONE frame of an already-shot SKU, reusing its campaign decisions.

    The whole point of a parked frame is that the other six were fine — redoing the SKU to fix
    one of them wastes five good generations. This reloads `_campaign.json` for the hero,
    stylist, location and persona, keeps the existing anchor so continuity holds, and replaces
    just that frame.

    Retrying the ANCHOR is refused: every other frame is conditioned on it, so a new anchor
    silently orphans the set. Redo the SKU for that.
    """
    man_path = os.path.join(out_dir, "_campaign.json")
    if not os.path.exists(man_path):
        return {"ok": False, "error": "no _campaign.json — shoot the SKU first"}
    with open(man_path) as f:
        man = json.load(f)

    hero = man.get("hero", "top")
    frames = man.get("frames", [])
    anchor = next((f["path"] for f in frames
                   if f.get("ok") and f.get("shot") == "front_2"), None)
    if not anchor or not os.path.exists(anchor):
        return {"ok": False, "error": "anchor frame missing — redo the whole SKU"}
    if shot == "front_2":
        return {"ok": False, "error": "the anchor cannot be retried alone — "
                                      "every other frame depends on it. Redo the SKU."}

    target = next((f for f in frames if f.get("shot") == shot), None)
    if not target:
        return {"ok": False, "error": f"{shot} is not part of this campaign"}
    out = target.get("path") or os.path.join(
        out_dir, bo.DESTINATION.get(shot, "d2c"), f"{sku}_{shot}.jpg")
    os.makedirs(os.path.dirname(out), exist_ok=True)

    # Derived frames are crops — re-deriving costs nothing.
    if shot not in bo.GENERATED_SHOTS:
        try:
            bo._derive(anchor, out, hero, shot, _landmarks_cached(anchor))
            target.update(ok=True, status="OK", path=out, reject_reason="", derived_from=anchor)
            _save(man_path, man, frames)
            return {"ok": True, "shot": shot, "status": "OK", "cost_usd": 0.0,
                    "path": out, "derived": True}
        except Exception as e:
            return {"ok": False, "error": str(e)[:160]}

    refs = bo._refs(os.path.join(inbox, sku))
    from agents import model_router, pricing
    key = (model_router.model_field(model, "pricing_key") or "").split(".")[-1]
    per = pricing.load()["image_gen"].get(key, 0.05)

    base = bo._anchor_prompt(shot, hero)
    why, score, cost, accepted = target.get("reject_reason", ""), None, 0.0, False
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if cap and cost + per > cap:
            why = f"spend cap ${cap:.2f} reached"
            break
        prompt = base if not why else (
            f"{base} The previous attempt was rejected because: {why}. "
            f"Fix exactly that while keeping everything else identical.")
        try:
            bo._generate(model, [anchor] + refs[:2], prompt, out)
            cost += per
        except Exception as e:
            why = str(e)[:120]
            continue
        score = bo._score(out, refs[0], refs[2] if len(refs) > 2 else None, hero)
        accepted, why = _accept(score)
        if accepted:
            break

    target.update(ok=score is not None, status="OK" if accepted else "REVIEW", path=out,
                  qc=score, reject_reason="" if accepted else why)
    _save(man_path, man, frames)
    return {"ok": True, "shot": shot, "status": target["status"], "cost_usd": round(cost, 3),
            "path": out, "reject_reason": target["reject_reason"]}


def _save(man_path: str, man: dict, frames: list) -> None:
    man["frames"] = frames
    man["parked"] = [f["shot"] for f in frames if f.get("status") == "REVIEW"]
    man["status"] = "REVIEW" if man["parked"] else "DONE"
    with open(man_path, "w") as f:
        json.dump(man, f, indent=2)


def run_sku(sku: str, inbox: str, model: str, shots: list[str] | None, out_dir: str,
            quiet: bool = False, brand: str = "default", cap: float = 0.0) -> dict:
    """Shoot one SKU as a campaign. Returns a result dict; never raises for a single
    frame failure — a bad frame is recorded, the rest of the set still ships (plan §11.4)."""
    sku_dir = os.path.join(inbox, sku)
    refs = bo._refs(sku_dir)
    if not refs:
        return {"sku": sku, "status": "NEEDS_INPUT", "error": "no usable reference images",
                "frames": [], "cost_usd": 0.0}

    os.makedirs(out_dir, exist_ok=True)
    brief = bo._garment_brief(sku_dir)

    # Safety guard (plan §7): parks anything not clearly adult womenswear BEFORE any spend.
    aud = bo._audience(sku_dir, brief)
    if aud.get("blocked"):
        if not quiet:
            print(f"    BLOCKED — {aud.get('why', 'not clearly adult')}")
        return {"sku": sku, "status": "NEEDS_INPUT", "frames": [], "cost_usd": 0.0,
                "error": f"audience guard: {aud.get('why', '')}", "audience": aud}
    hero = bo._hero_type(brief)
    sid, pack, location = bo._pick_stylist(brief, sku)

    # L0 persona: condition the anchor on a real face IMAGE when a pool exists. Describing a
    # face in words makes every generation invent one from the model's own beauty prior — an
    # image gives it something to be faithful to, and locks the same face across the brand.
    persona = None
    try:
        import importlib.util as _ilu
        _ps = _ilu.spec_from_file_location(
            "pers", os.path.join(os.path.dirname(os.path.abspath(__file__)), "shoot_persona.py"))
        _pm = _ilu.module_from_spec(_ps); _ps.loader.exec_module(_pm)
        # Cast for the apparel genre, but still fixed for this SKU.
        persona = _pm.pick(brand, sku, sid)
    except Exception:
        persona = None
    if not quiet:
        print(f"    {brief[:58]}")
        print(f"    hero={hero}  stylist={pack['label']}  "
              f"persona={persona['id'] + ' (' + persona.get('build', '?') + ')' if persona else 'text-only (no pool)'}")

    from agents import model_router, pricing
    key = (model_router.model_field(model, "pricing_key") or "").split(".")[-1]
    per = pricing.load()["image_gen"].get(key, 0.05)

    # Sets get 7 shots (two details); everything else 6 — brief pages 6-7.
    shots = shots or bo.SHOT_LIST.get(hero, bo.SHOT_LIST["top"])
    if not quiet:
        print(f"    shots={', '.join(shots)}")

    # Generated frames first (the full-length anchor leads), then the tight frames are CROPPED
    # out of the anchor rather than generated — see bo.GENERATED_SHOTS.
    gen = [sh for sh in shots if sh in bo.GENERATED_SHOTS]
    der = [sh for sh in shots if sh not in bo.GENERATED_SHOTS]
    ordered = gen + der

    anchor, frames, drifts, cost, parked = None, [], [], 0.0, []
    for shot in ordered:
        i = shots.index(shot)
        dest = bo.DESTINATION.get(shot, "d2c")
        dest_dir = os.path.join(out_dir, dest)
        os.makedirs(dest_dir, exist_ok=True)
        out = os.path.join(dest_dir, f"{sku}_{i}_{shot}.jpg")

        if shot in der:
            if not anchor:
                frames.append({"shot": shot, "ok": False, "error": "no anchor to crop from"})
                continue
            try:
                lm = _landmarks_cached(anchor)
                bo._derive(anchor, out, hero, shot, lm)
                frames.append({"shot": shot, "ok": True, "path": out, "derived_from": anchor})
                if not quiet:
                    print(f"      {shot:<10} derived (crop of {os.path.basename(anchor)}) — free")
            except Exception as e:
                frames.append({"shot": shot, "ok": False, "error": str(e)[:160]})
                if not quiet:
                    print(f"      {shot:<10} DERIVE FAILED  {str(e)[:60]}")
            continue

        base_prompt = (bo._prompt(brief, hero, shot, pack, location, persona_ref=bool(persona))
                       if anchor is None else bo._anchor_prompt(shot, hero))
        srcs = (([persona["path"]] + refs[:2]) if persona else refs) if anchor is None \
            else [anchor] + refs[:2]

        accepted, score, why, attempts = False, None, "", 0
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if cap and cost + per > cap:
                why = f"spend cap ${cap:.2f} reached"
                if not quiet:
                    print(f"      {shot:<10} STOPPED  {why}")
                break
            prompt = base_prompt if attempt == 1 else (
                f"{base_prompt} The previous attempt was rejected because: {why}. "
                f"Fix exactly that while keeping everything else identical.")
            attempts = attempt
            try:
                bo._generate(model, srcs, prompt, out)
                cost += per
            except Exception as e:
                why = str(e)[:120]
                if not quiet:
                    print(f"      {shot:<10} gen failed ({attempt}/{MAX_ATTEMPTS})  {why[:50]}")
                continue
            score = bo._score(out, refs[0], refs[2] if len(refs) > 2 else None, hero)
            accepted, why = _accept(score)
            if accepted:
                break
            if not quiet:
                print(f"      {shot:<10} re-roll {attempt}/{MAX_ATTEMPTS} — {why[:56]}")

        if score is None and not accepted:
            frames.append({"shot": shot, "ok": False, "status": "FAILED", "error": why})
            if not quiet:
                print(f"      {shot:<10} FAILED  {why[:70]}")
            continue

        status = "OK" if accepted else "REVIEW"
        frames.append({"shot": shot, "ok": True, "status": status, "path": out,
                       "attempts": attempts, "qc": score, "reject_reason": "" if accepted else why})
        if not accepted:
            parked.append(shot)

        if anchor is None:
            anchor = out
            if not quiet:
                print(f"      {shot:<10} anchor   {status}"
                      f"{'' if accepted else '  ← ' + why[:50]}")
            continue
        d = _drift(anchor, out)
        drifts.append(d)
        if not quiet:
            print(f"      {shot:<10} {status:<7} person {d['same_person']} "
                  f"location {d['same_location']} hero {d['same_hero']} "
                  f"styling {d['same_styling']}"
                  f"{'' if accepted else '  ← ' + why[:40]}")

    ok = [f for f in frames if f.get("ok")]
    n = len(drifts) or 1
    manifest = {
        "sku": sku, "product": brief, "hero": hero, "stylist": sid,
        "persona_id": persona["id"] if persona else None,
        "stylist_label": pack["label"], "location": location, "model": model,
        "persona": bo.PERSONA, "shots": shots,
        "frames": frames,
        "continuity": {
            "person":   round(sum(d["same_person"] for d in drifts) / n, 2),
            "location": round(sum(d["same_location"] for d in drifts) / n, 2),
            "hero":     round(sum(d["same_hero"] for d in drifts) / n, 2),
            "styling":  round(sum(d["same_styling"] for d in drifts) / n, 2),
        } if drifts else {},
        "destinations": sorted({bo.DESTINATION.get(f["shot"], "d2c")
                                for f in frames if f.get("ok")}),
        "generated": len([f for f in frames if f.get("ok") and not f.get("derived_from")]),
        "derived": len([f for f in frames if f.get("derived_from")]),
        "parked": parked,
        "cost_usd": round(cost, 3),
        "status": ("REVIEW" if parked else "DONE") if len(ok) == len(shots)
                  else ("FAILED" if not ok else "PARTIAL"),
    }
    with open(os.path.join(out_dir, "_campaign.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", action="store_true")
    ap.add_argument("--inbox", default=bo.INBOX)
    ap.add_argument("--sku", default="443347745_red")
    ap.add_argument("--model", default="nano_banana_edit")
    ap.add_argument("--shots", default="", help="override; default is the product type's list")
    a = ap.parse_args()

    from agents import model_router, pricing
    shots = [s.strip() for s in a.shots.split(",") if s.strip()]
    key = (model_router.model_field(a.model, "pricing_key") or "").split(".")[-1]
    per = pricing.load()["image_gen"].get(key, 0.05)

    print(f"SKU    : {a.sku}")
    print(f"model  : {a.model}")
    print(f"shots  : {' → '.join(shots) if shots else 'auto (by product type)'}"
          f"   (frame 1 is the anchor)")
    print(f"cost   : ~${per * (len(shots) or 6):.2f} + {len(shots) or 6} vision calls")
    if not a.go:
        print("\ndry run — nothing spent. Re-run with --go.")
        return 0

    sku_dir = os.path.join(a.inbox, a.sku)
    refs = bo._refs(sku_dir)
    if not refs:
        print(f"no reference images in {sku_dir}", file=sys.stderr)
        return 1
    os.makedirs(OUT_DIR, exist_ok=True)

    brief = bo._garment_brief(sku_dir)
    hero = bo._hero_type(brief)
    sid, pack, location = bo._pick_stylist(brief, a.sku)
    print(f"\ntag    : {brief or '(unread)'}")
    print(f"hero   : {hero}")
    print(f"stylist: {pack['label']}  ({sid})")
    print(f"stage  : {location[:96]}…\n")

    anchor, rows = None, []
    for i, shot in enumerate(shots):
        out = os.path.join(OUT_DIR, f"{a.sku}__{i}_{shot}.jpg")
        if anchor is None:
            prompt, srcs = bo._prompt(brief, hero, shot, pack, location), refs
        else:
            # Anchor FIRST — the clause refers to it as "IMAGE 1".
            prompt, srcs = bo._anchor_prompt(shot, hero), [anchor] + refs[:2]
        try:
            bo._generate(a.model, srcs, prompt, out)
        except Exception as e:
            print(f"  {shot:<10} FAILED  {str(e)[:100]}")
            continue
        if anchor is None:
            anchor = out
            print(f"  {shot:<10} anchor  {os.path.basename(out)}")
            continue
        d = _drift(anchor, out)
        d.update(shot=shot, path=out)
        rows.append(d)
        print(f"  {shot:<10} person {d['same_person']} location {d['same_location']} "
              f"hero {d['same_hero']} styling {d['same_styling']} "
              f"{'pose-varied' if d['pose_changed'] else 'POSE STATIC'}  {d['what_drifted'][:40]}")

    if rows:
        n = len(rows)
        print(f"\n  {'mean vs anchor':<16} person {sum(r['same_person'] for r in rows)/n:.2f}  "
              f"location {sum(r['same_location'] for r in rows)/n:.2f}  "
              f"hero {sum(r['same_hero'] for r in rows)/n:.2f}  "
              f"styling {sum(r['same_styling'] for r in rows)/n:.2f}")
        with open(os.path.join(OUT_DIR, f"{a.sku}_drift.json"), "w") as f:
            json.dump(rows, f, indent=2)

    # Contact sheet — the whole point is seeing the set together.
    frames = sorted(f for f in os.listdir(OUT_DIR) if f.startswith(a.sku) and f.endswith(".jpg"))
    if frames:
        cell = 460
        sheet = Image.new("RGB", (cell * len(frames), cell + 20), (250, 250, 248))
        for i, f in enumerate(frames):
            im = Image.open(os.path.join(OUT_DIR, f))
            im.thumbnail((cell - 10, cell - 10))
            sheet.paste(im, (i * cell + 5, 5))
        sheet.save(os.path.join(OUT_DIR, f"{a.sku}_campaign.png"))
        print(f"\ncontact sheet: {OUT_DIR}/{a.sku}_campaign.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
