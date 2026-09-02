import base64
import hashlib
import re

from omega.application.visual_asset_binding import BoundVisualAsset
from omega.application.visual_asset_engine import ResolvedVisualAsset
from omega.application.visual_direction import VisualAssetKind

_SHA256_REGEX = re.compile(r"^[0-9a-f]{64}$")
_MAX_IMAGE_SIZE = 25 * 1024 * 1024  # 25 MiB ceiling matching Pexels


class VisualAssetMaterializerError(Exception):
    pass


class VisualAssetMaterializer:
    @staticmethod
    def materialize(resolved: ResolvedVisualAsset) -> BoundVisualAsset:
        # 1. Kind validation
        if resolved.kind != VisualAssetKind.IMAGE:
            raise VisualAssetMaterializerError("Unsupported asset kind")

        # 2. MIME validation
        allowed_mimes = ("image/jpeg", "image/png", "image/webp")
        if resolved.mime_type not in allowed_mimes:
            raise VisualAssetMaterializerError("Unsupported image MIME type")

        # 3. Content SHA256 string validation
        if not resolved.content_sha256 or not _SHA256_REGEX.fullmatch(resolved.content_sha256):
            raise VisualAssetMaterializerError("Invalid content_sha256 format")

        # 4. File existence & regular file check
        path = resolved.local_path
        try:
            st = path.stat()
        except (OSError, FileNotFoundError) as e:
            raise VisualAssetMaterializerError(f"Image file cannot be accessed: {e}") from e

        try:
            is_file = path.is_file()
        except OSError as e:
            raise VisualAssetMaterializerError(f"Image path is not a file: {e}") from e

        if not is_file:
            raise VisualAssetMaterializerError("Image path is not a regular file")

        # 5. Size gate using stat
        size = st.st_size
        if size <= 0:
            raise VisualAssetMaterializerError("Image file is empty")
        if size > _MAX_IMAGE_SIZE:
            raise VisualAssetMaterializerError(f"Image file size {size} exceeds 25 MiB ceiling")

        # 6. Read bytes
        try:
            content = path.read_bytes()
        except OSError as e:
            raise VisualAssetMaterializerError(f"Failed to read image file: {e}") from e

        # 7. Hash check
        actual_hash = hashlib.sha256(content).hexdigest()
        if actual_hash != resolved.content_sha256:
            raise VisualAssetMaterializerError("Image SHA256 mismatch")

        # 8. Magic-byte signature consistency
        if resolved.mime_type == "image/png":
            if not content.startswith(b"\x89PNG\r\n\x1a\n"):
                raise VisualAssetMaterializerError("Image content does not match MIME type")
        elif resolved.mime_type == "image/jpeg":
            if not content.startswith(b"\xff\xd8\xff"):
                raise VisualAssetMaterializerError("Image content does not match MIME type")
        elif resolved.mime_type == "image/webp" and (
            len(content) < 12 or content[0:4] != b"RIFF" or content[8:12] != b"WEBP"
        ):
            raise VisualAssetMaterializerError("Image content does not match MIME type")

        # 9. Deterministic Data URI construction
        b64_str = base64.b64encode(content).decode("ascii")
        data_uri = f"data:{resolved.mime_type};base64,{b64_str}"

        return BoundVisualAsset(
            asset_id=resolved.asset_id,
            kind=resolved.kind,
            mime_type=resolved.mime_type,
            content_sha256=resolved.content_sha256,
            data_uri=data_uri,
            width=resolved.width,
            height=resolved.height,
        )
