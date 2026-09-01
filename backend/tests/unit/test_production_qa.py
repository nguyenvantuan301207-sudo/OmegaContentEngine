"""Unit tests for 17 canonical Production QA rules and rights verification."""

import uuid

from omega.application.production_qa import ProductionQAEngine
from omega.domain.production import (
    LicenseStatus,
    ProductionQARuleCode,
    ProductionQAStatus,
)


def test_production_qa_all_pass():
    """Verify clean production artifacts evaluate to PASSED."""
    engine = ProductionQAEngine()
    s_id = uuid.uuid4()
    dna_id = uuid.uuid4()
    req_data = {
        "script_version_id": s_id,
        "channel_dna_revision_id": dna_id,
        "target_width": 1920,
        "target_height": 1080,
        "video_codec": "h264",
    }
    script_data = {"id": s_id}
    content_req_data = {"channel_dna_revision_id": dna_id}

    req_id = uuid.uuid4()
    reqs_list = [{"id": req_id, "purpose": "Background", "required": True}]
    assets_list = [
        {
            "id": uuid.uuid4(),
            "asset_requirement_id": req_id,
            "asset_type": "BACKGROUND",
            "provider_type": "SYSTEM",
            "source_ref": "Local Scene Card Generator",
            "license_status": LicenseStatus.GENERATED.value,
        },
        {
            "id": uuid.uuid4(),
            "asset_requirement_id": None,
            "asset_type": "SUBTITLE",
            "provider_type": "SYSTEM",
            "mime_type": "application/x-subrip",
            "source_ref": "Generated Subtitles",
            "license_status": LicenseStatus.OWNED.value,
        },
    ]
    narr_list = [{"id": uuid.uuid4(), "start_ms": 0, "end_ms": 3000}]
    subs_list = [{"cue_order": 1, "start_ms": 0, "end_ms": 3000, "text": "Sub text"}]

    probe_summary = {
        "duration_ms": 3000,
        "width": 1920,
        "height": 1080,
        "video_codec": "h264",
        "has_video": True,
        "has_audio": True,
        "mean_volume_db": -16.0,
        "max_volume_db": -3.0,
    }

    status, findings = engine.evaluate(
        request_data=req_data,
        script_version_data=script_data,
        content_request_data=content_req_data,
        assets_data=assets_list,
        requirements_data=reqs_list,
        narration_segments=narr_list,
        subtitle_cues=subs_list,
        media_probe_summary=probe_summary,
        artifact_file_path=None,
        expected_hash=None,
    )

    assert status == ProductionQAStatus.PASSED
    assert len(findings) == 0


def test_production_qa_blocked_rights():
    """Verify BLOCKED asset rights trigger BLOCKING finding and status."""
    engine = ProductionQAEngine()
    s_id = uuid.uuid4()
    dna_id = uuid.uuid4()
    req_data = {
        "script_version_id": s_id,
        "channel_dna_revision_id": dna_id,
        "target_width": 1920,
        "target_height": 1080,
        "video_codec": "h264",
    }
    script_data = {"id": s_id}
    content_req_data = {"channel_dna_revision_id": dna_id}

    req_id = uuid.uuid4()
    reqs_list = [{"id": req_id, "purpose": "Background", "required": True}]
    assets_list = [
        {
            "id": uuid.uuid4(),
            "asset_requirement_id": req_id,
            "license_status": LicenseStatus.BLOCKED.value,
            "source_ref": "Blocked asset",
        }
    ]
    narr_list = [{"id": uuid.uuid4(), "start_ms": 0, "end_ms": 3000}]

    status, findings = engine.evaluate(
        request_data=req_data,
        script_version_data=script_data,
        content_request_data=content_req_data,
        assets_data=assets_list,
        requirements_data=reqs_list,
        narration_segments=narr_list,
        subtitle_cues=[],
        media_probe_summary={
            "duration_ms": 3000,
            "width": 1920,
            "height": 1080,
            "video_codec": "h264",
            "has_audio": True,
        },
        artifact_file_path=None,
        expected_hash=None,
    )

    assert status == ProductionQAStatus.BLOCKED
    assert any(f.rule_code == ProductionQARuleCode.BLOCKED_ASSET_RIGHTS for f in findings)


def test_production_qa_lineage_mismatch():
    """Verify script and DNA lineage mismatches are flagged as BLOCKING."""
    engine = ProductionQAEngine()
    s_id = uuid.uuid4()
    dna_id = uuid.uuid4()
    other_dna_id = uuid.uuid4()

    req_data = {
        "script_version_id": s_id,
        "channel_dna_revision_id": dna_id,
        "target_width": 1920,
        "target_height": 1080,
        "video_codec": "h264",
    }
    script_data = {"id": uuid.uuid4()}  # Mismatched script
    content_req_data = {"channel_dna_revision_id": other_dna_id}  # Mismatched DNA

    status, findings = engine.evaluate(
        request_data=req_data,
        script_version_data=script_data,
        content_request_data=content_req_data,
        assets_data=[],
        requirements_data=[],
        narration_segments=[{"id": uuid.uuid4(), "start_ms": 0, "end_ms": 1000}],
        subtitle_cues=[],
        media_probe_summary=None,
        artifact_file_path=None,
        expected_hash=None,
    )

    assert status == ProductionQAStatus.BLOCKED
    codes = [f.rule_code for f in findings]
    assert ProductionQARuleCode.SCRIPT_PIN_MISMATCH in codes
    assert ProductionQARuleCode.DNA_LINEAGE_MISMATCH in codes


def test_production_qa_silent_audio_blocked():
    """Verify digital silence (-91 dBFS) triggers SILENT_AUDIO_STREAM BLOCKING finding."""
    engine = ProductionQAEngine()
    s_id = uuid.uuid4()
    dna_id = uuid.uuid4()
    req_id = uuid.uuid4()

    status, findings = engine.evaluate(
        request_data={
            "script_version_id": s_id,
            "channel_dna_revision_id": dna_id,
            "target_width": 1920,
            "target_height": 1080,
            "video_codec": "h264",
        },
        script_version_data={"id": s_id},
        content_request_data={"channel_dna_revision_id": dna_id},
        assets_data=[
            {
                "id": uuid.uuid4(),
                "asset_requirement_id": req_id,
                "asset_type": "BACKGROUND",
                "provider_type": "SYSTEM",
                "source_ref": "Local Scene Card Generator",
                "license_status": LicenseStatus.GENERATED.value,
            }
        ],
        requirements_data=[{"id": req_id, "purpose": "Background", "required": True}],
        narration_segments=[{"id": uuid.uuid4(), "start_ms": 0, "end_ms": 3000}],
        subtitle_cues=[],
        media_probe_summary={
            "duration_ms": 3000,
            "width": 1920,
            "height": 1080,
            "video_codec": "h264",
            "has_audio": True,
            "mean_volume_db": -91.0,
            "max_volume_db": -91.0,
        },
        artifact_file_path=None,
        expected_hash=None,
    )

    assert status == ProductionQAStatus.BLOCKED
    assert any(f.rule_code == ProductionQARuleCode.SILENT_AUDIO_STREAM for f in findings)


def test_production_qa_placeholder_only_visuals_blocked():
    """Verify placeholder-only visual assets trigger PLACEHOLDER_ONLY_VISUALS BLOCKING finding."""
    engine = ProductionQAEngine()
    s_id = uuid.uuid4()
    dna_id = uuid.uuid4()
    req_id = uuid.uuid4()

    status, findings = engine.evaluate(
        request_data={
            "script_version_id": s_id,
            "channel_dna_revision_id": dna_id,
            "target_width": 1920,
            "target_height": 1080,
            "video_codec": "h264",
        },
        script_version_data={"id": s_id},
        content_request_data={"channel_dna_revision_id": dna_id},
        assets_data=[
            {
                "id": uuid.uuid4(),
                "asset_requirement_id": req_id,
                "asset_type": "BACKGROUND",
                "provider_type": "PLACEHOLDER",
                "source_ref": "Local Generator Fallback",
                "license_status": LicenseStatus.GENERATED.value,
            }
        ],
        requirements_data=[{"id": req_id, "purpose": "Background", "required": True}],
        narration_segments=[{"id": uuid.uuid4(), "start_ms": 0, "end_ms": 3000}],
        subtitle_cues=[],
        media_probe_summary={
            "duration_ms": 3000,
            "width": 1920,
            "height": 1080,
            "video_codec": "h264",
            "has_audio": True,
            "mean_volume_db": -16.0,
            "max_volume_db": -3.0,
        },
        artifact_file_path=None,
        expected_hash=None,
    )

    assert status == ProductionQAStatus.BLOCKED
    assert any(f.rule_code == ProductionQARuleCode.PLACEHOLDER_ONLY_VISUALS for f in findings)


def test_production_qa_missing_subtitle_render_blocked():
    """Verify missing subtitle render when cues exist triggers MISSING_SUBTITLE_RENDER."""
    engine = ProductionQAEngine()
    s_id = uuid.uuid4()
    dna_id = uuid.uuid4()
    req_id = uuid.uuid4()

    status, findings = engine.evaluate(
        request_data={
            "script_version_id": s_id,
            "channel_dna_revision_id": dna_id,
            "target_width": 1920,
            "target_height": 1080,
            "video_codec": "h264",
        },
        script_version_data={"id": s_id},
        content_request_data={"channel_dna_revision_id": dna_id},
        assets_data=[
            {
                "id": uuid.uuid4(),
                "asset_requirement_id": req_id,
                "asset_type": "BACKGROUND",
                "provider_type": "SYSTEM",
                "source_ref": "Local Scene Card Generator",
                "license_status": LicenseStatus.GENERATED.value,
            }
        ],
        requirements_data=[{"id": req_id, "purpose": "Background", "required": True}],
        narration_segments=[{"id": uuid.uuid4(), "start_ms": 0, "end_ms": 3000}],
        subtitle_cues=[{"cue_order": 1, "start_ms": 0, "end_ms": 3000, "text": "Sub text"}],
        media_probe_summary={
            "duration_ms": 3000,
            "width": 1920,
            "height": 1080,
            "video_codec": "h264",
            "has_audio": True,
            "mean_volume_db": -16.0,
            "max_volume_db": -3.0,
        },
        artifact_file_path=None,
        expected_hash=None,
    )

    assert status == ProductionQAStatus.BLOCKED
    assert any(f.rule_code == ProductionQARuleCode.MISSING_SUBTITLE_RENDER for f in findings)
