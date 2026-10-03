"""NarrativePlan application service and repository.

Provides lifecycle management, versioning, persistence, and deterministic
validation operations for NarrativePlan authorities.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from omega.application.narrative_plan_validator import NarrativePlanValidator
from omega.domain.narrative_plan import (
    FORMAT_PROFILE_CONSTRAINTS,
    NarrativeFormatProfile,
    NarrativePlan,
    NarrativePlanStatus,
    NarrativePlanValidationResult,
    NarrativeSection,
)
from omega.logging import get_logger

logger = get_logger("omega-narrative-plan-service")


class NarrativePlanRepository(Protocol):
    """Protocol for NarrativePlan persistence."""

    def save(self, plan: NarrativePlan) -> NarrativePlan: ...
    def get(self, plan_id: UUID) -> NarrativePlan | None: ...
    def list_by_request(self, content_generation_request_id: UUID) -> list[NarrativePlan]: ...
    def get_current_for_request(self, content_generation_request_id: UUID) -> NarrativePlan | None: ...


class InMemoryNarrativePlanRepository:
    """Thread-safe in-memory store for NarrativePlan authorities."""

    def __init__(self) -> None:
        self._plans: dict[UUID, NarrativePlan] = {}

    def save(self, plan: NarrativePlan) -> NarrativePlan:
        # If this plan is marked is_current, set previous plans for same request to is_current = False
        if plan.is_current:
            for p in self._plans.values():
                if p.content_generation_request_id == plan.content_generation_request_id and p.id != plan.id:
                    p.is_current = False
        self._plans[plan.id] = plan
        return plan

    def get(self, plan_id: UUID) -> NarrativePlan | None:
        return self._plans.get(plan_id)

    def list_by_request(self, content_generation_request_id: UUID) -> list[NarrativePlan]:
        plans = [
            p for p in self._plans.values()
            if p.content_generation_request_id == content_generation_request_id
        ]
        return sorted(plans, key=lambda x: x.version)

    def get_current_for_request(self, content_generation_request_id: UUID) -> NarrativePlan | None:
        for p in self._plans.values():
            if p.content_generation_request_id == content_generation_request_id and p.is_current:
                return p
        # Fallback to highest version if no is_current flag is True
        plans = self.list_by_request(content_generation_request_id)
        return plans[-1] if plans else None


class FileBackedNarrativePlanRepository:
    """JSON file-backed persistent repository for NarrativePlan authorities."""

    def __init__(self, storage_dir: Path | str) -> None:
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._memory = InMemoryNarrativePlanRepository()
        self._load_all()

    def _load_all(self) -> None:
        for f in self.storage_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                plan = NarrativePlan.from_serializable_dict(data)
                self._memory._plans[plan.id] = plan
            except Exception as e:
                logger.warning(f"Failed to load narrative plan from {f}: {e}")

    def save(self, plan: NarrativePlan) -> NarrativePlan:
        self._memory.save(plan)
        file_path = self.storage_dir / f"{plan.id}.json"
        file_path.write_text(json.dumps(plan.to_serializable_dict(), indent=2), encoding="utf-8")
        return plan

    def get(self, plan_id: UUID) -> NarrativePlan | None:
        return self._memory.get(plan_id)

    def list_by_request(self, content_generation_request_id: UUID) -> list[NarrativePlan]:
        return self._memory.list_by_request(content_generation_request_id)

    def get_current_for_request(self, content_generation_request_id: UUID) -> NarrativePlan | None:
        return self._memory.get_current_for_request(content_generation_request_id)


# Global default in-memory repository instance for runtime
_default_repository = InMemoryNarrativePlanRepository()


class NarrativePlanService:
    """Application service for NarrativePlan operations."""

    def __init__(
        self,
        repository: NarrativePlanRepository | None = None,
        validator: NarrativePlanValidator | None = None,
    ) -> None:
        self.repository = repository or _default_repository
        self.validator = validator or NarrativePlanValidator()

    def create_plan(
        self,
        content_generation_request_id: UUID,
        channel_dna_revision_id: UUID,
        format_profile: NarrativeFormatProfile,
        target_duration_seconds: int | None = None,
        sections: list[NarrativeSection] | None = None,
        topic_candidate_id: UUID | None = None,
        research_brief_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
        auto_validate: bool = True,
    ) -> tuple[NarrativePlan, NarrativePlanValidationResult | None]:
        """Create and optionally validate a new NarrativePlan (version 1)."""
        constraints = FORMAT_PROFILE_CONSTRAINTS[format_profile]
        target_dur = target_duration_seconds or constraints.default_duration_seconds
        sec_list = sections or []
        est_dur = sum(s.target_duration_seconds for s in sec_list)

        plan = NarrativePlan(
            id=uuid.uuid4(),
            content_generation_request_id=content_generation_request_id,
            topic_candidate_id=topic_candidate_id,
            research_brief_id=research_brief_id,
            channel_dna_revision_id=channel_dna_revision_id,
            version=1,
            status=NarrativePlanStatus.DRAFT,
            format_profile=format_profile,
            target_duration_seconds=target_dur,
            estimated_duration_seconds=est_dur,
            is_current=True,
            supersedes_plan_id=None,
            schema_version=1,
            sections=sec_list,
            metadata=metadata or {},
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        val_result = None
        if auto_validate and plan.sections:
            val_result = self.validator.validate(plan)
            if val_result.is_valid:
                plan.status = NarrativePlanStatus.VALIDATED
            else:
                plan.status = NarrativePlanStatus.REJECTED

        self.repository.save(plan)
        logger.info(
            "Created NarrativePlan",
            plan_id=str(plan.id),
            version=plan.version,
            profile=plan.format_profile.value,
            sections_count=len(plan.sections),
            status=plan.status.value,
        )
        return plan, val_result

    def validate_plan(
        self,
        plan: NarrativePlan,
        require_grounding: bool | None = None,
    ) -> NarrativePlanValidationResult:
        """Run deterministic validation and update plan status."""
        result = self.validator.validate(plan, require_grounding=require_grounding)
        if result.is_valid:
            plan.status = NarrativePlanStatus.VALIDATED
        else:
            plan.status = NarrativePlanStatus.REJECTED
        plan.updated_at = datetime.now(UTC)
        self.repository.save(plan)
        return result

    def get_plan(self, plan_id: UUID) -> NarrativePlan | None:
        """Retrieve a specific NarrativePlan by ID."""
        return self.repository.get(plan_id)

    def get_current_plan(self, content_generation_request_id: UUID) -> NarrativePlan | None:
        """Retrieve the current active plan version for a content generation request."""
        return self.repository.get_current_for_request(content_generation_request_id)

    def list_plans(self, content_generation_request_id: UUID) -> list[NarrativePlan]:
        """List all revisions of a NarrativePlan for a content generation request."""
        return self.repository.list_by_request(content_generation_request_id)

    def create_revision(
        self,
        plan_id: UUID,
        new_sections: list[NarrativeSection],
        new_target_duration_seconds: int | None = None,
        notes: str | None = None,
        auto_validate: bool = True,
    ) -> tuple[NarrativePlan, NarrativePlanValidationResult | None]:
        """Create and persist revision N+1 of an existing NarrativePlan."""
        existing = self.repository.get(plan_id)
        if not existing:
            raise ValueError(f"NarrativePlan '{plan_id}' does not exist.")

        new_plan = existing.create_revision(
            new_sections=new_sections,
            new_target_duration_seconds=new_target_duration_seconds,
            notes=notes,
        )

        val_result = None
        if auto_validate:
            val_result = self.validator.validate(new_plan)
            if val_result.is_valid:
                new_plan.status = NarrativePlanStatus.VALIDATED
            else:
                new_plan.status = NarrativePlanStatus.REJECTED

        # Mark existing as superseded
        existing.status = NarrativePlanStatus.SUPERSEDED
        existing.is_current = False
        existing.updated_at = datetime.now(UTC)
        self.repository.save(existing)

        # Save new plan
        self.repository.save(new_plan)
        logger.info(
            "Created NarrativePlan revision",
            plan_id=str(new_plan.id),
            version=new_plan.version,
            supersedes=str(existing.id),
            status=new_plan.status.value,
        )
        return new_plan, val_result
