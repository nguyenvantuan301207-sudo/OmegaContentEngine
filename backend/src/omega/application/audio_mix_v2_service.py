"""Deterministic Audio Mixing v2 planning and timeline validation."""

from __future__ import annotations

from collections.abc import Sequence

from omega.domain.audio_mix import (
    AudioMixPlan,
    AudioStem,
    AudioStemRole,
    DuckingPolicy,
    GainEnvelopePoint,
    LoudnessPolicy,
)


class AudioMixPlanner:
    """Builds technical mix instructions from already-selected stems."""

    @classmethod
    def plan(
        cls,
        *,
        timeline_duration_ms: int,
        stems: Sequence[AudioStem],
        narration_intervals_ms: Sequence[tuple[int, int]] = (),
        loudness: LoudnessPolicy | None = None,
        ducking: DuckingPolicy | None = None,
    ) -> AudioMixPlan:
        if timeline_duration_ms <= 0:
            raise ValueError("timeline_duration_ms must be > 0")
        ordered = tuple(
            sorted(stems, key=lambda stem: (stem.start_ms, -stem.priority, stem.stem_id))
        )
        intervals = tuple(sorted(narration_intervals_ms))
        envelopes: dict[str, tuple[GainEnvelopePoint, ...]] = {}
        adjusted: list[AudioStem] = []
        for stem in ordered:
            duration = stem.end_ms - stem.start_ms
            points = [GainEnvelopePoint(offset_ms=0, gain_db=stem.gain_db)]
            if stem.fade_in_ms:
                points[0] = GainEnvelopePoint(offset_ms=0, gain_db=-96.0)
                points.append(GainEnvelopePoint(offset_ms=stem.fade_in_ms, gain_db=stem.gain_db))
            if stem.fade_out_ms:
                points.extend(
                    [
                        GainEnvelopePoint(
                            offset_ms=duration - stem.fade_out_ms,
                            gain_db=stem.gain_db,
                        ),
                        GainEnvelopePoint(offset_ms=duration, gain_db=-96.0),
                    ]
                )
            envelopes[stem.stem_id] = tuple(sorted(set(points), key=lambda point: point.offset_ms))

            # SFX placement is preserved; only level is bounded when it collides with speech.
            if stem.role == AudioStemRole.SFX and any(
                stem.start_ms < end and stem.end_ms > start for start, end in intervals
            ):
                adjusted.append(stem.model_copy(update={"gain_db": max(-30.0, stem.gain_db - 6.0)}))
            else:
                adjusted.append(stem)

        return AudioMixPlan(
            timeline_duration_ms=timeline_duration_ms,
            stems=tuple(adjusted),
            gain_envelopes=envelopes,
            narration_intervals_ms=intervals,
            loudness=loudness or LoudnessPolicy(),
            ducking=ducking or DuckingPolicy(),
        )
