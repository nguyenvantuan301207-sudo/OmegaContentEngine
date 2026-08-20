"""Research Engine application service.

Coordinates source ingestion, source independence clustering, claim extraction,
first-class evidence mapping, contradiction tracking, explainable confidence scoring,
and versioned immutable ResearchBrief compilation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from omega.application.claim_extractor import (
    extract_deterministic_claims_from_source,
    normalize_claim_text,
)
from omega.application.research_scorer import (
    calculate_source_quality,
    detect_claim_conflicts,
    determine_research_outcome,
    evaluate_claim_confidence,
)
from omega.application.source_independence import cluster_source_independence
from omega.application.source_normalizer import (
    bound_excerpt,
    compute_source_content_hash,
    normalize_source_text,
    normalize_url,
)
from omega.application.source_provider import SourceAuthorityProvider
from omega.domain.channel import ChannelState
from omega.domain.research import (
    CitationRef,
    ClaimEvidenceCreate,
    ClaimEvidenceResponse,
    ClaimType,
    ConflictBrief,
    ConflictStatus,
    EvidenceDirection,
    PrimarySourceStatus,
    ResearchBriefResponse,
    ResearchBriefSummaryResponse,
    ResearchClaimCreate,
    ResearchClaimResponse,
    ResearchConflictResponse,
    ResearchOutcome,
    ResearchRequestCreate,
    ResearchRequestResponse,
    ResearchRequestStatus,
    ResearchRunPayload,
    ResearchSourceBatchCreate,
    ResearchSourceCreate,
    ResearchSourceResponse,
    UncertainClaimBrief,
    VerifiedClaimBrief,
)
from omega.domain.research_scoring import (
    DEFAULT_CLAIM_CONFIDENCE_PROFILE,
    DEFAULT_OUTCOME_PROFILE,
    ClaimConfidenceProfile,
    ResearchOutcomeProfile,
)
from omega.domain.topic import EvaluationContextMode, TopicStatus
from omega.infrastructure.models import (
    Channel,
    ClaimEvidence,
    MissionExecution,
    ResearchBrief,
    ResearchClaim,
    ResearchConflict,
    ResearchRequest,
    ResearchSource,
    TopicCandidate,
)
from omega.logging import get_logger

logger = get_logger(service="omega-research-service")


async def create_research_request(
    session: AsyncSession,
    channel_id: UUID,
    request_in: ResearchRequestCreate,
) -> ResearchRequestResponse:
    """Create a research request with topic eligibility and context validation."""
    # 1. Verify Channel exists and is not ARCHIVED
    chan_res = await session.execute(select(Channel).where(Channel.id == channel_id))
    channel = chan_res.scalar_one_or_none()
    if not channel:
        raise ValueError(f"Channel with ID '{channel_id}' not found.")
    if channel.state == ChannelState.ARCHIVED.value:
        raise ValueError(f"Cannot perform research for ARCHIVED channel '{channel_id}'.")

    # 2. Verify TopicCandidate exists and belongs to Channel
    topic_res = await session.execute(
        select(TopicCandidate).where(
            TopicCandidate.id == request_in.topic_candidate_id,
            TopicCandidate.channel_id == channel_id,
        )
    )
    topic = topic_res.scalar_one_or_none()
    if not topic:
        raise ValueError(
            f"TopicCandidate '{request_in.topic_candidate_id}' not found for channel '{channel_id}'."
        )

    mode = EvaluationContextMode.INTERACTIVE.value
    # 3. Topic State Eligibility & Context Resolution
    if request_in.mission_execution_id:
        mode = EvaluationContextMode.MISSION_EXECUTION.value
        # In MISSION_EXECUTION mode, topic MUST be SELECTED
        if topic.status != TopicStatus.SELECTED.value:
            raise ValueError(
                f"TopicCandidate '{topic.id}' is in state '{topic.status}'. In MISSION_EXECUTION mode, topic must be in 'SELECTED' state."
            )

        exec_res = await session.execute(
            select(MissionExecution)
            .options(selectinload(MissionExecution.mission))
            .where(MissionExecution.id == request_in.mission_execution_id)
        )
        execution = exec_res.scalar_one_or_none()
        if not execution:
            raise ValueError(f"MissionExecution '{request_in.mission_execution_id}' not found.")
        if execution.mission.channel_id != channel_id:
            raise ValueError(
                f"MissionExecution '{execution.id}' belongs to channel '{execution.mission.channel_id}', not target channel '{channel_id}'."
            )
        if not execution.channel_dna_revision_id:
            raise ValueError(
                f"MissionExecution '{execution.id}' does not have a pinned ChannelDNARevision."
            )
    else:
        # In INTERACTIVE mode, topic can be EVALUATED, RECOMMENDED, or SELECTED. DISCOVERED/ARCHIVED are rejected.
        if topic.status in (TopicStatus.DISCOVERED.value, TopicStatus.ARCHIVED.value):
            raise ValueError(
                f"Cannot initiate research on topic candidate in '{topic.status}' state. Candidate must be EVALUATED, RECOMMENDED, or SELECTED."
            )

    request_id = uuid.uuid4()
    req = ResearchRequest(
        id=request_id,
        channel_id=channel_id,
        topic_candidate_id=request_in.topic_candidate_id,
        mission_execution_id=request_in.mission_execution_id,
        mode=mode,
        status=ResearchRequestStatus.PENDING.value,
        research_question=request_in.research_question,
        scope=request_in.scope,
        language=request_in.language,
        region=request_in.region,
        max_sources=request_in.max_sources,
        minimum_source_quality=request_in.minimum_source_quality,
        minimum_claim_confidence=request_in.minimum_claim_confidence,
        metadata_=request_in.metadata,
    )
    session.add(req)
    await session.commit()

    logger.info(
        "ResearchRequest created",
        request_id=str(request_id),
        channel_id=str(channel_id),
        topic_id=str(request_in.topic_candidate_id),
        mode=mode,
    )
    return _to_request_response(req)


async def get_research_request(
    session: AsyncSession,
    request_id: UUID,
) -> ResearchRequestResponse | None:
    """Retrieve single ResearchRequest by ID."""
    res = await session.execute(select(ResearchRequest).where(ResearchRequest.id == request_id))
    req = res.scalar_one_or_none()
    if not req:
        return None
    return _to_request_response(req)


async def list_research_requests(
    session: AsyncSession,
    channel_id: UUID,
    status: str | None = None,
    topic_candidate_id: UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[ResearchRequestResponse]:
    """List research requests for a channel with filtering and pagination."""
    stmt = (
        select(ResearchRequest)
        .where(ResearchRequest.channel_id == channel_id)
        .order_by(ResearchRequest.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if status:
        stmt = stmt.where(ResearchRequest.status == status)
    if topic_candidate_id:
        stmt = stmt.where(ResearchRequest.topic_candidate_id == topic_candidate_id)

    res = await session.execute(stmt)
    return [_to_request_response(r) for r in res.scalars().all()]


async def add_source(
    session: AsyncSession,
    request_id: UUID,
    source_in: ResearchSourceCreate,
    authority_provider: SourceAuthorityProvider | None = None,
) -> ResearchSourceResponse:
    """Add and normalize a research source."""
    req_res = await session.execute(
        select(ResearchRequest)
        .options(selectinload(ResearchRequest.topic_candidate))
        .where(ResearchRequest.id == request_id)
    )
    req = req_res.scalar_one_or_none()
    if not req:
        raise ValueError(f"ResearchRequest '{request_id}' not found.")
    if req.status in (ResearchRequestStatus.CANCELLED.value, ResearchRequestStatus.FAILED.value):
        raise ValueError(f"Cannot add source to ResearchRequest in '{req.status}' state.")

    # 1. Normalize and bound
    norm_text = normalize_source_text(source_in.content_excerpt)
    bounded_text = bound_excerpt(source_in.content_excerpt)
    content_hash = compute_source_content_hash(norm_text)
    norm_url = normalize_url(source_in.url)

    # 2. Prevent duplicate source within same request
    dup_res = await session.execute(
        select(ResearchSource).where(
            ResearchSource.research_request_id == request_id,
            ResearchSource.content_hash == content_hash,
        )
    )
    dup = dup_res.scalar_one_or_none()
    if dup:
        return _to_source_response(dup)

    # 3. Compute initial source quality
    keywords = req.topic_candidate.keywords if req.topic_candidate else []
    q_score, rel_score, fresh_score, reasons = calculate_source_quality(
        publisher=source_in.publisher,
        url=norm_url,
        primary_source_status=source_in.primary_source_status,
        published_at=source_in.published_at,
        topic_keywords=keywords,
        content_excerpt=bounded_text,
        authority_provider=authority_provider,
    )

    source_id = uuid.uuid4()
    source = ResearchSource(
        id=source_id,
        research_request_id=request_id,
        channel_id=req.channel_id,
        source_type=source_in.source_type.value,
        title=source_in.title.strip(),
        publisher=source_in.publisher.strip(),
        author=source_in.author.strip() if source_in.author else None,
        url=norm_url,
        content_excerpt=bounded_text,
        content_hash=content_hash,
        primary_source_status=source_in.primary_source_status.value,
        quality_score=q_score,
        relevance_score=rel_score,
        freshness_score=fresh_score,
        quality_reasons=reasons,
        published_at=source_in.published_at,
        language=source_in.language,
        region=source_in.region,
        metadata_=source_in.metadata,
    )
    session.add(source)
    await session.commit()

    logger.info("ResearchSource added", source_id=str(source_id), request_id=str(request_id))
    return _to_source_response(source)


async def batch_add_sources(
    session: AsyncSession,
    request_id: UUID,
    batch_in: ResearchSourceBatchCreate,
    authority_provider: SourceAuthorityProvider | None = None,
) -> list[ResearchSourceResponse]:
    """Batch ingest multiple research sources."""
    results: list[ResearchSourceResponse] = []
    for s_in in batch_in.sources:
        s_resp = await add_source(session, request_id, s_in, authority_provider)
        results.append(s_resp)
    return results


async def list_sources(
    session: AsyncSession,
    request_id: UUID,
    limit: int = 50,
    offset: int = 0,
) -> list[ResearchSourceResponse]:
    """List normalized sources for a research request."""
    res = await session.execute(
        select(ResearchSource)
        .where(ResearchSource.research_request_id == request_id)
        .order_by(ResearchSource.retrieved_at.asc())
        .limit(limit)
        .offset(offset)
    )
    return [_to_source_response(s) for s in res.scalars().all()]


async def add_claim(
    session: AsyncSession,
    request_id: UUID,
    claim_in: ResearchClaimCreate,
) -> ResearchClaimResponse:
    """Create an extracted claim with optional initial evidence linkages."""
    req_res = await session.execute(select(ResearchRequest).where(ResearchRequest.id == request_id))
    req = req_res.scalar_one_or_none()
    if not req:
        raise ValueError(f"ResearchRequest '{request_id}' not found.")

    claim_id = uuid.uuid4()
    norm_claim = normalize_claim_text(claim_in.claim_text)

    claim = ResearchClaim(
        id=claim_id,
        research_request_id=request_id,
        channel_id=req.channel_id,
        claim_text=claim_in.claim_text.strip(),
        normalized_claim=norm_claim,
        claim_type=claim_in.claim_type.value,
        metadata_=claim_in.metadata,
    )

    # Attach initial evidence
    for ev_in in claim_in.evidence:
        # Verify source belongs to request
        src_res = await session.execute(
            select(ResearchSource).where(
                ResearchSource.id == ev_in.source_id,
                ResearchSource.research_request_id == request_id,
            )
        )
        if not src_res.scalar_one_or_none():
            raise ValueError(
                f"Source '{ev_in.source_id}' does not belong to request '{request_id}'."
            )

        claim.evidence.append(
            ClaimEvidence(
                id=uuid.uuid4(),
                claim_id=claim_id,
                source_id=ev_in.source_id,
                support_direction=ev_in.support_direction.value,
                excerpt=ev_in.excerpt.strip(),
                source_location=ev_in.source_location,
                strength_score=ev_in.strength_score,
            )
        )

    session.add(claim)
    await session.commit()

    loaded_claim_res = await session.execute(
        select(ResearchClaim)
        .options(selectinload(ResearchClaim.evidence))
        .where(ResearchClaim.id == claim_id)
    )
    loaded_claim = loaded_claim_res.scalars().one()
    return _to_claim_response(loaded_claim)


async def add_evidence(
    session: AsyncSession,
    request_id: UUID,
    claim_id: UUID,
    evidence_in: ClaimEvidenceCreate,
) -> ClaimEvidenceResponse:
    """Add first-class evidence linked to a claim and source."""
    claim_res = await session.execute(
        select(ResearchClaim).where(
            ResearchClaim.id == claim_id,
            ResearchClaim.research_request_id == request_id,
        )
    )
    claim = claim_res.scalar_one_or_none()
    if not claim:
        raise ValueError(f"Claim '{claim_id}' not found for request '{request_id}'.")

    src_res = await session.execute(
        select(ResearchSource).where(
            ResearchSource.id == evidence_in.source_id,
            ResearchSource.research_request_id == request_id,
        )
    )
    if not src_res.scalar_one_or_none():
        raise ValueError(
            f"Source '{evidence_in.source_id}' does not belong to request '{request_id}'."
        )

    ev_id = uuid.uuid4()
    ev = ClaimEvidence(
        id=ev_id,
        claim_id=claim_id,
        source_id=evidence_in.source_id,
        support_direction=evidence_in.support_direction.value,
        excerpt=evidence_in.excerpt.strip(),
        source_location=evidence_in.source_location,
        strength_score=evidence_in.strength_score,
    )
    session.add(ev)
    await session.commit()

    logger.info("ClaimEvidence added", evidence_id=str(ev_id), claim_id=str(claim_id))
    return ClaimEvidenceResponse.model_validate(ev)


async def list_evidence(
    session: AsyncSession,
    claim_id: UUID,
) -> list[ClaimEvidenceResponse]:
    """List all evidence for a claim."""
    res = await session.execute(
        select(ClaimEvidence)
        .where(ClaimEvidence.claim_id == claim_id)
        .order_by(ClaimEvidence.created_at.asc())
    )
    return [ClaimEvidenceResponse.model_validate(e) for e in res.scalars().all()]


async def list_claims(
    session: AsyncSession,
    request_id: UUID,
    is_verified: bool | None = None,
    claim_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[ResearchClaimResponse]:
    """List claims for a research request."""
    stmt = (
        select(ResearchClaim)
        .options(selectinload(ResearchClaim.evidence))
        .where(ResearchClaim.research_request_id == request_id)
        .order_by(ResearchClaim.confidence_score.desc())
        .limit(limit)
        .offset(offset)
    )
    if is_verified is not None:
        stmt = stmt.where(ResearchClaim.is_verified == is_verified)
    if claim_type:
        stmt = stmt.where(ResearchClaim.claim_type == claim_type)

    res = await session.execute(stmt)
    return [_to_claim_response(c) for c in res.scalars().all()]


async def list_conflicts(
    session: AsyncSession,
    request_id: UUID,
    status: str | None = None,
) -> list[ResearchConflictResponse]:
    """List detected contradictions for a research request."""
    stmt = (
        select(ResearchConflict)
        .where(ResearchConflict.research_request_id == request_id)
        .order_by(ResearchConflict.detected_at.desc())
    )
    if status:
        stmt = stmt.where(ResearchConflict.status == status)

    res = await session.execute(stmt)
    return [ResearchConflictResponse.model_validate(c) for c in res.scalars().all()]


async def run_research(
    session: AsyncSession,
    request_id: UUID,
    payload: ResearchRunPayload | None = None,
    confidence_profile: ClaimConfidenceProfile = DEFAULT_CLAIM_CONFIDENCE_PROFILE,
    outcome_profile: ResearchOutcomeProfile = DEFAULT_OUTCOME_PROFILE,
    authority_provider: SourceAuthorityProvider | None = None,
) -> ResearchBriefResponse:
    """Execute complete research pipeline and produce an immutable versioned ResearchBrief."""
    # 1. Acquire row-level lock on ResearchRequest
    req_res = await session.execute(
        select(ResearchRequest)
        .options(selectinload(ResearchRequest.topic_candidate))
        .where(ResearchRequest.id == request_id)
        .with_for_update()
    )
    req = req_res.scalar_one_or_none()
    if not req:
        raise ValueError(f"ResearchRequest '{request_id}' not found.")

    if req.status == ResearchRequestStatus.RUNNING.value:
        raise ValueError("Research request is currently running. Please wait for completion.")
    if req.status == ResearchRequestStatus.CANCELLED.value:
        raise ValueError("Cannot run CANCELLED research request.")

    req.status = ResearchRequestStatus.RUNNING.value
    req.started_at = datetime.now(UTC)
    await session.flush()

    # 2. Load all sources
    src_res = await session.execute(
        select(ResearchSource).where(ResearchSource.research_request_id == request_id)
    )
    sources = src_res.scalars().all()

    # 3. Cluster source independence
    sources_data = [
        {
            "id": s.id,
            "title": s.title,
            "publisher": s.publisher,
            "url": s.url,
            "content_excerpt": s.content_excerpt,
            "content_hash": s.content_hash,
            "quality_score": s.quality_score,
            "primary_source_status": PrimarySourceStatus(s.primary_source_status),
        }
        for s in sources
    ]
    clusters = cluster_source_independence(sources_data)
    for s in sources:
        s.independence_cluster_id = clusters.get(s.id)

    # 4. Extract claims from sources if no claims have been added yet
    claim_res = await session.execute(
        select(ResearchClaim)
        .options(selectinload(ResearchClaim.evidence))
        .where(ResearchClaim.research_request_id == request_id)
    )
    claims = list(claim_res.scalars().all())

    if not claims:
        for s in sources:
            extracted = extract_deterministic_claims_from_source(
                source_title=s.title,
                source_excerpt=s.content_excerpt,
                metadata=dict(s.metadata_ or {}),
            )
            for item in extracted:
                c_id = uuid.uuid4()
                claim_obj = ResearchClaim(
                    id=c_id,
                    research_request_id=request_id,
                    channel_id=req.channel_id,
                    claim_text=item["claim_text"],
                    normalized_claim=normalize_claim_text(item["claim_text"]),
                    claim_type=item["claim_type"].value,
                )
                claim_obj.evidence.append(
                    ClaimEvidence(
                        id=uuid.uuid4(),
                        claim_id=c_id,
                        source_id=s.id,
                        support_direction=EvidenceDirection.SUPPORTS.value,
                        excerpt=item["excerpt"],
                        source_location=item["source_location"],
                        strength_score=item["strength_score"],
                    )
                )
                session.add(claim_obj)
                claims.append(claim_obj)
        await session.flush()

    # 5. Detect and record conflicts
    sources_map = {s["id"]: s for s in sources_data}
    open_conflicts: list[dict[str, Any]] = []

    for c in claims:
        ev_data = [
            {
                "id": e.id,
                "source_id": e.source_id,
                "support_direction": EvidenceDirection(e.support_direction),
                "excerpt": e.excerpt,
                "strength_score": e.strength_score,
            }
            for e in c.evidence
        ]
        detected = detect_claim_conflicts(c.id, c.claim_text, ev_data)
        for conf_dict in detected:
            open_conflicts.append(conf_dict)
            conflict_obj = ResearchConflict(
                id=uuid.uuid4(),
                research_request_id=request_id,
                claim_id=c.id,
                conflict_type=conf_dict["conflict_type"],
                severity=conf_dict["severity"].value,
                status=ConflictStatus.OPEN.value,
                description=conf_dict["description"],
                involved_evidence_ids=conf_dict["involved_evidence_ids"],
                involved_source_ids=conf_dict["involved_source_ids"],
            )
            session.add(conflict_obj)

    # 6. Evaluate claim confidence and verification status
    for c in claims:
        ev_data = [
            {
                "id": e.id,
                "source_id": e.source_id,
                "support_direction": EvidenceDirection(e.support_direction),
                "excerpt": e.excerpt,
                "strength_score": e.strength_score,
            }
            for e in c.evidence
        ]
        claim_conflicts = [conf for conf in open_conflicts if conf.get("claim_id") == c.id]
        metrics = evaluate_claim_confidence(
            claim={"claim_type": c.claim_type},
            evidence_items=ev_data,
            sources_map=sources_map,
            conflicts=claim_conflicts,
            profile=confidence_profile,
        )

        c.confidence_score = metrics["confidence_score"]
        c.confidence_band = metrics["confidence_band"].value
        c.supporting_sources_count = metrics["supporting_sources_count"]
        c.contradicting_sources_count = metrics["contradicting_sources_count"]
        c.independent_sources_count = metrics["independent_sources_count"]
        c.is_verified = metrics["is_verified"]
        c.reasons = metrics["reasons"]

    # 7. Calculate overall metrics and outcome
    verified_claims = [c for c in claims if c.is_verified]
    uncertain_claims = [c for c in claims if not c.is_verified]
    independent_clusters_count = len(
        {s.independence_cluster_id for s in sources if s.independence_cluster_id}
    )

    outcome = determine_research_outcome(
        sources_count=len(sources),
        independent_sources_count=independent_clusters_count,
        verified_claims_count=len(verified_claims),
        open_high_conflicts_count=len(open_conflicts),
        profile=outcome_profile,
    )

    avg_conf = sum(c.confidence_score for c in claims) / len(claims) if claims else 0.0

    # 8. Versioned Immutable ResearchBrief creation
    # Fetch existing brief revisions for this request
    briefs_res = await session.execute(
        select(ResearchBrief)
        .where(ResearchBrief.research_request_id == request_id)
        .order_by(desc(ResearchBrief.version))
        .with_for_update()
    )
    existing_briefs = list(briefs_res.scalars().all())

    # Mark existing is_current briefs as False
    previous_current_id: UUID | None = None
    next_version = 1
    if existing_briefs:
        next_version = existing_briefs[0].version + 1
        for b in existing_briefs:
            if b.is_current:
                previous_current_id = b.id
                b.is_current = False

    # Format verified claims with citation references
    verified_brief_items: list[dict[str, Any]] = []
    for c in verified_claims:
        citations = []
        for e in c.evidence:
            src_obj = next((s for s in sources if s.id == e.source_id), None)
            citations.append(
                {
                    "source_id": str(e.source_id),
                    "evidence_id": str(e.id),
                    "publisher": src_obj.publisher if src_obj else "Unknown",
                    "excerpt": e.excerpt,
                    "source_location": e.source_location,
                }
            )
        verified_brief_items.append(
            {
                "claim_id": str(c.id),
                "text": c.claim_text,
                "type": c.claim_type,
                "confidence_score": c.confidence_score,
                "confidence_band": c.confidence_band,
                "citations": citations,
            }
        )

    uncertain_brief_items = [
        {
            "claim_id": str(c.id),
            "text": c.claim_text,
            "type": c.claim_type,
            "confidence_score": c.confidence_score,
            "confidence_band": c.confidence_band,
            "uncertainty_reason": "; ".join(c.reasons)
            if c.reasons
            else "Below confidence threshold",
        }
        for c in uncertain_claims
    ]

    conflicts_brief_items = [
        {
            "conflict_id": str(uuid.uuid4()),
            "claim_id": str(conf["claim_id"]) if conf.get("claim_id") else None,
            "description": conf["description"],
            "severity": conf["severity"].value,
            "involved_source_ids": conf.get("involved_source_ids", []),
        }
        for conf in open_conflicts
    ]

    # Synthesize key facts, stats, quotes
    key_facts = [c.claim_text for c in verified_claims if c.claim_type == ClaimType.FACT.value]
    statistics = [
        {"claim": c.claim_text, "confidence": c.confidence_score}
        for c in verified_claims
        if c.claim_type == ClaimType.STATISTIC.value
    ]
    dates = [
        {"event": c.claim_text, "confidence": c.confidence_score}
        for c in verified_claims
        if c.claim_type == ClaimType.DATE.value
    ]
    quotes = [
        {"quote": c.claim_text, "confidence": c.confidence_score}
        for c in verified_claims
        if c.claim_type == ClaimType.QUOTE.value
    ]

    topic_title = req.topic_candidate.title if req.topic_candidate else "Research Topic"
    summary_text = (
        f"Research briefing for '{topic_title}'. "
        f"Analyzed {len(sources)} sources across {independent_clusters_count} independent clusters. "
        f"Verified {len(verified_claims)} factual claims with {len(open_conflicts)} open conflicts. "
        f"Outcome: {outcome.value}."
    )

    brief_id = uuid.uuid4()
    brief = ResearchBrief(
        id=brief_id,
        research_request_id=request_id,
        channel_id=req.channel_id,
        topic_candidate_id=req.topic_candidate_id,
        version=next_version,
        supersedes_brief_id=previous_current_id,
        is_current=True,
        outcome=outcome.value,
        overall_confidence=round(avg_conf, 2),
        title=f"Research Brief: {topic_title} (v{next_version})",
        summary=summary_text,
        verified_claims=verified_brief_items,
        uncertain_claims=uncertain_brief_items,
        contradictions=conflicts_brief_items,
        key_facts=key_facts,
        statistics=statistics,
        dates=dates,
        quotes=quotes,
        open_questions=[
            "Further empirical evidence required."
            if outcome != ResearchOutcome.SUFFICIENT
            else "None."
        ],
        sources_summary={
            "total_sources": len(sources),
            "independent_clusters": independent_clusters_count,
            "high_quality_sources": sum(1 for s in sources if s.quality_score >= 70.0),
            "confirmed_primary_sources": sum(
                1 for s in sources if s.primary_source_status == PrimarySourceStatus.CONFIRMED.value
            ),
        },
    )
    session.add(brief)

    # 9. Update ResearchRequest status
    req.status = ResearchRequestStatus.SUCCEEDED.value
    req.outcome = outcome.value
    req.completed_at = datetime.now(UTC)

    await session.commit()

    logger.info(
        "Research pipeline completed",
        request_id=str(request_id),
        brief_version=next_version,
        outcome=outcome.value,
        verified_claims=len(verified_claims),
    )
    return _to_brief_response(brief)


async def get_brief(
    session: AsyncSession,
    request_id: UUID,
    version: int | None = None,
) -> ResearchBriefResponse | None:
    """Retrieve current or specific version of ResearchBrief."""
    stmt = select(ResearchBrief).where(ResearchBrief.research_request_id == request_id)
    if version is not None:
        stmt = stmt.where(ResearchBrief.version == version)
    else:
        stmt = stmt.where(ResearchBrief.is_current.is_(True))

    res = await session.execute(stmt)
    brief = res.scalar_one_or_none()
    if not brief:
        return None
    return _to_brief_response(brief)


async def list_briefs(
    session: AsyncSession,
    request_id: UUID,
) -> list[ResearchBriefSummaryResponse]:
    """List all historical brief revisions for a research request."""
    res = await session.execute(
        select(ResearchBrief)
        .where(ResearchBrief.research_request_id == request_id)
        .order_by(desc(ResearchBrief.version))
    )
    briefs = res.scalars().all()
    return [
        ResearchBriefSummaryResponse(
            id=b.id,
            research_request_id=b.research_request_id,
            version=b.version,
            supersedes_brief_id=b.supersedes_brief_id,
            is_current=b.is_current,
            outcome=ResearchOutcome(b.outcome),
            overall_confidence=b.overall_confidence,
            verified_claims_count=len(b.verified_claims or []),
            contradictions_count=len(b.contradictions or []),
            created_at=b.created_at,
        )
        for b in briefs
    ]


async def cancel_research_request(
    session: AsyncSession,
    request_id: UUID,
) -> ResearchRequestResponse:
    """Cancel an in-flight or pending research request."""
    res = await session.execute(select(ResearchRequest).where(ResearchRequest.id == request_id))
    req = res.scalar_one_or_none()
    if not req:
        raise ValueError(f"ResearchRequest '{request_id}' not found.")

    if req.status in (ResearchRequestStatus.SUCCEEDED.value, ResearchRequestStatus.CANCELLED.value):
        return _to_request_response(req)

    req.status = ResearchRequestStatus.CANCELLED.value
    await session.commit()
    logger.info("ResearchRequest cancelled", request_id=str(request_id))
    return _to_request_response(req)


# ── Response Converters ──


def _to_request_response(req: ResearchRequest) -> ResearchRequestResponse:
    return ResearchRequestResponse(
        id=req.id,
        channel_id=req.channel_id,
        topic_candidate_id=req.topic_candidate_id,
        mission_execution_id=req.mission_execution_id,
        mode=req.mode,
        status=ResearchRequestStatus(req.status),
        outcome=ResearchOutcome(req.outcome) if req.outcome else None,
        research_question=req.research_question,
        scope=req.scope,
        language=req.language,
        region=req.region,
        max_sources=req.max_sources,
        minimum_source_quality=req.minimum_source_quality,
        minimum_claim_confidence=req.minimum_claim_confidence,
        metadata_=dict(req.metadata_ or {}),
        created_at=req.created_at,
        started_at=req.started_at,
        completed_at=req.completed_at,
        failed_at=req.failed_at,
    )


def _to_source_response(s: ResearchSource) -> ResearchSourceResponse:
    return ResearchSourceResponse(
        id=s.id,
        research_request_id=s.research_request_id,
        channel_id=s.channel_id,
        source_type=s.source_type,  # type: ignore[arg-type]
        title=s.title,
        publisher=s.publisher,
        author=s.author,
        url=s.url,
        content_excerpt=s.content_excerpt,
        content_hash=s.content_hash,
        primary_source_status=PrimarySourceStatus(s.primary_source_status),
        quality_score=s.quality_score,
        relevance_score=s.relevance_score,
        freshness_score=s.freshness_score,
        quality_reasons=list(s.quality_reasons or []),
        independence_cluster_id=s.independence_cluster_id,
        published_at=s.published_at,
        retrieved_at=s.retrieved_at,
        language=s.language,
        region=s.region,
        metadata_=dict(s.metadata_ or {}),
    )


def _to_claim_response(c: ResearchClaim) -> ResearchClaimResponse:
    ev_resps = [
        ClaimEvidenceResponse(
            id=e.id,
            claim_id=e.claim_id,
            source_id=e.source_id,
            support_direction=e.support_direction,  # type: ignore[arg-type]
            excerpt=e.excerpt,
            source_location=e.source_location,
            strength_score=e.strength_score,
            created_at=e.created_at,
        )
        for e in (c.evidence or [])
    ]

    return ResearchClaimResponse(
        id=c.id,
        research_request_id=c.research_request_id,
        channel_id=c.channel_id,
        claim_text=c.claim_text,
        normalized_claim=c.normalized_claim,
        claim_type=c.claim_type,  # type: ignore[arg-type]
        confidence_score=c.confidence_score,
        confidence_band=c.confidence_band,  # type: ignore[arg-type]
        supporting_sources_count=c.supporting_sources_count,
        contradicting_sources_count=c.contradicting_sources_count,
        independent_sources_count=c.independent_sources_count,
        is_verified=c.is_verified,
        reasons=list(c.reasons or []),
        evidence=ev_resps,
        metadata_=dict(c.metadata_ or {}),
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


def _to_brief_response(b: ResearchBrief) -> ResearchBriefResponse:
    verified_items = [
        VerifiedClaimBrief(
            claim_id=UUID(item["claim_id"]),
            text=item["text"],
            type=item["type"],
            confidence_score=item["confidence_score"],
            confidence_band=item["confidence_band"],
            citations=[
                CitationRef(
                    source_id=UUID(cit["source_id"]),
                    evidence_id=UUID(cit["evidence_id"]),
                    publisher=cit["publisher"],
                    excerpt=cit["excerpt"],
                    source_location=cit.get("source_location"),
                )
                for cit in item.get("citations", [])
            ],
        )
        for item in (b.verified_claims or [])
    ]

    uncertain_items = [
        UncertainClaimBrief(
            claim_id=UUID(item["claim_id"]),
            text=item["text"],
            type=item["type"],
            confidence_score=item["confidence_score"],
            confidence_band=item["confidence_band"],
            uncertainty_reason=item["uncertainty_reason"],
        )
        for item in (b.uncertain_claims or [])
    ]

    conflict_items = [
        ConflictBrief(
            conflict_id=UUID(item["conflict_id"]),
            claim_id=UUID(item["claim_id"]) if item.get("claim_id") else None,
            description=item["description"],
            severity=item["severity"],
            involved_source_ids=list(item.get("involved_source_ids", [])),
        )
        for item in (b.contradictions or [])
    ]

    return ResearchBriefResponse(
        id=b.id,
        research_request_id=b.research_request_id,
        topic_candidate_id=b.topic_candidate_id,
        channel_id=b.channel_id,
        version=b.version,
        supersedes_brief_id=b.supersedes_brief_id,
        is_current=b.is_current,
        outcome=b.outcome,  # type: ignore[arg-type]
        overall_confidence=b.overall_confidence,
        title=b.title,
        summary=b.summary,
        verified_claims=verified_items,
        uncertain_claims=uncertain_items,
        contradictions=conflict_items,
        key_facts=list(b.key_facts or []),
        statistics=list(b.statistics or []),
        dates=list(b.dates or []),
        quotes=list(b.quotes or []),
        open_questions=list(b.open_questions or []),
        sources_summary=dict(b.sources_summary or {}),
        metadata_=dict(b.metadata_ or {}),
        created_at=b.created_at,
    )
