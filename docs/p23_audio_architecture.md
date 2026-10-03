# P23 Audio Architecture

## P23-A Audio Mixing v2

`AudioStem` is the renderer-neutral description of an already supplied audio source. The supported technical roles are `NARRATION`, `MUSIC`, `SFX`, and `AMBIENCE`. A stem records placement, trim, gain, fades, priority, source identity/hash, and non-secret lineage. P23-A never selects music or effects.

`AudioMixPlanner` derives an immutable `AudioMixPlan`. The plan fixes the production duration, 48 kHz stereo output, ordered stems, gain envelopes, narration intervals, ducking policy, loudness target, peak ceiling, and duration tolerance. It rejects missing narration, duplicate stems, negative/out-of-range timing, overflowing stems, invalid fades, and envelopes outside their stem.

The canonical online-video mastering defaults are -16 LUFS integrated, -1.5 dBTP true peak, 7 LU loudness range, and 48 kHz stereo. They reuse Omega's established final-master policy and remain explicit/configurable model values.

The mastering chain is:

1. trim and timeline alignment;
2. deterministic resampling/channel formatting;
3. per-stem gain and fades;
4. narration-keyed music ducking (80 ms attack, 350 ms release by default);
5. SFX collision attenuation without moving or deleting the supplied event;
6. stem summing;
7. -1.5 dBTP bounded limiter;
8. a measured two-pass FFmpeg `loudnorm` normalization at the single final-master boundary;
9. AAC mux with video stream copy.

The domain/application models contain no shell command fragments. `FFmpegRenderer` is the physical rendering boundary and invokes FFmpeg with argument arrays. The final mux retains video frames, uses AAC at 192 kb/s, and the final master is resampled to 48 kHz. RuntimeTruth receives the plan/renderer versions, supplied stem hashes, policies, mastering chain, and intermediate master hash.

Optional missing music or SFX may be omitted before planning; a missing required narration stem, corrupt input, invalid timeline, render failure, or empty master fails closed. Intentional narration gaps remain valid. Production QA continues to own missing/silent-stream acceptance checks.

## Later phase seams

- P23-B selects eligible music and supplies a `MUSIC` stem; it does not alter the mixer.
- P23-C selects and places SFX, then supplies `SFX` stems; P23-A preserves those placements.
- P23-D owns audio-library governance, licensing policy, and final audio acceptance QA. P23-A only preserves source metadata needed by that authority.

No schema change is required: plans are derived and their non-secret provenance is carried in the existing render manifest and RuntimeTruth payload.

## P23-B Music Director

`MusicDirector` consumes the accepted `NarrativePlan`, its derived `PacingPlan`, and pinned `ChannelDNA`. It maps section roles, recommended durations, pacing profile, and channel tone into a derived `MusicArc`. The arc contains bounded energy values and typed `MusicCue` editorial intents; it never contains FFmpeg filters.

`MusicIntent` expresses why music is present (`INTRO`, `BACKGROUND`, `DISCOVERY`, `BUILD`, `PAYOFF`, `REFLECTION`, `RESOLUTION`, or `OUTRO`) or explicitly absent (`NONE`). It includes mood, bounded energy/intensity, tempo preference, vocal policy, timing, narrative role, continuity group, and transition intent. Silence is selected for an explicit no-music channel preference and may also protect serious grounded context.

Track metadata remains factual and unknown values remain `None`. Eligibility runs before scoring. Missing files, blocked or unknown usage state, unconfirmed instrumental status under `INSTRUMENTAL_ONLY`, and short non-loop-safe tracks are excluded. Creative scoring cannot revive them. Eligible candidates are ranked deterministically by energy, mood, tempo, duration, motif continuity, and recent repetition; stable asset ID ordering breaks ties.

Continuity favors the same asset inside the `main-motif` group. Changes are editorially signaled around payoff and closing boundaries. Deterministic sequence evaluation detects excessive switching, motif drift, unjustified track changes, arc discontinuity, premature peaks, and excessive oscillation. Track reuse across unrelated recent contexts receives a penalty, while intentional motif continuation receives a stronger continuity benefit.

Resolved cues translate to P23-A `AudioStem(role=MUSIC)` values with exact timing, source identity/hash, fades, continuity metadata, narrative-section lineage, and Music Director version. P23-A remains the sole owner of physical fades, 80/350 ms narration ducking, gain, normalization, limiting, format conversion, and muxing.

P23-B does not choose SFX (P23-C) and does not establish catalog or license governance (P23-D). It consumes known usage state only: known-invalid and unknown-permission assets are ineligible, and an empty eligible set deterministically becomes narration-only output.
