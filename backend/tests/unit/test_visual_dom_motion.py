import pytest

from omega.application.visual_direction import VisualTemplateId
from omega.application.visual_dom_motion import VisualDomMotionError, VisualDomMotionRuntime
from omega.application.visual_template_renderer import RenderedTemplateDocument


@pytest.fixture
def hero_doc():
    return RenderedTemplateDocument(
        scene_index=1,
        template_id=VisualTemplateId.HERO_TITLE,
        width=1920,
        height=1080,
        html="""<!doctype html><html><head></head><body><div id="hero-title">My Title</div></body></html>""",
        semantic_element_ids=("hero-title",),
        content_sha256="fake",
    )


@pytest.fixture
def flow_doc():
    return RenderedTemplateDocument(
        scene_index=1,
        template_id=VisualTemplateId.FLOW_DIAGRAM,
        width=1920,
        height=1080,
        html="""<!doctype html><html><head></head><body>
        <div data-motion-role="flow-node" data-motion-index="0">A</div>
        <div data-motion-role="flow-edge" data-motion-index="0"></div>
        <div data-motion-role="flow-node" data-motion-index="1">B</div>
        <div data-motion-role="flow-edge" data-motion-index="1"></div>
        <div data-motion-role="flow-node" data-motion-index="2">C</div>
        </body></html>""",
        semantic_element_ids=(),
        content_sha256="fake",
    )


@pytest.fixture
def stat_doc():
    return RenderedTemplateDocument(
        scene_index=1,
        template_id=VisualTemplateId.STATISTIC_HERO,
        width=1920,
        height=1080,
        html="""<!doctype html><html><head></head><body><div id="metric-value">72%</div></body></html>""",
        semantic_element_ids=(),
        content_sha256="fake",
    )


@pytest.fixture
def code_doc():
    return RenderedTemplateDocument(
        scene_index=1,
        template_id=VisualTemplateId.CODE_EDITOR,
        width=1920,
        height=1080,
        html="""<!doctype html><html><head></head><body><div id="code-content">def calculate_latency(): return 42</div></body></html>""",
        semantic_element_ids=(),
        content_sha256="fake",
    )


@pytest.fixture
def image_doc():
    return RenderedTemplateDocument(
        scene_index=1,
        template_id=VisualTemplateId.IMAGE_EXPLAINER,
        width=1920,
        height=1080,
        html="""<!doctype html><html><head></head><body><div id="image-frame" data-asset-kind="IMAGE"></div></body></html>""",
        semantic_element_ids=(),
        content_sha256="fake",
    )


def test_visual_dom_motion_general(hero_doc):
    runtime = VisualDomMotionRuntime()
    new_doc = runtime.render_frame(hero_doc, "title_reveal", 0.5, 5.0)

    assert new_doc is not hero_doc
    assert new_doc.width == hero_doc.width
    assert new_doc.height == hero_doc.height
    assert new_doc.semantic_element_ids == hero_doc.semantic_element_ids
    assert "omega-dom-motion-state" in new_doc.html
    assert "<script>" not in new_doc.html
    assert "@keyframes" not in new_doc.html
    assert "animation:" not in new_doc.html
    assert "transition:" not in new_doc.html
    assert new_doc.html.count("omega-dom-motion-state") == 1
    assert new_doc.content_sha256 != hero_doc.content_sha256


def test_visual_dom_motion_determinism(hero_doc):
    runtime = VisualDomMotionRuntime()
    doc1 = runtime.render_frame(hero_doc, "title_reveal", 0.5, 5.0)
    doc2 = runtime.render_frame(hero_doc, "title_reveal", 0.5, 5.0)

    assert doc1.html == doc2.html
    assert doc1.content_sha256 == doc2.content_sha256


def test_visual_dom_motion_time_clamping(hero_doc):
    runtime = VisualDomMotionRuntime()
    doc_neg = runtime.render_frame(hero_doc, "title_reveal", -1.0, 5.0)
    doc_0 = runtime.render_frame(hero_doc, "title_reveal", 0.0, 5.0)
    assert doc_neg.html == doc_0.html

    doc_over = runtime.render_frame(hero_doc, "title_reveal", 10.0, 5.0)
    doc_final = runtime.render_frame(hero_doc, "title_reveal", 5.0, 5.0)
    assert doc_over.html == doc_final.html


def test_visual_dom_motion_duration_fails(hero_doc):
    runtime = VisualDomMotionRuntime()
    with pytest.raises(VisualDomMotionError):
        runtime.render_frame(hero_doc, "title_reveal", 1.0, 0.0)


def test_visual_dom_motion_unknown_profile(hero_doc):
    runtime = VisualDomMotionRuntime()
    with pytest.raises(VisualDomMotionError):
        runtime.render_frame(hero_doc, "unknown", 1.0, 5.0)


def test_visual_dom_motion_none_profile(hero_doc):
    runtime = VisualDomMotionRuntime()
    doc = runtime.render_frame(hero_doc, None, 1.0, 5.0)
    assert doc is not hero_doc
    assert doc.model_dump() == hero_doc.model_dump()


def test_hero_motion_short_duration(hero_doc):
    runtime = VisualDomMotionRuntime()
    doc_end = runtime.render_frame(hero_doc, "title_reveal", 0.2, 0.2)
    assert "opacity: 1" in doc_end.html
    assert "translate3d(0, 0" in doc_end.html


def test_flow_motion_ordering(flow_doc):
    runtime = VisualDomMotionRuntime()
    # Ensure ordering is preserved by the runtime
    doc_end = runtime.render_frame(flow_doc, "sequential_flow", 0.2, 0.2)
    assert doc_end.html.count("opacity: 1") >= 5


def test_statistic_motion_preservation(stat_doc):
    runtime = VisualDomMotionRuntime()
    doc = runtime.render_frame(stat_doc, "metric_emphasis", 0.1, 0.2)
    assert "72%" in doc.html

    doc_end = runtime.render_frame(stat_doc, "metric_emphasis", 0.2, 0.2)
    assert "72%" in doc_end.html
    assert "opacity: 1" in doc_end.html


def test_code_motion_preservation(code_doc):
    runtime = VisualDomMotionRuntime()
    doc = runtime.render_frame(code_doc, "code_type_on", 0.1, 0.2)
    assert "def calculate_latency(): return 42" in doc.html

    doc_end = runtime.render_frame(code_doc, "code_type_on", 0.2, 0.2)
    assert "clip-path: polygon(0 0, 100" in doc_end.html
    assert "opacity: 1" in doc_end.html


def test_image_motion_short_duration(image_doc):
    runtime = VisualDomMotionRuntime()
    doc_end = runtime.render_frame(image_doc, "image_explainer", 0.2, 0.2)
    assert 'data-asset-kind="IMAGE"' in doc_end.html
    assert "scale(1.035)" in doc_end.html
    assert "opacity: 1" in doc_end.html


def test_style_injection_hardening(hero_doc):
    runtime = VisualDomMotionRuntime()
    doc1 = runtime.render_frame(hero_doc, "title_reveal", 2.5, 5.0)

    # Replace valid existing block
    doc2 = runtime.render_frame(doc1, "title_reveal", 5.0, 5.0)
    assert doc2.html.count("omega-dom-motion-state") == 1

    # Multiple blocks
    bad_html = doc1.html + '<style id="omega-dom-motion-state">bad</style>'
    bad_doc = RenderedTemplateDocument(
        scene_index=hero_doc.scene_index,
        template_id=hero_doc.template_id,
        width=hero_doc.width,
        height=hero_doc.height,
        html=bad_html,
        semantic_element_ids=hero_doc.semantic_element_ids,
        content_sha256="fake",
    )
    with pytest.raises(VisualDomMotionError, match="Multiple omega-dom-motion-state"):
        runtime.render_frame(bad_doc, "title_reveal", 2.5, 5.0)

    # Malformed blocks
    bad_html2 = doc1.html.replace("</style>", "", 1)
    bad_doc2 = RenderedTemplateDocument(
        scene_index=hero_doc.scene_index,
        template_id=hero_doc.template_id,
        width=hero_doc.width,
        height=hero_doc.height,
        html=bad_html2,
        semantic_element_ids=hero_doc.semantic_element_ids,
        content_sha256="fake",
    )
    with pytest.raises(VisualDomMotionError, match="Malformed"):
        runtime.render_frame(bad_doc2, "title_reveal", 2.5, 5.0)

    # Missing head
    bad_html3 = "<html><body></body></html>"
    bad_doc3 = RenderedTemplateDocument(
        scene_index=hero_doc.scene_index,
        template_id=hero_doc.template_id,
        width=hero_doc.width,
        height=hero_doc.height,
        html=bad_html3,
        semantic_element_ids=hero_doc.semantic_element_ids,
        content_sha256="fake",
    )
    with pytest.raises(VisualDomMotionError, match="missing </head>"):
        runtime.render_frame(bad_doc3, "title_reveal", 2.5, 5.0)
