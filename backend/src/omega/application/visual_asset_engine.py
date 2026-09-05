from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, model_validator

from omega.application.visual_direction import VisualAssetKind, VisualAssetRequirement


class VisualAssetEngineError(ValueError):
    pass


class VisualAssetRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    scene_index: int
    kind: VisualAssetKind
    query: str
    purpose: str
    required: bool
    preferred_orientation: str | None = None
    preferred_width: int | None = None
    preferred_height: int | None = None

    @model_validator(mode="after")
    def validate_invariants(self) -> "VisualAssetRequest":
        if self.scene_index < 0:
            raise ValueError("scene_index must be >= 0")
        if not self.query.strip():
            raise ValueError("query must be non-empty")
        if not self.purpose.strip():
            raise ValueError("purpose must be non-empty")

        clean = self.query.strip().lower()
        if clean in ["placeholder", "generic"] or "abstract technology background" in clean:
            raise ValueError("Meaningless query for asset")

        return self


class ResolvedVisualAsset(BaseModel):
    model_config = ConfigDict(frozen=True)
    asset_id: str
    kind: VisualAssetKind
    provider: str
    source_url: str | None
    source_page_url: str | None
    local_path: Path
    mime_type: str
    width: int | None
    height: int | None
    duration_seconds: float | None
    content_sha256: str
    license_name: str | None
    license_url: str | None
    attribution_text: str | None
    query: str
    metadata: dict[str, Any]


class VisualAssetCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)
    provider_id: str
    kind: VisualAssetKind
    provider: str
    source_url: str | None
    source_page_url: str | None
    mime_type: str
    width: int | None
    height: int | None
    duration_seconds: float | None
    license_name: str | None
    license_url: str | None
    attribution_text: str | None
    metadata: dict[str, Any]


class VisualAssetProvider(Protocol):
    @property
    def provider_name(self) -> str: ...

    async def search(
        self,
        request: VisualAssetRequest,
        limit: int = 5,
    ) -> list[VisualAssetCandidate]: ...

    async def fetch(
        self,
        candidate: VisualAssetCandidate,
    ) -> ResolvedVisualAsset: ...


class VisualAssetEngine:
    def _is_meaningful_query(self, query: str | None) -> bool:
        if not query:
            return False
        clean = query.strip().lower()
        if not clean:
            return False
        if clean in ["placeholder", "generic"]:
            return False
        return "abstract technology background" not in clean

    def build_request(self, scene_index: int, requirement: VisualAssetRequirement) -> VisualAssetRequest | None:
        is_meaningful = self._is_meaningful_query(requirement.query_hint)

        if not is_meaningful:
            if requirement.required:
                raise VisualAssetEngineError("Meaningless query for required asset")
            return None

        # mypy type narrowing
        assert requirement.query_hint is not None
        clean_query = requirement.query_hint.strip()

        try:
            return VisualAssetRequest(
                scene_index=scene_index,
                kind=requirement.kind,
                query=clean_query,
                purpose=requirement.purpose,
                required=requirement.required,
                preferred_orientation=None,
                preferred_width=None,
                preferred_height=None,
            )
        except ValueError as e:
            raise VisualAssetEngineError(str(e)) from e

    def select_candidate(self, request: VisualAssetRequest, candidates: list[VisualAssetCandidate]) -> VisualAssetCandidate | None:
        valid_candidates = []
        for i, c in enumerate(candidates):
            if c.kind != request.kind:
                continue
            if not c.provider_id or not c.provider_id.strip():
                continue
            if not c.provider or not c.provider.strip():
                continue
            if not c.source_url and not c.source_page_url:
                continue
            valid_candidates.append((i, c))

        if not valid_candidates:
            return None

        def _score(item: tuple[int, VisualAssetCandidate]) -> tuple:
            i, c = item
            valid_dims = bool(c.width and c.height and c.width > 0 and c.height > 0)
            # Use strict type cast to calm the typechecker down if it thinks it might be None
            area = (int(c.width) * int(c.height)) if valid_dims and c.width is not None and c.height is not None else 0

            if request.kind == VisualAssetKind.IMAGE:
                return (
                    not valid_dims,
                    -area,
                    i,
                    c.provider_id,
                    c.provider,
                    c.source_page_url or "",
                    c.source_url or "",
                )
            else:
                valid_duration = bool(c.duration_seconds and c.duration_seconds > 0)
                return (
                    not valid_duration,
                    not valid_dims,
                    -area,
                    i,
                    c.provider_id,
                    c.provider,
                    c.source_page_url or "",
                    c.source_url or "",
                )

        valid_candidates.sort(key=_score)
        return valid_candidates[0][1]
