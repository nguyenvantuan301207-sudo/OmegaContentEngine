"""Unit tests for P19-G2B Editorial Quality Pass.

Covers:
1. Viewer-facing text sanitation (strips test/phase tokens, converts casing, preserves acronyms).
2. Chapter / section label policy (title rendered at section entry, suppressed on subsequent beats).
3. Layout vocabulary & repetition control (no 3 consecutive identical layouts, semantic compatibility).
4. Semantic asset query derivation (literal technical subjects prioritized, decorative stock rejected).
5. Mechanism graphics explanatory primitives (labeled directed edges, concise named nodes).
6. Long explanation semantic decomposition (breaking long sentences into claim/mechanism/consequence).
7. Subtitle presentation & mobile readability settings (font size, stroke, margin).
8. RuntimeTruth v4 and provenance preservation invariant checks.
"""

import pytest
from omega.application.viewer_text_sanitizer import (
    sanitize_viewer_text,
    sanitize_chapter_title,
    is_structural_or_internal_label,
)
from omega.application.semantic_asset_query import (
    derive_semantic_asset_query,
    is_unrelated_stock_concept,
)
from omega.application.editorial_layout import (
    EditorialLayout,
    select_editorial_layout,
    get_compatible_layouts,
)
from omega.application.mechanism_diagram import (
    MechanismDiagramSpec,
    MechanismDiagramEdge,
    derive_relation_label,
)
from omega.application.editorial_beat_planner import (
    _decompose_semantic_sentence,
    EditorialBeatPlanner,
)
from omega.application.editorial_beat import BeatSemanticRole
from omega.application.scene_template_registry import TemplateInputKey
from omega.application.storyboard_engine import StoryboardScene, VisualStrategy
from omega.application.subtitle_presets import get_subtitle_preset
from omega.application.template_payload_resolver import TemplatePayloadResolver
from omega.application.visual_direction import (
    VisualDirection,
    VisualRenderMode,
    VisualTemplateId,
)


# ======================================================================
# 1. VIEWER-FACING TEXT SANITIZATION
# ======================================================================

def test_viewer_text_sanitization_removes_internal_tags():
    """Verify internal test/phase tags are stripped completely."""
    raw = "[P19-G2A RETRY6] Architectural Provenance and Execution"
    clean = sanitize_viewer_text(raw)
    assert "[P19-G2A RETRY6]" not in clean
    assert "P19-G2A" not in clean
    assert "RETRY6" not in clean
    assert "Architectural Provenance and Execution" in clean

    raw2 = "Step 4: [P18-G1 CANARY] Engine Pipeline Run"
    clean2 = sanitize_viewer_text(raw2)
    assert "[P18-G1 CANARY]" not in clean2
    assert "Engine Pipeline Run" in clean2


def test_sanitize_chapter_title_title_casing_and_acronyms():
    """Verify long ALL-CAPS titles are converted to Title Case while preserving technical acronyms."""
    raw = "SECTION 2: EVENT LOOP ARCHITECTURE AND ASYNC HTTP API DISPATCH"
    clean = sanitize_chapter_title(raw)
    # Acronyms preserved: HTTP, API
    assert "HTTP" in clean
    assert "API" in clean
    # Section prefix and internal casing formatted
    assert "Event Loop Architecture" in clean
    assert not clean.isupper()


def test_is_structural_or_internal_label():
    """Verify identification of synthetic or structural internal labels."""
    assert is_structural_or_internal_label("scene_001_hook") is True
    assert is_structural_or_internal_label("SCENE_5_EXPLANATION") is True
    assert is_structural_or_internal_label("[P19-G2A RETRY6]") is True
    assert is_structural_or_internal_label("How Event Loops Scale") is False


# ======================================================================
# 2. CHAPTER DISPLAY POLICY
# ======================================================================

def test_chapter_display_policy_suppresses_subsequent_beats():
    """Verify chapter title is displayed at section entry but suppressed on subsequent beats."""
    resolver = TemplatePayloadResolver()

    scene = StoryboardScene(
        sequence_index=1,
        section_id="Event Loop Architecture",
        purpose="purpose",
        source_statement_references=[1],
        narration_excerpt="Performance scales by 10x with async event loops.",
        estimated_duration_seconds=5.0,
        visual_strategy=VisualStrategy.STATISTIC,
        visual_brief="technical brief",
        on_screen_text="10x Performance",
    )

    # Beat 1: section entry
    dir_entry = VisualDirection(
        scene_index=1,
        render_mode=VisualRenderMode.TEMPLATE,
        template_id=VisualTemplateId.STATISTIC_HERO,
        asset_requirements=[],
        motion_profile="metric_emphasis",
        rationale="",
        metadata={"is_section_entry": True},
    )
    payload_entry = resolver.resolve(scene, dir_entry)
    # At section entry, TITLE is present and non-empty
    assert TemplateInputKey.TITLE in payload_entry.inputs
    assert payload_entry.inputs[TemplateInputKey.TITLE] == "Event Loop Architecture"

    # Beat 2: subsequent explanatory beat
    dir_subsequent = VisualDirection(
        scene_index=1,
        render_mode=VisualRenderMode.TEMPLATE,
        template_id=VisualTemplateId.STATISTIC_HERO,
        asset_requirements=[],
        motion_profile="metric_emphasis",
        rationale="",
        metadata={"is_section_entry": False},
    )
    payload_subsequent = resolver.resolve(scene, dir_subsequent)
    # On subsequent beats, TITLE is suppressed
    assert TemplateInputKey.TITLE not in payload_subsequent.inputs


# ======================================================================
# 3. LAYOUT VOCABULARY & REPETITION CONTROL
# ======================================================================

def test_layout_selection_avoids_three_consecutive_identical_layouts():
    """Verify layout selection never produces 3 consecutive identical layouts."""
    history = [EditorialLayout.SPLIT_LEFT_VISUAL, EditorialLayout.SPLIT_LEFT_VISUAL]
    selected = select_editorial_layout(
        semantic_role=BeatSemanticRole.EXPLANATION,
        visual_strategy=VisualStrategy.IMAGE,
        history=history,
    )
    assert selected != EditorialLayout.SPLIT_LEFT_VISUAL
    assert selected in get_compatible_layouts(BeatSemanticRole.EXPLANATION, VisualStrategy.IMAGE)


def test_layout_diversity_across_multiple_beats():
    """Verify varied layout selections across a series of image and broll explainer beats."""
    history = []
    strategies = [
        VisualStrategy.IMAGE,
        VisualStrategy.IMAGE,
        VisualStrategy.BROLL,
        VisualStrategy.IMAGE,
        VisualStrategy.BROLL,
        VisualStrategy.IMAGE,
    ]
    for strat in strategies:
        layout = select_editorial_layout(
            semantic_role=BeatSemanticRole.EXPLANATION,
            visual_strategy=strat,
            history=history,
        )
        history.append(layout)

    # Check distinct layouts
    distinct = set(history)
    assert len(distinct) >= 3, f"Expected at least 3 distinct layouts, got {distinct}"

    # Check no 3 consecutive identical
    for i in range(len(history) - 2):
        assert not (history[i] == history[i+1] == history[i+2])


# ======================================================================
# 4. SEMANTIC ASSET RELEVANCE
# ======================================================================

def test_semantic_asset_query_technical_subject_priority():
    """Verify technical explanation text generates literal technical queries."""
    narration = (
        "The async worker processes queue jobs from Redis, executing event loop tasks "
        "and writing execution results to the database."
    )
    query = derive_semantic_asset_query(narration)
    assert any(term in query for term in ["async", "worker", "queue", "server", "code", "database"])
    assert not is_unrelated_stock_concept(query)


def test_rejection_of_unrelated_decorative_concepts():
    """Verify decorative stock terms like birds, divers, handshakes are detected as unrelated."""
    assert is_unrelated_stock_concept("tropical birds flying in sky") is True
    assert is_unrelated_stock_concept("scuba diver underwater coral") is True
    assert is_unrelated_stock_concept("business handshake close up") is True
    assert is_unrelated_stock_concept("abstract geometric 3d shapes") is True
    assert is_unrelated_stock_concept("software engineer terminal code") is False
    assert is_unrelated_stock_concept("cloud server infrastructure datacenter") is False


# ======================================================================
# 5. MECHANISM GRAPHICS EXPLANATORY PRIMITIVES
# ======================================================================

def test_mechanism_diagram_labeled_edges_and_relations():
    """Verify mechanism diagrams derive concise relation labels for edges."""
    label_dispatch = derive_relation_label("Scheduler", "Worker", "The scheduler dispatches tasks to the worker.")
    assert label_dispatch == "dispatches"

    label_queue = derive_relation_label("Client", "Queue", "The client enqueues requests into the queue.")
    assert label_queue == "enqueues"

    label_produce = derive_relation_label("Renderer", "VideoFile", "The renderer produces the output mp4 video file.")
    assert label_produce == "produces"


def test_mechanism_diagram_spec_structure_with_labels():
    """Verify MechanismDiagramSpec can store labeled edges without breaking backwards compatibility."""
    spec = MechanismDiagramSpec(
        nodes=("Dispatcher", "Task Queue", "Worker Pool"),
        edges=(
            MechanismDiagramEdge(from_node="Dispatcher", to_node="Task Queue", label="enqueues"),
            MechanismDiagramEdge(from_node="Task Queue", to_node="Worker Pool", label="dispatches"),
        ),
        source_text="Dispatcher enqueues to Task Queue, which dispatches to Worker Pool.",
    )
    assert len(spec.edges) == 2
    assert spec.edges[0].label == "enqueues"
    assert spec.edges[1].label == "dispatches"


# ======================================================================
# 6. EDITORIAL PACING: LONG EXPLANATION DECOMPOSITION
# ======================================================================

def test_long_explanation_semantic_sentence_decomposition():
    """Verify long compound sentences are decomposed at real semantic boundaries."""
    long_sentence = (
        "The asynchronous event loop handles concurrent network socket polling via epoll; "
        "which allows a single thread to multiplex thousands of active connections without thread overhead."
    )
    sub_clauses = _decompose_semantic_sentence(long_sentence)
    assert len(sub_clauses) >= 2
    assert "epoll" in sub_clauses[0]
    assert "multiplex thousands" in sub_clauses[1]


def test_short_sentence_not_split():
    """Verify concise sentences are preserved as a single beat."""
    short = "This is a simple architectural claim."
    sub_clauses = _decompose_semantic_sentence(short)
    assert len(sub_clauses) == 1
    assert sub_clauses[0] == short


# ======================================================================
# 7. SUBTITLE READABILITY PRESETS
# ======================================================================

def test_subtitle_readability_mobile_preset():
    """Verify default subtitle preset provides mobile readability (large font, strong outline)."""
    preset = get_subtitle_preset("default")
    assert preset.style.font_size >= 48, "Font size should be at least 48 for mobile legibility"
    assert preset.style.bold is True
    assert preset.style.outline_width >= 2.5
    assert preset.style.margin_v >= 80


# ======================================================================
# 8. RESOLVER PRESERVES METADATA
# ======================================================================

def test_template_payload_resolver_preserves_direction_metadata():
    """Verify direction metadata is propagated into TemplatePayload metadata."""
    resolver = TemplatePayloadResolver()
    scene = StoryboardScene(
        sequence_index=1,
        section_id="Data Pipeline",
        purpose="purpose",
        source_statement_references=[1],
        narration_excerpt="Data flows from ingest to warehouse.",
        estimated_duration_seconds=5.0,
        visual_strategy=VisualStrategy.TITLE_MOTION,
        visual_brief="technical brief",
        on_screen_text="Data Pipeline Flow",
    )
    dir_meta = VisualDirection(
        scene_index=1,
        render_mode=VisualRenderMode.TEMPLATE,
        template_id=VisualTemplateId.HERO_TITLE,
        asset_requirements=[],
        motion_profile="title_reveal",
        rationale="",
        metadata={"provenance_hash": "abc123", "runtime_truth_v4": True, "layout_variant": "CENTERED_MECHANISM"},
    )
    payload = resolver.resolve(scene, dir_meta)
    assert payload.metadata.get("provenance_hash") == "abc123"
    assert payload.metadata.get("runtime_truth_v4") is True
    assert payload.metadata.get("layout_variant") == "CENTERED_MECHANISM"


# ======================================================================
# 9. P19-G2B.1: MECHANISM LAYOUT SCALE & PAYLOAD AUTHORITY
# ======================================================================

def test_mechanism_layout_scale_and_node_geometry():
    """Verify 2-node mechanism produces large dominant nodes and readable badges."""
    from omega.application.visual_template_renderer import VisualTemplateRenderer
    from omega.application.template_payload_resolver import TemplatePayload, TemplateEdge

    renderer = VisualTemplateRenderer()
    payload = TemplatePayload(
        scene_index=1,
        template_id=VisualTemplateId.FLOW_DIAGRAM,
        inputs={
            TemplateInputKey.TITLE: "Core Mechanism",
            TemplateInputKey.NODES: ["Scheduler", "Worker Pool"],
            TemplateInputKey.EDGES: [TemplateEdge(from_node="Scheduler", to_node="Worker Pool", label="dispatches")],
        },
        asset_requirements=(),
        motion_profile="sequential_flow",
        metadata={"layout_variant": "CENTERED_MECHANISM"},
    )
    doc = renderer.render(payload)
    # Scaled dimensions present in generated HTML
    assert "min-width: 420px" in doc.html
    assert "min-height: 180px" in doc.html
    assert "font-size: 40px" in doc.html
    assert "dispatches" in doc.html
    assert "data-motion-role=\"flow-node\"" in doc.html
    assert "data-motion-role=\"flow-edge\"" in doc.html


# ======================================================================
# 10. P19-G2B.1: REVEAL-STATE DETERMINISM
# ======================================================================

def test_reveal_state_determinism_sequential_flow():
    """Verify deterministic progressive reveal stages for 2-node mechanism."""
    from omega.application.visual_template_renderer import VisualTemplateRenderer
    from omega.application.template_payload_resolver import TemplatePayload, TemplateEdge
    from omega.application.visual_dom_motion import VisualDomMotionRuntime

    renderer = VisualTemplateRenderer()
    payload = TemplatePayload(
        scene_index=1,
        template_id=VisualTemplateId.FLOW_DIAGRAM,
        inputs={
            TemplateInputKey.TITLE: "Core Mechanism",
            TemplateInputKey.NODES: ["Node A", "Node B"],
            TemplateInputKey.EDGES: [TemplateEdge(from_node="Node A", to_node="Node B", label="triggers")],
        },
        asset_requirements=(),
        motion_profile="sequential_flow",
        metadata={},
    )
    doc = renderer.render(payload)
    motion_rt = VisualDomMotionRuntime()

    # At t=0.0s (start): Node 0 opacity 0, edge 0 opacity 0, node 1 opacity 0
    doc_0 = motion_rt.render_frame(doc, "sequential_flow", time_seconds=0.0, duration_seconds=12.0)
    assert '[data-motion-role="flow-node"][data-motion-index="0"]' in doc_0.html
    assert "opacity: 0" in doc_0.html

    # At t=2.0s (mid-reveal): Node 0 is fully visible (opacity: 1), edge is revealing, node 1 is still opacity: 0
    doc_mid = motion_rt.render_frame(doc, "sequential_flow", time_seconds=2.0, duration_seconds=12.0)
    assert '[data-motion-role="flow-node"][data-motion-index="0"] {\n                opacity: 1' in doc_mid.html
    assert '[data-motion-role="flow-node"][data-motion-index="1"] {\n                opacity: 0' in doc_mid.html

    # At t=4.5s (completed): All nodes and edges are fully visible (opacity: 1)
    doc_end = motion_rt.render_frame(doc, "sequential_flow", time_seconds=4.5, duration_seconds=12.0)
    assert '[data-motion-role="flow-node"][data-motion-index="0"] {\n                opacity: 1' in doc_end.html
    assert '[data-motion-role="flow-node"][data-motion-index="1"] {\n                opacity: 1' in doc_end.html
    assert '[data-motion-role="flow-edge"][data-motion-index="0"] {\n                    opacity: 1' in doc_end.html


# ======================================================================
# 11. P19-G2B.1: MOTION ELIGIBILITY RULES
# ======================================================================

def test_motion_eligibility_rules_video_renderer():
    """Verify camera motion eligibility rules across templates."""
    from omega.infrastructure.visual_v2_video_renderer import VisualV2VideoRenderer, VisualV2VideoRenderError
    from omega.application.editorial_beat import BeatMotionIntent
    from unittest.mock import MagicMock
    from pathlib import Path

    renderer = VisualV2VideoRenderer()

    # Permitted camera motion on STATISTIC_HERO and CODE_EDITOR
    # Rendering should not immediately fail at permission check for eligible templates
    # Unsupported template like HERO_TITLE should raise error if given camera motion
    from omega.application.visual_template_renderer import RenderedTemplateDocument
    unsupported_doc = RenderedTemplateDocument(
        scene_index=1,
        template_id=VisualTemplateId.HERO_TITLE,
        width=1920,
        height=1080,
        html="<html><head></head><body></body></html>",
        semantic_element_ids=(),
        content_sha256="fake",
    )

    with pytest.raises(VisualV2VideoRenderError, match="Camera motion .* not permitted"):
        import asyncio
        asyncio.run(
            renderer.render_clip(
                document=unsupported_doc,
                motion_profile="title_reveal",
                duration_seconds=3.0,
                output_path=Path("/tmp/fake.mp4"),
                browser_runtime=MagicMock(),
                camera_motion_intent=BeatMotionIntent.FOCAL_ZOOM,
            )
        )


# ======================================================================
# 12. P19-G2B.1: PROVENANCE PRESERVATION
# ======================================================================

def test_real_asset_binding_provenance_preservation():
    """Verify BoundVisualAsset and BoundBrollAsset preserve sha256, paths, and metadata."""
    from omega.application.visual_asset_binding import BoundVisualAsset, BoundBrollAsset
    from omega.application.visual_direction import VisualAssetKind
    from pathlib import Path

    broll = BoundBrollAsset(
        asset_id="broll_dev_1",
        kind=VisualAssetKind.BROLL,
        mime_type="video/mp4",
        content_sha256="506bda77a4b29eeea9fed5a1ba74e3ce40541780c933152bac34c0c3bf82c4f0",
        local_path=Path("/app/data/asset.bin"),
        duration_seconds=14.0,
        width=1920,
        height=1080,
    )
    assert broll.content_sha256.startswith("506bda77")
    assert broll.width == 1920
    assert broll.height == 1080
    assert broll.duration_seconds == 14.0

    img = BoundVisualAsset(
        asset_id="img_code_1",
        kind=VisualAssetKind.IMAGE,
        mime_type="image/jpeg",
        content_sha256="f2c718fd8ab9a7ba224a97e622ac7fe63e8073a59ed7462734c76b5d426781d1",
        data_uri="data:image/jpeg;base64,fake",
        width=6048,
        height=4032,
    )
    assert img.content_sha256.startswith("f2c718fd")
    assert img.width == 6048
    assert img.height == 4032


# ======================================================================
# 13. P19-G2B.1: RENDER SEMANTICS CACHE INVALIDATION
# ======================================================================

def test_cache_invalidation_prevents_stale_pre_g2b_reuse():
    """Verify pre-G2B v5 render cache is rejected under authoritative v6 semantics."""
    from omega.application.visual_production_v2_service import (
        CANONICAL_RENDER_SEMANTICS_VERSION,
        _canonical_render_semantics_identity,
        _validate_cached_render_semantics,
        FINAL_MASTER_SAMPLE_RATE_HZ,
        SUBTITLE_SEMANTICS_VERSION,
        SubtitleMode,
        SubtitleFallbackPolicy,
    )

    # 1. Authority version is 6
    assert CANONICAL_RENDER_SEMANTICS_VERSION == 6
    assert "canonical-render-semantics-v6" in _canonical_render_semantics_identity()

    # 2. Pre-G2B manifest (version 5)
    pre_g2b_manifest = {
        "runtime_truth_schema_version": 4,
        "subtitle_semantics_version": SUBTITLE_SEMANTICS_VERSION,
        "canonical_render_semantics_version": 5,
        "final_master_sample_rate_hz": FINAL_MASTER_SAMPLE_RATE_HZ,
        "requested_subtitle_mode": "STANDARD",
        "effective_subtitle_mode": "STANDARD",
        "subtitle_fallback_applied": False,
        "subtitle_fallback_reason": None,
        "subtitle_timing_source": "DERIVED_SEGMENT_TIMING",
        "subtitle_enabled": True,
        "karaoke_subtitles_enabled": False,
        "subtitle_mode": "sentence",
        "subtitle_mode_decision": {
            "requested_mode": "STANDARD",
            "effective_mode": "STANDARD",
            "fallback_applied": False,
            "fallback_reason": None,
            "timing_source": "DERIVED_SEGMENT_TIMING",
        },
    }
    assert not _validate_cached_render_semantics(
        manifest=pre_g2b_manifest,
        canonical_requested_mode=SubtitleMode.STANDARD,
        fallback_policy=SubtitleFallbackPolicy.STANDARD_FALLBACK,
    ), "Stale pre-G2B cache (v5) must be rejected"

    # 3. Current G2B manifest (version 6)
    g2b_manifest = {
        "runtime_truth_schema_version": 4,
        "subtitle_semantics_version": SUBTITLE_SEMANTICS_VERSION,
        "canonical_render_semantics_version": 6,
        "final_master_sample_rate_hz": FINAL_MASTER_SAMPLE_RATE_HZ,
        "requested_subtitle_mode": "STANDARD",
        "effective_subtitle_mode": "STANDARD",
        "subtitle_fallback_applied": False,
        "subtitle_fallback_reason": None,
        "subtitle_timing_source": "DERIVED_SEGMENT_TIMING",
        "subtitle_enabled": True,
        "karaoke_subtitles_enabled": False,
        "subtitle_mode": "sentence",
        "subtitle_mode_decision": {
            "requested_mode": "STANDARD",
            "effective_mode": "STANDARD",
            "fallback_applied": False,
            "fallback_reason": None,
            "timing_source": "DERIVED_SEGMENT_TIMING",
        },
    }
    assert _validate_cached_render_semantics(
        manifest=g2b_manifest,
        canonical_requested_mode=SubtitleMode.STANDARD,
        fallback_policy=SubtitleFallbackPolicy.STANDARD_FALLBACK,
    ), "Current G2B cache (v6) must be accepted"
