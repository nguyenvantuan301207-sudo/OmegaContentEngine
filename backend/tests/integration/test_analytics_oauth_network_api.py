"""Integration tests for Analytics OAuth, Network manager integration, and REST API endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.analytics.capabilities import MetricCapabilityRegistry
from omega.application.analytics.quota_service import QuotaService
from omega.application.network.preflight import NetworkPreflightService
from omega.application.publisher.oauth_service import YOUTUBE_ANALYTICS_SCOPE, OAuthService
from omega.domain.analytics import (
    AnalyticsErrorCategory,
    MetricQuality,
    WindowState,
    WindowType,
)
from omega.domain.network import (
    NetworkAction,
    NetworkEgressPermit,
    NetworkPreflightRequest,
    ServiceCategory,
)
from omega.infrastructure.models import (
    AnalyticsAsset,
    AnalyticsComputedMetric,
    AnalyticsMetricLatestPointer,
    AnalyticsMetricObservation,
    AnalyticsProviderSnapshot,
    AnalyticsWindow,
    Channel,
    ChannelDNARevision,
    ContentGenerationRequest,
    CredentialVault,
    MediaArtifact,
    Mission,
    PlatformAccount,
    ProductionRequest,
    PublishAttempt,
    PublishIntent,
    ResearchBrief,
    ResearchRequest,
    ScriptVersion,
    Task,
    TopicCandidate,
)
from omega.infrastructure.vault import get_credential_vault
from omega.main import app

pytestmark = pytest.mark.usefixtures("publisher_test_env")


@pytest_asyncio.fixture
async def setup_api_asset(db_session: AsyncSession):
    """Fixture providing published asset with observations and computed metrics for API tests."""
    chan = Channel(
        id=uuid4(),
        slug=f"api-chan-{uuid4().hex[:8]}",
        name="API Test Channel",
        platform="YOUTUBE",
    )
    db_session.add(chan)

    dna = ChannelDNARevision(
        id=uuid4(),
        channel_id=chan.id,
        version=1,
        snapshot={"name": "API DNA"},
        change_reason="Initial",
    )
    db_session.add(dna)

    account = PlatformAccount(
        id=uuid4(),
        channel_id=chan.id,
        platform="YOUTUBE",
        account_display_name="API Channel Account",
        external_account_id="UC_api_123",
        status="ACTIVE",
        scopes=[
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.readonly",
            YOUTUBE_ANALYTICS_SCOPE,
        ],
    )
    db_session.add(account)

    vault = get_credential_vault()
    enc_access, v_ver = vault.encrypt("fake_access_token")
    enc_refresh, _ = vault.encrypt("fake_refresh_token")
    vault_entry = CredentialVault(
        id=uuid4(),
        platform_account_id=account.id,
        encrypted_access_token=enc_access,
        encrypted_refresh_token=enc_refresh,
        token_type="Bearer",
        access_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        key_version=v_ver,
    )
    db_session.add(vault_entry)

    mission = Mission(
        id=uuid4(),
        channel_id=chan.id,
        title="API Mission",
        objective="API Test Objective",
        autonomy_level="AUTONOMOUS",
        state="RUNNING",
    )
    db_session.add(mission)

    task = Task(
        id=uuid4(),
        mission_id=mission.id,
        task_type="PUBLISH_VIDEO",
        title="API Publish Task",
        state="COMPLETED",
    )
    db_session.add(task)

    topic = TopicCandidate(
        id=uuid4(),
        channel_id=chan.id,
        title="Topic",
        normalized_title="topic",
        source_name="Manual",
        summary="Summary",
        topic_fingerprint=uuid4().hex,
        status="APPROVED",
    )
    db_session.add(topic)

    r_req = ResearchRequest(
        id=uuid4(),
        channel_id=chan.id,
        topic_candidate_id=topic.id,
        status="COMPLETED",
    )
    db_session.add(r_req)

    brief = ResearchBrief(
        id=uuid4(),
        research_request_id=r_req.id,
        channel_id=chan.id,
        topic_candidate_id=topic.id,
        title="Brief",
        summary="Summary",
    )
    db_session.add(brief)

    content_req = ContentGenerationRequest(
        id=uuid4(),
        channel_id=chan.id,
        topic_candidate_id=topic.id,
        research_brief_id=brief.id,
        channel_dna_revision_id=dna.id,
        status="APPROVED",
    )
    db_session.add(content_req)

    script_ver = ScriptVersion(
        id=uuid4(),
        content_request_id=content_req.id,
        version=1,
        title="Script",
        hook_text="Hook",
        closing_text="Close",
        cta_text="CTA",
    )
    db_session.add(script_ver)

    prod_req = ProductionRequest(
        id=uuid4(),
        channel_id=chan.id,
        script_version_id=script_ver.id,
        content_request_id=content_req.id,
        channel_dna_revision_id=dna.id,
        status="COMPLETED",
    )
    db_session.add(prod_req)

    artifact = MediaArtifact(
        id=uuid4(),
        production_request_id=prod_req.id,
        artifact_type="VIDEO",
        version=1,
        is_current=True,
        storage_uri="videos/api_test.mp4",
        file_size_bytes=1024,
        content_hash="api_hash",
        mime_type="video/mp4",
    )
    db_session.add(artifact)

    intent = PublishIntent(
        id=uuid4(),
        mission_id=mission.id,
        task_id=task.id,
        channel_id=chan.id,
        platform_account_id=account.id,
        media_artifact_id=artifact.id,
        media_artifact_checksum="api_hash",
        channel_dna_revision_id=dna.id,
        title="API Published Video",
        description="Description",
        made_for_kids=False,
        intent_checksum=f"api_intent_{uuid4().hex}",
        state="PUBLISHED",
    )
    db_session.add(intent)

    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=intent.id,
        attempt_number=1,
        idempotency_key=f"api_attempt_{uuid4().hex}",
        state="SUCCEEDED",
        provider_video_id=f"api_vid_{uuid4().hex[:8]}",
        provider_url="https://youtube.com/watch?v=api_123",
    )
    db_session.add(attempt)

    asset = AnalyticsAsset(
        id=uuid4(),
        channel_id=chan.id,
        platform_account_id=account.id,
        publish_intent_id=intent.id,
        publish_attempt_id=attempt.id,
        media_artifact_id=artifact.id,
        channel_dna_revision_id=dna.id,
        provider="YOUTUBE",
        provider_video_id=attempt.provider_video_id,
        published_at=datetime.now(UTC) - timedelta(days=3),
        asset_status="ACTIVE",
        lifecycle_phase="ACTIVE_HOURLY",
        next_poll_due_at=datetime.now(UTC),
    )
    db_session.add(asset)

    window = AnalyticsWindow(
        id=uuid4(),
        asset_id=asset.id,
        window_type=WindowType.FIRST_24H.value,
        provider_date=None,
        provider_timezone="America/Los_Angeles",
        window_start_utc=asset.published_at,
        window_end_utc=asset.published_at + timedelta(hours=24),
        window_state=WindowState.FINALIZED.value,
    )
    db_session.add(window)

    snap = AnalyticsProviderSnapshot(
        id=uuid4(),
        asset_id=asset.id,
        channel_id=chan.id,
        platform_account_id=account.id,
        api_endpoint="YOUTUBE_DATA_API_VIDEOS",
        request_params={},
        raw_payload={"items": [{"statistics": {"viewCount": "4200"}}]},
        payload_checksum="chk_api",
        logical_query_key="l_query_api",
        poll_execution_key="p_exec_api",
        snapshot_dedupe_key=f"snap_dedupe_{uuid4().hex}",
        retrieval_timestamp=datetime.now(UTC),
        http_status=200,
    )
    db_session.add(snap)
    await db_session.flush()

    obs = AnalyticsMetricObservation(
        id=uuid4(),
        asset_id=asset.id,
        window_id=window.id,
        snapshot_id=snap.id,
        metric_name="views",
        classification="PROVIDER_FACT",
        metric_quality="AVAILABLE",
        integer_value=4200,
        numeric_value=4200.0,
        observation_dedupe_key=f"obs_dedupe_{uuid4().hex}",
        observation_timestamp=datetime.now(UTC),
    )
    db_session.add(obs)

    ptr = AnalyticsMetricLatestPointer(
        asset_id=asset.id,
        window_id=window.id,
        metric_name="views",
        current_observation_id=obs.id,
        current_revision_sequence=1,
    )
    db_session.add(ptr)

    comp = AnalyticsComputedMetric(
        id=uuid4(),
        asset_id=asset.id,
        window_id=window.id,
        metric_name="like_ratio_percent",
        classification="DETERMINISTIC_DERIVED",
        formula_identifier="FORMULA_LIKE_RATIO_V1",
        formula_version="1",
        source_checksum="chk",
        computation_dedupe_key=f"comp_dedupe_{uuid4().hex}",
        numeric_value=4.5,
        metric_quality="AVAILABLE",
        computation_revision_sequence=1,
        computed_at=datetime.now(UTC),
    )
    db_session.add(comp)
    await db_session.commit()

    return {
        "channel": chan,
        "account": account,
        "intent": intent,
        "attempt": attempt,
        "asset": asset,
        "window": window,
    }


# ── OAuth Scope Tests ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_analytics_scope_degrades_without_breaking_publisher(
    db_session: AsyncSession, setup_api_asset
):
    """Account with only upload/readonly remains valid for publishing, but Analytics gates permission."""
    account = setup_api_asset["account"]
    # Remove Analytics scope
    account.scopes = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.readonly",
    ]
    await db_session.commit()

    assert not OAuthService.has_analytics_scope(account)

    # Capability check for deep analytics returns PERMISSION_DENIED
    MetricCapabilityRegistry.initialize()
    is_valid, quality = MetricCapabilityRegistry.validate_metric_request(
        provider="YOUTUBE",
        canonical_metric_name="watch_time_seconds",
        api_source="ANALYTICS_API_V2",
        report_type="BASIC_VIDEO_REPORT",
        dimensions=["day", "video"],
        account_scopes=account.scopes,
    )
    assert not is_valid
    assert quality == MetricQuality.PERMISSION_DENIED


@pytest.mark.asyncio
async def test_scope_upgrade_requires_explicit_consent(db_session: AsyncSession, setup_api_asset):
    """Scope upgrade generates consent URL containing yt-analytics.readonly and prompt=consent."""
    account = setup_api_asset["account"]
    upgrade_url = await OAuthService.create_scope_upgrade_url(
        session=db_session,
        platform_account_id=account.id,
    )
    assert "prompt=consent" in upgrade_url
    assert "yt-analytics.readonly" in upgrade_url


@pytest.mark.asyncio
async def test_revoked_credential_halts_analytics(db_session: AsyncSession, setup_api_asset):
    """Simulated revoked OAuth credential produces AUTH_REVOKED and stops polling."""
    from omega.application.analytics.adapters.youtube import YouTubeAnalyticsAdapter

    adapter = YouTubeAnalyticsAdapter()
    dummy_resp = httpx.Response(
        status_code=401,
        json={"error": {"message": "Invalid Credentials", "errors": [{"reason": "authError"}]}},
        request=httpx.Request("GET", "https://example.com"),
    )

    err = adapter._classify_error(dummy_resp)
    assert err.category == AnalyticsErrorCategory.AUTH_REVOKED
    assert not err.retryable


# ── Network Manager Integration Tests ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_analytics_fresh_destination_permit(db_session: AsyncSession):
    """Preflight service returns a valid permit for official YouTube API endpoint."""
    preflight_svc = NetworkPreflightService(lambda: db_session)
    check_resp, permit = await preflight_svc.preflight(
        NetworkPreflightRequest(
            destination_url="https://www.googleapis.com/youtube/v3/videos",
            service_category=ServiceCategory.YOUTUBE_API,
            caller_key="test_fresh_permit",
        )
    )
    assert check_resp.decision.action in (NetworkAction.ALLOW, NetworkAction.ALLOW_DEGRADED)
    assert permit is not None
    assert permit.is_valid_for("https://www.googleapis.com/youtube/v3/videos")


@pytest.mark.asyncio
async def test_analytics_expired_permit_rejected():
    """An egress permit expired in the past is rejected."""
    expired_permit = NetworkEgressPermit(
        network_check_id=uuid4(),
        route_id=uuid4(),
        route_config_version=1,
        canonical_destination="https://www.googleapis.com",
        service_category=ServiceCategory.YOUTUBE_API,
        is_permitted=True,
        granted_at=datetime.now(UTC) - timedelta(minutes=10),
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    assert not expired_permit.is_valid_for("https://www.googleapis.com/youtube/v3/videos")


@pytest.mark.asyncio
async def test_analytics_blocked_destination_rejected(db_session: AsyncSession):
    """Disallowed destination returns non-ALLOW action without permit."""
    preflight_svc = NetworkPreflightService(lambda: db_session)
    check_resp, permit = await preflight_svc.preflight(
        NetworkPreflightRequest(
            destination_url="https://malicious-site.com/steal-data",
            service_category=ServiceCategory.YOUTUBE_API,
            caller_key="test_blocked",
        )
    )
    assert check_resp.decision.action not in (NetworkAction.ALLOW, NetworkAction.ALLOW_DEGRADED)
    assert permit is None or not permit.is_permitted


# ── REST API Tests ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_analytics_api_video_summary_and_timeline(setup_api_asset):
    """Test video analytics summary and timeline REST endpoints."""
    fix = setup_api_asset
    intent_id = fix["intent"].id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Summary
        resp = await client.get(f"/api/v1/analytics/videos/{intent_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["publish_intent_id"] == str(intent_id)
        assert "views" in data["latest_metrics"]
        assert data["latest_metrics"]["views"]["value"] == 4200
        assert data["latest_metrics"]["like_ratio_percent"]["value"] == 4.5

        # Timeline
        timeline_resp = await client.get(f"/api/v1/analytics/videos/{intent_id}/timeline")
        assert timeline_resp.status_code == 200
        tl_data = timeline_resp.json()
        assert len(tl_data["timeline"]) >= 1
        first_point = tl_data["timeline"][0]
        assert first_point["window_type"] == "FIRST_24H"
        assert first_point["metrics"]["views"]["value"] == 4200


@pytest.mark.asyncio
async def test_analytics_api_manual_refresh_rate_limit(setup_api_asset, db_session: AsyncSession):
    """Second immediate manual refresh request triggers HTTP 429."""
    fix = setup_api_asset
    asset = fix["asset"]
    intent_id = fix["intent"].id

    # If asset polled recently, 429
    asset.last_polled_at = datetime.now(UTC)
    await db_session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/v1/analytics/videos/{intent_id}/refresh")
        assert resp.status_code == 429
        assert "rate-limited" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_analytics_api_health(db_session: AsyncSession):
    """Test /api/v1/analytics/health endpoint returning operational status and quota buckets."""
    # Ensure a bucket exists
    await QuotaService.get_or_create_bucket(
        db_session, "YOUTUBE", "DATA_API_V3", "VIDEOS_LIST", configured_daily_limit=10000
    )
    await db_session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/analytics/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert len(data["quota_buckets"]) >= 1
        b = data["quota_buckets"][0]
        assert b["provider"] == "YOUTUBE"
        assert b["utilization_percent"] is not None
