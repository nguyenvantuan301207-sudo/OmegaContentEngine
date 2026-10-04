"""Publish Eligibility Gate for P25-A.

Evaluates upstream production artifacts and evidence against canonical prerequisites:
- current accepted artifact exists and matches request
- RuntimeTruth exists for that artifact (schema_version 4 & matching hash)
- ProductionQAEngine result = PASS
- Guardian accepts the artifact (ALLOW / ALLOW_WITH_WARNING)
- P24-D CreativeQAResult = PASS
- PackagingPlan belongs to same production lineage
- selected title exists and is valid
- physical thumbnail exists and passed validation
- description / metadata exist
- required attribution is preserved into description/metadata
- provider/channel mapping exists and is ACTIVE

Enforces fail-closed evaluation: missing upstream evidence strictly blocks publication.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from uuid import UUID

from omega.domain.creative_qa import CreativeQAResult, CreativeQAStatus
from omega.domain.packaging import PackagingPlan
from omega.domain.production import ProductionQAStatus
from omega.domain.publishing import (
    PublishEligibilityDenialReason,
    PublishEligibilityResult,
)
from omega.infrastructure.models import (
    MediaArtifact,
    PlatformAccount,
    ProductionRequest,
    ProductionRuntimeTruth,
)
from omega.logging import get_logger

logger = get_logger(service="omega-publish-eligibility-gate")


class PublishEligibilityGate:
    """Fail-closed gate ensuring all upstream prerequisites pass before publishing."""

    @classmethod
    def evaluate(
        cls,
        *,
        production_request: ProductionRequest | None,
        artifact: MediaArtifact | None,
        runtime_truth: ProductionRuntimeTruth | None,
        production_qa_status: ProductionQAStatus | str | None,
        guardian_allowed: bool,
        creative_qa_result: CreativeQAResult | None,
        packaging_plan: PackagingPlan | None,
        platform_account: PlatformAccount | None,
        channel_id: UUID | None = None,
        target_description: str | None = None,
        verify_physical_thumbnail: bool = True,
    ) -> PublishEligibilityResult:
        """Evaluate full upstream readiness and return typed, fail-closed eligibility result."""
        reasons: list[PublishEligibilityDenialReason] = []
        evidence: dict[str, Any] = {}

        # 1. MediaArtifact checks
        if artifact is None:
            reasons.append(PublishEligibilityDenialReason.NO_CURRENT_ARTIFACT)
        else:
            evidence["artifact_id"] = str(artifact.id)
            evidence["artifact_state"] = getattr(artifact, "state", None)
            evidence["is_current"] = getattr(artifact, "is_current", False)

            # Check if artifact is current
            if not getattr(artifact, "is_current", False):
                reasons.append(PublishEligibilityDenialReason.STALE_ARTIFACT)

            # Check if accepted
            state_str = str(getattr(artifact, "state", "")).upper()
            if state_str != "ACCEPTED":
                reasons.append(PublishEligibilityDenialReason.NO_CURRENT_ARTIFACT)

            # Check diagnostic / forensic exclusion
            art_type = str(getattr(artifact, "artifact_type", "")).upper()
            if "DIAGNOSTIC" in art_type or "FORENSIC" in art_type:
                reasons.append(PublishEligibilityDenialReason.NO_CURRENT_ARTIFACT)

            # Check lineage with ProductionRequest
            if production_request is None:
                reasons.append(PublishEligibilityDenialReason.PAYLOAD_LINEAGE_MISMATCH)
            elif artifact.production_request_id != production_request.id:
                reasons.append(PublishEligibilityDenialReason.PAYLOAD_LINEAGE_MISMATCH)

        # 2. ProductionRuntimeTruth checks
        if runtime_truth is None or artifact is None:
            reasons.append(PublishEligibilityDenialReason.RUNTIME_TRUTH_MISSING)
        else:
            evidence["runtime_truth_id"] = str(getattr(runtime_truth, "artifact_id", getattr(runtime_truth, "id", "")))
            if runtime_truth.artifact_id != artifact.id:
                reasons.append(PublishEligibilityDenialReason.RUNTIME_TRUTH_MISSING)
            else:
                schema_v = getattr(runtime_truth, "schema_version", None)
                try:
                    if int(schema_v) != 4:
                        reasons.append(PublishEligibilityDenialReason.RUNTIME_TRUTH_MISSING)
                except (ValueError, TypeError):
                    reasons.append(PublishEligibilityDenialReason.RUNTIME_TRUTH_MISSING)

                # Verify payload hash matches artifact hash
                payload = getattr(runtime_truth, "payload", {}) or {}
                truth_hash = payload.get("artifact_sha256") or payload.get("artifact_hash")
                if not truth_hash or str(truth_hash).lower() != str(artifact.content_hash).lower():
                    reasons.append(PublishEligibilityDenialReason.RUNTIME_TRUTH_MISSING)

        # 3. ProductionQAEngine check
        qa_str = (
            production_qa_status.value
            if isinstance(production_qa_status, ProductionQAStatus)
            else str(production_qa_status or "").upper()
        )
        evidence["production_qa_status"] = qa_str
        if qa_str not in (ProductionQAStatus.PASSED.value, "PASS", ProductionQAStatus.PASSED_WITH_WARNINGS.value):
            reasons.append(PublishEligibilityDenialReason.PRODUCTION_QA_NOT_PASS)

        # 4. Guardian gate check
        evidence["guardian_allowed"] = guardian_allowed
        if not guardian_allowed:
            reasons.append(PublishEligibilityDenialReason.GUARDIAN_NOT_ACCEPTED)

        # 5. CreativeQA result check
        if creative_qa_result is None:
            reasons.append(PublishEligibilityDenialReason.CREATIVE_QA_NOT_PASS)
        else:
            evidence["creative_qa_status"] = creative_qa_result.status.value
            evidence["creative_qa_is_accepted"] = creative_qa_result.is_accepted
            if (
                creative_qa_result.status != CreativeQAStatus.PASS
                or not creative_qa_result.is_accepted
            ):
                reasons.append(PublishEligibilityDenialReason.CREATIVE_QA_NOT_PASS)

        # 6. PackagingPlan & lineage checks
        if packaging_plan is None:
            reasons.append(PublishEligibilityDenialReason.PACKAGING_NOT_ACCEPTED)
        else:
            evidence["packaging_plan_id"] = str(packaging_plan.packaging_plan_id)
            # Check lineage
            if production_request is not None and channel_id is not None:
                if packaging_plan.channel_dna_revision_id != getattr(
                    production_request, "channel_dna_revision_id", None
                ) and getattr(production_request, "channel_dna_revision_id", None) is not None:
                    reasons.append(PublishEligibilityDenialReason.PAYLOAD_LINEAGE_MISMATCH)

            # Check selected title
            if (
                not packaging_plan.selected_title
                or not packaging_plan.selected_title.text
                or len(packaging_plan.selected_title.text.strip()) < 5
            ):
                reasons.append(PublishEligibilityDenialReason.PACKAGING_NOT_ACCEPTED)

            # Check description
            desc = target_description or packaging_plan.description
            if not desc or len(desc.strip()) < 10:
                reasons.append(PublishEligibilityDenialReason.PACKAGING_NOT_ACCEPTED)

            # Check physical thumbnail
            thumb_art = packaging_plan.physical_thumbnail_artifact
            if not thumb_art or not thumb_art.file_path:
                reasons.append(PublishEligibilityDenialReason.THUMBNAIL_MISSING)
            elif verify_physical_thumbnail:
                thumb_path = Path(thumb_art.file_path)
                if not thumb_path.is_file() or thumb_path.stat().st_size == 0:
                    reasons.append(PublishEligibilityDenialReason.THUMBNAIL_MISSING)
                elif thumb_art.content_sha256:
                    hasher = hashlib.sha256()
                    with open(thumb_path, "rb") as f:
                        while chunk := f.read(65536):
                            hasher.update(chunk)
                    if hasher.hexdigest().lower() != thumb_art.content_sha256.lower():
                        reasons.append(PublishEligibilityDenialReason.THUMBNAIL_MISSING)

            # 7. Attribution Preservation Check
            if packaging_plan.attribution_block:
                required_block = packaging_plan.attribution_block.strip()
                effective_desc = (target_description or packaging_plan.description or "").strip()
                if required_block not in effective_desc:
                    reasons.append(PublishEligibilityDenialReason.ATTRIBUTION_REQUIRED)

        # 8. Provider / Platform Account checks
        if platform_account is None:
            reasons.append(PublishEligibilityDenialReason.PROVIDER_NOT_CONFIGURED)
        else:
            evidence["platform_account_id"] = str(platform_account.id)
            evidence["account_status"] = getattr(platform_account, "status", None)
            if getattr(platform_account, "status", None) != "ACTIVE":
                reasons.append(PublishEligibilityDenialReason.PROVIDER_NOT_CONFIGURED)
            if channel_id and platform_account.channel_id != channel_id:
                reasons.append(PublishEligibilityDenialReason.PROVIDER_NOT_CONFIGURED)

        # Deduplicate reasons while preserving order
        deduped_reasons: list[PublishEligibilityDenialReason] = []
        for r in reasons:
            if r not in deduped_reasons:
                deduped_reasons.append(r)

        is_eligible = len(deduped_reasons) == 0

        logger.info(
            "Publish eligibility evaluated",
            is_eligible=is_eligible,
            denial_reasons=[r.value for r in deduped_reasons],
        )

        return PublishEligibilityResult(
            is_eligible=is_eligible,
            denial_reasons=tuple(deduped_reasons),
            production_request_id=production_request.id if production_request else None,
            artifact_id=artifact.id if artifact else None,
            channel_id=channel_id or (production_request.channel_id if production_request else None),
            platform_account_id=platform_account.id if platform_account else None,
            packaging_plan_id=packaging_plan.packaging_plan_id if packaging_plan else None,
            creative_qa_result_id=creative_qa_result.result_id if creative_qa_result else None,
            runtime_truth_id=getattr(runtime_truth, "artifact_id", getattr(runtime_truth, "id", None)) if runtime_truth else None,
            channel_dna_revision_id=packaging_plan.channel_dna_revision_id if packaging_plan else None,
            evidence=evidence,
        )
