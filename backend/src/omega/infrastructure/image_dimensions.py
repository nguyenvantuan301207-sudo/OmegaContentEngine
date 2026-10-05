"""Probe image headers only; this does not validate or decode pixel data."""

import zlib


class ImageDimensionError(ValueError):
    """Image headers cannot establish positive physical dimensions."""


def _invalid() -> ImageDimensionError:
    return ImageDimensionError("Invalid or unsupported image dimension header")


def _jpeg(content: bytes) -> tuple[int, int]:
    if not content.startswith(b"\xff\xd8"):
        raise _invalid()
    offset = 2
    sof_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while offset < len(content):
        if content[offset] != 0xFF:
            raise _invalid()
        while offset < len(content) and content[offset] == 0xFF:
            offset += 1
        if offset >= len(content):
            raise _invalid()
        marker = content[offset]
        offset += 1
        if marker in (0x00, 0xD8, 0xD9, 0xDA):
            raise _invalid()
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(content):
            raise _invalid()
        length = int.from_bytes(content[offset:offset + 2], "big")
        if length < 2 or offset + length > len(content):
            raise _invalid()
        if marker in sof_markers:
            if length < 8:
                raise _invalid()
            components = content[offset + 7]
            if components == 0 or length != 8 + 3 * components:
                raise _invalid()
            return (
                int.from_bytes(content[offset + 5:offset + 7], "big"),
                int.from_bytes(content[offset + 3:offset + 5], "big"),
            )
        offset += length
    raise _invalid()


def _png(content: bytes) -> tuple[int, int]:
    if (len(content) < 33 or not content.startswith(b"\x89PNG\r\n\x1a\n")
            or content[8:16] != b"\x00\x00\x00\rIHDR"):
        raise _invalid()
    if zlib.crc32(content[12:29]) != int.from_bytes(content[29:33], "big"):
        raise _invalid()
    return int.from_bytes(content[16:20], "big"), int.from_bytes(content[20:24], "big")


def _webp(content: bytes) -> tuple[int, int]:
    if len(content) < 20 or content[:4] != b"RIFF" or content[8:12] != b"WEBP":
        raise _invalid()
    end = int.from_bytes(content[4:8], "little") + 8
    if end != len(content):
        raise _invalid()
    offset = 12
    dimensions = None
    while offset < end:
        if offset + 8 > end:
            raise _invalid()
        kind = content[offset:offset + 4]
        size = int.from_bytes(content[offset + 4:offset + 8], "little")
        start = offset + 8
        offset = start + size + (size & 1)
        if offset > end:
            raise _invalid()
        if dimensions is not None:
            continue
        data = content[start:start + min(size, 10)]
        if kind == b"VP8 ":
            if size < 10 or data[0] & 1 or data[3:6] != b"\x9d\x01\x2a":
                raise _invalid()
            dimensions = (int.from_bytes(data[6:8], "little") & 0x3FFF,
                          int.from_bytes(data[8:10], "little") & 0x3FFF)
        elif kind == b"VP8L":
            if size < 5 or data[0] != 0x2F:
                raise _invalid()
            bits = int.from_bytes(data[1:5], "little")
            if bits >> 29:
                raise _invalid()
            dimensions = ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
        elif kind == b"VP8X":
            if size != 10 or data[0] & 0xC1 or data[1:4] != b"\x00\x00\x00":
                raise _invalid()
            dimensions = (int.from_bytes(data[4:7], "little") + 1,
                          int.from_bytes(data[7:10], "little") + 1)
    if dimensions is None:
        raise _invalid()
    return dimensions


def probe_image_dimensions(content: bytes, mime_type: str) -> tuple[int, int]:
    """Return physical header dimensions, failing closed on unsupported headers."""
    probes = {"image/jpeg": _jpeg, "image/png": _png, "image/webp": _webp}
    probe = probes.get(mime_type)
    if probe is None:
        raise _invalid()
    width, height = probe(content)
    if width <= 0 or height <= 0:
        raise _invalid()
    return width, height
