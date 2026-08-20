"""SQLAlchemy ORM models.

All database table definitions live here.
Preserves OMEGA-001 Foundation (Job) and OMEGA-002 Mission Engine models,
OMEGA-003 Channel Manager models, and introduces OMEGA-004 Topic Intelligence models.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    pass


# ── OMEGA-001 Foundation Model ──


class Job(Base):
    """Job database model from OMEGA-001 Foundation."""

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
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
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSON, nullable=False, server_default="{}"
    )

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    archived_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    revisions: Mapped[list[ChannelDNARevision]] = relationship(
        "ChannelDNARevision", back_populates="channel", cascade="all, delete-orphan", order_by="ChannelDNARevision.version.desc()"
    )
    missions: Mapped[list[Mission]] = relationship("Mission", back_populates="channel")
    topic_candidates: Mapped[list[TopicCandidate]] = relationship(
        "TopicCandidate", back_populates="channel", cascade="all, delete-orphan"
    )
    topic_memory: Mapped[list[TopicMemory]] = relationship(
        "TopicMemory", back_populates="channel", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Channel id={self.id} slug={self.slug!r} state={self.state}>"


class ChannelDNARevision(Base):
    """ChannelDNARevision model storing immutable snapshots of Channel DNA."""

    __tablename__ = "channel_dna_revisions"
    __table_args__ = (
        UniqueConstraint("channel_id", "version", name="uq_channel_dna_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    change_reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(50), nullable=False, default="USER")
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    channel: Mapped[Channel] = relationship("Channel", back_populates="revisions")
    executions: Mapped[list[MissionExecution]] = relationship(
        "MissionExecution", back_populates="channel_dna_revision"
    )

    def __repr__(self) -> str:
        return f"<ChannelDNARevision id={self.id} channel_id={self.channel_id} v={self.version}>"


# ── OMEGA-002 Mission Engine Models ──


class Mission(Base):
    """Mission database model representing intent and desired objective."""

    __tablename__ = "missions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channels.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(50), nullable=False, index=True, default="DRAFT")
    autonomy_level: Mapped[str] = mapped_column(
        String(50), nullable=False, default="SUPERVISED", index=True
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSON, nullable=False, server_default="{}"
    )

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

    channel: Mapped[Channel | None] = relationship("Channel", back_populates="missions")
    executions: Mapped[list[MissionExecution]] = relationship(
        "MissionExecution", back_populates="mission", cascade="all, delete-orphan"
    )
    tasks: Mapped[list[Task]] = relationship(
        "Task", back_populates="mission", cascade="all, delete-orphan"
    )
    dependencies: Mapped[list[TaskDependency]] = relationship(
        "TaskDependency", back_populates="mission", cascade="all, delete-orphan"
    )
    decisions: Mapped[list[DecisionLog]] = relationship(
        "DecisionLog", back_populates="mission", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Mission id={self.id} title={self.title!r} state={self.state}>"


class MissionExecution(Base):
    """MissionExecution model representing an individual execution run of a Mission."""

    __tablename__ = "mission_executions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("missions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel_dna_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channel_dna_revisions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    state: Mapped[str] = mapped_column(String(50), nullable=False, index=True, default="PLANNED")
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
        "ChannelDNARevision", back_populates="executions"
    )
    tasks: Mapped[list[Task]] = relationship("Task", back_populates="execution")
    decisions: Mapped[list[DecisionLog]] = relationship("DecisionLog", back_populates="execution")

    def __repr__(self) -> str:
        return f"<MissionExecution id={self.id} mission_id={self.mission_id} state={self.state}>"


class Task(Base):
    """Task model representing an individual unit of work within a MissionExecution DAG."""

    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("missions.id", ondelete="CASCADE"), nullable=False, index=True
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
    state: Mapped[str] = mapped_column(String(50), nullable=False, index=True, default="PENDING")
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

    mission: Mapped[Mission] = relationship("Mission", back_populates="tasks")
    execution: Mapped[MissionExecution | None] = relationship(
        "MissionExecution", back_populates="tasks"
    )
    decisions: Mapped[list[DecisionLog]] = relationship("DecisionLog", back_populates="task")

    def __repr__(self) -> str:
        return f"<Task id={self.id} type={self.task_type} state={self.state}>"


class TaskDependency(Base):
    """TaskDependency model defining DAG edges (task_id depends on depends_on_task_id)."""

    __tablename__ = "task_dependencies"
    __table_args__ = (
        UniqueConstraint("task_id", "depends_on_task_id", name="uq_task_dependency"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("missions.id", ondelete="CASCADE"), nullable=False, index=True
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

    mission: Mapped[Mission] = relationship("Mission", back_populates="dependencies")

    def __repr__(self) -> str:
        return f"<TaskDependency {self.task_id} -> {self.depends_on_task_id}>"


class DecisionLog(Base):
    """DecisionLog model recording operational rationales with zero chain-of-thought."""

    __tablename__ = "decision_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("missions.id", ondelete="CASCADE"), nullable=False, index=True
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

    mission: Mapped[Mission] = relationship("Mission", back_populates="decisions")
    execution: Mapped[MissionExecution | None] = relationship(
        "MissionExecution", back_populates="decisions"
    )
    task: Mapped[Task | None] = relationship("Task", back_populates="decisions")

    def __repr__(self) -> str:
        return f"<DecisionLog id={self.id} type={self.decision_type} actor={self.actor}>"


# ── OMEGA-004 Topic Intelligence Models ──


class TopicCandidate(Base):
    """TopicCandidate database model representing an ingested topic awaiting evaluation or selection."""

    __tablename__ = "topic_candidates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    normalized_title: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False, default="MANUAL", index=True)
    source_name: Mapped[str] = mapped_column(String(100), nullable=False, default="user_input")
    source_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    language: Mapped[str] = mapped_column(String(20), nullable=False, default="en")
    region: Mapped[str] = mapped_column(String(10), nullable=False, default="US")
    entities: Mapped[list] = mapped_column(JSON, nullable=False, server_default="[]")
    keywords: Mapped[list] = mapped_column(JSON, nullable=False, server_default="[]")
    tags: Mapped[list] = mapped_column(JSON, nullable=False, server_default="[]")
    topic_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Explainable Scoring Dimensions (0.0 to 100.0)
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

    status: Mapped[str] = mapped_column(String(50), nullable=False, default="DISCOVERED", index=True)
    reasons: Mapped[list] = mapped_column(JSON, nullable=False, server_default="[]")
    score_breakdown: Mapped[dict] = mapped_column(JSON, nullable=False, server_default="{}")
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSON, nullable=False, server_default="{}"
    )

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    evaluated_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    channel: Mapped[Channel] = relationship("Channel", back_populates="topic_candidates")
    similar_memory: Mapped[TopicMemory | None] = relationship("TopicMemory", foreign_keys=[similar_memory_id])
    angles: Mapped[list[TopicAngle]] = relationship(
        "TopicAngle", back_populates="candidate", cascade="all, delete-orphan"
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
        UUID(as_uuid=True), ForeignKey("channels.id", ondelete="CASCADE"), nullable=False, index=True
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
    last_produced_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_rejected_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_evaluation_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSON, nullable=False, server_default="{}"
    )

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
        UUID(as_uuid=True), ForeignKey("topic_candidates.id", ondelete="CASCADE"), nullable=True, index=True
    )
    memory_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("topic_memory.id", ondelete="CASCADE"), nullable=True, index=True
    )
    angle: Mapped[str] = mapped_column(String(300), nullable=False)
    normalized_angle: Mapped[str] = mapped_column(String(300), nullable=False)
    hook: Mapped[str | None] = mapped_column(String(500), nullable=True)
    audience_intent: Mapped[str | None] = mapped_column(String(100), nullable=True)
    format_hint: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    candidate: Mapped[TopicCandidate | None] = relationship("TopicCandidate", back_populates="angles")
    memory: Mapped[TopicMemory | None] = relationship("TopicMemory", back_populates="angles")

    def __repr__(self) -> str:
        return f"<TopicAngle id={self.id} angle={self.angle!r}>"
