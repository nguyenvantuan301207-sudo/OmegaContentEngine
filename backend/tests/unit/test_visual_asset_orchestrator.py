from pathlib import Path

import pytest

from omega.application.visual_asset_engine import (
    ResolvedVisualAsset,
    VisualAssetCandidate,
    VisualAssetEngine,
    VisualAssetKind,
    VisualAssetRequest,
)
from omega.application.visual_asset_orchestrator import (
    VisualAssetOrchestrator,
    VisualAssetOrchestratorError,
)


class MockProvider:
    def __init__(
        self,
        name: str,
        candidates: list[VisualAssetCandidate],
        search_error: Exception | None = None,
        fetch_error: Exception | None = None,
    ):
        self._name = name
        self.candidates = candidates
        self.search_error = search_error
        self.fetch_error = fetch_error
        self.searches = 0
        self.fetches = 0
        self.fetched_candidates: list[VisualAssetCandidate] = []

    @property
    def provider_name(self) -> str:
        return self._name

    async def search(
        self, request: VisualAssetRequest, limit: int = 5
    ) -> list[VisualAssetCandidate]:
        self.searches += 1
        if self.search_error:
            raise self.search_error
        return self.candidates

    async def fetch(self, candidate: VisualAssetCandidate) -> ResolvedVisualAsset:
        self.fetches += 1
        self.fetched_candidates.append(candidate)
        if self.fetch_error:
            raise self.fetch_error

        return ResolvedVisualAsset(
            asset_id=f"resolved_{candidate.provider_id}",
            kind=candidate.kind,
            provider=self._name,
            source_url=candidate.source_url,
            source_page_url=candidate.source_page_url,
            local_path=Path(f"/tmp/{candidate.provider_id}.bin"),
            mime_type=candidate.mime_type,
            width=candidate.width,
            height=candidate.height,
            duration_seconds=candidate.duration_seconds,
            content_sha256="0" * 64,
            license_name=candidate.license_name,
            license_url=candidate.license_url,
            attribution_text=candidate.attribution_text,
            query="test query",
            metadata=candidate.metadata,
        )


@pytest.fixture
def engine():
    return VisualAssetEngine()


def make_req(kind: VisualAssetKind) -> VisualAssetRequest:
    return VisualAssetRequest(
        scene_index=0,
        kind=kind,
        query="test query",
        purpose="test",
        required=True,
    )


def make_candidate(
    provider: str, provider_id: str, source_url: str, kind: VisualAssetKind
) -> VisualAssetCandidate:
    return VisualAssetCandidate(
        provider_id=provider_id,
        kind=kind,
        provider=provider,
        source_url=source_url,
        source_page_url=f"https://{provider}.test/page/{provider_id}",
        mime_type="image/jpeg" if kind == VisualAssetKind.IMAGE else "video/mp4",
        width=1920,
        height=1080,
        duration_seconds=10.0 if kind != VisualAssetKind.IMAGE else None,
        license_name="Test License",
        license_url="https://test.license",
        attribution_text="Test Attribution",
        metadata={"custom_key": "custom_val"},
    )


def test_no_providers_rejected(engine):
    with pytest.raises(VisualAssetOrchestratorError, match=r"^No providers registered$"):
        VisualAssetOrchestrator(engine, [])


def test_blank_provider_name_rejected(engine):
    p_blank = MockProvider("   ", [])
    with pytest.raises(VisualAssetOrchestratorError, match=r"^Provider name cannot be blank$"):
        VisualAssetOrchestrator(engine, [p_blank])


def test_duplicate_provider_name_rejected(engine):
    p1 = MockProvider("pexels", [])
    p2 = MockProvider("pexels", [])
    with pytest.raises(VisualAssetOrchestratorError, match=r"^Duplicate provider name: pexels$"):
        VisualAssetOrchestrator(engine, [p1, p2])


@pytest.mark.asyncio
async def test_successful_image_resolution(engine):
    c1 = make_candidate("provider_a", "id_1", "https://a.test/1.jpg", VisualAssetKind.IMAGE)
    p = MockProvider("provider_a", [c1])
    orch = VisualAssetOrchestrator(engine, [p])

    req = make_req(VisualAssetKind.IMAGE)
    res = await orch.resolve(req)

    assert res.kind == VisualAssetKind.IMAGE
    assert res.provider == "provider_a"
    assert res.source_url == "https://a.test/1.jpg"
    assert res.source_page_url == "https://provider_a.test/page/id_1"
    assert res.license_name == "Test License"
    assert res.license_url == "https://test.license"
    assert res.attribution_text == "Test Attribution"
    assert res.metadata == {"custom_key": "custom_val"}
    assert res.content_sha256 == "0" * 64
    assert p.searches == 1
    assert p.fetches == 1


@pytest.mark.asyncio
async def test_successful_broll_resolution(engine):
    c1 = make_candidate("provider_a", "id_2", "https://a.test/2.mp4", VisualAssetKind.BROLL)
    p = MockProvider("provider_a", [c1])
    orch = VisualAssetOrchestrator(engine, [p])

    req = make_req(VisualAssetKind.BROLL)
    res = await orch.resolve(req)

    assert res.kind == VisualAssetKind.BROLL
    assert res.provider == "provider_a"
    assert res.source_url == "https://a.test/2.mp4"
    assert res.mime_type == "video/mp4"
    assert p.searches == 1
    assert p.fetches == 1


@pytest.mark.asyncio
async def test_no_candidates(engine):
    p = MockProvider("provider_a", [])
    orch = VisualAssetOrchestrator(engine, [p])

    req = make_req(VisualAssetKind.IMAGE)
    with pytest.raises(VisualAssetOrchestratorError, match=r"^No candidates found$"):
        await orch.resolve(req)

    assert p.searches == 1
    assert p.fetches == 0


@pytest.mark.asyncio
async def test_provider_search_failure_fail_closed(engine):
    p_fail = MockProvider("provider_a", [], search_error=RuntimeError("Secret network failure details"))
    p_later = MockProvider("provider_b", [])
    orch = VisualAssetOrchestrator(engine, [p_fail, p_later])

    req = make_req(VisualAssetKind.IMAGE)
    with pytest.raises(VisualAssetOrchestratorError) as exc_info:
        await orch.resolve(req)

    assert str(exc_info.value) == "Provider search failed"
    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert p_fail.searches == 1
    assert p_later.searches == 0
    assert p_fail.fetches == 0
    assert p_later.fetches == 0


@pytest.mark.asyncio
async def test_provider_candidate_identity_mismatch(engine):
    c_mismatched = make_candidate(
        "provider_b", "id_1", "https://b.test/1.jpg", VisualAssetKind.IMAGE
    )
    p_a = MockProvider("provider_a", [c_mismatched])
    p_b = MockProvider("provider_b", [])

    orch = VisualAssetOrchestrator(engine, [p_a, p_b])

    req = make_req(VisualAssetKind.IMAGE)
    with pytest.raises(VisualAssetOrchestratorError) as exc_info:
        await orch.resolve(req)

    assert str(exc_info.value) == "Provider candidate identity mismatch"
    assert exc_info.value.__cause__ is None
    assert p_a.searches == 1
    assert p_b.searches == 0
    assert p_a.fetches == 0
    assert p_b.fetches == 0


@pytest.mark.asyncio
async def test_provider_fetch_failure(engine):
    c1 = make_candidate("provider_a", "id_1", "https://a.test/1.jpg", VisualAssetKind.IMAGE)
    p = MockProvider("provider_a", [c1], fetch_error=RuntimeError("Internal download leak"))
    orch = VisualAssetOrchestrator(engine, [p])

    req = make_req(VisualAssetKind.IMAGE)
    with pytest.raises(VisualAssetOrchestratorError) as exc_info:
        await orch.resolve(req)

    assert str(exc_info.value) == "Provider fetch failed"
    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert p.searches == 1
    assert p.fetches == 1


@pytest.mark.asyncio
async def test_deterministic_provider_ordering(engine):
    search_order = []

    class OrderTrackingProvider(MockProvider):
        async def search(
            self, request: VisualAssetRequest, limit: int = 5
        ) -> list[VisualAssetCandidate]:
            search_order.append(self._name)
            return await super().search(request, limit)

    p_z = OrderTrackingProvider("z_provider", [make_candidate("z_provider", "z1", "https://z.test/1.jpg", VisualAssetKind.IMAGE)])
    p_a = OrderTrackingProvider("a_provider", [make_candidate("a_provider", "a1", "https://a.test/1.jpg", VisualAssetKind.IMAGE)])
    p_m = OrderTrackingProvider("m_provider", [make_candidate("m_provider", "m1", "https://m.test/1.jpg", VisualAssetKind.IMAGE)])

    orch = VisualAssetOrchestrator(engine, [p_z, p_a, p_m])
    req = make_req(VisualAssetKind.IMAGE)
    await orch.resolve(req)

    assert search_order == ["a_provider", "m_provider", "z_provider"]


@pytest.mark.asyncio
async def test_deterministic_candidate_selection(engine):
    c_a = make_candidate("provider_a", "id_z", "https://a.test/z.jpg", VisualAssetKind.IMAGE)
    c_b = make_candidate("provider_b", "id_a", "https://b.test/a.jpg", VisualAssetKind.IMAGE)

    p_a = MockProvider("provider_a", [c_a])
    p_b = MockProvider("provider_b", [c_b])

    orch1 = VisualAssetOrchestrator(engine, [p_a, p_b])
    orch2 = VisualAssetOrchestrator(engine, [p_b, p_a])

    req = make_req(VisualAssetKind.IMAGE)
    res1 = await orch1.resolve(req)
    res2 = await orch2.resolve(req)

    assert res1.source_url == res2.source_url == "https://b.test/a.jpg"
    assert res1.provider == res2.provider == "provider_b"


@pytest.mark.asyncio
async def test_correct_provider_fetch(engine):
    c_a = make_candidate("provider_a", "id_2", "https://a.test/2.jpg", VisualAssetKind.IMAGE)
    c_b = make_candidate("provider_b", "id_1", "https://b.test/1.jpg", VisualAssetKind.IMAGE)

    p_a = MockProvider("provider_a", [c_a])
    p_b = MockProvider("provider_b", [c_b])

    orch = VisualAssetOrchestrator(engine, [p_a, p_b])
    req = make_req(VisualAssetKind.IMAGE)
    res = await orch.resolve(req)

    assert res.provider == "provider_b"
    assert p_b.fetches == 1
    assert p_a.fetches == 0
    assert len(p_b.fetched_candidates) == 1
    assert p_b.fetched_candidates[0].provider_id == "id_1"


@pytest.mark.asyncio
async def test_candidate_names_unregistered_fails(engine):
    c_unknown = make_candidate("unregistered_provider", "id_1", "https://u.test/1.jpg", VisualAssetKind.IMAGE)
    p = MockProvider("registered_provider", [c_unknown])

    # Bypass search validation to simulate an edge-case candidate if needed
    orch = VisualAssetOrchestrator(engine, [p])
    req = make_req(VisualAssetKind.IMAGE)

    # Note: Search identity validation will catch it first now!
    with pytest.raises(VisualAssetOrchestratorError, match=r"^Provider candidate identity mismatch$"):
        await orch.resolve(req)

    assert p.searches == 1
    assert p.fetches == 0
