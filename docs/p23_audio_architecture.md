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
8. the existing single final FFmpeg `loudnorm` pass;
9. AAC mux with video stream copy.

The domain/application models contain no shell command fragments. `FFmpegRenderer` is the physical rendering boundary and invokes FFmpeg with argument arrays. The final mux retains video frames, uses AAC at 192 kb/s, and the final master is resampled to 48 kHz. RuntimeTruth receives the plan/renderer versions, supplied stem hashes, policies, mastering chain, and intermediate master hash.

Optional missing music or SFX may be omitted before planning; a missing required narration stem, corrupt input, invalid timeline, render failure, or empty master fails closed. Intentional narration gaps remain valid. Production QA continues to own missing/silent-stream acceptance checks.

## Later phase seams

- P23-B selects licensed music and supplies a `MUSIC` stem; it does not alter the mixer.
- P23-C selects and places SFX, then supplies `SFX` stems; P23-A preserves those placements.
- P23-D owns audio-library governance, licensing policy, and final audio acceptance QA. P23-A only preserves source metadata needed by that authority.

No schema change is required: plans are derived and their non-secret provenance is carried in the existing render manifest and RuntimeTruth payload.
