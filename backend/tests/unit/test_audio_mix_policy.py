import pytest

from omega.application.audio_mix_policy import (
    build_audio_mix_plan,
    build_background_music_plan,
    build_sfx_event_plan,
    validate_audio_license,
)
from omega.domain.production import LicenseStatus


def test_license_gate_allowed():
    assert validate_audio_license(LicenseStatus.OWNED)
    assert validate_audio_license(LicenseStatus.GENERATED)
    assert validate_audio_license(LicenseStatus.LICENSED)
    assert validate_audio_license(LicenseStatus.PUBLIC_DOMAIN)
    assert validate_audio_license(LicenseStatus.ATTRIBUTION_REQUIRED)
    assert validate_audio_license("OWNED")


def test_license_gate_rejected():
    assert not validate_audio_license(LicenseStatus.UNKNOWN)
    assert not validate_audio_license(LicenseStatus.BLOCKED)
    assert not validate_audio_license("UNKNOWN")
    assert not validate_audio_license("BLOCKED")
    assert not validate_audio_license("SOME_RANDOM_LICENSE")


def test_background_music_default():
    plan = build_background_music_plan(
        video_duration_ms=10000,
        music_duration_ms=5000,
        license_status=LicenseStatus.OWNED,
    )
    assert plan.target_duration_ms == 10000
    assert plan.music_duration_ms == 5000
    assert plan.gain_db == -20.0
    assert plan.fade_in_ms == 1000
    assert plan.fade_out_ms == 1500
    assert plan.loop_required is True
    assert plan.attribution_required is False


def test_background_music_loop_required_false():
    plan = build_background_music_plan(
        video_duration_ms=5000,
        music_duration_ms=10000,
        license_status=LicenseStatus.OWNED,
    )
    assert plan.loop_required is False


def test_background_music_equal_duration():
    plan = build_background_music_plan(
        video_duration_ms=5000,
        music_duration_ms=5000,
        license_status=LicenseStatus.OWNED,
    )
    assert plan.loop_required is False


def test_background_music_gain_bounds():
    with pytest.raises(ValueError, match="gain_db"):
        build_background_music_plan(
            video_duration_ms=10000,
            music_duration_ms=5000,
            license_status="OWNED",
            gain_db=-41.0,
        )
    with pytest.raises(ValueError, match="gain_db"):
        build_background_music_plan(
            video_duration_ms=10000,
            music_duration_ms=5000,
            license_status="OWNED",
            gain_db=-5.0,
        )


def test_background_music_negative_fade():
    with pytest.raises(ValueError, match="fade values cannot be negative"):
        build_background_music_plan(
            video_duration_ms=10000,
            music_duration_ms=5000,
            license_status="OWNED",
            fade_in_ms=-1,
        )


def test_background_music_fade_clamping():
    plan = build_background_music_plan(
        video_duration_ms=2000,
        music_duration_ms=5000,
        license_status="OWNED",
        fade_in_ms=1500,
        fade_out_ms=1500,
    )
    assert plan.fade_in_ms + plan.fade_out_ms <= 2000
    assert plan.fade_in_ms == 1000
    assert plan.fade_out_ms == 1000


def test_background_music_attribution_required():
    plan = build_background_music_plan(
        video_duration_ms=10000,
        music_duration_ms=5000,
        license_status=LicenseStatus.ATTRIBUTION_REQUIRED,
    )
    assert plan.attribution_required is True


def test_sfx_event_valid():
    sfx = build_sfx_event_plan(
        event_id="pop",
        start_ms=1000,
        duration_ms=500,
        video_duration_ms=5000,
        license_status="OWNED",
        gain_db=-10.0,
    )
    assert sfx.event_id == "pop"
    assert sfx.attribution_required is False


def test_sfx_empty_event_id():
    with pytest.raises(ValueError, match="event_id must be non-empty"):
        build_sfx_event_plan(
            event_id="",
            start_ms=0,
            duration_ms=500,
            video_duration_ms=5000,
            license_status="OWNED",
            gain_db=-10.0,
        )


def test_sfx_negative_start():
    with pytest.raises(ValueError, match="start_ms must be >= 0"):
        build_sfx_event_plan(
            event_id="pop",
            start_ms=-1,
            duration_ms=500,
            video_duration_ms=5000,
            license_status="OWNED",
            gain_db=-10.0,
        )


def test_sfx_zero_duration():
    with pytest.raises(ValueError, match="duration_ms must be > 0"):
        build_sfx_event_plan(
            event_id="pop",
            start_ms=0,
            duration_ms=0,
            video_duration_ms=5000,
            license_status="OWNED",
            gain_db=-10.0,
        )


def test_sfx_outside_video():
    with pytest.raises(ValueError, match="SFX event must fit fully inside"):
        build_sfx_event_plan(
            event_id="pop",
            start_ms=4000,
            duration_ms=2000,
            video_duration_ms=5000,
            license_status="OWNED",
            gain_db=-10.0,
        )


def test_sfx_gain_out_of_bounds():
    with pytest.raises(ValueError, match="gain_db"):
        build_sfx_event_plan(
            event_id="pop",
            start_ms=0,
            duration_ms=500,
            video_duration_ms=5000,
            license_status="OWNED",
            gain_db=-31.0,
        )
    with pytest.raises(ValueError, match="gain_db"):
        build_sfx_event_plan(
            event_id="pop",
            start_ms=0,
            duration_ms=500,
            video_duration_ms=5000,
            license_status="OWNED",
            gain_db=1.0,
        )


def test_sfx_license_blocked():
    with pytest.raises(ValueError, match="Invalid or rejected license status"):
        build_sfx_event_plan(
            event_id="pop",
            start_ms=0,
            duration_ms=500,
            video_duration_ms=5000,
            license_status="BLOCKED",
            gain_db=-10.0,
        )


def test_audio_mix_plan_overlapping_sfx():
    sfx1 = build_sfx_event_plan(
        event_id="a",
        start_ms=1000,
        duration_ms=1000,
        video_duration_ms=5000,
        license_status="OWNED",
        gain_db=-10.0,
    )
    sfx2 = build_sfx_event_plan(
        event_id="b",
        start_ms=1500,
        duration_ms=1000,
        video_duration_ms=5000,
        license_status="OWNED",
        gain_db=-10.0,
    )
    plan = build_audio_mix_plan(video_duration_ms=5000, sfx_events=[sfx1, sfx2])
    assert len(plan.sfx_events) == 2


def test_audio_mix_plan_sorting():
    sfx1 = build_sfx_event_plan(
        event_id="b",
        start_ms=2000,
        duration_ms=1000,
        video_duration_ms=5000,
        license_status="OWNED",
        gain_db=-10.0,
    )
    sfx2 = build_sfx_event_plan(
        event_id="a",
        start_ms=1000,
        duration_ms=1000,
        video_duration_ms=5000,
        license_status="OWNED",
        gain_db=-10.0,
    )
    sfx3 = build_sfx_event_plan(
        event_id="c",
        start_ms=1000,
        duration_ms=1000,
        video_duration_ms=5000,
        license_status="OWNED",
        gain_db=-10.0,
    )

    plan = build_audio_mix_plan(video_duration_ms=5000, sfx_events=[sfx1, sfx2, sfx3])
    assert plan.sfx_events[0].event_id == "a"
    assert plan.sfx_events[1].event_id == "c"
    assert plan.sfx_events[2].event_id == "b"


def test_audio_mix_plan_identical_inputs():
    sfx1 = build_sfx_event_plan(
        event_id="a",
        start_ms=1000,
        duration_ms=1000,
        video_duration_ms=5000,
        license_status="OWNED",
        gain_db=-10.0,
    )
    bg1 = build_background_music_plan(
        video_duration_ms=5000,
        music_duration_ms=5000,
        license_status="OWNED",
    )
    plan1 = build_audio_mix_plan(
        video_duration_ms=5000,
        background_music=bg1,
        sfx_events=[sfx1],
    )

    sfx2 = build_sfx_event_plan(
        event_id="a",
        start_ms=1000,
        duration_ms=1000,
        video_duration_ms=5000,
        license_status="OWNED",
        gain_db=-10.0,
    )
    bg2 = build_background_music_plan(
        video_duration_ms=5000,
        music_duration_ms=5000,
        license_status="OWNED",
    )
    plan2 = build_audio_mix_plan(
        video_duration_ms=5000,
        background_music=bg2,
        sfx_events=[sfx2],
    )

    assert plan1 == plan2
