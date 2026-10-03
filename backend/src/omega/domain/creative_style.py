"""Domain models, types, and validation for P24-B Creative Style Director.

Defines:
- CreativeStylePlan: The derived, production-specific, recomputable creative direction
  pinned to a single immutable ChannelDNARevision.
- CreativeIntensityProfile: Bounded energy/intensity dimensions across modalities.
- CreativeArc & CreativeArcSection: Progressive style evolution across narrative sections.
- NarrativeStyleDirection, VisualStyleDirection, CameraStyleDirection,
  GraphicStyleDirection, AudioStyleDirection, PackagingStyleHints.
- CrossModalCoherenceFinding & CrossModalConflictCode: Detection of cross-modal incongruities.
- StyleConsistencyFinding: Detection of accidental cross-section style drift.
- StyleRationaleEntry: Inspectable provenance for creative decisions.
- CreativeStylePlanValidator: Comprehensive validation against hard constraints and bounds.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from omega.domain.channel_dna import (
    AudioPreferences,
    AvoidPatterns,
    CameraMotionIntensity,
    ContentPillar,
    EditorialVoice,
    FormatOverride,
    HardConstraints,
    NarrativeContextDepth,
    NarrativeHookStyle,
    NarrativePayoffStyle,
    NarrativePreferences,
    PackagingPreferences,
    ResolvedChannelDNA,
    ResolvedPackagingSpec,
    SoftPreferences,
    VisualDensityPreference,
    VisualPreferences,
    VoiceCharacter,
    VoiceDepth,
    VoiceEnergy,
    VoiceExpressiveness,
    VoiceFormality,
    VoiceTechnicality,
)
from omega.domain.narrative_plan import (
    InformationDensity,
    NarrativeFormatProfile,
    NarrativeSectionRole,
)


CREATIVE_STYLE_VERSION = "p24b-v1"


# ── Typed Enums for Creative Direction ──────────────────────────────────────


class HookIntensity(enum.StrEnum):
    SUBTLE = "SUBTLE"
    BALANCED = "BALANCED"
    ENGAGING = "ENGAGING"
    HIGH = "HIGH"


class ExplanationDensity(enum.StrEnum):
    CONCISE = "CONCISE"
    BALANCED = "BALANCED"
    DENSE = "DENSE"


class PayoffEmphasis(enum.StrEnum):
    SUBTLE = "SUBTLE"
    BALANCED = "BALANCED"
    PRONOUNCED = "PRONOUNCED"


class CTAIntensity(enum.StrEnum):
    NONE = "NONE"
    LOW = "LOW"
    BALANCED = "BALANCED"
    DIRECT = "DIRECT"


class CameraStyleIntent(enum.StrEnum):
    """Bounded camera motion style projection for P22-B."""
    STATIC_RESTRAINED = "STATIC_RESTRAINED"
    SUBTLE_MOTION = "SUBTLE_MOTION"
    MODERATE_EDITORIAL_MOTION = "MODERATE_EDITORIAL_MOTION"
    ENERGETIC_MOTION = "ENERGETIC_MOTION"


class GraphicStyleMode(enum.StrEnum):
    """Explanatory graphic and data-vis style mode for P22-C."""
    MINIMAL = "MINIMAL"
    EDITORIAL = "EDITORIAL"
    TECHNICAL = "TECHNICAL"
    DATA_FORWARD = "DATA_FORWARD"
    DOCUMENTARY = "DOCUMENTARY"
    HIGH_CONTRAST = "HIGH_CONTRAST"


class DocumentTreatmentMode(enum.StrEnum):
    CLEAN = "CLEAN"
    ANNOTATED = "ANNOTATED"
    HIGHLIGHTED = "HIGHLIGHTED"


class VisualCompositionCharacter(enum.StrEnum):
    MINIMAL = "MINIMAL"
    EDITORIAL = "EDITORIAL"
    TECHNICAL = "TECHNICAL"
    CINEMATIC = "CINEMATIC"


class CrossModalConflictCode(enum.StrEnum):
    """Structured codes for cross-modal coherence contradictions."""
    CALM_NARRATION_HYPERACTIVE_CAMERA = "CALM_NARRATION_HYPERACTIVE_CAMERA"
    CALM_NARRATION_OVERACTIVE_AUDIO = "CALM_NARRATION_OVERACTIVE_AUDIO"
    HIGH_ENERGY_HOOK_INERT_VISUALS = "HIGH_ENERGY_HOOK_INERT_VISUALS"
    UNJUSTIFIED_AUDIO_SPIKE = "UNJUSTIFIED_AUDIO_SPIKE"
    CONTRADICTORY_GRAPHIC_DENSITY = "CONTRADICTORY_GRAPHIC_DENSITY"
    HARD_CONSTRAINT_STYLE_VIOLATION = "HARD_CONSTRAINT_STYLE_VIOLATION"


class StyleDriftCode(enum.StrEnum):
    """Structured codes for accidental cross-section style drift."""
    NARRATIVE_STYLE_DRIFT = "NARRATIVE_STYLE_DRIFT"
    VISUAL_STYLE_DRIFT = "VISUAL_STYLE_DRIFT"
    CAMERA_STYLE_DRIFT = "CAMERA_STYLE_DRIFT"
    AUDIO_STYLE_DRIFT = "AUDIO_STYLE_DRIFT"
    GRAPHIC_STYLE_DRIFT = "GRAPHIC_STYLE_DRIFT"


class CreativeStyleFindingCode(enum.StrEnum):
    """Validation finding codes for CreativeStylePlan."""
    ENERGY_OUT_OF_BOUNDS = "ENERGY_OUT_OF_BOUNDS"
    HARD_CONSTRAINT_VIOLATION = "HARD_CONSTRAINT_VIOLATION"
    AVOID_PATTERN_BREACH = "AVOID_PATTERN_BREACH"
    CONTRADICTORY_STYLE = "CONTRADICTORY_STYLE"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    INVALID_PILLAR_REFERENCE = "INVALID_PILLAR_REFERENCE"
    INVALID_ARC_ORDERING = "INVALID_ARC_ORDERING"
    CROSS_MODAL_CONFLICT = "CROSS_MODAL_CONFLICT"
    MISSING_LINEAGE = "MISSING_LINEAGE"


# ── Granular Direction Models ────────────────────────────────────────────────


class NarrativeStyleDirection(BaseModel):
    """Production-specific narrative style guidance for P21 consumption."""
    hook_intensity: HookIntensity = HookIntensity.BALANCED
    context_depth: NarrativeContextDepth = NarrativeContextDepth.MODERATE
    explanation_density: ExplanationDensity = ExplanationDensity.BALANCED
    editorial_energy: VoiceEnergy = VoiceEnergy.MODERATE
    payoff_emphasis: PayoffEmphasis = PayoffEmphasis.BALANCED
    cta_intensity: CTAIntensity = CTAIntensity.BALANCED
    formality: VoiceFormality = VoiceFormality.SEMI_FORMAL
    technicality: VoiceTechnicality = VoiceTechnicality.BALANCED
    expressiveness: VoiceExpressiveness = VoiceExpressiveness.OBJECTIVE
    notes: str | None = None

    model_config = ConfigDict(frozen=True)


class VisualStyleDirection(BaseModel):
    """Production-specific visual style guidance for P22 consumption."""
    visual_density: VisualDensityPreference = VisualDensityPreference.MEDIUM
    evidence_emphasis: str = "HIGH"
    b_roll_tendency: str = "TARGETED"
    diagram_tendency: str = "SELECTIVE"
    document_treatment: DocumentTreatmentMode = DocumentTreatmentMode.CLEAN
    comparison_treatment: str = "SIDE_BY_SIDE"
    motion_intensity: CameraMotionIntensity = CameraMotionIntensity.RESTRAINED
    camera_restraint: bool = True
    transition_restraint: bool = True
    composition_character: VisualCompositionCharacter = VisualCompositionCharacter.EDITORIAL

    model_config = ConfigDict(frozen=True)


class CameraStyleDirection(BaseModel):
    """Bounded camera/motion style direction for P22-B."""
    camera_style_intent: CameraStyleIntent = CameraStyleIntent.SUBTLE_MOTION
    max_camera_energy: float = Field(default=0.6, ge=0.0, le=1.0)
    pan_allowed: bool = True
    tilt_allowed: bool = True
    zoom_allowed: bool = True
    abrupt_moves_forbidden: bool = True

    model_config = ConfigDict(frozen=True)


class GraphicStyleDirection(BaseModel):
    """Coordination guidance for P22-C explanatory graphics and diagrams."""
    graphic_mode: GraphicStyleMode = GraphicStyleMode.EDITORIAL
    high_contrast: bool = False
    annotation_density: str = "BALANCED"  # "LIGHT", "BALANCED", "HEAVY"
    color_palette_hint: str | None = None

    model_config = ConfigDict(frozen=True)


class AudioStyleDirection(BaseModel):
    """Production-specific audio style guidance for P23."""
    music_tendency: str = "SUBTLE_BACKGROUND"
    target_music_energy: float = Field(default=0.45, ge=0.0, le=1.0)
    music_energy_min: float = Field(default=0.2, ge=0.0, le=1.0)
    music_energy_max: float = Field(default=0.6, ge=0.0, le=1.0)
    vocal_policy: str = "INSTRUMENTAL_ONLY"
    sfx_density: str = "BALANCED"
    sfx_prominence: str = "BALANCED"  # "SUBTLE", "BALANCED", "PROMINENT"
    silence_permitted: bool = True
    audio_character: str = "CINEMATIC_AMBIENT"

    model_config = ConfigDict(frozen=True)


class MusicStyleConstraints(BaseModel):
    """Constraints on music selection and energy."""
    instrumental_only: bool = True
    max_energy_ceiling: float = Field(default=0.8, ge=0.0, le=1.0)
    min_energy_floor: float = Field(default=0.0, ge=0.0, le=1.0)
    silence_for_serious_claims: bool = True
    prohibited_genres: tuple[str, ...] = ()

    model_config = ConfigDict(frozen=True)


class SFXStyleConstraints(BaseModel):
    """Constraints on SFX selection and density."""
    sfx_allowed: bool = True
    max_density: str = "BALANCED"
    max_sfx_per_minute: int = Field(default=12, ge=0, le=60)
    prohibit_constant_sfx: bool = True
    no_sfx_during_serious_claims: bool = True

    model_config = ConfigDict(frozen=True)


class PackagingStyleHints(BaseModel):
    """Style projection prepared for P24-C packaging generation (NO TITLES/THUMBNAILS GENERATED)."""
    title_tone: str = "FACTUAL_COMPELLING"
    title_restraint: str = "FACTUAL_RESTRAINED"  # "FACTUAL_RESTRAINED", "BALANCED", "INTRIGUING"
    title_length_tendency: str = "CONCISE"
    thumbnail_density: str = "MINIMAL_TO_MODERATE"
    thumbnail_text_policy: str = "MAX_3_WORDS"
    thumbnail_emotional_intensity: str = "MEASURED"  # "SUBTLE", "MEASURED", "EXPRESSIVE"
    description_voice: str = "STRUCTURED_OUTLINE"
    metadata_voice: str = "OBJECTIVE"
    chapter_style: str = "SECTION_BASED"
    clickbait_tolerance: str = "ZERO_TOLERANCE"
    prohibited_patterns: tuple[str, ...] = ()

    model_config = ConfigDict(frozen=True)


# ── Creative Intensity & Arc ────────────────────────────────────────────────


class CreativeIntensityProfile(BaseModel):
    """Bounded creative intensity axes in [0.0, 1.0]."""
    narrative_energy: float = Field(default=0.5, ge=0.0, le=1.0)
    visual_energy: float = Field(default=0.5, ge=0.0, le=1.0)
    camera_energy: float = Field(default=0.4, ge=0.0, le=1.0)
    music_energy: float = Field(default=0.5, ge=0.0, le=1.0)
    sfx_energy: float = Field(default=0.4, ge=0.0, le=1.0)
    graphic_density: float = Field(default=0.5, ge=0.0, le=1.0)

    model_config = ConfigDict(frozen=True)


class CreativeArcSection(BaseModel):
    """Planned creative style evolution for a specific narrative section."""
    section_role: NarrativeSectionRole
    section_order: int = Field(ge=1)
    target_energy: float = Field(ge=0.0, le=1.0)
    visual_focus: str
    camera_motion: CameraStyleIntent
    audio_intent: str
    justified_escalation: bool = False
    rationale: str

    model_config = ConfigDict(frozen=True)


class CreativeArc(BaseModel):
    """The progression of creative style across narrative sections."""
    sections: tuple[CreativeArcSection, ...]
    overall_pacing: str = "BALANCED"

    model_config = ConfigDict(frozen=True)


# ── Findings and Provenance ──────────────────────────────────────────────────


class CrossModalCoherenceFinding(BaseModel):
    """Structured finding when modal styles conflict or violate coherence."""
    code: CrossModalConflictCode
    severity: Literal["INFO", "WARNING", "ERROR"] = "WARNING"
    modalities: tuple[str, ...]
    explanation: str
    recommended_remediation: str

    model_config = ConfigDict(frozen=True)


class StyleConsistencyFinding(BaseModel):
    """Finding when style shifts between sections without arc justification."""
    code: StyleDriftCode
    severity: Literal["INFO", "WARNING"] = "WARNING"
    from_section_order: int
    to_section_order: int
    explanation: str
    recommended_remediation: str

    model_config = ConfigDict(frozen=True)


class StyleRationaleEntry(BaseModel):
    """Inspectable provenance for a specific creative style choice."""
    aspect: str  # e.g. "camera_motion", "music_energy", "narrative_energy"
    decision: str
    contributing_source: str  # e.g. "ChannelDNA.visual_preferences", "NarrativePlan.role"
    explanation: str

    model_config = ConfigDict(frozen=True)


class CreativeStyleFinding(BaseModel):
    """Validation finding for a CreativeStylePlan."""
    code: CreativeStyleFindingCode
    severity: Literal["INFO", "WARNING", "ERROR", "BLOCKER"]
    field: str
    explanation: str
    recommended_remediation: str

    model_config = ConfigDict(frozen=True)


class CreativeStyleValidationResult(BaseModel):
    """Outcome of validating a CreativeStylePlan."""
    is_valid: bool
    findings: tuple[CreativeStyleFinding, ...]
    error_count: int
    warning_count: int

    model_config = ConfigDict(frozen=True)


# ── The Canonical CreativeStylePlan ──────────────────────────────────────────


class CreativeStylePlan(BaseModel):
    """Canonical derived, production-specific creative direction for an Omega video.

    Answers: How should THIS production express the pinned Channel DNA?
    Strictly pinned to a single ChannelDNARevision.
    Derived, recomputable, never a competing mutable channel root.
    """
    plan_id: UUID = Field(default_factory=uuid.uuid4)
    channel_dna_revision_id: UUID
    content_generation_request_id: UUID | None = None
    narrative_plan_id: UUID | None = None
    format_profile: NarrativeFormatProfile = NarrativeFormatProfile.MEDIUM
    content_pillar_id: str | None = None

    # Granular modality directions
    narrative_style: NarrativeStyleDirection = Field(default_factory=NarrativeStyleDirection)
    visual_style: VisualStyleDirection = Field(default_factory=VisualStyleDirection)
    camera_style: CameraStyleDirection = Field(default_factory=CameraStyleDirection)
    graphic_style: GraphicStyleDirection = Field(default_factory=GraphicStyleDirection)
    audio_style: AudioStyleDirection = Field(default_factory=AudioStyleDirection)
    music_constraints: MusicStyleConstraints = Field(default_factory=MusicStyleConstraints)
    sfx_constraints: SFXStyleConstraints = Field(default_factory=SFXStyleConstraints)
    packaging_hints: PackagingStyleHints = Field(default_factory=PackagingStyleHints)

    # Coordinated intensity and arc
    creative_intensity: CreativeIntensityProfile = Field(default_factory=CreativeIntensityProfile)
    creative_arc: CreativeArc = Field(default_factory=lambda: CreativeArc(sections=()))

    # Binding hard constraints propagated from ChannelDNA v2 (immutable, non-negotiable)
    hard_constraints_binding: HardConstraints = Field(default_factory=HardConstraints)

    # Coherence and quality findings
    coherence_findings: tuple[CrossModalCoherenceFinding, ...] = ()
    style_consistency_findings: tuple[StyleConsistencyFinding, ...] = ()
    style_rationale: tuple[StyleRationaleEntry, ...] = ()

    # Provenance
    version: str = CREATIVE_STYLE_VERSION
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    model_config = ConfigDict(extra="ignore", frozen=True)

    # ── Handoff Projections ──────────────────────────────────────────────────

    def to_p21_style_projection(self) -> dict[str, Any]:
        """Expose clean projection for P21 Narrative Director / Retention Pacing."""
        return {
            "hook_intensity": self.narrative_style.hook_intensity.value,
            "context_depth": self.narrative_style.context_depth.value,
            "explanation_density": self.narrative_style.explanation_density.value,
            "editorial_energy": self.narrative_style.editorial_energy.value,
            "payoff_emphasis": self.narrative_style.payoff_emphasis.value,
            "cta_intensity": self.narrative_style.cta_intensity.value,
            "formality": self.narrative_style.formality.value,
            "technicality": self.narrative_style.technicality.value,
            "expressiveness": self.narrative_style.expressiveness.value,
            "hard_constraints": self.hard_constraints_binding.model_dump(),
        }

    def to_p22_style_projection(self) -> dict[str, Any]:
        """Expose clean projection for P22 Visual Director / Editorial QA."""
        return {
            "visual_density": self.visual_style.visual_density.value,
            "evidence_emphasis": self.visual_style.evidence_emphasis,
            "b_roll_tendency": self.visual_style.b_roll_tendency,
            "diagram_tendency": self.visual_style.diagram_tendency,
            "document_treatment": self.visual_style.document_treatment.value,
            "comparison_treatment": self.visual_style.comparison_treatment,
            "motion_intensity": self.visual_style.motion_intensity.value,
            "camera_style_intent": self.camera_style.camera_style_intent.value,
            "max_camera_energy": self.camera_style.max_camera_energy,
            "transition_restraint": self.visual_style.transition_restraint,
            "graphic_mode": self.graphic_style.graphic_mode.value,
            "hard_constraints": self.hard_constraints_binding.model_dump(),
        }

    def to_p23_style_projection(self) -> dict[str, Any]:
        """Expose clean projection for P23 Music Director / SFX Director."""
        return {
            "music_tendency": self.audio_style.music_tendency,
            "target_music_energy": self.audio_style.target_music_energy,
            "music_energy_range": (self.audio_style.music_energy_min, self.audio_style.music_energy_max),
            "vocal_policy": self.audio_style.vocal_policy,
            "sfx_density": self.audio_style.sfx_density,
            "sfx_prominence": self.audio_style.sfx_prominence,
            "silence_permitted": self.audio_style.silence_permitted,
            "audio_character": self.audio_style.audio_character,
            "music_constraints": self.music_constraints.model_dump(),
            "sfx_constraints": self.sfx_constraints.model_dump(),
            "hard_constraints": self.hard_constraints_binding.model_dump(),
        }

    def to_p24c_packaging_hints(self) -> PackagingStyleHints:
        """Expose clean projection for P24-C packaging generation."""
        return self.packaging_hints

    def to_p24d_qa_context(self) -> dict[str, Any]:
        """Expose full provenance and constraints for P24-D creative QA."""
        return {
            "plan_id": str(self.plan_id),
            "channel_dna_revision_id": str(self.channel_dna_revision_id),
            "hard_constraints_binding": self.hard_constraints_binding.model_dump(),
            "coherence_findings": [f.model_dump() for f in self.coherence_findings],
            "style_consistency_findings": [f.model_dump() for f in self.style_consistency_findings],
            "creative_intensity": self.creative_intensity.model_dump(),
        }


# ── Validator ───────────────────────────────────────────────────────────────


class CreativeStylePlanValidator:
    """Validates CreativeStylePlan against hard constraints, ranges, and coherence rules."""

    @classmethod
    def validate(cls, plan: CreativeStylePlan, channel_pillars: list[ContentPillar] | None = None) -> CreativeStyleValidationResult:
        findings: list[CreativeStyleFinding] = []

        # 1. Lineage
        if not plan.channel_dna_revision_id:
            findings.append(
                CreativeStyleFinding(
                    code=CreativeStyleFindingCode.MISSING_LINEAGE,
                    severity="BLOCKER",
                    field="channel_dna_revision_id",
                    explanation="CreativeStylePlan must be pinned to a ChannelDNARevision.",
                    recommended_remediation="Provide valid channel_dna_revision_id.",
                )
            )

        # 2. Creative intensity bounds
        for field_name in ("narrative_energy", "visual_energy", "camera_energy", "music_energy", "sfx_energy", "graphic_density"):
            val = getattr(plan.creative_intensity, field_name)
            if not (0.0 <= val <= 1.0):
                findings.append(
                    CreativeStyleFinding(
                        code=CreativeStyleFindingCode.ENERGY_OUT_OF_BOUNDS,
                        severity="ERROR",
                        field=f"creative_intensity.{field_name}",
                        explanation=f"Intensity {field_name}={val} is outside [0.0, 1.0].",
                        recommended_remediation="Clamp energy value between 0.0 and 1.0.",
                    )
                )

        # 3. Audio energy range
        if plan.audio_style.music_energy_min > plan.audio_style.music_energy_max:
            findings.append(
                CreativeStyleFinding(
                    code=CreativeStyleFindingCode.CONTRADICTORY_STYLE,
                    severity="ERROR",
                    field="audio_style.music_energy_min",
                    explanation=f"Music energy min ({plan.audio_style.music_energy_min}) exceeds max ({plan.audio_style.music_energy_max}).",
                    recommended_remediation="Ensure music_energy_min <= music_energy_max.",
                )
            )

        # 4. Hard constraints vs style instructions
        hard = plan.hard_constraints_binding
        if hard.no_misleading_clickbait and plan.packaging_hints.clickbait_tolerance != "ZERO_TOLERANCE":
            findings.append(
                CreativeStyleFinding(
                    code=CreativeStyleFindingCode.HARD_CONSTRAINT_VIOLATION,
                    severity="ERROR",
                    field="packaging_hints.clickbait_tolerance",
                    explanation="Channel hard constraint forbids clickbait, but packaging hints allow it.",
                    recommended_remediation="Set clickbait_tolerance='ZERO_TOLERANCE'.",
                )
            )

        # 5. Content pillar validation
        if plan.content_pillar_id and channel_pillars:
            valid_ids = {p.pillar_id for p in channel_pillars}
            if plan.content_pillar_id not in valid_ids:
                findings.append(
                    CreativeStyleFinding(
                        code=CreativeStyleFindingCode.INVALID_PILLAR_REFERENCE,
                        severity="ERROR",
                        field="content_pillar_id",
                        explanation=f"Referenced pillar '{plan.content_pillar_id}' not found in channel content pillars.",
                        recommended_remediation=f"Reference one of: {sorted(valid_ids)}.",
                    )
                )

        # 6. Creative arc ordering
        if plan.creative_arc.sections:
            orders = [s.section_order for s in plan.creative_arc.sections]
            if orders != sorted(orders):
                findings.append(
                    CreativeStyleFinding(
                        code=CreativeStyleFindingCode.INVALID_ARC_ORDERING,
                        severity="ERROR",
                        field="creative_arc.sections",
                        explanation="Creative arc section orders must be strictly monotonically increasing.",
                        recommended_remediation="Sort creative arc sections by section_order.",
                    )
                )

        # 7. Check cross-modal error findings
        for cm in plan.coherence_findings:
            if cm.severity == "ERROR":
                findings.append(
                    CreativeStyleFinding(
                        code=CreativeStyleFindingCode.CROSS_MODAL_CONFLICT,
                        severity="ERROR",
                        field=f"coherence.{cm.code.value}",
                        explanation=cm.explanation,
                        recommended_remediation=cm.recommended_remediation,
                    )
                )

        has_errors = any(f.severity in ("ERROR", "BLOCKER") for f in findings)
        return CreativeStyleValidationResult(
            is_valid=not has_errors,
            findings=tuple(findings),
            error_count=sum(1 for f in findings if f.severity in ("ERROR", "BLOCKER")),
            warning_count=sum(1 for f in findings if f.severity == "WARNING"),
        )
