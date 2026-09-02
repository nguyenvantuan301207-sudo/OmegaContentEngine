import pytest

from omega.application.visual_asset_engine import (
    VisualAssetCandidate,
    VisualAssetEngine,
    VisualAssetEngineError,
    VisualAssetRequest,
)
from omega.application.visual_direction import VisualAssetKind, VisualAssetRequirement


def test_build_request_success():
    engine = VisualAssetEngine()
    req = VisualAssetRequirement(
        kind=VisualAssetKind.IMAGE,
        query_hint="modern data center servers",
        purpose="Primary visual for image explainer",
        required=True
    )
    request = engine.build_request(1, req)

    assert request is not None
    assert request.scene_index == 1
    assert request.kind == VisualAssetKind.IMAGE
    assert request.query == "modern data center servers"
    assert request.purpose == "Primary visual for image explainer"
    assert request.required is True


def test_build_request_rejections():
    engine = VisualAssetEngine()

    req1 = VisualAssetRequirement(
        kind=VisualAssetKind.IMAGE,
        query_hint="placeholder",
        purpose="purpose",
        required=True
    )
    with pytest.raises(VisualAssetEngineError, match="Meaningless"):
        engine.build_request(1, req1)

    req2 = VisualAssetRequirement(
        kind=VisualAssetKind.IMAGE,
        query_hint="   ",
        purpose="purpose",
        required=True
    )
    with pytest.raises(VisualAssetEngineError, match="Meaningless"):
        engine.build_request(1, req2)

    req3 = VisualAssetRequirement(
        kind=VisualAssetKind.IMAGE,
        query_hint="generic",
        purpose="purpose",
        required=False
    )
    assert engine.build_request(1, req3) is None

    req4 = VisualAssetRequirement(
        kind=VisualAssetKind.IMAGE,
        query_hint="valid",
        purpose="  ",
        required=True
    )
    with pytest.raises(VisualAssetEngineError, match="purpose must be non-empty"):
        engine.build_request(1, req4)


def test_visual_asset_request_invariants():
    with pytest.raises(ValueError, match="scene_index"):
        VisualAssetRequest(scene_index=-1, kind=VisualAssetKind.IMAGE, query="test", purpose="test", required=True)

    with pytest.raises(ValueError, match="query"):
        VisualAssetRequest(scene_index=1, kind=VisualAssetKind.IMAGE, query="   ", purpose="test", required=True)

    with pytest.raises(ValueError, match="purpose"):
        VisualAssetRequest(scene_index=1, kind=VisualAssetKind.IMAGE, query="test", purpose="   ", required=True)

    with pytest.raises(ValueError, match="Meaningless"):
        VisualAssetRequest(scene_index=1, kind=VisualAssetKind.IMAGE, query="placeholder", purpose="test", required=True)


def test_deterministic_selection():
    engine = VisualAssetEngine()
    req = VisualAssetRequest(
        scene_index=1,
        kind=VisualAssetKind.IMAGE,
        query="test",
        purpose="test",
        required=True
    )

    c1 = VisualAssetCandidate(
        provider_id="B",
        kind=VisualAssetKind.IMAGE,
        provider="test",
        source_url="http://test",
        source_page_url=None,
        mime_type="image/jpeg",
        width=100,
        height=100,
        duration_seconds=None,
        license_name=None,
        license_url=None,
        attribution_text=None,
        metadata={}
    )
    c2 = VisualAssetCandidate(
        provider_id="A",
        kind=VisualAssetKind.IMAGE,
        provider="test",
        source_url="http://test",
        source_page_url=None,
        mime_type="image/jpeg",
        width=100,
        height=100,
        duration_seconds=None,
        license_name=None,
        license_url=None,
        attribution_text=None,
        metadata={}
    )
    c3 = VisualAssetCandidate(
        provider_id="C",
        kind=VisualAssetKind.VIDEO,
        provider="test",
        source_url="http://test",
        source_page_url=None,
        mime_type="video/mp4",
        width=100,
        height=100,
        duration_seconds=None,
        license_name=None,
        license_url=None,
        attribution_text=None,
        metadata={}
    )

    selected1 = engine.select_candidate(req, [c1, c2, c3])
    assert selected1 is not None
    assert selected1.provider_id == "A"

    selected2 = engine.select_candidate(req, [c3, c2, c1])
    assert selected2 is not None
    assert selected2.provider_id == "A"


def test_candidate_provenance_rejections():
    engine = VisualAssetEngine()
    req = VisualAssetRequest(
        scene_index=1,
        kind=VisualAssetKind.IMAGE,
        query="test",
        purpose="test",
        required=True
    )

    def base_candidate():
        return dict(
            provider_id="A",
            kind=VisualAssetKind.IMAGE,
            provider="test_provider",
            source_url="http://test",
            source_page_url=None,
            mime_type="image/jpeg",
            width=100,
            height=100,
            duration_seconds=None,
            license_name=None,
            license_url=None,
            attribution_text=None,
            metadata={}
        )

    c_empty_provider_id = VisualAssetCandidate(**{**base_candidate(), "provider_id": "   "})
    c_empty_provider = VisualAssetCandidate(**{**base_candidate(), "provider": "   "})
    c_no_sources = VisualAssetCandidate(**{**base_candidate(), "source_url": None, "source_page_url": None})

    assert engine.select_candidate(req, [c_empty_provider_id]) is None
    assert engine.select_candidate(req, [c_empty_provider]) is None
    assert engine.select_candidate(req, [c_no_sources]) is None


def test_candidate_tie_breaking():
    engine = VisualAssetEngine()
    req = VisualAssetRequest(
        scene_index=1,
        kind=VisualAssetKind.IMAGE,
        query="test",
        purpose="test",
        required=True
    )

    def c(provider_id: str, provider: str):
        return VisualAssetCandidate(
            provider_id=provider_id,
            kind=VisualAssetKind.IMAGE,
            provider=provider,
            source_url="http://test",
            source_page_url=None,
            mime_type="image/jpeg",
            width=100,
            height=100,
            duration_seconds=None,
            license_name=None,
            license_url=None,
            attribution_text=None,
            metadata={}
        )

    # Same provider_id, different provider name
    c1 = c("ID1", "ProvB")
    c2 = c("ID1", "ProvA")

    # Should deterministically pick c2 based on provider name tiebreak
    assert engine.select_candidate(req, [c1, c2]) == c2
    assert engine.select_candidate(req, [c2, c1]) == c2
