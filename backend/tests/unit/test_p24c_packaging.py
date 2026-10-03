"""Unit tests for P24-C Packaging Engine covering Test Matrix A through Q."""

import tempfile
import uuid
from pathlib import Path
from typing import Any

import pytest

from omega.application.packaging_engine import (
    PackagingEngine,
    PackagingModelClient,
)
from omega.domain.channel_dna import (
    ChannelDNA,
    HardConstraints,
    PackagingPreferences,
    SoftPreferences,
)
from omega.domain.creative_style import (
    CreativeStylePlan,
    PackagingStyleHints,
)
from omega.domain.narrative_plan import (
    NarrativeFormatProfile,
    NarrativePlan,
    NarrativeSection,
    NarrativeSectionRole,
)
from omega.domain.packaging import (
    PackagingFindingCode,
    PackagingPlan,
    TextOverlayIntent,
    ThumbnailConcept,
    ThumbnailSafeZone,
    TitleCandidate,
    TitleStrategy,
    VideoChapter,
)
from omega.infrastructure.thumbnail_renderer import (
    ThumbnailPhysicalValidationError,
    ThumbnailRenderer,
)


@pytest.fixture
def test_channel_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def base_channel_dna(test_channel_id: uuid.UUID) -> Any:
    dna = ChannelDNA(
        hard_constraints=HardConstraints(
            no_fabricated_claims=True,
            no_misleading_clickbait=True,
            prohibited_vocabulary=("scam", "forbidden_word"),
        ),
        packaging_preferences=PackagingPreferences(
            title_tone="FACTUAL_COMPELLING",
            title_length_tendency="CONCISE",
            thumbnail_text_policy="MAX_3_WORDS",
            clickbait_tolerance="ZERO_TOLERANCE",
        ),
    )
    return dna.resolve_for_format("LONG", revision_id=test_channel_id)


@pytest.fixture
def base_creative_style_plan(test_channel_id: uuid.UUID) -> CreativeStylePlan:
    return CreativeStylePlan(
        channel_dna_revision_id=test_channel_id,
        packaging_hints=PackagingStyleHints(
            title_tone="FACTUAL_COMPELLING",
            title_restraint="FACTUAL_RESTRAINED",
            title_length_tendency="CONCISE",
            thumbnail_text_policy="MAX_3_WORDS",
            thumbnail_emotional_intensity="MEASURED",
        ),
    )


@pytest.fixture
def base_narrative_plan(test_channel_id: uuid.UUID) -> NarrativePlan:
    sec1 = NarrativeSection(
        section_order=1,
        role=NarrativeSectionRole.HOOK,
        objective="Introduce semiconductor quantum scaling challenge",
        key_information=["1,000 qubits achieved in silicon substrate"],
        target_duration_seconds=60,
    )
    sec2 = NarrativeSection(
        section_order=2,
        role=NarrativeSectionRole.CONTEXT,
        objective="Explain thermal noise limits and error rates",
        key_information=["Error rates below 0.1% threshold"],
        target_duration_seconds=120,
    )
    sec3 = NarrativeSection(
        section_order=3,
        role=NarrativeSectionRole.PAYOFF,
        objective="Demonstrate benchmark validation results",
        key_information=["10x coherence time improvement measured"],
        target_duration_seconds=120,
    )
    return NarrativePlan(
        channel_dna_revision_id=test_channel_id,
        content_generation_request_id=uuid.uuid4(),
        metadata={"premise": "Silicon Quantum Processor: Achieving 1,000 Qubits at Scale"},
        sections=[sec1, sec2, sec3],
        format_profile=NarrativeFormatProfile.MEDIUM,
        target_duration_seconds=300,
        estimated_duration_seconds=300,
    )


# ── Test Matrix A: Title Generation ──────────────────────────────────────────


def test_matrix_a_title_generation(
    base_channel_dna: Any,
    base_creative_style_plan: CreativeStylePlan,
    base_narrative_plan: NarrativePlan,
) -> None:
    """Verifies generation of multiple meaningfully different candidate titles."""
    engine = PackagingEngine()
    plan = engine.generate_packaging_plan(
        channel_dna=base_channel_dna,
        creative_style_plan=base_creative_style_plan,
        narrative_plan=base_narrative_plan,
    )

    strategies = {c.strategy for c in plan.title_candidates}
    assert TitleStrategy.FACTUAL in strategies
    assert TitleStrategy.EXPLAINER in strategies
    assert TitleStrategy.CURIOSITY in strategies
    assert TitleStrategy.QUESTION in strategies
    assert TitleStrategy.OUTCOME in strategies
    assert TitleStrategy.CONTRAST in strategies

    # Verify candidates are distinct and not trivial punctuation variants
    titles = [c.text for c in plan.title_candidates]
    assert len(titles) == len(set(titles))
    assert len(titles) >= 4


# ── Test Matrix B: Grounding ────────────────────────────────────────────────


def test_matrix_b_title_grounding(
    base_channel_dna: Any,
    base_creative_style_plan: CreativeStylePlan,
    base_narrative_plan: NarrativePlan,
) -> None:
    """Verifies that unsupported claims, numbers, and superlatives are detected/rejected."""
    engine = PackagingEngine()
    grounded_context = engine._extract_grounded_context(base_narrative_plan, None)

    # 1. Supported claim: matches numbers in narrative (1,000)
    cand_supported = TitleCandidate(
        text="Silicon Quantum Processor: 1,000 Qubits Demonstrated",
        strategy=TitleStrategy.FACTUAL,
    )
    eval_supported = engine._evaluate_title_candidate(
        cand_supported,
        grounded_context=grounded_context,
        hard_constraints=base_channel_dna.hard_constraints,
        style_hints=base_creative_style_plan.packaging_hints,
        prohibited_patterns=base_channel_dna.packaging_preferences.prohibited_packaging_patterns,
    )
    assert eval_supported.is_grounded is True
    assert len(eval_supported.constraint_findings) == 0

    # 2. Unsupported numeric mismatch (50,000 not in content)
    cand_unsupported_num = TitleCandidate(
        text="Silicon Quantum Processor: 50,000 Qubits Built Today",
        strategy=TitleStrategy.FACTUAL,
    )
    eval_num = engine._evaluate_title_candidate(
        cand_unsupported_num,
        grounded_context=grounded_context,
        hard_constraints=base_channel_dna.hard_constraints,
        style_hints=base_creative_style_plan.packaging_hints,
        prohibited_patterns=base_channel_dna.packaging_preferences.prohibited_packaging_patterns,
    )
    assert eval_num.is_grounded is False
    assert PackagingFindingCode.TITLE_NUMERIC_MISMATCH.value in eval_num.constraint_findings

    # 3. Exaggerated superlative ("revolutionizes everything")
    cand_superlative = TitleCandidate(
        text="New Quantum Chip Revolutionizes Everything in Science",
        strategy=TitleStrategy.FACTUAL,
    )
    eval_sup = engine._evaluate_title_candidate(
        cand_superlative,
        grounded_context=grounded_context,
        hard_constraints=base_channel_dna.hard_constraints,
        style_hints=base_creative_style_plan.packaging_hints,
        prohibited_patterns=base_channel_dna.packaging_preferences.prohibited_packaging_patterns,
    )
    assert eval_sup.is_grounded is False
    assert PackagingFindingCode.TITLE_EXAGGERATION.value in eval_sup.constraint_findings


# ── Test Matrix C: Clickbait Guard ──────────────────────────────────────────


def test_matrix_c_clickbait_boundary(
    base_channel_dna: Any,
    base_creative_style_plan: CreativeStylePlan,
    base_narrative_plan: NarrativePlan,
) -> None:
    """Verifies that misleading clickbait is disqualified and hard constraints prevail."""
    engine = PackagingEngine()
    grounded_context = engine._extract_grounded_context(base_narrative_plan, None)

    # 1. Permitted curiosity (grounded inquiry)
    cand_curiosity = TitleCandidate(
        text="The Overlooked Physics Behind Quantum Coherence",
        strategy=TitleStrategy.CURIOSITY,
    )
    eval_curiosity = engine._evaluate_title_candidate(
        cand_curiosity,
        grounded_context=grounded_context,
        hard_constraints=base_channel_dna.hard_constraints,
        style_hints=base_creative_style_plan.packaging_hints,
        prohibited_patterns=base_channel_dna.packaging_preferences.prohibited_packaging_patterns,
    )
    assert eval_curiosity.is_grounded is True
    assert eval_curiosity.clickbait_risk in ("NONE", "LOW")

    # 2. Sensational clickbait ("You Won't Believe", "Shocking")
    cand_clickbait = TitleCandidate(
        text="You Won't Believe What This Shocking Quantum Chip Did",
        strategy=TitleStrategy.CURIOSITY,
    )
    eval_clickbait = engine._evaluate_title_candidate(
        cand_clickbait,
        grounded_context=grounded_context,
        hard_constraints=base_channel_dna.hard_constraints,
        style_hints=base_creative_style_plan.packaging_hints,
        prohibited_patterns=base_channel_dna.packaging_preferences.prohibited_packaging_patterns,
    )
    assert eval_clickbait.is_grounded is False
    assert eval_clickbait.clickbait_risk == "HIGH"
    assert PackagingFindingCode.TITLE_CLICKBAIT_RISK.value in eval_clickbait.constraint_findings

    # 3. Prohibited vocabulary from hard constraints ("forbidden_word")
    cand_vocab = TitleCandidate(
        text="Silicon Quantum Processor forbidden_word Analysis",
        strategy=TitleStrategy.FACTUAL,
    )
    eval_vocab = engine._evaluate_title_candidate(
        cand_vocab,
        grounded_context=grounded_context,
        hard_constraints=base_channel_dna.hard_constraints,
        style_hints=base_creative_style_plan.packaging_hints,
        prohibited_patterns=base_channel_dna.packaging_preferences.prohibited_packaging_patterns,
    )
    assert eval_vocab.is_grounded is False
    assert PackagingFindingCode.HARD_CONSTRAINT_VIOLATION.value in eval_vocab.constraint_findings


# ── Test Matrix D: Title Selection ──────────────────────────────────────────


def test_matrix_d_title_selection(
    base_creative_style_plan: CreativeStylePlan,
) -> None:
    """Verifies deterministic ranking: grounded candidate beats flashy invalid candidate."""
    engine = PackagingEngine()

    c1 = TitleCandidate(
        candidate_id="c1-grounded",
        text="Silicon Quantum Processor: Benchmark Validation Results",
        strategy=TitleStrategy.FACTUAL,
        clarity_score=0.92,
        specificity_score=0.90,
        is_grounded=True,
        constraint_findings=(),
    )
    c2 = TitleCandidate(
        candidate_id="c2-flashy-clickbait",
        text="SHOCKING: Quantum Scientists Break Everything You Know",
        strategy=TitleStrategy.CURIOSITY,
        clarity_score=0.99,
        specificity_score=0.99,
        is_grounded=False,
        clickbait_risk="HIGH",
        constraint_findings=(PackagingFindingCode.TITLE_CLICKBAIT_RISK.value,),
    )
    c3 = TitleCandidate(
        candidate_id="c3-grounded-explainer",
        text="How Silicon Quantum Processors Work: Mechanism and Scaling",
        strategy=TitleStrategy.EXPLAINER,
        clarity_score=0.88,
        specificity_score=0.88,
        is_grounded=True,
        constraint_findings=(),
    )

    selected = engine._select_best_title([c1, c2, c3], style_hints=base_creative_style_plan.packaging_hints)
    assert selected.candidate_id == "c1-grounded"
    assert selected.is_grounded is True


# ── Test Matrix E: Thumbnail Concepts & Text Policy ─────────────────────────


def test_matrix_e_thumbnail_concepts_and_text_policy(
    base_channel_dna: Any,
    base_creative_style_plan: CreativeStylePlan,
    base_narrative_plan: NarrativePlan,
) -> None:
    """Verifies thumbnail concept generation, no-text and short-text policies, and rejection of overloaded text."""
    engine = PackagingEngine()
    plan = engine.generate_packaging_plan(
        channel_dna=base_channel_dna,
        creative_style_plan=base_creative_style_plan,
        narrative_plan=base_narrative_plan,
    )

    assert len(plan.thumbnail_concepts) >= 2
    thumb = plan.selected_thumbnail
    assert thumb.primary_subject != ""
    assert thumb.visual_hierarchy != ""
    assert thumb.safe_zone in (ThumbnailSafeZone.LEFT_WEIGHTED, ThumbnailSafeZone.CENTER_FOCUSED)

    # Test overloaded text rejection
    overloaded_thumb = ThumbnailConcept(
        primary_subject="Quantum Processor",
        text_overlay_intent=TextOverlayIntent.SHORT_TEXT,
        text_content="This is a very long sentence that has way more than four words in it",
    )
    findings = engine.validate_packaging_coherence(
        title=plan.selected_title,
        thumbnail=overloaded_thumb,
        description=plan.description,
        chapters=list(plan.chapters),
        tags=list(plan.tags),
        narrative_plan=base_narrative_plan,
        hard_constraints=base_channel_dna.hard_constraints,
        final_duration_seconds=300,
    )
    assert any(f.code == PackagingFindingCode.THUMBNAIL_TEXT_OVERLOADED for f in findings)


# ── Test Matrix F & G: Thumbnail Grounding & Composition ────────────────────


def test_matrix_f_and_g_thumbnail_grounding_and_composition(
    base_channel_dna: Any,
    base_creative_style_plan: CreativeStylePlan,
    base_narrative_plan: NarrativePlan,
) -> None:
    """Verifies thumbnail subject grounding and composition safe zones."""
    engine = PackagingEngine()
    plan = engine.generate_packaging_plan(
        channel_dna=base_channel_dna,
        creative_style_plan=base_creative_style_plan,
        narrative_plan=base_narrative_plan,
    )
    thumb = plan.selected_thumbnail
    # Grounded subject check
    assert "quantum" in thumb.primary_subject.lower() or "silicon" in thumb.primary_subject.lower()
    # Left-weighted to protect platform badge safe area in bottom right
    assert thumb.safe_zone == ThumbnailSafeZone.LEFT_WEIGHTED


# ── Test Matrix H: Physical Thumbnail Rendering & Physical QA ───────────────


def test_matrix_h_physical_rendering_and_qa(
    base_channel_dna: Any,
    base_creative_style_plan: CreativeStylePlan,
    base_narrative_plan: NarrativePlan,
) -> None:
    """Verifies genuine 1280x720 RGB PNG image rendering and physical QA checks."""
    engine = PackagingEngine()
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_path = Path(tmp_dir)
        plan = engine.generate_packaging_plan(
            channel_dna=base_channel_dna,
            creative_style_plan=base_creative_style_plan,
            narrative_plan=base_narrative_plan,
            output_dir=out_path,
        )

        artifact = plan.physical_thumbnail_artifact
        assert artifact is not None
        assert artifact.file_path.is_file()
        assert artifact.width == 1280
        assert artifact.height == 720
        assert artifact.format == "PNG"
        assert artifact.file_size_bytes > 1000
        assert len(artifact.content_sha256) == 64

        # Verify renderer physical validator raises error on corrupt file
        corrupt_file = out_path / "corrupt.png"
        corrupt_file.write_bytes(b"not a png image")
        with pytest.raises(ThumbnailPhysicalValidationError):
            engine.thumbnail_renderer.validate_physical(corrupt_file, concept=plan.selected_thumbnail)


# ── Test Matrix I: Description Generation & Grounding ───────────────────────


def test_matrix_i_description_generation_and_grounding(
    base_channel_dna: Any,
    base_creative_style_plan: CreativeStylePlan,
    base_narrative_plan: NarrativePlan,
) -> None:
    """Verifies structured factual description, absence of fabricated claims, and CTA integration."""
    engine = PackagingEngine()
    plan = engine.generate_packaging_plan(
        channel_dna=base_channel_dna,
        creative_style_plan=base_creative_style_plan,
        narrative_plan=base_narrative_plan,
    )

    desc = plan.description
    assert "Silicon Quantum Processor" in desc
    assert "Key Takeaways & Findings:" in desc
    assert "Timestamps:" in desc
    # Soft CTA from ChannelDNA
    assert "comments" in desc.lower() or "discussion" in desc.lower()


# ── Test Matrix J: Chapters & Duration Reconciliation ───────────────────────


def test_matrix_j_chapter_generation(
    base_channel_dna: Any,
    base_creative_style_plan: CreativeStylePlan,
    base_narrative_plan: NarrativePlan,
) -> None:
    """Verifies monotonic timestamps, physical duration bounds, and duplicate label handling."""
    engine = PackagingEngine()
    plan = engine.generate_packaging_plan(
        channel_dna=base_channel_dna,
        creative_style_plan=base_creative_style_plan,
        narrative_plan=base_narrative_plan,
        final_duration_seconds=300,
    )

    chapters = plan.chapters
    assert len(chapters) == 3
    assert chapters[0].start_time_seconds == 0
    assert chapters[0].formatted_timestamp == "00:00"

    # Strictly monotonic
    for i in range(1, len(chapters)):
        assert chapters[i].start_time_seconds > chapters[i - 1].start_time_seconds
        assert chapters[i].end_time_seconds <= 300


# ── Test Matrix K: Metadata & Keywords ──────────────────────────────────────


def test_matrix_k_metadata_keywords(
    base_channel_dna: Any,
    base_creative_style_plan: CreativeStylePlan,
    base_narrative_plan: NarrativePlan,
) -> None:
    """Verifies content-derived bounded tags and absence of keyword stuffing."""
    engine = PackagingEngine()
    plan = engine.generate_packaging_plan(
        channel_dna=base_channel_dna,
        creative_style_plan=base_creative_style_plan,
        narrative_plan=base_narrative_plan,
    )

    assert 4 <= len(plan.tags) <= 15
    # No keyword stuffing: tags should not contain huge lists
    assert len(set(plan.tags)) == len(plan.tags)


# ── Test Matrix L: Attribution Integration ──────────────────────────────────


def test_matrix_l_attribution_integration(
    base_channel_dna: Any,
    base_creative_style_plan: CreativeStylePlan,
    base_narrative_plan: NarrativePlan,
) -> None:
    """Verifies preservation of P23-D attribution obligations in packaging."""
    engine = PackagingEngine()
    manifest = {
        "audio_narration": "Generated by OMEGA Kokoro Local TTS Engine",
        "visual_diagram": "Chart data courtesy of Semiconductor Research Institute",
    }
    plan = engine.generate_packaging_plan(
        channel_dna=base_channel_dna,
        creative_style_plan=base_creative_style_plan,
        narrative_plan=base_narrative_plan,
        attribution_manifest=manifest,
    )

    assert plan.attribution_block is not None
    assert "OMEGA Kokoro Local TTS Engine" in plan.attribution_block
    assert "Semiconductor Research Institute" in plan.attribution_block
    assert "Attribution & Sources:" in plan.description


# ── Test Matrix M: Packaging Coherence ──────────────────────────────────────


def test_matrix_m_packaging_coherence(
    base_channel_dna: Any,
    base_creative_style_plan: CreativeStylePlan,
    base_narrative_plan: NarrativePlan,
) -> None:
    """Verifies that mismatch between title and thumbnail is flagged."""
    engine = PackagingEngine()
    plan = engine.generate_packaging_plan(
        channel_dna=base_channel_dna,
        creative_style_plan=base_creative_style_plan,
        narrative_plan=base_narrative_plan,
    )

    # Coherent by default
    assert len(plan.validation_findings) == 0

    # Introduce mismatch
    mismatched_thumb = ThumbnailConcept(
        primary_subject="Cryptocurrency Bitcoin Mining",
        secondary_subject="Mining Farm",
    )
    findings = engine.validate_packaging_coherence(
        title=plan.selected_title,
        thumbnail=mismatched_thumb,
        description=plan.description,
        chapters=list(plan.chapters),
        tags=list(plan.tags),
        narrative_plan=base_narrative_plan,
        hard_constraints=base_channel_dna.hard_constraints,
        final_duration_seconds=300,
    )
    assert any(f.code == PackagingFindingCode.TITLE_THUMBNAIL_PROMISE_MISMATCH for f in findings)


# ── Test Matrix O: Format Adaptation ────────────────────────────────────────


def test_matrix_o_format_adaptation(
    test_channel_id: uuid.UUID,
    base_channel_dna: Any,
    base_creative_style_plan: CreativeStylePlan,
) -> None:
    """Verifies format adaptation for SHORT format: omitted chapters and concise packaging."""
    sec1 = NarrativeSection(
        section_order=1,
        role=NarrativeSectionRole.HOOK,
        objective="Hook the viewer on quantum chips in 5 seconds",
        key_information=["1,000 qubits"],
        target_duration_seconds=10,
    )
    sec2 = NarrativeSection(
        section_order=2,
        role=NarrativeSectionRole.PAYOFF,
        objective="Reveal quantum benchmark result",
        key_information=["99.9% fidelity"],
        target_duration_seconds=35,
    )
    short_plan = NarrativePlan(
        channel_dna_revision_id=test_channel_id,
        content_generation_request_id=uuid.uuid4(),
        metadata={"premise": "Quantum Chips in 45 Seconds"},
        sections=[sec1, sec2],
        format_profile=NarrativeFormatProfile.SHORT,
        target_duration_seconds=45,
        estimated_duration_seconds=45,
    )

    engine = PackagingEngine()
    plan = engine.generate_packaging_plan(
        channel_dna=base_channel_dna,
        creative_style_plan=base_creative_style_plan,
        narrative_plan=short_plan,
        final_duration_seconds=45,
    )

    assert plan.format_profile == NarrativeFormatProfile.SHORT
    # Shorts do not have multi-part chapter listings
    assert len(plan.chapters) == 0


# ── Test Matrix P: Cross-Phase Lineage ───────────────────────────────────────


def test_matrix_p_cross_phase_lineage(
    test_channel_id: uuid.UUID,
    base_channel_dna: Any,
    base_creative_style_plan: CreativeStylePlan,
    base_narrative_plan: NarrativePlan,
) -> None:
    """Verifies full cross-phase lineage tracking in provenance."""
    engine = PackagingEngine()
    plan = engine.generate_packaging_plan(
        channel_dna=base_channel_dna,
        creative_style_plan=base_creative_style_plan,
        narrative_plan=base_narrative_plan,
    )

    prov = plan.provenance
    assert prov.channel_dna_revision_id == test_channel_id
    assert prov.creative_style_plan_id == base_creative_style_plan.plan_id
    assert prov.narrative_plan_id == base_narrative_plan.id
    assert prov.selected_title_id == plan.selected_title.candidate_id
    assert prov.selected_thumbnail_id == plan.selected_thumbnail.concept_id


# ── Test Matrix Q: Model Assistance & Fallback ──────────────────────────────


class FakeMalformedModelClient:
    """Mock model provider returning malformed / insufficient proposals."""

    def generate_packaging_proposal(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"titles": [{"text": "Bad", "strategy": "FACTUAL"}]}  # too few candidates


class FakeSuccessfulModelClient:
    """Mock model provider returning valid proposals."""

    def generate_packaging_proposal(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "titles": [
                {"text": "Silicon Quantum Processor: 1,000 Qubit Breakthrough", "strategy": "FACTUAL", "clarity_score": 0.95, "specificity_score": 0.95},
                {"text": "How Silicon Quantum Processors Work in 2026", "strategy": "EXPLAINER", "clarity_score": 0.90, "specificity_score": 0.90},
                {"text": "The Real Story Behind Silicon Quantum Scaling", "strategy": "CURIOSITY", "clarity_score": 0.85, "specificity_score": 0.85},
                {"text": "What Happens When Silicon Qubits Scale to 1,000?", "strategy": "OUTCOME", "clarity_score": 0.88, "specificity_score": 0.88},
            ]
        }


def test_matrix_q_model_fallback(
    base_channel_dna: Any,
    base_creative_style_plan: CreativeStylePlan,
    base_narrative_plan: NarrativePlan,
) -> None:
    """Verifies bounded retry and deterministic fallback when model proposals fail."""
    # 1. Successful model assistance
    good_engine = PackagingEngine(model_client=FakeSuccessfulModelClient())
    plan_good = good_engine.generate_packaging_plan(
        channel_dna=base_channel_dna,
        creative_style_plan=base_creative_style_plan,
        narrative_plan=base_narrative_plan,
    )
    assert plan_good.provenance.generation_method == "MODEL_ASSISTED"
    assert len(plan_good.title_candidates) >= 4

    # 2. Malformed model proposals fall back deterministically
    bad_engine = PackagingEngine(model_client=FakeMalformedModelClient())
    plan_fallback = bad_engine.generate_packaging_plan(
        channel_dna=base_channel_dna,
        creative_style_plan=base_creative_style_plan,
        narrative_plan=base_narrative_plan,
    )
    assert plan_fallback.provenance.generation_method == "DETERMINISTIC_HYBRID"
    assert len(plan_fallback.title_candidates) >= 4
    assert plan_fallback.selected_title.is_grounded is True
