"""NarrativePlan application service and repository.

Provides lifecycle management, versioning, persistence, and deterministic
validation operations for NarrativePlan authorities.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from omega.application.narrative_plan_validator import NarrativePlanValidator
from omega.config import get_settings
from omega.domain.narrative_plan import (
    FORMAT_PROFILE_CONSTRAINTS,
    GroundingReference,
    GroundingType,
    InformationDensity,
    NarrativeFormatProfile,
    NarrativePlan,
    NarrativePlanStatus,
    NarrativePlanValidationResult,
    NarrativeSection,
    NarrativeSectionRole,
)
from omega.infrastructure.models import (
    ContentGenerationRequest,
    NarrativeGroundingCitation as NarrativeGroundingCitationModel,
    NarrativePlan as NarrativePlanModel,
    NarrativeSection as NarrativeSectionModel,
)
from omega.logging import get_logger

logger = get_logger("omega-narrative-plan-service")


class HistoricalRevisionImmutableError(RuntimeError):
    """Raised when an operation attempts to mutate the narrative content of a superseded/historical revision."""


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
        self._lock = threading.Lock()

    def save(self, plan: NarrativePlan) -> NarrativePlan:
        with self._lock:
            existing = self._plans.get(plan.id)
            if existing and (not existing.is_current or existing.status == NarrativePlanStatus.SUPERSEDED):
                if (
                    existing.format_profile != plan.format_profile
                    or existing.target_duration_seconds != plan.target_duration_seconds
                    or existing.estimated_duration_seconds != plan.estimated_duration_seconds
                    or len(existing.sections) != len(plan.sections)
                ):
                    raise HistoricalRevisionImmutableError(
                        f"NarrativePlan '{plan.id}' is historical/superseded and its narrative content is immutable."
                    )

            if plan.is_current:
                for p in self._plans.values():
                    if p.content_generation_request_id == plan.content_generation_request_id and p.id != plan.id:
                        p.is_current = False
            self._plans[plan.id] = plan
            return plan

    def get(self, plan_id: UUID) -> NarrativePlan | None:
        with self._lock:
            return self._plans.get(plan_id)

    def list_by_request(self, content_generation_request_id: UUID) -> list[NarrativePlan]:
        with self._lock:
            plans = [
                p for p in self._plans.values()
                if p.content_generation_request_id == content_generation_request_id
            ]
            return sorted(plans, key=lambda x: x.version)

    def get_current_for_request(self, content_generation_request_id: UUID) -> NarrativePlan | None:
        with self._lock:
            for p in self._plans.values():
                if p.content_generation_request_id == content_generation_request_id and p.is_current:
                    return p
            plans = [
                p for p in self._plans.values()
                if p.content_generation_request_id == content_generation_request_id
            ]
            return sorted(plans, key=lambda x: x.version)[-1] if plans else None

    def create_revision_atomic(
        self,
        plan_id: UUID,
        new_sections: list[NarrativeSection],
        new_target_duration_seconds: int | None = None,
        notes: str | None = None,
        status: NarrativePlanStatus = NarrativePlanStatus.DRAFT,
    ) -> NarrativePlan:
        with self._lock:
            existing = self._plans.get(plan_id)
            if not existing:
                raise ValueError(f"NarrativePlan '{plan_id}' does not exist.")

            req_plans = [
                p for p in self._plans.values()
                if p.content_generation_request_id == existing.content_generation_request_id
            ]
            max_version = max((p.version for p in req_plans), default=0)
            next_version = max_version + 1

            for p in req_plans:
                if p.is_current:
                    p.is_current = False
                    if p.status != NarrativePlanStatus.SUPERSEDED:
                        p.status = NarrativePlanStatus.SUPERSEDED
                    p.updated_at = datetime.now(UTC)

            new_plan = existing.create_revision(
                new_sections=new_sections,
                new_target_duration_seconds=new_target_duration_seconds,
                notes=notes,
            )
            object.__setattr__(new_plan, "version", next_version)
            object.__setattr__(new_plan, "status", status)
            object.__setattr__(new_plan, "is_current", True)
            self._plans[new_plan.id] = new_plan
            return new_plan


class FileBackedNarrativePlanRepository:
    """JSON file-backed repository for NarrativePlan authorities in local non-production environments."""

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

    def create_revision_atomic(
        self,
        plan_id: UUID,
        new_sections: list[NarrativeSection],
        new_target_duration_seconds: int | None = None,
        notes: str | None = None,
        status: NarrativePlanStatus = NarrativePlanStatus.DRAFT,
    ) -> NarrativePlan:
        new_plan = self._memory.create_revision_atomic(
            plan_id=plan_id,
            new_sections=new_sections,
            new_target_duration_seconds=new_target_duration_seconds,
            notes=notes,
            status=status,
        )
        file_path = self.storage_dir / f"{new_plan.id}.json"
        file_path.write_text(json.dumps(new_plan.to_serializable_dict(), indent=2), encoding="utf-8")
        return new_plan


def _orm_to_domain(model: NarrativePlanModel) -> NarrativePlan:
    """Convert SQLAlchemy NarrativePlan ORM model to domain NarrativePlan."""
    sections: list[NarrativeSection] = []
    sorted_sections = sorted(model.sections, key=lambda s: s.section_order)
    for s in sorted_sections:
        grounding_refs: list[GroundingReference] = []
        for gc in getattr(s, "grounding_citations", []) or []:
            grounding_refs.append(
                GroundingReference(
                    research_brief_id=gc.research_brief_id,
                    claim_id=gc.claim_id,
                    evidence_id=gc.evidence_id,
                    source_id=gc.source_id,
                    grounding_type=GroundingType(gc.grounding_type),
                    description=gc.description,
                )
            )
        sections.append(
            NarrativeSection(
                id=s.id,
                section_order=s.section_order,
                role=NarrativeSectionRole(s.role),
                objective=s.objective,
                key_information=list(s.key_information or []),
                grounding_references=grounding_refs,
                target_duration_seconds=s.target_duration_seconds,
                target_information_density=InformationDensity(s.target_information_density),
                open_loop_intent=s.open_loop_intent,
                promise_id=s.promise_id,
                payoff_reference=s.payoff_reference,
                notes=s.notes,
            )
        )

    return NarrativePlan(
        id=model.id,
        content_generation_request_id=model.content_generation_request_id,
        topic_candidate_id=model.topic_candidate_id,
        research_brief_id=model.research_brief_id,
        channel_dna_revision_id=model.channel_dna_revision_id,
        version=model.version,
        status=NarrativePlanStatus(model.status),
        format_profile=NarrativeFormatProfile(model.format_profile),
        target_duration_seconds=model.target_duration_seconds,
        estimated_duration_seconds=model.estimated_duration_seconds,
        is_current=model.is_current,
        supersedes_plan_id=model.supersedes_plan_id,
        schema_version=model.schema_version,
        sections=sections,
        metadata=dict(model.metadata_ or {}),
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _domain_to_orm(plan: NarrativePlan) -> NarrativePlanModel:
    """Convert domain NarrativePlan to SQLAlchemy NarrativePlan ORM model."""
    plan_model = NarrativePlanModel(
        id=plan.id,
        content_generation_request_id=plan.content_generation_request_id,
        topic_candidate_id=plan.topic_candidate_id,
        research_brief_id=plan.research_brief_id,
        channel_dna_revision_id=plan.channel_dna_revision_id,
        version=plan.version,
        is_current=plan.is_current,
        supersedes_plan_id=plan.supersedes_plan_id,
        status=plan.status.value,
        format_profile=plan.format_profile.value,
        target_duration_seconds=plan.target_duration_seconds,
        estimated_duration_seconds=plan.estimated_duration_seconds,
        schema_version=plan.schema_version,
        metadata_=dict(plan.metadata or {}),
    )
    for s in plan.sections:
        sec_model = NarrativeSectionModel(
            id=s.id,
            narrative_plan_id=plan.id,
            section_order=s.section_order,
            role=s.role.value,
            objective=s.objective,
            key_information=list(s.key_information or []),
            target_duration_seconds=s.target_duration_seconds,
            target_information_density=s.target_information_density.value,
            open_loop_intent=s.open_loop_intent,
            promise_id=s.promise_id,
            payoff_reference=s.payoff_reference,
            notes=s.notes,
        )
        for g in s.grounding_references:
            cite_model = NarrativeGroundingCitationModel(
                id=uuid.uuid4(),
                narrative_section_id=s.id,
                research_brief_id=g.research_brief_id,
                claim_id=g.claim_id,
                evidence_id=g.evidence_id,
                source_id=g.source_id,
                grounding_type=g.grounding_type.value,
                description=g.description,
            )
            sec_model.grounding_citations.append(cite_model)
        plan_model.sections.append(sec_model)
    return plan_model


class PostgresNarrativePlanRepository:
    """Authoritative PostgreSQL repository for NarrativePlan persistence with transactional versioning."""

    def __init__(
        self,
        session_factory: Callable[[], Session] | None = None,
        session: Session | None = None,
    ) -> None:
        if session is not None:
            self._session_factory = None
            self._session = session
        elif session_factory is not None:
            self._session_factory = session_factory
            self._session = None
        else:
            from omega.infrastructure.database_sync import SyncSessionLocal
            self._session_factory = SyncSessionLocal
            self._session = None

    def _get_session(self) -> tuple[Session, bool]:
        if self._session is not None:
            return self._session, False
        return self._session_factory(), True

    def get(self, plan_id: UUID) -> NarrativePlan | None:
        session, is_owned = self._get_session()
        try:
            stmt = (
                select(NarrativePlanModel)
                .where(NarrativePlanModel.id == plan_id)
                .options(
                    selectinload(NarrativePlanModel.sections).selectinload(
                        NarrativeSectionModel.grounding_citations
                    )
                )
            )
            model = session.scalars(stmt).first()
            if not model:
                return None
            return _orm_to_domain(model)
        finally:
            if is_owned:
                session.close()

    def get_current_for_request(self, content_generation_request_id: UUID) -> NarrativePlan | None:
        session, is_owned = self._get_session()
        try:
            stmt = (
                select(NarrativePlanModel)
                .where(
                    NarrativePlanModel.content_generation_request_id == content_generation_request_id,
                    NarrativePlanModel.is_current.is_(True),
                )
                .options(
                    selectinload(NarrativePlanModel.sections).selectinload(
                        NarrativeSectionModel.grounding_citations
                    )
                )
            )
            model = session.scalars(stmt).first()
            if not model:
                return None
            return _orm_to_domain(model)
        finally:
            if is_owned:
                session.close()

    def list_by_request(self, content_generation_request_id: UUID) -> list[NarrativePlan]:
        session, is_owned = self._get_session()
        try:
            stmt = (
                select(NarrativePlanModel)
                .where(NarrativePlanModel.content_generation_request_id == content_generation_request_id)
                .order_by(NarrativePlanModel.version.asc())
                .options(
                    selectinload(NarrativePlanModel.sections).selectinload(
                        NarrativeSectionModel.grounding_citations
                    )
                )
            )
            models = session.scalars(stmt).all()
            return [_orm_to_domain(m) for m in models]
        finally:
            if is_owned:
                session.close()

    def save(self, plan: NarrativePlan) -> NarrativePlan:
        session, is_owned = self._get_session()
        try:
            stmt = (
                select(NarrativePlanModel)
                .where(NarrativePlanModel.id == plan.id)
                .options(
                    selectinload(NarrativePlanModel.sections).selectinload(
                        NarrativeSectionModel.grounding_citations
                    )
                )
            )
            existing = session.scalars(stmt).first()
            if existing:
                # Immutability Check: historical/superseded revisions cannot have content mutated
                if not existing.is_current or existing.status == NarrativePlanStatus.SUPERSEDED.value:
                    if (
                        existing.format_profile != plan.format_profile.value
                        or existing.target_duration_seconds != plan.target_duration_seconds
                        or existing.estimated_duration_seconds != plan.estimated_duration_seconds
                        or len(existing.sections) != len(plan.sections)
                    ):
                        raise HistoricalRevisionImmutableError(
                            f"NarrativePlan '{plan.id}' is historical/superseded and its narrative content is immutable. "
                            "Create a new revision instead."
                        )
                existing.status = plan.status.value
                existing.is_current = plan.is_current
                existing.updated_at = datetime.now(UTC)
                session.flush()
            else:
                # Lock parent if ContentGenerationRequest exists to serialize first plan creation
                session.execute(
                    select(ContentGenerationRequest.id)
                    .where(ContentGenerationRequest.id == plan.content_generation_request_id)
                    .with_for_update()
                )
                if plan.is_current:
                    # Demote any existing current plan for this request
                    curr_stmt = (
                        select(NarrativePlanModel)
                        .where(
                            NarrativePlanModel.content_generation_request_id == plan.content_generation_request_id,
                            NarrativePlanModel.is_current.is_(True),
                        )
                        .with_for_update()
                    )
                    for curr in session.scalars(curr_stmt).all():
                        curr.is_current = False
                        if curr.status != NarrativePlanStatus.SUPERSEDED.value:
                            curr.status = NarrativePlanStatus.SUPERSEDED.value
                        curr.updated_at = datetime.now(UTC)
                    session.flush()

                new_model = _domain_to_orm(plan)
                session.add(new_model)
                session.flush()

            if is_owned:
                session.commit()
            return plan
        except Exception:
            if is_owned:
                session.rollback()
            raise
        finally:
            if is_owned:
                session.close()

    def create_revision_atomic(
        self,
        plan_id: UUID,
        new_sections: list[NarrativeSection],
        new_target_duration_seconds: int | None = None,
        notes: str | None = None,
        status: NarrativePlanStatus = NarrativePlanStatus.DRAFT,
    ) -> NarrativePlan:
        """Create and persist revision N+1 atomically with row-level parent locking."""
        session, is_owned = self._get_session()
        try:
            # 1. Load base plan
            plan_stmt = (
                select(NarrativePlanModel)
                .where(NarrativePlanModel.id == plan_id)
                .options(
                    selectinload(NarrativePlanModel.sections).selectinload(
                        NarrativeSectionModel.grounding_citations
                    )
                )
            )
            base_plan = session.scalars(plan_stmt).first()
            if not base_plan:
                raise ValueError(f"NarrativePlan '{plan_id}' does not exist.")

            req_id = base_plan.content_generation_request_id

            # 2. Lock owning ContentGenerationRequest parent row
            session.execute(
                select(ContentGenerationRequest.id)
                .where(ContentGenerationRequest.id == req_id)
                .with_for_update()
            )

            # 3. Load all plans for this request with row locks to determine monotonic next version
            plans_stmt = (
                select(NarrativePlanModel)
                .where(NarrativePlanModel.content_generation_request_id == req_id)
                .order_by(NarrativePlanModel.version.asc())
                .with_for_update()
            )
            existing_plans = session.scalars(plans_stmt).all()
            max_version = max((p.version for p in existing_plans), default=0)
            next_version = max_version + 1

            # 4. Demote all previous plans to non-current / superseded
            for p in existing_plans:
                if p.is_current:
                    p.is_current = False
                    if p.status != NarrativePlanStatus.SUPERSEDED.value:
                        p.status = NarrativePlanStatus.SUPERSEDED.value
                    p.updated_at = datetime.now(UTC)
            session.flush()

            # 5. Build and insert new revision
            target_dur = new_target_duration_seconds or base_plan.target_duration_seconds
            est_dur = sum(s.target_duration_seconds for s in new_sections)

            new_plan_id = uuid.uuid4()
            new_plan_model = NarrativePlanModel(
                id=new_plan_id,
                content_generation_request_id=req_id,
                topic_candidate_id=base_plan.topic_candidate_id,
                research_brief_id=base_plan.research_brief_id,
                channel_dna_revision_id=base_plan.channel_dna_revision_id,
                version=next_version,
                is_current=True,
                supersedes_plan_id=base_plan.id,
                status=status.value,
                format_profile=base_plan.format_profile,
                target_duration_seconds=target_dur,
                estimated_duration_seconds=est_dur,
                schema_version=base_plan.schema_version,
                metadata_={
                    **dict(base_plan.metadata_ or {}),
                    "revision_notes": notes,
                    "superseded_plan_id": str(base_plan.id),
                    "created_from_version": base_plan.version,
                },
            )

            for s in new_sections:
                sec_id = uuid.uuid4()
                sec_model = NarrativeSectionModel(
                    id=sec_id,
                    narrative_plan_id=new_plan_id,
                    section_order=s.section_order,
                    role=s.role.value,
                    objective=s.objective,
                    key_information=list(s.key_information or []),
                    target_duration_seconds=s.target_duration_seconds,
                    target_information_density=s.target_information_density.value,
                    open_loop_intent=s.open_loop_intent,
                    promise_id=s.promise_id,
                    payoff_reference=s.payoff_reference,
                    notes=s.notes,
                )
                for g in s.grounding_references:
                    cite_model = NarrativeGroundingCitationModel(
                        id=uuid.uuid4(),
                        narrative_section_id=sec_id,
                        research_brief_id=g.research_brief_id,
                        claim_id=g.claim_id,
                        evidence_id=g.evidence_id,
                        source_id=g.source_id,
                        grounding_type=g.grounding_type.value,
                        description=g.description,
                    )
                    sec_model.grounding_citations.append(cite_model)
                new_plan_model.sections.append(sec_model)

            session.add(new_plan_model)
            session.flush()

            if is_owned:
                session.commit()

            return _orm_to_domain(new_plan_model)
        except Exception:
            if is_owned:
                session.rollback()
            raise
        finally:
            if is_owned:
                session.close()

    def create_first_plan_atomic(
        self,
        content_generation_request_id: UUID,
        channel_dna_revision_id: UUID,
        format_profile: NarrativeFormatProfile,
        target_duration_seconds: int,
        sections: list[NarrativeSection] | None = None,
        topic_candidate_id: UUID | None = None,
        research_brief_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
        status: NarrativePlanStatus = NarrativePlanStatus.DRAFT,
    ) -> NarrativePlan:
        """Create and persist version 1 atomically with row-level parent locking."""
        session, is_owned = self._get_session()
        try:
            # 1. Lock owning ContentGenerationRequest parent row if present
            session.execute(
                select(ContentGenerationRequest.id)
                .where(ContentGenerationRequest.id == content_generation_request_id)
                .with_for_update()
            )

            # 2. Check for any existing plans for this request
            plans_stmt = (
                select(NarrativePlanModel)
                .where(NarrativePlanModel.content_generation_request_id == content_generation_request_id)
                .order_by(NarrativePlanModel.version.asc())
                .with_for_update()
            )
            existing_plans = session.scalars(plans_stmt).all()
            for p in existing_plans:
                if p.is_current:
                    p.is_current = False
                    if p.status != NarrativePlanStatus.SUPERSEDED.value:
                        p.status = NarrativePlanStatus.SUPERSEDED.value
                    p.updated_at = datetime.now(UTC)
            session.flush()

            sec_list = sections or []
            est_dur = sum(s.target_duration_seconds for s in sec_list)
            new_plan_id = uuid.uuid4()

            new_plan_model = NarrativePlanModel(
                id=new_plan_id,
                content_generation_request_id=content_generation_request_id,
                topic_candidate_id=topic_candidate_id,
                research_brief_id=research_brief_id,
                channel_dna_revision_id=channel_dna_revision_id,
                version=1,
                is_current=True,
                supersedes_plan_id=None,
                status=status.value,
                format_profile=format_profile.value,
                target_duration_seconds=target_duration_seconds,
                estimated_duration_seconds=est_dur,
                schema_version=1,
                metadata_=dict(metadata or {}),
            )

            for s in sec_list:
                sec_id = s.id or uuid.uuid4()
                sec_model = NarrativeSectionModel(
                    id=sec_id,
                    narrative_plan_id=new_plan_id,
                    section_order=s.section_order,
                    role=s.role.value,
                    objective=s.objective,
                    key_information=list(s.key_information or []),
                    target_duration_seconds=s.target_duration_seconds,
                    target_information_density=s.target_information_density.value,
                    open_loop_intent=s.open_loop_intent,
                    promise_id=s.promise_id,
                    payoff_reference=s.payoff_reference,
                    notes=s.notes,
                )
                for g in s.grounding_references:
                    cite_model = NarrativeGroundingCitationModel(
                        id=uuid.uuid4(),
                        narrative_section_id=sec_id,
                        research_brief_id=g.research_brief_id,
                        claim_id=g.claim_id,
                        evidence_id=g.evidence_id,
                        source_id=g.source_id,
                        grounding_type=g.grounding_type.value,
                        description=g.description,
                    )
                    sec_model.grounding_citations.append(cite_model)
                new_plan_model.sections.append(sec_model)

            session.add(new_plan_model)
            session.flush()

            if is_owned:
                session.commit()

            return _orm_to_domain(new_plan_model)
        except Exception:
            if is_owned:
                session.rollback()
            raise
        finally:
            if is_owned:
                session.close()


def get_narrative_plan_repository() -> NarrativePlanRepository:
    """Return authoritative NarrativePlan repository according to environment."""
    settings = get_settings()
    if settings.environment.lower() in ("production", "prod"):
        from omega.infrastructure.database_sync import SyncSessionLocal
        return PostgresNarrativePlanRepository(session_factory=SyncSessionLocal)
    return _default_repository


# Global default in-memory repository instance for test runtime
_default_repository = InMemoryNarrativePlanRepository()


class NarrativePlanService:
    """Application service for NarrativePlan operations."""

    def __init__(
        self,
        repository: NarrativePlanRepository | None = None,
        validator: NarrativePlanValidator | None = None,
    ) -> None:
        settings = get_settings()
        is_production = settings.environment.lower() in ("production", "prod")

        if is_production:
            if isinstance(repository, FileBackedNarrativePlanRepository):
                raise RuntimeError(
                    "FileBackedNarrativePlanRepository is prohibited in production. "
                    "PostgreSQL repository authority is required."
                )
            if repository is None:
                from omega.infrastructure.database_sync import SyncSessionLocal
                repository = PostgresNarrativePlanRepository(session_factory=SyncSessionLocal)

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

        candidate = NarrativePlan(
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
        status = NarrativePlanStatus.DRAFT
        if auto_validate and candidate.sections:
            val_result = self.validator.validate(candidate)
            if val_result.is_valid:
                status = NarrativePlanStatus.VALIDATED
            else:
                status = NarrativePlanStatus.REJECTED
        candidate.status = status

        if hasattr(self.repository, "create_first_plan_atomic"):
            plan = self.repository.create_first_plan_atomic(
                content_generation_request_id=content_generation_request_id,
                channel_dna_revision_id=channel_dna_revision_id,
                format_profile=format_profile,
                target_duration_seconds=target_dur,
                sections=sec_list,
                topic_candidate_id=topic_candidate_id,
                research_brief_id=research_brief_id,
                metadata=metadata,
                status=status,
            )
        else:
            self.repository.save(candidate)
            plan = candidate

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

        candidate = existing.create_revision(
            new_sections=new_sections,
            new_target_duration_seconds=new_target_duration_seconds,
            notes=notes,
        )

        val_result = None
        status = NarrativePlanStatus.DRAFT
        if auto_validate:
            val_result = self.validator.validate(candidate)
            if val_result.is_valid:
                status = NarrativePlanStatus.VALIDATED
            else:
                status = NarrativePlanStatus.REJECTED

        if hasattr(self.repository, "create_revision_atomic"):
            new_plan = self.repository.create_revision_atomic(
                plan_id=plan_id,
                new_sections=new_sections,
                new_target_duration_seconds=new_target_duration_seconds,
                notes=notes,
                status=status,
            )
        else:
            candidate.status = status
            existing.status = NarrativePlanStatus.SUPERSEDED
            existing.is_current = False
            existing.updated_at = datetime.now(UTC)
            self.repository.save(existing)
            self.repository.save(candidate)
            new_plan = candidate

        logger.info(
            "Created NarrativePlan revision",
            plan_id=str(new_plan.id),
            version=new_plan.version,
            supersedes=str(existing.id),
            status=new_plan.status.value,
        )
        return new_plan, val_result
