from __future__ import annotations

import pytest
from pydantic import ValidationError

from omega.application.audio_mix_v2_service import AudioMixPlanner
from omega.domain.audio_mix import (
    AudioMixPlan,
    AudioStem,
    AudioStemRole,
    DuckingPolicy,
    GainEnvelopePoint,
    LoudnessPolicy,
)


def stem(
    role: AudioStemRole,
    *,
    stem_id: str | None = None,
    start: int = 0,
    end: int = 4_000,
    gain: float = 0.0,
    fade_in: int = 0,
    fade_out: int = 0,
) -> AudioStem:
    return AudioStem(
        stem_id=stem_id or role.value.lower(),
        role=role,
        source_artifact=f"/test/{role.value.lower()}.wav",
        source_identity=f"test-{role.value.lower()}",
        start_ms=start,
        end_ms=end,
        gain_db=gain,
        fade_in_ms=fade_in,
        fade_out_ms=fade_out,
        priority=100 if role == AudioStemRole.NARRATION else 50,
        optional=role != AudioStemRole.NARRATION,
    )


def test_narration_only_plan_uses_canonical_format_and_policy():
    plan = AudioMixPlanner.plan(timeline_duration_ms=4_000, stems=[stem(AudioStemRole.NARRATION)])
    assert plan.sample_rate_hz == 48_000
    assert plan.channel_layout == "stereo"
    assert plan.loudness == LoudnessPolicy()
    assert plan.stems[0].priority == 100


def test_narration_music_sfx_and_ambience_are_supported():
    roles = [
        AudioStemRole.NARRATION,
        AudioStemRole.MUSIC,
        AudioStemRole.SFX,
        AudioStemRole.AMBIENCE,
    ]
    plan = AudioMixPlanner.plan(
        timeline_duration_ms=4_000,
        stems=[stem(role, stem_id=f"s-{index}") for index, role in enumerate(roles)],
    )
    assert {item.role for item in plan.stems} == set(roles)


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"start_ms": -1}, "greater than or equal"),
        ({"start_ms": 1_000, "end_ms": 1_000}, "greater than"),
        ({"fade_in_ms": 3_000, "fade_out_ms": 2_000}, "fades exceed"),
    ],
)
def test_invalid_stem_timing_is_rejected(kwargs, code):
    data = stem(AudioStemRole.NARRATION).model_dump()
    data.update(kwargs)
    with pytest.raises(ValidationError, match=code):
        AudioStem(**data)


def test_missing_narration_fails_closed():
    with pytest.raises(ValidationError, match="EMPTY_NARRATION"):
        AudioMixPlanner.plan(timeline_duration_ms=4_000, stems=[stem(AudioStemRole.MUSIC)])


def test_overflow_and_bad_narration_interval_are_detected():
    with pytest.raises(ValidationError, match="AUDIO_OVERFLOW"):
        AudioMixPlanner.plan(
            timeline_duration_ms=3_000,
            stems=[stem(AudioStemRole.NARRATION, end=4_000)],
        )
    with pytest.raises(ValidationError, match="NARRATION_TIMING_MISMATCH"):
        AudioMixPlanner.plan(
            timeline_duration_ms=4_000,
            stems=[stem(AudioStemRole.NARRATION)],
            narration_intervals_ms=[(2_000, 1_000)],
        )


def test_fade_envelopes_are_deterministic_and_bounded():
    music = stem(AudioStemRole.MUSIC, fade_in=500, fade_out=750, gain=-18)
    plan_a = AudioMixPlanner.plan(
        timeline_duration_ms=4_000,
        stems=[music, stem(AudioStemRole.NARRATION)],
    )
    plan_b = AudioMixPlanner.plan(
        timeline_duration_ms=4_000,
        stems=[stem(AudioStemRole.NARRATION), music],
    )
    assert plan_a == plan_b
    assert plan_a.gain_envelopes["music"] == (
        GainEnvelopePoint(offset_ms=0, gain_db=-96.0),
        GainEnvelopePoint(offset_ms=500, gain_db=-18.0),
        GainEnvelopePoint(offset_ms=3_250, gain_db=-18.0),
        GainEnvelopePoint(offset_ms=4_000, gain_db=-96.0),
    )


def test_sfx_collision_reduces_level_without_moving_event():
    sfx = stem(AudioStemRole.SFX, start=900, end=1_300, gain=-2)
    plan = AudioMixPlanner.plan(
        timeline_duration_ms=4_000,
        stems=[stem(AudioStemRole.NARRATION), sfx],
        narration_intervals_ms=[(1_000, 2_000)],
    )
    adjusted = next(item for item in plan.stems if item.role == AudioStemRole.SFX)
    assert (adjusted.start_ms, adjusted.end_ms) == (900, 1_300)
    assert adjusted.gain_db == -8


def test_sfx_outside_narration_keeps_requested_gain():
    sfx = stem(AudioStemRole.SFX, start=2_500, end=3_000, gain=-2)
    plan = AudioMixPlanner.plan(
        timeline_duration_ms=4_000,
        stems=[stem(AudioStemRole.NARRATION), sfx],
        narration_intervals_ms=[(500, 2_000)],
    )
    assert next(item for item in plan.stems if item.role == AudioStemRole.SFX).gain_db == -2


def test_ducking_policy_is_bounded():
    with pytest.raises(ValidationError):
        DuckingPolicy(attenuation_db=-40)
    assert DuckingPolicy().attack_ms == 80
    assert DuckingPolicy().release_ms == 350


def test_plan_has_no_renderer_command_fields():
    fields = set(AudioMixPlan.model_fields)
    assert not fields.intersection({"command", "filter_complex", "shell"})


def test_source_lineage_and_hash_are_preserved():
    narration = stem(AudioStemRole.NARRATION).model_copy(
        update={"source_sha256": "a" * 64, "lineage": {"tts": "kokoro", "script": "v7"}}
    )
    plan = AudioMixPlanner.plan(timeline_duration_ms=4_000, stems=[narration])
    assert plan.stems[0].source_sha256 == "a" * 64
    assert plan.stems[0].lineage == {"tts": "kokoro", "script": "v7"}
