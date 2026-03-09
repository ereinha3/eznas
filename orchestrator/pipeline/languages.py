"""Language name to ISO 639-2/B code mapping.

Radarr and Sonarr return language names (e.g. "English", "Chinese") from
their ``originalLanguage`` field. This module maps those names to ISO 639
three-letter codes used by ffmpeg/ffprobe and our media policy config.

All codes use the **bibliographic** (B) variant of ISO 639-2 where two
codes exist for the same language (e.g. ``fre`` not ``fra`` for French).
The remux module normalises terminological (T) codes to B codes so that
language comparisons work regardless of which variant the source uses.
"""
from __future__ import annotations

from typing import Optional

# Radarr/Sonarr language name -> ISO 639-2/B code
# Sourced from Radarr ``GET /api/v3/language`` (57 entries).
# Keys are lowercased for case-insensitive lookup.
# Where a language has both B and T codes, we use the B (bibliographic) code.
ARR_NAME_TO_ISO: dict[str, str] = {
    "any": "und",
    "arabic": "ara",
    "bengali": "ben",
    "bosnian": "bos",
    "bulgarian": "bul",
    "catalan": "cat",
    "chinese": "chi",       # B code (T=zho)
    "croatian": "hrv",
    "czech": "cze",          # B code (T=ces)
    "danish": "dan",
    "dutch": "dut",          # B code (T=nld)
    "english": "eng",
    "estonian": "est",
    "finnish": "fin",
    "flemish": "dut",        # Flemish = Dutch variant, B code
    "french": "fre",         # B code (T=fra)
    "german": "ger",         # B code (T=deu)
    "greek": "gre",          # B code (T=ell)
    "hebrew": "heb",
    "hindi": "hin",
    "hungarian": "hun",
    "icelandic": "ice",      # B code (T=isl)
    "indonesian": "ind",
    "irish": "gle",
    "italian": "ita",
    "japanese": "jpn",
    "kannada": "kan",
    "korean": "kor",
    "latvian": "lav",
    "lithuanian": "lit",
    "macedonian": "mac",     # B code (T=mkd)
    "malay": "may",          # B code (T=msa)
    "malayalam": "mal",
    "norwegian": "nor",
    "original": "und",       # Special *arr value meaning "keep original"
    "persian": "per",        # B code (T=fas)
    "polish": "pol",
    "portuguese": "por",
    "portuguese (brazil)": "por",
    "punjabi": "pan",
    "romanian": "rum",       # B code (T=ron)
    "russian": "rus",
    "serbian": "srp",
    "sinhalese": "sin",
    "slovak": "slo",         # B code (T=slk)
    "slovenian": "slv",
    "spanish": "spa",
    "spanish (latino)": "spa",
    "swedish": "swe",
    "tamil": "tam",
    "telugu": "tel",
    "thai": "tha",
    "turkish": "tur",
    "ukrainian": "ukr",
    "unknown": "und",
    "urdu": "urd",
    "vietnamese": "vie",
}

# Reverse mapping: ISO code -> canonical English name (first match wins)
_ISO_TO_NAME: dict[str, str] = {}
for _name, _code in ARR_NAME_TO_ISO.items():
    if _code not in _ISO_TO_NAME and _name not in ("any", "original", "unknown"):
        _ISO_TO_NAME[_code] = _name


def arr_language_to_iso(name: str) -> Optional[str]:
    """Convert a Radarr/Sonarr language name to an ISO 639-2/B code.

    Returns None if the name is not recognized.

    >>> arr_language_to_iso("English")
    'eng'
    >>> arr_language_to_iso("Chinese")
    'chi'
    >>> arr_language_to_iso("Unknown")
    'und'
    """
    return ARR_NAME_TO_ISO.get(name.lower().strip())


def iso_to_name(code: str) -> str:
    """Convert an ISO 639 code to a human-readable name.

    Returns the code itself if no name is known.
    """
    return _ISO_TO_NAME.get(code.lower(), code)
