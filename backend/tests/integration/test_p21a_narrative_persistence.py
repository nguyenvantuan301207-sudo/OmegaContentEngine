"""PostgreSQL integration and concurrency tests for NarrativePlan persistence authority and lineage.

Validates:
- Requirements A through O of P21-A-M027:
  A. create NarrativePlan v1
  B. retrieve NarrativePlan with ordered sections
  C. grounding round-trip
  D. create revision v2
  E. v1 becomes non-current / superseded
  F. v2 becomes sole current revision
  G. unique owner/version constraint (DB enforced)
  H. single-current partial unique constraint (DB enforced)
  I. concurrent revision creation from independent sessions/processes
  J. no lost update
  K. monotonic version numbers
  L. historical revision remains unchanged / immutable
  M. ScriptVersion pins exact NarrativePlan revision
  N. legacy ScriptVersion with NULL NarrativePlan remains valid
  O. Storyboard lineage can trace:
     Storyboard -> ScriptVersion -> NarrativePlan -> ContentGenerationRequest / research lineage
- Concurrency Canary (Section 13)
- Production Fail-Closed on FileBacked repository (Section 14)
"""

from __future__ import annotations

import concurrent.futures
import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from omega.application.narrative_plan_service import (
    FileBackedNarrativePlanRepository,
    HistoricalRevisionImmutableError,
    InMemoryNarrativePlanRepository,
    NarrativePlanService,
    PostgresNarrativePlanRepository,
)
from omega.application.storyboard_engine import StoryboardEngine, StoryboardPlan
from omega.config import get_settings
from omega.domain.narrative_plan import (
    GroundingReference,
    GroundingType,
    InformationDensity,
    NarrativeFormatProfile,
    NarrativePlan,
    NarrativePlanStatus,
    NarrativeSection,
    NarrativeSectionRole,
)
from omega.infrastructure.models import (
    Channel,
    ChannelDNARevision,
    ClaimEvidence,
    ContentGenerationRequest,
    NarrativeGroundingCitation as NarrativeGroundingCitationModel,
    NarrativePlan as NarrativePlanModel,
    NarrativeSection as NarrativeSectionModel,
    ResearchBrief,
    ResearchClaim,
    ResearchRequest,
    ResearchSource,
    ScriptVersion,
    TopicCandidate,
)


@pytest.fixture(scope="module")
def sync_db_sessionmaker():
    """Create a synchronous session factory bound to the isolated test database."""
    test_url = os.getenv("TEST_DATABASE_URL")
    if not test_url:
        pytest.skip("TEST_DATABASE_URL not set; skipping PostgreSQL integration tests.")

    sync_url = test_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    engine = create_engine(sync_url, echo=False, pool_pre_ping=True)
    maker = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    yield maker
    engine.dispose()


@pytest.fixture
def db_session(sync_db_sessionmaker):
    """Provide a clean transactional session for a test."""
    session = sync_db_sessionmaker()
    try:
        yield session
    finally:
        session.close()


def _seed_hierarchy(session: Session) -> dict[str, uuid.UUID]:
    """Seed parent entities required for NarrativePlan and ScriptVersion persistence."""
    channel_id = uuid.uuid4()
    channel = Channel(
        id=channel_id,
        slug=f"ch-{uuid.uuid4().hex[:8]}",
        name="Test Narrative Channel",
    )
    session.add(channel)

    dna_rev_id = uuid.uuid4()
    dna_rev = ChannelDNARevision(
        id=dna_rev_id,
        channel_id=channel_id,
        version=1,
        snapshot={"name": "Science DNA"},
        change_reason="Initial",
    )
    session.add(dna_rev)

    topic_id = uuid.uuid4()
    topic = TopicCandidate(
        id=topic_id,
        channel_id=channel_id,
        title="Cosmic Radiation Invariance",
        normalized_title="cosmic radiation invariance",
        source_name="Manual",
        summary="A study of cosmic background invariance.",
        topic_fingerprint=uuid.uuid4().hex,
        status="APPROVED",
    )
    session.add(topic)

    r_req_id = uuid.uuid4()
    r_req = ResearchRequest(
        id=r_req_id,
        channel_id=channel_id,
        topic_candidate_id=topic_id,
        status="COMPLETED",
    )
    session.add(r_req)

    brief_id = uuid.uuid4()
    brief = ResearchBrief(
        id=brief_id,
        research_request_id=r_req_id,
        channel_id=channel_id,
        topic_candidate_id=topic_id,
        title="Cosmic Radiation Brief",
        summary="Summary of cosmic radiation findings.",
    )
    session.add(brief)

    source_id = uuid.uuid4()
    source = ResearchSource(
        id=source_id,
        research_request_id=r_req_id,
        channel_id=channel_id,
        title="Astrophysical Journal 1992",
        publisher="AAS",
        url="https://example.com/cobe-1992",
        content_excerpt="Uniform cosmic background radiation measured by COBE.",
        content_hash=uuid.uuid4().hex,
        source_type="MANUAL",
    )
    session.add(source)

    claim_id = uuid.uuid4()
    claim = ResearchClaim(
        id=claim_id,
        research_request_id=r_req_id,
        channel_id=channel_id,
        claim_text="Background radiation is uniform within 1 part in 100,000.",
        normalized_claim="background radiation is uniform within 1 part in 100000",
        confidence_score=0.98,
    )
    session.add(claim)

    evidence_id = uuid.uuid4()
    evidence = ClaimEvidence(
        id=evidence_id,
        claim_id=claim_id,
        source_id=source_id,
        excerpt="COBE satellite DMR instrument measurements.",
        strength_score=99.0,
    )
    session.add(evidence)

    content_req_id = uuid.uuid4()
    content_req = ContentGenerationRequest(
        id=content_req_id,
        channel_id=channel_id,
        topic_candidate_id=topic_id,
        research_brief_id=brief_id,
        channel_dna_revision_id=dna_rev_id,
        status="APPROVED",
    )
    session.add(content_req)
    session.commit()

    return {
        "channel_id": channel_id,
        "channel_dna_revision_id": dna_rev_id,
        "topic_candidate_id": topic_id,
        "research_brief_id": brief_id,
        "claim_id": claim_id,
        "evidence_id": evidence_id,
        "source_id": source_id,
        "content_generation_request_id": content_req_id,
    }


def _build_test_sections(seeds: dict[str, uuid.UUID]) -> list[NarrativeSection]:
    """Build a deterministic list of NarrativeSections with grounding."""
    sec1 = NarrativeSection(
        id=uuid.uuid4(),
        section_order=1,
        role=NarrativeSectionRole.HOOK,
        objective="Hook the audience with the anomaly in cosmic microwave data.",
        key_information=["The universe has an ancient signature.", "It appears identical in every direction."],
        grounding_references=[
            GroundingReference(
                research_brief_id=seeds["research_brief_id"],
                claim_id=seeds["claim_id"],
                evidence_id=seeds["evidence_id"],
                source_id=seeds["source_id"],
                grounding_type=GroundingType.FACTUAL,
                description="COBE measurement proof.",
            )
        ],
        target_duration_seconds=10,
        target_information_density=InformationDensity.HIGH,
        open_loop_intent="Why is the temperature so uniform across vast disconnected horizons?",
        promise_id="loop_cosmic_horizon",
    )
    sec2 = NarrativeSection(
        id=uuid.uuid4(),
        section_order=2,
        role=NarrativeSectionRole.PAYOFF,
        objective="Deliver the payoff explaining inflation theory.",
        key_information=["Cosmic inflation expanded the quantum fluctuations.", "Uniformity was locked in."],
        grounding_references=[
            GroundingReference(
                research_brief_id=seeds["research_brief_id"],
                claim_id=seeds["claim_id"],
                evidence_id=seeds["evidence_id"],
                source_id=seeds["source_id"],
                grounding_type=GroundingType.FACTUAL,
                description="Inflationary model citation.",
            )
        ],
        target_duration_seconds=20,
        target_information_density=InformationDensity.MEDIUM,
        payoff_reference="loop_cosmic_horizon",
    )
    sec3 = NarrativeSection(
        id=uuid.uuid4(),
        section_order=3,
        role=NarrativeSectionRole.TAKEAWAY,
        objective="Summarize the core insight for viewers.",
        key_information=["The universe remembers its origin."],
        grounding_references=[],
        target_duration_seconds=15,
        target_information_density=InformationDensity.LOW,
    )
    return [sec1, sec2, sec3]


def test_narrative_plan_lifecycle_and_persistence(sync_db_sessionmaker):
    """Test Requirements A through F: create v1, retrieve ordered sections, grounding round-trip, create v2, demote v1."""
    session = sync_db_sessionmaker()
    seeds = _seed_hierarchy(session)
    session.close()

    repo = PostgresNarrativePlanRepository(session_factory=sync_db_sessionmaker)
    service = NarrativePlanService(repository=repo)
    sections = _build_test_sections(seeds)

    # A. Create NarrativePlan v1
    plan_v1, val_v1 = service.create_plan(
        content_generation_request_id=seeds["content_generation_request_id"],
        channel_dna_revision_id=seeds["channel_dna_revision_id"],
        topic_candidate_id=seeds["topic_candidate_id"],
        research_brief_id=seeds["research_brief_id"],
        format_profile=NarrativeFormatProfile.SHORT,
        target_duration_seconds=45,
        sections=sections,
        auto_validate=True,
    )
    assert plan_v1.version == 1
    assert plan_v1.is_current is True
    assert plan_v1.supersedes_plan_id is None
    assert plan_v1.status == NarrativePlanStatus.VALIDATED

    # B. Retrieve NarrativePlan with ordered sections
    retrieved = repo.get(plan_v1.id)
    assert retrieved is not None
    assert retrieved.id == plan_v1.id
    assert len(retrieved.sections) == 3
    assert [s.section_order for s in retrieved.sections] == [1, 2, 3]
    assert retrieved.sections[0].role == NarrativeSectionRole.HOOK
    assert retrieved.sections[1].role == NarrativeSectionRole.PAYOFF
    assert retrieved.sections[2].role == NarrativeSectionRole.TAKEAWAY

    # C. Grounding round-trip verification
    sec_hook = retrieved.sections[0]
    assert len(sec_hook.grounding_references) == 1
    g_ref = sec_hook.grounding_references[0]
    assert g_ref.research_brief_id == seeds["research_brief_id"]
    assert g_ref.claim_id == seeds["claim_id"]
    assert g_ref.evidence_id == seeds["evidence_id"]
    assert g_ref.source_id == seeds["source_id"]
    assert g_ref.grounding_type == GroundingType.FACTUAL

    # D, E, F. Create revision v2 and verify v1 demoted to superseded/non-current
    new_sections = [
        NarrativeSection(
            id=uuid.uuid4(),
            section_order=1,
            role=NarrativeSectionRole.HOOK,
            objective="Updated refined hook with sharper question.",
            key_information=["Universe temperature signature."],
            grounding_references=sec_hook.grounding_references,
            target_duration_seconds=12,
            promise_id="rev_promise",
        ),
        NarrativeSection(
            id=uuid.uuid4(),
            section_order=2,
            role=NarrativeSectionRole.PAYOFF,
            objective="Updated payoff detailing inflation.",
            key_information=["Expansion smoothed all irregularities."],
            target_duration_seconds=23,
            payoff_reference="rev_promise",
        ),
        NarrativeSection(
            id=uuid.uuid4(),
            section_order=3,
            role=NarrativeSectionRole.TAKEAWAY,
            objective="Updated closing takeaway.",
            key_information=["Cosmic structure resolved."],
            target_duration_seconds=10,
        ),
    ]

    plan_v2, val_v2 = service.create_revision(
        plan_id=plan_v1.id,
        new_sections=new_sections,
        notes="Refined hook timing.",
        auto_validate=True,
    )
    assert plan_v2.version == 2
    assert plan_v2.is_current is True
    assert plan_v2.supersedes_plan_id == plan_v1.id

    # Verify v1 is now non-current and superseded
    reloaded_v1 = repo.get(plan_v1.id)
    assert reloaded_v1.is_current is False
    assert reloaded_v1.status == NarrativePlanStatus.SUPERSEDED

    # Verify v2 is the sole current revision
    current_plan = repo.get_current_for_request(seeds["content_generation_request_id"])
    assert current_plan is not None
    assert current_plan.id == plan_v2.id
    assert current_plan.version == 2


def test_database_enforced_single_current_constraint(sync_db_sessionmaker):
    """Test Requirement H: PostgreSQL partial unique index uq_narrative_plan_single_current enforces exactly one current row."""
    session = sync_db_sessionmaker()
    seeds = _seed_hierarchy(session)

    # Insert plan 1 as current
    p1_id = uuid.uuid4()
    p1 = NarrativePlanModel(
        id=p1_id,
        content_generation_request_id=seeds["content_generation_request_id"],
        channel_dna_revision_id=seeds["channel_dna_revision_id"],
        version=1,
        is_current=True,
        status="DRAFT",
        format_profile="SHORT",
        target_duration_seconds=30,
    )
    session.add(p1)
    session.commit()

    # Attempt to insert plan 2 for the same request directly with is_current=True bypassing application code
    p2_id = uuid.uuid4()
    p2 = NarrativePlanModel(
        id=p2_id,
        content_generation_request_id=seeds["content_generation_request_id"],
        channel_dna_revision_id=seeds["channel_dna_revision_id"],
        version=2,
        is_current=True,
        status="DRAFT",
        format_profile="SHORT",
        target_duration_seconds=30,
    )
    session.add(p2)
    with pytest.raises(IntegrityError) as exc_info:
        session.commit()

    assert "uq_narrative_plan_single_current" in str(exc_info.value).lower()
    session.rollback()
    session.close()


def test_database_enforced_unique_version_constraint(sync_db_sessionmaker):
    """Test Requirement G: PostgreSQL unique constraint uq_narrative_plan_version prevents duplicate versions."""
    session = sync_db_sessionmaker()
    seeds = _seed_hierarchy(session)

    p1 = NarrativePlanModel(
        id=uuid.uuid4(),
        content_generation_request_id=seeds["content_generation_request_id"],
        channel_dna_revision_id=seeds["channel_dna_revision_id"],
        version=1,
        is_current=False,
        status="SUPERSEDED",
        format_profile="SHORT",
        target_duration_seconds=30,
    )
    session.add(p1)
    session.commit()

    # Attempt duplicate version 1
    p2 = NarrativePlanModel(
        id=uuid.uuid4(),
        content_generation_request_id=seeds["content_generation_request_id"],
        channel_dna_revision_id=seeds["channel_dna_revision_id"],
        version=1,
        is_current=False,
        status="SUPERSEDED",
        format_profile="SHORT",
        target_duration_seconds=30,
    )
    session.add(p2)
    with pytest.raises(IntegrityError) as exc_info:
        session.commit()

    assert "uq_narrative_plan_version" in str(exc_info.value).lower()
    session.rollback()
    session.close()


def test_concurrency_canary_cross_process_locking(sync_db_sessionmaker):
    """Test Requirements I, J, K & Section 13: Concurrency canary.

    Spawns multiple concurrent threads with independent database sessions attempting
    to create new revisions on the same parent request concurrently.
    Verifies:
    - exactly one current plan
    - no duplicate version numbers
    - no lost previous revisions
    - strictly monotonic version sequence
    """
    session = sync_db_sessionmaker()
    seeds = _seed_hierarchy(session)
    session.close()

    repo = PostgresNarrativePlanRepository(session_factory=sync_db_sessionmaker)
    service = NarrativePlanService(repository=repo)
    sections = _build_test_sections(seeds)

    # Initial plan v1
    initial_plan, _ = service.create_plan(
        content_generation_request_id=seeds["content_generation_request_id"],
        channel_dna_revision_id=seeds["channel_dna_revision_id"],
        format_profile=NarrativeFormatProfile.SHORT,
        target_duration_seconds=45,
        sections=sections,
    )
    assert initial_plan.version == 1

    num_concurrent_workers = 3

    def worker_create_revision(worker_id: int):
        # Create an independent repository instance per thread
        worker_repo = PostgresNarrativePlanRepository(session_factory=sync_db_sessionmaker)
        sec = [
            NarrativeSection(
                id=uuid.uuid4(),
                section_order=1,
                role=NarrativeSectionRole.HOOK,
                objective=f"Worker {worker_id} revised hook",
                target_duration_seconds=10,
                promise_id=f"worker_{worker_id}_loop",
            ),
            NarrativeSection(
                id=uuid.uuid4(),
                section_order=2,
                role=NarrativeSectionRole.PAYOFF,
                objective=f"Worker {worker_id} revised payoff",
                target_duration_seconds=20,
                payoff_reference=f"worker_{worker_id}_loop",
            ),
            NarrativeSection(
                id=uuid.uuid4(),
                section_order=3,
                role=NarrativeSectionRole.TAKEAWAY,
                objective=f"Worker {worker_id} takeaway",
                target_duration_seconds=15,
            ),
        ]
        # Use initial plan ID to race for next revision
        return worker_repo.create_revision_atomic(
            plan_id=initial_plan.id,
            new_sections=sec,
            notes=f"Worker {worker_id} update",
            status=NarrativePlanStatus.VALIDATED,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_concurrent_workers) as executor:
        futures = [executor.submit(worker_create_revision, i) for i in range(num_concurrent_workers)]
        results = [f.result() for f in futures]

    assert len(results) == num_concurrent_workers

    # Verify final database state
    all_plans = repo.list_by_request(seeds["content_generation_request_id"])
    versions = [p.version for p in all_plans]
    # Expected versions: [1, 2, 3, 4]
    expected_versions = list(range(1, num_concurrent_workers + 2))
    assert versions == expected_versions, f"Versions {versions} are not strictly monotonic {expected_versions}"

    # Exactly one current plan
    current_plans = [p for p in all_plans if p.is_current]
    assert len(current_plans) == 1, f"Expected exactly 1 current plan, found {len(current_plans)}"
    assert current_plans[0].version == max(expected_versions)

    # All earlier plans must be superseded and non-current
    for p in all_plans[:-1]:
        assert p.is_current is False
        assert p.status == NarrativePlanStatus.SUPERSEDED


def test_historical_revision_immutability(sync_db_sessionmaker):
    """Test Requirement L: Ordinary repository operations must reject narrative mutation of historical revisions."""
    session = sync_db_sessionmaker()
    seeds = _seed_hierarchy(session)
    session.close()

    repo = PostgresNarrativePlanRepository(session_factory=sync_db_sessionmaker)
    service = NarrativePlanService(repository=repo)
    sections = _build_test_sections(seeds)

    v1, _ = service.create_plan(
        content_generation_request_id=seeds["content_generation_request_id"],
        channel_dna_revision_id=seeds["channel_dna_revision_id"],
        format_profile=NarrativeFormatProfile.SHORT,
        target_duration_seconds=45,
        sections=sections,
    )

    # Create v2 so v1 becomes historical/superseded
    v2, _ = service.create_revision(
        plan_id=v1.id,
        new_sections=sections,
        notes="supersede v1",
    )

    reloaded_v1 = repo.get(v1.id)
    assert reloaded_v1.is_current is False
    assert reloaded_v1.status == NarrativePlanStatus.SUPERSEDED

    # Attempt to mutate narrative content on historical v1
    mutated_v1 = NarrativePlan(
        id=reloaded_v1.id,
        content_generation_request_id=reloaded_v1.content_generation_request_id,
        channel_dna_revision_id=reloaded_v1.channel_dna_revision_id,
        version=reloaded_v1.version,
        format_profile=NarrativeFormatProfile.LONG,  # Mutated content
        target_duration_seconds=600,
        estimated_duration_seconds=600,
        is_current=False,
        status=reloaded_v1.status,
        sections=reloaded_v1.sections,
        schema_version=reloaded_v1.schema_version,
    )

    with pytest.raises(HistoricalRevisionImmutableError):
        repo.save(mutated_v1)


def test_script_version_narrative_lineage_and_legacy_compatibility(sync_db_sessionmaker):
    """Test Requirements M, N, O: ScriptVersion pins exact revision, legacy rows remain valid, and Storyboard lineage traces."""
    session = sync_db_sessionmaker()
    seeds = _seed_hierarchy(session)
    session.close()

    repo = PostgresNarrativePlanRepository(session_factory=sync_db_sessionmaker)
    service = NarrativePlanService(repository=repo)
    sections = _build_test_sections(seeds)

    v1, _ = service.create_plan(
        content_generation_request_id=seeds["content_generation_request_id"],
        channel_dna_revision_id=seeds["channel_dna_revision_id"],
        topic_candidate_id=seeds["topic_candidate_id"],
        research_brief_id=seeds["research_brief_id"],
        format_profile=NarrativeFormatProfile.SHORT,
        target_duration_seconds=45,
        sections=sections,
    )

    session = sync_db_sessionmaker()

    # N. Legacy ScriptVersion with NULL narrative_plan_id remains valid
    legacy_script = ScriptVersion(
        id=uuid.uuid4(),
        content_request_id=seeds["content_generation_request_id"],
        version=1,
        title="Legacy Script Without Plan",
        hook_text="Legacy Hook",
        closing_text="Legacy Close",
        cta_text="Legacy CTA",
        narrative_plan_id=None,
    )
    session.add(legacy_script)
    session.commit()

    reloaded_legacy = session.scalars(
        select(ScriptVersion).where(ScriptVersion.id == legacy_script.id)
    ).first()
    assert reloaded_legacy.narrative_plan_id is None
    assert reloaded_legacy.narrative_plan_version is None

    # M. ScriptVersion pins exact NarrativePlan revision
    legacy_script.is_current = False
    linked_script = ScriptVersion(
        id=uuid.uuid4(),
        content_request_id=seeds["content_generation_request_id"],
        version=2,
        is_current=True,
        title="Modern Script With Pinned NarrativePlan",
        hook_text="Modern Hook",
        closing_text="Modern Close",
        cta_text="Modern CTA",
        narrative_plan_id=v1.id,
    )
    session.add(legacy_script)
    session.add(linked_script)
    session.commit()

    reloaded_linked = session.scalars(
        select(ScriptVersion).where(ScriptVersion.id == linked_script.id)
    ).first()
    assert reloaded_linked.narrative_plan_id == v1.id
    assert reloaded_linked.narrative_plan_version == 1
    assert reloaded_linked.narrative_plan.id == v1.id

    # O. Storyboard lineage trace:
    # Storyboard -> ScriptVersion -> NarrativePlan -> ContentGenerationRequest / research lineage
    engine = StoryboardEngine()
    script_dict = {
        "title": reloaded_linked.title,
        "narrative_plan_id": str(reloaded_linked.narrative_plan_id),
        "narrative_plan_version": reloaded_linked.narrative_plan_version,
        "estimated_duration_seconds": 45,
        "sections": [
            {
                "heading": "Intro",
                "statements": [
                    {"statement_text": "Did you know that cosmic radiation is invariant?"},
                ],
            },
            {
                "heading": "Payoff",
                "statements": [
                    {"statement_text": "Inflation smoothed every horizon across space."},
                ],
            },
        ],
    }

    storyboard = engine.generate_storyboard(script_dict, pacing="BALANCED")
    assert isinstance(storyboard, StoryboardPlan)
    assert storyboard.narrative_plan_id == str(v1.id)
    assert storyboard.narrative_plan_version == 1

    # Follow the full lineage backward to parent ContentGenerationRequest and ResearchBrief
    traced_plan = repo.get(uuid.UUID(storyboard.narrative_plan_id))
    assert traced_plan is not None
    assert traced_plan.content_generation_request_id == seeds["content_generation_request_id"]
    assert traced_plan.research_brief_id == seeds["research_brief_id"]
    assert traced_plan.topic_candidate_id == seeds["topic_candidate_id"]

    session.close()


def test_production_fail_closed_file_backed():
    """Test Section 14: NarrativePlanService fails closed in production if FileBacked repository is provided."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        file_repo = FileBackedNarrativePlanRepository(storage_dir=tmpdir)

        # Mock production environment
        orig_env = os.environ.get("ENVIRONMENT")
        os.environ["ENVIRONMENT"] = "production"
        get_settings.cache_clear() if hasattr(get_settings, "cache_clear") else None

        try:
            with pytest.raises(RuntimeError) as exc_info:
                NarrativePlanService(repository=file_repo)
            assert "prohibited in production" in str(exc_info.value).lower()
        finally:
            if orig_env is not None:
                os.environ["ENVIRONMENT"] = orig_env
            else:
                os.environ.pop("ENVIRONMENT", None)
            get_settings.cache_clear() if hasattr(get_settings, "cache_clear") else None
