"""Publishing State Machine for P25-A.

Enforces deterministic lifecycle transitions across the 11 authoritative states.
Illegal transitions fail closed with an explicit IllegalPublishStateTransitionError.
"""

from __future__ import annotations

from omega.domain.publishing import PublishingState


class IllegalPublishStateTransitionError(Exception):
    """Raised when an illegal transition is attempted on the publish state machine."""

    def __init__(self, from_state: PublishingState, to_state: PublishingState, reason: str = "") -> None:
        super().__init__(
            f"Illegal publishing state transition from '{from_state.value}' to '{to_state.value}'. {reason}".strip()
        )
        self.from_state = from_state
        self.to_state = to_state


ALLOWED_TRANSITIONS: dict[PublishingState, set[PublishingState]] = {
    PublishingState.PREPARED: {
        PublishingState.ELIGIBLE,
        PublishingState.FAILED_TERMINAL,
    },
    PublishingState.ELIGIBLE: {
        PublishingState.SUBMITTING,
        PublishingState.FAILED_TERMINAL,
    },
    PublishingState.SUBMITTING: {
        PublishingState.UPLOADED,
        PublishingState.FAILED_RETRYABLE,
        PublishingState.FAILED_TERMINAL,
        PublishingState.RECONCILIATION_REQUIRED,
    },
    PublishingState.UPLOADED: {
        PublishingState.METADATA_APPLIED,
        PublishingState.FAILED_RETRYABLE,
        PublishingState.FAILED_TERMINAL,
        PublishingState.RECONCILIATION_REQUIRED,
    },
    PublishingState.METADATA_APPLIED: {
        PublishingState.THUMBNAIL_APPLIED,
        PublishingState.FAILED_RETRYABLE,
        PublishingState.FAILED_TERMINAL,
        PublishingState.RECONCILIATION_REQUIRED,
    },
    PublishingState.THUMBNAIL_APPLIED: {
        PublishingState.PUBLISHED,
        PublishingState.SCHEDULED,
        PublishingState.FAILED_RETRYABLE,
        PublishingState.FAILED_TERMINAL,
        PublishingState.RECONCILIATION_REQUIRED,
    },
    PublishingState.SCHEDULED: {
        PublishingState.PUBLISHED,
        PublishingState.FAILED_TERMINAL,
        PublishingState.RECONCILIATION_REQUIRED,
    },
    PublishingState.RECONCILIATION_REQUIRED: {
        PublishingState.UPLOADED,
        PublishingState.METADATA_APPLIED,
        PublishingState.THUMBNAIL_APPLIED,
        PublishingState.SCHEDULED,
        PublishingState.PUBLISHED,
        PublishingState.FAILED_RETRYABLE,
        PublishingState.FAILED_TERMINAL,
    },
    PublishingState.FAILED_RETRYABLE: {
        PublishingState.SUBMITTING,
        PublishingState.UPLOADED,
        PublishingState.METADATA_APPLIED,
        PublishingState.THUMBNAIL_APPLIED,
        PublishingState.RECONCILIATION_REQUIRED,
        PublishingState.FAILED_TERMINAL,
    },
    PublishingState.PUBLISHED: set(),  # Terminal success
    PublishingState.FAILED_TERMINAL: set(),  # Terminal failure
}


class PublishingStateMachine:
    """Enforces deterministic transitions for the publishing lifecycle."""

    @classmethod
    def can_transition(cls, from_state: PublishingState, to_state: PublishingState) -> bool:
        """Check if transition is valid."""
        return to_state in ALLOWED_TRANSITIONS.get(from_state, set())

    @classmethod
    def transition(
        cls, from_state: PublishingState, to_state: PublishingState, reason: str = ""
    ) -> PublishingState:
        """Validate and execute state transition, failing closed on illegal transitions."""
        if not cls.can_transition(from_state, to_state):
            raise IllegalPublishStateTransitionError(from_state, to_state, reason)
        return to_state
