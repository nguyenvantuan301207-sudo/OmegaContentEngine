"""Focused Brand Pass 2 render execution contracts."""

from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from omega.application.brand_asset_resolver import (
    BrandAssetResolutionError,
    BrandMediaKind,
    ResolvedBrandAsset,
)
from omega.application.visual_production_v2_service import VisualProductionV2Service
from omega.domain.channel_dna import BrandAssetReference


def _resolved(path: Path, content_hash: str, kind: BrandMediaKind, reference: str) -> ResolvedBrandAsset:
    return ResolvedBrandAsset(
        local_path=path,
        content_hash=content_hash,
        media_kind=kind,
        mime_type="image/png" if kind == BrandMediaKind.IMAGE else "video/mp4",
        reference=reference,
    )


def _service(tmp_path: Path, resolver=None) -> VisualProductionV2Service:
    return VisualProductionV2Service(
        asset_orchestrator=MagicMock(),
        output_root=tmp_path,
        brand_asset_resolver=resolver,
    )


def test_no_brand_assets_preserve_unbranded_behavior(tmp_path: Path) -> None:
    service = _service(tmp_path)
    assert service._resolve_visual_brand_assets(uuid4(), None, None, None) == (None, None, None)
    content = tmp_path / "content.mp4"
    assert service._brand_clip_paths([content], None, None) == [content]
    assert service._resolved_brand_asset_identity(None, None, None) is None


def test_verified_visual_assets_enter_deterministic_render_order_and_identity(tmp_path: Path) -> None:
    logo = _resolved(tmp_path / "logo.png", "a" * 64, BrandMediaKind.IMAGE, "brand://channel/logo.png")
    intro = _resolved(tmp_path / "intro.mp4", "b" * 64, BrandMediaKind.VIDEO, "brand://channel/intro.mp4")
    outro = _resolved(tmp_path / "outro.mp4", "c" * 64, BrandMediaKind.VIDEO, "brand://channel/outro.mp4")
    resolver = MagicMock()
    resolver.resolve_optional.side_effect = [logo, intro, outro]
    service = _service(tmp_path, resolver)
    references = (
        BrandAssetReference(reference=logo.reference, content_hash=logo.content_hash, mime_type=logo.mime_type),
        BrandAssetReference(reference=intro.reference, content_hash=intro.content_hash, mime_type=intro.mime_type),
        BrandAssetReference(reference=outro.reference, content_hash=outro.content_hash, mime_type=outro.mime_type),
    )

    assert service._resolve_visual_brand_assets(uuid4(), *references) == (logo, intro, outro)
    content = tmp_path / "content.mp4"
    assert service._brand_clip_paths([content], intro, outro) == [intro.local_path, content, outro.local_path]
    first = service._resolved_brand_asset_identity(logo, intro, outro)
    replay = service._resolved_brand_asset_identity(logo, intro, outro)
    changed = service._resolved_brand_asset_identity(
        logo.model_copy(update={"content_hash": "d" * 64}), intro, outro
    )
    assert first == replay
    assert first != changed


@pytest.mark.parametrize("role", ["logo", "intro", "outro"])
def test_configured_unresolved_visual_assets_fail_closed(tmp_path: Path, role: str) -> None:
    resolver = MagicMock()
    resolver.resolve_optional.side_effect = BrandAssetResolutionError("unresolved")
    service = _service(tmp_path, resolver)
    reference = BrandAssetReference(
        reference=f"brand://channel/{role}.bin",
        content_hash="a" * 64,
        mime_type="image/png" if role == "logo" else "video/mp4",
    )
    values = {
        "logo": (reference, None, None),
        "intro": (None, reference, None),
        "outro": (None, None, reference),
    }
    with pytest.raises(BrandAssetResolutionError, match="unresolved"):
        service._resolve_visual_brand_assets(uuid4(), *values[role])


@pytest.mark.parametrize("role", ["logo", "intro", "outro"])
def test_wrong_resolved_visual_media_kind_fails_closed(tmp_path: Path, role: str) -> None:
    expected_mime = "image/png" if role == "logo" else "video/mp4"
    wrong_kind = BrandMediaKind.VIDEO if role == "logo" else BrandMediaKind.IMAGE
    wrong = _resolved(tmp_path / "wrong.bin", "a" * 64, wrong_kind, f"brand://channel/{role}.bin")
    resolver = MagicMock()
    resolver.resolve_optional.return_value = wrong
    service = _service(tmp_path, resolver)
    reference = BrandAssetReference(
        reference=f"brand://channel/{role}.bin",
        content_hash="a" * 64,
        mime_type=expected_mime,
    )
    values = {
        "logo": (reference, None, None),
        "intro": (None, reference, None),
        "outro": (None, None, reference),
    }
    with pytest.raises(BrandAssetResolutionError, match="media kind mismatch"):
        service._resolve_visual_brand_assets(uuid4(), *values[role])
