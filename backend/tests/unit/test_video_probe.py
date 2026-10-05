import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from omega.infrastructure import video_probe
from omega.infrastructure.video_probe import VideoProbeError, probe_video_metadata


def payload(width=1280, height=720, duration="9.173333"):
    return {"streams": [{"width": width, "height": height}], "format": {"duration": duration}}


class Process:
    def __init__(self, stdout=b"", stderr=b"", code=0, hang=False):
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(stdout)
        self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_data(stderr)
        self.stderr.feed_eof()
        self.returncode = None
        self.code = code
        self.hang = hang
        self.killed = False

    def kill(self):
        self.killed = True
        self.returncode = -9

    async def wait(self):
        if self.hang and not self.killed:
            await asyncio.Future()
        if self.returncode is None:
            self.returncode = self.code
        return self.returncode


@pytest.mark.asyncio
@pytest.mark.parametrize("width,height,duration", [(1920, 1080, "57.04"), (1280, 720, "9.173333")])
async def test_valid_probe_closed_exact_temp_file(monkeypatch, width, height, duration):
    paths = []

    async def spawn(*args, **kwargs):
        assert args[:3] == ("ffprobe", "-v", "error")
        assert args[args.index("-select_streams") + 1] == "v:0"
        assert args[args.index("-protocol_whitelist") + 1] == "file"
        assert "shell" not in kwargs
        assert kwargs["stdout"] == kwargs["stderr"] == asyncio.subprocess.PIPE
        path = Path(args[-1])
        paths.append(path)
        # Reopening proves it is closed on Windows, and the bytes are exact.
        with path.open("rb") as file:
            assert file.read() == b"exact-downloaded-video"
        return Process(json.dumps(payload(width, height, duration)).encode())

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    result = await probe_video_metadata(b"exact-downloaded-video")
    assert (result.width, result.height) == (width, height)
    assert result.duration_seconds == pytest.approx(float(duration), abs=1e-6)
    assert paths and all(not path.exists() for path in paths)


INVALID = [
    {}, [], {"streams": []}, {"streams": [None], "format": {}},
    {"streams": "not-list", "format": {}}, {"streams": [{}], "format": []},
    payload(width=0), payload(height=0), payload(width=-1), payload(height=-1),
    payload(width=True), payload(width="1280"), payload(height=720.5),
] + [payload(duration=value) for value in (None, 0, -1, "NaN", "Infinity", "-Infinity", True, "bad", {}, [])]


@pytest.mark.asyncio
@pytest.mark.parametrize("data", INVALID)
async def test_bad_metadata_fails_closed_and_cleans_temp(monkeypatch, data):
    paths = []

    async def spawn(*args, **kwargs):
        paths.append(Path(args[-1]))
        return Process(json.dumps(data).encode())

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(VideoProbeError, match="^Invalid physical video metadata$"):
        await probe_video_metadata(b"bytes")
    assert all(not path.exists() for path in paths)


@pytest.mark.asyncio
@pytest.mark.parametrize("output", [b"not-json", b"\xff", b'{"streams":'])
async def test_bad_json(monkeypatch, output):
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=Process(output)))
    with pytest.raises(VideoProbeError, match="Invalid physical video metadata"):
        await probe_video_metadata(b"bytes")


@pytest.mark.asyncio
async def test_nonzero_stderr_not_exposed(monkeypatch):
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=Process(
        json.dumps(payload()).encode(), b"secret /private/path/unreadable.mp4", code=1,
    )))
    with pytest.raises(VideoProbeError, match="^Video probe failed$"):
        await probe_video_metadata(b"bytes")


@pytest.mark.asyncio
@pytest.mark.parametrize("stream,limit", [("stdout", video_probe._STDOUT_LIMIT), ("stderr", video_probe._STDERR_LIMIT)])
async def test_output_caps_kill_process(monkeypatch, stream, limit):
    process = Process(**{stream: b"sensitive" * (limit // 9 + 2)})
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    with pytest.raises(VideoProbeError, match="^Video probe output limit exceeded$"):
        await probe_video_metadata(b"bytes")
    assert process.killed


@pytest.mark.asyncio
async def test_timeout_kills_and_cleans(monkeypatch):
    process = Process(hang=True)
    paths = []

    async def spawn(*args, **kwargs):
        paths.append(Path(args[-1]))
        return process

    monkeypatch.setattr(video_probe, "_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(VideoProbeError, match="^Video probe timed out$"):
        await probe_video_metadata(b"bytes")
    assert process.killed and all(not path.exists() for path in paths)


@pytest.mark.asyncio
async def test_missing_executable_cleans_without_exposing_path(monkeypatch):
    paths = []

    async def spawn(*args, **kwargs):
        paths.append(Path(args[-1]))
        raise FileNotFoundError("secret /private/path")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(VideoProbeError, match="^Video probe unavailable$"):
        await probe_video_metadata(b"bytes")
    assert all(not path.exists() for path in paths)
