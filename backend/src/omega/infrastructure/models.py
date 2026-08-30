"""SQLAlchemy ORM models for database persistence.

Provides declarative models for Jobs, Missions, Tasks, DecisionLogs,
Channels, ChannelDNARevisions, TopicCandidates, TopicMemory, TopicAngles,
ResearchRequests, ResearchSources, ResearchClaims, ClaimEvidence,
ResearchConflicts, and ResearchBriefs.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
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
