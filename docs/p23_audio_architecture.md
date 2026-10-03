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

## P23-C SFX Director

`SFXDirector` is the editorial authority for sound effect intent derivation, collision planning, density enforcement, and eligible asset selection. It consumes the accepted `NarrativePlan`, its derived `PacingPlan`, `ChannelDNA`, `VisualBeat` sequence, `CameraTransitionPlan`, and `MusicArc`. P23-C owns WHY an effect exists, WHAT kind is appropriate, WHEN it occurs, HOW strong/prominent it should be, and WHICH eligible asset is selected. P23-C never becomes another mixing authority and contains no FFmpeg filter or rendering instructions.

### SFXIntent & SFXCue Model

`SFXIntent` describes the editorial justification for why a sound effect exists. Supported typed intents are:
- `NONE`: Explicitly intentional absence of SFX.
- `UI_CLICK`: Subtle digital/tactile accents (e.g. document/diagram inspections, comparison toggles).
- `WHOOSH`: Movement/sweep accents (e.g. narrative hook, camera whip pan, slide transitions).
- `IMPACT`: Heavy emphatic punctuation (e.g. major evidentiary reveals, climax payoffs).
- `RISER`: Rising anticipation leading into reveals.
- `REVEAL`: Punctuation for major informational discoveries.
- `TRANSITION`: Contextual scene shifts and hard context cuts.
- `ACCENT`: General subtle emphasis for structured beats.
- `NOTIFICATION`: Alerts or system cues.
- `MECHANICAL`: Physical/mechanical interaction sounds.
- `ENVIRONMENTAL`: Natural/room physical events.
- `AMBIENCE`: Continuous atmospheric texture.
- `STINGER`: Sharp editorial punctuation.

`SFXCue` represents an editorial cue on the production timeline, containing:
- `cue_id`: Unique identifier.
- `intent`: Editorial `SFXIntent`.
- `start_ms` and `duration_ms`: Timeline placement.
- `importance`, `energy` (0.0 to 1.0), and `prominence` (`SUBTLE`, `BALANCED`, `PROMINENT`, `ACCENT`).
- Lineage tracking: `narrative_section_id`, `visual_beat_id`, `camera_transition_lineage`.
- `continuity_motif_id`: Explicit identifier for intentional audio motifs.
- `collision_priority`: Numerical weight (0-100) for collision resolution.
- `fade_in_ms`, `fade_out_ms`, `gain_db` (editorial intent only).
- `suppressed` and `suppression_reason`: Audit trail when policy deactivates a cue.

### SFX Asset Metadata & Eligibility

`SFXAssetMetadata` represents available audio assets in the catalog with:
- `asset_id`, `source_artifact`, `intent`, `duration_ms`, `energy`, `prominence`, `loop_safe`, `source_catalog`, `usage_state`, `source_sha256`, `tags`, `format`, `exists`.

Deterministic eligibility (`SFXEligibilityEngine`) evaluates candidates before any scoring:
1. Asset exists on the filesystem / artifact store.
2. Format is compatible (`wav`, `mp3`, `aac`, `ogg`, `flac`).
3. Usage state is allowed (`OWNED`, `GENERATED`, `LICENSED`, `PUBLIC_DOMAIN`, `ATTRIBUTION_REQUIRED`). Assets with `UNKNOWN`, `BLOCKED`, or `RESTRICTED` fail eligibility immediately.
4. Duration suitability: Effect must not exceed maximum cue duration tolerance without an applicable trim/loop policy.
5. Intent compatibility: Primary intent matches or cue intent is present in asset tags.

Ineligible assets are excluded; creative scoring cannot revive them.

### Deterministic Selection Engine

`SFXSelectionEngine` deterministically ranks eligible candidates using weighted multi-factor scoring:
- Intent match (0.40): Primary intent match or tag match.
- Energy match (0.20): Proximity between asset energy and cue requested energy.
- Duration fit (0.15): Proximity to cue duration window.
- Channel DNA fit (0.15): Alignment with tone (subtle/restrained for calm/analytical vs bold/prominent for high-energy).
- Continuity / motif (0.10): Alignment with cue's `continuity_motif_id`.
- Repetition penalty (-0.25): Penalty applied if the asset was recently selected in the last 2 cues without an explicit motif match.

Ties are stably resolved by `asset_id` lexicographical sorting. If no eligible candidate exists, selection deterministically returns `selected_asset=None` with inspectable rationale.

### Editorial Intentional Silence & Narrative Restraint

`NONE` is a first-class editorial outcome:
- Routine narrative development sections without major reveals or hooks default to no SFX.
- Serious, factual, or analytical channel tones suppress unbacked sensational accents.
- MusicDirector intentional silence or music peaks protect narrative and musical clarity.

### Density & Collision Policy

Bounded density prevents acoustic clutter:
- Density ceiling responds to pacing profile (`FAST`: 10 cues/min, `BALANCED`: 6 cues/min, `DELIBERATE`: 4 cues/min) and tone constraints (calm caps at 4 cues/min).
- Minimum cue gap (`min_cue_gap_ms`: 400ms for fast, 800ms for balanced, 1200ms for deliberate, 1000ms for calm).
- Overlapping cues within the gap window are evaluated by `collision_priority`; lower-priority cues are suppressed with explicit audit codes (`SEMANTIC_COLLISION`, `SFX_OVERUSE`).

### Duration & Motifs

- Short effects play within the cue window without time-stretching.
- Longer effects are marked for bounded trimming by P23-A or rejected if duration exceeds tolerance.
- Recurring audio motifs (e.g. system click, brand stinger) are identified explicitly via `continuity_motif_id`, exempting them from accidental repetition penalties.

### MusicDirector Coexistence

P23-C consumes `MusicArc` but preserves MusicDirector authority:
- When MusicDirector mandates intentional silence (`MusicIntentType.NONE`), aggressive impacts are suppressed to preserve the quiet space.
- During music peaks (`target_energy >= 0.85`), competing impacts are suppressed or de-escalated (`MUSIC_PEAK_COLLISION`).
- P23-C never modifies music tracks, music cues, or music timing.

### P23-A Handoff & Mix Authority Preservation

Resolved selections are mapped via `sfx_selections_to_audio_stems` into `AudioStem(role=AudioStemRole.SFX)` for P23-A:
- Preserves `start_ms`, `end_ms`, `source_artifact`, `source_sha256`, `priority`, and rich editorial lineage.
- Base `gain_db` remains 0.0 dB.
- P23-C does NOT perform physical mixing, gain calculation, 6 dB collision attenuation under narration, loudnorm, limiting, resampling, or FFmpeg muxing. P23-A remains the sole mixing authority.

### P23-D Governance & License Boundary

P23-C respects license boundaries by filtering unverified usage states (`UNKNOWN`, `BLOCKED`). P23-D retains final authority over the enterprise audio library catalog, license enforcement, and post-mux audio QA.
