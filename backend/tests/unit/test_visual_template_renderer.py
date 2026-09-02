import pytest

from omega.application.scene_template_registry import TemplateInputKey
from omega.application.template_payload_resolver import TemplatePayload
from omega.application.visual_asset_binding import BoundVisualAsset
from omega.application.visual_direction import VisualAssetKind, VisualTemplateId
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

def make_bound_asset(
    kind=VisualAssetKind.IMAGE,
    mime_type="image/jpeg",
    data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRg==",
    content_sha256="0" * 64,
):
    return BoundVisualAsset(
        asset_id="asset_1",
        kind=kind,
        mime_type=mime_type,
        content_sha256=content_sha256,
        data_uri=data_uri,
        width=1920,
        height=1080,
    )


def test_image_explainer(renderer):
    payload = create_payload(VisualTemplateId.IMAGE_EXPLAINER, {
        TemplateInputKey.BODY: "Some body text",
        TemplateInputKey.TITLE: "Image Title",
        TemplateInputKey.CAPTION: "Image Caption",
    })
    asset = make_bound_asset()
    doc = renderer.render(payload, assets=(asset,))

    assert "Some body text" in doc.html
    assert "Image Title" in doc.html
    assert "Image Caption" in doc.html
    assert 'data-asset-kind="IMAGE"' in doc.html
    assert 'data-motion-role="image-frame"' in doc.html
    assert "placeholder" not in doc.html.lower()
    assert '<img id="image-element" class="image-asset"' in doc.html
    assert 'src="data:image/jpeg;base64,/9j/4AAQSkZJRg=="' in doc.html
    assert 'alt="Image Caption"' in doc.html
    assert "http://" not in doc.html
    assert "https://" not in doc.html


def test_image_explainer_missing_asset_fails(renderer):
    payload = create_payload(VisualTemplateId.IMAGE_EXPLAINER, {
        TemplateInputKey.BODY: "Some body text",
    })
    with pytest.raises(
        VisualTemplateRenderError,
        match=r"^Missing required IMAGE asset for IMAGE_EXPLAINER$",
    ):
        renderer.render(payload, assets=())


def test_image_explainer_multiple_assets_fail(renderer):
    payload = create_payload(VisualTemplateId.IMAGE_EXPLAINER, {
        TemplateInputKey.BODY: "Some body text",
    })
    a1 = make_bound_asset()
    a2 = make_bound_asset()
    with pytest.raises(
        VisualTemplateRenderError,
        match=r"^Multiple IMAGE assets provided for IMAGE_EXPLAINER$",
    ):
        renderer.render(payload, assets=(a1, a2))


def test_image_explainer_wrong_asset_kind_fails(renderer):
    payload = create_payload(VisualTemplateId.IMAGE_EXPLAINER, {
        TemplateInputKey.BODY: "Some body text",
    })
    asset = make_bound_asset(kind=VisualAssetKind.BROLL, mime_type="video/mp4")
    with pytest.raises(
        VisualTemplateRenderError,
        match=r"^Invalid asset kind for IMAGE_EXPLAINER$",
    ):
        renderer.render(payload, assets=(asset,))


def test_image_explainer_unsupported_mime_fails():
    with pytest.raises(
        ValueError,
        match=r"Unsupported bound image MIME type",
    ):
        make_bound_asset(
            mime_type="image/gif",
            data_uri="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7",
        )


def test_image_explainer_alt_escaping(renderer):
    payload = create_payload(VisualTemplateId.IMAGE_EXPLAINER, {
        TemplateInputKey.BODY: "Body & text",
        TemplateInputKey.CAPTION: 'Caption with "quotes" & <tags>',
    })
    asset = make_bound_asset()
    doc = renderer.render(payload, assets=(asset,))

    assert 'alt="Caption with &quot;quotes&quot; &amp; &lt;tags&gt;"' in doc.html


def test_image_explainer_determinism(renderer):
    payload = create_payload(VisualTemplateId.IMAGE_EXPLAINER, {
        TemplateInputKey.BODY: "Body text",
        TemplateInputKey.TITLE: "Title",
    })
    asset = make_bound_asset()

    doc1 = renderer.render(payload, assets=(asset,))
    doc2 = renderer.render(payload, assets=(asset,))

    assert doc1.content_sha256 == doc2.content_sha256
    assert doc1.html == doc2.html


def test_non_image_template_unexpected_asset_fails(renderer):
    payload = create_payload(VisualTemplateId.HERO_TITLE, {
        TemplateInputKey.TITLE: "Hero Title",
    })
    asset = make_bound_asset()

    with pytest.raises(
        VisualTemplateRenderError,
        match=r"^Unexpected bound assets for template$",
    ):
        renderer.render(payload, assets=(asset,))


def test_bound_visual_asset_valid_formats():
    # JPEG
    b_jpeg = BoundVisualAsset(
        asset_id="1",
        kind=VisualAssetKind.IMAGE,
        mime_type="image/jpeg",
        content_sha256="a" * 64,
        data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRg==",
    )
    assert b_jpeg.mime_type == "image/jpeg"

    # PNG
    b_png = BoundVisualAsset(
        asset_id="2",
        kind=VisualAssetKind.IMAGE,
        mime_type="image/png",
        content_sha256="b" * 64,
        data_uri="data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==",
    )
    assert b_png.mime_type == "image/png"

    # WEBP
    b_webp = BoundVisualAsset(
        asset_id="3",
        kind=VisualAssetKind.IMAGE,
        mime_type="image/webp",
        content_sha256="c" * 64,
        data_uri="data:image/webp;base64,UklGRg==",
    )
    assert b_webp.mime_type == "image/webp"


def test_bound_visual_asset_remote_urls_rejected():
    with pytest.raises(ValueError, match=r"data_uri must begin with exact"):
        BoundVisualAsset(
            asset_id="1",
            kind=VisualAssetKind.IMAGE,
            mime_type="image/jpeg",
            content_sha256="a" * 64,
            data_uri="https://example.com/image.jpg",
        )

    with pytest.raises(ValueError, match=r"data_uri must begin with exact"):
        BoundVisualAsset(
            asset_id="1",
            kind=VisualAssetKind.IMAGE,
            mime_type="image/jpeg",
            content_sha256="a" * 64,
            data_uri="http://example.com/image.jpg",
        )

    with pytest.raises(ValueError, match=r"data_uri must begin with exact"):
        BoundVisualAsset(
            asset_id="1",
            kind=VisualAssetKind.IMAGE,
            mime_type="image/jpeg",
            content_sha256="a" * 64,
            data_uri="file:///tmp/image.jpg",
        )


def test_bound_visual_asset_mime_mismatch_rejected():
    with pytest.raises(ValueError, match=r"data_uri MIME does not match mime_type"):
        BoundVisualAsset(
            asset_id="1",
            kind=VisualAssetKind.IMAGE,
            mime_type="image/jpeg",
            content_sha256="a" * 64,
            data_uri="data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==",
        )


def test_bound_visual_asset_malformed_base64_rejected():
    with pytest.raises(ValueError, match=r"data_uri must contain valid base64 image data"):
        BoundVisualAsset(
            asset_id="1",
            kind=VisualAssetKind.IMAGE,
            mime_type="image/jpeg",
            content_sha256="a" * 64,
            data_uri="data:image/jpeg;base64,not_valid_base64!@#$",
        )


def test_bound_visual_asset_empty_payload_rejected():
    with pytest.raises(ValueError, match=r"data_uri must contain non-empty base64 payload"):
        BoundVisualAsset(
            asset_id="1",
            kind=VisualAssetKind.IMAGE,
            mime_type="image/jpeg",
            content_sha256="a" * 64,
            data_uri="data:image/jpeg;base64,",
        )


def test_renderer_cannot_receive_remotely_backed_asset(renderer):
    # Proves remote URL cannot bypass to renderer
    with pytest.raises(ValueError, match=r"data_uri must begin with exact"):
        make_bound_asset(data_uri="https://attacker.com/malicious.jpg")
