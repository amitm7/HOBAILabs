"""Supported output languages for multi-language reels.

Operator-chosen — the pipeline NEVER fans out to every language automatically.
The picker defaults to the source language only; the operator opts in per render.

Launch set is 5. Adding a language is two edits: one entry here, plus its native
regional voice ids under `language_voices` in config/voices.json.
"""

from __future__ import annotations

# code → display names. `code` is a short language tag used everywhere downstream
# (translation prompt, voice lookup, caption file naming).
SUPPORTED_LANGUAGES: dict[str, dict[str, str]] = {
    "hi": {"english": "Hindi",   "native": "हिन्दी"},
    "en": {"english": "English", "native": "English"},
    "mr": {"english": "Marathi", "native": "मराठी"},
    "pa": {"english": "Punjabi", "native": "ਪੰਜਾਬੀ"},
    "bn": {"english": "Bangla",  "native": "বাংলা"},
}

DEFAULT_LANGUAGE = "en"

# Tolerate full names / common spellings so a sloppy client payload still resolves.
_ALIASES: dict[str, str] = {
    "hindi": "hi",
    "english": "en",
    "marathi": "mr",
    "punjabi": "pa", "panjabi": "pa",
    "bangla": "bn", "bengali": "bn",
}


def is_supported(code: str) -> bool:
    return code in SUPPORTED_LANGUAGES


def language_name(code: str) -> str:
    info = SUPPORTED_LANGUAGES.get(code)
    return info["english"] if info else code


def normalize_languages(codes) -> list[str]:
    """Filter a requested language list against the supported set, de-duped and
    order-preserving. Unknown codes are dropped — the operator picks from a fixed
    list, so silently ignoring junk is safer than rendering an unsupported voice."""
    out: list[str] = []
    for c in codes or []:
        code = str(c or "").strip().lower()
        code = _ALIASES.get(code, code)
        if code in SUPPORTED_LANGUAGES and code not in out:
            out.append(code)
    return out


def catalogue() -> list[dict]:
    """UI-friendly list for the language picker."""
    return [{"code": code, **names} for code, names in SUPPORTED_LANGUAGES.items()]


# T13a: caption font per script. Latin house fonts (Baskerville/Montserrat/Satoshi)
# have NO Devanagari/Gurmukhi/Bengali glyphs — a Hindi caption in Baskerville renders
# tofu (□□). Bundled Noto Serif faces (deploy/fonts/) keep the serif storytelling look
# per script. An operator-chosen NON-Latin-default font is respected as-is.
CAPTION_FONTS: dict[str, str] = {
    "hi": "Noto Serif Devanagari",
    "mr": "Noto Serif Devanagari",   # Marathi uses Devanagari
    "pa": "Noto Serif Gurmukhi",
    "bn": "Noto Serif Bengali",
}

_LATIN_ONLY_DEFAULTS = {"baskerville", "libre baskerville", "montserrat", "satoshi"}


def font_for_language(code: str, requested_font: str = "") -> str:
    """The caption font that can actually draw this language's script. Keeps the
    operator's font when it isn't one of the Latin-only house defaults."""
    if code not in CAPTION_FONTS:
        return requested_font
    if requested_font and requested_font.strip().lower() not in _LATIN_ONLY_DEFAULTS:
        return requested_font       # explicit non-default choice — operator wins
    return CAPTION_FONTS[code]
