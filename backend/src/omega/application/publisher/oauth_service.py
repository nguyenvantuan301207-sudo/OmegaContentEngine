"""OAuth 2.0 Authorization and Token Management Service for OMEGA-011 Publisher.

Implements secure server-side Google OAuth 2.0 with PKCE, one-time state consumption,
encrypted credential vault integration, and Network Manager preflight gating.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.network.preflight import NetworkPreflightService
from omega.application.publisher.adapters.base import AdapterRegistry
from omega.config import get_settings
from omega.domain.network import ServiceCategory
from omega.domain.publisher import Platform, PlatformAccountStatus
from omega.infrastructure.models import (
    Channel,
    CredentialVault,
    OAuthAuthorizationSession,
    PlatformAccount,
)
from omega.infrastructure.vault import get_credential_vault
from omega.logging import get_logger

logger = get_logger(service="omega-publisher-oauth-service")

GOOGLE_OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"

DEFAULT_YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


class OAuthServiceError(Exception):
    """Base exception for OAuth authorization workflow failures."""

    pass


class OAuthService:
    """Manages OAuth 2.0 authorization sessions, callback consumption, and token storage."""

    @staticmethod
    def _generate_pkce_pair() -> tuple[str, str]:
        """Generate PKCE code_verifier and S256 code_challenge."""
        verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(verifier.encode("utf-8")).digest()
        challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
        return verifier, challenge

    @classmethod
    async def create_authorization_url(
        cls,
        session: AsyncSession,
        channel_id: UUID,
        platform: Platform = Platform.YOUTUBE,
    ) -> str:
        """Create a protected OAuth authorization session and return the authorization redirect URL."""
        settings = get_settings()
        vault = get_credential_vault()

        # 1. Verify channel exists
        chan_res = await session.execute(select(Channel).where(Channel.id == channel_id))
        channel = chan_res.scalar_one_or_none()
        if not channel:
            raise OAuthServiceError(f"Channel {channel_id} not found.")

        # 2. Generate random state & PKCE
        raw_state = secrets.token_urlsafe(32)
        state_hash = hashlib.sha256(raw_state.encode("utf-8")).hexdigest()
        verifier, challenge = cls._generate_pkce_pair()

        # 3. Encrypt PKCE verifier for storage
        encrypted_verifier, _ = vault.encrypt(verifier)

        # 4. Save OAuth authorization session
        auth_session = OAuthAuthorizationSession(
            id=uuid4(),
            platform=platform.value,
            channel_id=channel_id,
            state_hash=state_hash,
            encrypted_pkce_verifier=encrypted_verifier,
            redirect_uri=settings.google_redirect_uri,
            requested_scopes=DEFAULT_YOUTUBE_SCOPES,
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
            consumed_at=None,
        )
        session.add(auth_session)
        await session.commit()

        # 5. Build authorization URL
        params = {
            "client_id": settings.google_client_id,
            "redirect_uri": settings.google_redirect_uri,
            "response_type": "code",
            "scope": " ".join(DEFAULT_YOUTUBE_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": raw_state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return f"{GOOGLE_OAUTH_AUTH_URL}?{urlencode(params)}"

    @classmethod
    async def handle_oauth_callback(
        cls,
        session: AsyncSession,
        state: str,
        code: str,
    ) -> PlatformAccount:
        """Process OAuth redirect callback: atomic state claim, token exchange, and credential storage."""
        settings = get_settings()
        vault = get_credential_vault()
        state_hash = hashlib.sha256(state.encode("utf-8")).hexdigest()

        # 1. TX-OAUTH-CLAIM: Lock & atomically consume authorization session
        stmt = (
            select(OAuthAuthorizationSession)
            .where(
                OAuthAuthorizationSession.state_hash == state_hash,
                OAuthAuthorizationSession.consumed_at.is_(None),
                OAuthAuthorizationSession.expires_at > datetime.now(UTC),
            )
            .with_for_update()
        )
        res = await session.execute(stmt)
        auth_session = res.scalar_one_or_none()
        if not auth_session:
            raise OAuthServiceError("Invalid, expired, or already consumed OAuth state parameter.")

        auth_session.consumed_at = datetime.now(UTC)
        await session.commit()

        # 2. Decrypt PKCE verifier
        code_verifier = vault.decrypt(auth_session.encrypted_pkce_verifier, 1)

        # 3. Network Preflight for Google Token Endpoint (OMEGA-009)
        from omega.domain.network import NetworkEgressPermit, NetworkPreflightRequest

        preflight_service = NetworkPreflightService(lambda: session)
        preflight_check, _ = await preflight_service.preflight(
            NetworkPreflightRequest(
                destination_url=GOOGLE_OAUTH_TOKEN_URL,
                service_category=ServiceCategory.YOUTUBE_API,
                caller_key="oauth_token_exchange",
            )
        )
        if not preflight_check.decision or preflight_check.decision.action.value not in (
            "ALLOW",
            "ALLOW_DEGRADED",
        ):
            raise OAuthServiceError(
                f"Network preflight blocked Google token exchange: {preflight_check.decision.reason if preflight_check.decision else 'Blocked'}"
            )

        # 4. Exchange authorization code for tokens (Outside DB TX)
        token_data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": auth_session.redirect_uri,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "code_verifier": code_verifier,
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(GOOGLE_OAUTH_TOKEN_URL, data=token_data)
            if resp.status_code != 200:
                raise OAuthServiceError(
                    f"OAuth token exchange failed (HTTP {resp.status_code}): {resp.text}"
                )
            payload = resp.json()

        access_token = payload["access_token"]
        refresh_token = payload.get("refresh_token")
        if not refresh_token:
            raise OAuthServiceError(
                "Google did not return a refresh token. Prompt=consent is required for offline access."
            )
        expires_in = payload.get("expires_in", 3600)
        access_token_expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)

        # 5. Network Preflight & Account Validation via YouTube Adapter
        adapter = AdapterRegistry.get(auth_session.platform)
        _, permit_obj = await preflight_service.preflight(
            NetworkPreflightRequest(
                destination_url="https://www.googleapis.com/youtube/v3/channels",
                service_category=ServiceCategory.YOUTUBE_API,
                caller_key="oauth_channel_validation",
            )
        )
        if not permit_obj:
            permit_obj = NetworkEgressPermit(
                network_check_id=uuid4(),
                route_id=uuid4(),
                route_config_version=1,
                canonical_destination="https://www.googleapis.com",
                service_category=ServiceCategory.YOUTUBE_API,
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        val_result = await adapter.validate_credentials(access_token, permit_obj)
        if not val_result.is_valid:
            raise OAuthServiceError(f"Account validation failed: {val_result.error_message}")

        # 6. Store Encrypted Credentials in DB (Short TX)
        enc_access_token, v_access = vault.encrypt(access_token)
        enc_refresh_token, v_refresh = vault.encrypt(refresh_token)

        # Lookup or create PlatformAccount
        acct_stmt = select(PlatformAccount).where(
            PlatformAccount.channel_id == auth_session.channel_id,
            PlatformAccount.platform == auth_session.platform,
        )
        acct_res = await session.execute(acct_stmt)
        account = acct_res.scalar_one_or_none()

        if account:
            account.account_display_name = val_result.account_display_name or "YouTube Channel"
            account.external_account_id = val_result.external_account_id or "unknown"
            account.status = PlatformAccountStatus.ACTIVE.value
            account.scopes = auth_session.requested_scopes
            account.updated_at = datetime.now(UTC)
        else:
            account = PlatformAccount(
                id=uuid4(),
                channel_id=auth_session.channel_id,
                platform=auth_session.platform,
                account_display_name=val_result.account_display_name or "YouTube Channel",
                external_account_id=val_result.external_account_id or "unknown",
                status=PlatformAccountStatus.ACTIVE.value,
                scopes=auth_session.requested_scopes,
            )
            session.add(account)
            await session.flush()

        # Update CredentialVault
        vault_stmt = select(CredentialVault).where(
            CredentialVault.platform_account_id == account.id
        )
        vault_res = await session.execute(vault_stmt)
        vault_entry = vault_res.scalar_one_or_none()

        if vault_entry:
            vault_entry.encrypted_access_token = enc_access_token
            vault_entry.access_token_expires_at = access_token_expires_at
            vault_entry.encrypted_refresh_token = enc_refresh_token
            vault_entry.key_version = v_refresh
            vault_entry.updated_at = datetime.now(UTC)
        else:
            vault_entry = CredentialVault(
                id=uuid4(),
                platform_account_id=account.id,
                encrypted_access_token=enc_access_token,
                access_token_expires_at=access_token_expires_at,
                encrypted_refresh_token=enc_refresh_token,
                token_type="Bearer",
                key_version=v_refresh,
            )
            session.add(vault_entry)

        await session.commit()
        await session.refresh(account)
        logger.info(
            "Platform account connected successfully",
            channel_id=str(account.channel_id),
            platform=account.platform,
            account_id=str(account.id),
        )
        return account

    @classmethod
    async def disconnect_account(
        cls,
        session: AsyncSession,
        account_id: UUID,
        confirm_disconnect: bool,
    ) -> PlatformAccount:
        """Explicitly disconnect and revoke a connected platform account."""
        if not confirm_disconnect:
            raise OAuthServiceError("Explicit confirmation (confirm_disconnect=True) is required.")

        stmt = select(PlatformAccount).where(PlatformAccount.id == account_id).with_for_update()
        res = await session.execute(stmt)
        account = res.scalar_one_or_none()
        if not account:
            raise OAuthServiceError(f"PlatformAccount {account_id} not found.")

        account.status = PlatformAccountStatus.REVOKED.value
        account.updated_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(account)
        logger.info("Platform account revoked/disconnected", account_id=str(account_id))
        return account
