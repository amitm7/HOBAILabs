"""
Brand / advertisement layer (Phase B1) — see docs/BRAND_PLAN.md.

The storytelling engine is reused unchanged; this module adds the brand-only
pieces that sit ON TOP of it:

  · extract_brief(text)         paste-the-brief → structured fields (LLM, fast tier)
  · validate_mandatories(...)   the HARD-BLOCK gate (run before any spend)
  · brand_scene_context(brand)  visual direction context for treatment/scene design
  · disclosure_text(brand)      the burned-in "Paid partnership with …" line
  · build_cta_card(...)         the auto-appended end-card image (logo + CTA)

HARD RULE (BRAND_PLAN §5): the AI never writes ad copy or regulated claims. All
on-screen / spoken copy is brand-supplied and placed verbatim. extract_brief only
PARSES text the brand already wrote; it must not invent or rephrase claims.
"""

import os

_BRIEF_FIELDS = ("name", "product", "objective", "key_message",
                 "cta_text", "cta_url", "tagline")

_BRIEF_SCHEMA = {"name": "brief", "schema": {
    "type": "object", "additionalProperties": False,
    "properties": {f: {"type": "string"} for f in _BRIEF_FIELDS},
    "required": list(_BRIEF_FIELDS),
}}


def extract_brief(text: str) -> dict:
    """
    Parse a pasted brand brief into structured fields. PARSE ONLY — copy the
    brand's own words; never invent objective/claims/CTA. Returns {} on failure
    (the UI then keeps whatever the operator typed in the structured fields).
    """
    text = (text or "").strip()
    if not text:
        return {}
    try:
        from agents import llm
        out = llm.chat(
            [{"role": "user", "content": (
                "Extract these fields from the brand brief below. Copy the brand's "
                "OWN words verbatim — do NOT invent, summarise, or rephrase claims, "
                "the call-to-action, or the tagline. Leave a field empty ("") if the "
                "brief doesn't state it.\n"
                f"Fields: {', '.join(_BRIEF_FIELDS)}.\n\n"
                f"BRIEF:\n{text[:4000]}\n\n"
                'Reply ONLY as JSON with exactly those keys.'
            )}],
            json_mode=True, json_schema=_BRIEF_SCHEMA,
            max_tokens=600, model_tier="fast",
        )
        data = llm.json_loads_lenient(out)
        return {f: (data.get(f) or "").strip() for f in _BRIEF_FIELDS}
    except Exception as e:
        print(f"[Brand] brief extraction failed ({e}) — keep typed fields")
        return {}


def validate_mandatories(frames: list[dict], brand: dict) -> list[str]:
    """
    Return a list of MISSING mandatories (human-readable). Empty list == OK to
    render. Called synchronously before a brand render starts so nothing is
    spent on an incomplete ad. Which mandatories are enforced is brand-set
    (default: all on).
    """
    m = (brand or {}).get("mandatories") or {}
    enforce = {k: m.get(k, True) for k in ("logo", "cta", "disclosure", "product_beat")}
    missing = []

    if enforce["logo"]:
        logo = (brand.get("logo_path") or "").strip()
        if not logo or not os.path.exists(logo):
            missing.append("Brand logo (upload the logo file)")

    if enforce["cta"] and not (brand.get("cta_text") or "").strip():
        missing.append("Call-to-action text (e.g. “Use Tata Sampurna”)")

    if enforce["disclosure"] and not brand.get("disclosure", True):
        missing.append("Sponsored disclosure must stay on for branded content")

    if enforce["product_beat"] and not any(f.get("product_beat") for f in frames):
        missing.append("Mark at least one frame as the product/brand beat")

    # Brand-supplied audio modes require the actual file present.
    if brand.get("vo_mode") == "brand_audio" and not (brand.get("vo_audio_path") or "").strip():
        missing.append("Brand voice-over audio file (VO set to brand-supplied)")
    if brand.get("music_mode") == "brand_audio" and not (brand.get("music_audio_path") or "").strip():
        missing.append("Brand music file (music set to brand-supplied)")

    return missing


def disclosure_text(brand: dict) -> str:
    name = (brand.get("name") or "the brand").strip()
    return f"Paid partnership with {name}"


def brand_scene_context(brand: dict) -> str:
    """
    Visual-direction context handed to the treatment + scene design so shots
    support the campaign. Carries the product/message for FRAMING only — it does
    NOT become on-screen or spoken copy (that stays brand-supplied + verbatim).
    """
    if not brand:
        return ""
    bits = []
    if brand.get("name"):        bits.append(f"Brand: {brand['name']}")
    if brand.get("product"):     bits.append(f"Product: {brand['product']}")
    if brand.get("objective"):   bits.append(f"Campaign objective: {brand['objective']}")
    if brand.get("key_message"): bits.append(f"Key message to support visually: {brand['key_message']}")
    if not bits:
        return ""
    return ("BRAND AD CONTEXT — design shots that support this campaign. Do NOT put "
            "any claim or marketing copy in the image; copy is added separately and "
            "brand-supplied.\n" + "\n".join(bits))


# ── CTA end-card (auto-appended final beat) ───────────────────────────────────

def _cta_font(size: int):
    from PIL import ImageFont
    here = os.path.dirname(__file__)
    for p in (os.path.join(here, "..", "deploy", "fonts", "Montserrat-Regular.ttf"),):
        try:
            return ImageFont.truetype(os.path.abspath(p), size)
        except Exception:
            pass
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def _hex_to_rgb(h: str, default=(12, 12, 12)) -> tuple:
    h = (h or "").lstrip("#")
    if len(h) == 6:
        try:
            return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
        except ValueError:
            pass
    return default


def build_cta_card(brand: dict, out_path: str, width: int = 1080, height: int = 1920) -> str:
    """
    Render the end-card still: brand-colour background, centered logo, tagline,
    and the CTA text + link line. All text is brand-supplied. Returns out_path.
    """
    from PIL import Image, ImageDraw
    colors = brand.get("colors") or []
    bg = _hex_to_rgb(colors[0] if colors else "", default=(12, 12, 12))
    # readable text colour from bg luminance
    lum = 0.2126 * bg[0] + 0.7152 * bg[1] + 0.0722 * bg[2]
    fg = (20, 20, 20) if lum > 150 else (245, 245, 245)

    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)
    cy = height // 2 - 220

    logo = (brand.get("logo_path") or "").strip()
    if logo and os.path.exists(logo):
        try:
            lg = Image.open(logo).convert("RGBA")
            maxw = int(width * 0.55)
            scale = min(maxw / lg.width, 1.0)
            lg = lg.resize((int(lg.width * scale), int(lg.height * scale)))
            img.paste(lg, ((width - lg.width) // 2, cy), lg)
            cy += lg.height + 60
        except Exception as e:
            print(f"[Brand] CTA logo paste failed ({e})")

    def _centered(text: str, size: int, y: int, color) -> int:
        if not text:
            return y
        font = _cta_font(size)
        try:
            w = draw.textlength(text, font=font)
        except Exception:
            w = len(text) * size * 0.5
        draw.text(((width - w) / 2, y), text, fill=color, font=font)
        return y + size + 28

    cy = _centered(brand.get("tagline", ""),  58, cy, fg)
    cy = _centered(brand.get("cta_text", ""),  72, cy + 20, fg)
    link = (brand.get("cta_url") or "").strip() or "Link in bio"
    _centered(link, 44, cy + 10, fg)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    img.save(out_path, "JPEG", quality=92)
    return out_path
