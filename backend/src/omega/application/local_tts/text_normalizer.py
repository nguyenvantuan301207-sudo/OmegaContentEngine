"""Minimal and deterministic text normalization for local English TTS narration."""

from __future__ import annotations

import re

# Mapping of Unicode punctuation to ASCII speech-safe equivalents
UNICODE_PUNCTUATION_MAP: dict[str, str] = {
    "’": "'",
    "‘": "'",
    "`": "'",
    "“": '"',
    "”": '"',
    "«": '"',
    "»": '"',
    "—": " - ",
    "–": " - ",
    "…": "...",
    "\u00a0": " ",  # Non-breaking space
    "\u200b": "",   # Zero-width space
}


def normalize_narration_text(text: str) -> str:
    """Perform minimal, deterministic text normalization for English speech synthesis.

    Preserves semantic numbers, percentages, currencies, dates, times, and acronyms
    without destructive or lossy phonetic rewriting.
    """
    if not text:
        return ""

    s = text

    # 1. Normalize Unicode punctuation to speech-safe equivalents
    for unicode_char, replacement in UNICODE_PUNCTUATION_MAP.items():
        s = s.replace(unicode_char, replacement)

    # 2. Normalize newlines and carriage returns to space
    s = s.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")

    # 3. Collapse multiple whitespace characters into single space
    s = re.sub(r"[ \t]+", " ", s)

    # 4. Trim leading and trailing whitespace
    return s.strip()
