"""Unit tests for Contradiction and Conflict Analysis."""

from __future__ import annotations

import uuid

from omega.application.research_scorer import detect_claim_conflicts
from omega.domain.research import ConflictSeverity, EvidenceDirection


def test_direct_contradiction_detection() -> None:
    """Test that evidence with CONTRADICTS direction generates HIGH severity conflict."""
    claim_id = uuid.uuid4()
    claim_text = "PostgreSQL handles connection pooling without external middleware."

    evidence = [
        {
            "id": uuid.uuid4(),
            "source_id": uuid.uuid4(),
            "support_direction": EvidenceDirection.SUPPORTS,
            "excerpt": "PostgreSQL processes connections internally.",
        },
        {
            "id": uuid.uuid4(),
            "source_id": uuid.uuid4(),
            "support_direction": EvidenceDirection.CONTRADICTS,
            "excerpt": "PostgreSQL forks a backend process per connection; PgBouncer is required for high scale.",
        },
    ]

    conflicts = detect_claim_conflicts(claim_id, claim_text, evidence)
    assert len(conflicts) >= 1
    assert any(c["conflict_type"] == "DIRECT_CONTRADICTION" and c["severity"] == ConflictSeverity.HIGH for c in conflicts)


def test_numerical_discrepancy_detection() -> None:
    """Test that conflicting numeric tokens trigger numerical discrepancy conflict."""
    claim_id = uuid.uuid4()
    claim_text = "Benchmark showed a 50% decrease in response latency."

    evidence = [
        {
            "id": uuid.uuid4(),
            "source_id": uuid.uuid4(),
            "support_direction": EvidenceDirection.SUPPORTS,
            "excerpt": "Our test suite recorded a 15% decrease in overall latency under load.",
        }
    ]

    conflicts = detect_claim_conflicts(claim_id, claim_text, evidence)
    assert len(conflicts) == 1
    assert conflicts[0]["conflict_type"] == "NUMERICAL_DISCREPANCY"
    assert conflicts[0]["severity"] == ConflictSeverity.MEDIUM
