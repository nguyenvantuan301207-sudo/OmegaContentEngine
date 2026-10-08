"""Audit the exact Retry #6 corpus through the production pipeline in memory.

Usage: python offline_audit.py INPUT_JSON PERSISTED_SOURCES_JSON PERSISTED_CLAIMS_JSON OUTPUT_JSON
The two persisted snapshots are read-only database exports. No DB engine is used.
"""

import json
import sys
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy.orm import Session

from omega.application.claim_extractor import extract_deterministic_claims_from_source
from omega.application.claim_reconciliation import reconcile_source_extractions_into_claims
from omega.application.research_query_planner import (
    build_corroboration_targets,
    plan_research_queries,
)
from omega.application.research_service import evaluate_canonical_claims
from omega.domain.causal_direction import CausalDirection
from omega.domain.numeric_promise import (
    derive_verified_family_label,
    extract_distinct_entities,
    extract_numeric_promise,
)
from omega.domain.research import ResearchQueryIntent
from omega.infrastructure.models import ResearchSource


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def main():
    data, stored_sources, stored_claims = map(read, sys.argv[1:4])
    request, topic = data["request"], data["topic"]
    assert request["id"] == "b52c1b9f-3b14-421e-97ac-e7db83fc89da"
    rows = data["sources"]
    assert len(rows) == len(stored_sources) == 14
    def projection(r):
        return (r["id"], r["content_hash"], r["content_excerpt"], r["url"])
    assert {projection(r) for r in rows} == {projection(r) for r in stored_sources}
    sources = []
    fields = [
        "source_type",
        "title",
        "publisher",
        "url",
        "content_excerpt",
        "content_hash",
        "primary_source_status",
        "quality_score",
        "language",
        "region",
    ]
    for row in rows:
        assert row["research_request_id"] == request["id"]
        assert row["metadata"]["content_provenance"]["extractor"] == "tavily_extract"
        assert row["metadata"]["content_provenance"]["extracted_url"] == row["url"]
        sources.append(
            ResearchSource(
                id=UUID(row["id"]),
                research_request_id=UUID(request["id"]),
                channel_id=UUID(request["channel_id"]),
                metadata_=row["metadata"],
                **{k: row[k] for k in fields},
            )
        )
    eligible = [s for s in sources if s.quality_score >= request["minimum_source_quality"]]
    assert len(eligible) == 14
    claims = []
    with Session() as memory:
        for source in eligible:
            extracted = extract_deterministic_claims_from_source(
                source_title=source.title,
                source_excerpt=source.content_excerpt,
                metadata=dict(source.metadata_ or {}),
                source_type=source.source_type,
                topic_keywords=topic["keywords"],
            )
            reconcile_source_extractions_into_claims(
                session=memory,
                existing_claims=claims,
                extracted_items=extracted,
                source=source,
                channel_id=UUID(request["channel_id"]),
                request_id=UUID(request["id"]),
            )
        # Audit-only identity mapping after extraction; persisted claims supply
        # IDs only, never text/evidence/confidence/verification to the replay.
        persisted_ids = {r["normalized_claim"]: UUID(r["id"]) for r in stored_claims}
        for claim in claims:
            if claim.normalized_claim not in persisted_ids:
                continue
            claim.id = persisted_ids[claim.normalized_claim]
            for ev in claim.evidence:
                ev.claim_id = claim.id
        verified, conflicts, clusters = evaluate_canonical_claims(
            eligible_sources=eligible, claims=claims
        )
        contract = extract_numeric_promise(topic["title"])
        families = extract_distinct_entities(
            [c.claim_text for c in verified],
            topic_title=topic["title"],
            entity_type=contract.entity_type,
        )
        source_map = {s.id: s for s in sources}
        foreign_count = sum(e.source_id not in source_map for c in claims for e in c.evidence)
        before = [
            (c.id, c.is_verified, c.confidence_score, c.independent_sources_count) for c in claims
        ]
        targets = build_corroboration_targets(
            claims,
            source_map,
            contract,
            topic_keywords=topic["keywords"],
            topic_title=topic["title"],
            already_supported_families=families,
        )
        queries = plan_research_queries(topic["title"], contract, 2, families, 3, targets)
        assert before == [
            (c.id, c.is_verified, c.confidence_score, c.independent_sources_count) for c in claims
        ]
        details = []
        by_id = {c.id: c for c in claims}
        for q in queries:
            detail = q.model_dump(mode="json")
            if q.target_claim_id:
                assert q.target_claim_id in set(persisted_ids.values())
                c = by_id[q.target_claim_id]
                detail["target_claim_text"] = c.claim_text
                detail["independent_evidence"] = [
                    {
                        "source_id": str(e.source_id),
                        "domain": urlparse(source_map[e.source_id].url).netloc,
                        "cluster": clusters[e.source_id],
                        "excerpt": e.excerpt,
                    }
                    for e in c.evidence
                    if str(e.support_direction) == "SUPPORTS"
                ]
            details.append(detail)
        assertions = [
            q.causal_assertion for q in queries if q.intent == ResearchQueryIntent.CORROBORATION
        ]
        counters = {
            "CONSEQUENCE_TARGET_QUERY_COUNT": sum(
                a.direction == CausalDirection.CONSEQUENCE_OF_TOPIC_OUTCOME for a in assertions
            ),
            "MITIGATION_TARGET_QUERY_COUNT": sum(
                t.semantic_role == "MITIGATION_OR_PREVENTION"
                for q in queries
                for t in targets
                if t.claim_id == q.target_claim_id
            ),
            "METADATA_TARGET_QUERY_COUNT": sum(
                t.semantic_role == "METADATA"
                for q in queries
                for t in targets
                if t.claim_id == q.target_claim_id
            ),
            "REDUNDANT_VERIFIED_QUERY_COUNT": sum(
                by_id[q.target_claim_id].is_verified for q in queries if q.target_claim_id
            ),
            "UNSUPPORTED_HYBRID_QUERY_COUNT": sum(
                a.direction == CausalDirection.AMBIGUOUS_RELATION
                or not a.provenance.get("source_grounded")
                for a in assertions
            ),
            "FOREIGN_SOURCE_EVIDENCE_COUNT": foreign_count,
        }
        output = {
            "source_count": len(sources),
            "canonical_claim_count": len(claims),
            "verified_claim_count": len(verified),
            "valid_families": families,
            "verified_claims": [
                {
                    "id": str(c.id),
                    "text": c.claim_text,
                    "confidence": c.confidence_score,
                    "independent_support": c.independent_sources_count,
                    "family": derive_verified_family_label(
                        c.claim_text, entity_type=contract.entity_type
                    )[1],
                }
                for c in verified
            ],
            "queries": details,
            "eligible_target_count": len(targets),
            "counters": counters,
        }
        Path(sys.argv[4]).write_text(json.dumps(output, indent=2), encoding="utf-8")
        assert len(verified) == 3 and set(families) == {
            "differential shrinkage",
            "plastic shrinkage",
        }
        assert all(v == 0 for v in counters.values())
        assert len(queries) == 3 and len({q.candidate_family for q in queries}) == 3
        print(
            json.dumps(
                {
                    "source_count": len(sources),
                    "verified_claim_count": len(verified),
                    "valid_families": families,
                    "queries": [q.query_text for q in queries],
                    "counters": counters,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
