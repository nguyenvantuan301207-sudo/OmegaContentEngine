"""Content Quality Detector.

Wraps ContentQAAdapter to evaluate script narrative quality, factual provenance,
forbidden vocabulary, and structural rules.
Failure policy is REQUIRE_REVIEW.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from omega.application.guardian.adapters.content_qa_adapter import ContentQAAdapter
from omega.application.guardian.detectors.base import BaseDetector, GuardianEvaluationContext
from omega.domain.guardian import (
    DetectorFailurePolicy,
    GuardianCheckpoint,
    GuardianFindingData,
)
from omega.infrastructure.models import (
    Channel,
    ChannelDNARevision,
    ContentGenerationRequest,
    Mission,
    MissionExecution,
    ResearchBrief,
    ScriptSection,
    ScriptStatement,
    ScriptVersion,
)


class ContentQualityDetector(BaseDetector):
    """Detects content quality violations by evaluating script narrative and citations."""

    detector_type = "CONTENT_QUALITY"
    detector_version = "1.0.0"
    supported_checkpoints = {GuardianCheckpoint.PRE_TASK_DISPATCH, GuardianCheckpoint.PRE_RENDER}
    failure_policy = DetectorFailurePolicy.REQUIRE_REVIEW

    async def evaluate(
        self,
        context: GuardianEvaluationContext,
        session_factory: Callable[[], AsyncSession],
    ) -> list[GuardianFindingData]:
        findings: list[GuardianFindingData] = []
        diag = context.diagnostic_context or {}

        # 1. Direct evaluation from diagnostic payload if provided
        if "script_data" in diag:
            script_data = diag["script_data"]
            target_dur = int(diag.get("target_duration_seconds", 60))
            dna_dict = diag.get("dna_dict", {})
            brief_dict = diag.get("brief_dict", {})
            return ContentQAAdapter.evaluate(script_data, target_dur, dna_dict, brief_dict)

        # 2. Database resolution via mission -> content request -> script version
        async with session_factory() as session:
            # Load mission with channel
            mission_res = await session.execute(
                select(Mission).where(Mission.id == context.mission_id)
            )
            mission = mission_res.scalar_one_or_none()
            if not mission:
                return findings

            # Load active channel DNA
            dna_dict = {}
            if mission.channel_id:
                dna_res = await session.execute(
                    select(ChannelDNARevision)
                    .where(ChannelDNARevision.channel_id == mission.channel_id)
                    .order_by(ChannelDNARevision.version.desc())
                    .limit(1)
                )
                active_dna = dna_res.scalar_one_or_none()
                if active_dna:
                    dna_dict = active_dna.snapshot or {}
                else:
                    chan_res = await session.execute(
                        select(Channel).where(Channel.id == mission.channel_id)
                    )
                    chan = chan_res.scalar_one_or_none()
                    if chan:
                        dna_dict = chan.dna or {}

            # Load script version linked to mission
            script_res = await session.execute(
                select(ScriptVersion)
                .join(
                    ContentGenerationRequest,
                    ContentGenerationRequest.id == ScriptVersion.content_request_id,
                )
                .join(
                    MissionExecution,
                    MissionExecution.id == ContentGenerationRequest.mission_execution_id,
                )
                .where(MissionExecution.mission_id == context.mission_id)
                .options(
                    selectinload(ScriptVersion.sections)
                    .selectinload(ScriptSection.statements)
                    .selectinload(ScriptStatement.citations),
                    selectinload(ScriptVersion.content_request),
                )
                .order_by(ScriptVersion.version.desc())
                .limit(1)
            )
            script_version = script_res.scalar_one_or_none()
            if not script_version:
                return findings

            # Load research brief if linked
            brief_dict = {}
            if script_version.content_request and script_version.content_request.research_brief_id:
                brief_res = await session.execute(
                    select(ResearchBrief).where(
                        ResearchBrief.id == script_version.content_request.research_brief_id
                    )
                )
                brief = brief_res.scalar_one_or_none()
                if brief:
                    brief_dict = {
                        "contradictions": [
                            {"severity": "HIGH", "claim_id": str(c.get("claim_id"))}
                            for c in (brief.key_conflicts or [])
                        ]
                    }

            # Build script_data dict
            sections_data = []
            for sec in script_version.sections:
                stmts_data = []
                for stmt in sec.statements:
                    stmts_data.append(
                        {
                            "statement_order": stmt.statement_order,
                            "statement_text": stmt.statement_text,
                            "statement_type": stmt.statement_type,
                            "citations": [
                                {"claim_id": str(cit.claim_id)} for cit in stmt.citations
                            ],
                        }
                    )
                sections_data.append(
                    {
                        "heading": sec.heading,
                        "narration_text": sec.narration_text,
                        "statements": stmts_data,
                    }
                )

            script_data = {
                "hook_text": script_version.hook_text,
                "closing_text": script_version.closing_text or "",
                "cta_text": script_version.cta_text or "",
                "estimated_duration_seconds": script_version.estimated_duration_seconds,
                "sections": sections_data,
            }

            target_dur = script_version.target_duration_seconds or 60
            return ContentQAAdapter.evaluate(script_data, target_dur, dna_dict, brief_dict)
