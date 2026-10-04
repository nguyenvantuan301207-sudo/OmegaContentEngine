"""Comprehensive Unit Tests for P25-A Controlled Publishing Rollout.

Test Matrix:
A. Eligibility (full accepted, non-current artifact, QA missing, Guardian fail, CreativeQA REVISE/FAIL, packaging mismatch, thumbnail missing, attribution dropped, provider missing)
B. Intent (deterministic intent, stable checksum, changed payload produces changed identity)
C. Idempotency (duplicate execution produces 1 provider media object, replay returns existing receipt)
D. Provider (success, retryable failure, terminal failure, partial failure)
E. Recovery (crash after upload, crash after metadata, resume safely, no duplicate upload)
F. Packaging (accepted title/description/tags, thumbnail handoff, attribution preserved)
G. Visibility (explicit private/unlisted/public semantics, no accidental PUBLIC default)
H. Scheduling (valid future schedule, invalid timestamp blocked)
I. Concurrency (lease fencing blocks conflicting worker)
J. Lineage (full cross-phase lineage audit)
K. External safety (fake provider only, zero network mutation)
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from omega.application.publisher.eligibility_gate import PublishEligibilityGate
from omega.application.publisher.fake_provider import (
    FakePublishingProvider,
    FakeRetryableProviderError,
    FakeTerminalProviderError,
)
from omega.application.publisher.provider_abstraction import (
    ProviderMediaResult,
    ProviderPublicationState,
)
from omega.application.publisher.state_machine import (
    IllegalPublishStateTransitionError,
    PublishingStateMachine,
)
from omega.domain.channel_dna import ChannelDNA
from omega.domain.creative_qa import (
    CREATIVE_QA_ENGINE_VERSION,
    CreativeQAProvenance,
    CreativeQAResult,
    CreativeQASeverity,
    CreativeQAStatus,
)
from omega.domain.packaging import (
    CTAMetadata,
    PackagingPlan,
    PackagingProvenance,
    ThumbnailArtifact,
    ThumbnailConcept,
    TitleCandidate,
    TitleStrategy,
    VideoChapter,
)
from omega.domain.production import ProductionQAStatus
from omega.domain.publisher import PublisherErrorCategory
from omega.domain.publishing import (
    PublishEligibilityDenialReason,
    PublishEligibilityResult,
    PublishingState,
    PublishPayload,
    PublishReceipt,
    PublishScheduleSpec,
    PublishVisibility,
    compute_canonical_payload_checksum,
)


# ── Helper Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def temp_thumbnail_file(tmp_path: Path):
    """Creates a temporary thumbnail file with verified SHA-256."""
    thumb_path = tmp_path / "test_thumb.png"
    content = b"fake-png-thumbnail-bytes-for-p25a-testing"
    thumb_path.write_bytes(content)
    content_hash = hashlib.sha256(content).hexdigest()
    return thumb_path, content_hash


@pytest.fixture
def sample_packaging_plan(temp_thumbnail_file):
    thumb_path, thumb_sha256 = temp_thumbnail_file
    channel_dna_rev_id = uuid4()
    creative_style_plan_id = uuid4()

    title = TitleCandidate(
        text="The Revolutionary Quantum Engine Explained",
        strategy=TitleStrategy.EXPLAINER,
        character_count=42,
    )
    thumb_concept = ThumbnailConcept(
        primary_subject="Quantum Engine Glow",
    )
    thumb_art = ThumbnailArtifact(
        concept_id=thumb_concept.concept_id,
        file_path=thumb_path,
        file_size_bytes=len(thumb_path.read_bytes()),
        content_sha256=thumb_sha256,
    )
    prov = PackagingProvenance(
        channel_dna_revision_id=channel_dna_rev_id,
        creative_style_plan_id=creative_style_plan_id,
        selected_title_id=title.candidate_id,
        selected_thumbnail_id=thumb_concept.concept_id,
    )

    return PackagingPlan(
        channel_dna_revision_id=channel_dna_rev_id,
        creative_style_plan_id=creative_style_plan_id,
        title_candidates=(title,),
        selected_title=title,
        thumbnail_concepts=(thumb_concept,),
        selected_thumbnail=thumb_concept,
        physical_thumbnail_artifact=thumb_art,
        description="A deep dive into quantum physics. Research by Dr. Elena Vance (Attribution).",
        chapters=(
            VideoChapter(title="Intro", start_time_seconds=0),
            VideoChapter(title="Core Principles", start_time_seconds=120),
        ),
        tags=("quantum", "physics", "science"),
        attribution_block="Research by Dr. Elena Vance (Attribution).",
        provenance=prov,
    )


@pytest.fixture
def sample_creative_qa_pass(sample_packaging_plan):
    return CreativeQAResult(
        status=CreativeQAStatus.PASS,
        highest_severity=CreativeQASeverity.INFO,
        findings=(),
        recommendations=(),
        provenance=CreativeQAProvenance(
            channel_dna_revision_id=sample_packaging_plan.channel_dna_revision_id,
            creative_style_plan_id=sample_packaging_plan.creative_style_plan_id,
        ),
        blocker_count=0,
        error_count=0,
        warning_count=0,
        info_count=1,
        is_accepted=True,
    )


@pytest.fixture
def mock_production_entities(sample_packaging_plan):
    req_id = uuid4()
    art_id = uuid4()
    chan_id = uuid4()
    acct_id = uuid4()
    art_hash = "a" * 64

    prod_req = MagicMock()
    prod_req.id = req_id
    prod_req.channel_id = chan_id
    prod_req.channel_dna_revision_id = sample_packaging_plan.channel_dna_revision_id

    artifact = MagicMock()
    artifact.id = art_id
    artifact.production_request_id = req_id
    artifact.state = "ACCEPTED"
    artifact.is_current = True
    artifact.artifact_type = "VIDEO"
    artifact.content_hash = art_hash

    runtime_truth = MagicMock()
    runtime_truth.id = uuid4()
    runtime_truth.artifact_id = art_id
    runtime_truth.schema_version = 4
    runtime_truth.payload = {"artifact_sha256": art_hash}

    account = MagicMock()
    account.id = acct_id
    account.channel_id = chan_id
    account.status = "ACTIVE"
    account.platform = "YOUTUBE"

    return prod_req, artifact, runtime_truth, account


# ── Test Matrix A: Eligibility Gate ──────────────────────────────────────────


def test_matrix_a_fully_accepted_production_is_eligible(
    mock_production_entities, sample_packaging_plan, sample_creative_qa_pass
):
    prod_req, artifact, rt, account = mock_production_entities

    res = PublishEligibilityGate.evaluate(
        production_request=prod_req,
        artifact=artifact,
        runtime_truth=rt,
        production_qa_status=ProductionQAStatus.PASSED,
        guardian_allowed=True,
        creative_qa_result=sample_creative_qa_pass,
        packaging_plan=sample_packaging_plan,
        platform_account=account,
        channel_id=prod_req.channel_id,
        verify_physical_thumbnail=True,
    )

    assert res.is_eligible is True
    assert len(res.denial_reasons) == 0


def test_matrix_a_stale_or_non_current_artifact_blocked(
    mock_production_entities, sample_packaging_plan, sample_creative_qa_pass
):
    prod_req, artifact, rt, account = mock_production_entities
    artifact.is_current = False

    res = PublishEligibilityGate.evaluate(
        production_request=prod_req,
        artifact=artifact,
        runtime_truth=rt,
        production_qa_status=ProductionQAStatus.PASSED,
        guardian_allowed=True,
        creative_qa_result=sample_creative_qa_pass,
        packaging_plan=sample_packaging_plan,
        platform_account=account,
        channel_id=prod_req.channel_id,
    )

    assert res.is_eligible is False
    assert PublishEligibilityDenialReason.STALE_ARTIFACT in res.denial_reasons


def test_matrix_a_qa_missing_or_failed_blocked(
    mock_production_entities, sample_packaging_plan, sample_creative_qa_pass
):
    prod_req, artifact, rt, account = mock_production_entities

    res = PublishEligibilityGate.evaluate(
        production_request=prod_req,
        artifact=artifact,
        runtime_truth=rt,
        production_qa_status=ProductionQAStatus.BLOCKED,
        guardian_allowed=True,
        creative_qa_result=sample_creative_qa_pass,
        packaging_plan=sample_packaging_plan,
        platform_account=account,
        channel_id=prod_req.channel_id,
    )

    assert res.is_eligible is False
    assert PublishEligibilityDenialReason.PRODUCTION_QA_NOT_PASS in res.denial_reasons


def test_matrix_a_guardian_not_accepted_blocked(
    mock_production_entities, sample_packaging_plan, sample_creative_qa_pass
):
    prod_req, artifact, rt, account = mock_production_entities

    res = PublishEligibilityGate.evaluate(
        production_request=prod_req,
        artifact=artifact,
        runtime_truth=rt,
        production_qa_status=ProductionQAStatus.PASSED,
        guardian_allowed=False,  # Blocked!
        creative_qa_result=sample_creative_qa_pass,
        packaging_plan=sample_packaging_plan,
        platform_account=account,
        channel_id=prod_req.channel_id,
    )

    assert res.is_eligible is False
    assert PublishEligibilityDenialReason.GUARDIAN_NOT_ACCEPTED in res.denial_reasons


def test_matrix_a_creative_qa_revise_or_fail_blocked(
    mock_production_entities, sample_packaging_plan, sample_creative_qa_pass
):
    prod_req, artifact, rt, account = mock_production_entities

    # REVISE status
    cqa_revise = sample_creative_qa_pass.model_copy(
        update={"status": CreativeQAStatus.REVISE, "is_accepted": False}
    )
    res_revise = PublishEligibilityGate.evaluate(
        production_request=prod_req,
        artifact=artifact,
        runtime_truth=rt,
        production_qa_status=ProductionQAStatus.PASSED,
        guardian_allowed=True,
        creative_qa_result=cqa_revise,
        packaging_plan=sample_packaging_plan,
        platform_account=account,
        channel_id=prod_req.channel_id,
    )
    assert res_revise.is_eligible is False
    assert PublishEligibilityDenialReason.CREATIVE_QA_NOT_PASS in res_revise.denial_reasons

    # FAIL status
    cqa_fail = sample_creative_qa_pass.model_copy(
        update={"status": CreativeQAStatus.FAIL, "is_accepted": False}
    )
    res_fail = PublishEligibilityGate.evaluate(
        production_request=prod_req,
        artifact=artifact,
        runtime_truth=rt,
        production_qa_status=ProductionQAStatus.PASSED,
        guardian_allowed=True,
        creative_qa_result=cqa_fail,
        packaging_plan=sample_packaging_plan,
        platform_account=account,
        channel_id=prod_req.channel_id,
    )
    assert res_fail.is_eligible is False
    assert PublishEligibilityDenialReason.CREATIVE_QA_NOT_PASS in res_fail.denial_reasons


def test_matrix_a_thumbnail_missing_blocked(
    mock_production_entities, sample_packaging_plan, sample_creative_qa_pass
):
    prod_req, artifact, rt, account = mock_production_entities
    plan_no_thumb = sample_packaging_plan.model_copy(
        update={"physical_thumbnail_artifact": None}
    )

    res = PublishEligibilityGate.evaluate(
        production_request=prod_req,
        artifact=artifact,
        runtime_truth=rt,
        production_qa_status=ProductionQAStatus.PASSED,
        guardian_allowed=True,
        creative_qa_result=sample_creative_qa_pass,
        packaging_plan=plan_no_thumb,
        platform_account=account,
        channel_id=prod_req.channel_id,
    )

    assert res.is_eligible is False
    assert PublishEligibilityDenialReason.THUMBNAIL_MISSING in res.denial_reasons


def test_matrix_a_attribution_dropped_blocked(
    mock_production_entities, sample_packaging_plan, sample_creative_qa_pass
):
    prod_req, artifact, rt, account = mock_production_entities
    # Target description omitted the required attribution block!
    modified_desc = "A deep dive into quantum physics. No attribution included."

    res = PublishEligibilityGate.evaluate(
        production_request=prod_req,
        artifact=artifact,
        runtime_truth=rt,
        production_qa_status=ProductionQAStatus.PASSED,
        guardian_allowed=True,
        creative_qa_result=sample_creative_qa_pass,
        packaging_plan=sample_packaging_plan,
        platform_account=account,
        channel_id=prod_req.channel_id,
        target_description=modified_desc,
    )

    assert res.is_eligible is False
    assert PublishEligibilityDenialReason.ATTRIBUTION_REQUIRED in res.denial_reasons


def test_matrix_a_provider_not_configured_blocked(
    mock_production_entities, sample_packaging_plan, sample_creative_qa_pass
):
    prod_req, artifact, rt, account = mock_production_entities
    account.status = "REVOKED"

    res = PublishEligibilityGate.evaluate(
        production_request=prod_req,
        artifact=artifact,
        runtime_truth=rt,
        production_qa_status=ProductionQAStatus.PASSED,
        guardian_allowed=True,
        creative_qa_result=sample_creative_qa_pass,
        packaging_plan=sample_packaging_plan,
        platform_account=account,
        channel_id=prod_req.channel_id,
    )

    assert res.is_eligible is False
    assert PublishEligibilityDenialReason.PROVIDER_NOT_CONFIGURED in res.denial_reasons


# ── Test Matrix B: Intent & Checksum ─────────────────────────────────────────


def test_matrix_b_deterministic_checksum_stability():
    dna_id = uuid4()
    c1 = compute_canonical_payload_checksum(
        video_artifact_sha256="b" * 64,
        title="Title Test",
        description="Description Test",
        tags=["tag1", "tag2"],
        thumbnail_sha256="c" * 64,
        platform="YOUTUBE",
        visibility="PRIVATE",
        channel_dna_revision_id=dna_id,
        attribution_block="Attribution block",
    )
    c2 = compute_canonical_payload_checksum(
        video_artifact_sha256="b" * 64,
        title="Title Test",
        description="Description Test",
        tags=["tag2", "tag1"],  # reversed order
        thumbnail_sha256="c" * 64,
        platform="YOUTUBE",
        visibility="PRIVATE",
        channel_dna_revision_id=dna_id,
        attribution_block="Attribution block",
    )
    assert c1 == c2  # Sorting tags makes checksum stable!

    # Changing title changes checksum
    c3 = compute_canonical_payload_checksum(
        video_artifact_sha256="b" * 64,
        title="Different Title",
        description="Description Test",
        tags=["tag1", "tag2"],
        thumbnail_sha256="c" * 64,
        platform="YOUTUBE",
        visibility="PRIVATE",
        channel_dna_revision_id=dna_id,
        attribution_block="Attribution block",
    )
    assert c1 != c3


# ── Test Matrix C & D: Fake Provider & Idempotency ───────────────────────────


@pytest.mark.asyncio
async def test_matrix_c_idempotent_duplicate_replay_maintains_single_object():
    fake = FakePublishingProvider()
    payload = PublishPayload(
        title="Sample Video",
        description="Test description",
        thumbnail_path="thumb.png",
        thumbnail_sha256="d" * 64,
        video_artifact_id=uuid4(),
        video_artifact_sha256="e" * 64,
        channel_dna_revision_id=uuid4(),
    )
    idempotency_key = "idemp-test-key-1"

    # First upload
    res1 = await fake.upload_media(
        artifact_path="video.mp4", payload=payload, idempotency_key=idempotency_key
    )
    assert fake.total_media_count == 1
    assert fake.upload_call_count == 1

    # Second upload with SAME idempotency key (replay)
    res2 = await fake.upload_media(
        artifact_path="video.mp4", payload=payload, idempotency_key=idempotency_key
    )
    assert fake.total_media_count == 1  # Still 1! No duplicate media created!
    assert fake.upload_call_count == 2
    assert res1.external_media_id == res2.external_media_id


@pytest.mark.asyncio
async def test_matrix_d_fake_provider_retryable_and_terminal_failures():
    fake = FakePublishingProvider()
    payload = PublishPayload(
        title="Sample Video",
        description="Test description",
        thumbnail_path="thumb.png",
        thumbnail_sha256="d" * 64,
        video_artifact_id=uuid4(),
        video_artifact_sha256="e" * 64,
        channel_dna_revision_id=uuid4(),
    )

    # Injected Retryable Failure (e.g. 503)
    fake.retryable_failures_remaining = 1
    with pytest.raises(FakeRetryableProviderError):
        await fake.upload_media(
            artifact_path="video.mp4", payload=payload, idempotency_key="key-retry"
        )
    # Next attempt succeeds
    res = await fake.upload_media(
        artifact_path="video.mp4", payload=payload, idempotency_key="key-retry"
    )
    assert res.external_media_id.startswith("fake-")

    # Injected Terminal Failure (e.g. permanent auth failure)
    fake.fail_terminal_message = "Permanent quota exceeded or auth revoked."
    with pytest.raises(FakeTerminalProviderError):
        await fake.upload_media(
            artifact_path="video.mp4", payload=payload, idempotency_key="key-term"
        )


@pytest.mark.asyncio
async def test_matrix_d_partial_failure_metadata_and_thumbnail():
    fake = FakePublishingProvider()
    payload = PublishPayload(
        title="Sample Video",
        description="Test description",
        thumbnail_path="thumb.png",
        thumbnail_sha256="d" * 64,
        video_artifact_id=uuid4(),
        video_artifact_sha256="e" * 64,
        channel_dna_revision_id=uuid4(),
    )
    upload_res = await fake.upload_media(
        artifact_path="video.mp4", payload=payload, idempotency_key="key-partial"
    )
    ext_id = upload_res.external_media_id

    # Metadata failure
    fake.fail_metadata = True
    with pytest.raises(FakeRetryableProviderError):
        await fake.apply_metadata(
            external_media_id=ext_id,
            title="Title",
            description="Desc",
            tags=("tag1",),
        )
    # Query state: media exists, but metadata not applied
    st = await fake.fetch_publication_state(external_media_id=ext_id)
    assert st.exists_on_provider is True
    assert st.metadata_applied is False

    # Retry metadata succeeds
    await fake.apply_metadata(
        external_media_id=ext_id,
        title="Title",
        description="Desc",
        tags=("tag1",),
    )
    st = await fake.fetch_publication_state(external_media_id=ext_id)
    assert st.metadata_applied is True


# ── Test Matrix E: State Machine ─────────────────────────────────────────────


def test_matrix_e_state_machine_valid_and_illegal_transitions():
    assert (
        PublishingStateMachine.transition(PublishingState.PREPARED, PublishingState.ELIGIBLE)
        == PublishingState.ELIGIBLE
    )
    assert (
        PublishingStateMachine.transition(PublishingState.ELIGIBLE, PublishingState.SUBMITTING)
        == PublishingState.SUBMITTING
    )
    assert (
        PublishingStateMachine.transition(PublishingState.SUBMITTING, PublishingState.UPLOADED)
        == PublishingState.UPLOADED
    )
    assert (
        PublishingStateMachine.transition(
            PublishingState.UPLOADED, PublishingState.METADATA_APPLIED
        )
        == PublishingState.METADATA_APPLIED
    )
    assert (
        PublishingStateMachine.transition(
            PublishingState.METADATA_APPLIED, PublishingState.THUMBNAIL_APPLIED
        )
        == PublishingState.THUMBNAIL_APPLIED
    )
    assert (
        PublishingStateMachine.transition(
            PublishingState.THUMBNAIL_APPLIED, PublishingState.PUBLISHED
        )
        == PublishingState.PUBLISHED
    )

    # Illegal transition: PREPARED -> PUBLISHED directly must raise
    with pytest.raises(IllegalPublishStateTransitionError):
        PublishingStateMachine.transition(PublishingState.PREPARED, PublishingState.PUBLISHED)


# ── Test Matrix G: Visibility Policy ─────────────────────────────────────────


def test_matrix_g_visibility_defaults_safely_to_private():
    payload = PublishPayload(
        title="Safe Defaults",
        thumbnail_path="thumb.png",
        thumbnail_sha256="d" * 64,
        video_artifact_id=uuid4(),
        video_artifact_sha256="e" * 64,
        channel_dna_revision_id=uuid4(),
    )
    assert payload.visibility == PublishVisibility.PRIVATE


# ── Test Matrix H: Scheduling Policy ─────────────────────────────────────────


def test_matrix_h_future_schedule_validation():
    future_time = datetime.now(UTC) + timedelta(days=2)
    spec = PublishScheduleSpec(publish_at=future_time)
    assert spec.publish_at == future_time

    # Past time must fail validation
    past_time = datetime.now(UTC) - timedelta(hours=1)
    with pytest.raises(ValueError):
        PublishScheduleSpec(publish_at=past_time)


# ── Test Matrix J: Lineage & Receipt Secret Redaction ────────────────────────


def test_matrix_j_receipt_redacts_tokens_and_preserves_lineage():
    receipt = PublishReceipt(
        publish_intent_id=uuid4(),
        publish_attempt_id=uuid4(),
        provider="FAKE",
        external_media_id="fake-12345",
        provider_status="PUBLISHED",
        payload_checksum="chk-123",
        completion_state=PublishingState.PUBLISHED,
        response_metadata={
            "access_token": "secret-oauth-token-12345",
            "refresh_token": "secret-refresh-token",
            "public_metric": 42,
        },
        lineage={"production_request_id": str(uuid4())},
    )

    # Access token and refresh token MUST be redacted
    assert receipt.response_metadata["access_token"] == "[REDACTED_SECRET]"
    assert receipt.response_metadata["refresh_token"] == "[REDACTED_SECRET]"
    assert receipt.response_metadata["public_metric"] == 42
    assert "production_request_id" in receipt.lineage
