"""P22-C grounded diagram and data visualization acceptance matrix."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from omega.application.camera_transition_director import CameraTransitionDirector
from omega.application.visual_explanation import (
    ChartValidator,
    DiagramLayoutEngine,
    DiagramValidator,
    GeneratedVisualAssetAdapter,
    VisualExplanationPlanner,
    style_from_channel_dna,
    wrap_label,
)
from omega.domain.channel_dna import VisualStyle
from omega.domain.visual_beat import ComparisonSide, VisualBeat, VisualRole
from omega.domain.visual_explanation import (
    ChartSpec,
    DataPoint,
    DiagramEdge,
    DiagramNode,
    DiagramOrientation,
    DiagramSpec,
    ExplanationType,
    GroundedEvidenceItem,
    GroundingState,
    VisualizationFindingCode,
)
from omega.infrastructure.visual_explanation_renderer import (
    VisualExplanationRenderer,
    VisualExplanationRenderError,
    artifact_manifest,
)

CLAIM_A = UUID(int=101)
CLAIM_B = UUID(int=102)
EVIDENCE_A = UUID(int=201)
EVIDENCE_B = UUID(int=202)
SOURCE_A = UUID(int=301)
SOURCE_B = UUID(int=302)


def beat(
    role: VisualRole = VisualRole.DATA,
    *,
    text: str = "Verified values explain the result.",
    goal: str = "Verified result",
) -> VisualBeat:
    return VisualBeat(
        id=UUID(int=1),
        scene_id="scene-1",
        parent_scene_index=1,
        beat_index=0,
        source_editorial_beat_indices=(0,),
        start_offset_ms=0,
        end_offset_ms=3000,
        duration_ms=3000,
        narration_text=text,
        visual_intent=goal,
        information_goal=goal,
        visual_role=role,
        preferred_asset_type="DIAGRAM" if role == VisualRole.DIAGRAM else "DATA_CARD",
    )


def evidence(
    claim_id: UUID = CLAIM_A,
    evidence_id: UUID = EVIDENCE_A,
    source_id: UUID = SOURCE_A,
    *,
    statement: str = "The verified measurement is 72 percent.",
    verified: bool = True,
    quote: bool = False,
) -> GroundedEvidenceItem:
    return GroundedEvidenceItem(
        claim_id=claim_id,
        evidence_id=evidence_id,
        source_id=source_id,
        statement=statement,
        source_label="Synthetic isolated research source",
        claim_type="QUOTE" if quote else "STATISTIC",
        grounding_state=GroundingState.VERIFIED if verified else GroundingState.UNCERTAIN,
        direct_quote=quote,
    )


def point(
    label: str,
    value: float | str,
    *,
    claim_id: UUID = CLAIM_A,
    evidence_id: UUID = EVIDENCE_A,
    source_id: UUID = SOURCE_A,
    unit: str = "%",
    series: str = "Primary",
    time: str | None = None,
    verified: bool = True,
) -> DataPoint:
    return DataPoint(
        label=label,
        value=value,
        unit=unit,
        series=series,
        category=label,
        time_coordinate=time,
        source_claim_id=claim_id,
        source_evidence_id=evidence_id,
        source_id=source_id,
        grounding_state=GroundingState.VERIFIED if verified else GroundingState.UNCERTAIN,
    )


def test_planner_mechanism_to_grounded_process_diagram():
    plan = VisualExplanationPlanner.plan(
        beat(VisualRole.DIAGRAM, text="Requests enter the queue, then workers process them."),
        evidence=[evidence(statement="Requests enter the queue, then workers process them.")],
    )
    assert plan.explanation_type == ExplanationType.PROCESS_FLOW
    assert plan.diagram_spec is not None
    assert len(plan.diagram_spec.nodes) == 2


def test_planner_chronology_to_timeline():
    plan = VisualExplanationPlanner.plan(
        beat(VisualRole.DIAGRAM, text="First the request is accepted, then the worker starts."),
        evidence=[evidence(statement="First the request is accepted, then the worker starts.")],
    )
    assert plan.explanation_type == ExplanationType.TIMELINE


def test_numeric_comparison_to_bar_chart_and_grounding_retained():
    items = [
        evidence(),
        evidence(CLAIM_B, EVIDENCE_B, SOURCE_B, statement="The second result is 48 percent."),
    ]
    plan = VisualExplanationPlanner.plan(
        beat(VisualRole.COMPARE),
        data_points=[
            point("A", 72),
            point("B", 48, claim_id=CLAIM_B, evidence_id=EVIDENCE_B, source_id=SOURCE_B),
        ],
        evidence=items,
    )
    assert plan.explanation_type == ExplanationType.BAR_CHART
    assert plan.chart_spec is not None
    assert plan.chart_spec.data_points[1].source_id == SOURCE_B


def test_trend_to_line_chart_with_stable_order():
    points = [point("Jan", 10, time="2026-01"), point("Feb", 20, time="2026-02")]
    first = VisualExplanationPlanner.plan(beat(), data_points=points, evidence=[evidence()])
    second = VisualExplanationPlanner.plan(beat(), data_points=points, evidence=[evidence()])
    assert first.explanation_type == ExplanationType.LINE_CHART
    assert first == second


def test_quote_and_paraphrase_remain_distinct():
    quote = VisualExplanationPlanner.plan(beat(VisualRole.QUOTE), evidence=[evidence(quote=True)])
    paraphrase = VisualExplanationPlanner.plan(beat(VisualRole.EVIDENCE), evidence=[evidence()])
    assert quote.explanation_type == ExplanationType.QUOTE_CARD
    assert quote.evidence_spec is not None and quote.evidence_spec.direct_quote is True
    assert paraphrase.explanation_type == ExplanationType.EVIDENCE_CARD
    assert paraphrase.evidence_spec is not None and paraphrase.evidence_spec.direct_quote is False


def test_non_visualizable_statement_uses_safe_fallback():
    plan = VisualExplanationPlanner.plan(beat(VisualRole.EXPLAIN, text="A general observation."))
    assert plan.fallback_used is True
    assert plan.fallback_reason == "NO_GROUNDED_VISUAL_EVIDENCE"


def test_unknown_claim_id_and_uncertain_evidence_are_not_promoted():
    unknown = VisualExplanationPlanner.plan(
        beat(), data_points=[point("A", 72, claim_id=UUID(int=999))], evidence=[evidence()]
    )
    uncertain = VisualExplanationPlanner.plan(
        beat(), data_points=[point("A", 72, verified=False)], evidence=[evidence(verified=False)]
    )
    assert unknown.fallback_reason == "UNGROUNDED_DATA_POINT"
    assert uncertain.fallback_reason == "UNVERIFIED_EVIDENCE"


def test_single_verified_value_becomes_key_value():
    plan = VisualExplanationPlanner.plan(
        beat(), data_points=[point("Result", 72)], evidence=[evidence()]
    )
    assert plan.explanation_type == ExplanationType.KEY_VALUE
    assert plan.evidence_spec is not None and plan.evidence_spec.value == "72.0"


def diagram_spec(*, nodes=2, edges=True, orientation=DiagramOrientation.LEFT_TO_RIGHT):
    diagram_nodes = tuple(
        DiagramNode(id=f"n{i}", label=f"Node {i}", order=i, source_claim_ids=(CLAIM_A,))
        for i in range(nodes)
    )
    diagram_edges = (
        tuple(
            DiagramEdge(
                source_node_id=f"n{i}",
                target_node_id=f"n{i + 1}",
                source_claim_ids=(CLAIM_A,),
            )
            for i in range(nodes - 1)
        )
        if edges
        else ()
    )
    return DiagramSpec(
        id="diagram-test",
        visual_beat_id=UUID(int=1),
        explanation_type=ExplanationType.PROCESS_FLOW,
        title="Deterministic process",
        nodes=diagram_nodes,
        edges=diagram_edges,
        orientation=orientation,
        source_claim_ids=(CLAIM_A,),
    )


def test_diagram_layout_is_deterministic_non_overlapping_and_aspect_safe():
    spec = diagram_spec(nodes=4)
    first = DiagramLayoutEngine.layout(spec)
    second = DiagramLayoutEngine.layout(spec)
    assert first == second
    assert (first.width, first.height) == (1920, 1080)
    for left, right in zip(first.boxes, first.boxes[1:], strict=False):
        assert left.x + left.width <= right.x


def test_vertical_layout_is_stably_ordered():
    layout = DiagramLayoutEngine.layout(
        diagram_spec(nodes=3, orientation=DiagramOrientation.TOP_TO_BOTTOM)
    )
    assert [box.y for box in layout.boxes] == sorted(box.y for box in layout.boxes)


def test_diagram_validation_orphan_invalid_edge_self_reference_and_complexity():
    orphan_codes = {
        finding.code for finding in DiagramValidator.validate(diagram_spec(edges=False))
    }
    assert VisualizationFindingCode.ORPHAN_NODE in orphan_codes
    assert VisualizationFindingCode.AMBIGUOUS_DIRECTION in orphan_codes
    invalid = diagram_spec().model_copy(
        update={
            "edges": (
                DiagramEdge(
                    source_node_id="n0", target_node_id="missing", explanatory_synthesis=True
                ),
            )
        }
    )
    assert VisualizationFindingCode.INVALID_EDGE in {
        finding.code for finding in DiagramValidator.validate(invalid)
    }
    self_ref = diagram_spec().model_copy(
        update={
            "edges": (
                DiagramEdge(source_node_id="n0", target_node_id="n0", explanatory_synthesis=True),
            )
        }
    )
    assert VisualizationFindingCode.SELF_REFERENCE in {
        finding.code for finding in DiagramValidator.validate(self_ref)
    }
    assert VisualizationFindingCode.EXCESSIVE_NODE_COUNT in {
        finding.code for finding in DiagramValidator.validate(diagram_spec(nodes=9))
    }


def chart_spec(points, kind=ExplanationType.BAR_CHART, zero=True):
    return ChartSpec(
        id="chart-test",
        visual_beat_id=UUID(int=1),
        explanation_type=kind,
        title="Grounded chart",
        x_axis_label="Category",
        y_axis_label="Percent",
        data_points=tuple(points),
        zero_baseline=zero,
    )


def test_chart_validation_valid_bar_and_line():
    assert ChartValidator.validate(chart_spec([point("A", 1), point("B", 2)])) == ()
    assert (
        ChartValidator.validate(
            chart_spec(
                [point("Jan", 1, time="2026-01"), point("Feb", 2, time="2026-02")],
                ExplanationType.LINE_CHART,
            )
        )
        == ()
    )


@pytest.mark.parametrize(
    ("spec", "code"),
    [
        (chart_spec([]), VisualizationFindingCode.EMPTY_DATASET),
        (chart_spec([point("A", "not-a-number")]), VisualizationFindingCode.NON_NUMERIC_VALUE),
        (
            chart_spec([point("A", 1, unit="%"), point("B", 2, unit="ms")]),
            VisualizationFindingCode.MIXED_INCOMPATIBLE_UNITS,
        ),
        (chart_spec([point("A", 1), point("A", 2)]), VisualizationFindingCode.DUPLICATE_CATEGORY),
        (
            chart_spec([point("Only", 1, time="2026")], ExplanationType.LINE_CHART),
            VisualizationFindingCode.INSUFFICIENT_POINTS,
        ),
        (
            chart_spec(
                [point("Feb", 2, time="2026-02"), point("Jan", 1, time="2026-01")],
                ExplanationType.LINE_CHART,
            ),
            VisualizationFindingCode.INVALID_TIME_ORDER,
        ),
        (
            chart_spec([point("A", 50), point("B", 60)], zero=False),
            VisualizationFindingCode.MISLEADING_AXIS_RANGE,
        ),
    ],
)
def test_chart_validation_findings(spec, code):
    assert code in {finding.code for finding in ChartValidator.validate(spec)}


def test_text_wrapping_and_overflow_detection():
    lines = wrap_label("A long but readable label that wraps safely", max_chars=14, max_lines=4)
    assert len(lines) > 1
    too_long = diagram_spec().model_copy(
        update={
            "nodes": (
                DiagramNode(id="n0", label="word " * 24, order=0, source_claim_ids=(CLAIM_A,)),
                DiagramNode(id="n1", label="safe", order=1, source_claim_ids=(CLAIM_A,)),
            )
        }
    )
    assert VisualizationFindingCode.TEXT_OVERFLOW in {
        finding.code for finding in DiagramValidator.validate(too_long)
    }


def test_channel_style_projection_does_not_touch_values():
    projected = style_from_channel_dna(
        VisualStyle(
            color_preferences=["#111111", "#22AAFF", "#EEEEEE"], font_preferences=["Montserrat"]
        )
    )
    assert projected.background == "#111111"
    assert projected.accent == "#22AAFF"
    assert projected.font_family.startswith("Montserrat")


def test_comparison_mapping_and_p22b_camera_stay_stable():
    comparison_beat = beat(VisualRole.COMPARE)
    left = CameraTransitionDirector.direct(
        [comparison_beat], comparison_focus={0: ComparisonSide.LEFT}
    ).camera_plans[0]
    right = CameraTransitionDirector.direct(
        [comparison_beat], comparison_focus={0: ComparisonSide.RIGHT}
    ).camera_plans[0]
    assert left.focus_region is not None and left.focus_region.label == "comparison LEFT"
    assert right.focus_region is not None and right.focus_region.label == "comparison RIGHT"


def test_svg_renderer_outputs_all_supported_physical_families():
    renderer = VisualExplanationRenderer()
    process = VisualExplanationPlanner.plan(
        beat(VisualRole.DIAGRAM, text="Input causes output."),
        evidence=[evidence(statement="Input causes output.")],
    )
    bar = VisualExplanationPlanner.plan(
        beat(VisualRole.COMPARE),
        data_points=[point("A", 72), point("B", 48)],
        evidence=[evidence()],
    )
    line = VisualExplanationPlanner.plan(
        beat(),
        data_points=[point("Jan", 10, time="2026-01"), point("Feb", 20, time="2026-02")],
        evidence=[evidence()],
    )
    for plan in (process, bar, line):
        svg = renderer.render_svg(plan)
        assert 'viewBox="0 0 1920 1080"' in svg
        assert plan.explanation_type.value not in svg or "<svg" in svg


def test_quote_markup_only_for_direct_quote():
    renderer = VisualExplanationRenderer()
    direct = VisualExplanationPlanner.plan(beat(VisualRole.QUOTE), evidence=[evidence(quote=True)])
    paraphrase = VisualExplanationPlanner.plan(beat(VisualRole.EVIDENCE), evidence=[evidence()])
    assert "“" in renderer.render_svg(direct)
    assert "“" not in renderer.render_svg(paraphrase)


@pytest.mark.asyncio
async def test_physical_artifact_provenance_hash_and_asset_adapter(tmp_path: Path):
    plan = VisualExplanationPlanner.plan(
        beat(VisualRole.COMPARE),
        data_points=[point("A", 72), point("B", 48)],
        evidence=[evidence()],
    )
    artifact = VisualExplanationRenderer().render(
        plan,
        output_dir=tmp_path,
        generated_at=datetime(2026, 10, 3, tzinfo=UTC),
    )
    assert artifact.svg_path.is_file() and artifact.png_path.is_file()
    assert (artifact.width, artifact.height) == (1920, 1080)
    assert len(artifact.content_sha256) == 64
    assert artifact.provenance.visual_beat_id == plan.visual_beat_id
    assert artifact.provenance.source_claim_ids == (CLAIM_A,)
    bound = GeneratedVisualAssetAdapter.to_bound_visual_asset(artifact)
    assert bound.asset_id.startswith("generated:")
    assert bound.content_sha256 == artifact.content_sha256
    assert '"generator_version":"omega-p22c-svg-v1"' in artifact_manifest([artifact])


def test_renderer_conversion_failure_requests_static_fallback(tmp_path: Path, monkeypatch):
    plan = VisualExplanationPlanner.plan(beat(VisualRole.EVIDENCE), evidence=[evidence()])
    renderer = VisualExplanationRenderer()
    monkeypatch.setattr(
        renderer,
        "_convert_svg",
        lambda *_: (_ for _ in ()).throw(VisualExplanationRenderError("offline")),
    )
    result = renderer.render_with_static_fallback(plan, output_dir=tmp_path)
    assert result.artifact is None
    assert result.static_fallback_required is True
    assert "offline" in (result.fallback_reason or "")


def test_planner_system_diagram_and_relationship_map_cues():
    system_plan = VisualExplanationPlanner.plan(
        beat(VisualRole.DIAGRAM, text="The system architecture coordinates client, then server responds.", goal="System Architecture"),
        evidence=[evidence(statement="The system architecture coordinates client, then server responds.")],
    )
    assert system_plan.explanation_type == ExplanationType.SYSTEM_DIAGRAM

    rel_plan = VisualExplanationPlanner.plan(
        beat(VisualRole.DIAGRAM, text="Network nodes interact, then the relationship graph updates.", goal="Network relationship"),
        evidence=[evidence(statement="Network nodes interact, then the relationship graph updates.")],
    )
    assert rel_plan.explanation_type == ExplanationType.RELATIONSHIP_MAP


def test_planner_invalid_chart_data_falls_back_with_findings():
    # Incompatible mixed units -> blocking finding -> falls back gracefully
    items = [evidence(), evidence(CLAIM_B, EVIDENCE_B, SOURCE_B)]
    plan = VisualExplanationPlanner.plan(
        beat(VisualRole.COMPARE),
        data_points=[
            point("A", 72, unit="%"),
            point("B", 48, claim_id=CLAIM_B, evidence_id=EVIDENCE_B, source_id=SOURCE_B, unit="ms"),
        ],
        evidence=items,
    )
    assert plan.fallback_used is True
    assert plan.fallback_reason == "INVALID_CHART_DATA"
    assert any(f.code == VisualizationFindingCode.MIXED_INCOMPATIBLE_UNITS for f in plan.findings)


def test_planner_invalid_diagram_falls_back_with_findings():
    # If mechanism resolution produces orphan or self reference
    plan = VisualExplanationPlanner.plan(
        beat(VisualRole.DIAGRAM, text="No mechanism here at all.", goal="General concept"),
        evidence=[evidence()],
    )
    assert plan.fallback_used is True
    assert plan.fallback_reason == "DIAGRAM_RELATION_NOT_GROUNDED"


def test_canonical_pipeline_integration_beat_to_renderer_contract(tmp_path: Path):
    """End-to-end integration: VisualBeat -> Plan -> Artifact -> BoundVisualAsset -> Director -> Adapter -> Unit."""
    from omega.application.beat_render_adapter import BeatRenderAdapter
    from omega.application.beat_visual_direction import BeatVisualDirector
    from omega.application.editorial_beat_planner import EditorialBeatPlanner
    from omega.application.storyboard_engine import StoryboardScene, VisualStrategy
    from omega.application.beat_asset_policy import BeatAssetPlan, BeatAssetDecision, BeatAssetAction
    from omega.application.visual_direction import VisualAssetKind

    # 1. Storyboard Scene
    scene = StoryboardScene(
        sequence_index=1,
        section_id="sec_perf",
        purpose="Demonstrate performance comparison",
        source_statement_references=[10],
        narration_excerpt="Postgres handles ACID transactions with 72 percent efficiency while MongoDB achieves 48 percent.",
        estimated_duration_seconds=4.0,
        visual_strategy=VisualStrategy.IMAGE,
        visual_brief="Database benchmark comparison",
    )

    # 2. Editorial Beat Plan + Timing
    ed_plan = EditorialBeatPlanner.plan(scene=scene)
    timing_plan = EditorialBeatPlanner.allocate_timing(ed_plan, scene_duration_ms=4000)

    # 3. VisualBeat
    v_beat = beat(
        VisualRole.COMPARE,
        text=scene.narration_excerpt,
        goal="Database benchmark comparison",
    )

    # 4. VisualExplanationPlanner + Renderer
    items = [
        evidence(CLAIM_A, EVIDENCE_A, SOURCE_A, statement="Postgres handles ACID transactions with 72 percent efficiency."),
        evidence(CLAIM_B, EVIDENCE_B, SOURCE_B, statement="MongoDB achieves 48 percent."),
    ]
    expl_plan = VisualExplanationPlanner.plan(
        v_beat,
        data_points=[
            point("Postgres", 72, claim_id=CLAIM_A, evidence_id=EVIDENCE_A, source_id=SOURCE_A),
            point("MongoDB", 48, claim_id=CLAIM_B, evidence_id=EVIDENCE_B, source_id=SOURCE_B),
        ],
        evidence=items,
    )
    assert expl_plan.explanation_type == ExplanationType.BAR_CHART

    renderer = VisualExplanationRenderer()
    artifact = renderer.render(expl_plan, output_dir=tmp_path)
    assert artifact.png_path.is_file()

    # 5. BoundVisualAsset Adapter
    bound_asset = GeneratedVisualAssetAdapter.to_bound_visual_asset(artifact)
    assert bound_asset.kind == VisualAssetKind.IMAGE

    # 6. BeatVisualDirector
    direction_plan = BeatVisualDirector.resolve_plan(scene=scene, beat_plan=ed_plan)
    assert len(direction_plan.directions) == len(ed_plan.beats)

    # 7. CameraTransitionDirector
    ct_plan = CameraTransitionDirector.direct([v_beat])
    camera_plans = ct_plan.camera_plans
    transition_plans = ct_plan.transition_plans

    # 8. Asset Plan with bound asset
    asset_plan = BeatAssetPlan(
        parent_scene_index=1,
        decisions=(
            BeatAssetDecision(
                parent_scene_index=1,
                beat_index=0,
                action=BeatAssetAction.ACQUIRE_IF_NEEDED,
                asset_id=bound_asset.asset_id,
                required_kind=VisualAssetKind.IMAGE,
                rationale="Grounded explanatory graphic",
            ),
        ),
    )

    # 9. BeatRenderAdapter adapts to BeatRenderPlan
    res = BeatRenderAdapter.adapt(
        scene=scene,
        beat_plan=ed_plan,
        timing_plan=timing_plan,
        direction_plan=direction_plan,
        asset_plan=asset_plan,
        camera_plans=camera_plans,
        transition_plans=transition_plans,
    )
    assert res.eligible is True
    assert res.plan is not None
    assert len(res.plan.units) == 1
    unit = res.plan.units[0]
    assert unit.duration_ms == 4000
    assert unit.direction_view.scene_index == 1


def _decode_png_pixels(png_bytes: bytes, width: int = 1920, height: int = 1080) -> list[bytes]:
    import struct
    import zlib
    pos = 8
    idat_data = bytearray()
    while pos < len(png_bytes):
        length = struct.unpack(">I", png_bytes[pos:pos + 4])[0]
        tag = png_bytes[pos + 4:pos + 8]
        if tag == b"IDAT":
            idat_data.extend(png_bytes[pos + 8:pos + 8 + length])
        pos += 12 + length
    decompressed = zlib.decompress(bytes(idat_data))
    stride = 1 + width * 3
    return [decompressed[y * stride + 1: (y + 1) * stride] for y in range(height)]


def _get_pixel(rows: list[bytes], x: int, y: int) -> tuple[int, int, int]:
    offset = x * 3
    return (rows[y][offset], rows[y][offset + 1], rows[y][offset + 2])


def test_p22c_raster_content_and_layout(tmp_path: Path):
    """Verifies that materialized PNG contains actual foreground shapes, text, and layout regions."""
    renderer = VisualExplanationRenderer()

    # 1. Diagram Plan
    diag_plan = VisualExplanationPlanner.plan(
        beat(VisualRole.DIAGRAM, text="Input causes processing then storage."),
        evidence=[evidence(statement="Input causes processing then storage.")],
    )
    art_diag = renderer.render(diag_plan, output_dir=tmp_path / "diag")
    assert art_diag.png_path.is_file()
    diag_png = art_diag.png_path.read_bytes()
    diag_rows = _decode_png_pixels(diag_png)

    # Background color at corner
    bg_pixel = _get_pixel(diag_rows, 10, 10)

    # Verify PNG differs from blank frame (multiple foreground regions/pixels exist)
    diag_non_bg = sum(
        1 for y in range(0, 1080, 5) for x in range(0, 1920, 5)
        if _get_pixel(diag_rows, x, y) != bg_pixel
    )
    assert diag_non_bg > 500, "Diagram PNG must contain substantial non-background foreground pixels"

    # Layout correspondence: diagram layout boxes should have node color at center
    layout = DiagramLayoutEngine.layout(diag_plan.diagram_spec)
    for box in layout.boxes:
        center_x = int(box.x + box.width / 2)
        center_y = int(box.y + box.height / 2)
        node_pixel = _get_pixel(diag_rows, center_x, center_y)
        # Center of node is either text or node box fill, neither of which is background
        assert node_pixel != bg_pixel, f"Diagram node at ({center_x}, {center_y}) must not be background"

    # 2. Bar Chart Plan
    e1, e2 = evidence(), evidence(CLAIM_B, EVIDENCE_B, SOURCE_B, statement="The second is 48 percent.")
    chart_plan = VisualExplanationPlanner.plan(
        beat(VisualRole.COMPARE),
        data_points=[point("A", 72), point("B", 48, claim_id=CLAIM_B, evidence_id=EVIDENCE_B, source_id=SOURCE_B)],
        evidence=[e1, e2],
    )
    art_chart = renderer.render(chart_plan, output_dir=tmp_path / "chart")
    assert art_chart.png_path.is_file()
    chart_png = art_chart.png_path.read_bytes()
    chart_rows = _decode_png_pixels(chart_png)

    chart_non_bg = sum(
        1 for y in range(0, 1080, 5) for x in range(0, 1920, 5)
        if _get_pixel(chart_rows, x, y) != bg_pixel
    )
    assert chart_non_bg > 500, "Chart PNG must contain substantial non-background foreground pixels"

    # Layout correspondence: bar area must have accent color
    # Left axis is at x=220, top=250, bottom at y=860. Bar 0 is at x in [355, 835], y in [320, 860]
    bar_sample_pixel = _get_pixel(chart_rows, 500, 500)
    assert bar_sample_pixel != bg_pixel, "Bar area at (500, 500) must be drawn with foreground/bar fill"


def test_p22c_raster_determinism_and_dataset_sensitivity(tmp_path: Path):
    """Verifies determinism (identical input -> identical hash) and sensitivity (different input -> different hash)."""
    import hashlib
    renderer = VisualExplanationRenderer()

    # Plan 1 (A=72, B=48)
    e1, e2 = evidence(), evidence(CLAIM_B, EVIDENCE_B, SOURCE_B, statement="The second is 48 percent.")
    plan_1a = VisualExplanationPlanner.plan(
        beat(VisualRole.COMPARE),
        data_points=[point("A", 72), point("B", 48, claim_id=CLAIM_B, evidence_id=EVIDENCE_B, source_id=SOURCE_B)],
        evidence=[e1, e2],
    )
    plan_1b = VisualExplanationPlanner.plan(
        beat(VisualRole.COMPARE),
        data_points=[point("A", 72), point("B", 48, claim_id=CLAIM_B, evidence_id=EVIDENCE_B, source_id=SOURCE_B)],
        evidence=[e1, e2],
    )

    # Plan 2 (A=15, B=95) with different values
    e2_alt = evidence(CLAIM_B, EVIDENCE_B, SOURCE_B, statement="The second is 95 percent.")
    plan_2 = VisualExplanationPlanner.plan(
        beat(VisualRole.COMPARE),
        data_points=[point("A", 15), point("B", 95, claim_id=CLAIM_B, evidence_id=EVIDENCE_B, source_id=SOURCE_B)],
        evidence=[e1, e2_alt],
    )

    art_1a = renderer.render(plan_1a, output_dir=tmp_path / "1a")
    art_1b = renderer.render(plan_1b, output_dir=tmp_path / "1b")
    art_2 = renderer.render(plan_2, output_dir=tmp_path / "2")

    hash_1a = hashlib.sha256(art_1a.png_path.read_bytes()).hexdigest()
    hash_1b = hashlib.sha256(art_1b.png_path.read_bytes()).hexdigest()
    hash_2 = hashlib.sha256(art_2.png_path.read_bytes()).hexdigest()

    # P22C_RASTER_DETERMINISM_TEST = PASS
    assert hash_1a == hash_1b, "Deterministic identical input must produce identical PNG hash"

    # P22C_RASTER_CONTENT_TEST = PASS (different data produces different physical image hash)
    assert hash_1a != hash_2, "Two different source datasets must produce different PNG content hashes"


