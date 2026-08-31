"""SQLAlchemy ORM models for database persistence.

Provides declarative models for Jobs, Missions, Tasks, DecisionLogs,
Channels, ChannelDNARevisions, TopicCandidates, TopicMemory, TopicAngles,
ResearchRequests, ResearchSources, ResearchClaims, ClaimEvidence,
ResearchConflicts, and ResearchBriefs.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""

    pass


class Job(Base):
    """Job database model for async background tasks."""

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    queued_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<Job id={self.id} type={self.job_type} state={self.state}>"


# ── OMEGA-003 Channel Manager Models ──


class Channel(Base):
    """Channel database model representing an autonomous operating workspace."""

    __tablename__ = "channels"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(50), nullable=False, default="DRAFT", index=True)
    platform: Mapped[str] = mapped_column(String(50), nullable=False, default="YOUTUBE", index=True)
    platform_channel_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    primary_language: Mapped[str] = mapped_column(String(20), nullable=False, default="en")
    target_region: Mapped[str] = mapped_column(String(10), nullable=False, default="US")
    timezone: Mapped[str] = mapped_column(String(50), nullable=False, default="UTC")
    dna: Mapped[dict] = mapped_column(JSON, nullable=False, server_default="{}")
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, server_default="{}")

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    archived_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    revisions: Mapped[list[ChannelDNARevision]] = relationship(
        "ChannelDNARevision",
        back_populates="channel",
        cascade="all, delete-orphan",
        order_by="ChannelDNARevision.version.desc()",
    )
    missions: Mapped[list[Mission]] = relationship("Mission", back_populates="channel")
    topic_candidates: Mapped[list[TopicCandidate]] = relationship(
        "TopicCandidate", back_populates="channel", cascade="all, delete-orphan"
    )
    topic_memory: Mapped[list[TopicMemory]] = relationship(
        "TopicMemory", back_populates="channel", cascade="all, delete-orphan"
    )
    research_requests: Mapped[list[ResearchRequest]] = relationship(
        "ResearchRequest", back_populates="channel", cascade="all, delete-orphan"
    )
    content_requests: Mapped[list[ContentGenerationRequest]] = relationship(
        "ContentGenerationRequest", back_populates="channel", cascade="all, delete-orphan"
    )
    production_requests: Mapped[list[ProductionRequest]] = relationship(
        "ProductionRequest", back_populates="channel", cascade="all, delete-orphan"
    )
    production_assets: Mapped[list[ProductionAsset]] = relationship(
        "ProductionAsset", back_populates="channel", cascade="all, delete-orphan"
    )
    guardian_exceptions: Mapped[list[GuardianException]] = relationship(
        "GuardianException", back_populates="channel"
    )

    def __repr__(self) -> str:
        return f"<Channel id={self.id} slug={self.slug!r} state={self.state}>"


class ChannelDNARevision(Base):
    """ChannelDNARevision model storing immutable snapshots of Channel DNA."""

    __tablename__ = "channel_dna_revisions"
    __table_args__ = (UniqueConstraint("channel_id", "version", name="uq_channel_dna_version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    change_reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(50), nullable=False, default="USER")
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    channel: Mapped[Channel] = relationship("Channel", back_populates="revisions")
    mission_executions: Mapped[list[MissionExecution]] = relationship(
        "MissionExecution", back_populates="channel_dna_revision"
    )
    content_requests: Mapped[list[ContentGenerationRequest]] = relationship(
        "ContentGenerationRequest", back_populates="channel_dna_revision"
    )

    def __repr__(self) -> str:
        return f"<ChannelDNARevision channel_id={self.channel_id} version={self.version}>"


# ── OMEGA-002 Mission Engine Models ──


class Mission(Base):
    """Mission database model."""

    __tablename__ = "missions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(50), nullable=False, default="DRAFT", index=True)
    autonomy_level: Mapped[str] = mapped_column(
        String(50), nullable=False, default="SUPERVISED", index=True
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, server_default="{}")

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    started_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    guardian_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    channel: Mapped[Channel | None] = relationship("Channel", back_populates="missions")
    executions: Mapped[list[MissionExecution]] = relationship(
        "MissionExecution", back_populates="mission", cascade="all, delete-orphan"
    )
    tasks: Mapped[list[Task]] = relationship(
        "Task", back_populates="mission", cascade="all, delete-orphan"
    )
    decision_logs: Mapped[list[DecisionLog]] = relationship(
        "DecisionLog", back_populates="mission", cascade="all, delete-orphan"
    )
    guardian_checks: Mapped[list[GuardianCheck]] = relationship(
        "GuardianCheck", back_populates="mission"
    )
    guardian_state_transitions: Mapped[list[GuardianStateTransition]] = relationship(
        "GuardianStateTransition", back_populates="mission"
    )
    cost_records: Mapped[list[CostRecord]] = relationship("CostRecord", back_populates="mission")

    def __repr__(self) -> str:
        return f"<Mission id={self.id} title={self.title!r} state={self.state}>"


class MissionExecution(Base):
    """Mission execution run model."""

    __tablename__ = "mission_executions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("missions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel_dna_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channel_dna_revisions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    state: Mapped[str] = mapped_column(String(50), nullable=False, default="PLANNED", index=True)
    trigger_type: Mapped[str] = mapped_column(String(50), nullable=False, default="MANUAL")
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    started_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    mission: Mapped[Mission] = relationship("Mission", back_populates="executions")
    channel_dna_revision: Mapped[ChannelDNARevision | None] = relationship(
        "ChannelDNARevision", back_populates="mission_executions"
    )
    tasks: Mapped[list[Task]] = relationship("Task", back_populates="execution")
    research_requests: Mapped[list[ResearchRequest]] = relationship(
        "ResearchRequest", back_populates="mission_execution"
    )
    content_requests: Mapped[list[ContentGenerationRequest]] = relationship(
        "ContentGenerationRequest", back_populates="mission_execution"
    )
    production_requests: Mapped[list[ProductionRequest]] = relationship(
        "ProductionRequest", back_populates="mission_execution"
    )

    def __repr__(self) -> str:
        return f"<MissionExecution id={self.id} mission_id={self.mission_id} state={self.state}>"


class Task(Base):
    """Task database model representing nodes in the Mission execution DAG."""

    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("missions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mission_executions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    task_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(50), nullable=False, default="PENDING", index=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    input: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    queued_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dispatched_epoch: Mapped[int | None] = mapped_column(Integer, nullable=True)

    mission: Mapped[Mission] = relationship("Mission", back_populates="tasks")
    execution: Mapped[MissionExecution | None] = relationship(
        "MissionExecution", back_populates="tasks"
    )
    guardian_checks: Mapped[list[GuardianCheck]] = relationship(
        "GuardianCheck", back_populates="task"
    )
    cost_records: Mapped[list[CostRecord]] = relationship("CostRecord", back_populates="task")

    upstream_dependencies: Mapped[list[TaskDependency]] = relationship(
        "TaskDependency",
        foreign_keys="[TaskDependency.task_id]",
        back_populates="task",
        cascade="all, delete-orphan",
    )
    downstream_dependencies: Mapped[list[TaskDependency]] = relationship(
        "TaskDependency",
        foreign_keys="[TaskDependency.depends_on_task_id]",
        back_populates="depends_on_task",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Task id={self.id} title={self.title!r} state={self.state}>"


class TaskDependency(Base):
    """Task dependency model defining execution edges in the DAG."""

    __tablename__ = "task_dependencies"
    __table_args__ = (UniqueConstraint("task_id", "depends_on_task_id", name="uq_task_dependency"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("missions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    depends_on_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    task: Mapped[Task] = relationship(
        "Task", foreign_keys=[task_id], back_populates="upstream_dependencies"
    )
    depends_on_task: Mapped[Task] = relationship(
        "Task", foreign_keys=[depends_on_task_id], back_populates="downstream_dependencies"
    )

    def __repr__(self) -> str:
        return f"<TaskDependency {self.task_id} depends_on {self.depends_on_task_id}>"


class DecisionLog(Base):
    """Decision log audit model for AI and governance decisions."""

    __tablename__ = "decision_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("missions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mission_executions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    decision_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String(500), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    mission: Mapped[Mission] = relationship("Mission", back_populates="decision_logs")

    def __repr__(self) -> str:
        return f"<DecisionLog id={self.id} mission_id={self.mission_id} type={self.decision_type}>"


# ── OMEGA-004 Topic Intelligence Models ──


class TopicCandidate(Base):
    """TopicCandidate database model representing an ingested and evaluated topic candidate."""

    __tablename__ = "topic_candidates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    normalized_title: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False, default="MANUAL")
    source_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    language: Mapped[str] = mapped_column(String(20), nullable=False, default="en")
    region: Mapped[str] = mapped_column(String(10), nullable=False, default="US")
    entities: Mapped[list] = mapped_column(JSON, nullable=False, server_default="[]")
    keywords: Mapped[list] = mapped_column(JSON, nullable=False, server_default="[]")
    tags: Mapped[list] = mapped_column(JSON, nullable=False, server_default="[]")
    topic_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # 8-Dimensional Positive Scores
    audience_fit_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    strategic_fit_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    trend_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    novelty_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    content_gap_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    historical_performance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_efficiency_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    revenue_potential_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_score: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)

    duplicate_status: Mapped[str] = mapped_column(String(50), nullable=False, default="FRESH_TOPIC")
    similar_memory_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("topic_memory.id", ondelete="SET NULL"), nullable=True
    )
    similarity_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="DISCOVERED", index=True
    )
    reasons: Mapped[list] = mapped_column(JSON, nullable=False, server_default="[]")
    score_breakdown: Mapped[dict] = mapped_column(JSON, nullable=False, server_default="{}")
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, server_default="{}")

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    evaluated_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    channel: Mapped[Channel] = relationship("Channel", back_populates="topic_candidates")
    similar_memory: Mapped[TopicMemory | None] = relationship(
        "TopicMemory", foreign_keys=[similar_memory_id]
    )
    angles: Mapped[list[TopicAngle]] = relationship(
        "TopicAngle", back_populates="candidate", cascade="all, delete-orphan"
    )
    research_requests: Mapped[list[ResearchRequest]] = relationship(
        "ResearchRequest", back_populates="topic_candidate", cascade="all, delete-orphan"
    )
    content_requests: Mapped[list[ContentGenerationRequest]] = relationship(
        "ContentGenerationRequest", back_populates="topic_candidate", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<TopicCandidate id={self.id} title={self.title!r} status={self.status}>"


class TopicMemory(Base):
    """TopicMemory database model storing historical topic records per Channel."""

    __tablename__ = "topic_memory"
    __table_args__ = (
        UniqueConstraint("channel_id", "topic_fingerprint", name="uq_channel_topic_fingerprint"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    canonical_topic: Mapped[str] = mapped_column(String(300), nullable=False)
    normalized_topic: Mapped[str] = mapped_column(String(300), nullable=False)
    topic_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entities: Mapped[list] = mapped_column(JSON, nullable=False, server_default="[]")
    keywords: Mapped[list] = mapped_column(JSON, nullable=False, server_default="[]")
    semantic_tags: Mapped[list] = mapped_column(JSON, nullable=False, server_default="[]")

    first_seen_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    times_discovered: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    times_selected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    times_produced: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    times_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    last_selected_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_produced_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_rejected_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_evaluation_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, server_default="{}")

    channel: Mapped[Channel] = relationship("Channel", back_populates="topic_memory")
    angles: Mapped[list[TopicAngle]] = relationship(
        "TopicAngle", back_populates="memory", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<TopicMemory id={self.id} canonical={self.canonical_topic!r}>"


class TopicAngle(Base):
    """TopicAngle database model storing specific framings/hooks for candidates or memory."""

    __tablename__ = "topic_angles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("topic_candidates.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    memory_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("topic_memory.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    angle: Mapped[str] = mapped_column(String(300), nullable=False)
    normalized_angle: Mapped[str] = mapped_column(String(300), nullable=False)
    hook: Mapped[str | None] = mapped_column(String(500), nullable=True)
    audience_intent: Mapped[str | None] = mapped_column(String(100), nullable=True)
    format_hint: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    candidate: Mapped[TopicCandidate | None] = relationship(
        "TopicCandidate", back_populates="angles"
    )
    memory: Mapped[TopicMemory | None] = relationship("TopicMemory", back_populates="angles")

    def __repr__(self) -> str:
        return f"<TopicAngle id={self.id} angle={self.angle!r}>"


# ── OMEGA-005 Research Engine Models ──


class ResearchRequest(Base):
    """ResearchRequest database model representing a research execution request."""

    __tablename__ = "research_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    topic_candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("topic_candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mission_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mission_executions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    mode: Mapped[str] = mapped_column(String(50), nullable=False, default="INTERACTIVE")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="PENDING", index=True)
    outcome: Mapped[str | None] = mapped_column(String(50), nullable=True)
    research_question: Mapped[str | None] = mapped_column(String(500), nullable=True)
    scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str] = mapped_column(String(20), nullable=False, default="en")
    region: Mapped[str] = mapped_column(String(10), nullable=False, default="US")
    max_sources: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    minimum_source_quality: Mapped[float] = mapped_column(Float, nullable=False, default=50.0)
    minimum_claim_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=60.0)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, server_default="{}")

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    channel: Mapped[Channel] = relationship("Channel", back_populates="research_requests")
    topic_candidate: Mapped[TopicCandidate] = relationship(
        "TopicCandidate", back_populates="research_requests"
    )
    mission_execution: Mapped[MissionExecution | None] = relationship(
        "MissionExecution", back_populates="research_requests"
    )

    sources: Mapped[list[ResearchSource]] = relationship(
        "ResearchSource",
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="ResearchSource.retrieved_at.asc()",
    )
    claims: Mapped[list[ResearchClaim]] = relationship(
        "ResearchClaim",
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="ResearchClaim.confidence_score.desc()",
    )
    conflicts: Mapped[list[ResearchConflict]] = relationship(
        "ResearchConflict",
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="ResearchConflict.detected_at.desc()",
    )
    briefs: Mapped[list[ResearchBrief]] = relationship(
        "ResearchBrief",
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="ResearchBrief.version.desc()",
    )

    def __repr__(self) -> str:
        return f"<ResearchRequest id={self.id} channel_id={self.channel_id} status={self.status}>"


class ResearchSource(Base):
    """ResearchSource database model storing bounded normalized research sources."""

    __tablename__ = "research_sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    research_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_type: Mapped[str] = mapped_column(String(50), nullable=False, default="MANUAL")
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    publisher: Mapped[str] = mapped_column(String(200), nullable=False)
    author: Mapped[str | None] = mapped_column(String(200), nullable=True)
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    content_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    primary_source_status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="UNKNOWN"
    )

    quality_score: Mapped[float] = mapped_column(Float, nullable=False, default=50.0)
    relevance_score: Mapped[float] = mapped_column(Float, nullable=False, default=50.0)
    freshness_score: Mapped[float] = mapped_column(Float, nullable=False, default=50.0)
    quality_reasons: Mapped[list] = mapped_column(JSON, nullable=False, server_default="[]")
    independence_cluster_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )

    published_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retrieved_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    language: Mapped[str] = mapped_column(String(20), nullable=False, default="en")
    region: Mapped[str] = mapped_column(String(10), nullable=False, default="US")
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, server_default="{}")

    request: Mapped[ResearchRequest] = relationship("ResearchRequest", back_populates="sources")
    evidence: Mapped[list[ClaimEvidence]] = relationship(
        "ClaimEvidence", back_populates="source", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<ResearchSource id={self.id} publisher={self.publisher!r} quality={self.quality_score}>"


class ResearchClaim(Base):
    """ResearchClaim database model storing extracted facts and claims."""

    __tablename__ = "research_claims"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    research_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_claim: Mapped[str] = mapped_column(Text, nullable=False)
    claim_type: Mapped[str] = mapped_column(String(50), nullable=False, default="FACT")

    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, index=True)
    confidence_band: Mapped[str] = mapped_column(String(50), nullable=False, default="LOW")
    supporting_sources_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    contradicting_sources_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    independent_sources_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    reasons: Mapped[list] = mapped_column(JSON, nullable=False, server_default="[]")
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, server_default="{}")

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    request: Mapped[ResearchRequest] = relationship("ResearchRequest", back_populates="claims")
    evidence: Mapped[list[ClaimEvidence]] = relationship(
        "ClaimEvidence", back_populates="claim", cascade="all, delete-orphan"
    )
    conflicts: Mapped[list[ResearchConflict]] = relationship(
        "ResearchConflict", back_populates="claim"
    )

    def __repr__(self) -> str:
        return f"<ResearchClaim id={self.id} type={self.claim_type} confidence={self.confidence_score}>"


class ClaimEvidence(Base):
    """ClaimEvidence database model establishing provenance between a source and a claim."""

    __tablename__ = "claim_evidence"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_claims.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    support_direction: Mapped[str] = mapped_column(String(50), nullable=False, default="SUPPORTS")
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    source_location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    strength_score: Mapped[float] = mapped_column(Float, nullable=False, default=80.0)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    claim: Mapped[ResearchClaim] = relationship("ResearchClaim", back_populates="evidence")
    source: Mapped[ResearchSource] = relationship("ResearchSource", back_populates="evidence")

    def __repr__(self) -> str:
        return f"<ClaimEvidence id={self.id} claim_id={self.claim_id} dir={self.support_direction}>"


class ResearchConflict(Base):
    """ResearchConflict database model tracking contradictions and discrepancies."""

    __tablename__ = "research_conflicts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    research_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    claim_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_claims.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    conflict_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="DIRECT_CONTRADICTION"
    )
    severity: Mapped[str] = mapped_column(String(50), nullable=False, default="HIGH")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="OPEN", index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    involved_evidence_ids: Mapped[list] = mapped_column(JSON, nullable=False, server_default="[]")
    involved_source_ids: Mapped[list] = mapped_column(JSON, nullable=False, server_default="[]")
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    detected_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    request: Mapped[ResearchRequest] = relationship("ResearchRequest", back_populates="conflicts")
    claim: Mapped[ResearchClaim | None] = relationship("ResearchClaim", back_populates="conflicts")

    def __repr__(self) -> str:
        return f"<ResearchConflict id={self.id} severity={self.severity} status={self.status}>"


class ResearchBrief(Base):
    """ResearchBrief database model storing versioned immutable research snapshots."""

    __tablename__ = "research_briefs"
    __table_args__ = (
        UniqueConstraint("research_request_id", "version", name="uq_research_brief_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    research_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    topic_candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("topic_candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    supersedes_brief_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_briefs.id", ondelete="SET NULL"), nullable=True
    )
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    outcome: Mapped[str] = mapped_column(String(50), nullable=False, default="INSUFFICIENT")
    overall_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)

    verified_claims: Mapped[list] = mapped_column(JSON, nullable=False, server_default="[]")
    uncertain_claims: Mapped[list] = mapped_column(JSON, nullable=False, server_default="[]")
    contradictions: Mapped[list] = mapped_column(JSON, nullable=False, server_default="[]")
    key_facts: Mapped[list] = mapped_column(JSON, nullable=False, server_default="[]")
    statistics: Mapped[list] = mapped_column(JSON, nullable=False, server_default="[]")
    dates: Mapped[list] = mapped_column(JSON, nullable=False, server_default="[]")
    quotes: Mapped[list] = mapped_column(JSON, nullable=False, server_default="[]")
    open_questions: Mapped[list] = mapped_column(JSON, nullable=False, server_default="[]")
    sources_summary: Mapped[dict] = mapped_column(JSON, nullable=False, server_default="{}")
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, server_default="{}")

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    request: Mapped[ResearchRequest] = relationship("ResearchRequest", back_populates="briefs")
    content_requests: Mapped[list[ContentGenerationRequest]] = relationship(
        "ContentGenerationRequest", back_populates="research_brief"
    )

    def __repr__(self) -> str:
        return f"<ResearchBrief id={self.id} request_id={self.research_request_id} v={self.version} current={self.is_current}>"


# ── OMEGA-006 Content Engine Models ──


class ContentGenerationRequest(Base):
    """ContentGenerationRequest database model representing a structured content generation job."""

    __tablename__ = "content_generation_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    topic_candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("topic_candidates.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    research_brief_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_briefs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    channel_dna_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channel_dna_revisions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    mission_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mission_executions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    mode: Mapped[str] = mapped_column(String(50), nullable=False, default="INTERACTIVE")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="DRAFT", index=True)
    outcome: Mapped[str | None] = mapped_column(String(50), nullable=True)
    content_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="YOUTUBE_LONGFORM"
    )
    target_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=480)
    target_word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    language: Mapped[str] = mapped_column(String(20), nullable=False, default="en")
    region: Mapped[str] = mapped_column(String(10), nullable=False, default="US")
    creative_direction: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, server_default="{}")

    channel: Mapped[Channel] = relationship("Channel", back_populates="content_requests")
    topic_candidate: Mapped[TopicCandidate] = relationship(
        "TopicCandidate", back_populates="content_requests"
    )
    research_brief: Mapped[ResearchBrief] = relationship(
        "ResearchBrief", back_populates="content_requests"
    )
    channel_dna_revision: Mapped[ChannelDNARevision] = relationship(
        "ChannelDNARevision", back_populates="content_requests"
    )
    mission_execution: Mapped[MissionExecution | None] = relationship(
        "MissionExecution", back_populates="content_requests"
    )

    intent: Mapped[ContentIntent | None] = relationship(
        "ContentIntent",
        back_populates="content_request",
        cascade="all, delete-orphan",
        uselist=False,
    )
    hooks: Mapped[list[ContentHook]] = relationship(
        "ContentHook",
        back_populates="content_request",
        cascade="all, delete-orphan",
        order_by="ContentHook.hook_variant_index.asc()",
    )
    outline: Mapped[ContentOutline | None] = relationship(
        "ContentOutline",
        back_populates="content_request",
        cascade="all, delete-orphan",
        uselist=False,
    )
    scripts: Mapped[list[ScriptVersion]] = relationship(
        "ScriptVersion",
        back_populates="content_request",
        cascade="all, delete-orphan",
        order_by="ScriptVersion.version.desc()",
    )
    production_requests: Mapped[list[ProductionRequest]] = relationship(
        "ProductionRequest", back_populates="content_request", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<ContentGenerationRequest id={self.id} channel_id={self.channel_id} status={self.status}>"


class ContentIntent(Base):
    """ContentIntent database model storing derived editorial goals and style orientation."""

    __tablename__ = "content_intents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("content_generation_requests.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    primary_goal: Mapped[str] = mapped_column(Text, nullable=False)
    audience_intent: Mapped[str] = mapped_column(Text, nullable=False)
    viewer_promise: Mapped[str] = mapped_column(Text, nullable=False)
    central_question: Mapped[str] = mapped_column(Text, nullable=False)
    core_takeaway: Mapped[str] = mapped_column(Text, nullable=False)
    tone: Mapped[str] = mapped_column(String(100), nullable=False)
    pace: Mapped[str] = mapped_column(String(100), nullable=False)
    complexity: Mapped[str] = mapped_column(String(100), nullable=False)
    desired_emotion: Mapped[str] = mapped_column(String(100), nullable=False)
    call_to_action_type: Mapped[str] = mapped_column(String(100), nullable=False)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    content_request: Mapped[ContentGenerationRequest] = relationship(
        "ContentGenerationRequest", back_populates="intent"
    )

    def __repr__(self) -> str:
        return f"<ContentIntent id={self.id} request_id={self.content_request_id}>"


class ContentHook(Base):
    """ContentHook database model storing hook candidates."""

    __tablename__ = "content_hooks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("content_generation_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    hook_variant_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    hook_type: Mapped[str] = mapped_column(String(50), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    reason_codes: Mapped[list] = mapped_column(JSON, nullable=False, server_default="[]")
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    content_request: Mapped[ContentGenerationRequest] = relationship(
        "ContentGenerationRequest", back_populates="hooks"
    )
    citations: Mapped[list[ContentHookCitation]] = relationship(
        "ContentHookCitation", back_populates="hook", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<ContentHook id={self.id} variant={self.hook_variant_index} type={self.hook_type} selected={self.selected}>"


class ContentHookCitation(Base):
    """ContentHookCitation database model linking factual assertions in hooks to research."""

    __tablename__ = "content_hook_citations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("content_hooks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    research_brief_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_briefs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_claims.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("claim_evidence.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    hook: Mapped[ContentHook] = relationship("ContentHook", back_populates="citations")

    def __repr__(self) -> str:
        return f"<ContentHookCitation id={self.id} hook_id={self.hook_id} claim_id={self.claim_id}>"


class ContentOutline(Base):
    """ContentOutline database model representing structured outline."""

    __tablename__ = "content_outlines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("content_generation_requests.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    opening_description: Mapped[str] = mapped_column(Text, nullable=False)
    sections: Mapped[list] = mapped_column(JSON, nullable=False, server_default="[]")
    closing_description: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    content_request: Mapped[ContentGenerationRequest] = relationship(
        "ContentGenerationRequest", back_populates="outline"
    )

    def __repr__(self) -> str:
        return f"<ContentOutline id={self.id} request_id={self.content_request_id}>"


class ScriptVersion(Base):
    """ScriptVersion database model storing immutable, versioned script drafts."""

    __tablename__ = "script_versions"
    __table_args__ = (
        UniqueConstraint("content_request_id", "version", name="uq_script_version_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("content_generation_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    supersedes_script_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("script_versions.id", ondelete="SET NULL"), nullable=True
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    hook_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_hooks.id", ondelete="SET NULL"), nullable=True
    )
    hook_text: Mapped[str] = mapped_column(Text, nullable=False)
    closing_text: Mapped[str] = mapped_column(Text, nullable=False)
    cta_text: Mapped[str] = mapped_column(Text, nullable=False)

    estimated_word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    qa_status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="PENDING", index=True
    )
    style_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, server_default="{}")

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    content_request: Mapped[ContentGenerationRequest] = relationship(
        "ContentGenerationRequest", back_populates="scripts"
    )
    hook: Mapped[ContentHook | None] = relationship("ContentHook")
    sections: Mapped[list[ScriptSection]] = relationship(
        "ScriptSection",
        back_populates="script_version",
        cascade="all, delete-orphan",
        order_by="ScriptSection.section_order.asc()",
    )
    qa_result: Mapped[ContentQAResult | None] = relationship(
        "ContentQAResult",
        back_populates="script_version",
        cascade="all, delete-orphan",
        uselist=False,
    )
    production_requests: Mapped[list[ProductionRequest]] = relationship(
        "ProductionRequest", back_populates="script_version", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<ScriptVersion id={self.id} request_id={self.content_request_id} v={self.version} current={self.is_current}>"


class ScriptSection(Base):
    """ScriptSection database model storing individual structured sections of a script."""

    __tablename__ = "script_sections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    script_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("script_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    section_order: Mapped[int] = mapped_column(Integer, nullable=False)
    heading: Mapped[str] = mapped_column(String(255), nullable=False)
    narration_text: Mapped[str] = mapped_column(Text, nullable=False)
    estimated_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    transition_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    retention_beat: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    script_version: Mapped[ScriptVersion] = relationship("ScriptVersion", back_populates="sections")
    statements: Mapped[list[ScriptStatement]] = relationship(
        "ScriptStatement",
        back_populates="script_section",
        cascade="all, delete-orphan",
        order_by="ScriptStatement.statement_order.asc()",
    )

    def __repr__(self) -> str:
        return f"<ScriptSection id={self.id} order={self.section_order} heading={self.heading!r}>"


class ScriptStatement(Base):
    """ScriptStatement database model storing individual classified statements in a script section."""

    __tablename__ = "script_statements"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    script_section_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("script_sections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    statement_order: Mapped[int] = mapped_column(Integer, nullable=False)
    statement_text: Mapped[str] = mapped_column(Text, nullable=False)
    statement_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    qualification_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    script_section: Mapped[ScriptSection] = relationship(
        "ScriptSection", back_populates="statements"
    )
    citations: Mapped[list[ContentCitation]] = relationship(
        "ContentCitation", back_populates="statement", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<ScriptStatement id={self.id} type={self.statement_type} order={self.statement_order}>"


class ContentCitation(Base):
    """ContentCitation database model storing normalized provenance relationships."""

    __tablename__ = "content_citations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    script_statement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("script_statements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    research_brief_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_briefs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_claims.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("claim_evidence.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    statement: Mapped[ScriptStatement] = relationship("ScriptStatement", back_populates="citations")

    def __repr__(self) -> str:
        return f"<ContentCitation id={self.id} statement_id={self.script_statement_id} claim_id={self.claim_id}>"


class ContentQAResult(Base):
    """ContentQAResult database model storing local QA findings for a script version."""

    __tablename__ = "content_qa_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    script_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("script_versions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="PENDING", index=True)
    findings: Mapped[list] = mapped_column(JSON, nullable=False, server_default="[]")

    executed_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    script_version: Mapped[ScriptVersion] = relationship(
        "ScriptVersion", back_populates="qa_result"
    )

    def __repr__(self) -> str:
        return f"<ContentQAResult id={self.id} script_version_id={self.script_version_id} status={self.status}>"


# ── OMEGA-007 Production Engine Models ──


class ProductionRequest(Base):
    """ProductionRequest database model representing a request to produce media from a script."""

    __tablename__ = "production_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    script_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("script_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("content_generation_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel_dna_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channel_dna_revisions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mission_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mission_executions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    mode: Mapped[str] = mapped_column(String(50), nullable=False, default="INTERACTIVE")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="DRAFT", index=True)
    outcome: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)

    target_width: Mapped[int] = mapped_column(Integer, nullable=False, default=1920)
    target_height: Mapped[int] = mapped_column(Integer, nullable=False, default=1080)
    fps: Mapped[int] = mapped_column(Integer, nullable=False, default=30)

    video_codec: Mapped[str] = mapped_column(String(30), nullable=False, default="h264")
    audio_codec: Mapped[str] = mapped_column(String(30), nullable=False, default="aac")
    container_format: Mapped[str] = mapped_column(String(20), nullable=False, default="mp4")

    voice_profile: Mapped[dict] = mapped_column(JSON, nullable=False, server_default="{}")
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, server_default="{}")

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    channel: Mapped[Channel] = relationship("Channel", back_populates="production_requests")
    script_version: Mapped[ScriptVersion] = relationship(
        "ScriptVersion", back_populates="production_requests"
    )
    content_request: Mapped[ContentGenerationRequest] = relationship(
        "ContentGenerationRequest", back_populates="production_requests"
    )
    channel_dna_revision: Mapped[ChannelDNARevision] = relationship("ChannelDNARevision")
    mission_execution: Mapped[MissionExecution | None] = relationship(
        "MissionExecution", back_populates="production_requests"
    )

    scenes: Mapped[list[ProductionScene]] = relationship(
        "ProductionScene",
        back_populates="production_request",
        cascade="all, delete-orphan",
        order_by="ProductionScene.scene_order.asc()",
    )
    assets: Mapped[list[ProductionAsset]] = relationship(
        "ProductionAsset",
        back_populates="production_request",
        cascade="all, delete-orphan",
    )
    narration_segments: Mapped[list[NarrationSegment]] = relationship(
        "NarrationSegment",
        back_populates="production_request",
        cascade="all, delete-orphan",
        order_by="NarrationSegment.start_ms.asc()",
    )
    subtitle_cues: Mapped[list[SubtitleCue]] = relationship(
        "SubtitleCue",
        back_populates="production_request",
        cascade="all, delete-orphan",
        order_by="SubtitleCue.cue_order.asc()",
    )
    render_plans: Mapped[list[RenderPlan]] = relationship(
        "RenderPlan",
        back_populates="production_request",
        cascade="all, delete-orphan",
        order_by="RenderPlan.version.desc()",
    )
    render_jobs: Mapped[list[ProductionRenderJob]] = relationship(
        "ProductionRenderJob",
        back_populates="production_request",
        cascade="all, delete-orphan",
        order_by="ProductionRenderJob.created_at.desc()",
    )
    artifacts: Mapped[list[MediaArtifact]] = relationship(
        "MediaArtifact",
        back_populates="production_request",
        cascade="all, delete-orphan",
        order_by="MediaArtifact.version.desc()",
    )
    qa_results: Mapped[list[ProductionQAResult]] = relationship(
        "ProductionQAResult",
        back_populates="production_request",
        cascade="all, delete-orphan",
        order_by="ProductionQAResult.executed_at.desc()",
    )
    guardian_checks: Mapped[list[GuardianCheck]] = relationship(
        "GuardianCheck", back_populates="production_request"
    )
    cost_records: Mapped[list[CostRecord]] = relationship(
        "CostRecord", back_populates="production_request"
    )

    def __repr__(self) -> str:
        return f"<ProductionRequest id={self.id} channel_id={self.channel_id} status={self.status}>"


class ProductionScene(Base):
    """ProductionScene database model representing an individual visual/auditory scene."""

    __tablename__ = "production_scenes"
    __table_args__ = (
        UniqueConstraint("production_request_id", "scene_order", name="uq_production_scene_order"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("production_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scene_order: Mapped[int] = mapped_column(Integer, nullable=False)
    script_section_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("script_sections.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    start_statement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("script_statements.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    end_statement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("script_statements.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    scene_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    narration_text: Mapped[str] = mapped_column(Text, nullable=False)
    estimated_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    visual_intent: Mapped[str | None] = mapped_column(Text, nullable=True)
    transition_in: Mapped[str | None] = mapped_column(String(50), nullable=True)
    transition_out: Mapped[str | None] = mapped_column(String(50), nullable=True)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    production_request: Mapped[ProductionRequest] = relationship(
        "ProductionRequest", back_populates="scenes"
    )
    script_section: Mapped[ScriptSection | None] = relationship("ScriptSection")
    asset_requirements: Mapped[list[AssetRequirement]] = relationship(
        "AssetRequirement",
        back_populates="scene",
        cascade="all, delete-orphan",
    )
    narration_segments: Mapped[list[NarrationSegment]] = relationship(
        "NarrationSegment",
        back_populates="scene",
        cascade="all, delete-orphan",
    )
    subtitle_cues: Mapped[list[SubtitleCue]] = relationship(
        "SubtitleCue",
        back_populates="scene",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<ProductionScene id={self.id} req_id={self.production_request_id} order={self.scene_order} type={self.scene_type}>"


class AssetRequirement(Base):
    """AssetRequirement database model storing asset needs for a scene."""

    __tablename__ = "asset_requirements"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scene_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("production_scenes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    asset_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(String(100), nullable=False)
    query_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="PENDING", index=True)
    license_requirement: Mapped[str] = mapped_column(
        String(50), nullable=False, default="COMMERCIAL_ALLOWED"
    )

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    scene: Mapped[ProductionScene] = relationship(
        "ProductionScene", back_populates="asset_requirements"
    )
    assets: Mapped[list[ProductionAsset]] = relationship(
        "ProductionAsset", back_populates="asset_requirement"
    )

    def __repr__(self) -> str:
        return f"<AssetRequirement id={self.id} scene_id={self.scene_id} type={self.asset_type}>"


class ProductionAsset(Base):
    """ProductionAsset database model storing resolved visual and audio assets."""

    __tablename__ = "production_assets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    production_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("production_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    asset_requirement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("asset_requirements.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    asset_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    provider_type: Mapped[str] = mapped_column(String(50), nullable=False, default="PLACEHOLDER")
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)

    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    license_status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="GENERATED", index=True
    )
    source_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    attribution: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    channel: Mapped[Channel] = relationship("Channel", back_populates="production_assets")
    production_request: Mapped[ProductionRequest] = relationship(
        "ProductionRequest", back_populates="assets"
    )
    asset_requirement: Mapped[AssetRequirement | None] = relationship(
        "AssetRequirement", back_populates="assets"
    )
    narration_segments: Mapped[list[NarrationSegment]] = relationship(
        "NarrationSegment", back_populates="audio_asset"
    )

    def __repr__(self) -> str:
        return f"<ProductionAsset id={self.id} type={self.asset_type} uri={self.storage_uri!r}>"


class NarrationSegment(Base):
    """NarrationSegment database model storing spoken audio segment timeline boundaries."""

    __tablename__ = "narration_segments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("production_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scene_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("production_scenes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    audio_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("production_assets.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    text: Mapped[str] = mapped_column(Text, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    production_request: Mapped[ProductionRequest] = relationship(
        "ProductionRequest", back_populates="narration_segments"
    )
    scene: Mapped[ProductionScene] = relationship(
        "ProductionScene", back_populates="narration_segments"
    )
    audio_asset: Mapped[ProductionAsset | None] = relationship(
        "ProductionAsset", back_populates="narration_segments"
    )

    def __repr__(self) -> str:
        return f"<NarrationSegment id={self.id} start_ms={self.start_ms} end_ms={self.end_ms}>"


class SubtitleCue(Base):
    """SubtitleCue database model storing timed subtitle cue entries."""

    __tablename__ = "subtitle_cues"
    __table_args__ = (
        UniqueConstraint("production_request_id", "cue_order", name="uq_subtitle_cue_order"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("production_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scene_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("production_scenes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cue_order: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    production_request: Mapped[ProductionRequest] = relationship(
        "ProductionRequest", back_populates="subtitle_cues"
    )
    scene: Mapped[ProductionScene] = relationship("ProductionScene", back_populates="subtitle_cues")

    def __repr__(self) -> str:
        return f"<SubtitleCue id={self.id} order={self.cue_order} start_ms={self.start_ms}>"


class RenderPlan(Base):
    """RenderPlan database model storing deterministic compilation manifests."""

    __tablename__ = "render_plans"
    __table_args__ = (
        UniqueConstraint("production_request_id", "version", name="uq_render_plan_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("production_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    width: Mapped[int] = mapped_column(Integer, nullable=False, default=1920)
    height: Mapped[int] = mapped_column(Integer, nullable=False, default=1080)
    fps: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    video_codec: Mapped[str] = mapped_column(String(30), nullable=False, default="h264")
    audio_codec: Mapped[str] = mapped_column(String(30), nullable=False, default="aac")
    container: Mapped[str] = mapped_column(String(20), nullable=False, default="mp4")
    total_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    scene_manifest: Mapped[list] = mapped_column(JSON, nullable=False, server_default="[]")
    audio_manifest: Mapped[list] = mapped_column(JSON, nullable=False, server_default="[]")
    subtitle_manifest: Mapped[list] = mapped_column(JSON, nullable=False, server_default="[]")

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    production_request: Mapped[ProductionRequest] = relationship(
        "ProductionRequest", back_populates="render_plans"
    )
    render_jobs: Mapped[list[ProductionRenderJob]] = relationship(
        "ProductionRenderJob",
        back_populates="render_plan",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<RenderPlan id={self.id} req_id={self.production_request_id} v={self.version}>"


class ProductionRenderJob(Base):
    """ProductionRenderJob database model representing an idempotent execution of a render plan."""

    __tablename__ = "production_render_jobs"
    __table_args__ = (
        UniqueConstraint(
            "production_request_id",
            "idempotency_key",
            name="uq_production_render_job_idempotency",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("production_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    render_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("render_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    state: Mapped[str] = mapped_column(String(50), nullable=False, default="PENDING", index=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sanitized_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    production_request: Mapped[ProductionRequest] = relationship(
        "ProductionRequest", back_populates="render_jobs"
    )
    render_plan: Mapped[RenderPlan] = relationship("RenderPlan", back_populates="render_jobs")
    artifact: Mapped[MediaArtifact | None] = relationship(
        "MediaArtifact", back_populates="render_job", uselist=False
    )

    def __repr__(self) -> str:
        return f"<ProductionRenderJob id={self.id} state={self.state} attempt={self.attempt}>"


class MediaArtifact(Base):
    """MediaArtifact database model storing immutable rendered media files."""

    __tablename__ = "media_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "production_request_id",
            "artifact_type",
            "version",
            name="uq_media_artifact_version",
        ),
        Index(
            "uq_media_artifact_current_video",
            "production_request_id",
            "artifact_type",
            unique=True,
            postgresql_where=text("is_current = true AND artifact_type = 'VIDEO'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("production_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    render_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("production_render_jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    artifact_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)

    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    production_request: Mapped[ProductionRequest] = relationship(
        "ProductionRequest", back_populates="artifacts"
    )
    render_job: Mapped[ProductionRenderJob | None] = relationship(
        "ProductionRenderJob", back_populates="artifact"
    )
    qa_result: Mapped[ProductionQAResult | None] = relationship(
        "ProductionQAResult",
        back_populates="artifact",
        cascade="all, delete-orphan",
        uselist=False,
    )
    guardian_checks: Mapped[list[GuardianCheck]] = relationship(
        "GuardianCheck", back_populates="media_artifact"
    )

    def __repr__(self) -> str:
        return f"<MediaArtifact id={self.id} type={self.artifact_type} v={self.version} current={self.is_current}>"


class ProductionQAResult(Base):
    """ProductionQAResult database model storing local QA findings for a rendered media artifact."""

    __tablename__ = "production_qa_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("production_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media_artifacts.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="PENDING", index=True)
    findings: Mapped[list] = mapped_column(JSON, nullable=False, server_default="[]")

    executed_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    production_request: Mapped[ProductionRequest] = relationship(
        "ProductionRequest", back_populates="qa_results"
    )
    artifact: Mapped[MediaArtifact] = relationship("MediaArtifact", back_populates="qa_result")

    def __repr__(self) -> str:
        return (
            f"<ProductionQAResult id={self.id} artifact_id={self.artifact_id} status={self.status}>"
        )


# ── OMEGA-008 QA + Guardian Subsystem Models ──


class GuardianRuleSet(Base):
    """Versioned rule configuration for Guardian subsystem."""

    __tablename__ = "guardian_rulesets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="DRAFT", index=True)
    effective_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    rules_config: Mapped[dict] = mapped_column(JSON, nullable=False, server_default="{}")
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    checks: Mapped[list[GuardianCheck]] = relationship("GuardianCheck", back_populates="ruleset")

    def __repr__(self) -> str:
        return f"<GuardianRuleSet version={self.version} status={self.status}>"


class GuardianCheck(Base):
    """Authoritative evaluation event at a protected workflow checkpoint."""

    __tablename__ = "guardian_checks"
    __table_args__ = (
        UniqueConstraint(
            "mission_id", "checkpoint", "idempotency_key", name="uq_guardian_check_idempotency"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("missions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    production_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("production_requests.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    media_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media_artifacts.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    trigger_type: Mapped[str] = mapped_column(String(50), nullable=False)
    checkpoint: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    ruleset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("guardian_rulesets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    ruleset_version: Mapped[str] = mapped_column(String(50), nullable=False)
    ruleset_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="PENDING", index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    guardian_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    diagnostic_context: Mapped[dict] = mapped_column(JSON, nullable=False, server_default="{}")
    started_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    mission: Mapped[Mission] = relationship("Mission", back_populates="guardian_checks")
    task: Mapped[Task | None] = relationship("Task", back_populates="guardian_checks")
    production_request: Mapped[ProductionRequest | None] = relationship(
        "ProductionRequest", back_populates="guardian_checks"
    )
    media_artifact: Mapped[MediaArtifact | None] = relationship(
        "MediaArtifact", back_populates="guardian_checks"
    )
    ruleset: Mapped[GuardianRuleSet] = relationship("GuardianRuleSet", back_populates="checks")
    decision: Mapped[GuardianDecision | None] = relationship(
        "GuardianDecision", back_populates="check", uselist=False
    )
    findings: Mapped[list[GuardianFinding]] = relationship(
        "GuardianFinding", back_populates="check"
    )
    detector_runs: Mapped[list[GuardianDetectorRun]] = relationship(
        "GuardianDetectorRun", back_populates="check"
    )

    def __repr__(self) -> str:
        return f"<GuardianCheck id={self.id} checkpoint={self.checkpoint} status={self.status}>"


class GuardianDetectorRun(Base):
    """Lifecycle and execution tracking for an individual detector within a check."""

    __tablename__ = "guardian_detector_runs"
    __table_args__ = (
        UniqueConstraint(
            "guardian_check_id",
            "detector_type",
            "detector_version",
            name="uq_guardian_detector_run",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    guardian_check_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("guardian_checks.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    detector_type: Mapped[str] = mapped_column(String(100), nullable=False)
    detector_version: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="PENDING", index=True)
    failure_policy: Mapped[str] = mapped_column(String(50), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    error_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    check: Mapped[GuardianCheck] = relationship("GuardianCheck", back_populates="detector_runs")
    findings: Mapped[list[GuardianFinding]] = relationship(
        "GuardianFinding", back_populates="detector_run"
    )

    def __repr__(self) -> str:
        return (
            f"<GuardianDetectorRun id={self.id} detector={self.detector_type} status={self.status}>"
        )


class GuardianFinding(Base):
    """Immutable finding emitted by a detector run."""

    __tablename__ = "guardian_findings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    guardian_check_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("guardian_checks.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    detector_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("guardian_detector_runs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    detector_type: Mapped[str] = mapped_column(String(100), nullable=False)
    detector_version: Mapped[str] = mapped_column(String(50), nullable=False)
    rule_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    risk_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, server_default="{}")
    location_reference: Mapped[dict] = mapped_column(JSON, nullable=False, server_default="{}")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    check: Mapped[GuardianCheck] = relationship("GuardianCheck", back_populates="findings")
    detector_run: Mapped[GuardianDetectorRun] = relationship(
        "GuardianDetectorRun", back_populates="findings"
    )
    decision_associations: Mapped[list[GuardianDecisionFinding]] = relationship(
        "GuardianDecisionFinding", back_populates="finding"
    )
    resolution_events: Mapped[list[GuardianResolutionEvent]] = relationship(
        "GuardianResolutionEvent", back_populates="finding"
    )

    def __repr__(self) -> str:
        return f"<GuardianFinding id={self.id} rule={self.rule_id} severity={self.severity}>"


class GuardianDecision(Base):
    """Immutable authoritative decision event for a GuardianCheck. Exactly one decision per check."""

    __tablename__ = "guardian_decisions"
    __table_args__ = (UniqueConstraint("guardian_check_id", name="uq_guardian_decision_check"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    guardian_check_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("guardian_checks.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    resulting_gate_state: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(100), nullable=False, default="GUARDIAN_ENGINE")
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    check: Mapped[GuardianCheck] = relationship("GuardianCheck", back_populates="decision")
    finding_associations: Mapped[list[GuardianDecisionFinding]] = relationship(
        "GuardianDecisionFinding", back_populates="decision"
    )
    resolution_events: Mapped[list[GuardianResolutionEvent]] = relationship(
        "GuardianResolutionEvent", back_populates="decision"
    )

    def __repr__(self) -> str:
        return f"<GuardianDecision id={self.id} check_id={self.guardian_check_id} action={self.action}>"


class GuardianDecisionFinding(Base):
    """Association table linking decisions to findings and applied exceptions."""

    __tablename__ = "guardian_decision_findings"

    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("guardian_decisions.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    finding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("guardian_findings.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    applied_exception_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("guardian_exceptions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    decision: Mapped[GuardianDecision] = relationship(
        "GuardianDecision", back_populates="finding_associations"
    )
    finding: Mapped[GuardianFinding] = relationship(
        "GuardianFinding", back_populates="decision_associations"
    )
    applied_exception: Mapped[GuardianException | None] = relationship("GuardianException")


class GuardianResolutionEvent(Base):
    """Append-only audit event recording operator actions on findings or decisions."""

    __tablename__ = "guardian_resolution_events"
    __table_args__ = (
        CheckConstraint(
            "finding_id IS NOT NULL OR decision_id IS NOT NULL", name="ck_resolution_target"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    finding_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("guardian_findings.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("guardian_decisions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    resolution_type: Mapped[str] = mapped_column(String(50), nullable=False)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    finding: Mapped[GuardianFinding | None] = relationship(
        "GuardianFinding", back_populates="resolution_events"
    )
    decision: Mapped[GuardianDecision | None] = relationship(
        "GuardianDecision", back_populates="resolution_events"
    )

    def __repr__(self) -> str:
        return (
            f"<GuardianResolutionEvent id={self.id} type={self.resolution_type} actor={self.actor}>"
        )


class GuardianException(Base):
    """Scoped, auditable policy or rule exception with append-only revocation metadata."""

    __tablename__ = "guardian_exceptions"
    __table_args__ = (
        CheckConstraint("rule_id IS NOT NULL OR risk_type IS NOT NULL", name="ck_exception_scope"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    risk_type: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    channel_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    mission_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("missions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    expires_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_reason: Mapped[str] = mapped_column(Text, nullable=False)
    revoked_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    revoked_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    revocation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    channel: Mapped[Channel | None] = relationship("Channel", back_populates="guardian_exceptions")
    mission: Mapped[Mission | None] = relationship("Mission")

    def __repr__(self) -> str:
        return f"<GuardianException id={self.id} rule={self.rule_id} revoked={self.revoked_at is not None}>"


class GuardianAlertOutbox(Base):
    """Transactional outbox for alerts generated by Guardian decisions."""

    __tablename__ = "guardian_alert_outbox"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    guardian_check_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("guardian_checks.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("guardian_decisions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    destination: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="PENDING", index=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    sent_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    check: Mapped[GuardianCheck] = relationship("GuardianCheck")
    decision: Mapped[GuardianDecision] = relationship("GuardianDecision")

    def __repr__(self) -> str:
        return f"<GuardianAlertOutbox id={self.id} status={self.status} channel={self.channel}>"


class CostRecord(Base):
    """Idempotent operational resource cost tracking."""

    __tablename__ = "cost_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("missions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    production_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("production_requests.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    cost_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_type: Mapped[str] = mapped_column(String(100), nullable=False)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )
    recorded_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    mission: Mapped[Mission] = relationship("Mission", back_populates="cost_records")
    task: Mapped[Task | None] = relationship("Task", back_populates="cost_records")
    production_request: Mapped[ProductionRequest | None] = relationship(
        "ProductionRequest", back_populates="cost_records"
    )

    def __repr__(self) -> str:
        return f"<CostRecord id={self.id} type={self.cost_type} amount={self.amount_usd}>"


class GuardianStateTransition(Base):
    """Audit log of gate state transitions."""

    __tablename__ = "guardian_state_transitions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("missions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    checkpoint: Mapped[str] = mapped_column(String(50), nullable=False)
    from_gate_state: Mapped[str] = mapped_column(String(50), nullable=False)
    to_gate_state: Mapped[str] = mapped_column(String(50), nullable=False)
    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("guardian_decisions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    guardian_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    mission: Mapped[Mission] = relationship("Mission", back_populates="guardian_state_transitions")
    decision: Mapped[GuardianDecision] = relationship("GuardianDecision")

    def __repr__(self) -> str:
        return f"<GuardianStateTransition {self.from_gate_state} -> {self.to_gate_state} epoch={self.guardian_epoch}>"


# ══════════════════════════════════════════════════════════════════
# OMEGA-009: Network Manager Models
# ══════════════════════════════════════════════════════════════════


class NetworkProfile(Base):
    """Grouping of network routes for operational contexts (e.g. production default)."""

    __tablename__ = "network_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    routes: Mapped[list[NetworkRoute]] = relationship(
        "NetworkRoute", back_populates="profile", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<NetworkProfile id={self.id} name={self.name} default={self.is_default}>"


class NetworkRoute(Base):
    """Concrete outbound egress route with operational fencing and credential reference."""

    __tablename__ = "network_routes"
    __table_args__ = (
        CheckConstraint("tls_verify = true", name="ck_network_route_tls_verify_mandatory"),
        CheckConstraint(
            "route_type IN ('DIRECT', 'HTTPS_CONNECT_PROXY', 'SOCKS5', 'SYSTEM_DEFAULT')",
            name="ck_network_route_allowed_types",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("network_profiles.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    route_type: Mapped[str] = mapped_column(String(50), nullable=False)
    endpoint_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    credential_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    allowed_service_categories: Mapped[list] = mapped_column(
        JSON, nullable=False, server_default="[]"
    )
    tls_verify: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    connect_timeout_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=5.0)
    read_timeout_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=10.0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    priority_weight: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    config_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    config_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    profile: Mapped[NetworkProfile] = relationship("NetworkProfile", back_populates="routes")
    circuit_states: Mapped[list[NetworkCircuitState]] = relationship(
        "NetworkCircuitState", back_populates="route"
    )
    checks: Mapped[list[NetworkCheck]] = relationship("NetworkCheck", back_populates="route")

    def __repr__(self) -> str:
        return f"<NetworkRoute id={self.id} name={self.name} type={self.route_type} v={self.config_version}>"


class NetworkPolicy(Base):
    """Immutable, versioned network policy for a specific service category."""

    __tablename__ = "network_policies"
    __table_args__ = (
        UniqueConstraint("service_category", "version", name="uq_network_policy_category_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="DRAFT", index=True)
    effective_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    policy_config: Mapped[dict] = mapped_column(JSON, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    checks: Mapped[list[NetworkCheck]] = relationship("NetworkCheck", back_populates="policy")

    def __repr__(self) -> str:
        return f"<NetworkPolicy id={self.id} category={self.service_category} v={self.version} status={self.status}>"


class NetworkCircuitState(Base):
    """Authoritative persistent circuit breaker state per (route_id, service_category)."""

    __tablename__ = "network_circuit_states"

    route_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("network_routes.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    service_category: Mapped[str] = mapped_column(String(50), primary_key=True)
    state: Mapped[str] = mapped_column(String(50), nullable=False, default="CLOSED", index=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    half_open_successes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    opened_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cooldown_until: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failure_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    half_open_lease_token: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    half_open_lease_expires_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    route: Mapped[NetworkRoute] = relationship("NetworkRoute", back_populates="circuit_states")

    def __repr__(self) -> str:
        return f"<NetworkCircuitState route={self.route_id} cat={self.service_category} state={self.state}>"


class NetworkCheck(Base):
    """Authoritative preflight evaluation event binding exact pinned configurations."""

    __tablename__ = "network_checks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("missions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    route_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("network_routes.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    route_config_version: Mapped[int] = mapped_column(Integer, nullable=False)
    route_config_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("network_policies.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    policy_version: Mapped[str] = mapped_column(String(50), nullable=False)
    policy_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    service_category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    canonical_destination: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="PENDING", index=True)
    started_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    route: Mapped[NetworkRoute] = relationship("NetworkRoute", back_populates="checks")
    policy: Mapped[NetworkPolicy] = relationship("NetworkPolicy", back_populates="checks")
    decision: Mapped[NetworkDecision | None] = relationship(
        "NetworkDecision", back_populates="check", uselist=False
    )
    probe_runs: Mapped[list[NetworkProbeRun]] = relationship(
        "NetworkProbeRun", back_populates="check"
    )

    def __repr__(self) -> str:
        return f"<NetworkCheck id={self.id} dest={self.canonical_destination} status={self.status}>"


class NetworkProbeRun(Base):
    """Evidence record of an individual probe step executed during a NetworkCheck."""

    __tablename__ = "network_probe_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    check_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("network_checks.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    probe_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, server_default="{}")
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    check: Mapped[NetworkCheck] = relationship("NetworkCheck", back_populates="probe_runs")

    def __repr__(self) -> str:
        return f"<NetworkProbeRun id={self.id} probe={self.probe_type} status={self.status}>"


class NetworkDecision(Base):
    """Immutable authoritative decision event for a NetworkCheck. Exactly one decision per check."""

    __tablename__ = "network_decisions"
    __table_args__ = (UniqueConstraint("network_check_id", name="uq_network_decision_check"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    network_check_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("network_checks.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    resulting_health_state: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(100), nullable=False, default="NETWORK_MANAGER")
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    check: Mapped[NetworkCheck] = relationship("NetworkCheck", back_populates="decision")

    def __repr__(self) -> str:
        return f"<NetworkDecision id={self.id} check={self.network_check_id} action={self.action}>"


class NetworkCircuitTransition(Base):
    """Immutable append-only audit trail of circuit state transitions."""

    __tablename__ = "network_circuit_transitions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    route_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("network_routes.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    service_category: Mapped[str] = mapped_column(String(50), nullable=False)
    from_state: Mapped[str] = mapped_column(String(50), nullable=False)
    to_state: Mapped[str] = mapped_column(String(50), nullable=False)
    trigger_reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<NetworkCircuitTransition {self.from_state} -> {self.to_state}>"


# ── OMEGA-010 Smart Scheduler Models ──


class SchedulePolicy(Base):
    """Versioned, immutable scheduling policy configuration."""

    __tablename__ = "schedule_policies"
    __table_args__ = (
        UniqueConstraint(
            "workload_category", "version", name="uq_schedule_policy_category_version"
        ),
        CheckConstraint(
            "status IN ('DRAFT', 'ACTIVE', 'RETIRED')",
            name="ck_schedule_policies_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workload_category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="DRAFT", index=True
    )
    policy_config: Mapped[dict] = mapped_column(JSON, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    decisions: Mapped[list[ScheduleDecision]] = relationship(
        "ScheduleDecision", back_populates="policy"
    )
    reservations: Mapped[list[ScheduleReservation]] = relationship(
        "ScheduleReservation", back_populates="policy"
    )

    def __repr__(self) -> str:
        return f"<SchedulePolicy {self.workload_category} v{self.version} [{self.status}]>"


class ScheduleDecision(Base):
    """Authoritative, immutable scheduling decision record."""

    __tablename__ = "schedule_decisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("missions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    channel_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    target_type: Mapped[str] = mapped_column(String(50), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    workload_category: Mapped[str] = mapped_column(String(50), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    scheduled_start_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scheduled_end_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("schedule_policies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    policy_version: Mapped[str] = mapped_column(String(50), nullable=False)
    policy_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    channel_dna_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channel_dna_revisions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    reservation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    guardian_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    diagnostic_context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    evaluated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    policy: Mapped[SchedulePolicy] = relationship("SchedulePolicy", back_populates="decisions")
    mission: Mapped[Mission] = relationship("Mission")
    task: Mapped[Task | None] = relationship("Task")
    channel: Mapped[Channel | None] = relationship("Channel")

    def __repr__(self) -> str:
        return f"<ScheduleDecision {self.id} action={self.action}>"


class ScheduleReservation(Base):
    """Authoritative slot allocation with state machine lifecycle."""

    __tablename__ = "schedule_reservations"
    __table_args__ = (
        CheckConstraint(
            "state IN ('ACTIVE', 'DISPATCHING', 'CONSUMED', 'RELEASED', 'EXPIRED', 'CANCELLED')",
            name="ck_schedule_reservations_state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("schedule_decisions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    channel_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    mission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("missions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    target_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    workload_category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    scheduled_start_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    scheduled_end_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="ACTIVE", index=True
    )
    priority_score: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.0")
    policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("schedule_policies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    policy_version: Mapped[str] = mapped_column(String(50), nullable=False)
    policy_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    channel_dna_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channel_dna_revisions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    guardian_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    dispatching_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    decision: Mapped[ScheduleDecision] = relationship("ScheduleDecision")
    policy: Mapped[SchedulePolicy] = relationship("SchedulePolicy", back_populates="reservations")
    mission: Mapped[Mission] = relationship("Mission")
    state_transitions: Mapped[list[ScheduleStateTransition]] = relationship(
        "ScheduleStateTransition", back_populates="reservation"
    )
    outbox_items: Mapped[list[SchedulerDispatchOutbox]] = relationship(
        "SchedulerDispatchOutbox", back_populates="reservation"
    )

    def __repr__(self) -> str:
        return f"<ScheduleReservation {self.id} state={self.state}>"


class ScheduleStateTransition(Base):
    """Append-only audit trail for reservation state transitions."""

    __tablename__ = "schedule_state_transitions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    reservation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("schedule_reservations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    from_state: Mapped[str] = mapped_column(String(50), nullable=False)
    to_state: Mapped[str] = mapped_column(String(50), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(50), nullable=False, server_default="SCHEDULER")
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    reservation: Mapped[ScheduleReservation] = relationship(
        "ScheduleReservation", back_populates="state_transitions"
    )

    def __repr__(self) -> str:
        return f"<ScheduleStateTransition {self.from_state} -> {self.to_state}>"


class SchedulerDispatchOutbox(Base):
    """Transactional outbox for crash-safe dispatch to Celery broker."""

    __tablename__ = "scheduler_dispatch_outbox"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'SENT', 'RETRY', 'DEAD_LETTER')",
            name="ck_scheduler_dispatch_outbox_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    reservation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("schedule_reservations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    mission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("missions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    celery_task_name: Mapped[str] = mapped_column(String(255), nullable=False)
    celery_args: Mapped[dict] = mapped_column(JSON, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="PENDING", index=True
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="5")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_send_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    sent_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_retry_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    reservation: Mapped[ScheduleReservation] = relationship(
        "ScheduleReservation", back_populates="outbox_items"
    )
    task: Mapped[Task] = relationship("Task")

    def __repr__(self) -> str:
        return f"<SchedulerDispatchOutbox {self.id} status={self.status}>"


# ══════════════════════════════════════════════════════════════════════════════
# OMEGA-011 Publisher Models
# ══════════════════════════════════════════════════════════════════════════════


class PlatformAccount(Base):
    """Connected external platform account registry."""

    __tablename__ = "platform_accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    account_display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    external_account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    scopes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    channel: Mapped[Channel] = relationship("Channel")
    vault_entry: Mapped[CredentialVault | None] = relationship(
        "CredentialVault", back_populates="platform_account", uselist=False
    )
    publish_intents: Mapped[list[PublishIntent]] = relationship(
        "PublishIntent", back_populates="platform_account"
    )

    __table_args__ = (
        UniqueConstraint("channel_id", "platform", name="uq_platform_accounts_channel_platform"),
        CheckConstraint(
            "status IN ('ACTIVE', 'EXPIRED', 'REVOKED')", name="chk_platform_accounts_status"
        ),
    )

    def __repr__(self) -> str:
        return f"<PlatformAccount {self.id} platform={self.platform} status={self.status}>"


class CredentialVault(Base):
    """Encrypted OAuth refresh and access token storage."""

    __tablename__ = "credential_vault"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    platform_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("platform_accounts.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    encrypted_access_token: Mapped[str] = mapped_column(Text, nullable=False)
    access_token_expires_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    encrypted_refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    token_type: Mapped[str] = mapped_column(String(32), nullable=False, default="Bearer")
    key_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    platform_account: Mapped[PlatformAccount] = relationship(
        "PlatformAccount", back_populates="vault_entry"
    )

    __table_args__ = (CheckConstraint("key_version > 0", name="chk_credential_vault_key_version"),)

    def __repr__(self) -> str:
        return f"<CredentialVault {self.id} account={self.platform_account_id} key_v={self.key_version}>"


class OAuthAuthorizationSession(Base):
    """OAuth state hash & PKCE challenge tracking."""

    __tablename__ = "oauth_authorization_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    encrypted_pkce_verifier: Mapped[str] = mapped_column(Text, nullable=False)
    redirect_uri: Mapped[str] = mapped_column(String(512), nullable=False)
    requested_scopes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    expires_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    channel: Mapped[Channel] = relationship("Channel")

    __table_args__ = (
        CheckConstraint("expires_at > created_at", name="chk_oauth_sessions_expiry"),
        Index("idx_oauth_sessions_state", "state_hash", "expires_at"),
    )

    def __repr__(self) -> str:
        return f"<OAuthAuthorizationSession {self.id} platform={self.platform} consumed={self.consumed_at is not None}>"


class PublishIntent(Base):
    """Immutable publishing contract snapshot with claim fencing."""

    __tablename__ = "publish_intents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("missions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    platform_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("platform_accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    media_artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media_artifacts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    media_artifact_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    channel_dna_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channel_dna_revisions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    supersedes_intent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publish_intents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    requested_privacy_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PRIVATE"
    )
    category_id: Mapped[str] = mapped_column(String(32), nullable=False, default="28")
    made_for_kids: Mapped[bool] = mapped_column(Boolean, nullable=False)
    platform_custom_options: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    intent_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="APPROVED")
    claim_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    attempt_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    claimed_by_worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    superseded_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    mission: Mapped[Mission] = relationship("Mission")
    task: Mapped[Task] = relationship("Task")
    channel: Mapped[Channel] = relationship("Channel")
    platform_account: Mapped[PlatformAccount] = relationship(
        "PlatformAccount", back_populates="publish_intents"
    )
    media_artifact: Mapped[MediaArtifact] = relationship("MediaArtifact")
    channel_dna_revision: Mapped[ChannelDNARevision | None] = relationship("ChannelDNARevision")
    attempts: Mapped[list[PublishAttempt]] = relationship(
        "PublishAttempt", back_populates="publish_intent"
    )
    transitions: Mapped[list[PublishIntentTransition]] = relationship(
        "PublishIntentTransition", back_populates="publish_intent"
    )

    __table_args__ = (
        UniqueConstraint("task_id", "revision_number", name="uq_publish_intents_task_revision"),
        CheckConstraint(
            "state IN ('DRAFT', 'APPROVED', 'CLAIMED', 'PUBLISHED', 'FAILED', 'SUPERSEDED', 'CANCELLED')",
            name="chk_publish_intents_state",
        ),
        CheckConstraint("revision_number >= 1", name="chk_publish_intents_revision_number"),
        CheckConstraint("attempt_generation >= 0", name="chk_publish_intents_attempt_generation"),
        Index("idx_publish_intents_active_claim", "state", "lease_expires_at"),
    )

    def __repr__(self) -> str:
        return f"<PublishIntent {self.id} task={self.task_id} state={self.state} rev={self.revision_number}>"


class PublishIntentTransition(Base):
    """Append-only audit history for PublishIntent state transitions."""

    __tablename__ = "publish_intent_transitions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    publish_intent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publish_intents.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    from_state: Mapped[str] = mapped_column(String(32), nullable=False)
    to_state: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    publish_intent: Mapped[PublishIntent] = relationship(
        "PublishIntent", back_populates="transitions"
    )

    __table_args__ = (Index("idx_intent_transitions_intent", "publish_intent_id", "created_at"),)

    def __repr__(self) -> str:
        return f"<PublishIntentTransition {self.from_state}->{self.to_state} intent={self.publish_intent_id}>"


class PublishAttempt(Base):
    """Authoritative record of an external publishing execution."""

    __tablename__ = "publish_attempts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    publish_intent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publish_intents.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="CREATED")
    provider_video_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    effective_privacy_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_after_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reconciliation_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    publish_intent: Mapped[PublishIntent] = relationship("PublishIntent", back_populates="attempts")
    upload_session: Mapped[UploadSession | None] = relationship(
        "UploadSession", back_populates="publish_attempt", uselist=False
    )
    transitions: Mapped[list[PublishAttemptTransition]] = relationship(
        "PublishAttemptTransition", back_populates="publish_attempt"
    )

    __table_args__ = (
        CheckConstraint(
            "state IN ('CREATED', 'UPLOADING', 'FINALIZING', 'SUCCEEDED', 'RETRYABLE_FAILED', 'PERMANENT_FAILED', 'UNKNOWN', 'BLOCKED_GUARDIAN', 'CANCELLED')",
            name="chk_publish_attempts_state",
        ),
        Index("idx_publish_attempts_intent", "publish_intent_id", "attempt_number"),
    )

    def __repr__(self) -> str:
        return f"<PublishAttempt {self.id} state={self.state} provider_id={self.provider_video_id}>"


class PublishAttemptTransition(Base):
    """Append-only audit history for PublishAttempt state transitions."""

    __tablename__ = "publish_attempt_transitions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    publish_attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publish_attempts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    from_state: Mapped[str] = mapped_column(String(32), nullable=False)
    to_state: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    publish_attempt: Mapped[PublishAttempt] = relationship(
        "PublishAttempt", back_populates="transitions"
    )

    __table_args__ = (Index("idx_attempt_transitions_attempt", "publish_attempt_id", "created_at"),)

    def __repr__(self) -> str:
        return f"<PublishAttemptTransition {self.from_state}->{self.to_state} attempt={self.publish_attempt_id}>"


class UploadSession(Base):
    """Resumable upload session URI and byte progress."""

    __tablename__ = "upload_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    publish_attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publish_attempts.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    session_uri: Mapped[str] = mapped_column(Text, nullable=False)
    total_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bytes_uploaded: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    chunk_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=8388608)
    expires_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    publish_attempt: Mapped[PublishAttempt] = relationship(
        "PublishAttempt", back_populates="upload_session"
    )

    __table_args__ = (
        CheckConstraint("total_bytes > 0", name="chk_upload_sessions_total_bytes"),
        CheckConstraint("bytes_uploaded >= 0", name="chk_upload_sessions_bytes_uploaded"),
        Index("idx_upload_sessions_expires", "expires_at"),
    )

    def __repr__(self) -> str:
        return f"<UploadSession {self.id} uploaded={self.bytes_uploaded}/{self.total_bytes}>"


class PublisherSchedulerHandoffOutbox(Base):
    """Durable cross-service retry handoff outbox to OMEGA-010 Scheduler."""

    __tablename__ = "publisher_scheduler_handoff_outbox"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    publish_intent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publish_intents.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    publish_attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publish_attempts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    mission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("missions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    earliest_retry_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    claim_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    claimed_by_worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claimed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivery_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    delivered_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    publish_intent: Mapped[PublishIntent] = relationship("PublishIntent")
    publish_attempt: Mapped[PublishAttempt] = relationship("PublishAttempt")
    task: Mapped[Task] = relationship("Task")
    mission: Mapped[Mission] = relationship("Mission")

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'CLAIMED', 'DELIVERED', 'DEAD_LETTER')",
            name="chk_pub_sched_outbox_status",
        ),
        Index("idx_pub_sched_outbox_sweep", "status", "next_attempt_at", "lease_expires_at"),
    )

    def __repr__(self) -> str:
        return f"<PublisherSchedulerHandoffOutbox {self.id} status={self.status} attempt={self.attempt_count}>"


# ══════════════════════════════════════════════════════════════════════════════
# OMEGA-012 Analytics Engine Models
# ══════════════════════════════════════════════════════════════════════════════


class AnalyticsAsset(Base):
    """Authoritative entity binding PublishIntent and PublishAttempt to analytics tracking."""

    __tablename__ = "analytics_assets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    platform_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("platform_accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    publish_intent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publish_intents.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    publish_attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publish_attempts.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    media_artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media_artifacts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    channel_dna_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channel_dna_revisions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="YOUTUBE")
    provider_video_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    published_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    asset_status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    lifecycle_phase: Mapped[str] = mapped_column(
        String(32), nullable=False, default="ACTIVE_HOURLY"
    )
    next_poll_due_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    last_polled_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    poll_error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    channel: Mapped[Channel] = relationship("Channel")
    platform_account: Mapped[PlatformAccount] = relationship("PlatformAccount")
    publish_intent: Mapped[PublishIntent] = relationship("PublishIntent")
    publish_attempt: Mapped[PublishAttempt] = relationship("PublishAttempt")
    media_artifact: Mapped[MediaArtifact] = relationship("MediaArtifact")
    channel_dna_revision: Mapped[ChannelDNARevision | None] = relationship("ChannelDNARevision")

    windows: Mapped[list[AnalyticsWindow]] = relationship(
        "AnalyticsWindow", back_populates="asset", cascade="all, delete-orphan"
    )
    observations: Mapped[list[AnalyticsMetricObservation]] = relationship(
        "AnalyticsMetricObservation", back_populates="asset"
    )

    __table_args__ = (
        UniqueConstraint(
            "provider", "provider_video_id", name="uq_analytics_assets_provider_video"
        ),
        CheckConstraint(
            "asset_status IN ('ACTIVE', 'MATURE', 'ARCHIVED', 'PROVIDER_DELETED', 'UNAVAILABLE', 'FAILED')",
            name="chk_analytics_assets_status",
        ),
        CheckConstraint(
            "lifecycle_phase IN ('ACTIVE_HOURLY', 'MATURE_DAILY', 'STABLE_WEEKLY', 'ARCHIVED_MONTHLY', 'PAUSED')",
            name="chk_analytics_assets_lifecycle",
        ),
        Index("idx_analytics_assets_poll_due", "asset_status", "next_poll_due_at"),
    )

    def __repr__(self) -> str:
        return f"<AnalyticsAsset {self.id} provider_video_id={self.provider_video_id} status={self.asset_status}>"


class AnalyticsMetricCapability(Base):
    """Static registry of supported metric/dimension/report combinations and scope gates."""

    __tablename__ = "analytics_metric_capabilities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    canonical_metric_name: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_metric_name: Mapped[str] = mapped_column(String(64), nullable=False)
    api_source: Mapped[str] = mapped_column(String(64), nullable=False)
    supported_report_types: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    compatible_dimensions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    required_scopes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    availability_class: Mapped[str] = mapped_column(String(32), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False, default="COUNT")
    aggregation_semantics: Mapped[str] = mapped_column(String(32), nullable=False, default="SUM")
    capability_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "provider",
            "canonical_metric_name",
            "api_source",
            "capability_version",
            name="uq_analytics_metric_capabilities",
        ),
        CheckConstraint(
            "availability_class IN ('UNIVERSAL', 'DIMENSION_RESTRICTED', 'PRIVACY_GATED', 'PROVIDER_GATED', 'CHANNEL_ONLY', 'DEPRECATED')",
            name="chk_metric_capabilities_class",
        ),
        Index("idx_metric_capabilities_lookup", "provider", "canonical_metric_name"),
    )

    def __repr__(self) -> str:
        return f"<AnalyticsMetricCapability {self.canonical_metric_name} ({self.api_source})>"


class AnalyticsQuotaBucket(Base):
    """Dynamic tracking of provider quota consumption and internal budget thresholds."""

    __tablename__ = "analytics_quota_buckets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    quota_bucket: Mapped[str] = mapped_column(String(64), nullable=False)
    method: Mapped[str] = mapped_column(String(64), nullable=False)
    configured_daily_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    internal_budget_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    provider_authoritative_usage: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    estimated_internal_units: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    provider_timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="America/Los_Angeles"
    )
    last_reset_at_utc: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    next_reset_at_utc: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence_source: Mapped[str] = mapped_column(
        String(64), nullable=False, default="DEFAULT_ESTIMATE"
    )
    last_verified_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("provider", "quota_bucket", "method", name="uq_analytics_quota_buckets"),
    )

    def __repr__(self) -> str:
        return f"<AnalyticsQuotaBucket {self.provider}:{self.quota_bucket} est={self.estimated_internal_units}>"


class AnalyticsPollJob(Base):
    """Lease coordination and fencing for background polling tasks."""

    __tablename__ = "analytics_poll_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    poll_execution_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_asset_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    claim_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    claim_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    claimed_by_worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'CLAIMED', 'COMPLETED', 'FAILED', 'EXPIRED')",
            name="chk_analytics_poll_jobs_status",
        ),
        Index("idx_poll_jobs_lease", "status", "lease_expires_at"),
    )

    def __repr__(self) -> str:
        return f"<AnalyticsPollJob {self.id} status={self.status} gen={self.claim_generation}>"


class AnalyticsProviderSnapshot(Base):
    """Single authoritative append-only store for raw HTTP responses (both video and channel)."""

    __tablename__ = "analytics_provider_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analytics_assets.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    platform_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("platform_accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    api_endpoint: Mapped[str] = mapped_column(String(64), nullable=False)
    request_params: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    raw_payload: Mapped[Any] = mapped_column(JSON, nullable=False)
    payload_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    logical_query_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    poll_execution_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    snapshot_dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    retrieval_timestamp: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    http_status: Mapped[int] = mapped_column(Integer, nullable=False, default=200)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    asset: Mapped[AnalyticsAsset | None] = relationship("AnalyticsAsset")
    channel: Mapped[Channel] = relationship("Channel")
    platform_account: Mapped[PlatformAccount] = relationship("PlatformAccount")

    __table_args__ = (
        Index("idx_provider_snapshots_asset_time", "asset_id", "retrieval_timestamp"),
    )

    def __repr__(self) -> str:
        return f"<AnalyticsProviderSnapshot {self.id} dedupe={self.snapshot_dedupe_key[:8]}>"


class AnalyticsWindow(Base):
    """Temporal anchor for evaluation windows (relative elapsed and Pacific calendar days)."""

    __tablename__ = "analytics_windows"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analytics_assets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    window_type: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    provider_timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="America/Los_Angeles"
    )
    window_start_utc: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end_utc: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_state: Mapped[str] = mapped_column(String(32), nullable=False, default="PROVISIONAL")
    finalized_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    asset: Mapped[AnalyticsAsset] = relationship("AnalyticsAsset", back_populates="windows")
    observations: Mapped[list[AnalyticsMetricObservation]] = relationship(
        "AnalyticsMetricObservation", back_populates="window", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint(
            "asset_id",
            "window_type",
            "window_start_utc",
            name="uq_analytics_windows_asset_type_start",
        ),
        CheckConstraint(
            "window_state IN ('PENDING', 'PROVISIONAL', 'FINALIZED', 'REVISED')",
            name="chk_analytics_windows_state",
        ),
        Index("idx_analytics_windows_state", "asset_id", "window_state"),
    )

    def __repr__(self) -> str:
        return f"<AnalyticsWindow {self.id} type={self.window_type} state={self.window_state}>"


class AnalyticsMetricObservation(Base):
    """Normalized, unit-standardized provider facts (immutable append-only)."""

    __tablename__ = "analytics_metric_observations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analytics_assets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    window_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analytics_windows.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analytics_provider_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    metric_name: Mapped[str] = mapped_column(String(64), nullable=False)
    classification: Mapped[str] = mapped_column(String(32), nullable=False, default="PROVIDER_FACT")
    metric_quality: Mapped[str] = mapped_column(String(32), nullable=False)
    numeric_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    integer_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    string_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dimensions: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    dimensions_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    normalization_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    observation_dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    revision_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    preceding_observation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analytics_metric_observations.id", ondelete="RESTRICT"),
        nullable=True,
    )
    observation_timestamp: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    asset: Mapped[AnalyticsAsset] = relationship("AnalyticsAsset", back_populates="observations")
    window: Mapped[AnalyticsWindow] = relationship("AnalyticsWindow", back_populates="observations")
    snapshot: Mapped[AnalyticsProviderSnapshot] = relationship("AnalyticsProviderSnapshot")

    __table_args__ = (
        UniqueConstraint(
            "window_id",
            "metric_name",
            "dimensions_hash",
            "revision_sequence",
            name="uq_metric_obs_window_metric_dim_rev",
        ),
        CheckConstraint(
            "classification = 'PROVIDER_FACT'",
            name="chk_metric_obs_classification",
        ),
        CheckConstraint(
            "metric_quality IN ('AVAILABLE', 'ZERO_CONFIRMED', 'NOT_READY', 'NOT_AVAILABLE', 'SUPPRESSED', 'PERMISSION_DENIED', 'PROVIDER_ERROR', 'STALE', 'REVISED', 'UNKNOWN_MISSING')",
            name="chk_metric_obs_quality",
        ),
        Index("idx_metric_obs_asset_metric", "asset_id", "metric_name"),
    )

    def __repr__(self) -> str:
        return f"<AnalyticsMetricObservation {self.id} {self.metric_name} rev={self.revision_sequence}>"


class AnalyticsMetricLatestPointer(Base):
    """Mutable query projection tracking current observation state per metric/window/dimensions."""

    __tablename__ = "analytics_metric_latest_pointer"

    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analytics_assets.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    window_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analytics_windows.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    metric_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    dimensions_hash: Mapped[str] = mapped_column(String(64), primary_key=True, default="")
    current_observation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analytics_metric_observations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    current_revision_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    current_observation: Mapped[AnalyticsMetricObservation] = relationship(
        "AnalyticsMetricObservation"
    )

    def __repr__(self) -> str:
        return f"<AnalyticsMetricLatestPointer {self.metric_name} rev={self.current_revision_sequence}>"


class AnalyticsComputedMetric(Base):
    """Derived and heuristic metrics with multi-source observation lineage (immutable history)."""

    __tablename__ = "analytics_computed_metrics"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analytics_assets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    window_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analytics_windows.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    metric_name: Mapped[str] = mapped_column(String(64), nullable=False)
    classification: Mapped[str] = mapped_column(String(32), nullable=False)
    formula_identifier: Mapped[str] = mapped_column(String(64), nullable=False)
    formula_version: Mapped[str] = mapped_column(String(32), nullable=False)
    source_observation_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    source_computed_metric_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    source_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    computation_dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    numeric_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    metric_quality: Mapped[str] = mapped_column(String(32), nullable=False, default="AVAILABLE")
    computation_revision_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    computed_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    asset: Mapped[AnalyticsAsset] = relationship("AnalyticsAsset")
    window: Mapped[AnalyticsWindow] = relationship("AnalyticsWindow")

    __table_args__ = (
        UniqueConstraint(
            "window_id",
            "metric_name",
            "formula_version",
            "computation_revision_sequence",
            name="uq_computed_metrics_window_name_ver_rev",
        ),
        CheckConstraint(
            "classification IN ('DETERMINISTIC_DERIVED', 'HEURISTIC_FEATURE')",
            name="chk_computed_metrics_classification",
        ),
        CheckConstraint(
            "metric_quality IN ('AVAILABLE', 'ZERO_CONFIRMED', 'NOT_READY', 'NOT_AVAILABLE', 'SUPPRESSED', 'PERMISSION_DENIED', 'PROVIDER_ERROR', 'STALE', 'REVISED', 'UNKNOWN_MISSING')",
            name="chk_computed_metrics_quality",
        ),
        Index("idx_computed_metrics_lookup", "asset_id", "metric_name", "classification"),
    )

    def __repr__(self) -> str:
        return f"<AnalyticsComputedMetric {self.metric_name} ({self.classification})={self.numeric_value}>"


class AnalyticsChannelSnapshot(Base):
    """Normalized channel metric snapshots referencing raw provider evidence."""

    __tablename__ = "analytics_channel_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    platform_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("platform_accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analytics_provider_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    provider_timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="America/Los_Angeles"
    )
    window_start_utc: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end_utc: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    total_views: Mapped[int] = mapped_column(BigInteger, nullable=False)
    subscriber_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    video_count: Mapped[int] = mapped_column(Integer, nullable=False)
    aggregate_watch_time_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    revision_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    preceding_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analytics_channel_snapshots.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    channel: Mapped[Channel] = relationship("Channel")
    platform_account: Mapped[PlatformAccount] = relationship("PlatformAccount")
    snapshot: Mapped[AnalyticsProviderSnapshot] = relationship("AnalyticsProviderSnapshot")

    __table_args__ = (
        UniqueConstraint(
            "channel_id",
            "snapshot_date",
            "revision_sequence",
            name="uq_channel_snapshots_channel_date_rev",
        ),
        Index("idx_channel_snapshots_date", "channel_id", "snapshot_date"),
    )

    def __repr__(self) -> str:
        return f"<AnalyticsChannelSnapshot {self.id} date={self.snapshot_date} rev={self.revision_sequence}>"


class AnalyticsChannelLatestPointer(Base):
    """Mutable query projection tracking current channel snapshot per date."""

    __tablename__ = "analytics_channel_latest_pointer"

    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True)
    current_channel_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analytics_channel_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
    )
    current_revision_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    current_snapshot: Mapped[AnalyticsChannelSnapshot] = relationship("AnalyticsChannelSnapshot")

    def __repr__(self) -> str:
        return f"<AnalyticsChannelLatestPointer {self.channel_id}:{self.snapshot_date} rev={self.current_revision_sequence}>"


# ============================================================================
# OMEGA-013 Learning Engine Models
# ============================================================================


class LearningIngestionCursor(Base):
    """Durable high-water mark tracking for OMEGA-012 observation discovery."""

    __tablename__ = "learning_ingestion_cursors"

    consumer_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    cursor_updated_at_utc: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    cursor_window_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    high_water_mark_sequence: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    last_success_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<LearningIngestionCursor {self.consumer_id} seq={self.high_water_mark_sequence}>"


class LearningInputSnapshot(Base):
    """Immutable store of ingested OMEGA-012 learning observations."""

    __tablename__ = "learning_input_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    observation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    publish_intent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("publish_intents.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    provider_video_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    media_artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_artifacts.id", ondelete="RESTRICT"), nullable=False
    )
    channel_dna_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channel_dna_revisions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    window_type: Mapped[str] = mapped_column(String(32), nullable=False)
    window_state: Mapped[str] = mapped_column(String(32), nullable=False)
    window_start_utc: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end_utc: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at_utc: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    metric_qualities: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    classifications: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    is_fully_finalized: Mapped[bool] = mapped_column(Boolean, nullable=False)
    quality_flags: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    payload_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    input_dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    revision_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    preceding_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("learning_input_snapshots.id", ondelete="RESTRICT"),
        nullable=True,
    )
    ingested_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "observation_id",
            "window_type",
            "revision_sequence",
            name="uq_learning_input_obs_win_rev",
        ),
        CheckConstraint(
            "window_state IN ('PENDING', 'PROVISIONAL', 'FINALIZED', 'REVISED')",
            name="ck_learning_input_window_state",
        ),
    )

    def __repr__(self) -> str:
        return f"<LearningInputSnapshot {self.id} obs={self.observation_id} rev={self.revision_sequence}>"


class LearningInputLatestPointer(Base):
    """Mutable query projection and concurrency anchor for input snapshots."""

    __tablename__ = "learning_input_latest_pointers"

    observation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    window_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    current_input_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("learning_input_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
    )
    current_revision_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    current_payload_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    current_snapshot: Mapped[LearningInputSnapshot] = relationship("LearningInputSnapshot")

    def __repr__(self) -> str:
        return f"<LearningInputLatestPointer {self.observation_id}:{self.window_type} rev={self.current_revision_sequence}>"


class LearningFeatureSnapshot(Base):
    """Extracted deterministic and model-extracted content features."""

    __tablename__ = "learning_feature_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    input_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("learning_input_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    feature_schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    extractor_version: Mapped[str] = mapped_column(String(32), nullable=False)
    deterministic_features: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    model_features: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    feature_snapshot_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<LearningFeatureSnapshot {self.id} input={self.input_snapshot_id}>"


class LearningModelExtraction(Base):
    """Audit trail for LLM prompt/response extraction provenance."""

    __tablename__ = "learning_model_extractions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    feature_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("learning_feature_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    model_provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_template_version: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    response_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    structured_output: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<LearningModelExtraction {self.id} provider={self.model_provider} model={self.model_name}>"


class LearningCohort(Base):
    """Channel-local comparison cohort definitions."""

    __tablename__ = "learning_cohorts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    cohort_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    content_format: Mapped[str] = mapped_column(String(32), nullable=False)
    duration_bucket: Mapped[str] = mapped_column(String(32), nullable=False)
    dna_compatibility_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default="STRICT_REVISION"
    )
    evaluation_window: Mapped[str] = mapped_column(String(32), nullable=False)
    inclusion_criteria: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("channel_id", "cohort_slug", name="uq_learning_cohorts_chan_slug"),
        CheckConstraint(
            "content_format IN ('SHORT', 'LONG_FORM')", name="ck_learning_cohorts_content_format"
        ),
    )

    def __repr__(self) -> str:
        return f"<LearningCohort {self.cohort_slug} chan={self.channel_id}>"


class LearningCohortMember(Base):
    """Association between feature snapshots and cohorts."""

    __tablename__ = "learning_cohort_members"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cohort_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("learning_cohorts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    feature_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("learning_feature_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    cohort_member_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    admitted_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<LearningCohortMember cohort={self.cohort_id} feat={self.feature_snapshot_id}>"


class LearningBaseline(Base):
    """Immutable cohort performance baselines."""

    __tablename__ = "learning_baselines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cohort_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("learning_cohorts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    outcome_metric: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluation_window: Mapped[str] = mapped_column(String(32), nullable=False)
    baseline_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False)
    metric_median: Mapped[float] = mapped_column(Float, nullable=False)
    metric_mean: Mapped[float] = mapped_column(Float, nullable=False)
    metric_stddev: Mapped[float] = mapped_column(Float, nullable=False)
    metric_iqr: Mapped[float] = mapped_column(Float, nullable=False)
    metric_mad: Mapped[float] = mapped_column(Float, nullable=False)
    member_ids_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluation_as_of_utc: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    selection_policy_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    baseline_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    calculated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index(
            "idx_baselines_lookup_latest",
            "cohort_id",
            "outcome_metric",
            "evaluation_window",
            text("baseline_version DESC"),
        ),
    )

    def __repr__(self) -> str:
        return f"<LearningBaseline cohort={self.cohort_id} metric={self.outcome_metric} ver={self.baseline_version}>"


class LearningHypothesis(Base):
    """Immutable versioned hypothesis definitions."""

    __tablename__ = "learning_hypotheses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hypothesis_family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    hypothesis_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    cohort_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("learning_cohorts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    hypothesis_slug: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    factor_name: Mapped[str] = mapped_column(String(64), nullable=False)
    treatment_definition: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    control_definition: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    target_outcome_metric: Mapped[str] = mapped_column(String(64), nullable=False)
    target_evaluation_window: Mapped[str] = mapped_column(String(32), nullable=False)
    definition_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_hypothesis_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("learning_hypotheses.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "hypothesis_family_id", "hypothesis_version", name="uq_hypotheses_family_version"
        ),
        UniqueConstraint(
            "hypothesis_family_id", "definition_checksum", name="uq_hypotheses_family_def_checksum"
        ),
    )

    def __repr__(self) -> str:
        return f"<LearningHypothesis {self.hypothesis_slug} fam={self.hypothesis_family_id} ver={self.hypothesis_version}>"


class LearningHypothesisLatestPointer(Base):
    """Mutable query projection tracking current state per hypothesis family."""

    __tablename__ = "learning_hypothesis_latest_pointers"

    hypothesis_family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    current_hypothesis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("learning_hypotheses.id", ondelete="RESTRICT"),
        nullable=False,
    )
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    current_status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    latest_evaluation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("learning_hypothesis_evaluations.id", ondelete="RESTRICT"),
        nullable=True,
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    current_hypothesis: Mapped[LearningHypothesis] = relationship(
        "LearningHypothesis", foreign_keys=[current_hypothesis_id]
    )

    __table_args__ = (
        CheckConstraint(
            "current_status IN ('DRAFT', 'ACTIVE', 'INCONCLUSIVE', 'SUPPORTED', 'WEAKENED', 'CONTRADICTED', 'STALE', 'SUPERSEDED')",
            name="ck_hypotheses_current_status",
        ),
    )

    def __repr__(self) -> str:
        return f"<LearningHypothesisLatestPointer {self.hypothesis_family_id} status={self.current_status}>"


class LearningHypothesisEvaluation(Base):
    """Immutable statistical evaluation results."""

    __tablename__ = "learning_hypothesis_evaluations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hypothesis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("learning_hypotheses.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    hypothesis_family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    baseline_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("learning_baselines.id", ondelete="RESTRICT"), nullable=False
    )
    evaluation_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    sample_size_treatment: Mapped[int] = mapped_column(Integer, nullable=False)
    sample_size_control: Mapped[int] = mapped_column(Integer, nullable=False)
    treatment_median: Mapped[float] = mapped_column(Float, nullable=False)
    control_median: Mapped[float] = mapped_column(Float, nullable=False)
    effect_size_absolute: Mapped[float] = mapped_column(Float, nullable=False)
    effect_size_relative_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    cliffs_delta: Mapped[float] = mapped_column(Float, nullable=False)
    p_value_raw: Mapped[float] = mapped_column(Float, nullable=False)
    p_value_adjusted: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_class: Mapped[str] = mapped_column(String(32), nullable=False)
    resulting_status: Mapped[str] = mapped_column(String(32), nullable=False)
    evaluation_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    evaluated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    hypothesis: Mapped[LearningHypothesis] = relationship("LearningHypothesis")
    baseline: Mapped[LearningBaseline] = relationship("LearningBaseline")

    __table_args__ = (
        CheckConstraint(
            "resulting_status IN ('INCONCLUSIVE', 'SUPPORTED', 'WEAKENED', 'CONTRADICTED')",
            name="ck_evaluation_resulting_status",
        ),
        CheckConstraint(
            "confidence_class IN ('VERY_LOW', 'LOW', 'MODERATE', 'HIGH', 'VERY_HIGH')",
            name="ck_evaluation_confidence_class",
        ),
    )

    def __repr__(self) -> str:
        return f"<LearningHypothesisEvaluation {self.id} hyp={self.hypothesis_id} status={self.resulting_status}>"


class LearningEvaluationEvidenceManifest(Base):
    """Complete, reproducible sample population manifest for an evaluation."""

    __tablename__ = "learning_evaluation_evidence_manifests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    evaluation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("learning_hypothesis_evaluations.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    treatment_input_snapshot_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    control_input_snapshot_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    source_learning_observation_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    source_feature_snapshot_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    baseline_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("learning_baselines.id", ondelete="RESTRICT"), nullable=False
    )
    treatment_values: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    control_values: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    member_set_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    analysis_version: Mapped[str] = mapped_column(String(32), nullable=False)
    effect_policy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    effect_policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence_policy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence_policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    multiple_comparison_family_id: Mapped[str] = mapped_column(String(128), nullable=False)
    correction_method: Mapped[str] = mapped_column(String(32), nullable=False)
    correction_method_version: Mapped[int] = mapped_column(Integer, nullable=False)
    evaluation_as_of_utc: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    manifest_checksum: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    evaluation: Mapped[LearningHypothesisEvaluation] = relationship("LearningHypothesisEvaluation")

    def __repr__(self) -> str:
        return f"<LearningEvaluationEvidenceManifest {self.id} eval={self.evaluation_id}>"


class LearningKnowledgeItem(Base):
    """Immutable knowledge claims and statistical evidence links."""

    __tablename__ = "learning_knowledge_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    knowledge_family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    knowledge_type: Mapped[str] = mapped_column(String(64), nullable=False)
    structured_claim: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    human_readable_summary: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(32), nullable=False, default="OBSERVATIONAL")
    confidence_class: Mapped[str] = mapped_column(String(32), nullable=False)
    effect_size_absolute: Mapped[float] = mapped_column(Float, nullable=False)
    effect_size_relative_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    cliffs_delta: Mapped[float] = mapped_column(Float, nullable=False)
    sample_size_treatment: Mapped[int] = mapped_column(Integer, nullable=False)
    sample_size_control: Mapped[int] = mapped_column(Integer, nullable=False)
    source_hypothesis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("learning_hypotheses.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_evaluation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("learning_hypothesis_evaluations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("learning_knowledge_items.id", ondelete="RESTRICT"),
        nullable=True,
    )
    knowledge_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "evidence_type IN ('OBSERVATIONAL', 'CONTROLLED_EXPERIMENT', 'MANUAL_LABEL', 'EXTERNAL_PRIOR')",
            name="ck_knowledge_evidence_type",
        ),
    )

    def __repr__(self) -> str:
        return f"<LearningKnowledgeItem {self.id} fam={self.knowledge_family_id} rev={self.revision_number}>"


class LearningKnowledgeLatestPointer(Base):
    """Mutable lifecycle projection for knowledge claims."""

    __tablename__ = "learning_knowledge_latest_pointers"

    knowledge_family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    current_knowledge_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("learning_knowledge_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    current_revision_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    current_status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    current_knowledge: Mapped[LearningKnowledgeItem] = relationship(
        "LearningKnowledgeItem", foreign_keys=[current_knowledge_item_id]
    )

    __table_args__ = (
        CheckConstraint(
            "current_status IN ('ACTIVE', 'WEAKENED', 'STALE', 'SUPERSEDED', 'RETRACTED')",
            name="ck_knowledge_current_status",
        ),
    )

    def __repr__(self) -> str:
        return f"<LearningKnowledgeLatestPointer {self.knowledge_family_id} status={self.current_status}>"


class LearningEvent(Base):
    """Authoritative append-only audit event journal."""

    __tablename__ = "learning_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(32), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channels.id", ondelete="RESTRICT"), nullable=False
    )
    source_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    event_dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    occurred_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_learning_events_chan_time", "channel_id", "occurred_at"),
        Index("idx_learning_events_agg", "aggregate_type", "aggregate_id"),
    )

    def __repr__(self) -> str:
        return f"<LearningEvent {self.event_type} agg={self.aggregate_id}>"


class LearningJob(Base):
    """Fenced background execution jobs."""

    __tablename__ = "learning_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING", index=True)
    job_dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    claim_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    claim_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    claimed_by_worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'CLAIMED', 'COMPLETED', 'FAILED', 'DEFERRED')",
            name="ck_learning_jobs_status",
        ),
    )

    def __repr__(self) -> str:
        return f"<LearningJob {self.id} type={self.job_type} status={self.status}>"
