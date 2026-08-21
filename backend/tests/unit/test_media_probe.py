"""Unit tests for Media Probe summary extraction."""

from omega.application.media_probe import MediaProbe


def test_media_probe_extract_summary():
    """Verify summary extraction from mock ffprobe JSON structure."""
    probe = MediaProbe()
    raw_data = {
        "format": {
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "duration": "10.500000",
            "bit_rate": "2500000",
        },
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "r_frame_rate": "30/1",
                "duration": "10.500000",
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "duration": "10.500000",
            },
        ],
    }

    summary = probe._extract_summary(raw_data, file_size_bytes=1048576)

    assert summary["file_size_bytes"] == 1048576
    assert summary["duration_ms"] == 10500
    assert summary["has_video"] is True
    assert summary["has_audio"] is True
    assert summary["width"] == 1920
    assert summary["height"] == 1080
    assert summary["fps"] == 30.0
    assert summary["video_codec"] == "h264"
    assert summary["audio_codec"] == "aac"
