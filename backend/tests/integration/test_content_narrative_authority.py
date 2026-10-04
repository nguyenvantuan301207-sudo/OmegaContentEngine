"""New scripts require PostgreSQL narrative authority; historical scripts remain readable."""

import copy
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from omega.application import content_service
from omega.application.content_provider import TemplateContentProvider
from omega.application.narrative_plan_service import (
    NarrativePlanService,
    PostgresNarrativePlanRepository,
)
from omega.application.narrative_planning_service import NarrativePlanningService
from omega.application.storyboard_engine import StoryboardEngine
from omega.domain.content import ContentRunPayload
from omega.domain.narrative_plan import NarrativePlanStatus
from omega.infrastructure.models import (
    ContentGenerationRequest,
    MissionExecution,
    NarrativePlan,
    NarrativeSection,
    ResearchBrief,
    ScriptVersion,
)
from omega.main import app
from tests.research_fixtures import create_research_case, seed_sufficient_research


@pytest.fixture
async def narrative_case(db_session):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel, topic, research = await create_research_case(client)
        await seed_sufficient_research(client, channel, research)
        brief = (await client.post(f"/api/v1/channels/{channel}/research/{research}/run")).json()
        response = await client.post(
            f"/api/v1/channels/{channel}/content",
            json={
                "topic_candidate_id": topic,
                "research_brief_id": brief["id"],
            },
        )
        assert response.status_code == 201, response.text
        yield client, UUID(channel), UUID(response.json()["id"]), brief


@pytest.mark.asyncio
async def test_generation_persists_plan_before_script_and_reuses_exact_revision(
    db_session, narrative_case
):
    client, channel, request, brief = narrative_case
    provider = TemplateContentProvider()
    generate_script = provider.generate_script
    planning = NarrativePlanningService.plan_narrative_for_request
    events = []

    def checked_planning(service, **kwargs):
        result = planning(service, **kwargs)
        assert service.plan_service.repository.get(result[0].id) is not None
        events.append("persisted_plan")
        return result

    def checked_script(**kwargs):
        outline = kwargs["outline_dict"]
        assert outline["narrative_plan_id"]
        assert events == ["persisted_plan"]
        return generate_script(**kwargs)

    with (
        patch.object(NarrativePlanningService, "plan_narrative_for_request", checked_planning),
        patch.object(
            provider, "generate_outline", side_effect=AssertionError("legacy fallback used")
        ),
        patch.object(provider, "generate_script", side_effect=checked_script),
    ):
        script = await content_service.generate_content(
            db_session, channel, request, provider=provider
        )
    response = await client.get(f"/api/v1/channels/{channel}/content/{request}/narrative-plan")
    assert response.status_code == 200
    plan = response.json()
    assert str(script.narrative_plan_id) == plan["id"]
    assert script.narrative_plan_version == plan["version"] == 1
    storyboard = StoryboardEngine().generate_storyboard(script.model_dump(mode="json"))
    assert storyboard.narrative_plan_id == plan["id"]
    assert storyboard.narrative_plan_version == 1
    assert plan["research_brief_id"] == brief["id"]
    assert plan["metadata"]["narrative_qa"]["status"] == "PASS"
    assert [s["section_order"] for s in plan["sections"]] == list(
        range(1, len(plan["sections"]) + 1)
    )
    valid_refs = {
        (str(c["claim_id"]), str(e["evidence_id"]), str(e["source_id"]))
        for c in brief["verified_claims"]
        for e in c["citations"]
    }
    refs = [g for s in plan["sections"] for g in s["grounding_references"]]
    assert refs
    assert all(
        g["research_brief_id"] == brief["id"]
        and (g["claim_id"], g["evidence_id"], g["source_id"]) in valid_refs
        for g in refs
    )
    for _ in range(2):
        regenerated = await client.post(f"/api/v1/channels/{channel}/content/{request}/regenerate")
        assert regenerated.status_code == 200, regenerated.text
        assert regenerated.json()["narrative_plan_id"] == plan["id"]
    assert await db_session.scalar(select(func.count()).select_from(NarrativePlan)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect",
    ["REJECTED", "DRAFT", "noncurrent", "structure", "foreign_grounding", "unverified_evidence"],
)
async def test_invalid_authority_blocks_new_script(db_session, narrative_case, defect):
    client, channel, request, _ = narrative_case
    initial = await client.post(f"/api/v1/channels/{channel}/content/{request}/generate")
    assert initial.status_code == 200, initial.text
    plan_id = UUID(initial.json()["narrative_plan_id"])
    plan = await db_session.scalar(
        select(NarrativePlan)
        .where(NarrativePlan.id == plan_id)
        .options(
            selectinload(NarrativePlan.sections).selectinload(NarrativeSection.grounding_citations)
        )
    )
    if defect in ("REJECTED", "DRAFT"):
        plan.status = defect
    elif defect == "noncurrent":
        plan.is_current = False
    elif defect == "structure":
        plan.sections[0].role = "CLOSING"
    else:
        ref_section = next(s for s in plan.sections if s.grounding_citations)
        if defect == "foreign_grounding":
            # A valid FK with the wrong pinned authority: another immutable brief revision.
            req = await db_session.get(ContentGenerationRequest, request)
            brief = await db_session.get(ResearchBrief, req.research_brief_id)
            new_brief = await client.post(
                f"/api/v1/channels/{channel}/research/{brief.research_request_id}/run"
            )
            ref_section.grounding_citations[0].research_brief_id = UUID(new_brief.json()["id"])
        else:
            ref_section.grounding_citations[0].claim_id = None
    await db_session.commit()
    response = await client.post(f"/api/v1/channels/{channel}/content/{request}/regenerate")
    assert response.status_code == 400, response.text
    assert await db_session.scalar(select(func.count()).select_from(ScriptVersion)) == 1


@pytest.mark.asyncio
async def test_explicit_revision_preserves_v1_and_new_script_pins_v2(db_session, narrative_case):
    client, channel, request, _ = narrative_case
    initial = (await client.post(f"/api/v1/channels/{channel}/content/{request}/generate")).json()
    plan_id = UUID(initial["narrative_plan_id"])

    def revise(sync):
        service = NarrativePlanService(repository=PostgresNarrativePlanRepository(session=sync))
        old = service.get_plan(plan_id)
        before = copy.deepcopy([s.model_dump(mode="json") for s in old.sections])
        sections = [s.model_copy(deep=True) for s in old.sections]
        sections[
            2
        ].objective = "Explain the original records through a revised narrative objective."
        body_duration = sum(s.target_duration_seconds for s in sections[1:])
        revised_body_duration = 960 - sections[0].target_duration_seconds
        for section in sections[1:]:
            section.target_duration_seconds = (
                section.target_duration_seconds * revised_body_duration // body_duration
            )
        sections[-1].target_duration_seconds += 960 - sum(
            s.target_duration_seconds for s in sections
        )
        new, validation = service.create_revision(
            plan_id, sections, new_target_duration_seconds=960, notes="Explicit operator revision"
        )
        assert validation.is_valid
        return before, new.id, old.target_duration_seconds

    old_sections, new_id, old_duration = await db_session.run_sync(revise)
    await db_session.commit()
    stale = await client.post(
        f"/api/v1/channels/{channel}/content/{request}/regenerate",
        json={"narrative_plan_id": str(plan_id)},
    )
    assert stale.status_code == 400
    prose_targets = []
    qa_targets = []
    prose = TemplateContentProvider.generate_script
    qa_checks = content_service.run_content_qa_checks

    def checked_prose(provider, **kwargs):
        prose_targets.append(kwargs["target_duration_seconds"])
        return prose(provider, **kwargs)

    def checked_qa(**kwargs):
        qa_targets.append(kwargs["target_duration_seconds"])
        return qa_checks(**kwargs)

    with (
        patch.object(TemplateContentProvider, "generate_script", checked_prose),
        patch.object(content_service, "run_content_qa_checks", checked_qa),
    ):
        response = await client.post(f"/api/v1/channels/{channel}/content/{request}/regenerate")
        assert response.status_code == 200, response.text
        assert (
            await client.post(f"/api/v1/channels/{channel}/content/{request}/scripts/2/qa")
        ).status_code == 200
        assert (
            await client.post(f"/api/v1/channels/{channel}/content/{request}/scripts/1/qa")
        ).status_code == 200
    assert prose_targets == [960]
    assert qa_targets == [960, 960, old_duration]
    assert sum(s["estimated_duration_seconds"] for s in response.json()["sections"]) == 960
    assert response.status_code == 200, response.text
    assert response.json()["narrative_plan_id"] == str(new_id)
    assert response.json()["narrative_plan_version"] == 2
    old = await db_session.run_sync(
        lambda sync: PostgresNarrativePlanRepository(session=sync).get(plan_id)
    )
    new = await db_session.run_sync(
        lambda sync: PostgresNarrativePlanRepository(session=sync).get(new_id)
    )
    assert [s.model_dump(mode="json") for s in old.sections] == old_sections
    assert new.supersedes_plan_id == old.id
    assert old.status == NarrativePlanStatus.SUPERSEDED
    history = (await client.get(f"/api/v1/channels/{channel}/content/{request}/scripts/1")).json()
    assert history["narrative_plan_id"] == str(plan_id)


@pytest.mark.asyncio
async def test_historical_null_plan_script_readable_and_new_script_requires_plan(
    db_session, narrative_case
):
    client, channel, request, _ = narrative_case
    generated = await client.post(f"/api/v1/channels/{channel}/content/{request}/generate")
    assert generated.status_code == 200, generated.text
    historical = await db_session.get(ScriptVersion, UUID(generated.json()["id"]))
    historical.narrative_plan_id = (
        None  # Simulate a pre-P21 database row, never a new generation path.
    )
    await db_session.commit()
    read = await client.get(f"/api/v1/channels/{channel}/content/{request}/scripts/1")
    assert read.status_code == 200 and read.json()["narrative_plan_id"] is None
    regenerated = await client.post(f"/api/v1/channels/{channel}/content/{request}/regenerate")
    assert regenerated.status_code == 200, regenerated.text
    assert regenerated.json()["narrative_plan_id"]


@pytest.mark.asyncio
async def test_missing_explicit_plan_never_falls_back(db_session, narrative_case):
    _, channel, request, _ = narrative_case
    provider = TemplateContentProvider()
    with (
        patch.object(provider, "generate_outline", side_effect=AssertionError("legacy fallback")),
        patch.object(provider, "generate_script", side_effect=AssertionError("script called")),
        pytest.raises(ValueError, match="not the current authority"),
    ):
        await content_service.generate_content(
            db_session,
            channel,
            request,
            ContentRunPayload(narrative_plan_id=uuid4()),
            provider=provider,
        )
    assert await db_session.scalar(select(func.count()).select_from(ScriptVersion)) == 0


@pytest.mark.asyncio
async def test_mission_generation_uses_the_same_pinned_plan_authority(db_session, narrative_case):
    client, channel, _, brief = narrative_case
    topic = (await db_session.get(ResearchBrief, UUID(brief["id"]))).topic_candidate_id
    selected = await client.post(f"/api/v1/channels/{channel}/topics/candidates/{topic}/select")
    assert selected.status_code == 200
    mission = await client.post(
        "/api/v1/missions",
        json={
            "title": "Narrative mission",
            "objective": "Generate grounded content",
            "channel_id": str(channel),
        },
    )
    assert mission.status_code == 201, mission.text
    mission_id = UUID(mission.json()["id"])
    assert (await client.post(f"/api/v1/missions/{mission_id}/plan")).status_code == 200
    execution = await db_session.scalar(
        select(MissionExecution).where(MissionExecution.mission_id == mission_id)
    )
    created = await client.post(
        f"/api/v1/channels/{channel}/content",
        json={
            "topic_candidate_id": str(topic),
            "research_brief_id": brief["id"],
            "mission_execution_id": str(execution.id),
        },
    )
    assert created.status_code == 201, created.text
    request = created.json()
    generated = await client.post(f"/api/v1/channels/{channel}/content/{request['id']}/generate")
    assert generated.status_code == 200, generated.text
    plan = (
        await client.get(f"/api/v1/channels/{channel}/content/{request['id']}/narrative-plan")
    ).json()
    assert generated.json()["narrative_plan_id"] == plan["id"]
    assert plan["content_generation_request_id"] == request["id"]
    assert plan["research_brief_id"] == request["research_brief_id"]
    assert plan["channel_dna_revision_id"] == request["channel_dna_revision_id"]


@pytest.mark.asyncio
async def test_qa_rejection_persists_findings_without_creating_script(db_session, narrative_case):
    from omega.application.narrative_qa_service import NarrativeQAService
    from omega.domain.narrative_qa import NarrativeQAStatus

    client, channel, request, _ = narrative_case
    evaluate = NarrativeQAService.evaluate_plan

    def rejected(service, *args, **kwargs):
        return evaluate(service, *args, **kwargs).model_copy(
            update={"status": NarrativeQAStatus.FAIL}
        )

    with patch.object(NarrativeQAService, "evaluate_plan", rejected):
        response = await client.post(f"/api/v1/channels/{channel}/content/{request}/generate")
    assert response.status_code == 400, response.text
    assert await db_session.scalar(select(func.count()).select_from(ScriptVersion)) == 0
    plan = (await client.get(f"/api/v1/channels/{channel}/content/{request}/narrative-plan")).json()
    assert plan["status"] == "REJECTED"
    assert plan["metadata"]["narrative_qa"]["status"] == "FAIL"
