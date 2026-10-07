"""Coverage-Driven Research Expansion Service (P0.3).

Coordinates bounded research search planning, source discovery, deduplication,
source ingestion, evidence extraction, distinct entity clustering, and fail-closed
sufficiency gating.

CRITICAL INVARIANTS:
1. P0.2 NumericPromiseContract is the canonical sufficiency authority.
2. System never invents missing promised entities or claims.
3. Research authority is strictly derived from verified acquired sources.
4. Discovery snippets are never treated as verified claims.
5. All executions are bounded and idempotent.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from omega.application import research_service
from omega.application.claim_extractor import extract_deterministic_claims_from_source
from omega.application.claim_reconciliation import reconcile_source_extractions_into_claims
from omega.application.research_discovery import (
    ContentExtractionError,
    DiscoveryProviderError,
    NullResearchContentExtractor,
    ResearchContentExtractor,
    ResearchDiscoveryProvider,
    filter_and_deduplicate_candidates,
)
from omega.application.research_query_planner import (
    build_corroboration_targets,
    plan_research_queries,
)
from omega.application.research_scorer import calculate_source_quality
from omega.application.research_service import evaluate_canonical_claims
from omega.application.source_normalizer import bound_excerpt, normalize_url
from omega.application.source_provider import SourceAuthorityProvider
from omega.domain.numeric_promise import (
    NumericPromiseContract,
    extract_distinct_entities,
    extract_numeric_promise,
)
from omega.domain.research import (
    CorroborationTarget,
    DiscoveryCandidate,
    ResearchAcquisitionMode,
    ResearchCoverageRoundTruth,
    ResearchCoverageStopReason,
    ResearchOutcome,
    ResearchSourceCreate,
    ResearchSourceType,
)
from omega.infrastructure.models import (
    ResearchClaim,
    ResearchRequest,
    ResearchSource,
)
from omega.logging import get_logger

logger = get_logger(service="omega-research-coverage-service")


async def execute_coverage_driven_research(
    session: AsyncSession,
    request_id: UUID,
    discovery_provider: ResearchDiscoveryProvider | None = None,
    content_extractor: ResearchContentExtractor | None = None,
    authority_provider: SourceAuthorityProvider | None = None,
    max_rounds: int = 3,
    max_queries_per_round: int = 3,
    max_candidates_per_query: int = 5,
    max_accepted_sources_per_round: int = 5,
    max_total_acquired_sources: int = 15,
) -> dict[str, Any]:
    """Execute bounded coverage-driven research acquisition and brief compilation.

    Returns an inspectable result summary containing:
    - request_id
    - acquisition_mode
    - stop_reason
    - rounds_executed
    - initial_supported_count
    - final_supported_count
    - final_supported_families
    - brief (ResearchBriefResponse or None)
    - coverage_truth
    """
    if content_extractor is None:
        content_extractor = NullResearchContentExtractor()

    # 1. Load ResearchRequest with relationships
    req_res = await session.execute(
        select(ResearchRequest)
        .options(
            selectinload(ResearchRequest.topic_candidate),
            selectinload(ResearchRequest.sources),
        )
        .where(ResearchRequest.id == request_id)
    )
    req = req_res.scalar_one_or_none()
    if not req:
        raise ValueError(f"ResearchRequest '{request_id}' not found.")

    req_meta = dict(req.metadata_ or {})
    acquisition_mode = req_meta.get("acquisition_mode", ResearchAcquisitionMode.MANUAL.value)

    topic_title = (
        req.topic_candidate.title
        if req.topic_candidate and req.topic_candidate.title
        else (req.research_question or "Research Topic")
    )

    # Resolve NumericPromiseContract
    contract: NumericPromiseContract | None = None
    if "numeric_contract" in req_meta and isinstance(req_meta["numeric_contract"], dict):
        contract = NumericPromiseContract.from_dict(req_meta["numeric_contract"])
    elif req.topic_candidate and req.topic_candidate.metadata_ and "numeric_contract" in req.topic_candidate.metadata_:
        contract = NumericPromiseContract.from_dict(req.topic_candidate.metadata_["numeric_contract"])
    elif topic_title:
        contract = extract_numeric_promise(topic_title)

    promised_count = contract.promised_count if contract else None
    entity_type = contract.entity_type if contract else None

    # Check for MANUAL mode preservation
    if acquisition_mode == ResearchAcquisitionMode.MANUAL.value or discovery_provider is None:
        logger.info(
            "ResearchRequest is in MANUAL mode; skipping automated discovery",
            request_id=str(request_id),
            acquisition_mode=acquisition_mode,
        )
        return {
            "request_id": str(request_id),
            "acquisition_mode": acquisition_mode,
            "stop_reason": ResearchCoverageStopReason.MANUAL_ONLY_AWAITING_INPUT.value,
            "rounds_executed": 0,
            "initial_supported_count": 0,
            "final_supported_count": 0,
            "final_supported_families": [],
            "brief": None,
            "coverage_truth": [],
            "manual_input_required": True,
        }

    # 2. Evaluate current verified distinct coverage
    all_init_sources_res = await session.execute(
        select(ResearchSource).where(ResearchSource.research_request_id == request_id)
    )
    all_init_sources = list(all_init_sources_res.scalars().all())
    eligible_init_sources = [s for s in all_init_sources if s.quality_score >= req.minimum_source_quality]

    existing_claims_res = await session.execute(
        select(ResearchClaim)
        .options(selectinload(ResearchClaim.evidence))
        .where(ResearchClaim.research_request_id == request_id)
    )
    existing_claims = list(existing_claims_res.scalars().all())
    existing_src_ids = {e.source_id for c in existing_claims for e in (c.evidence or [])}

    for s in eligible_init_sources:
        if s.id not in existing_src_ids:
            extracted = extract_deterministic_claims_from_source(
                source_title=s.title,
                source_excerpt=s.content_excerpt,
                metadata=dict(s.metadata_ or {}),
                source_type=s.source_type,
                topic_keywords=req.topic_candidate.keywords if req.topic_candidate else None,
            )
            reconcile_source_extractions_into_claims(
                session=session,
                existing_claims=existing_claims,
                extracted_items=extracted,
                source=s,
                channel_id=req.channel_id,
                request_id=request_id,
            )
    if eligible_init_sources:
        await session.flush()

    # Canonical evaluation of existing claims
    init_verified_claims, _, _ = evaluate_canonical_claims(
        eligible_sources=eligible_init_sources,
        claims=existing_claims,
    )

    initial_families: list[str] = []
    if init_verified_claims and contract:
        vclaim_texts = [c.claim_text for c in init_verified_claims]
        initial_families = extract_distinct_entities(
            vclaim_texts,
            topic_title=topic_title,
            entity_type=contract.entity_type,
        )
    initial_supported_count = len(initial_families)

    # Effective source limit: respect stricter of req.max_sources and max_total_acquired_sources
    effective_max_sources = min(req.max_sources, max_total_acquired_sources)

    # If already fulfilled before discovery:
    if contract and initial_supported_count >= contract.promised_count:
        brief = await research_service.run_research(
            session=session,
            request_id=request_id,
            authority_provider=authority_provider,
        )
        init_stop_reason = (
            ResearchCoverageStopReason.COVERAGE_FULFILLED
            if brief.outcome == ResearchOutcome.SUFFICIENT
            else ResearchCoverageStopReason.NUMERIC_COVERAGE_NOT_FULFILLED
        )
        return {
            "request_id": str(request_id),
            "acquisition_mode": acquisition_mode,
            "stop_reason": init_stop_reason.value,
            "rounds_executed": 0,
            "initial_supported_count": initial_supported_count,
            "final_supported_count": initial_supported_count,
            "final_supported_families": initial_families,
            "brief": brief,
            "coverage_truth": [],
            "manual_input_required": False,
        }

    # 3. Initialize Coverage Planning & Loop State
    already_seen_urls: set[str] = {
        normalize_url(s.url) for s in (req.sources or []) if s.url
    }
    current_supported_families: list[str] = list(initial_families)
    rounds_truth: list[ResearchCoverageRoundTruth] = []
    stop_reason: ResearchCoverageStopReason | None = None
    issued_query_texts: set[str] = set()
    current_corroboration_targets: list[CorroborationTarget] = []
    if existing_claims and eligible_init_sources:
        init_sources_map = {s.id: s for s in eligible_init_sources}
        current_corroboration_targets = build_corroboration_targets(
            claims=existing_claims,
            sources_map=init_sources_map,
        )

    # 4. Coverage Expansion Loop
    for round_num in range(1, max_rounds + 1):
        supported_before = len(current_supported_families)

        # Budget Check: Total Acquired Sources against effective ceiling
        current_sources_res = await session.execute(
            select(ResearchSource).where(ResearchSource.research_request_id == request_id)
        )
        current_source_count = len(current_sources_res.scalars().all())
        remaining_capacity = effective_max_sources - current_source_count
        if remaining_capacity <= 0:
            stop_reason = ResearchCoverageStopReason.SEARCH_BUDGET_EXHAUSTED
            break

        # Generate deterministic grounded queries
        queries = plan_research_queries(
            topic_title=topic_title,
            contract=contract,
            round_number=round_num,
            already_supported_families=current_supported_families,
            max_queries=max_queries_per_round,
            corroboration_targets=current_corroboration_targets,
            issued_query_texts=issued_query_texts,
        )
        if not queries:
            stop_reason = ResearchCoverageStopReason.NO_NEW_RELEVANT_SOURCES
            break

        for q in queries:
            issued_query_texts.add(q.query_text)

        round_queries_meta = [
            {"query_text": q.query_text, "intent": q.intent.value, "reason": q.reason}
            for q in queries
        ]

        # Execute discovery queries via provider
        raw_candidates: list[DiscoveryCandidate] = []
        provider_failed = False
        for q in queries:
            try:
                candidates = await discovery_provider.search(
                    query=q.query_text,
                    limit=max_candidates_per_query,
                )
                raw_candidates.extend(candidates)
            except (DiscoveryProviderError, Exception) as exc:
                logger.warning(
                    "Discovery provider search failed",
                    query=q.query_text,
                    error=str(exc),
                )
                provider_failed = True
                stop_reason = ResearchCoverageStopReason.DISCOVERY_PROVIDER_UNAVAILABLE
                break

        if provider_failed:
            rounds_truth.append(
                ResearchCoverageRoundTruth(
                    round_number=round_num,
                    queries=round_queries_meta,
                    candidates_discovered=len(raw_candidates),
                    sources_accepted=0,
                    sources_rejected=0,
                    rejection_reasons=["PROVIDER_FAILURE"],
                    supported_count_before=supported_before,
                    supported_count_after=supported_before,
                    supported_families_after=current_supported_families,
                    remaining_count=max(0, (promised_count or 0) - supported_before),
                    stop_reason=stop_reason.value if stop_reason else None,
                )
            )
            break

        # Strictly bound round acceptance to remaining capacity
        round_allowed = min(max_accepted_sources_per_round, remaining_capacity)

        # Filter & deduplicate prospective candidates from the discovered pool
        prospective_candidates, rejections = filter_and_deduplicate_candidates(
            candidates=raw_candidates,
            already_seen_urls=already_seen_urls,
            max_accepted=None,
        )

        # Ingest qualifying evidence-eligible sources through ResearchContentExtractor into canonical pipeline
        sources_added_this_round = 0
        for cand in prospective_candidates:
            if sources_added_this_round >= round_allowed:
                rejections.append(f"ROUND_BUDGET_REACHED: '{cand.canonical_url}'")
                break

            if current_source_count + sources_added_this_round >= effective_max_sources:
                rejections.append(f"CAPPED_AT_MAX_SOURCES: '{cand.canonical_url}'")
                break

            norm_cand_url = normalize_url(cand.canonical_url)

            try:
                extracted_doc = await content_extractor.extract_document(cand)
            except (ContentExtractionError, Exception) as exc:
                logger.warning("Content extraction failed", url=cand.canonical_url, error=str(exc))
                if norm_cand_url:
                    already_seen_urls.add(norm_cand_url)
                rejections.append(f"EXTRACTION_FAILED: '{cand.canonical_url}'")
                continue

            if (
                extracted_doc is None
                or not extracted_doc.extracted_content
                or len(extracted_doc.extracted_content.strip()) < 15
            ):
                if norm_cand_url:
                    already_seen_urls.add(norm_cand_url)
                rejections.append(f"CONTENT_UNAVAILABLE: '{cand.canonical_url}'")
                continue

            # CRITICAL AUTHORITY BOUNDARY:
            # content_excerpt is strictly derived from extracted_doc.extracted_content.
            # cand.snippet is ONLY preserved as discovery metadata / observability.
            # Bound content_excerpt to MAX_EXCERPT_LENGTH (<= 5000 chars) BEFORE validation/scoring
            bounded_content = bound_excerpt(extracted_doc.extracted_content)

            candidate_url = normalize_url(extracted_doc.canonical_url or cand.canonical_url)
            candidate_publisher = (extracted_doc.publisher or cand.publisher or "").strip()
            candidate_primary_status = extracted_doc.primary_source_status
            candidate_published_at = extracted_doc.published_at or cand.published_at
            topic_keywords = req.topic_candidate.keywords if req.topic_candidate else []

            # Canonical pre-persistence quality calculation
            pre_quality_score, _, _, _ = calculate_source_quality(
                publisher=candidate_publisher,
                url=candidate_url,
                primary_source_status=candidate_primary_status,
                published_at=candidate_published_at,
                topic_keywords=topic_keywords,
                content_excerpt=bounded_content,
                authority_provider=authority_provider,
            )

            # Quality gate: only EVIDENCE-ELIGIBLE sources qualify for persistence and budget consumption
            if pre_quality_score < req.minimum_source_quality:
                if norm_cand_url:
                    already_seen_urls.add(norm_cand_url)
                if candidate_url:
                    already_seen_urls.add(candidate_url)
                rejections.append(
                    f"SOURCE_QUALITY_BELOW_MINIMUM: '{candidate_url or cand.canonical_url}' score={pre_quality_score} minimum={req.minimum_source_quality}"
                )
                continue

            # Metadata firewall: clean discovery metadata, isolate snippet, forbid authority injection
            clean_discovery_meta: dict[str, Any] = {
                "snippet": cand.snippet,
                "rank": cand.metadata.get("rank"),
                "score": cand.metadata.get("score"),
                "provider": cand.metadata.get("provider"),
            }
            forbidden_authority_keys = {
                "claims",
                "evidence",
                "verified_claims",
                "structured_claims",
                "is_verified",
                "confidence_score",
                "authority",
            }
            extra_meta = {
                k: v
                for k, v in cand.metadata.items()
                if k not in forbidden_authority_keys and k not in clean_discovery_meta
            }
            if extra_meta:
                clean_discovery_meta["extra"] = extra_meta

            src_in = ResearchSourceCreate(
                source_type=ResearchSourceType.WEB_SEARCH,
                title=(extracted_doc.title or cand.title).strip(),
                publisher=candidate_publisher,
                author=(extracted_doc.author or cand.author).strip() if (extracted_doc.author or cand.author) else None,
                url=candidate_url,
                content_excerpt=bounded_content,
                primary_source_status=candidate_primary_status,
                published_at=candidate_published_at,
                language=extracted_doc.language,
                region=extracted_doc.region,
                metadata={
                    "discovery": clean_discovery_meta,
                    "discovery_snippet": cand.snippet,
                    "content_provenance": extracted_doc.content_provenance,
                },
            )
            await research_service.add_source(
                session=session,
                request_id=request_id,
                source_in=src_in,
                authority_provider=authority_provider,
            )
            if norm_cand_url:
                already_seen_urls.add(norm_cand_url)
            if candidate_url:
                already_seen_urls.add(candidate_url)
            sources_added_this_round += 1

        # Extract claims for newly added sources
        all_sources_res = await session.execute(
            select(ResearchSource).where(ResearchSource.research_request_id == request_id)
        )
        all_sources = list(all_sources_res.scalars().all())
        eligible_sources = [s for s in all_sources if s.quality_score >= req.minimum_source_quality]

        curr_claims_res = await session.execute(
            select(ResearchClaim)
            .options(selectinload(ResearchClaim.evidence))
            .where(ResearchClaim.research_request_id == request_id)
        )
        curr_claims = list(curr_claims_res.scalars().all())
        existing_src_ids_with_claims = {
            e.source_id for c in curr_claims for e in (c.evidence or [])
        }

        for s in eligible_sources:
            if s.id not in existing_src_ids_with_claims:
                extracted = extract_deterministic_claims_from_source(
                    source_title=s.title,
                    source_excerpt=s.content_excerpt,
                    metadata=dict(s.metadata_ or {}),
                    source_type=s.source_type,
                    topic_keywords=req.topic_candidate.keywords if req.topic_candidate else None,
                )
                reconcile_source_extractions_into_claims(
                    session=session,
                    existing_claims=curr_claims,
                    extracted_items=extracted,
                    source=s,
                    channel_id=req.channel_id,
                    request_id=request_id,
                )
        await session.flush()

        # Canonically evaluate claims for verified coverage derivation
        verified_claims, _, _ = evaluate_canonical_claims(
            eligible_sources=eligible_sources,
            claims=curr_claims,
        )

        # Distinct coverage is strictly derived from VERIFIED claims only!
        if contract and verified_claims:
            vtexts = [c.claim_text for c in verified_claims]
            current_supported_families = extract_distinct_entities(
                vtexts,
                topic_title=topic_title,
                entity_type=contract.entity_type,
            )
        supported_after = len(current_supported_families)
        remaining = max(0, (promised_count or 0) - supported_after) if promised_count else 0

        # Calculate PLANNING-ONLY corroboration deficits for next search round
        sources_map_for_targets = {s.id: s for s in eligible_sources}
        current_corroboration_targets = build_corroboration_targets(
            claims=curr_claims,
            sources_map=sources_map_for_targets,
        )

        # Record round truth
        rounds_truth.append(
            ResearchCoverageRoundTruth(
                round_number=round_num,
                queries=round_queries_meta,
                candidates_discovered=len(raw_candidates),
                sources_accepted=sources_added_this_round,
                sources_rejected=len(rejections),
                rejection_reasons=rejections,
                supported_count_before=supported_before,
                supported_count_after=supported_after,
                supported_families_after=current_supported_families,
                remaining_count=remaining,
            )
        )

        # Check stopping conditions
        if contract and supported_after >= contract.promised_count:
            stop_reason = ResearchCoverageStopReason.COVERAGE_FULFILLED
            break

        if not contract and sources_added_this_round > 0 and len(eligible_sources) >= 3:
            stop_reason = ResearchCoverageStopReason.COVERAGE_FULFILLED
            break

        if current_source_count + sources_added_this_round >= effective_max_sources:
            stop_reason = ResearchCoverageStopReason.SEARCH_BUDGET_EXHAUSTED
            break

        if sources_added_this_round == 0:
            stop_reason = ResearchCoverageStopReason.NO_NEW_RELEVANT_SOURCES
            break

    # Resolve final stop reason if loop finished without explicit break
    if stop_reason is None:
        if contract and len(current_supported_families) >= (promised_count or 0):
            stop_reason = ResearchCoverageStopReason.COVERAGE_FULFILLED
        elif contract:
            stop_reason = ResearchCoverageStopReason.SEARCH_BUDGET_EXHAUSTED
        else:
            stop_reason = ResearchCoverageStopReason.COVERAGE_FULFILLED

    # 5. Compile final canonical ResearchBrief
    brief = await research_service.run_research(
        session=session,
        request_id=request_id,
        authority_provider=authority_provider,
    )

    # 6. STOP REASON / BRIEF CONSISTENCY GATE
    # Impossible state: stop_reason == COVERAGE_FULFILLED while brief.outcome != SUFFICIENT
    if (
        brief.outcome != ResearchOutcome.SUFFICIENT
        and stop_reason == ResearchCoverageStopReason.COVERAGE_FULFILLED
    ):
        logger.warning(
            "Stop reason was COVERAGE_FULFILLED but canonical brief outcome was %s; failing closed to NUMERIC_COVERAGE_NOT_FULFILLED",
            brief.outcome.value,
            request_id=str(request_id),
        )
        if contract:
            stop_reason = ResearchCoverageStopReason.NUMERIC_COVERAGE_NOT_FULFILLED
        else:
            stop_reason = ResearchCoverageStopReason.SEARCH_BUDGET_EXHAUSTED

    # 7. Update ResearchRequest with Coverage Expansion Observability
    coverage_truth_dicts = [r.model_dump() for r in rounds_truth]
    req_meta["coverage_expansion"] = {
        "acquisition_mode": acquisition_mode,
        "stop_reason": stop_reason.value,
        "initial_supported_count": initial_supported_count,
        "final_supported_count": len(current_supported_families),
        "final_supported_families": current_supported_families,
        "promised_count": promised_count,
        "entity_type": entity_type,
        "rounds_executed": len(rounds_truth),
        "rounds": coverage_truth_dicts,
    }
    req.metadata_ = req_meta
    await session.commit()

    logger.info(
        "Coverage-driven research completed",
        request_id=str(request_id),
        stop_reason=stop_reason.value,
        final_supported_count=len(current_supported_families),
        brief_outcome=brief.outcome.value,
    )

    return {
        "request_id": str(request_id),
        "acquisition_mode": acquisition_mode,
        "stop_reason": stop_reason.value,
        "rounds_executed": len(rounds_truth),
        "initial_supported_count": initial_supported_count,
        "final_supported_count": len(current_supported_families),
        "final_supported_families": current_supported_families,
        "brief": brief,
        "coverage_truth": coverage_truth_dicts,
        "manual_input_required": False,
    }
