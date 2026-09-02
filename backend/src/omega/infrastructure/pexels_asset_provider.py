from typing import Any

import httpx

from omega.application.visual_asset_engine import (
    ResolvedVisualAsset,
    VisualAssetCandidate,
    VisualAssetRequest,
)
from omega.application.visual_direction import VisualAssetKind
from omega.infrastructure.visual_asset_cache import VisualAssetCache


class PexelsAssetProviderError(Exception):
    pass


class PexelsAssetProvider:
    def __init__(
        self,
        api_key: str,
        cache: VisualAssetCache,
        client: httpx.AsyncClient | None = None,
    ):
        if not api_key or not api_key.strip():
            raise PexelsAssetProviderError("API key cannot be empty")
        self._api_key = api_key.strip()
        self._cache = cache
        self._client = client
        self._owns_client = client is None

    @property
    def provider_name(self) -> str:
        return "pexels"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=20.0)
        return self._client

    async def close(self):
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _http_get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        client = await self._get_client()
        headers = {"Authorization": self._api_key}

        try:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                limit = e.response.headers.get("X-Ratelimit-Limit", "unknown")
                remaining = e.response.headers.get("X-Ratelimit-Remaining", "unknown")
                reset = e.response.headers.get("X-Ratelimit-Reset", "unknown")
                msg = f"Rate limited by Pexels API (Limit: {limit}, Remaining: {remaining}, Reset: {reset})"
                raise PexelsAssetProviderError(msg) from None
            raise PexelsAssetProviderError(f"Pexels API HTTP {e.response.status_code}") from None
        except httpx.RequestError as e:
            raise PexelsAssetProviderError(f"Network error: {type(e).__name__}") from None

        try:
            data = response.json()
        except ValueError as e:
            raise PexelsAssetProviderError("Invalid JSON from Pexels API") from e

        if not isinstance(data, dict):
            raise PexelsAssetProviderError("Pexels API response is not a JSON object")

        return data

    def _select_image_variant(self, src: dict[str, Any]) -> str | None:
        return src.get("original") or src.get("landscape") or src.get("large2x") or src.get("large") or src.get("medium")

    def _select_video_variant(self, files: list[dict[str, Any]]) -> dict[str, Any] | None:
        valid_files = []
        for f in files:
            if f.get("file_type") != "video/mp4":
                continue
            link = f.get("link")
            w = f.get("width")
            h = f.get("height")
            if not link or w is None or h is None:
                continue
            valid_files.append(f)

        if not valid_files:
            return None

        def score_file(f: dict[str, Any]) -> tuple[int, int, int, int]:
            w = f["width"]
            h = f["height"]

            # 1. Landscape orientation
            is_landscape = 1 if w > h else 0

            # 2. Resolution closest to / at least 1920x1080. Avoid 4k if 1080p exists.
            target_w, target_h = 1920, 1080
            if w >= target_w and h >= target_h:
                resolution_score = -(abs(w - target_w) + abs(h - target_h)) # Closer to 1080p is better
            else:
                resolution_score = -1000000 + (w * h) # If smaller, bigger is better

            # Tie-break
            return (is_landscape, resolution_score, w, f.get("id", 0))

        valid_files.sort(key=score_file, reverse=True)
        return valid_files[0]

    async def search(
        self,
        request: VisualAssetRequest,
        limit: int = 5,
    ) -> list[VisualAssetCandidate]:

        if request.kind not in (VisualAssetKind.IMAGE, VisualAssetKind.VIDEO, VisualAssetKind.BROLL):
            raise PexelsAssetProviderError(f"Unsupported kind: {request.kind}")

        if not (1 <= limit <= 20):
            raise PexelsAssetProviderError(f"Search limit {limit} must be between 1 and 20")

        params: dict[str, Any] = {"query": request.query, "per_page": limit}
        if request.preferred_orientation in ("landscape", "portrait", "square"):
            params["orientation"] = request.preferred_orientation

        candidates = []

        if request.kind == VisualAssetKind.IMAGE:
            data = await self._http_get("https://api.pexels.com/v1/search", params=params)
            photos = data.get("photos", [])
            if not isinstance(photos, list):
                raise PexelsAssetProviderError("Invalid response shape: 'photos' is not a list")

            for photo in photos:
                if not isinstance(photo, dict):
                    continue
                pid = photo.get("id")
                if pid is None:
                    continue
                src = photo.get("src", {})
                source_url = self._select_image_variant(src)
                if not source_url:
                    continue

                photographer = photo.get("photographer")
                attr = f"Photo by {photographer} on Pexels" if photographer else None

                candidates.append(VisualAssetCandidate(
                    provider_id=str(pid),
                    kind=VisualAssetKind.IMAGE,
                    provider="pexels",
                    source_url=source_url,
                    source_page_url=photo.get("url"),
                    mime_type="image/jpeg",
                    width=photo.get("width"),
                    height=photo.get("height"),
                    duration_seconds=None,
                    license_name="Pexels License",
                    license_url="https://www.pexels.com/license/",
                    attribution_text=attr,
                    metadata={
                        "photographer": photographer,
                        "photographer_url": photo.get("photographer_url"),
                        "search_query": request.query,
                    }
                ))

        else: # VIDEO / BROLL
            data = await self._http_get("https://api.pexels.com/v1/videos/search", params=params)
            videos = data.get("videos", [])
            if not isinstance(videos, list):
                raise PexelsAssetProviderError("Invalid response shape: 'videos' is not a list")

            for video in videos:
                if not isinstance(video, dict):
                    continue
                vid = video.get("id")
                if vid is None:
                    continue
                vfiles = video.get("video_files", [])
                if not isinstance(vfiles, list):
                    continue
                variant = self._select_video_variant(vfiles)
                if not variant:
                    continue

                user = video.get("user", {})
                creator = user.get("name")
                attr = f"Video by {creator} on Pexels" if creator else None

                candidates.append(VisualAssetCandidate(
                    provider_id=str(vid),
                    kind=request.kind,
                    provider="pexels",
                    source_url=variant["link"],
                    source_page_url=video.get("url"),
                    mime_type="video/mp4",
                    width=variant["width"],
                    height=variant["height"],
                    duration_seconds=video.get("duration"),
                    license_name="Pexels License",
                    license_url="https://www.pexels.com/license/",
                    attribution_text=attr,
                    metadata={
                        "creator": creator,
                        "creator_url": user.get("url"),
                        "search_query": request.query,
                    }
                ))

        return candidates

    async def fetch(
        self,
        candidate: VisualAssetCandidate,
    ) -> ResolvedVisualAsset:
        if candidate.provider != "pexels":
            raise PexelsAssetProviderError(f"Unsupported provider: {candidate.provider}")
        if not candidate.source_url:
            raise PexelsAssetProviderError("Candidate source_url is missing")
        if candidate.kind not in (VisualAssetKind.IMAGE, VisualAssetKind.VIDEO, VisualAssetKind.BROLL):
            raise PexelsAssetProviderError(f"Unsupported kind: {candidate.kind}")

        source_url_obj = httpx.URL(candidate.source_url)
        if source_url_obj.scheme != "https":
            raise PexelsAssetProviderError(f"Final URL scheme must be https: {candidate.source_url}")

        if candidate.kind == VisualAssetKind.IMAGE:
            if candidate.mime_type not in ("image/jpeg", "image/png", "image/webp"):
                raise PexelsAssetProviderError(f"Unsupported image candidate MIME: {candidate.mime_type}")
        else:
            if candidate.mime_type != "video/mp4":
                raise PexelsAssetProviderError(f"Unsupported video candidate MIME: {candidate.mime_type}")

        search_query = candidate.metadata.get("search_query")
        if not isinstance(search_query, str):
            raise PexelsAssetProviderError("Missing or invalid search_query in metadata")
        search_query = search_query.strip()
        if not search_query:
            raise PexelsAssetProviderError("Empty search_query in metadata")

        max_size = 25 * 1024 * 1024 if candidate.kind == VisualAssetKind.IMAGE else 150 * 1024 * 1024

        client = await self._get_client()
        buffer = bytearray()

        try:
            _ = httpx.URL(candidate.source_url)
        except httpx.InvalidURL as e:
            raise PexelsAssetProviderError("Invalid candidate source URL") from e

        current_url = candidate.source_url
        redirect_count = 0
        max_redirects = 5

        while True:
            try:
                async with client.stream("GET", current_url, follow_redirects=False, headers={"User-Agent": "Omega/1.0"}) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        if redirect_count >= max_redirects:
                            raise PexelsAssetProviderError("Redirect limit exceeded")

                        location = response.headers.get("Location")
                        if not location:
                            raise PexelsAssetProviderError("Missing Location in redirect")

                        try:
                            next_url = response.url.join(location)
                        except httpx.InvalidURL as e:
                            raise PexelsAssetProviderError("Invalid redirect Location") from e

                        if next_url.scheme != "https":
                            raise PexelsAssetProviderError("Redirected to non-https URL")

                        current_url = str(next_url)
                        redirect_count += 1
                        continue

                    response.raise_for_status()

                    content_length_str = response.headers.get("Content-Length")
                    if content_length_str:
                        try:
                            content_length = int(content_length_str)
                        except ValueError:
                            raise PexelsAssetProviderError("Malformed Content-Length") from None
                        if content_length < 0:
                            raise PexelsAssetProviderError("Malformed Content-Length")
                        if content_length > max_size:
                            raise PexelsAssetProviderError(f"Content-Length exceeds limit of {max_size} bytes")

                    content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
                    if candidate.kind == VisualAssetKind.IMAGE:
                        if content_type not in ("image/jpeg", "image/png", "image/webp"):
                            raise PexelsAssetProviderError(f"Invalid image Content-Type: {content_type}")
                    else:
                        if content_type != "video/mp4":
                            raise PexelsAssetProviderError(f"Invalid video Content-Type: {content_type}")

                    if content_type != candidate.mime_type:
                        raise PexelsAssetProviderError(f"Response MIME {content_type} does not match candidate MIME {candidate.mime_type}")

                    async for chunk in response.aiter_bytes():
                        buffer.extend(chunk)
                        if len(buffer) > max_size:
                            raise PexelsAssetProviderError(f"Received body exceeds limit of {max_size} bytes")

                    break

            except httpx.HTTPStatusError as e:
                raise PexelsAssetProviderError(f"Download HTTP {e.response.status_code}") from None
            except httpx.RequestError as e:
                raise PexelsAssetProviderError(f"Download network error: {type(e).__name__}") from None

        if not buffer:
            raise PexelsAssetProviderError("Empty response body")

        content = bytes(buffer)

        return self._cache.store(
            content=content,
            kind=candidate.kind,
            provider="pexels",
            mime_type=candidate.mime_type,
            query=search_query,
            source_url=candidate.source_url,
            source_page_url=candidate.source_page_url,
            width=candidate.width,
            height=candidate.height,
            duration_seconds=candidate.duration_seconds,
            license_name=candidate.license_name,
            license_url=candidate.license_url,
            attribution_text=candidate.attribution_text,
            metadata=candidate.metadata,
        )
