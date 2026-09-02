import pytest

from omega.application.scene_template_registry import TemplateInputKey
from omega.application.template_payload_resolver import TemplatePayload
from omega.application.visual_direction import VisualTemplateId
from omega.application.visual_template_renderer import (
    VisualTemplateRenderer,
    VisualTemplateRenderError,
)


@pytest.fixture
def renderer():
    return VisualTemplateRenderer()

def create_payload(template_id, inputs):
    return TemplatePayload(
        scene_index=1,
        template_id=template_id,
        inputs=inputs,
        asset_requirements=(),
        motion_profile="test",
        metadata={}
    )

def test_general_html_structure(renderer):
    payload = create_payload(VisualTemplateId.HERO_TITLE, {TemplateInputKey.TITLE: "Hello"})
    doc = renderer.render(payload)

    assert doc.width == 1920
    assert doc.height == 1080
    assert "<!doctype html>" in doc.html.lower()
    assert "<html>" in doc.html.lower()
    assert "<head>" in doc.html.lower()
    assert "<body>" in doc.html.lower()
    assert "1920px" in doc.html
    assert "1080px" in doc.html
    assert "http://" not in doc.html
    assert "https://" not in doc.html
    assert "<script" not in doc.html.lower()
    assert "test" not in doc.html.lower()
    assert "placeholder" not in doc.html.lower()

def test_determinism_and_mutation(renderer):
    inputs = {TemplateInputKey.TITLE: "Hello"}
    payload = create_payload(VisualTemplateId.HERO_TITLE, inputs)

    dump1 = payload.model_dump()
    doc1 = renderer.render(payload)
    doc2 = renderer.render(payload)

    assert doc1.content_sha256 == doc2.content_sha256
    assert doc1.html == doc2.html
    assert payload.model_dump() == dump1

def test_unsupported_template_fails(renderer):
    payload = create_payload(VisualTemplateId.BROLL_EXPLAINER, {TemplateInputKey.BODY: "Hello"})
    with pytest.raises(VisualTemplateRenderError, match="Unsupported template_id"):
        renderer.render(payload)

def test_missing_required_input_fails(renderer):
    payload = create_payload(VisualTemplateId.HERO_TITLE, {})
    with pytest.raises(VisualTemplateRenderError, match="Missing required input key"):
        renderer.render(payload)

def test_unexpected_input_fails(renderer):
    payload = create_payload(VisualTemplateId.HERO_TITLE, {
        TemplateInputKey.TITLE: "Hello",
        TemplateInputKey.BODY: "Unexpected"
    })
    with pytest.raises(VisualTemplateRenderError, match="Unexpected input key"):
        renderer.render(payload)

def test_html_content_escaped(renderer):
    payload = create_payload(VisualTemplateId.HERO_TITLE, {TemplateInputKey.TITLE: "<script>alert('1')</script>"})
    doc = renderer.render(payload)
    assert "<script>" not in doc.html
    assert "&lt;script&gt;alert(&#x27;1&#x27;)&lt;/script&gt;" in doc.html

def test_hero_rendering(renderer):
    payload = create_payload(VisualTemplateId.HERO_TITLE, {
        TemplateInputKey.TITLE: "My Title",
        TemplateInputKey.SUBTITLE: "My Subtitle"
    })
    doc = renderer.render(payload)

    assert "My Title" in doc.html
    assert "My Subtitle" in doc.html
    assert 'class="hero-title"' in doc.html
    assert 'data-motion-role="title"' in doc.html
    assert 'data-motion-role="subtitle"' in doc.html
    assert 'data-motion-role="accent"' in doc.html
    assert "scene-root" in doc.semantic_element_ids
    assert "hero-title" in doc.semantic_element_ids

def test_flow_diagram_2_nodes(renderer):
    payload = create_payload(VisualTemplateId.FLOW_DIAGRAM, {
        TemplateInputKey.NODES: ["A", "B"]
    })
    doc = renderer.render(payload)

    assert "A" in doc.html
    assert "B" in doc.html
    assert 'data-motion-role="flow-node"' in doc.html
    assert 'data-motion-role="flow-edge"' in doc.html
    # 2 nodes, 1 edge -> 1 arrow marker
    assert doc.html.count('data-motion-role="flow-node"') == 2
    assert doc.html.count('data-motion-role="flow-edge"') == 1

def test_flow_diagram_5_nodes(renderer):
    payload = create_payload(VisualTemplateId.FLOW_DIAGRAM, {
        TemplateInputKey.NODES: ["A", "B", "C", "D", "E"]
    })
    doc = renderer.render(payload)
    assert doc.html.count('data-motion-role="flow-node"') == 5
    assert doc.html.count('data-motion-role="flow-edge"') == 4

def test_statistic_hero_percent(renderer):
    payload = create_payload(VisualTemplateId.STATISTIC_HERO, {
        TemplateInputKey.METRIC: "72%"
    })
    doc = renderer.render(payload)
    assert "72%" in doc.html
    assert 'data-motion-role="metric"' in doc.html
    assert "stroke-dasharray=" in doc.html

def test_statistic_hero_non_percent(renderer):
    payload = create_payload(VisualTemplateId.STATISTIC_HERO, {
        TemplateInputKey.METRIC: "50ms"
    })
    doc = renderer.render(payload)
    assert "50ms" in doc.html
    assert 'data-motion-role="metric"' in doc.html
    # should not have exact percentage calculation
    assert "stroke-dasharray=" not in doc.html

def test_code_editor(renderer):
    code = "def foo():\n    return '<tag>'"
    payload = create_payload(VisualTemplateId.CODE_EDITOR, {
        TemplateInputKey.CODE: code,
        TemplateInputKey.LANGUAGE: "python"
    })
    doc = renderer.render(payload)

    assert "python" in doc.html
    assert "def foo():" in doc.html
    assert "&lt;tag&gt;" in doc.html
    assert 'data-motion-role="code-panel"' in doc.html
    assert 'data-motion-role="code-content"' in doc.html

def test_image_explainer(renderer):
    payload = create_payload(VisualTemplateId.IMAGE_EXPLAINER, {
        TemplateInputKey.BODY: "Some body text",
        TemplateInputKey.TITLE: "Image Title",
        TemplateInputKey.CAPTION: "Image Caption"
    })
    doc = renderer.render(payload)

    assert "Some body text" in doc.html
    assert "Image Title" in doc.html
    assert "Image Caption" in doc.html
    assert 'data-asset-kind="IMAGE"' in doc.html
    assert 'data-motion-role="image-frame"' in doc.html
    assert "placeholder" not in doc.html.lower()
