"""Canonical attribution obligations and append-only delivery evidence contracts."""

from __future__ import annotations

import enum
import hashlib
import json
import re
import unicodedata
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

ATTRIBUTION_OBLIGATION_SCHEMA_VERSION = 1
ATTRIBUTION_EVIDENCE_SCHEMA_VERSION = 1
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_WHITESPACE = re.compile(r"\s+")


class AttributionDeliveryChannel(enum.StrEnum):
    """Approved external attribution delivery channels."""

    PHYSICAL_RENDER = "PHYSICAL_RENDER"
    PUBLISH_METADATA = "PUBLISH_METADATA"
    EXPORT_SIDECAR = "EXPORT_SIDECAR"


class AttributionDeliveryState(enum.StrEnum):
    """Aggregate delivery state exposed by artifact-scoped read models."""

    UNKNOWN = "UNKNOWN"
    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


class AttributionEvidenceVerificationState(enum.StrEnum):
    """Verification state of one immutable evidence record."""

    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


class AttributionPlacement(enum.StrEnum):
    """Known placement constraints for one attribution obligation."""

    ARTIFACT_WIDE = "ARTIFACT_WIDE"
    SCENE_ADJACENT = "SCENE_ADJACENT"


class _FrozenContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


def normalize_attribution_text(value: str) -> str:
    """Normalize attribution text without changing non-whitespace content."""
    if not isinstance(value, str):
        raise TypeError("Attribution text must be a string")
    normalized = unicodedata.normalize("NFC", value)
    normalized = _WHITESPACE.sub(" ", normalized).strip()
    if not normalized:
        raise ValueError("Attribution text must be non-empty after normalization")
    return normalized


def _normalize_optional_identity(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = unicodedata.normalize("NFC", str(value)).strip()
    if not normalized:
        return None
    return normalized


def _canonical_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_sha256(value: str, field_name: str) -> None:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{field_name} must be lowercase SHA-256")


class AttributionObligation(_FrozenContract):
    """Immutable attribution duty bound to one rendered artifact and visual."""

    schema_version: Literal[1] = ATTRIBUTION_OBLIGATION_SCHEMA_VERSION
    obligation_id: str
    artifact_id: UUID
    artifact_sha256: str
    scene_index: int = Field(ge=1)
    provider: str | None = None
    provider_asset_id: str | None = None
    visual_content_sha256: str | None = None
    source_asset_identity: str
    source_reference: str | None = None
    attribution_text: str
    attribution_text_sha256: str
    allowed_channels: tuple[AttributionDeliveryChannel, ...] = ()
    placement: AttributionPlacement | None = None
    policy_version: int = Field(default=1, ge=1)

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "artifact_id": str(self.artifact_id).lower(),
            "artifact_sha256": self.artifact_sha256,
            "scene_index": self.scene_index,
            "provider": self.provider,
            "provider_asset_id": self.provider_asset_id,
            "visual_content_sha256": self.visual_content_sha256,
            "source_asset_identity": self.source_asset_identity,
            "source_reference": self.source_reference,
            "attribution_text": self.attribution_text,
            "attribution_text_sha256": self.attribution_text_sha256,
            "allowed_channels": [channel.value for channel in self.allowed_channels],
            "placement": self.placement.value if self.placement else None,
            "policy_version": self.policy_version,
        }

    @model_validator(mode="after")
    def validate_canonical_identity(self) -> AttributionObligation:
        _validate_sha256(self.obligation_id, "obligation_id")
        _validate_sha256(self.artifact_sha256, "artifact_sha256")
        _validate_sha256(self.attribution_text_sha256, "attribution_text_sha256")
        if self.visual_content_sha256 is not None:
            _validate_sha256(self.visual_content_sha256, "visual_content_sha256")
        if self.attribution_text != normalize_attribution_text(self.attribution_text):
            raise ValueError("attribution_text must be canonical")
        expected_text_hash = hashlib.sha256(self.attribution_text.encode("utf-8")).hexdigest()
        if self.attribution_text_sha256 != expected_text_hash:
            raise ValueError("attribution_text_sha256 does not match attribution_text")
        if not self.source_asset_identity.strip():
            raise ValueError("source_asset_identity must be non-empty")
        expected_channels = tuple(sorted(set(self.allowed_channels), key=str))
        if self.allowed_channels != expected_channels:
            raise ValueError("allowed_channels must be unique and canonically ordered")
        if self.obligation_id != _canonical_sha256(self.identity_payload()):
            raise ValueError("obligation_id does not match canonical obligation facts")
        return self


def create_attribution_obligation(
    *,
    artifact_id: UUID,
    artifact_sha256: str,
    scene_index: int,
    attribution_text: str,
    provider: str | None = None,
    provider_asset_id: str | None = None,
    visual_content_sha256: str | None = None,
    template_id: str | None = None,
    source_reference: str | None = None,
    allowed_channels: tuple[AttributionDeliveryChannel, ...] = (),
    placement: AttributionPlacement | None = None,
    policy_version: int = 1,
) -> AttributionObligation:
    """Create one obligation with deterministic normalization and identity."""
    canonical_text = normalize_attribution_text(attribution_text)
    canonical_provider = _normalize_optional_identity(provider)
    canonical_provider_asset_id = _normalize_optional_identity(provider_asset_id)
    canonical_template_id = _normalize_optional_identity(template_id)
    canonical_source_reference = _normalize_optional_identity(source_reference)
    channels = tuple(sorted(set(allowed_channels), key=str))
    if canonical_provider_asset_id:
        source_asset_identity = (
            f"provider:{canonical_provider or 'UNKNOWN'}:{canonical_provider_asset_id}"
        )
    elif visual_content_sha256:
        source_asset_identity = f"sha256:{visual_content_sha256}"
    elif canonical_template_id:
        source_asset_identity = f"template:{canonical_template_id}"
    else:
        source_asset_identity = f"scene:{scene_index}"
    text_hash = hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()
    values: dict[str, Any] = {
        "schema_version": ATTRIBUTION_OBLIGATION_SCHEMA_VERSION,
        "artifact_id": artifact_id,
        "artifact_sha256": artifact_sha256,
        "scene_index": scene_index,
        "provider": canonical_provider,
        "provider_asset_id": canonical_provider_asset_id,
        "visual_content_sha256": visual_content_sha256,
        "source_asset_identity": source_asset_identity,
        "source_reference": canonical_source_reference,
        "attribution_text": canonical_text,
        "attribution_text_sha256": text_hash,
        "allowed_channels": channels,
        "placement": placement,
        "policy_version": policy_version,
    }
    identity_payload = {
        **values,
        "artifact_id": str(artifact_id).lower(),
        "allowed_channels": [channel.value for channel in channels],
        "placement": placement.value if placement else None,
    }
    return AttributionObligation(
        obligation_id=_canonical_sha256(identity_payload),
        **values,
    )


def canonicalize_attribution_obligations(
    obligations: list[AttributionObligation] | tuple[AttributionObligation, ...],
) -> tuple[AttributionObligation, ...]:
    """Return deterministic scene/provider/asset/text obligation order."""
    return tuple(
        sorted(
            obligations,
            key=lambda item: (
                item.scene_index,
                item.provider or "",
                item.source_asset_identity,
                item.attribution_text,
            ),
        )
    )


class AttributionDisplayEntry(_FrozenContract):
    """Display-level grouping that retains every underlying obligation."""

    attribution_text: str
    obligation_ids: tuple[str, ...]


def deduplicate_attribution_display(
    obligations: list[AttributionObligation] | tuple[AttributionObligation, ...],
) -> tuple[AttributionDisplayEntry, ...]:
    """Group exact canonical display text without losing obligation identities."""
    grouped: dict[str, list[str]] = {}
    for obligation in canonicalize_attribution_obligations(obligations):
        grouped.setdefault(obligation.attribution_text, []).append(obligation.obligation_id)
    return tuple(
        AttributionDisplayEntry(
            attribution_text=text,
            obligation_ids=tuple(ids),
        )
        for text, ids in grouped.items()
    )


class AttributionDeliveryEvidence(_FrozenContract):
    """Immutable append-only record of channel-specific delivery evidence."""

    schema_version: Literal[1] = ATTRIBUTION_EVIDENCE_SCHEMA_VERSION
    evidence_id: UUID
    artifact_id: UUID
    artifact_sha256: str
    obligation_ids: tuple[str, ...]
    delivery_channel: AttributionDeliveryChannel
    target_context_id: str
    target_platform: str | None = None
    target_account_id: str | None = None
    delivered_text: str
    delivered_text_sha256: str
    verification_state: AttributionEvidenceVerificationState
    evidence_reference: str
    evidence_checksum: str
    supersedes_evidence_id: UUID | None = None
    recorded_at: datetime

    def checksum_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "artifact_id": str(self.artifact_id).lower(),
            "artifact_sha256": self.artifact_sha256,
            "obligation_ids": list(self.obligation_ids),
            "delivery_channel": self.delivery_channel.value,
            "target_context_id": self.target_context_id,
            "target_platform": self.target_platform,
            "target_account_id": self.target_account_id,
            "delivered_text": self.delivered_text,
            "delivered_text_sha256": self.delivered_text_sha256,
            "verification_state": self.verification_state.value,
            "evidence_reference": self.evidence_reference,
            "supersedes_evidence_id": (
                str(self.supersedes_evidence_id).lower() if self.supersedes_evidence_id else None
            ),
        }

    @model_validator(mode="after")
    def validate_evidence(self) -> AttributionDeliveryEvidence:
        _validate_sha256(self.artifact_sha256, "artifact_sha256")
        _validate_sha256(self.delivered_text_sha256, "delivered_text_sha256")
        _validate_sha256(self.evidence_checksum, "evidence_checksum")
        if not self.obligation_ids:
            raise ValueError("Delivery evidence must cover at least one obligation")
        for obligation_id in self.obligation_ids:
            _validate_sha256(obligation_id, "obligation_id")
        if self.obligation_ids != tuple(sorted(set(self.obligation_ids))):
            raise ValueError("obligation_ids must be unique and canonically ordered")
        if self.delivered_text != normalize_attribution_text(self.delivered_text):
            raise ValueError("delivered_text must be canonical")
        expected_text_hash = hashlib.sha256(self.delivered_text.encode("utf-8")).hexdigest()
        if self.delivered_text_sha256 != expected_text_hash:
            raise ValueError("delivered_text_sha256 does not match delivered_text")
        if not self.target_context_id.strip():
            raise ValueError("target_context_id must be non-empty")
        if not self.evidence_reference.strip():
            raise ValueError("evidence_reference must be non-empty")
        if self.delivery_channel == AttributionDeliveryChannel.PUBLISH_METADATA:
            if not self.target_platform or not self.target_account_id:
                raise ValueError("PUBLISH_METADATA evidence requires platform and account target")
        elif self.target_platform is not None or self.target_account_id is not None:
            raise ValueError("Only PUBLISH_METADATA evidence may carry platform/account targets")
        if self.supersedes_evidence_id == self.evidence_id:
            raise ValueError("Evidence cannot supersede itself")
        if self.recorded_at.tzinfo is None or self.recorded_at.utcoffset() is None:
            raise ValueError("recorded_at must be timezone-aware")
        if self.evidence_checksum != _canonical_sha256(self.checksum_payload()):
            raise ValueError("evidence_checksum does not match canonical evidence facts")
        return self


class AttributionDeliveryEvidenceList(_FrozenContract):
    """Artifact-scoped read envelope for append-only delivery evidence."""

    artifact_id: UUID
    artifact_sha256: str
    required_obligation_ids: tuple[str, ...] = ()
    delivery_state: AttributionDeliveryState
    evidence: tuple[AttributionDeliveryEvidence, ...] = ()

    @model_validator(mode="after")
    def validate_artifact_scope(self) -> AttributionDeliveryEvidenceList:
        _validate_sha256(self.artifact_sha256, "artifact_sha256")
        for item in self.evidence:
            if item.artifact_id != self.artifact_id or item.artifact_sha256 != self.artifact_sha256:
                raise ValueError("Delivery evidence read envelope crossed artifact scope")
        if self.required_obligation_ids != tuple(
            sorted(set(self.required_obligation_ids))
        ):
            raise ValueError("required_obligation_ids must be unique and ordered")
        if self.delivery_state != derive_attribution_delivery_state(
            list(self.evidence), self.required_obligation_ids
        ):
            raise ValueError("Delivery evidence aggregate state is inconsistent")
        return self


def create_attribution_delivery_evidence(
    *,
    evidence_id: UUID,
    artifact_id: UUID,
    artifact_sha256: str,
    obligation_ids: tuple[str, ...],
    delivery_channel: AttributionDeliveryChannel,
    target_context_id: str,
    delivered_text: str,
    verification_state: AttributionEvidenceVerificationState,
    evidence_reference: str,
    recorded_at: datetime,
    target_platform: str | None = None,
    target_account_id: str | None = None,
    supersedes_evidence_id: UUID | None = None,
) -> AttributionDeliveryEvidence:
    """Build immutable evidence facts and their deterministic checksum."""
    canonical_text = normalize_attribution_text(delivered_text)
    canonical_obligation_ids = tuple(sorted(set(obligation_ids)))
    text_hash = hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()
    values: dict[str, Any] = {
        "schema_version": ATTRIBUTION_EVIDENCE_SCHEMA_VERSION,
        "evidence_id": evidence_id,
        "artifact_id": artifact_id,
        "artifact_sha256": artifact_sha256,
        "obligation_ids": canonical_obligation_ids,
        "delivery_channel": delivery_channel,
        "target_context_id": normalize_attribution_text(target_context_id),
        "target_platform": _normalize_optional_identity(target_platform),
        "target_account_id": _normalize_optional_identity(target_account_id),
        "delivered_text": canonical_text,
        "delivered_text_sha256": text_hash,
        "verification_state": verification_state,
        "evidence_reference": normalize_attribution_text(evidence_reference),
        "supersedes_evidence_id": supersedes_evidence_id,
        "recorded_at": recorded_at,
    }
    checksum_payload = {
        "schema_version": ATTRIBUTION_EVIDENCE_SCHEMA_VERSION,
        "artifact_id": str(artifact_id).lower(),
        "artifact_sha256": artifact_sha256,
        "obligation_ids": list(canonical_obligation_ids),
        "delivery_channel": delivery_channel.value,
        "target_context_id": values["target_context_id"],
        "target_platform": values["target_platform"],
        "target_account_id": values["target_account_id"],
        "delivered_text": canonical_text,
        "delivered_text_sha256": text_hash,
        "verification_state": verification_state.value,
        "evidence_reference": values["evidence_reference"],
        "supersedes_evidence_id": (
            str(supersedes_evidence_id).lower() if supersedes_evidence_id else None
        ),
    }
    return AttributionDeliveryEvidence(
        evidence_checksum=_canonical_sha256(checksum_payload),
        **values,
    )


def validate_evidence_coverage(
    evidence: AttributionDeliveryEvidence,
    obligations: list[AttributionObligation] | tuple[AttributionObligation, ...],
) -> None:
    """Fail closed unless evidence is bound to known obligations of one artifact."""
    known = {item.obligation_id: item for item in obligations}
    for obligation_id in evidence.obligation_ids:
        obligation = known.get(obligation_id)
        if obligation is None:
            raise ValueError("Evidence references an unknown attribution obligation")
        if (
            obligation.artifact_id != evidence.artifact_id
            or obligation.artifact_sha256 != evidence.artifact_sha256
        ):
            raise ValueError("Evidence artifact binding does not match obligation")
        if not obligation.allowed_channels:
            raise ValueError("Obligation delivery channels are unresolved")
        if evidence.delivery_channel not in obligation.allowed_channels:
            raise ValueError("Evidence channel is not allowed for obligation")


def derive_attribution_delivery_state(
    evidence: list[AttributionDeliveryEvidence] | tuple[AttributionDeliveryEvidence, ...],
    required_obligation_ids: tuple[str, ...] = (),
) -> AttributionDeliveryState:
    """Derive a conservative aggregate state without inventing legacy evidence."""
    if not evidence:
        return AttributionDeliveryState.UNKNOWN
    states = {item.verification_state for item in evidence}
    if AttributionEvidenceVerificationState.REJECTED in states:
        return AttributionDeliveryState.REJECTED
    if not required_obligation_ids:
        return AttributionDeliveryState.UNVERIFIED
    verified_obligations = {
        obligation_id
        for item in evidence
        if item.verification_state == AttributionEvidenceVerificationState.VERIFIED
        for obligation_id in item.obligation_ids
    }
    if set(required_obligation_ids).issubset(verified_obligations):
        return AttributionDeliveryState.VERIFIED
    return AttributionDeliveryState.UNVERIFIED


__all__ = [
    "ATTRIBUTION_EVIDENCE_SCHEMA_VERSION",
    "ATTRIBUTION_OBLIGATION_SCHEMA_VERSION",
    "AttributionDeliveryChannel",
    "AttributionDeliveryEvidence",
    "AttributionDeliveryEvidenceList",
    "AttributionDeliveryState",
    "AttributionDisplayEntry",
    "AttributionEvidenceVerificationState",
    "AttributionObligation",
    "AttributionPlacement",
    "canonicalize_attribution_obligations",
    "create_attribution_delivery_evidence",
    "create_attribution_obligation",
    "deduplicate_attribution_display",
    "derive_attribution_delivery_state",
    "normalize_attribution_text",
    "validate_evidence_coverage",
]
