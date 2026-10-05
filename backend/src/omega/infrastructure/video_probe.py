"""Bounded, read-only ffprobe metadata for downloaded video bytes."""

import asyncio
import contextlib
import json
import math
import tempfile
from dataclasses import dataclass
from pathlib import Path

_TIMEOUT_SECONDS = 10
_STDOUT_LIMIT = 64 * 1024
_STDERR_LIMIT = 16 * 1024


class VideoProbeError(ValueError):
    """Physical video metadata cannot be established safely."""


@dataclass(frozen=True)
class PhysicalVideoMetadata:
    width: int
    height: int
    duration_seconds: float


def _parse_metadata(output: bytes) -> PhysicalVideoMetadata:
    try:
        data = json.loads(output)
        if not isinstance(data, dict):
            raise ValueError
        streams, container = data.get("streams"), data.get("format")
        if not isinstance(streams, list) or not streams or not isinstance(container, dict):
            raise ValueError
        if not isinstance(streams[0], dict):
            raise ValueError
        width, height = streams[0].get("width"), streams[0].get("height")
        if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
            raise ValueError
        duration = container.get("duration")
        if isinstance(duration, bool):
            raise ValueError
        duration = float(duration)
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError
        return PhysicalVideoMetadata(width, height, duration)
    except (ValueError, TypeError, OverflowError):
        raise VideoProbeError("Invalid physical video metadata") from None


async def _read_bounded(stream, limit: int, process) -> tuple[bytes, bool]:
    output = bytearray()
    exceeded = False
    while chunk := await stream.read(8192):
        if not exceeded:
            if len(output) + len(chunk) > limit:
                exceeded = True
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
            else:
                output.extend(chunk)
    return bytes(output), exceeded


async def _discard(stream) -> None:
    while await stream.read(8192):
        pass


async def _probe_path(path: Path) -> PhysicalVideoMetadata:
    process = None
    try:
        async with asyncio.timeout(_TIMEOUT_SECONDS):
            process = await asyncio.create_subprocess_exec(
                "ffprobe", "-v", "error", "-protocol_whitelist", "file",
                "-select_streams", "v:0", "-show_entries",
                "stream=width,height:format=duration", "-of", "json", str(path),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                limit=_STDOUT_LIMIT,
            )
            stdout, stderr, code = await asyncio.gather(
                _read_bounded(process.stdout, _STDOUT_LIMIT, process),
                _read_bounded(process.stderr, _STDERR_LIMIT, process), process.wait(),
            )
        if stdout[1] or stderr[1]:
            raise VideoProbeError("Video probe output limit exceeded")
        if code != 0:
            raise VideoProbeError("Video probe failed")
        return _parse_metadata(stdout[0])
    except TimeoutError:
        raise VideoProbeError("Video probe timed out") from None
    except OSError:
        raise VideoProbeError("Video probe unavailable") from None
    finally:
        if process is not None and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            # Drain pipes after a timeout/cancellation so process.wait cannot
            # hang behind a paused stream transport. Never retain this output.
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(2):
                    await asyncio.gather(
                        _discard(process.stdout), _discard(process.stderr), process.wait(),
                    )


async def probe_video_metadata(content: bytes) -> PhysicalVideoMetadata:
    """Probe exact bytes in a closed temporary file, outside the asset cache."""
    path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="omega-video-probe-", suffix=".mp4", delete=False) as file:
            path = Path(file.name)
            file.write(content)
        return await _probe_path(path)
    except OSError:
        raise VideoProbeError("Video probe temporary file unavailable") from None
    finally:
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                raise VideoProbeError("Video probe temporary file cleanup failed") from None
