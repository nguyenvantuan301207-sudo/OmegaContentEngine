import base64
import json
import os
import uuid
import wave
from pathlib import Path

import httpx
import pytest

from omega.application.media_storage import LocalMediaStorageProvider
from omega.application.narration_provider import (
    GeminiTTSNarrationProvider,
    NarrationProviderError,
    get_narration_provider,
)


@pytest.fixture
def storage(tmp_path):
    class FakeStorage(LocalMediaStorageProvider):
        def __init__(self):
            self.root = tmp_path

        def get_narration_dir(self, channel_id, request_id):
            p = self.root / str(channel_id) / str(request_id)
            p.mkdir(parents=True, exist_ok=True)
            return p

        def to_relative_uri(self, channel_id, request_id, path):
            return f"media/{channel_id}/{request_id}/{path.name}"

    return FakeStorage()


def generate_valid_pcm(frames=12000):
    # 24kHz mono 16-bit PCM (12000 frames = 0.5 seconds)
    return b"\x00\x00" * frames


def create_mock_client(status_code=200, json_data=None):
    def handler(request):
        return httpx.Response(status_code, json=json_data)
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


@pytest.mark.asyncio
async def test_missing_api_key_fails(storage):
    if "GEMINI_API_KEY" in os.environ:
        del os.environ["GEMINI_API_KEY"]
    provider = GeminiTTSNarrationProvider(storage, api_key="")
    with pytest.raises(NarrationProviderError, match="GEMINI_API_KEY missing"):
        await provider.synthesize_segment_audio(uuid.uuid4(), uuid.uuid4(), {"text": "Hello"})


@pytest.mark.asyncio
async def test_successful_pcm_creates_wav(storage):
    pcm_data = generate_valid_pcm(24000)  # 1 second
    b64_data = base64.b64encode(pcm_data).decode("ascii")

    json_resp = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"inlineData": {"data": b64_data}}
                    ]
                }
            }
        ]
    }

    client = create_mock_client(200, json_resp)
    provider = GeminiTTSNarrationProvider(storage, api_key="test-key", client=client)

    res = await provider.synthesize_segment_audio(
        uuid.uuid4(), uuid.uuid4(), {"text": "Test"}
    )

    assert res["mime_type"] == "audio/wav"
    assert res["duration_ms"] == 1000  # 1 second of audio
    assert res["source_ref"] == "Gemini TTS (voice: Kore)"

    # Check WAV file
    wav_path = storage.root / str(res["channel_id"]) / str(res["production_request_id"]) / Path(res["storage_uri"]).name
    assert wav_path.exists()

    with wave.open(str(wav_path), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 24000
        assert wf.getnframes() == 24000


@pytest.mark.asyncio
async def test_voice_direction_and_raw_narration(storage):
    pcm_data = generate_valid_pcm()
    b64_data = base64.b64encode(pcm_data).decode("ascii")
    json_resp = {"candidates": [{"content": {"parts": [{"inlineData": {"data": b64_data}}]}}]}

    req_history = []

    def handler(request):
        req_history.append(json.loads(request.content))
        return httpx.Response(200, json=json_resp)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GeminiTTSNarrationProvider(storage, api_key="test", client=client)

    voice_profile = {
        "voice_ref": "Aoede",
        "language": "en",
        "style": "dramatic narration",
        "speaking_rate": "1.2",
        "pitch": "low"
    }

    await provider.synthesize_segment_audio(
        uuid.uuid4(), uuid.uuid4(), {"text": "Test narration text."}, voice_profile
    )

    assert len(req_history) == 1
    req_payload = req_history[0]

    text_sent = req_payload["contents"][0]["parts"][0]["text"]

    assert "dramatic narration" in text_sent
    assert "natural American English / en-US" in text_sent
    assert "Speaking rate: 1.2" in text_sent
    assert "Pitch: low" in text_sent
    assert "Narration text:\nTest narration text." in text_sent
    assert "exactly as written" in text_sent
    assert "No added words" in text_sent

    voice_name = req_payload["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"]
    assert voice_name == "Aoede"

    assert "AUDIO" in req_payload["generationConfig"]["responseModalities"]


@pytest.mark.asyncio
async def test_malformed_response_fails(storage):
    client = create_mock_client(200, {"candidates": []})
    provider = GeminiTTSNarrationProvider(storage, api_key="test", client=client)

    with pytest.raises(NarrationProviderError, match="Malformed response shape"):
        await provider.synthesize_segment_audio(uuid.uuid4(), uuid.uuid4(), {"text": "Test"})


@pytest.mark.asyncio
async def test_invalid_base64_fails(storage):
    json_resp = {"candidates": [{"content": {"parts": [{"inlineData": {"data": "invalid-b64!@#"}}]}}]}
    client = create_mock_client(200, json_resp)
    provider = GeminiTTSNarrationProvider(storage, api_key="test", client=client)

    with pytest.raises(NarrationProviderError, match="Malformed response shape or invalid base64"):
        await provider.synthesize_segment_audio(uuid.uuid4(), uuid.uuid4(), {"text": "Test"})


@pytest.mark.asyncio
async def test_http_failure_sanitized(storage):
    client = create_mock_client(500, {})
    provider = GeminiTTSNarrationProvider(storage, api_key="test-key-1234", client=client)

    with pytest.raises(NarrationProviderError, match="Gemini API returned status code 500"):
        await provider.synthesize_segment_audio(uuid.uuid4(), uuid.uuid4(), {"text": "Test"})


@pytest.mark.asyncio
async def test_cache_hit_avoids_second_request(storage):
    pcm_data = generate_valid_pcm()
    b64_data = base64.b64encode(pcm_data).decode("ascii")
    json_resp = {"candidates": [{"content": {"parts": [{"inlineData": {"data": b64_data}}]}}]}

    call_count = 0

    def handler(request):
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=json_resp)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GeminiTTSNarrationProvider(storage, api_key="test", client=client)

    chan = uuid.uuid4()
    req = uuid.uuid4()

    # First call
    res1 = await provider.synthesize_segment_audio(chan, req, {"text": "Idempotent text"})
    assert call_count == 1

    # Second call with same text and voice
    res2 = await provider.synthesize_segment_audio(chan, req, {"text": "Idempotent text"})
    assert call_count == 1  # Should hit cache
    assert res1["duration_ms"] == 500
    assert res2["duration_ms"] == 500

@pytest.mark.asyncio
async def test_cache_hit_duration_uses_frames_not_size(storage):
    # 1000 frames @ 24000Hz = ~41.66ms -> 41ms duration.
    # 1000 frames * 2 bytes = 2000 bytes PCM data.
    # WAV header is 44 bytes. Total file size = 2044 bytes.
    # Old logic: int(2044 / 48000 * 1000) = 42ms.
    # New logic: int(1000 / 24000 * 1000) = 41ms.
    pcm_data = generate_valid_pcm(1000)
    b64_data = base64.b64encode(pcm_data).decode("ascii")
    json_resp = {"candidates": [{"content": {"parts": [{"inlineData": {"data": b64_data}}]}}]}

    client = create_mock_client(200, json_resp)
    provider = GeminiTTSNarrationProvider(storage, api_key="test", client=client)

    chan = uuid.uuid4()
    req = uuid.uuid4()

    # First call generates WAV
    res1 = await provider.synthesize_segment_audio(chan, req, {"text": "Duration test text"})
    assert res1["duration_ms"] == 41

    # Next call reads from cache
    res2 = await provider.synthesize_segment_audio(chan, req, {"text": "Duration test text"})
    assert res2["duration_ms"] == 41

    # Prove it fails closed if WAV is malformed
    wav_path = storage.root / str(chan) / str(req) / Path(res1["storage_uri"]).name
    wav_path.write_bytes(b"garbage" * 10)  # corrupt the WAV

    with pytest.raises(NarrationProviderError, match="Cached WAV file is malformed or not a valid WAV"):
        await provider.synthesize_segment_audio(chan, req, {"text": "Duration test text"})


def test_factory_selects_gemini(storage, monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    provider = get_narration_provider(storage)
    assert isinstance(provider, GeminiTTSNarrationProvider)


def test_factory_gemini_missing_key_fails(storage, monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "gemini")
    if "GEMINI_API_KEY" in os.environ:
        monkeypatch.delenv("GEMINI_API_KEY")

    with pytest.raises(NarrationProviderError, match="GEMINI_API_KEY missing"):
        get_narration_provider(storage)
