"""Content Engine application service.

Coordinates content generation requests, immutable Channel DNA and ResearchBrief pinning,
hook generation & selection, outline planning, script drafting, statement classification,
citation provenance mapping, and local QA verification.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import desc, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from omega.application.content_provider import ContentGenerationProvider, TemplateContentProvider
from omega.application.content_qa import run_content_qa_checks
from omega.application.statement_validator import validate_and_classify_statement
from omega.domain.channel import ChannelState
from omega.domain.content import (
    ContentCitationResponse,
    ContentGenerationMode,
    ContentGenerationRequestCreate,
    ContentGenerationRequestResponse,
    ContentHookResponse,
    ContentIntentResponse,
    ContentOutcome,
    ContentOutlineResponse,
    ContentQAResultResponse,
    ContentRequestStatus,
    ContentRunPayload,
    OutlineSectionSchema,
    QAFindingSchema,
    ScriptQAStatus,
    ScriptSectionResponse,
    ScriptStatementResponse,
    ScriptVersionResponse,
    ScriptVersionSummaryResponse,
)
from omega.domain.topic import TopicStatus
from omega.infrastructure.models import (
    Channel,
    ChannelDNARevision,
    ContentCitation,
    ContentGenerationRequest,
    ContentHook,
    ContentHookCitation,
    ContentIntent,
    ContentOutline,
    ContentQAResult,
    MissionExecution,
    ResearchBrief,
    ScriptSection,
    ScriptStatement,
    ScriptVersion,
    TopicCandidate,
)
from omega.logging import get_logger

logger = get_logger("omega-content-service")


async def create_request(
    session: AsyncSession,
    channel_id: UUID,
    request_in: ContentGenerationRequestCreate,
    idempotency_key: str | None = None,
) -> ContentGenerationRequestResponse:
    """Create a new ContentGenerationRequest with pinned ResearchBrief and ChannelDNARevision."""
    if idempotency_key:
        stmt_existing = select(ContentGenerationRequest).where(
            ContentGenerationRequest.idempotency_key == idempotency_key
        )
        existing = (await session.execute(stmt_existing)).scalar_one_or_none()
        if existing:
            return ContentGenerationRequestResponse.model_validate(existing)
    # 1. Validate Channel
    chan_res = await session.execute(select(Channel).where(Channel.id == channel_id))
    channel = chan_res.scalar_one_or_none()
    if not channel:
        raise ValueError(f"Channel with ID '{channel_id}' does not exist.")
    if channel.state == ChannelState.ARCHIVED.value:
        raise ValueError(f"Cannot generate content for archived channel '{channel.name}'.")

    # 2. Validate TopicCandidate
    cand_res = await session.execute(
        select(TopicCandidate).where(
            TopicCandidate.id == request_in.topic_candidate_id,
            TopicCandidate.channel_id == channel_id,
        )
    )
    candidate = cand_res.scalar_one_or_none()
    if not candidate:
        raise ValueError(
            f"TopicCandidate with ID '{request_in.topic_candidate_id}' does not exist for this channel."
        )

    # 3. Validate ResearchBrief
    brief_res = await session.execute(
        select(ResearchBrief).where(
            ResearchBrief.id == request_in.research_brief_id,
            ResearchBrief.channel_id == channel_id,
            ResearchBrief.topic_candidate_id == request_in.topic_candidate_id,
        )
    )
    brief = brief_res.scalar_one_or_none()
    if not brief:
        raise ValueError(
            f"ResearchBrief with ID '{request_in.research_brief_id}' does not exist or does not match channel/topic."
        )

    # 4. Resolve Context Mode and Pin ChannelDNARevision
    mode: ContentGenerationMode
    channel_dna_revision_id: UUID

    if request_in.mission_execution_id:
        mode = ContentGenerationMode.MISSION_EXECUTION
        # In MISSION_EXECUTION, candidate must be SELECTED
        if candidate.status != TopicStatus.SELECTED.value:
            raise ValueError(
                f"Candidate in MISSION_EXECUTION mode must be in SELECTED state (current: {candidate.status})."
            )

        exec_res = await session.execute(
            select(MissionExecution).where(MissionExecution.id == request_in.mission_execution_id)
        )
        execution = exec_res.scalar_one_or_none()
        if not execution:
            raise ValueError(
                f"MissionExecution with ID '{request_in.mission_execution_id}' does not exist."
            )
        if not execution.channel_dna_revision_id:
            raise ValueError("MissionExecution is missing pinned ChannelDNARevision.")
        channel_dna_revision_id = execution.channel_dna_revision_id
    else:
        mode = ContentGenerationMode.INTERACTIVE
        # In INTERACTIVE, candidate must be EVALUATED, RECOMMENDED, or SELECTED
        if candidate.status not in (
            TopicStatus.EVALUATED.value,
            TopicStatus.RECOMMENDED.value,
            TopicStatus.SELECTED.value,
        ):
            raise ValueError(
                f"TopicCandidate must be EVALUATED, RECOMMENDED, or SELECTED (current: {candidate.status})."
            )

        # Pin latest active ChannelDNARevision
        rev_res = await session.execute(
            select(ChannelDNARevision)
            .where(ChannelDNARevision.channel_id == channel_id)
            .order_by(ChannelDNARevision.version.desc())
        )
        latest_rev = rev_res.scalars().first()
        if not latest_rev:
            raise ValueError("Channel has no ChannelDNARevision snapshots available.")
        channel_dna_revision_id = latest_rev.id

    content_req = ContentGenerationRequest(
        id=uuid.uuid4(),
        channel_id=channel_id,
        topic_candidate_id=request_in.topic_candidate_id,
        research_brief_id=request_in.research_brief_id,
        channel_dna_revision_id=channel_dna_revision_id,
        mission_execution_id=request_in.mission_execution_id,
        idempotency_key=idempotency_key,
        mode=mode.value,
        status=ContentRequestStatus.DRAFT.value,
        content_type=request_in.content_type.value,
        target_duration_seconds=request_in.target_duration_seconds,
        target_word_count=request_in.target_word_count,
        language=request_in.language,
        region=request_in.region,
        creative_direction=request_in.creative_direction,
    )
    try:
        session.add(content_req)
        await session.commit()
        await session.refresh(content_req)
    except IntegrityError:
        await session.rollback()
        if idempotency_key:
            stmt_existing = select(ContentGenerationRequest).where(
                ContentGenerationRequest.idempotency_key == idempotency_key
            )
            existing = (await session.execute(stmt_existing)).scalar_one_or_none()
            if existing:
                return ContentGenerationRequestResponse.model_validate(existing)
        raise

    logger.info(
        "ContentGenerationRequest created",
        request_id=str(content_req.id),
        channel_id=str(channel_id),
        pinned_brief_id=str(content_req.research_brief_id),
        pinned_dna_rev_id=str(content_req.channel_dna_revision_id),
    )
    return ContentGenerationRequestResponse.model_validate(content_req)


async def list_requests(
    session: AsyncSession,
    channel_id: UUID,
    status: str | None = None,
) -> list[ContentGenerationRequestResponse]:
    """List content generation requests for a channel."""
    stmt = select(ContentGenerationRequest).where(ContentGenerationRequest.channel_id == channel_id)
    if status:
        stmt = stmt.where(ContentGenerationRequest.status == status)
    stmt = stmt.order_by(desc(ContentGenerationRequest.created_at))
    res = await session.execute(stmt)
    return [ContentGenerationRequestResponse.model_validate(r) for r in res.scalars().all()]


async def get_request(
    session: AsyncSession,
    channel_id: UUID,
    request_id: UUID,
) -> ContentGenerationRequestResponse | None:
    """Get a single ContentGenerationRequest."""
    stmt = select(ContentGenerationRequest).where(
        ContentGenerationRequest.id == request_id,
        ContentGenerationRequest.channel_id == channel_id,
    )
    res = await session.execute(stmt)
    req = res.scalar_one_or_none()
    return ContentGenerationRequestResponse.model_validate(req) if req else None


async def cancel_request(
    session: AsyncSession,
    channel_id: UUID,
    request_id: UUID,
) -> ContentGenerationRequestResponse:
    """Cancel an in-progress or draft content generation request."""
    stmt = select(ContentGenerationRequest).where(
        ContentGenerationRequest.id == request_id,
        ContentGenerationRequest.channel_id == channel_id,
    )
    res = await session.execute(stmt)
    req = res.scalar_one_or_none()
    if not req:
        raise ValueError(f"ContentGenerationRequest '{request_id}' not found.")
    if req.status in (ContentRequestStatus.SUCCEEDED.value, ContentRequestStatus.CANCELLED.value):
        raise ValueError(f"Cannot cancel request in status '{req.status}'.")

    req.status = ContentRequestStatus.CANCELLED.value
    await session.commit()
    await session.refresh(req)
    return ContentGenerationRequestResponse.model_validate(req)


async def generate_content(
    session: AsyncSession,
    channel_id: UUID,
    request_id: UUID,
    payload: ContentRunPayload | None = None,
    provider: ContentGenerationProvider | None = None,
) -> ScriptVersionResponse:
    """Execute full content generation pipeline and persist versioned script."""
    if provider is None:
        provider = TemplateContentProvider()

    # Step A: Load required context (Channel, Topic, Pinned DNA Revision, Pinned Brief)
    req_res = await session.execute(
        select(ContentGenerationRequest)
        .where(
            ContentGenerationRequest.id == request_id,
            ContentGenerationRequest.channel_id == channel_id,
        )
        .options(
            selectinload(ContentGenerationRequest.topic_candidate),
            selectinload(ContentGenerationRequest.channel_dna_revision),
            selectinload(ContentGenerationRequest.research_brief),
        )
    )
    req = req_res.scalar_one_or_none()
    if not req:
        raise ValueError(f"ContentGenerationRequest '{request_id}' not found.")

    if req.status == ContentRequestStatus.RUNNING.value:
        raise ValueError("Content generation request is already running.")

    # Step B: Logically separated provider execution (Outside DB row lock)
    topic_title = req.topic_candidate.title
    topic_summary = req.topic_candidate.summary
    dna_dict = req.channel_dna_revision.snapshot
    brief = req.research_brief

    brief_dict = {
        "id": str(brief.id),
        "title": brief.title,
        "summary": brief.summary,
        "verified_claims": brief.verified_claims,
        "uncertain_claims": brief.uncertain_claims,
        "contradictions": brief.contradictions,
    }

    intent_data = provider.generate_intent(
        topic_title=topic_title,
        topic_summary=topic_summary,
        brief_dict=brief_dict,
        dna_dict=dna_dict,
        creative_direction=req.creative_direction,
    )

    hooks_data = provider.generate_hooks(
        topic_title=topic_title,
        brief_dict=brief_dict,
        dna_dict=dna_dict,
        intent_dict=intent_data,
    )

    selected_hook = next(
        (h for h in hooks_data if h.get("selected")), hooks_data[0] if hooks_data else None
    )

    outline_data = provider.generate_outline(
        topic_title=topic_title,
        brief_dict=brief_dict,
        dna_dict=dna_dict,
        intent_dict=intent_data,
        selected_hook=selected_hook,
        target_duration_seconds=req.target_duration_seconds,
    )

    raw_script_data = provider.generate_script(
        topic_title=topic_title,
        brief_dict=brief_dict,
        dna_dict=dna_dict,
        intent_dict=intent_data,
        selected_hook=selected_hook or {},
        outline_dict=outline_data,
        target_duration_seconds=req.target_duration_seconds,
    )

    # Step C: Untrusted Provider Classification & Citation Validation
    verified_claim_ids = {
        str(c.get("claim_id")) for c in brief.verified_claims if c.get("claim_id")
    }
    conflict_claim_ids = {
        str(c.get("claim_id"))
        for c in brief.contradictions
        if c.get("severity") in ("HIGH", "CRITICAL") and c.get("claim_id")
    }

    validated_sections: list[dict[str, Any]] = []
    for sec in raw_script_data.get("sections", []):
        val_statements: list[dict[str, Any]] = []
        for stmt in sec.get("statements", []):
            st_text = stmt.get("statement_text", "")
            s_type_sug = stmt.get("statement_type", "CREATIVE")
            cits = stmt.get("citations", [])

            eff_type, _policy, _reasons = validate_and_classify_statement(
                statement_text=st_text,
                suggested_type=s_type_sug,
                citations=cits,
                pinned_brief_id=brief.id,
                brief_verified_claim_ids=verified_claim_ids,
                brief_conflict_claim_ids=conflict_claim_ids,
            )

            val_statements.append(
                {
                    "statement_order": stmt.get("statement_order", 1),
                    "statement_text": st_text,
                    "statement_type": eff_type.value,
                    "qualification_note": stmt.get("qualification_note"),
                    "citations": cits,
                }
            )

        validated_sections.append(
            {
                "section_order": sec.get("section_order", 1),
                "heading": sec.get("heading", ""),
                "narration_text": sec.get("narration_text", ""),
                "estimated_duration_seconds": sec.get("estimated_duration_seconds", 0),
                "transition_text": sec.get("transition_text"),
                "retention_beat": sec.get("retention_beat"),
                "statements": val_statements,
            }
        )

    raw_script_data["sections"] = validated_sections

    # Step D: Run Local QA Checks
    qa_status, findings = run_content_qa_checks(
        script_data=raw_script_data,
        target_duration_seconds=req.target_duration_seconds,
        dna_dict=dna_dict,
        brief_dict=brief_dict,
    )

    # Step E: Acquire Locks and Persist in Atomic Transaction
    # Lock request for update
    lock_req_res = await session.execute(
        select(ContentGenerationRequest)
        .where(ContentGenerationRequest.id == request_id)
        .with_for_update()
    )
    locked_req = lock_req_res.scalar_one()
    locked_req.status = ContentRequestStatus.RUNNING.value
    locked_req.started_at = datetime.now(UTC)

    # 1. Persist Intent (if not already created)
    intent_res = await session.execute(
        select(ContentIntent).where(ContentIntent.content_request_id == request_id)
    )
    existing_intent = intent_res.scalar_one_or_none()
    if not existing_intent:
        content_intent = ContentIntent(
            id=uuid.uuid4(),
            content_request_id=request_id,
            primary_goal=intent_data["primary_goal"],
            audience_intent=intent_data["audience_intent"],
            viewer_promise=intent_data["viewer_promise"],
            central_question=intent_data["central_question"],
            core_takeaway=intent_data["core_takeaway"],
            tone=intent_data["tone"],
            pace=intent_data["pace"],
            complexity=intent_data["complexity"],
            desired_emotion=intent_data["desired_emotion"],
            call_to_action_type=intent_data["call_to_action_type"],
        )
        session.add(content_intent)

    # 2. Persist Hooks and Hook Citations (if not already created)
    hooks_res = await session.execute(
        select(ContentHook).where(ContentHook.content_request_id == request_id)
    )
    existing_hooks = hooks_res.scalars().all()
    hook_map: dict[int, ContentHook] = {}

    if not existing_hooks:
        for h_data in hooks_data:
            c_hook = ContentHook(
                id=uuid.uuid4(),
                content_request_id=request_id,
                hook_variant_index=h_data["hook_variant_index"],
                text=h_data["text"],
                hook_type=h_data["hook_type"],
                score=h_data.get("score", 0.0),
                reason_codes=h_data.get("reason_codes", []),
                selected=h_data.get("selected", False),
            )
            session.add(c_hook)
            hook_map[h_data["hook_variant_index"]] = c_hook

            for cit in h_data.get("citations", []):
                h_cit = ContentHookCitation(
                    id=uuid.uuid4(),
                    hook_id=c_hook.id,
                    research_brief_id=brief.id,
                    claim_id=uuid.UUID(str(cit["claim_id"])),
                    evidence_id=uuid.UUID(str(cit["evidence_id"])),
                    source_id=uuid.UUID(str(cit["source_id"])),
                )
                session.add(h_cit)
    else:
        for eh in existing_hooks:
            hook_map[eh.hook_variant_index] = eh

    # 3. Persist Outline (if not already created)
    outline_res = await session.execute(
        select(ContentOutline).where(ContentOutline.content_request_id == request_id)
    )
    existing_outline = outline_res.scalar_one_or_none()
    if not existing_outline:
        content_outline = ContentOutline(
            id=uuid.uuid4(),
            content_request_id=request_id,
            opening_description=outline_data["opening_description"],
            sections=outline_data["sections"],
            closing_description=outline_data["closing_description"],
        )
        session.add(content_outline)

    # 4. Fetch existing scripts to determine version N+1 and mark previous is_current = False
    scripts_res = await session.execute(
        select(ScriptVersion)
        .where(ScriptVersion.content_request_id == request_id)
        .order_by(desc(ScriptVersion.version))
        .with_for_update()
    )
    existing_scripts = scripts_res.scalars().all()

    new_version_num = 1
    prev_script_id: UUID | None = None
    if existing_scripts:
        new_version_num = existing_scripts[0].version + 1
        for s in existing_scripts:
            if s.is_current:
                s.is_current = False
                prev_script_id = s.id

    selected_hook_record = next((h for h in hook_map.values() if h.selected), None)
    hook_id = selected_hook_record.id if selected_hook_record else None

    # 5. Insert new ScriptVersion
    script_version = ScriptVersion(
        id=uuid.uuid4(),
        content_request_id=request_id,
        version=new_version_num,
        is_current=True,
        supersedes_script_id=prev_script_id,
        title=raw_script_data["title"],
        hook_id=hook_id,
        hook_text=raw_script_data["hook_text"],
        closing_text=raw_script_data["closing_text"],
        cta_text=raw_script_data["cta_text"],
        estimated_word_count=raw_script_data["estimated_word_count"],
        estimated_duration_seconds=raw_script_data["estimated_duration_seconds"],
        qa_status=qa_status.value,
        style_snapshot=raw_script_data["style_snapshot"],
    )
    session.add(script_version)
    await session.flush()

    # 6. Insert ScriptSections, ScriptStatements, and ContentCitations
    for sec_data in validated_sections:
        script_sec = ScriptSection(
            id=uuid.uuid4(),
            script_version_id=script_version.id,
            section_order=sec_data["section_order"],
            heading=sec_data["heading"],
            narration_text=sec_data["narration_text"],
            estimated_duration_seconds=sec_data["estimated_duration_seconds"],
            transition_text=sec_data.get("transition_text"),
            retention_beat=sec_data.get("retention_beat"),
        )
        session.add(script_sec)
        await session.flush()

        for stmt_data in sec_data.get("statements", []):
            script_stmt = ScriptStatement(
                id=uuid.uuid4(),
                script_section_id=script_sec.id,
                statement_order=stmt_data["statement_order"],
                statement_text=stmt_data["statement_text"],
                statement_type=stmt_data["statement_type"],
                qualification_note=stmt_data.get("qualification_note"),
            )
            session.add(script_stmt)
            await session.flush()

            for cit_data in stmt_data.get("citations", []):
                # Enforce pinned brief citation invariant
                c_brief_id = str(cit_data["research_brief_id"])
                if c_brief_id != str(brief.id):
                    raise ValueError(
                        f"Cross-brief citation rejected: Citation brief '{c_brief_id}' != Pinned brief '{brief.id}'"
                    )

                citation = ContentCitation(
                    id=uuid.uuid4(),
                    script_statement_id=script_stmt.id,
                    research_brief_id=brief.id,
                    claim_id=uuid.UUID(str(cit_data["claim_id"])),
                    evidence_id=uuid.UUID(str(cit_data["evidence_id"])),
                    source_id=uuid.UUID(str(cit_data["source_id"])),
                )
                session.add(citation)

    # 7. Persist QA Result
    qa_result = ContentQAResult(
        id=uuid.uuid4(),
        script_version_id=script_version.id,
        status=qa_status.value,
        findings=findings,
    )
    session.add(qa_result)

    # 8. Update request status & outcome
    locked_req.status = ContentRequestStatus.SUCCEEDED.value
    locked_req.outcome = (
        ContentOutcome.BLOCKED.value
        if qa_status == ScriptQAStatus.BLOCKED
        else ContentOutcome.GENERATED.value
    )
    locked_req.completed_at = datetime.now(UTC)

    await session.commit()

    logger.info(
        "Content generation completed",
        request_id=str(request_id),
        version=new_version_num,
        qa_status=qa_status.value,
        outcome=locked_req.outcome,
    )

    # Return full script response
    return await get_script(session, channel_id, request_id, new_version_num)  # type: ignore[return-value]


async def regenerate_script(
    session: AsyncSession,
    channel_id: UUID,
    request_id: UUID,
    payload: ContentRunPayload | None = None,
    provider: ContentGenerationProvider | None = None,
) -> ScriptVersionResponse:
    """Explicitly regenerate a new script revision (vN+1) using pinned DNA and ResearchBrief."""
    return await generate_content(
        session=session,
        channel_id=channel_id,
        request_id=request_id,
        payload=payload,
        provider=provider,
    )


async def get_intent(
    session: AsyncSession,
    channel_id: UUID,
    request_id: UUID,
) -> ContentIntentResponse | None:
    """Retrieve ContentIntent for a request."""
    req_res = await session.execute(
        select(ContentGenerationRequest).where(
            ContentGenerationRequest.id == request_id,
            ContentGenerationRequest.channel_id == channel_id,
        )
    )
    if not req_res.scalar_one_or_none():
        return None

    res = await session.execute(
        select(ContentIntent).where(ContentIntent.content_request_id == request_id)
    )
    intent = res.scalar_one_or_none()
    return ContentIntentResponse.model_validate(intent) if intent else None


async def list_hooks(
    session: AsyncSession,
    channel_id: UUID,
    request_id: UUID,
) -> list[ContentHookResponse]:
    """List hook variants for a request."""
    req_res = await session.execute(
        select(ContentGenerationRequest).where(
            ContentGenerationRequest.id == request_id,
            ContentGenerationRequest.channel_id == channel_id,
        )
    )
    if not req_res.scalar_one_or_none():
        return []

    res = await session.execute(
        select(ContentHook)
        .where(ContentHook.content_request_id == request_id)
        .options(selectinload(ContentHook.citations))
        .order_by(ContentHook.hook_variant_index.asc())
    )
    return [ContentHookResponse.model_validate(h) for h in res.scalars().all()]


async def select_hook(
    session: AsyncSession,
    channel_id: UUID,
    request_id: UUID,
    hook_id: UUID,
    selected: bool = True,
) -> ContentHookResponse:
    """Select or deselect a hook variant, enforcing the single-selected-hook invariant under row lock."""
    req_res = await session.execute(
        select(ContentGenerationRequest).where(
            ContentGenerationRequest.id == request_id,
            ContentGenerationRequest.channel_id == channel_id,
        )
    )
    if not req_res.scalar_one_or_none():
        raise ValueError(
            f"ContentGenerationRequest '{request_id}' not found for channel '{channel_id}'."
        )

    # Lock all hooks for this request
    hooks_res = await session.execute(
        select(ContentHook).where(ContentHook.content_request_id == request_id).with_for_update()
    )
    all_hooks = list(hooks_res.scalars().all())
    target_hook = next((h for h in all_hooks if h.id == hook_id), None)
    if not target_hook:
        raise ValueError(f"ContentHook '{hook_id}' not found for request '{request_id}'.")

    if selected:
        # First unselect all hooks for this request
        await session.execute(
            update(ContentHook)
            .where(ContentHook.content_request_id == request_id)
            .values(selected=False)
        )
        await session.flush()

        # Now select the target hook
        await session.execute(
            update(ContentHook).where(ContentHook.id == hook_id).values(selected=True)
        )
    else:
        await session.execute(
            update(ContentHook).where(ContentHook.id == hook_id).values(selected=False)
        )

    await session.commit()

    # Reload with citations
    reloaded = await session.execute(
        select(ContentHook)
        .where(ContentHook.id == hook_id)
        .options(selectinload(ContentHook.citations))
    )
    return ContentHookResponse.model_validate(reloaded.scalar_one())


async def get_outline(
    session: AsyncSession,
    channel_id: UUID,
    request_id: UUID,
) -> ContentOutlineResponse | None:
    """Retrieve ContentOutline for a request."""
    req_res = await session.execute(
        select(ContentGenerationRequest).where(
            ContentGenerationRequest.id == request_id,
            ContentGenerationRequest.channel_id == channel_id,
        )
    )
    if not req_res.scalar_one_or_none():
        return None

    res = await session.execute(
        select(ContentOutline).where(ContentOutline.content_request_id == request_id)
    )
    outline = res.scalar_one_or_none()
    if not outline:
        return None

    return ContentOutlineResponse(
        id=outline.id,
        content_request_id=outline.content_request_id,
        opening_description=outline.opening_description,
        sections=[OutlineSectionSchema(**sec) for sec in outline.sections],
        closing_description=outline.closing_description,
        created_at=outline.created_at,
    )


async def list_scripts(
    session: AsyncSession,
    channel_id: UUID,
    request_id: UUID,
) -> list[ScriptVersionSummaryResponse]:
    """List summary of all script revisions for a request."""
    req_res = await session.execute(
        select(ContentGenerationRequest).where(
            ContentGenerationRequest.id == request_id,
            ContentGenerationRequest.channel_id == channel_id,
        )
    )
    if not req_res.scalar_one_or_none():
        return []

    res = await session.execute(
        select(ScriptVersion)
        .where(ScriptVersion.content_request_id == request_id)
        .order_by(desc(ScriptVersion.version))
    )
    return [ScriptVersionSummaryResponse.model_validate(s) for s in res.scalars().all()]


async def get_script(
    session: AsyncSession,
    channel_id: UUID,
    request_id: UUID,
    version: int | None = None,
) -> ScriptVersionResponse | None:
    """Retrieve a specific script revision or current active script with full sections, statements, and citations."""
    req_res = await session.execute(
        select(ContentGenerationRequest).where(
            ContentGenerationRequest.id == request_id,
            ContentGenerationRequest.channel_id == channel_id,
        )
    )
    if not req_res.scalar_one_or_none():
        return None

    stmt = select(ScriptVersion).where(ScriptVersion.content_request_id == request_id)
    if version is not None:
        stmt = stmt.where(ScriptVersion.version == version)
    else:
        stmt = stmt.where(ScriptVersion.is_current == True)  # noqa: E712

    stmt = stmt.options(
        selectinload(ScriptVersion.sections)
        .selectinload(ScriptSection.statements)
        .selectinload(ScriptStatement.citations)
    )

    res = await session.execute(stmt)
    script = res.scalar_one_or_none()
    if not script:
        return None

    sec_responses: list[ScriptSectionResponse] = []
    for sec in script.sections:
        stmt_responses: list[ScriptStatementResponse] = []
        for st in sec.statements:
            cit_responses = [
                ContentCitationResponse(
                    id=c.id,
                    script_statement_id=c.script_statement_id,
                    research_brief_id=c.research_brief_id,
                    claim_id=c.claim_id,
                    evidence_id=c.evidence_id,
                    source_id=c.source_id,
                    created_at=c.created_at,
                )
                for c in st.citations
            ]
            stmt_responses.append(
                ScriptStatementResponse(
                    id=st.id,
                    script_section_id=st.script_section_id,
                    statement_order=st.statement_order,
                    statement_text=st.statement_text,
                    statement_type=st.statement_type,  # type: ignore[arg-type]
                    qualification_note=st.qualification_note,
                    citations=cit_responses,
                    created_at=st.created_at,
                )
            )

        sec_responses.append(
            ScriptSectionResponse(
                id=sec.id,
                script_version_id=sec.script_version_id,
                section_order=sec.section_order,
                heading=sec.heading,
                narration_text=sec.narration_text,
                estimated_duration_seconds=sec.estimated_duration_seconds,
                transition_text=sec.transition_text,
                retention_beat=sec.retention_beat,
                statements=stmt_responses,
                created_at=sec.created_at,
            )
        )

    return ScriptVersionResponse(
        id=script.id,
        content_request_id=script.content_request_id,
        version=script.version,
        is_current=script.is_current,
        supersedes_script_id=script.supersedes_script_id,
        title=script.title,
        hook_id=script.hook_id,
        hook_text=script.hook_text,
        closing_text=script.closing_text,
        cta_text=script.cta_text,
        estimated_word_count=script.estimated_word_count,
        estimated_duration_seconds=script.estimated_duration_seconds,
        qa_status=script.qa_status,  # type: ignore[arg-type]
        style_snapshot=script.style_snapshot,
        sections=sec_responses,
        created_at=script.created_at,
    )


async def get_qa_result(
    session: AsyncSession,
    channel_id: UUID,
    request_id: UUID,
    version: int | None = None,
) -> ContentQAResultResponse | None:
    """Retrieve ContentQAResult for a script version."""
    script = await get_script(session, channel_id, request_id, version)
    if not script:
        return None

    res = await session.execute(
        select(ContentQAResult).where(ContentQAResult.script_version_id == script.id)
    )
    qa = res.scalar_one_or_none()
    if not qa:
        return None

    return ContentQAResultResponse(
        id=qa.id,
        script_version_id=qa.script_version_id,
        status=qa.status,  # type: ignore[arg-type]
        findings=[QAFindingSchema(**f) for f in qa.findings],
        executed_at=qa.executed_at,
    )


async def run_qa(
    session: AsyncSession,
    channel_id: UUID,
    request_id: UUID,
    version: int | None = None,
) -> ContentQAResultResponse:
    """Re-run QA checks on a specific script version."""
    req_res = await session.execute(
        select(ContentGenerationRequest)
        .where(
            ContentGenerationRequest.id == request_id,
            ContentGenerationRequest.channel_id == channel_id,
        )
        .options(
            selectinload(ContentGenerationRequest.channel_dna_revision),
            selectinload(ContentGenerationRequest.research_brief),
        )
    )
    req = req_res.scalar_one_or_none()
    if not req:
        raise ValueError(f"ContentGenerationRequest '{request_id}' not found.")

    script = await get_script(session, channel_id, request_id, version)
    if not script:
        raise ValueError("Script version not found.")

    dna_dict = req.channel_dna_revision.snapshot
    brief = req.research_brief
    brief_dict = {
        "id": str(brief.id),
        "verified_claims": brief.verified_claims,
        "contradictions": brief.contradictions,
    }

    script_data = {
        "hook_text": script.hook_text,
        "closing_text": script.closing_text,
        "cta_text": script.cta_text,
        "estimated_duration_seconds": script.estimated_duration_seconds,
        "sections": [
            {
                "heading": s.heading,
                "narration_text": s.narration_text,
                "statements": [
                    {
                        "statement_order": st.statement_order,
                        "statement_text": st.statement_text,
                        "statement_type": st.statement_type.value,
                        "citations": [{"claim_id": str(c.claim_id)} for c in st.citations],
                    }
                    for st in s.statements
                ],
            }
            for s in script.sections
        ],
    }

    qa_status, findings = run_content_qa_checks(
        script_data=script_data,
        target_duration_seconds=req.target_duration_seconds,
        dna_dict=dna_dict,
        brief_dict=brief_dict,
    )

    # Update or insert QA result
    qa_res = await session.execute(
        select(ContentQAResult).where(ContentQAResult.script_version_id == script.id)
    )
    qa = qa_res.scalar_one_or_none()
    if qa:
        qa.status = qa_status.value
        qa.findings = findings
        qa.executed_at = datetime.now(UTC)
    else:
        qa = ContentQAResult(
            id=uuid.uuid4(),
            script_version_id=script.id,
            status=qa_status.value,
            findings=findings,
        )
        session.add(qa)

    # Update script version qa_status
    sv_res = await session.execute(select(ScriptVersion).where(ScriptVersion.id == script.id))
    sv = sv_res.scalar_one()
    sv.qa_status = qa_status.value

    # Update request outcome if this is current version
    if sv.is_current:
        req.outcome = (
            ContentOutcome.BLOCKED.value
            if qa_status == ScriptQAStatus.BLOCKED
            else ContentOutcome.GENERATED.value
        )

    await session.commit()
    await session.refresh(qa)

    return ContentQAResultResponse(
        id=qa.id,
        script_version_id=qa.script_version_id,
        status=qa.status,  # type: ignore[arg-type]
        findings=[QAFindingSchema(**f) for f in qa.findings],
        executed_at=qa.executed_at,
    )
