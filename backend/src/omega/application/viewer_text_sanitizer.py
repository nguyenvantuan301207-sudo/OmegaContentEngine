"""Pure deterministic viewer-facing text sanitizer and chapter presentation policy.

Enforces strict separation between internal orchestration metadata and viewer-facing copy:
- Strips internal tags (e.g. [P19-G2A RETRY6], [P18-G1], [RETRY-5])
- Strips internal phase, retry, mission, and test/acceptance identifiers
- Formats uppercase technical titles into clean, readable Title Case
- Enforces chapter display policy: full title at section entry, suppressed or concise thereafter
- Preserves full internal metadata in RuntimeTruth/logs; sanitizes ONLY visible text
"""

from __future__ import annotations

import re

# Internal structural planning labels that should not be displayed as viewer titles
_STRUCTURAL_EXACT_LABELS = frozenset({
    "hook",
    "closing",
    "cta",
    "call to action",
    "call-to-action",
    "intro",
    "introduction",
    "outro",
    "conclusion",
    "placeholder",
    "test",
})

_STRUCTURAL_PATTERN = re.compile(
    r"^(?:section|scene|chapter|beat|part)(?:[_\s-]+\d*(?:[_\s-]+\w+)*|\d+)$",
    re.IGNORECASE,
)

# Bracketed internal tokens: e.g. [P19-G2A RETRY6], [P18-G1], [ACCEPTANCE], [DEBUG]
_BRACKETED_TAG_PATTERN = re.compile(r"\[.*?\]")

# Internal phase, retry, mission, test, benchmark patterns
_INTERNAL_TOKEN_PATTERNS = (
    re.compile(r"\bP\d+-[A-Z0-9_-]+\b", re.IGNORECASE),          # P19-G2A, P18-F, etc.
    re.compile(r"\bRETRY-?\d+\b", re.IGNORECASE),                 # RETRY6, RETRY-5, etc.
    re.compile(r"\bPHASE-?\d+[A-Z0-9_-]*\b", re.IGNORECASE),      # PHASE-19, etc.
    re.compile(r"\bMISSION-?[0-9a-fA-F-]+\b", re.IGNORECASE),     # MISSION-uuid
    re.compile(r"\bRUN-?\d+\b", re.IGNORECASE),                   # RUN-1
    re.compile(r"\b(?:PRODUCTION\s+)?ACCEPTANCE(?:\s+RUN)?\b", re.IGNORECASE),
    re.compile(r"\b(?:PRODUCTION\s+)?CANARY\b", re.IGNORECASE),
    re.compile(r"\b(?:PRODUCTION\s+)?BENCHMARK\b", re.IGNORECASE),
    re.compile(r"\b(?:PRODUCTION\s+)?VERIFICATION(?:\s+RUN)?\b", re.IGNORECASE),
    re.compile(r"\bTEST\s+(?:HARNESS|RUN|SUITE)\b", re.IGNORECASE),
)


# Standard acronyms to preserve in title casing
_PRESERVED_ACRONYMS = frozenset({
    "API", "APIs", "HTTP", "HTTPS", "CPU", "GPU", "RAM", "SQL", "TTS", "LLM",
    "UI", "UX", "OS", "CLI", "IO", "I/O", "JSON", "XML", "CSS", "HTML", "REST",
    "SDK", "TCP", "IP", "DNS", "URL", "URI", "UUID", "DB", "FFMPEG", "FFMpeg",
    "QA", "CI", "CD", "AI", "ML", "GCP", "AWS", "RPC", "IPC",
})

_LOWERCASE_WORDS = frozenset({
    "a", "an", "and", "as", "at", "but", "by", "for", "in", "nor", "of", "on",
    "or", "so", "the", "to", "up", "yet", "with",
})


def is_structural_or_internal_label(text: str | None) -> bool:
    """Identify internal orchestration/structural planning labels."""
    if not text or not text.strip():
        return True
    cleaned = text.strip()
    lower = cleaned.lower()
    if lower in _STRUCTURAL_EXACT_LABELS:
        return True
    if _BRACKETED_TAG_PATTERN.search(cleaned):
        return True
    for pat in _INTERNAL_TOKEN_PATTERNS:
        if pat.search(cleaned):
            return True
    return bool(_STRUCTURAL_PATTERN.match(cleaned))



def sanitize_viewer_text(text: str | None) -> str | None:
    """Deterministically strip internal metadata and test markers from viewer-facing text.

    Preserves original content if no internal tags are present.
    Returns None if text was entirely internal metadata.
    """
    if not text or not isinstance(text, str):
        return None

    cleaned = text.strip()
    if not cleaned:
        return None

    # 1. Strip bracketed internal tags: [P19-G2A RETRY6] -> ""
    cleaned = _BRACKETED_TAG_PATTERN.sub("", cleaned)

    # 2. Strip internal phase, retry, mission, and test tokens
    for pat in _INTERNAL_TOKEN_PATTERNS:
        cleaned = pat.sub("", cleaned)

    # 3. Clean up leftover punctuation artifacts: e.g. " - ", " : ", leading/trailing hyphens/colons
    cleaned = re.sub(r"^\s*[-–—:;,|/]\s*", "", cleaned)
    cleaned = re.sub(r"\s*[-–—:;,|/]\s*$", "", cleaned)
    cleaned = re.sub(r"\s+[-–—:;,|/]\s*$", "", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()

    if not cleaned:
        return None

    # Check if remaining string is just an internal structural label
    if is_structural_or_internal_label(cleaned):
        return None

    return cleaned


def to_viewer_title_case(text: str) -> str:
    """Convert a technical heading to clean Title Case while preserving standard acronyms."""
    if not text:
        return ""

    words = text.split()
    if not words:
        return ""

    # Only convert if heading is mostly UPPERCASE
    is_mostly_upper = sum(1 for c in text if c.isupper()) > sum(1 for c in text if c.islower())
    if not is_mostly_upper:
        return text

    cased_words: list[str] = []
    for i, word in enumerate(words):
        upper_word = word.upper().strip(".,;:!?\"'()")
        if upper_word in _PRESERVED_ACRONYMS:
            # Preserve acronym with surrounding punctuation
            prefix = word[:len(word) - len(word.lstrip(".,;:!?\"'()"))]
            suffix = word[len(word.rstrip(".,;:!?\"'()")):]
            cased_words.append(f"{prefix}{upper_word}{suffix}")
        elif i > 0 and word.lower() in _LOWERCASE_WORDS and i != len(words) - 1:
            cased_words.append(word.lower())
        else:
            cased_words.append(word.capitalize())

    return " ".join(cased_words)


def sanitize_chapter_title(text: str | None, *, max_chars: int = 55) -> str | None:
    """Sanitize and format a chapter heading for viewer-facing transition display.

    1. Strips internal tags and test tokens.
    2. Strips 'Chapter X:' or 'Section X:' prefix for cleaner viewer presentation.
    3. Converts ALL-CAPS into clean Title Case with preserved acronyms.
    4. Shortens overly long technical titles if necessary.
    """
    clean = sanitize_viewer_text(text)
    if not clean:
        return None

    # Remove leading "Chapter X: " or "Section X: " prefix if present
    clean = re.sub(r"^(?:Chapter|Section|Part)\s*\d+\s*[-–—:]\s*", "", clean, flags=re.IGNORECASE).strip()
    if not clean:
        return None

    formatted = to_viewer_title_case(clean)

    # Shorten if overly long while respecting word boundaries
    if len(formatted) > max_chars:
        # Try cutting at sentence / clause boundary
        split_match = re.search(r"[:;–—\-|]\s*", formatted[:max_chars])
        if split_match and split_match.start() > 15:
            formatted = formatted[:split_match.start()].strip()
        else:
            # Cut at last whitespace before max_chars
            words = formatted[:max_chars].rsplit(" ", 1)
            formatted = words[0].strip()

    return formatted or None
