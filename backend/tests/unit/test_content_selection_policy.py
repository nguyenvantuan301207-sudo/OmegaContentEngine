"""Unit contracts for canonical content-selection identity."""

from __future__ import annotations

import uuid

from omega.application.content_selection_service import (
    candidate_set_checksum,
    policy_checksum,
)


def test_policy_checksum_is_stable() -> None:
    assert policy_checksum() == policy_checksum()
    assert len(policy_checksum()) == 64


def test_candidate_set_checksum_is_order_independent_and_context_bound() -> None:
    candidate_a = uuid.uuid4()
    candidate_b = uuid.uuid4()
    dna_revision = uuid.uuid4()
    policy = policy_checksum()

    first = candidate_set_checksum(
        candidate_ids=[candidate_a, candidate_b],
        channel_dna_revision_id=dna_revision,
        selection_policy_checksum=policy,
        mission_execution_id=None,
    )
    reordered = candidate_set_checksum(
        candidate_ids=[candidate_b, candidate_a],
        channel_dna_revision_id=dna_revision,
        selection_policy_checksum=policy,
        mission_execution_id=None,
    )
    changed = candidate_set_checksum(
        candidate_ids=[candidate_a],
        channel_dna_revision_id=dna_revision,
        selection_policy_checksum=policy,
        mission_execution_id=None,
    )

    assert first == reordered
    assert first != changed
