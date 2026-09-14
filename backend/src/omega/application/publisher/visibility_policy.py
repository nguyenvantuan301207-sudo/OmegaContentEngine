"""Controlled Visibility Policy for OMEGA-011 Publisher.

Authoritative policy engine governing publication visibility:
- PRIVATE: permitted when standard approval and safety gates pass.
- UNLISTED: requires explicit channel/policy permission + valid human approval.
- PUBLIC: requires explicit channel/policy permission + valid human approval.
- PRIVATE CANARY MODE: strictly fail-closed, rejecting UNLISTED and PUBLIC requests.
- REQUESTED vs EFFECTIVE PRIVACY: explicit separation; zero silent upward upgrades.
- PROVIDER RESTRICTION (Unverified project): default fail-closed unless explicit
  fallback policy is configured, producing an auditable downgrade to PRIVATE.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass

from omega.config import Settings
from omega.domain.publisher import (
    PrivacyStatus,
    PublisherErrorCategory,
    compute_publish_intent_checksum,
)
from omega.infrastructure.models import (
    Channel,
    PlatformAccount,
    PublishIntent,
)

PRIVACY_HIERARCHY: dict[PrivacyStatus, int] = {
    PrivacyStatus.PRIVATE: 0,
    PrivacyStatus.UNLISTED: 1,
    PrivacyStatus.PUBLIC: 2,
}


@dataclass(frozen=True)
class VisibilityDecision:
    """Decision outcome of ControlledVisibilityPolicy evaluation."""

    is_allowed: bool
    requested_privacy: PrivacyStatus
    effective_privacy: PrivacyStatus
    fallback_applied: bool = False
    fallback_reason: str | None = None
    error_category: PublisherErrorCategory | None = None
    error_message: str | None = None


class ControlledVisibilityPolicy:
    """Authoritative evaluation engine for publication visibility."""

    @classmethod
    def resolve_allowed_visibilities(
        cls,
        channel: Channel | None,
        intent: PublishIntent | None,
    ) -> set[PrivacyStatus]:
        """Resolve the set of permitted privacy statuses for a channel and intent.

        PRIVATE is always in the base set.
        UNLISTED and PUBLIC are only permitted if explicitly authorized by:
        1. channel.metadata_
        2. intent.platform_custom_options
        """
        allowed: set[PrivacyStatus] = {PrivacyStatus.PRIVATE}

        # 1. Check channel metadata if present
        if channel is not None and isinstance(channel.metadata_, dict):
            c_meta = channel.metadata_
            vis_list = c_meta.get("allowed_visibilities") or c_meta.get("allowed_visibility")
            if isinstance(vis_list, list):
                for v in vis_list:
                    with contextlib.suppress(ValueError):
                        allowed.add(PrivacyStatus(str(v).upper()))
            if c_meta.get("policy_permit_unlisted") is True or c_meta.get("permit_unlisted") is True:
                allowed.add(PrivacyStatus.UNLISTED)
            if c_meta.get("policy_permit_public") is True or c_meta.get("permit_public") is True:
                allowed.add(PrivacyStatus.PUBLIC)

        # 2. Check intent platform_custom_options if present
        if intent is not None and isinstance(intent.platform_custom_options, dict):
            i_opts = intent.platform_custom_options
            vis_list = i_opts.get("allowed_visibilities") or i_opts.get("allowed_visibility")
            if isinstance(vis_list, list):
                for v in vis_list:
                    with contextlib.suppress(ValueError):
                        allowed.add(PrivacyStatus(str(v).upper()))
            if i_opts.get("policy_permit_unlisted") is True or i_opts.get("permit_unlisted") is True:
                allowed.add(PrivacyStatus.UNLISTED)
            if i_opts.get("policy_permit_public") is True or i_opts.get("permit_public") is True:
                allowed.add(PrivacyStatus.PUBLIC)

        return allowed

    @classmethod
    def evaluate_visibility(
        cls,
        *,
        intent: PublishIntent,
        channel: Channel | None,
        account: PlatformAccount | None,
        settings: Settings,
    ) -> VisibilityDecision:
        """Evaluate visibility contract and determine allowed status and effective visibility."""
        # 1. Validate requested privacy string
        try:
            requested = PrivacyStatus(intent.requested_privacy_status)
        except (ValueError, TypeError) as exc:
            return VisibilityDecision(
                is_allowed=False,
                requested_privacy=PrivacyStatus.PRIVATE,
                effective_privacy=PrivacyStatus.PRIVATE,
                error_category=PublisherErrorCategory.PRIVACY_RESTRICTION_BLOCKED,
                error_message=f"Invalid requested privacy status: {exc}",
            )

        # 2. PRIVATE CANARY MODE check (Fail closed for UNLISTED / PUBLIC)
        if settings.publisher_private_canary_mode and requested != PrivacyStatus.PRIVATE:
            return VisibilityDecision(
                is_allowed=False,
                requested_privacy=requested,
                effective_privacy=requested,
                error_category=PublisherErrorCategory.PRIVACY_RESTRICTION_BLOCKED,
                error_message=(
                    "Private canary mode rejects PUBLIC and UNLISTED requests; "
                    "submit an explicitly PRIVATE intent."
                ),
            )

        # 3. Human Approval & Checksum Gate
        # Verify that the intent was explicitly approved for this requested privacy status
        # and has not been tampered with or become stale.
        if intent.state not in ("APPROVED", "CLAIMED"):
            return VisibilityDecision(
                is_allowed=False,
                requested_privacy=requested,
                effective_privacy=requested,
                error_category=PublisherErrorCategory.PRIVACY_RESTRICTION_BLOCKED,
                error_message=f"PublishIntent is in unapproved state '{intent.state}'.",
            )

        if requested in (PrivacyStatus.PUBLIC, PrivacyStatus.UNLISTED):
            if not intent.intent_checksum:
                return VisibilityDecision(
                    is_allowed=False,
                    requested_privacy=requested,
                    effective_privacy=requested,
                    error_category=PublisherErrorCategory.PRIVACY_RESTRICTION_BLOCKED,
                    error_message=(
                        f"Explicit approval evidence required for '{requested.value}' publication."
                    ),
                )
            expected_checksum = compute_publish_intent_checksum(
                task_id=intent.task_id,
                media_artifact_checksum=intent.media_artifact_checksum,
                channel_dna_revision_id=intent.channel_dna_revision_id,
                platform=account.platform if account else "YOUTUBE",
                title=intent.title,
                description=intent.description,
                tags=intent.tags or [],
                requested_privacy_status=intent.requested_privacy_status,
                category_id=intent.category_id,
                made_for_kids=intent.made_for_kids,
                platform_custom_options=intent.platform_custom_options,
            )
            if intent.intent_checksum != expected_checksum:
                return VisibilityDecision(
                    is_allowed=False,
                    requested_privacy=requested,
                    effective_privacy=requested,
                    error_category=PublisherErrorCategory.PRIVACY_RESTRICTION_BLOCKED,
                    error_message=(
                        "PublishIntent approval checksum mismatch: intent parameters "
                        "or visibility were altered after approval."
                    ),
                )

        # 4. Explicit Policy Permission Gate for UNLISTED and PUBLIC
        allowed_visibilities = cls.resolve_allowed_visibilities(channel=channel, intent=intent)
        if requested not in allowed_visibilities:
            return VisibilityDecision(
                is_allowed=False,
                requested_privacy=requested,
                effective_privacy=requested,
                error_category=PublisherErrorCategory.PRIVACY_RESTRICTION_BLOCKED,
                error_message=(
                    f"Requested visibility '{requested.value}' is not authorized by channel "
                    f"or publication policy. Explicit permission required."
                ),
            )

        # 5. Provider / Project Restriction Gate (Unverified Google API project)
        options = intent.platform_custom_options or {}
        channel_meta = channel.metadata_ if (channel and isinstance(channel.metadata_, dict)) else {}
        platform = account.platform if account else "YOUTUBE"

        provider_requires_private = False
        if platform == "YOUTUBE":
            project_verified = bool(
                options.get("youtube_project_verified", False)
                or channel_meta.get("youtube_project_verified", False)
            )
            provider_requires_private = not project_verified

        effective_privacy = requested
        fallback_applied = False
        fallback_reason = None

        if requested != PrivacyStatus.PRIVATE and provider_requires_private:
            fallback_allowed = bool(
                options.get("privacy_fallback_allowed", False)
                or channel_meta.get("privacy_fallback_allowed", False)
            )
            if not fallback_allowed:
                return VisibilityDecision(
                    is_allowed=False,
                    requested_privacy=requested,
                    effective_privacy=requested,
                    error_category=PublisherErrorCategory.PRIVACY_RESTRICTION_BLOCKED,
                    error_message=(
                        "Requested privacy status requires verified YouTube API project "
                        "(privacy fallback disabled)."
                    ),
                )
            # Authorized fallback to PRIVATE
            effective_privacy = PrivacyStatus.PRIVATE
            fallback_applied = True
            fallback_reason = "Effective privacy downgraded to PRIVATE per explicit channel fallback policy."

        # 6. Strict Monotonicity: effective_privacy cannot upgrade beyond requested
        if PRIVACY_HIERARCHY[effective_privacy] > PRIVACY_HIERARCHY[requested]:
            return VisibilityDecision(
                is_allowed=False,
                requested_privacy=requested,
                effective_privacy=requested,
                error_category=PublisherErrorCategory.PRIVACY_RESTRICTION_BLOCKED,
                error_message="Privacy upgrade violation: effective visibility cannot exceed requested visibility.",
            )

        return VisibilityDecision(
            is_allowed=True,
            requested_privacy=requested,
            effective_privacy=effective_privacy,
            fallback_applied=fallback_applied,
            fallback_reason=fallback_reason,
        )
