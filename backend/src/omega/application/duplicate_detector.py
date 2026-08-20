"""Deterministic topic normalization, versioned fingerprinting, and duplicate detection.

Zero external ML framework dependencies. Implements authoritative similarity classification
with explicit angle evidence requirements.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

from omega.domain.topic import DuplicateStatus, TopicAngleCreate
from omega.domain.topic_scoring import DEFAULT_SIMILARITY_PROFILE, SimilarityProfile

# Non-alphanumeric punctuation removal regex (keeps single hyphens and spaces)
PUNCTUATION_REGEX = re.compile(r"[^\w\s-]", re.UNICODE)
WHITESPACE_REGEX = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Normalize a text string deterministically.

    Steps:
    1. NFKD decomposition + diacritic mark stripping.
    2. Unicode NFKC normalization.
    3. Lowercasing.
    4. Punctuation stripping (retaining letters, numbers, hyphens).
    5. Whitespace trimming and single-space collapsing.
    """
    if not text:
        return ""
    # Strip diacritics / accents
    nfkd = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    # Unicode NFKC
    nfkc = unicodedata.normalize("NFKC", stripped)
    # Lowercase
    lowered = nfkc.lower()
    # Replace punctuation with space
    cleaned = PUNCTUATION_REGEX.sub(" ", lowered)
    # Collapse whitespace
    collapsed = WHITESPACE_REGEX.sub(" ", cleaned).strip()
    return collapsed


def tokenize(normalized_text: str) -> set[str]:
    """Tokenize normalized text into a set of distinct words."""
    if not normalized_text:
        return set()
    return {w for w in normalized_text.split(" ") if len(w) > 1}


def compute_topic_fingerprint(
    normalized_title: str,
    language: str,
    entities: list[str] | None = None,
    keywords: list[str] | None = None,
) -> str:
    """Compute a versioned, collision-resistant SHA-256 fingerprint.

    Canonical format (v1):
    omega-topic-fingerprint:v1
    <language>
    <normalized_title>
    <sorted_normalized_entities_joined_by_comma>
    <sorted_normalized_keywords_joined_by_comma>
    """
    norm_lang = (language or "en").strip().lower()
    norm_title = normalized_title.strip().lower()

    norm_entities = sorted({normalize_text(e) for e in (entities or []) if normalize_text(e)})
    norm_keywords = sorted({normalize_text(k) for k in (keywords or []) if normalize_text(k)})

    canonical_payload = (
        f"omega-topic-fingerprint:v1\n"
        f"{norm_lang}\n"
        f"{norm_title}\n"
        f"{','.join(norm_entities)}\n"
        f"{','.join(norm_keywords)}"
    )

    return hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()


def compute_dice_similarity(tokens_a: set[str], tokens_b: set[str]) -> float:
    """Compute Sørensen-Dice coefficient between two token sets."""
    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = len(tokens_a.intersection(tokens_b))
    return (2.0 * intersection) / (len(tokens_a) + len(tokens_b))


def compute_entity_overlap(entities_a: list[str], entities_b: list[str]) -> float:
    """Compute normalized overlap ratio between two entity/keyword lists."""
    set_a = {normalize_text(e) for e in entities_a if normalize_text(e)}
    set_b = {normalize_text(e) for e in entities_b if normalize_text(e)}
    if not set_a and not set_b:
        return 0.0
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a.intersection(set_b))
    return intersection / max(len(set_a), len(set_b))


def has_distinct_angle_evidence(
    candidate_angles: list[TopicAngleCreate] | list[dict] | None,
    memory_angles: list[dict] | None,
) -> bool:
    """Verify if explicit deterministic angle evidence exists distinguishing candidate from memory.

    Evidence requires:
    - Candidate has at least one explicit angle/hook/format intent.
    - The candidate's angle is not identical to existing angles in memory.
    """
    if not candidate_angles:
        return False

    candidate_angle_texts: set[str] = set()
    for a in candidate_angles:
        if isinstance(a, TopicAngleCreate):
            ang_norm = normalize_text(a.angle)
            if ang_norm:
                candidate_angle_texts.add(ang_norm)
        elif isinstance(a, dict) and "angle" in a:
            ang_norm = normalize_text(a["angle"])
            if ang_norm:
                candidate_angle_texts.add(ang_norm)

    if not candidate_angle_texts:
        return False

    # Check against memory angles if present
    mem_angle_texts: set[str] = set()
    for ma in memory_angles or []:
        if isinstance(ma, dict) and "angle" in ma:
            ang_norm = normalize_text(ma["angle"])
            if ang_norm:
                mem_angle_texts.add(ang_norm)

    # If candidate introduces an angle not in memory, evidence is positive
    diff = candidate_angle_texts - mem_angle_texts
    return len(diff) > 0


def classify_topic_similarity(
    candidate_norm_title: str,
    candidate_entities: list[str],
    candidate_keywords: list[str],
    candidate_fingerprint: str,
    candidate_angles: list[TopicAngleCreate] | list[dict] | None,
    memory_norm_title: str,
    memory_entities: list[str],
    memory_keywords: list[str],
    memory_fingerprint: str,
    memory_angles: list[dict] | None = None,
    profile: SimilarityProfile = DEFAULT_SIMILARITY_PROFILE,
) -> tuple[DuplicateStatus, float]:
    """Authoritative similarity classification with deterministic precedence.

    Precedence:
    1. Exact match / fingerprint match -> SAME_TOPIC_NEW_ANGLE (if angle evidence) else EXACT_DUPLICATE (1.0)
    2. Sim >= exact_duplicate_threshold -> SAME_TOPIC_NEW_ANGLE (if angle evidence) else EXACT_DUPLICATE
    3. Sim >= semantic_duplicate_threshold:
       - with distinct explicit angle evidence -> SAME_TOPIC_NEW_ANGLE
       - otherwise -> SEMANTIC_DUPLICATE
    4. Sim >= new_angle_min_similarity:
       - with distinct explicit angle evidence -> SAME_TOPIC_NEW_ANGLE
       - otherwise -> RELATED_TOPIC
    5. Sim >= fresh_threshold -> RELATED_TOPIC
    6. Otherwise -> FRESH_TOPIC
    """
    has_angle = has_distinct_angle_evidence(candidate_angles, memory_angles)

    # 1. Exact fingerprint match
    if candidate_fingerprint == memory_fingerprint:
        if has_angle:
            return DuplicateStatus.SAME_TOPIC_NEW_ANGLE, 1.0
        return DuplicateStatus.EXACT_DUPLICATE, 1.0

    # Token & Entity similarity calculation
    cand_tokens = tokenize(candidate_norm_title)
    mem_tokens = tokenize(memory_norm_title)

    token_sim = compute_dice_similarity(cand_tokens, mem_tokens)

    all_cand_entities = candidate_entities + candidate_keywords
    all_mem_entities = memory_entities + memory_keywords
    entity_sim = compute_entity_overlap(all_cand_entities, all_mem_entities)

    if all_cand_entities and all_mem_entities:
        composite_sim = (0.6 * token_sim) + (0.4 * entity_sim)
    else:
        composite_sim = token_sim

    # 2. Sim >= exact_duplicate_threshold or title exact match
    if (
        composite_sim >= profile.exact_duplicate_threshold
        or candidate_norm_title == memory_norm_title
    ):
        if has_angle:
            return DuplicateStatus.SAME_TOPIC_NEW_ANGLE, composite_sim
        return DuplicateStatus.EXACT_DUPLICATE, composite_sim

    # 3. Sim >= semantic_duplicate_threshold
    if composite_sim >= profile.semantic_duplicate_threshold:
        if has_angle:
            return DuplicateStatus.SAME_TOPIC_NEW_ANGLE, composite_sim
        return DuplicateStatus.SEMANTIC_DUPLICATE, composite_sim

    # 4. Sim >= new_angle_min_similarity
    if composite_sim >= profile.new_angle_min_similarity:
        if has_angle:
            return DuplicateStatus.SAME_TOPIC_NEW_ANGLE, composite_sim
        return DuplicateStatus.RELATED_TOPIC, composite_sim

    # 5. Sim >= fresh_threshold
    if composite_sim >= profile.fresh_threshold:
        return DuplicateStatus.RELATED_TOPIC, composite_sim

    # 6. Otherwise FRESH_TOPIC
    return DuplicateStatus.FRESH_TOPIC, composite_sim
