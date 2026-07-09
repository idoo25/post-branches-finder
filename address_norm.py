"""
Hebrew/English address normalization for the geocode cache key.

Goal: maximize cache hit rate by mapping all the obvious user variants of the
same address to a single canonical string, *without* losing semantic distinction.

Examples — all of these become the same key:
    "Dizengoff 50, Tel Aviv"
    "  dizengoff   50,  TEL-AVIV  "
    "דיזנגוף 50, תל-אביב"
    "דיזנגוף 50  ,  תל אביב"
    'דִּיזֶנְגּוֹף 50, תל אביב'           (with niqqud)
"""
from __future__ import annotations

import re
import unicodedata

# Hebrew niqqud (combining marks) range, plus cantillation marks.
_NIQQUD_RE = re.compile(r"[֑-ׇ]")

# Punctuation we want to drop entirely — keep only spaces and word characters.
_PUNCT_RE = re.compile(r'[,.;:"\'`()\[\]{}/\\|*&^%$#@!?<>~+\-_=]')

_MULTISPACE_RE = re.compile(r"\s+")

# Common abbreviations / synonyms in Hebrew addresses.
_REPLACEMENTS = [
    (re.compile(r'\bת"א\b'), "תל אביב"),
    (re.compile(r"\bת'א\b"), "תל אביב"),
    (re.compile(r"\bר'?ג\b"), "רמת גן"),
    (re.compile(r"\bפ'?ת\b"), "פתח תקווה"),
    (re.compile(r"\bב'?ש\b"), "באר שבע"),
    (re.compile(r"\bרח'\b"), ""),    # "רחוב" prefix carries no information
    (re.compile(r"\bרחוב\b"), ""),
    (re.compile(r"\bשדרות\b"), "שד"),
    (re.compile(r"\bשד'\b"), "שד"),
]


def normalize(address: str) -> str:
    """Return the canonical form. Empty string if input is junk."""
    if not address:
        return ""

    s = address

    # 1) Unicode NFKC: collapses look-alikes and width variants.
    s = unicodedata.normalize("NFKC", s)

    # 2) Strip Hebrew niqqud / cantillation.
    s = _NIQQUD_RE.sub("", s)

    # 3) Lowercase (English mixed-in is common in Israeli addresses).
    s = s.lower()

    # 4) Apply common Hebrew abbreviation expansions.
    for pat, repl in _REPLACEMENTS:
        s = pat.sub(repl, s)

    # 5) Drop punctuation.
    s = _PUNCT_RE.sub(" ", s)

    # 6) Collapse whitespace.
    s = _MULTISPACE_RE.sub(" ", s).strip()

    return s


if __name__ == "__main__":
    # Quick demo
    samples = [
        "Dizengoff 50, Tel Aviv",
        "  dizengoff   50,  TEL-AVIV  ",
        "דיזנגוף 50, תל-אביב",
        "דיזנגוף 50  ,  תל אביב",
        'דִּיזֶנְגּוֹף 50, תל אביב',
        'רחוב דיזנגוף 50, ת"א',
        'שד\' רוטשילד 1, ת"א',
        '  ',
    ]
    for s in samples:
        print(f"{s!r:45s} -> {normalize(s)!r}")
