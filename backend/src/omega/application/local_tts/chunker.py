"""Semantic text chunking for long narration scenes."""

from __future__ import annotations

import re

# Benchmark target: 80–150 tokens (approx 45–90 words)
UPPER_TOKEN_THRESHOLD = 150
LOWER_TARGET_TOKENS = 80


def estimate_tokens(text: str) -> int:
    """Approximate token count for English text (1 word ≈ 1.3 tokens)."""
    words = text.split()
    if not words:
        return 0
    return max(1, int(len(words) * 1.3))


def chunk_narration_segment(
    text: str,
    max_tokens: int = UPPER_TOKEN_THRESHOLD,
    min_target_tokens: int = LOWER_TARGET_TOKENS,
) -> list[str]:
    """Chunk narration text ONLY if it exceeds the upper token threshold.

    Short scenes (below threshold) are preserved as-is. Long scenes are split
    along semantic sentence/clause boundaries without merging across scene boundaries.
    """
    clean = text.strip()
    if not clean:
        return []

    # If the scene fits comfortably within threshold, preserve as-is
    est_total = estimate_tokens(clean)
    if est_total <= max_tokens:
        return [clean]

    # Split into sentences using punctuation boundaries
    sentences = re.split(r"(?<=[.!?])\s+", clean)
    sentences = [s.strip() for s in sentences if s.strip()]

    if not sentences:
        return [clean]

    chunks: list[str] = []
    current_sentences: list[str] = []
    current_tokens = 0

    for s in sentences:
        s_tokens = estimate_tokens(s)

        # If a single sentence exceeds max_tokens on its own, split by clause boundaries
        if s_tokens > max_tokens:
            if current_sentences:
                chunks.append(" ".join(current_sentences))
                current_sentences = []
                current_tokens = 0

            clause_parts = re.split(r"(?<=[;:,])\s+", s)
            sub_chunk: list[str] = []
            sub_tokens = 0
            for part in clause_parts:
                part_t = estimate_tokens(part)
                if sub_tokens + part_t > max_tokens and sub_chunk:
                    chunks.append(" ".join(sub_chunk))
                    sub_chunk = [part]
                    sub_tokens = part_t
                else:
                    sub_chunk.append(part)
                    sub_tokens += part_t
            if sub_chunk:
                chunks.append(" ".join(sub_chunk))
            continue

        if current_tokens + s_tokens > max_tokens and current_sentences:
            chunks.append(" ".join(current_sentences))
            current_sentences = [s]
            current_tokens = s_tokens
        else:
            current_sentences.append(s)
            current_tokens += s_tokens

    if current_sentences:
        last_chunk = " ".join(current_sentences)
        # Avoid tiny orphaned fragment by merging with previous chunk if combined fits
        if chunks and estimate_tokens(last_chunk) < 20:
            combined = chunks[-1] + " " + last_chunk
            if estimate_tokens(combined) <= int(max_tokens * 1.25):
                chunks[-1] = combined
            else:
                chunks.append(last_chunk)
        else:
            chunks.append(last_chunk)

    return chunks
