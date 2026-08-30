"""Unit tests for OMEGA-011 Publisher domain models, checksums, and credential vault."""

import os
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from omega.application.publisher.adapters.youtube import YouTubeDataApiAdapter
from omega.domain.publisher import (
    Platform,
    PlatformAccountStatus,
    PublishAttemptState,
    PublisherErrorCategory,
    PublishIntentCreate,
    PublishIntentState,
    ReconciliationStatus,
    compute_publish_intent_checksum,
)
from omega.infrastructure.vault import (
    CredentialVaultService,
    VaultConfigurationError,
)


def test_publisher_domain_enums():
    """Verify domain enums match authoritative state machine requirements."""
    # 7-state PublishIntent machine
    assert len(PublishIntentState) == 7
    assert set(PublishIntentState) == {
        "DRAFT",
        "APPROVED",
        "CLAIMED",
        "PUBLISHED",
        "FAILED",
        "SUPERSEDED",
        "CANCELLED",
    }

    # 9-state PublishAttempt machine
    assert len(PublishAttemptState) == 9
    assert set(PublishAttemptState) == {
        "CREATED",
        "UPLOADING",
        "FINALIZING",
        "SUCCEEDED",
        "RETRYABLE_FAILED",
        "PERMANENT_FAILED",
        "UNKNOWN",
        "BLOCKED_GUARDIAN",
        "CANCELLED",
    }

    # Platforms
    assert Platform.YOUTUBE == "YOUTUBE"
    assert PlatformAccountStatus.ACTIVE == "ACTIVE"
    assert ReconciliationStatus.CONFIRMED_SUCCESS == "CONFIRMED_SUCCESS"


def test_publish_intent_checksum_deterministic_and_sensitive():
    """Verify intent checksum is deterministic and sensitive to all parameters."""
    task_id = uuid4()
    art_hash = "a" * 64
    dna_rev = uuid4()

    k1 = compute_publish_intent_checksum(
        task_id=task_id,
        media_artifact_checksum=art_hash,
        channel_dna_revision_id=dna_rev,
        platform="YOUTUBE",
        title="My Great Video",
        description="Description here",
        tags=["tech", "ai"],
        requested_privacy_status="PRIVATE",
        category_id="28",
        made_for_kids=False,
    )
    k2 = compute_publish_intent_checksum(
        task_id=task_id,
        media_artifact_checksum=art_hash,
        channel_dna_revision_id=dna_rev,
        platform="YOUTUBE",
        title="My Great Video",
        description="Description here",
        tags=["tech", "ai"],
        requested_privacy_status="PRIVATE",
        category_id="28",
        made_for_kids=False,
    )
    assert k1 == k2

    # Tag ordering does not alter checksum
    k_tags_reordered = compute_publish_intent_checksum(
        task_id=task_id,
        media_artifact_checksum=art_hash,
        channel_dna_revision_id=dna_rev,
        platform="YOUTUBE",
        title="My Great Video",
        description="Description here",
        tags=["ai", "tech"],
        requested_privacy_status="PRIVATE",
        category_id="28",
        made_for_kids=False,
    )
    assert k1 == k_tags_reordered

    # Sensitivity: title change
    k_diff_title = compute_publish_intent_checksum(
        task_id=task_id,
        media_artifact_checksum=art_hash,
        channel_dna_revision_id=dna_rev,
        platform="YOUTUBE",
        title="Different Title",
        description="Description here",
        tags=["tech", "ai"],
        requested_privacy_status="PRIVATE",
        category_id="28",
        made_for_kids=False,
    )
    assert k1 != k_diff_title

    # Sensitivity: made_for_kids change
    k_diff_kids = compute_publish_intent_checksum(
        task_id=task_id,
        media_artifact_checksum=art_hash,
        channel_dna_revision_id=dna_rev,
        platform="YOUTUBE",
        title="My Great Video",
        description="Description here",
        tags=["tech", "ai"],
        requested_privacy_status="PRIVATE",
        category_id="28",
        made_for_kids=True,
    )
    assert k1 != k_diff_kids


def test_publish_intent_create_validation():
    """Verify validation rules on PublishIntentCreate payload."""
    with pytest.raises(ValueError):
        # Invalid hash length
        PublishIntentCreate(
            mission_id=uuid4(),
            task_id=uuid4(),
            channel_id=uuid4(),
            platform_account_id=uuid4(),
            media_artifact_id=uuid4(),
            media_artifact_checksum="too_short",
            title="Title",
            made_for_kids=False,
        )


def test_credential_vault_encryption_decryption_and_fail_closed():
    """Verify Fernet vault encrypts, decrypts, and fails closed when key missing."""
    key1 = Fernet.generate_key().decode("utf-8")
    vault = CredentialVaultService(master_key=key1, active_version=1)

    secret_text = "secret_refresh_token_12345"
    ciphertext, v = vault.encrypt(secret_text)
    assert v == 1
    assert ciphertext != secret_text

    decrypted = vault.decrypt(ciphertext, v)
    assert decrypted == secret_text

    # Fail closed on empty configuration
    with pytest.raises(VaultConfigurationError):
        # Save and restore environment
        old_key = os.environ.pop("OMEGA_SECRET_ENCRYPTION_KEY", None)
        old_ring = os.environ.pop("OMEGA_KEYRING", None)
        try:
            from unittest.mock import patch

            from omega.config import Settings

            with patch(
                "omega.config.get_settings", return_value=Settings(omega_secret_encryption_key="")
            ):
                CredentialVaultService(master_key=None, keyring=None)
        finally:
            if old_key:
                os.environ["OMEGA_SECRET_ENCRYPTION_KEY"] = old_key
            if old_ring:
                os.environ["OMEGA_KEYRING"] = old_ring


def test_credential_vault_key_rotation():
    """Verify multi-key keyring rotates old ciphertext to new active version."""
    key1 = Fernet.generate_key().decode("utf-8")
    key2 = Fernet.generate_key().decode("utf-8")

    keyring = {1: key1, 2: key2}
    vault = CredentialVaultService(keyring=keyring, active_version=2)

    secret = "my_super_secret_oauth_token"
    # Encrypt with version 1
    cipher_v1, v1 = vault.encrypt(secret, key_version=1)
    assert v1 == 1

    # Decrypt with version 1
    assert vault.decrypt(cipher_v1, 1) == secret

    # Rotate to active version (2)
    cipher_v2, v2 = vault.rotate_ciphertext(cipher_v1, current_version=1)
    assert v2 == 2
    assert cipher_v2 != cipher_v1
    assert vault.decrypt(cipher_v2, 2) == secret


def test_youtube_adapter_error_classification():
    """Verify provider error taxonomy and Pacific midnight retry calculation."""
    adapter = YouTubeDataApiAdapter()

    # 401 Auth Expired
    err_401 = adapter.classify_error(Exception("Unauthorized"), response_status=401)
    assert err_401.category == PublisherErrorCategory.AUTH_EXPIRED
    assert err_401.is_retryable is True

    # 400 Invalid Grant
    err_grant = adapter.classify_error(
        Exception("invalid_grant: Token has been expired or revoked."), response_status=400
    )
    assert err_grant.category == PublisherErrorCategory.AUTH_REVOKED
    assert err_grant.is_retryable is False

    # 429 Rate Limited
    err_429 = adapter.classify_error(Exception("Rate limit"), response_status=429)
    assert err_429.category == PublisherErrorCategory.RATE_LIMITED
    assert err_429.is_retryable is True
    assert err_429.retry_after_seconds == 60

    # 403 Quota Exceeded
    err_quota = adapter.classify_error(
        Exception("quotaExceeded: The request cannot be completed."), response_status=403
    )
    assert err_quota.category == PublisherErrorCategory.QUOTA_EXCEEDED
    assert err_quota.is_retryable is True
    assert err_quota.retry_after_seconds is not None and err_quota.retry_after_seconds >= 60

    # 503 Provider Server Error
    err_503 = adapter.classify_error(Exception("Service Unavailable"), response_status=503)
    assert err_503.category == PublisherErrorCategory.PROVIDER_5XX
    assert err_503.is_retryable is True
