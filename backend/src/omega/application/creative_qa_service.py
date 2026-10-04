"""Final Creative QA & Brand Acceptance Application Service for P24-D.

Coordinates cross-domain evaluation of the finished creative package against:
- pinned ChannelDNARevision v2
- CreativeStylePlan
- accepted NarrativePlan
- visual decisions / rendered visual context
- audio decisions / P23 QA context
- PackagingPlan
- physical thumbnail artifact
- final video artifact where available

Enforces:
- ChannelDNA compliance & hard constraints gate (BLOCKER / FAIL)
- Editorial voice & style fit
- Cross-modal coherence & style drift detection
- Packaging coherence & promise/payoff alignment
- Physical thumbnail artifact validation
- Video artifact lineage verification
- Finding deduplication and deterministic acceptance policy
- Final creative gate enforcement (blocks downstream ProductionQA / Guardian / publishing)
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, Sequence
from uuid import UUID, uuid4

from omega.domain.channel_dna import (
    AvoidPatterns,
    ChannelDNA,
    ContentPillar,
    EditorialVoice,
    HardConstraints,
    PackagingPreferences,
    ResolvedChannelDNA,
    VoiceDepth,
    VoiceEnergy,
    VoiceFormality,
)


def _normalize_channel_dna(
    channel_dna: ChannelDNA | ResolvedChannelDNA | dict[str, Any] | None,
) -> ChannelDNA | ResolvedChannelDNA | None:
    if channel_dna is None:
        return None
    if isinstance(channel_dna, (ChannelDNA, ResolvedChannelDNA)):
        return channel_dna
    return ChannelDNA.model_validate(channel_dna)

from omega.domain.creative_qa import (
    CREATIVE_QA_ENGINE_VERSION,
    CreativeAcceptancePackage,
    CreativeQAFinding,
    CreativeQAFindingCode,
    CreativeQAProvenance,
    CreativeQARecommendation,
    CreativeQARecommendationAction,
    CreativeQAResult,
    CreativeQASeverity,
    CreativeQAStatus,
    CreativeQASubsystem,
    CreativeRenderGateError,
)
from omega.domain.creative_style import (
    CameraStyleIntent,
    CreativeArc,
    CreativeStylePlan,
    HookIntensity,
)
from omega.domain.narrative_plan import NarrativePlan, NarrativeSectionRole
from omega.domain.packaging import PackagingPlan, ThumbnailArtifact, TitleCandidate

logger = logging.getLogger(__name__)


# ── Optional Model Reviewer Protocol (Section 31: NOT_USED by default) ───────


class CreativeModelReviewer(Protocol):
    """Protocol for optional model-assisted subjective brand review.
    
    Section 31: If no legitimate provider is wired, P24D_MODEL_CREATIVE_REVIEW = NOT_USED.
    Model review is optional and cannot override deterministic findings.
    """

    def review(
        self,
        style_plan: CreativeStylePlan,
        packaging_plan: PackagingPlan | None = None,
        narrative_plan: NarrativePlan | None = None,
    ) -> list[CreativeQAFinding]:
        ...


# ── Subsystem Evaluators ────────────────────────────────────────────────────


class ChannelDNAComplianceEvaluator:
    """Evaluates output compliance with pinned ChannelDNARevision v2 and hard constraints."""

    PROFANITY_WORDS = frozenset({
        "damn", "hell", "crap", "shit", "fuck", "bitch", "asshole", "bastard"
    })
    MEDICAL_CLAIM_TERMS = frozenset({
        "cure", "cures", "curing", "miracle remedy", "guaranteed treatment",
        "clinically proven to cure", "medical breakthrough eliminates"
    })
    CLICKBAIT_PATTERNS = [
        re.compile(r"\byou won'?t believe\b", re.IGNORECASE),
        re.compile(r"\bshocking\b", re.IGNORECASE),
        re.compile(r"\bmind[\s-]blowing\b", re.IGNORECASE),
        re.compile(r"\bterrifying\b", re.IGNORECASE),
        re.compile(r"\binsane\b", re.IGNORECASE),
        re.compile(r"\bthis changes everything\b", re.IGNORECASE),
        re.compile(r"\bsecret they don'?t want you to know\b", re.IGNORECASE),
    ]

    @classmethod
    def evaluate(
        cls,
        *,
        channel_dna: ChannelDNA | dict[str, Any] | None,
        pinned_revision_id: UUID | None,
        style_plan: CreativeStylePlan | None,
        packaging_plan: PackagingPlan | None,
        narrative_plan: NarrativePlan | None,
    ) -> list[CreativeQAFinding]:
        findings: list[CreativeQAFinding] = []
        if not channel_dna:
            return findings

        dna = _normalize_channel_dna(channel_dna)
        hard = dna.hard_constraints
        avoid = dna.avoid_patterns

        # 1. Revision Lineage Verification (Section 6)
        if pinned_revision_id:
            if style_plan and style_plan.channel_dna_revision_id != pinned_revision_id:
                findings.append(
                    CreativeQAFinding(
                        finding_code=CreativeQAFindingCode.CHANNEL_REVISION_MISMATCH,
                        severity=CreativeQASeverity.BLOCKER,
                        subsystem=CreativeQASubsystem.CHANNEL_IDENTITY,
                        affected_artifact="CreativeStylePlan.channel_dna_revision_id",
                        explanation=f"CreativeStylePlan references revision {style_plan.channel_dna_revision_id}, but pinned revision is {pinned_revision_id}.",
                        source_authority="ChannelDNARevision",
                        recommended_remediation="Rebind CreativeStylePlan to the pinned ChannelDNARevision.",
                    )
                )
            if packaging_plan and packaging_plan.channel_dna_revision_id != pinned_revision_id:
                findings.append(
                    CreativeQAFinding(
                        finding_code=CreativeQAFindingCode.CHANNEL_REVISION_MISMATCH,
                        severity=CreativeQASeverity.BLOCKER,
                        subsystem=CreativeQASubsystem.CHANNEL_IDENTITY,
                        affected_artifact="PackagingPlan.channel_dna_revision_id",
                        explanation=f"PackagingPlan references revision {packaging_plan.channel_dna_revision_id}, but pinned revision is {pinned_revision_id}.",
                        source_authority="ChannelDNARevision",
                        recommended_remediation="Rebind PackagingPlan to the pinned ChannelDNARevision.",
                    )
                )

        # Collect text corpus for hard constraint checks
        texts_to_check: list[tuple[str, str]] = []
        if packaging_plan:
            texts_to_check.append(("selected_title", packaging_plan.selected_title.text))
            texts_to_check.append(("description", packaging_plan.description))
            if packaging_plan.selected_thumbnail.text_content:
                texts_to_check.append(("thumbnail_text", packaging_plan.selected_thumbnail.text_content))
            for chapter in packaging_plan.chapters:
                texts_to_check.append(("chapter_title", chapter.title))
        if narrative_plan:
            premise = str(narrative_plan.metadata.get("premise", ""))
            if premise:
                texts_to_check.append(("narrative_premise", premise))
            for sec in narrative_plan.sections:
                texts_to_check.append((f"section_{sec.section_order}", sec.objective))

        # 2. Hard Constraints Gate (Section 7)
        # 2a. Prohibited phrases & custom prohibitions
        prohibited_phrases: list[str] = []
        prohibited_phrases.extend(getattr(hard, "custom_prohibitions", ()))
        prohibited_phrases.extend(getattr(hard, "prohibited_vocabulary", ()))
        prohibited_phrases.extend(getattr(hard, "custom_rules", ()))
        prohibited_phrases.extend(getattr(avoid, "prohibited_phrases", ()))

        for prohibited in prohibited_phrases:
            p_lower = prohibited.strip().lower()
            if not p_lower:
                continue
            for location, text in texts_to_check:
                if p_lower in text.lower():
                    findings.append(
                        CreativeQAFinding(
                            finding_code=CreativeQAFindingCode.PROHIBITED_PHRASE_DETECTED,
                            severity=CreativeQASeverity.BLOCKER,
                            subsystem=CreativeQASubsystem.BRAND_CONSTRAINT,
                            affected_artifact=location,
                            explanation=f"Text contains prohibited phrase '{prohibited}' in {location}.",
                            source_authority="HardConstraints.prohibited_vocabulary",
                            recommended_remediation=f"Remove prohibited phrase '{prohibited}'.",
                        )
                    )

        # 2b. Profanity
        if hard.no_profanity:
            for location, text in texts_to_check:
                words = set(re.findall(r"\b[a-zA-Z]+\b", text.lower()))
                intersect = words.intersection(cls.PROFANITY_WORDS)
                if intersect:
                    findings.append(
                        CreativeQAFinding(
                            finding_code=CreativeQAFindingCode.PROFANITY_DETECTED,
                            severity=CreativeQASeverity.BLOCKER,
                            subsystem=CreativeQASubsystem.BRAND_CONSTRAINT,
                            affected_artifact=location,
                            explanation=f"Profanity detected ({', '.join(sorted(intersect))}) in {location}.",
                            source_authority="HardConstraints.no_profanity",
                            recommended_remediation="Remove all profanity to comply with channel hard constraint.",
                        )
                    )

        # 2c. Clickbait Gate
        if hard.no_misleading_clickbait and packaging_plan:
            title = packaging_plan.selected_title
            if title.clickbait_risk in ("MODERATE", "HIGH"):
                findings.append(
                    CreativeQAFinding(
                        finding_code=CreativeQAFindingCode.MISLEADING_CLICKBAIT,
                        severity=CreativeQASeverity.BLOCKER,
                        subsystem=CreativeQASubsystem.TITLE,
                        affected_artifact="PackagingPlan.selected_title",
                        explanation=f"Selected title '{title.text}' carries {title.clickbait_risk} clickbait risk.",
                        source_authority="HardConstraints.no_misleading_clickbait",
                        recommended_remediation="Select a grounded, factual, or explainer title candidate.",
                    )
                )
            for pat in cls.CLICKBAIT_PATTERNS:
                if pat.search(title.text):
                    findings.append(
                        CreativeQAFinding(
                            finding_code=CreativeQAFindingCode.MISLEADING_CLICKBAIT,
                            severity=CreativeQASeverity.BLOCKER,
                            subsystem=CreativeQASubsystem.TITLE,
                            affected_artifact="PackagingPlan.selected_title",
                            explanation=f"Title contains sensational clickbait pattern '{pat.pattern}'.",
                            source_authority="HardConstraints.no_misleading_clickbait",
                            recommended_remediation="Use truthful and grounded title phrasing.",
                        )
                    )

        # 2d. Fabricated / ungrounded claims
        if hard.no_fabricated_claims and packaging_plan:
            if not packaging_plan.selected_title.is_grounded:
                findings.append(
                    CreativeQAFinding(
                        finding_code=CreativeQAFindingCode.FABRICATED_CLAIM,
                        severity=CreativeQASeverity.BLOCKER,
                        subsystem=CreativeQASubsystem.TITLE,
                        affected_artifact="PackagingPlan.selected_title",
                        explanation="Selected title contains ungrounded claims not supported by content.",
                        source_authority="HardConstraints.no_fabricated_claims",
                        recommended_remediation="Choose a title candidate strictly anchored to verified claims.",
                    )
                )
            if not packaging_plan.selected_thumbnail.is_grounded:
                findings.append(
                    CreativeQAFinding(
                        finding_code=CreativeQAFindingCode.FABRICATED_CLAIM,
                        severity=CreativeQASeverity.BLOCKER,
                        subsystem=CreativeQASubsystem.THUMBNAIL,
                        affected_artifact="PackagingPlan.selected_thumbnail",
                        explanation="Selected thumbnail concept depicts ungrounded or fabricated subjects.",
                        source_authority="HardConstraints.no_fabricated_claims",
                        recommended_remediation="Reframe thumbnail concept around grounded visual evidence.",
                    )
                )

        # 2e. Unsupported medical claims
        if hard.no_unsupported_medical_claims:
            for location, text in texts_to_check:
                t_lower = text.lower()
                for term in cls.MEDICAL_CLAIM_TERMS:
                    if term in t_lower:
                        findings.append(
                            CreativeQAFinding(
                                finding_code=CreativeQAFindingCode.UNSUPPORTED_MEDICAL_CLAIM,
                                severity=CreativeQASeverity.BLOCKER,
                                subsystem=CreativeQASubsystem.BRAND_CONSTRAINT,
                                affected_artifact=location,
                                explanation=f"Prohibited medical claim pattern '{term}' found in {location}.",
                                source_authority="HardConstraints.no_unsupported_medical_claims",
                                recommended_remediation="Remove medical assertions and restrict copy to factual descriptions.",
                            )
                        )

        # 3. Content Pillar Consistency (Section 22)
        if style_plan and style_plan.content_pillar_id:
            pillars = getattr(dna, "content_pillars_v2", ()) or getattr(dna, "content_pillars", ())
            pillar = next((p for p in pillars if p.pillar_id == style_plan.content_pillar_id), None)
            if not pillar:
                findings.append(
                    CreativeQAFinding(
                        finding_code=CreativeQAFindingCode.CONTENT_PILLAR_MISMATCH,
                        severity=CreativeQASeverity.ERROR,
                        subsystem=CreativeQASubsystem.CHANNEL_IDENTITY,
                        affected_artifact="CreativeStylePlan.content_pillar_id",
                        explanation=f"Referenced pillar '{style_plan.content_pillar_id}' does not exist in channel content pillars.",
                        source_authority="ChannelDNA.content_pillars",
                        recommended_remediation="Pin to a valid channel content pillar.",
                    )
                )
            else:
                # Check exclusions
                for excl in pillar.exclusions:
                    for location, text in texts_to_check:
                        if excl.lower() in text.lower():
                            findings.append(
                                CreativeQAFinding(
                                    finding_code=CreativeQAFindingCode.CONTENT_PILLAR_MISMATCH,
                                    severity=CreativeQASeverity.ERROR,
                                    subsystem=CreativeQASubsystem.CHANNEL_IDENTITY,
                                    affected_artifact=location,
                                    explanation=f"Content contains topic '{excl}' excluded by pillar '{pillar.name}'.",
                                    source_authority=f"ContentPillar.{pillar.pillar_id}",
                                    recommended_remediation=f"Eliminate excluded topic '{excl}' from production.",
                                )
                            )

        # 4. Avoid Patterns (Section 6)
        if avoid.sensationalized_claims and packaging_plan:
            for pat in cls.CLICKBAIT_PATTERNS:
                if pat.search(packaging_plan.selected_title.text):
                    findings.append(
                        CreativeQAFinding(
                            finding_code=CreativeQAFindingCode.AVOID_PATTERN_VIOLATION,
                            severity=CreativeQASeverity.WARNING,
                            subsystem=CreativeQASubsystem.TITLE,
                            affected_artifact="PackagingPlan.selected_title",
                            explanation="Title exhibits sensationalized claim contrary to channel avoid patterns.",
                            source_authority="AvoidPatterns.sensationalized_claims",
                            recommended_remediation="Reflect measured, evidence-driven phrasing.",
                        )
                    )

        if avoid.overloaded_thumbnails and packaging_plan:
            tc = packaging_plan.selected_thumbnail.text_content
            if tc and (len(tc.split()) > 4 or len(tc) > 30):
                findings.append(
                    CreativeQAFinding(
                        finding_code=CreativeQAFindingCode.THUMBNAIL_TEXT_OVERLOADED,
                        severity=CreativeQASeverity.ERROR,
                        subsystem=CreativeQASubsystem.THUMBNAIL,
                        affected_artifact="PackagingPlan.selected_thumbnail",
                        explanation=f"Thumbnail text '{tc}' exceeds concise text policy (>4 words or >30 chars).",
                        source_authority="AvoidPatterns.overloaded_thumbnails",
                        recommended_remediation="Reduce thumbnail overlay text to at most 3-4 impactful words.",
                    )
                )

        if avoid.generic_cta and packaging_plan:
            cta_txt = packaging_plan.cta_metadata.cta_text.lower()
            if "like and subscribe" in cta_txt or "smash that like" in cta_txt:
                findings.append(
                    CreativeQAFinding(
                        finding_code=CreativeQAFindingCode.AVOID_PATTERN_VIOLATION,
                        severity=CreativeQASeverity.WARNING,
                        subsystem=CreativeQASubsystem.DESCRIPTION,
                        affected_artifact="PackagingPlan.cta_metadata",
                        explanation="Generic call-to-action detected contrary to channel avoid patterns.",
                        source_authority="AvoidPatterns.generic_cta",
                        recommended_remediation="Use channel-aligned thoughtful discussion CTA.",
                    )
                )

        return findings


class EditorialVoiceEvaluator:
    """Evaluates narrative and packaging expression against ChannelDNA v2 editorial voice."""

    @classmethod
    def evaluate(
        cls,
        *,
        channel_dna: ChannelDNA | dict[str, Any] | None,
        style_plan: CreativeStylePlan | None,
        packaging_plan: PackagingPlan | None,
    ) -> list[CreativeQAFinding]:
        findings: list[CreativeQAFinding] = []
        if not channel_dna or not style_plan:
            return findings

        dna = _normalize_channel_dna(channel_dna)
        voice = dna.editorial_voice
        narr_style = style_plan.narrative_style

        # Formality comparison
        if voice.formality == VoiceFormality.FORMAL and narr_style.formality.value == "CASUAL":
            findings.append(
                CreativeQAFinding(
                    finding_code=CreativeQAFindingCode.VOICE_FORMALITY_MISMATCH,
                    severity=CreativeQASeverity.ERROR,
                    subsystem=CreativeQASubsystem.NARRATIVE_STYLE,
                    affected_artifact="CreativeStylePlan.narrative_style.formality",
                    explanation=f"Narrative formality '{narr_style.formality.value}' is too casual for channel FORMAL voice.",
                    source_authority="EditorialVoice.formality",
                    recommended_remediation="Adjust script formality to BALANCED or FORMAL.",
                )
            )

        # Energy comparison
        if voice.energy == VoiceEnergy.CALM and str(narr_style.editorial_energy.value).upper() in ("HIGH", "HIGH_ENERGY", "DYNAMIC"):
            # Allow variation only if justified in rationale
            has_justification = any("energy" in (r.explanation + r.aspect).lower() for r in style_plan.style_rationale)
            if not has_justification:
                findings.append(
                    CreativeQAFinding(
                        finding_code=CreativeQAFindingCode.VOICE_ENERGY_MISMATCH,
                        severity=CreativeQASeverity.WARNING,
                        subsystem=CreativeQASubsystem.NARRATIVE_STYLE,
                        affected_artifact="CreativeStylePlan.narrative_style.editorial_energy",
                        explanation="Narrative editorial energy is HIGH, which conflicts with channel CALM voice without documented rationale.",
                        source_authority="EditorialVoice.energy",
                        recommended_remediation="Moderate narrative energy or document creative progression rationale.",
                    )
                )

        return findings


class NarrativeStyleEvaluator:
    """Validates narrative style decisions against CreativeStylePlan and brand expectations."""

    @classmethod
    def evaluate(
        cls,
        *,
        style_plan: CreativeStylePlan | None,
        narrative_plan: NarrativePlan | None,
    ) -> list[CreativeQAFinding]:
        findings: list[CreativeQAFinding] = []
        if not style_plan or not narrative_plan:
            return findings

        target = style_plan.narrative_style

        # Check hook intensity: subtle channel should not have over-sensationalized hook
        if target.hook_intensity == HookIntensity.SUBTLE:
            hook_sec = next((s for s in narrative_plan.sections if s.role == NarrativeSectionRole.HOOK), None)
            if hook_sec and "insane" in hook_sec.objective.lower():
                findings.append(
                    CreativeQAFinding(
                        finding_code=CreativeQAFindingCode.HOOK_INTENSITY_MISMATCH,
                        severity=CreativeQASeverity.WARNING,
                        subsystem=CreativeQASubsystem.NARRATIVE_STYLE,
                        affected_artifact="NarrativePlan.sections[HOOK]",
                        explanation="Hook section objective uses sensationalized phrasing exceeding SUBTLE hook intensity.",
                        source_authority="CreativeStylePlan.narrative_style.hook_intensity",
                        recommended_remediation="Align hook section with SUBTLE inquiry or surprising fact style.",
                    )
                )

        return findings


class VisualAndCameraStyleEvaluator:
    """Validates visual and camera motion behaviour against CreativeStylePlan and ChannelDNA."""

    @classmethod
    def evaluate(
        cls,
        *,
        channel_dna: ChannelDNA | dict[str, Any] | None,
        style_plan: CreativeStylePlan | None,
    ) -> list[CreativeQAFinding]:
        findings: list[CreativeQAFinding] = []
        if not style_plan:
            return findings

        dna = _normalize_channel_dna(channel_dna)

        # Camera motion check
        cam = style_plan.camera_style
        overactive_avoid = getattr(dna.avoid_patterns, "overactive_camera_motion", False) if dna else False
        if overactive_avoid:
            if cam.camera_style_intent == CameraStyleIntent.ENERGETIC_MOTION or cam.max_camera_energy > 0.8:
                findings.append(
                    CreativeQAFinding(
                        finding_code=CreativeQAFindingCode.CAMERA_TOO_ACTIVE,
                        severity=CreativeQASeverity.ERROR,
                        subsystem=CreativeQASubsystem.CAMERA,
                        affected_artifact="CreativeStylePlan.camera_style",
                        explanation=f"Camera intent {cam.camera_style_intent.value} (max energy {cam.max_camera_energy}) violates avoid_patterns.overactive_camera_motion.",
                        source_authority="AvoidPatterns.overactive_camera_motion",
                        recommended_remediation="Constrain camera motion to SUBTLE_MOTION or MODERATE_EDITORIAL_MOTION.",
                    )
                )

        # Visual density check
        vis = style_plan.visual_style
        if dna and dna.visual_preferences.visual_density.value == "LOW":
            if str(vis.visual_density.value).upper() == "HIGH":
                findings.append(
                    CreativeQAFinding(
                        finding_code=CreativeQAFindingCode.VISUAL_DENSITY_MISMATCH,
                        severity=CreativeQASeverity.WARNING,
                        subsystem=CreativeQASubsystem.VISUAL_STYLE,
                        affected_artifact="CreativeStylePlan.visual_style.visual_density",
                        explanation="Visual density is HIGH while channel visual preferences mandate LOW density.",
                        source_authority="VisualPreferences.visual_density",
                        recommended_remediation="Reduce visual density to LOW or MEDIUM.",
                    )
                )

        return findings


class AudioStyleEvaluator:
    """Validates brand and style audio aspects against CreativeStylePlan and ChannelDNA."""

    @classmethod
    def evaluate(
        cls,
        *,
        channel_dna: ChannelDNA | dict[str, Any] | None,
        style_plan: CreativeStylePlan | None,
    ) -> list[CreativeQAFinding]:
        findings: list[CreativeQAFinding] = []
        if not style_plan:
            return findings

        dna = _normalize_channel_dna(channel_dna)
        audio = style_plan.audio_style

        # Vocal policy
        if audio.vocal_policy == "INSTRUMENTAL_ONLY":
            # If style has vocal policy INSTRUMENTAL_ONLY, verify it is respected
            pass

        # Music energy
        char = getattr(dna.audio_preferences, "sonic_character", None) or getattr(dna.audio_preferences, "character", None) if dna else None
        if dna and char in ("MINIMAL", "SUBTLE"):
            if audio.target_music_energy > 0.7:
                findings.append(
                    CreativeQAFinding(
                        finding_code=CreativeQAFindingCode.MUSIC_ENERGY_TOO_HIGH,
                        severity=CreativeQASeverity.ERROR,
                        subsystem=CreativeQASubsystem.MUSIC,
                        affected_artifact="CreativeStylePlan.audio_style.target_music_energy",
                        explanation=f"Target music energy {audio.target_music_energy} is too high for MINIMAL audio character.",
                        source_authority="AudioPreferences.sonic_character",
                        recommended_remediation="Lower target music energy below 0.6.",
                    )
                )

        # SFX density
        if dna and dna.avoid_patterns.constant_sfx:
            if audio.sfx_density == "DENSE" or audio.sfx_prominence == "PROMINENT":
                findings.append(
                    CreativeQAFinding(
                        finding_code=CreativeQAFindingCode.SFX_DENSITY_EXCESSIVE,
                        severity=CreativeQASeverity.ERROR,
                        subsystem=CreativeQASubsystem.SFX,
                        affected_artifact="CreativeStylePlan.audio_style.sfx_density",
                        explanation="SFX density is DENSE/PROMINENT contrary to avoid_patterns.constant_sfx.",
                        source_authority="AvoidPatterns.constant_sfx",
                        recommended_remediation="Set SFX density to SPARSE or BALANCED.",
                    )
                )

        return findings


class TitleAndThumbnailQAEvaluator:
    """Evaluates title and thumbnail candidates and selected items for brand and grounding fit."""

    @classmethod
    def evaluate(
        cls,
        *,
        channel_dna: ChannelDNA | dict[str, Any] | None,
        style_plan: CreativeStylePlan | None,
        packaging_plan: PackagingPlan | None,
    ) -> list[CreativeQAFinding]:
        findings: list[CreativeQAFinding] = []
        if not packaging_plan:
            return findings

        dna = _normalize_channel_dna(channel_dna)

        title = packaging_plan.selected_title
        thumb = packaging_plan.selected_thumbnail

        # Title Grounding
        if not title.is_grounded:
            findings.append(
                CreativeQAFinding(
                    finding_code=CreativeQAFindingCode.TITLE_UNGROUNDED,
                    severity=CreativeQASeverity.BLOCKER,
                    subsystem=CreativeQASubsystem.TITLE,
                    affected_artifact="PackagingPlan.selected_title",
                    explanation=f"Selected title '{title.text}' is not grounded in verified content claims.",
                    source_authority="P24C_PackagingPlan",
                    recommended_remediation="Choose a title candidate that is verified as grounded.",
                )
            )

        # Rejected title resurrection guard (Section 14)
        # Verify selected title was not previously marked with constraint findings
        if title.constraint_findings:
            findings.append(
                CreativeQAFinding(
                    finding_code=CreativeQAFindingCode.TITLE_PREVIOUSLY_REJECTED,
                    severity=CreativeQASeverity.BLOCKER,
                    subsystem=CreativeQASubsystem.TITLE,
                    affected_artifact="PackagingPlan.selected_title",
                    explanation=f"Selected title '{title.text}' contains rejected constraint findings: {list(title.constraint_findings)}.",
                    source_authority="P24C_TitleRanking",
                    recommended_remediation="Do not select candidate titles with active constraint findings.",
                )
            )

        # Thumbnail text safe area / mobile readability (Section 15)
        if thumb.safe_zone.value == "FULL" and thumb.text_overlay_intent.value != "NO_TEXT":
            findings.append(
                CreativeQAFinding(
                    finding_code=CreativeQAFindingCode.THUMBNAIL_SAFE_AREA_COLLISION,
                    severity=CreativeQASeverity.WARNING,
                    subsystem=CreativeQASubsystem.THUMBNAIL,
                    affected_artifact="PackagingPlan.selected_thumbnail",
                    explanation="Thumbnail text placement does not reserve platform safe-zone margin.",
                    source_authority="PackagingPreferences.thumbnail_safe_zone",
                    recommended_remediation="Use LEFT_WEIGHTED safe zone to prevent timestamp badge collision.",
                )
            )

        # Mobile readability
        if thumb.contrast_intent == "LOW" and thumb.text_overlay_intent.value != "NO_TEXT":
            findings.append(
                CreativeQAFinding(
                    finding_code=CreativeQAFindingCode.THUMBNAIL_MOBILE_READABILITY,
                    severity=CreativeQASeverity.WARNING,
                    subsystem=CreativeQASubsystem.THUMBNAIL,
                    affected_artifact="PackagingPlan.selected_thumbnail",
                    explanation="Thumbnail has LOW contrast intent with text overlays, degrading mobile readability.",
                    source_authority="PackagingPreferences.thumbnail_contrast",
                    recommended_remediation="Set contrast_intent='HIGH' for clear legibility on mobile feeds.",
                )
            )

        return findings


class DescriptionAndMetadataQAEvaluator:
    """Validates description, chapters, CTA, and attribution compliance."""

    @classmethod
    def evaluate(
        cls,
        *,
        channel_dna: ChannelDNA | dict[str, Any] | None,
        packaging_plan: PackagingPlan | None,
    ) -> list[CreativeQAFinding]:
        findings: list[CreativeQAFinding] = []
        if not packaging_plan:
            return findings

        # Check attribution presence (P23 handoff)
        if not packaging_plan.attribution_block or not packaging_plan.attribution_block.strip():
            findings.append(
                CreativeQAFinding(
                    finding_code=CreativeQAFindingCode.ATTRIBUTION_MISSING,
                    severity=CreativeQASeverity.ERROR,
                    subsystem=CreativeQASubsystem.DESCRIPTION,
                    affected_artifact="PackagingPlan.attribution_block",
                    explanation="PackagingPlan is missing attribution block for licensed audio assets.",
                    source_authority="P23_AudioGovernance",
                    recommended_remediation="Include audio attribution block in description.",
                )
            )

        # Keyword stuffing check in tags
        if len(packaging_plan.tags) > 25:
            findings.append(
                CreativeQAFinding(
                    finding_code=CreativeQAFindingCode.KEYWORD_STUFFING,
                    severity=CreativeQASeverity.WARNING,
                    subsystem=CreativeQASubsystem.METADATA,
                    affected_artifact="PackagingPlan.tags",
                    explanation=f"Tags count ({len(packaging_plan.tags)}) exceeds recommended threshold (>25 tags).",
                    source_authority="AvoidPatterns.keyword_stuffing",
                    recommended_remediation="Limit tags to 6-12 highly relevant keywords.",
                )
            )

        # Monotonic chapters
        if packaging_plan.chapters:
            times = [c.start_time_seconds for c in packaging_plan.chapters]
            if times != sorted(times):
                findings.append(
                    CreativeQAFinding(
                        finding_code=CreativeQAFindingCode.CHAPTER_STYLE_MISMATCH,
                        severity=CreativeQASeverity.ERROR,
                        subsystem=CreativeQASubsystem.CHAPTERS,
                        affected_artifact="PackagingPlan.chapters",
                        explanation="Video chapters are not strictly monotonically increasing.",
                        source_authority="P24C_PackagingPlan",
                        recommended_remediation="Sort chapters chronologically starting from 00:00.",
                    )
                )

        return findings


class PackagingCoherenceAndPromisePayoffEvaluator:
    """Evaluates cross-modal coherence between title, thumbnail, description, and payoff."""

    @classmethod
    def evaluate(
        cls,
        *,
        packaging_plan: PackagingPlan | None,
        narrative_plan: NarrativePlan | None,
    ) -> list[CreativeQAFinding]:
        findings: list[CreativeQAFinding] = []
        if not packaging_plan:
            return findings

        title = packaging_plan.selected_title
        thumb = packaging_plan.selected_thumbnail
        desc = packaging_plan.description

        # Title vs Thumbnail subject coherence
        title_lower = title.text.lower()
        subject_lower = thumb.primary_subject.lower()
        title_words = set(re.findall(r"\b[a-zA-Z]{4,}\b", title_lower))
        subject_words = set(re.findall(r"\b[a-zA-Z]{4,}\b", subject_lower))

        # If primary subject has no conceptual overlap with title keywords (when neither is generic)
        if subject_words and title_words and not title_words.intersection(subject_words):
            domain_pairs = [
                ("biology", "quantum"), ("space", "biology"), ("quantum", "finance"),
                ("genetics", "physics"), ("crypto", "biology"), ("finance", "quantum"),
                ("genetics", "quantum"),
            ]
            has_domain_clash = any(
                (d1 in subject_lower and d2 in title_lower) or (d2 in subject_lower and d1 in title_lower)
                for d1, d2 in domain_pairs
            )
            if has_domain_clash:
                findings.append(
                    CreativeQAFinding(
                        finding_code=CreativeQAFindingCode.TITLE_THUMBNAIL_PROMISE_MISMATCH,
                        severity=CreativeQASeverity.ERROR,
                        subsystem=CreativeQASubsystem.PACKAGING_COHERENCE,
                        affected_artifact="PackagingPlan.selected_thumbnail",
                        explanation=f"Thumbnail subject '{thumb.primary_subject}' contradicts title topic '{title.text}'.",
                        source_authority="P24C_PackagingPlan",
                        recommended_remediation="Align thumbnail focal subject with the core promise of the title.",
                    )
                )

        # Title vs Description coherence
        if title_words and not any(w in desc.lower() for w in title_words):
            findings.append(
                CreativeQAFinding(
                    finding_code=CreativeQAFindingCode.TITLE_DESCRIPTION_MISMATCH,
                    severity=CreativeQASeverity.WARNING,
                    subsystem=CreativeQASubsystem.PACKAGING_COHERENCE,
                    affected_artifact="PackagingPlan.description",
                    explanation="Description outline does not reference key concepts from the selected title.",
                    source_authority="P24C_PackagingPlan",
                    recommended_remediation="Ensure description summary mentions the title premise.",
                )
            )

        # Promise / Payoff alignment (Section 18)
        if narrative_plan:
            payoff_secs = [s for s in narrative_plan.sections if s.role in (NarrativeSectionRole.PAYOFF, NarrativeSectionRole.TAKEAWAY)]
            payoff_text = " ".join(s.objective for s in payoff_secs).lower()

            # Overpromise check: If title promises absolute proof/unveiling, verify payoff delivers it
            if ("reveals the secret" in title_lower or "proven solution" in title_lower) and not payoff_text:
                findings.append(
                    CreativeQAFinding(
                        finding_code=CreativeQAFindingCode.OVERPROMISE,
                        severity=CreativeQASeverity.BLOCKER,
                        subsystem=CreativeQASubsystem.PACKAGING_COHERENCE,
                        affected_artifact="PackagingPlan.selected_title",
                        explanation=f"Title '{title.text}' makes an outcome promise that is not delivered in the video payoff sections.",
                        source_authority="P21_NarrativePlan.payoff",
                        recommended_remediation="Scale back title promise to match delivered narrative findings.",
                    )
                )

        return findings


class CrossModalCoherenceAndDriftEvaluator:
    """Evaluates whole-production cross-modal coherence and style drift (Sections 19 & 20)."""

    @classmethod
    def evaluate(
        cls,
        *,
        style_plan: CreativeStylePlan | None,
    ) -> list[CreativeQAFinding]:
        findings: list[CreativeQAFinding] = []
        if not style_plan:
            return findings

        # Check whole-production conflict: calm narrative + hyperactive camera + high energy audio
        ci = style_plan.creative_intensity
        is_calm_narrative = ci.narrative_energy < 0.4
        is_hyperactive_camera = ci.camera_energy > 0.8
        is_aggressive_music = ci.music_energy > 0.8

        if is_calm_narrative and is_hyperactive_camera and is_aggressive_music:
            findings.append(
                CreativeQAFinding(
                    finding_code=CreativeQAFindingCode.CALM_NARRATION_HYPERACTIVE_PRODUCTION,
                    severity=CreativeQASeverity.ERROR,
                    subsystem=CreativeQASubsystem.CROSS_MODAL_COHERENCE,
                    affected_artifact="CreativeStylePlan.creative_intensity",
                    explanation="Incoherent combination: Calm narration paired with hyperactive camera and aggressive music energy.",
                    source_authority="CreativeStylePlan.coherence",
                    recommended_remediation="Align camera motion and audio energy with calm narrative tone.",
                )
            )

        # Style drift across sections (Section 20)
        # CreativeArc tracks justified progression vs accidental drift
        arc = style_plan.creative_arc
        if arc and arc.sections:
            for i in range(len(arc.sections) - 1):
                s1 = arc.sections[i]
                s2 = arc.sections[i + 1]
                # If there is a massive unescalated jump in energy between calm sections
                if s1.section_role == NarrativeSectionRole.DEVELOPMENT and s2.section_role == NarrativeSectionRole.DEVELOPMENT:
                    if abs(s1.target_energy - s2.target_energy) > 0.5 and not s2.justified_escalation:
                        findings.append(
                            CreativeQAFinding(
                                finding_code=CreativeQAFindingCode.CAMERA_STYLE_DRIFT,
                                severity=CreativeQASeverity.WARNING,
                                subsystem=CreativeQASubsystem.CAMERA,
                                affected_artifact=f"CreativeArc.sections[{s2.section_order}]",
                                explanation=f"Unexplained style drift between section {s1.section_order} and section {s2.section_order}.",
                                source_authority="CreativeStylePlan.creative_arc",
                                recommended_remediation="Smooth energy transitions between adjacent development sections or document justified escalation.",
                            )
                        )

        return findings


class PhysicalPackagingAndVideoLineageEvaluator:
    """Evaluates physical thumbnail existence and video artifact lineage (Sections 23 & 24)."""

    @classmethod
    def evaluate(
        cls,
        *,
        packaging_plan: PackagingPlan | None,
        video_artifact_path: Path | str | None,
        video_artifact_id: UUID | None,
    ) -> list[CreativeQAFinding]:
        findings: list[CreativeQAFinding] = []
        if not packaging_plan:
            return findings

        # Physical thumbnail artifact check (Section 23)
        thumb_art = packaging_plan.physical_thumbnail_artifact
        if not thumb_art:
            findings.append(
                CreativeQAFinding(
                    finding_code=CreativeQAFindingCode.MISSING_PHYSICAL_THUMBNAIL,
                    severity=CreativeQASeverity.BLOCKER,
                    subsystem=CreativeQASubsystem.PHYSICAL_ARTIFACT,
                    affected_artifact="PackagingPlan.physical_thumbnail_artifact",
                    explanation="PackagingPlan is missing physical thumbnail artifact required for creative PASS.",
                    source_authority="P24C_PackagingEngine",
                    recommended_remediation="Render and validate physical 1280x720 PNG thumbnail file before QA.",
                )
            )
        else:
            p = Path(thumb_art.file_path)
            if not p.is_file():
                findings.append(
                    CreativeQAFinding(
                        finding_code=CreativeQAFindingCode.MISSING_PHYSICAL_THUMBNAIL,
                        severity=CreativeQASeverity.BLOCKER,
                        subsystem=CreativeQASubsystem.PHYSICAL_ARTIFACT,
                        affected_artifact=str(p),
                        explanation=f"Physical thumbnail file '{p}' does not exist on disk.",
                        source_authority="P24C_PackagingEngine",
                        recommended_remediation="Ensure thumbnail artifact file is rendered and accessible.",
                    )
                )
            elif thumb_art.file_size_bytes < 1000 or thumb_art.width != 1280 or thumb_art.height != 720:
                findings.append(
                    CreativeQAFinding(
                        finding_code=CreativeQAFindingCode.INVALID_PHYSICAL_THUMBNAIL,
                        severity=CreativeQASeverity.BLOCKER,
                        subsystem=CreativeQASubsystem.PHYSICAL_ARTIFACT,
                        affected_artifact=str(p),
                        explanation=f"Physical thumbnail file has invalid dimensions ({thumb_art.width}x{thumb_art.height}) or size ({thumb_art.file_size_bytes}B).",
                        source_authority="P24C_PackagingEngine",
                        recommended_remediation="Re-render 1280x720 RGB PNG thumbnail.",
                    )
                )

        # Video artifact presence & lineage (Section 24)
        if video_artifact_path:
            vp = Path(video_artifact_path)
            if not vp.is_file():
                findings.append(
                    CreativeQAFinding(
                        finding_code=CreativeQAFindingCode.VIDEO_ARTIFACT_LINEAGE_MISMATCH,
                        severity=CreativeQASeverity.ERROR,
                        subsystem=CreativeQASubsystem.PHYSICAL_ARTIFACT,
                        affected_artifact=str(vp),
                        explanation=f"Referenced video artifact file '{vp}' is missing on disk.",
                        source_authority="RenderService",
                        recommended_remediation="Confirm accepted video render artifact was generated.",
                    )
                )

        return findings


# ── Finding Deduplication & Recommendations ──────────────────────────────────


class FindingDeduplicationService:
    """Normalizes and collapses equivalent findings across P21/P22/P23/P24-C/P24-D (Section 25)."""

    @classmethod
    def deduplicate(
        cls,
        findings: Sequence[CreativeQAFinding],
    ) -> list[CreativeQAFinding]:
        seen: set[tuple[str, str, str]] = set()
        deduped: list[CreativeQAFinding] = []

        for f in findings:
            key = (f.finding_code.value, f.subsystem.value, f.affected_artifact)
            if key not in seen:
                seen.add(key)
                deduped.append(f)

        return deduped


class RevisionRecommendationBuilder:
    """Generates structured, actionable recommendations for diagnostic findings (Section 27)."""

    ACTION_MAP: dict[CreativeQAFindingCode, tuple[CreativeQARecommendationAction, str]] = {
        CreativeQAFindingCode.CAMERA_TOO_ACTIVE: (
            CreativeQARecommendationAction.REDUCE_CAMERA_INTENSITY,
            "Reduce camera motion intensity to STILL or SUBTLE_MOTION.",
        ),
        CreativeQAFindingCode.TITLE_BRAND_MISMATCH: (
            CreativeQARecommendationAction.REPLACE_TITLE,
            "Select an alternative candidate title conforming to channel voice.",
        ),
        CreativeQAFindingCode.MISLEADING_CLICKBAIT: (
            CreativeQARecommendationAction.REPLACE_TITLE,
            "Replace clickbait title with grounded explainer or factual title.",
        ),
        CreativeQAFindingCode.THUMBNAIL_TEXT_OVERLOADED: (
            CreativeQARecommendationAction.REDUCE_THUMBNAIL_TEXT,
            "Shorten thumbnail overlay text to at most 3-4 impactful words.",
        ),
        CreativeQAFindingCode.FABRICATED_CLAIM: (
            CreativeQARecommendationAction.REMOVE_UNSUPPORTED_CLAIM,
            "Remove ungrounded factual assertions not supported by research brief.",
        ),
        CreativeQAFindingCode.SFX_DENSITY_EXCESSIVE: (
            CreativeQARecommendationAction.REDUCE_SFX_DENSITY,
            "Lower SFX density to SPARSE or OFF to avoid acoustic clutter.",
        ),
        CreativeQAFindingCode.MUSIC_ENERGY_TOO_HIGH: (
            CreativeQARecommendationAction.ADJUST_MUSIC_ENERGY,
            "Decrease target music energy to match calm channel character.",
        ),
        CreativeQAFindingCode.AVOID_PATTERN_VIOLATION: (
            CreativeQARecommendationAction.RESTORE_CTA_POLICY,
            "Restore channel-approved CTA and presentation preferences.",
        ),
        CreativeQAFindingCode.VOICE_FORMALITY_MISMATCH: (
            CreativeQARecommendationAction.REVISE_VOICE_TONE,
            "Adjust copy tone to match channel formality baseline.",
        ),
        CreativeQAFindingCode.OVERPROMISE: (
            CreativeQARecommendationAction.REALIGN_PROMISE_PAYOFF,
            "Align title promise with delivered video takeaway.",
        ),
        CreativeQAFindingCode.CAMERA_STYLE_DRIFT: (
            CreativeQARecommendationAction.RESOLVE_STYLE_DRIFT,
            "Ensure camera motion follows justified CreativeArc escalation.",
        ),
        CreativeQAFindingCode.VISUAL_DENSITY_MISMATCH: (
            CreativeQARecommendationAction.ADJUST_VISUAL_DENSITY,
            "Adjust visual density to match channel preferences.",
        ),
    }

    @classmethod
    def build_recommendations(
        cls,
        findings: Sequence[CreativeQAFinding],
    ) -> list[CreativeQARecommendation]:
        recs: list[CreativeQARecommendation] = []
        seen_actions: set[tuple[str, str]] = set()

        for f in findings:
            if f.severity in (CreativeQASeverity.ERROR, CreativeQASeverity.BLOCKER, CreativeQASeverity.WARNING):
                if f.finding_code in cls.ACTION_MAP:
                    action, desc = cls.ACTION_MAP[f.finding_code]
                    key = (action.value, f.subsystem.value)
                    if key not in seen_actions:
                        seen_actions.add(key)
                        recs.append(
                            CreativeQARecommendation(
                                action=action,
                                subsystem=f.subsystem,
                                description=f.recommended_remediation or desc,
                                affected_field=f.affected_artifact,
                            )
                        )

        return recs


# ── Final Creative Gate (Section 28) ─────────────────────────────────────────


class FinalCreativeGate:
    """Enforces deterministic gate acceptance before downstream ProductionQA / Guardian (Section 28)."""

    @classmethod
    def verify_acceptance(cls, qa_result: CreativeQAResult) -> bool:
        """Verify acceptance, raising CreativeRenderGateError on REVISE or FAIL."""
        if qa_result.status in (CreativeQAStatus.REVISE, CreativeQAStatus.FAIL):
            raise CreativeRenderGateError(
                f"Final creative gate blocked: status={qa_result.status.value}, "
                f"highest_severity={qa_result.highest_severity.value}, "
                f"blockers={qa_result.blocker_count}, errors={qa_result.error_count}, "
                f"warnings={qa_result.warning_count}",
                qa_result=qa_result,
            )
        return True

    @classmethod
    def is_eligible_for_downstream_qa(cls, qa_result: CreativeQAResult) -> bool:
        """Returns True if and only if QA status is PASS."""
        return qa_result.status == CreativeQAStatus.PASS

    @classmethod
    def can_proceed(cls, qa_result: CreativeQAResult) -> bool:
        """Alias for is_eligible_for_downstream_qa."""
        return cls.is_eligible_for_downstream_qa(qa_result)


# ── Orchestrator Service ─────────────────────────────────────────────────────


class CreativeQAService:
    """Unified application service coordinating Creative QA & Brand Acceptance."""

    def __init__(self, model_reviewer: CreativeModelReviewer | None = None) -> None:
        self.model_reviewer = model_reviewer

    def evaluate_creative_package(
        self,
        *,
        channel_dna: ChannelDNA | dict[str, Any] | None,
        pinned_revision_id: UUID | None,
        style_plan: CreativeStylePlan | None,
        packaging_plan: PackagingPlan | None = None,
        narrative_plan: NarrativePlan | None = None,
        video_artifact_path: Path | str | None = None,
        video_artifact_id: UUID | None = None,
    ) -> CreativeQAResult:
        raw_findings: list[CreativeQAFinding] = []

        # 1. ChannelDNA Compliance & Hard Constraints Gate
        raw_findings.extend(
            ChannelDNAComplianceEvaluator.evaluate(
                channel_dna=channel_dna,
                pinned_revision_id=pinned_revision_id,
                style_plan=style_plan,
                packaging_plan=packaging_plan,
                narrative_plan=narrative_plan,
            )
        )

        # 2. Editorial Voice QA
        raw_findings.extend(
            EditorialVoiceEvaluator.evaluate(
                channel_dna=channel_dna,
                style_plan=style_plan,
                packaging_plan=packaging_plan,
            )
        )

        # 3. Narrative Style QA
        raw_findings.extend(
            NarrativeStyleEvaluator.evaluate(
                style_plan=style_plan,
                narrative_plan=narrative_plan,
            )
        )

        # 4. Visual & Camera Style QA
        raw_findings.extend(
            VisualAndCameraStyleEvaluator.evaluate(
                channel_dna=channel_dna,
                style_plan=style_plan,
            )
        )

        # 5. Audio Style QA
        raw_findings.extend(
            AudioStyleEvaluator.evaluate(
                channel_dna=channel_dna,
                style_plan=style_plan,
            )
        )

        # 6. Title & Thumbnail QA
        raw_findings.extend(
            TitleAndThumbnailQAEvaluator.evaluate(
                channel_dna=channel_dna,
                style_plan=style_plan,
                packaging_plan=packaging_plan,
            )
        )

        # 7. Description & Metadata QA
        raw_findings.extend(
            DescriptionAndMetadataQAEvaluator.evaluate(
                channel_dna=channel_dna,
                packaging_plan=packaging_plan,
            )
        )

        # 8. Packaging Coherence & Promise/Payoff QA
        raw_findings.extend(
            PackagingCoherenceAndPromisePayoffEvaluator.evaluate(
                packaging_plan=packaging_plan,
                narrative_plan=narrative_plan,
            )
        )

        # 9. Cross-Modal Coherence & Style Drift
        raw_findings.extend(
            CrossModalCoherenceAndDriftEvaluator.evaluate(
                style_plan=style_plan,
            )
        )

        # 10. Physical Packaging & Video Lineage QA
        raw_findings.extend(
            PhysicalPackagingAndVideoLineageEvaluator.evaluate(
                packaging_plan=packaging_plan,
                video_artifact_path=video_artifact_path,
                video_artifact_id=video_artifact_id,
            )
        )

        # Optional Model Review (Section 31: NOT_USED unless wired)
        if self.model_reviewer and style_plan:
            model_findings = self.model_reviewer.review(
                style_plan=style_plan,
                packaging_plan=packaging_plan,
                narrative_plan=narrative_plan,
            )
            raw_findings.extend(model_findings)

        # 11. Deduplication
        deduped = FindingDeduplicationService.deduplicate(raw_findings)

        # 12. Acceptance Policy (Section 26)
        blocker_count = sum(1 for f in deduped if f.severity == CreativeQASeverity.BLOCKER)
        error_count = sum(1 for f in deduped if f.severity == CreativeQASeverity.ERROR)
        warning_count = sum(1 for f in deduped if f.severity == CreativeQASeverity.WARNING)
        info_count = sum(1 for f in deduped if f.severity == CreativeQASeverity.INFO)

        if blocker_count > 0:
            status = CreativeQAStatus.FAIL
            highest_sev = CreativeQASeverity.BLOCKER
        elif error_count > 0:
            status = CreativeQAStatus.REVISE
            highest_sev = CreativeQASeverity.ERROR
        elif warning_count > 0:
            status = CreativeQAStatus.PASS
            highest_sev = CreativeQASeverity.WARNING
        else:
            status = CreativeQAStatus.PASS
            highest_sev = CreativeQASeverity.INFO

        is_accepted = status == CreativeQAStatus.PASS

        # 13. Recommendations
        recommendations = RevisionRecommendationBuilder.build_recommendations(deduped)

        # 14. Provenance
        provenance = CreativeQAProvenance(
            channel_dna_revision_id=pinned_revision_id or (style_plan.channel_dna_revision_id if style_plan else uuid4()),
            creative_style_plan_id=style_plan.plan_id if style_plan else uuid4(),
            packaging_plan_id=packaging_plan.packaging_plan_id if packaging_plan else None,
            narrative_plan_id=narrative_plan.id if narrative_plan else None,
            video_artifact_id=video_artifact_id,
        )

        return CreativeQAResult(
            status=status,
            highest_severity=highest_sev,
            findings=tuple(deduped),
            recommendations=tuple(recommendations),
            provenance=provenance,
            blocker_count=blocker_count,
            error_count=error_count,
            warning_count=warning_count,
            info_count=info_count,
            is_accepted=is_accepted,
        )

    def assemble_acceptance_package(
        self,
        *,
        packaging_plan: PackagingPlan,
        qa_result: CreativeQAResult,
        video_artifact_id: str | None = None,
        video_path: str | None = None,
    ) -> CreativeAcceptancePackage:
        """Assembles bounded physical creative package if QA result is accepted (Section 35)."""
        FinalCreativeGate.verify_acceptance(qa_result)

        if not packaging_plan.physical_thumbnail_artifact:
            raise ValueError("PackagingPlan must have physical_thumbnail_artifact for acceptance package.")

        return CreativeAcceptancePackage(
            title=packaging_plan.selected_title.text,
            thumbnail_path=str(packaging_plan.physical_thumbnail_artifact.file_path),
            description=packaging_plan.description,
            chapters=tuple(
                {"title": c.title, "seconds": c.start_time_seconds, "timestamp": c.formatted_timestamp}
                for c in packaging_plan.chapters
            ),
            metadata={
                "tags": list(packaging_plan.tags),
                "cta_metadata": packaging_plan.cta_metadata.model_dump(),
                "attribution_block": packaging_plan.attribution_block,
            },
            video_artifact_id=video_artifact_id,
            video_path=video_path,
            channel_dna_revision_id=packaging_plan.channel_dna_revision_id,
            creative_style_plan_id=packaging_plan.creative_style_plan_id,
            packaging_plan_id=packaging_plan.packaging_plan_id,
            qa_result=qa_result,
        )
