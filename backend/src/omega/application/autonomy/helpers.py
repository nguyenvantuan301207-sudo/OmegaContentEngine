"""Cryptographic, deterministic hashing and advisory lock helpers for OMEGA-014."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID


def advisory_lock_key(namespace: str, *identifiers: Any) -> int:
    """Derive a deterministic signed 64-bit integer key for PostgreSQL advisory locking.

    Formula: SHA256(namespace + ":" + ":".join(identifiers)) -> first 8 bytes -> signed int64.
    Never uses Python hash().
    """
    id_str = ":".join(str(i) for i in identifiers)
    payload = f"{namespace}:{id_str}".encode()
    digest = hashlib.sha256(payload).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def compute_action_checksum(
    action_type: str,
    target_entity_id: UUID | None,
    parameters: dict[str, Any],
    iteration_id: UUID,
) -> str:
    """Compute deterministic SHA-256 over planned action payload."""
    canonical_params = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
    payload = f"{action_type}:{target_entity_id or 'NONE'}:{canonical_params}:{iteration_id}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_precondition_fingerprint(
    action_type: str,
    target_entity_id: UUID | None,
    entity_state_token: str,
    guardian_context_checksum: str,
    dna_revision_id: UUID | None,
    policy_checksum: str,
    mission_state: str,
) -> str:
    """Compute deterministic precondition fingerprint for staleness detection."""
    payload = (
        f"{action_type}:"
        f"{target_entity_id or 'NONE'}:"
        f"{entity_state_token}:"
        f"{guardian_context_checksum}:"
        f"{dna_revision_id or 'NONE'}:"
        f"{policy_checksum}:"
        f"{mission_state}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_semantic_action_key(
    loop_id: UUID,
    iteration_sequence: int,
    action_type: str,
    target_entity_id: UUID | None,
) -> str:
    """Derive semantic identity of an action within an iteration."""
    payload = f"{loop_id}:{iteration_sequence}:{action_type}:{target_entity_id or 'NONE'}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_reservation_key(
    loop_id: UUID,
    action_plan_id: UUID,
    semantic_action_key: str,
) -> str:
    """Derive unique reservation key guaranteeing idempotency of budget reservations."""
    payload = f"{loop_id}:{action_plan_id}:{semantic_action_key}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_event_dedupe_key(
    loop_id: UUID,
    event_type: str,
    entity_identity: str,
) -> str:
    """Derive deterministic event dedupe key without timestamps."""
    payload = f"{loop_id}:{event_type}:{entity_identity}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
