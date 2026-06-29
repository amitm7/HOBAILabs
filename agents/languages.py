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
