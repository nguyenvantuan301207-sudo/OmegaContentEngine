from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.publisher.adapters.youtube import YouTubeDataApiAdapter
from omega.application.publisher.oauth_service import OAuthService, OAuthServiceError
from omega.domain.channel import ChannelState, Platform
from omega.domain.network import NetworkEgressPermit, ServiceCategory
from omega.domain.publisher import (
    PlatformAccountStatus,
    PrivacyStatus,
)
from omega.infrastructure.models import (
    Channel,
    CredentialVault,
    NetworkProfile,
    NetworkRoute,
    OAuthAuthorizationSession,
    PlatformAccount,
)

pytestmark = pytest.mark.usefixtures("publisher_test_env")


@pytest_asyncio.fixture
async def sample_channel(db_session: AsyncSession) -> Channel:
    now = datetime.now(UTC)
    prof = NetworkProfile(
        id=uuid4(),
        name=f"Profile-{uuid4().hex[:8]}",
        is_default=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(prof)
    await db_session.flush()

    route = NetworkRoute(
        id=uuid4(),
        profile_id=prof.id,
        name="Direct Internet",
        route_type="DIRECT",
        allowed_service_categories=[
            ServiceCategory.YOUTUBE_API.value,
            ServiceCategory.GENERAL_HTTP.value,
        ],
        tls_verify=True,
        config_version=1,
        config_checksum="chk1",
        created_at=now,
        updated_at=now,
    )
    db_session.add(route)

    chan = Channel(
        id=uuid4(),
        slug=f"test-pub-chan-{uuid4().hex[:8]}",
        name="Test Publisher Channel",
        platform=Platform.YOUTUBE.value,
        state=ChannelState.ACTIVE.value,
        dna={"target_audience": "Developers", "content_pillars": ["Tech", "Code"]},
    )
    db_session.add(chan)
    await db_session.commit()
    await db_session.refresh(chan)
    return chan


@pytest.mark.asyncio
async def test_oauth_authorization_url_generation(
    db_session: AsyncSession, sample_channel: Channel
):
    """Verify authorization URL generation stores session with state_hash and PKCE verifier."""
    auth_url = await OAuthService.create_authorization_url(
        session=db_session,
        channel_id=sample_channel.id,
        platform=Platform.YOUTUBE,
    )
    assert "https://accounts.google.com/o/oauth2/v2/auth" in auth_url
    assert "code_challenge=" in auth_url
    assert "state=" in auth_url

    # Check session in DB
    from sqlalchemy import select

    res = await db_session.execute(
        select(OAuthAuthorizationSession).where(
            OAuthAuthorizationSession.channel_id == sample_channel.id
        )
    )
    session_row = res.scalar_one_or_none()
    assert session_row is not None
    assert session_row.consumed_at is None
    assert len(session_row.state_hash) == 64
    assert session_row.encrypted_pkce_verifier is not None


@pytest.mark.asyncio
async def test_oauth_callback_one_time_consumption(
    db_session: AsyncSession, sample_channel: Channel, monkeypatch
):
    """Verify OAuth callback consumes state hash once and rejects replays."""
    # Mock token exchange
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "access_token": "mock_access_token_abc",
        "refresh_token": "mock_refresh_token_xyz",
        "expires_in": 3600,
    }
    mock_post = AsyncMock(return_value=mock_resp)

    mock_validate = AsyncMock()
    from omega.application.publisher.adapters.base import CredentialValidationResult

    mock_validate.return_value = CredentialValidationResult(
        is_valid=True,
        account_display_name="Tech Horizon",
        external_account_id="UC123456789",
    )

    monkeypatch.setattr("httpx.AsyncClient.post", mock_post)
    monkeypatch.setattr(
        "omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.validate_credentials",
        mock_validate,
    )

    auth_url = await OAuthService.create_authorization_url(
        session=db_session,
        channel_id=sample_channel.id,
        platform=Platform.YOUTUBE,
    )
    import urllib.parse

    parsed = urllib.parse.urlparse(auth_url)
    params = urllib.parse.parse_qs(parsed.query)
    raw_state = params["state"][0]

    # First callback: Should succeed
    account = await OAuthService.handle_oauth_callback(
        session=db_session,
        state=raw_state,
        code="mock_auth_code_123",
    )
    assert account is not None
    assert account.account_display_name == "Tech Horizon"
    assert account.status == PlatformAccountStatus.ACTIVE.value

    # Check credential vault
    from sqlalchemy import select

    vault_res = await db_session.execute(
        select(CredentialVault).where(CredentialVault.platform_account_id == account.id)
    )
    vault = vault_res.scalar_one_or_none()
    assert vault is not None
    assert vault.encrypted_refresh_token is not None

    # Second callback with SAME state: Must fail (replays prohibited)
    with pytest.raises(OAuthServiceError):
        await OAuthService.handle_oauth_callback(
            session=db_session,
            state=raw_state,
            code="mock_auth_code_replay",
        )


@pytest.mark.asyncio
async def test_disconnect_account_requires_confirmation(
    db_session: AsyncSession, sample_channel: Channel
):
    """Verify disconnect requires explicit confirmation boolean."""
    account = PlatformAccount(
        id=uuid4(),
        channel_id=sample_channel.id,
        platform="YOUTUBE",
        account_display_name="Channel Name",
        external_account_id="UC987654321",
        status=PlatformAccountStatus.ACTIVE.value,
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    db_session.add(account)
    await db_session.commit()

    # Reject without confirmation
    with pytest.raises(OAuthServiceError):
        await OAuthService.disconnect_account(db_session, account.id, confirm_disconnect=False)

    # Disconnect with confirmation
    revoked = await OAuthService.disconnect_account(db_session, account.id, confirm_disconnect=True)
    assert revoked.status == PlatformAccountStatus.REVOKED.value


@pytest.mark.asyncio
async def test_youtube_adapter_resumable_protocol(monkeypatch):
    """Verify official YouTube resumable upload protocol handling."""
    adapter = YouTubeDataApiAdapter()

    # 1. Mock session init
    mock_init_resp = MagicMock()
    mock_init_resp.status_code = 200
    mock_init_resp.headers = {
        "Location": "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=session_123"
    }
    mock_post = AsyncMock(return_value=mock_init_resp)
    monkeypatch.setattr("httpx.AsyncClient.post", mock_post)

    dummy_permit = NetworkEgressPermit(
        network_check_id=uuid4(),
        route_id=uuid4(),
        route_config_version=1,
        canonical_destination="https://www.googleapis.com",
        service_category=ServiceCategory.YOUTUBE_API,
        expires_at=datetime.now(UTC),
    )

    init_res = await adapter.initialize_resumable_upload(
        title="Test Video",
        description="Test Desc",
        tags=["tag1"],
        category_id="28",
        requested_privacy=PrivacyStatus.PRIVATE,
        made_for_kids=False,
        total_bytes=1000,
        access_token="mock_token",
        permit=dummy_permit,
    )
    assert "upload_id=session_123" in init_res.session_uri

    # 2. Mock intermediate chunk (308 Resume Incomplete)
    mock_chunk1_resp = MagicMock()
    mock_chunk1_resp.status_code = 308
    mock_chunk1_resp.headers = {"Range": "bytes=0-499"}
    mock_put1 = AsyncMock(return_value=mock_chunk1_resp)
    monkeypatch.setattr("httpx.AsyncClient.put", mock_put1)

    chunk1_res = await adapter.upload_chunk(
        session_uri=init_res.session_uri,
        chunk_data=b"0" * 500,
        start_byte=0,
        total_bytes=1000,
        permit=dummy_permit,
    )
    assert chunk1_res.is_complete is False
    assert chunk1_res.next_byte_offset == 500

    # 3. Mock final chunk (201 Created)
    mock_chunk2_resp = MagicMock()
    mock_chunk2_resp.status_code = 201
    mock_chunk2_resp.json.return_value = {
        "id": "yt_video_xyz987",
        "status": {"privacyStatus": "private"},
    }
    mock_put2 = AsyncMock(return_value=mock_chunk2_resp)
    monkeypatch.setattr("httpx.AsyncClient.put", mock_put2)

    chunk2_res = await adapter.upload_chunk(
        session_uri=init_res.session_uri,
        chunk_data=b"0" * 500,
        start_byte=500,
        total_bytes=1000,
        permit=dummy_permit,
    )
    assert chunk2_res.is_complete is True
    assert chunk2_res.provider_video_id == "yt_video_xyz987"
    assert chunk2_res.provider_url == "https://youtu.be/yt_video_xyz987"
    assert chunk2_res.effective_privacy_status == PrivacyStatus.PRIVATE


@pytest.mark.asyncio
async def test_youtube_adapter_authoritative_reconciliation(monkeypatch):
    """Verify UNKNOWN reconciliation checks: 200 confirms success, 308 resumes, 404 holds."""
    adapter = YouTubeDataApiAdapter()

    dummy_permit = NetworkEgressPermit(
        network_check_id=uuid4(),
        route_id=uuid4(),
        route_config_version=1,
        canonical_destination="https://www.googleapis.com",
        service_category=ServiceCategory.YOUTUBE_API,
        expires_at=datetime.now(UTC),
    )

    # 1. 200 OK -> Confirmed success
    mock_resp1 = MagicMock()
    mock_resp1.status_code = 200
    mock_resp1.json.return_value = {"id": "reconciled_video_123"}
    monkeypatch.setattr("httpx.AsyncClient.put", AsyncMock(return_value=mock_resp1))
    recon1 = await adapter.reconcile_upload_session("https://fake-session", 1000, dummy_permit)
    assert recon1.is_confirmed_success is True
    assert recon1.provider_video_id == "reconciled_video_123"

    # 2. 308 Resume Incomplete -> Incomplete with byte offset
    mock_resp2 = MagicMock()
    mock_resp2.status_code = 308
    mock_resp2.headers = {"Range": "bytes=0-749"}
    monkeypatch.setattr("httpx.AsyncClient.put", AsyncMock(return_value=mock_resp2))
    recon2 = await adapter.reconcile_upload_session("https://fake-session", 1000, dummy_permit)
    assert recon2.is_confirmed_success is False
    assert recon2.is_incomplete is True
    assert recon2.bytes_received == 750

    # 3. 404 Not Found -> Manual hold
    mock_resp3 = MagicMock()
    mock_resp3.status_code = 404
    monkeypatch.setattr("httpx.AsyncClient.put", AsyncMock(return_value=mock_resp3))
    recon3 = await adapter.reconcile_upload_session("https://fake-session", 1000, dummy_permit)
    assert recon3.is_confirmed_success is False
    assert recon3.is_incomplete is False
    assert recon3.is_held_for_review is True


@pytest.mark.asyncio
async def test_oauth_expired_state_rejected(db_session: AsyncSession, sample_channel: Channel):
    """Verify expired OAuth authorization state cannot be consumed and raises error."""
    import hashlib
    import secrets

    from omega.infrastructure.vault import get_credential_vault

    now = datetime.now(UTC)
    vault = get_credential_vault()
    raw_state = secrets.token_urlsafe(32)
    state_hash = hashlib.sha256(raw_state.encode("utf-8")).hexdigest()
    enc_ver, _ = vault.encrypt("mock_verifier")

    expired_session = OAuthAuthorizationSession(
        id=uuid4(),
        platform=Platform.YOUTUBE.value,
        channel_id=sample_channel.id,
        state_hash=state_hash,
        encrypted_pkce_verifier=enc_ver,
        redirect_uri="http://localhost:8000/callback",
        requested_scopes=["https://www.googleapis.com/auth/youtube.upload"],
        created_at=now - timedelta(minutes=20),
        expires_at=now - timedelta(minutes=10),
        consumed_at=None,
    )
    db_session.add(expired_session)
    await db_session.commit()

    with pytest.raises(
        OAuthServiceError, match="Invalid, expired, or already consumed OAuth state"
    ):
        await OAuthService.handle_oauth_callback(
            session=db_session,
            state=raw_state,
            code="mock_auth_code",
        )


@pytest.mark.asyncio
async def test_refresh_response_without_refresh_token_preserves_old_token(monkeypatch):
    """Verify when provider refresh response omits refresh_token, existing refresh token is preserved."""
    adapter = YouTubeDataApiAdapter()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "access_token": "new_refreshed_access_token_999",
        "expires_in": 3600,
        "token_type": "Bearer",
        # Notice: refresh_token omitted by Google
    }
    monkeypatch.setattr("httpx.AsyncClient.post", AsyncMock(return_value=mock_resp))

    dummy_permit = NetworkEgressPermit(
        network_check_id=uuid4(),
        route_id=uuid4(),
        route_config_version=1,
        canonical_destination="https://oauth2.googleapis.com",
        service_category=ServiceCategory.YOUTUBE_API,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    refreshed = await adapter.refresh_access_token(
        refresh_token="existing_unrotated_refresh_token",
        client_id="client_id",
        client_secret="client_secret",
        permit=dummy_permit,
    )
    assert refreshed.access_token == "new_refreshed_access_token_999"
    assert refreshed.new_refresh_token is None


@pytest.mark.asyncio
async def test_session_uri_ssrf_validation(db_session: AsyncSession):
    """Verify malicious private and link-local session URIs are rejected by network preflight before HTTP."""
    from omega.application.network.preflight import NetworkPreflightService
    from omega.domain.network import NetworkAction, NetworkPreflightRequest

    preflight_service = NetworkPreflightService(lambda: db_session)

    # 1. AWS/Cloud metadata service IP
    decision_meta, permit_meta = await preflight_service.preflight(
        NetworkPreflightRequest(
            destination_url="http://169.254.169.254/latest/meta-data",
            service_category=ServiceCategory.YOUTUBE_API,
            caller_key="ssrf_test_metadata",
        )
    )
    assert decision_meta.decision is not None
    assert decision_meta.decision.action == NetworkAction.BLOCKED_NETWORK
    assert permit_meta is None

    # 2. Localhost
    decision_local, permit_local = await preflight_service.preflight(
        NetworkPreflightRequest(
            destination_url="http://127.0.0.1:8000/internal",
            service_category=ServiceCategory.YOUTUBE_API,
            caller_key="ssrf_test_localhost",
        )
    )
    assert decision_local.decision is not None
    assert decision_local.decision.action == NetworkAction.BLOCKED_NETWORK
    assert permit_local is None


@pytest.mark.asyncio
async def test_separate_oauth_and_upload_network_permits(db_session: AsyncSession):
    """Verify OAuth token endpoint and upload endpoint require separate, distinct destination permits."""
    from omega.application.network.preflight import NetworkPreflightService
    from omega.domain.network import NetworkAction, NetworkPreflightRequest

    preflight_service = NetworkPreflightService(lambda: db_session)

    # 1. OAuth permit bound to oauth2.googleapis.com
    dec_oauth, permit_oauth = await preflight_service.preflight(
        NetworkPreflightRequest(
            destination_url="https://oauth2.googleapis.com/token",
            service_category=ServiceCategory.YOUTUBE_API,
            caller_key="oauth_permit_test",
        )
    )
    assert dec_oauth.decision is not None
    assert dec_oauth.decision.action == NetworkAction.ALLOW
    assert permit_oauth is not None
    assert permit_oauth.canonical_destination.startswith("https://oauth2.googleapis.com")

    # 2. Upload permit bound to www.googleapis.com
    dec_upload, permit_upload = await preflight_service.preflight(
        NetworkPreflightRequest(
            destination_url="https://www.googleapis.com/upload/youtube/v3/videos",
            service_category=ServiceCategory.YOUTUBE_API,
            caller_key="upload_permit_test",
        )
    )
    assert dec_upload.decision is not None
    assert dec_upload.decision.action == NetworkAction.ALLOW
    assert permit_upload is not None
    assert permit_upload.canonical_destination.startswith("https://www.googleapis.com")
    assert permit_oauth.canonical_destination != permit_upload.canonical_destination


@pytest.mark.asyncio
async def test_expired_permit_forces_fresh_preflight(db_session: AsyncSession):
    """Verify an expired NetworkEgressPermit is recognized as expired and requires a fresh preflight check."""
    from omega.domain.network import NetworkAction, NetworkEgressPermit, ServiceCategory

    now = datetime.now(UTC)
    expired_permit = NetworkEgressPermit(
        network_check_id=uuid4(),
        route_id=uuid4(),
        route_config_version=1,
        canonical_destination="https://www.googleapis.com",
        service_category=ServiceCategory.YOUTUBE_API,
        expires_at=now - timedelta(seconds=10),
    )
    assert expired_permit.expires_at < now

    # A fresh preflight request must generate a valid future-expiring permit
    from omega.application.network.preflight import NetworkPreflightService
    from omega.domain.network import NetworkPreflightRequest

    preflight_service = NetworkPreflightService(lambda: db_session)
    dec, fresh_permit = await preflight_service.preflight(
        NetworkPreflightRequest(
            destination_url="https://www.googleapis.com/upload/youtube/v3/videos",
            service_category=ServiceCategory.YOUTUBE_API,
            caller_key="fresh_preflight_after_expired",
        )
    )
    assert dec.decision is not None
    assert dec.decision.action == NetworkAction.ALLOW
    assert fresh_permit is not None
    assert fresh_permit.expires_at > now
