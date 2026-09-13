"""Canonical operational policy for publisher outcomes.

This module is deliberately pure: it chooses an operation but does not create a
queue, retry row, upload session, or provider request.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from omega.domain.publisher import (
    HandoffStatus,
    PublishAttemptState,
    PublisherErrorCategory,
    ReconciliationStatus,
)


class PublisherRecoveryAction(enum.StrEnum):
    RETRY = "RETRY"
    MANUAL_HOLD = "MANUAL_HOLD"
    DEAD_LETTER = "DEAD_LETTER"
    TERMINAL = "TERMINAL"
    CONFIRMED_SUCCESS = "CONFIRMED_SUCCESS"


@dataclass(frozen=True)
class PublisherFailureEvidence:
    """Provider/session evidence used to fence ambiguous recovery decisions."""

    has_upload_session: bool = False
    provider_offset_known: bool = False
    session_terminal: bool = False
    auth_refresh_available: bool = True


_RETRYABLE = {
    PublisherErrorCategory.AUTH_EXPIRED,
    PublisherErrorCategory.RATE_LIMITED,
    PublisherErrorCategory.QUOTA_EXCEEDED,
    PublisherErrorCategory.NETWORK_TRANSIENT,
    PublisherErrorCategory.PROVIDER_5XX,
}

_TERMINAL = {
    PublisherErrorCategory.AUTH_REVOKED,
    PublisherErrorCategory.INVALID_MEDIA,
    PublisherErrorCategory.INVALID_METADATA,
    PublisherErrorCategory.CONTENT_POLICY_REJECTED,
    PublisherErrorCategory.PERMISSION_DENIED,
    PublisherErrorCategory.PRIVACY_RESTRICTION_BLOCKED,
    PublisherErrorCategory.PERMANENT_PROVIDER_ERROR,
}


def determine_publisher_recovery_action(
    *,
    error_category: PublisherErrorCategory | None,
    attempt_state: PublishAttemptState,
    reconciliation_status: ReconciliationStatus | None = None,
    evidence: PublisherFailureEvidence | None = None,
    handoff_status: HandoffStatus | None = None,
) -> PublisherRecoveryAction:
    """Map current durable state and evidence to exactly one recovery action."""
    evidence = evidence or PublisherFailureEvidence()

    if attempt_state == PublishAttemptState.SUCCEEDED or (
        reconciliation_status == ReconciliationStatus.CONFIRMED_SUCCESS
    ):
        return PublisherRecoveryAction.CONFIRMED_SUCCESS
    if handoff_status == HandoffStatus.DEAD_LETTER:
        return PublisherRecoveryAction.DEAD_LETTER
    if reconciliation_status == ReconciliationStatus.MANUAL_HOLD:
        return PublisherRecoveryAction.MANUAL_HOLD
    if evidence.session_terminal:
        return PublisherRecoveryAction.MANUAL_HOLD
    if attempt_state == PublishAttemptState.UNKNOWN or (
        error_category == PublisherErrorCategory.UNKNOWN_OUTCOME
    ):
        # A known offset is resumable only through the existing UploadSession;
        # it is never permission to create a replacement publication attempt.
        return PublisherRecoveryAction.MANUAL_HOLD
    if error_category == PublisherErrorCategory.DUPLICATE_OR_CONFLICT:
        return PublisherRecoveryAction.MANUAL_HOLD
    if error_category == PublisherErrorCategory.AUTH_EXPIRED and not evidence.auth_refresh_available:
        return PublisherRecoveryAction.TERMINAL
    if error_category in _RETRYABLE:
        return PublisherRecoveryAction.RETRY
    if error_category in _TERMINAL:
        return PublisherRecoveryAction.TERMINAL
    return PublisherRecoveryAction.TERMINAL


CURRENT_ERROR_TO_ACTION_MATRIX: dict[PublisherErrorCategory, PublisherRecoveryAction] = {
    category: determine_publisher_recovery_action(
        error_category=category,
        attempt_state=(
            PublishAttemptState.UNKNOWN
            if category == PublisherErrorCategory.UNKNOWN_OUTCOME
            else PublishAttemptState.RETRYABLE_FAILED
        ),
    )
    for category in PublisherErrorCategory
}
