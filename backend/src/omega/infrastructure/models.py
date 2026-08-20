"""SQLAlchemy ORM models for database persistence.

Provides declarative models for Jobs, Missions, Tasks, DecisionLogs,
Channels, ChannelDNARevisions, TopicCandidates, TopicMemory, TopicAngles,
ResearchRequests, ResearchSources, ResearchClaims, ClaimEvidence,
ResearchConflicts, and ResearchBriefs.
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
    research_requests: Mapped[list[ResearchRequest]] = relationship(
        "ResearchRequest", back_populates="channel", cascade="all, delete-orphan"
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
    mission_executions: Mapped[list[MissionExecution]] = relationship(
        "MissionExecution", back_populates="channel_dna_revision"
    )

    def __repr__(self) -> str:
        return f"<ChannelDNARevision channel_id={self.channel_id} version={self.version}>"


# ── OMEGA-002 Mission Engine Models ──


class Mission(Base):
    """Mission database model."""

    __tablename__ = "missions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channels.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(50), nullable=False, default="DRAFT", index=True)
    autonomy_level: Mapped[str] = mapped_column(String(50), nullable=False, default="SUPERVISED", index=True)
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
    decision_logs: Mapped[list[DecisionLog]] = relationship(
        "DecisionLog", back_populates="mission", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Mission id={self.id} title={self.title!r} state={self.state}>"


class MissionExecution(Base):
    """Mission execution run model."""

    __tablename__ = "mission_executions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("missions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel_dna_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_dna_revisions.id", ondelete="RESTRICT"), nullable=True, index=True
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
    tasks: Mapped[list[Task]] = relationship(
        "Task", back_populates="execution"
    )
    research_requests: Mapped[list[ResearchRequest]] = relationship(
        "ResearchRequest", back_populates="mission_execution"
    )

    def __repr__(self) -> str:
        return f"<MissionExecution id={self.id} mission_id={self.mission_id} state={self.state}>"


class Task(Base):
    """Task database model representing nodes in the Mission execution DAG."""

    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("missions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mission_executions.id", ondelete="SET NULL"), nullable=True, index=True
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

    mission: Mapped[Mission] = relationship("Mission", back_populates="tasks")
    execution: Mapped[MissionExecution | None] = relationship("MissionExecution", back_populates="tasks")

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

    task: Mapped[Task] = relationship("Task", foreign_keys=[task_id], back_populates="upstream_dependencies")
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
        UUID(as_uuid=True), ForeignKey("missions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mission_executions.id", ondelete="SET NULL"), nullable=True, index=True
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
        UUID(as_uuid=True), ForeignKey("channels.id", ondelete="CASCADE"), nullable=False, index=True
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

    status: Mapped[str] = mapped_column(String(50), nullable=False, default="DISCOVERED", index=True)
    reasons: Mapped[list] = mapped_column(JSON, nullable=False, server_default="[]")
    score_breakdown: Mapped[dict] = mapped_column(JSON, nullable=False, server_default="{}")
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSON, nullable=False, server_default="{}"
    )

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
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
    research_requests: Mapped[list[ResearchRequest]] = relationship(
        "ResearchRequest", back_populates="topic_candidate", cascade="all, delete-orphan"
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


# ── OMEGA-005 Research Engine Models ──


class ResearchRequest(Base):
    """ResearchRequest database model representing a research execution request."""

    __tablename__ = "research_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    topic_candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("topic_candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mission_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mission_executions.id", ondelete="SET NULL"), nullable=True, index=True
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
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSON, nullable=False, server_default="{}"
    )

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    channel: Mapped[Channel] = relationship("Channel", back_populates="research_requests")
    topic_candidate: Mapped[TopicCandidate] = relationship("TopicCandidate", back_populates="research_requests")
    mission_execution: Mapped[MissionExecution | None] = relationship("MissionExecution", back_populates="research_requests")

    sources: Mapped[list[ResearchSource]] = relationship(
        "ResearchSource", back_populates="request", cascade="all, delete-orphan", order_by="ResearchSource.retrieved_at.asc()"
    )
    claims: Mapped[list[ResearchClaim]] = relationship(
        "ResearchClaim", back_populates="request", cascade="all, delete-orphan", order_by="ResearchClaim.confidence_score.desc()"
    )
    conflicts: Mapped[list[ResearchConflict]] = relationship(
        "ResearchConflict", back_populates="request", cascade="all, delete-orphan", order_by="ResearchConflict.detected_at.desc()"
    )
    briefs: Mapped[list[ResearchBrief]] = relationship(
        "ResearchBrief", back_populates="request", cascade="all, delete-orphan", order_by="ResearchBrief.version.desc()"
    )

    def __repr__(self) -> str:
        return f"<ResearchRequest id={self.id} channel_id={self.channel_id} status={self.status}>"


class ResearchSource(Base):
    """ResearchSource database model storing bounded normalized research sources."""

    __tablename__ = "research_sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    research_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_type: Mapped[str] = mapped_column(String(50), nullable=False, default="MANUAL")
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    publisher: Mapped[str] = mapped_column(String(200), nullable=False)
    author: Mapped[str | None] = mapped_column(String(200), nullable=True)
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    content_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    primary_source_status: Mapped[str] = mapped_column(String(50), nullable=False, default="UNKNOWN")

    quality_score: Mapped[float] = mapped_column(Float, nullable=False, default=50.0)
    relevance_score: Mapped[float] = mapped_column(Float, nullable=False, default=50.0)
    freshness_score: Mapped[float] = mapped_column(Float, nullable=False, default=50.0)
    quality_reasons: Mapped[list] = mapped_column(JSON, nullable=False, server_default="[]")
    independence_cluster_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    published_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retrieved_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    language: Mapped[str] = mapped_column(String(20), nullable=False, default="en")
    region: Mapped[str] = mapped_column(String(10), nullable=False, default="US")
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSON, nullable=False, server_default="{}"
    )

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
        UUID(as_uuid=True), ForeignKey("research_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channels.id", ondelete="CASCADE"), nullable=False, index=True
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
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSON, nullable=False, server_default="{}"
    )

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
        UUID(as_uuid=True), ForeignKey("research_claims.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_sources.id", ondelete="CASCADE"), nullable=False, index=True
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
        UUID(as_uuid=True), ForeignKey("research_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    claim_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_claims.id", ondelete="SET NULL"), nullable=True, index=True
    )
    conflict_type: Mapped[str] = mapped_column(String(50), nullable=False, default="DIRECT_CONTRADICTION")
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
        UUID(as_uuid=True), ForeignKey("research_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    topic_candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("topic_candidates.id", ondelete="CASCADE"), nullable=False, index=True
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
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSON, nullable=False, server_default="{}"
    )

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    request: Mapped[ResearchRequest] = relationship("ResearchRequest", back_populates="briefs")

    def __repr__(self) -> str:
        return f"<ResearchBrief id={self.id} request_id={self.research_request_id} v={self.version} current={self.is_current}>"
