import base64
import binascii
import re

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from omega.application.visual_direction import VisualAssetKind

_SHA256_REGEX = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_IMAGE_MIMES = ("image/jpeg", "image/png", "image/webp")


class BoundVisualAsset(BaseModel):
    model_config = ConfigDict(frozen=True)

    asset_id: str
    kind: VisualAssetKind
    mime_type: str
    content_sha256: str
    data_uri: str
    width: int | None = None
    height: int | None = None

    @field_validator("asset_id", "mime_type", "data_uri")
    @classmethod
    def _validate_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Field cannot be empty or whitespace")
        return v

    @field_validator("content_sha256")
    @classmethod
    def _validate_sha256(cls, v: str) -> str:
        if not _SHA256_REGEX.fullmatch(v):
            raise ValueError("content_sha256 must be exactly 64 lowercase hex characters")
        return v

    @model_validator(mode="after")
    def _validate_data_uri_and_mime(self) -> "BoundVisualAsset":
        if self.kind == VisualAssetKind.IMAGE:
            if self.mime_type not in _ALLOWED_IMAGE_MIMES:
                raise ValueError("Unsupported bound image MIME type")

            expected_prefix = f"data:{self.mime_type};base64,"
            if not self.data_uri.startswith(expected_prefix):
                if self.data_uri.startswith("data:"):
                    raise ValueError("data_uri MIME does not match mime_type")
                raise ValueError("data_uri must begin with exact data:{mime_type};base64, prefix")

            payload = self.data_uri[len(expected_prefix):]
            if not payload:
                raise ValueError("data_uri must contain non-empty base64 payload")

            try:
                base64.b64decode(payload, validate=True)
            except (binascii.Error, ValueError) as e:
                raise ValueError("data_uri must contain valid base64 image data") from e

        return self
