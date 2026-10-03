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

## P23-D Audio Library, License Governance & Final Audio QA

P23-D implements the final audio governance, provenance integrity, pre-mix verification, post-master technical audio QA, and final acceptance gate for Omega's media pipeline.

P23-D preserves all established authorities:
- **P23-A** owns technical mixing, ducking envelopes, gain, loudnorm normalization (-16.0 LUFS), limiter ceiling (-1.5 dBTP), sample-rate/channel conversion (48 kHz stereo), and video mux.
- **P23-B** owns musical direction, energy arcs, and track selection.
- **P23-C** owns sound effect intent, placement, density, and asset selection.
- **P23-D** owns audio asset library governance, usage/license eligibility, source/provenance integrity, pre-mix audio eligibility QA, post-master technical audio QA, and the final audio acceptance gate.
- **P20 ProductionQAEngine & Guardian** own overall artifact acceptance and pipeline health; P23-D feeds its typed results into them without competing or duplicating authority.

### 1. AudioLibraryEntry Projection Model

`AudioLibraryEntry` represents governed audio assets across all functional roles (`NARRATION`, `MUSIC`, `SFX`, `AMBIENCE`):
- `asset_id`: Canonical asset identifier.
- `role`: `AudioStemRole` (`NARRATION`, `MUSIC`, `SFX`, `AMBIENCE`).
- `source_uri`: Filesystem path or URI.
- `source_provider`: Source catalog or provider (e.g. `Kokoro-TTS`, `InHouseCatalog`, `FoleyLab`).
- `source_sha256`: SHA-256 integrity hash.
- `duration_ms`: Duration in milliseconds.
- `sample_rate_hz`, `channels`, `codec`: Audio format metadata.
- `usage_state`: Typed `TrackUsageState` (`OWNED`, `LICENSED`, `PUBLIC_DOMAIN`, `GENERATED`, `ATTRIBUTION_REQUIRED`, `BLOCKED`, `UNKNOWN`, `EXPIRED`, `RESTRICTED`).
- `attribution`: Structured `AudioAttribution` (creator, title, provider, license_name, attribution_text, source_url).
- `lineage`: Provenance dictionary tracking upstream generation (e.g. script version, TTS provider, seed).

### 2. Usage & License Eligibility Policy

`AudioLicensePolicy` enforces deterministic eligibility:
- Allowed states: `OWNED`, `LICENSED`, `PUBLIC_DOMAIN`, `GENERATED`, `ATTRIBUTION_REQUIRED`.
- Disallowed states: `BLOCKED`, `EXPIRED`, `RESTRICTED`, `UNKNOWN`.
- **Fail-Closed Rule**: Any asset with `UNKNOWN` or `BLOCKED` status is rejected immediately. `UNKNOWN` never silently becomes `ALLOWED`.
- TTS / Narration assets are validated under provider/output policy (`GENERATED`), rather than stock music licenses.
- Synthetic/local test assets maintain explicit test provenance.

### 3. Attribution Policy & Attribution Manifest

When an asset has status `ATTRIBUTION_REQUIRED` or supplies non-empty `attribution`, `AudioAttributionManifestBuilder` retains:
- `asset_id`, `role`, `provider`, `creator`, `source_url`, `license_name`, and `required_attribution_text`.
- The derived manifest is embedded into `AudioQAResult` and passed downstream to RuntimeTruth / production sidecars without mutating underlying databases.
- Attribution is never fabricated.

### 4. Source & Provenance Integrity

Every audio stem is traceable end-to-end:
```
Source Audio Assets
       ↓
AudioLibraryEntry / Provenance
       ↓
MusicCue / SFXCue (P23-B / P23-C)
       ↓
AudioStem (P23-A)
       ↓
AudioMixPlan (P23-A)
       ↓
Master Audio Artifact (48kHz stereo WAV/AAC)
       ↓
Final Video Artifact (MP4 container)
```
Narration retains `script_version`, `tts_provider`, and audio artifact SHA-256 identity.

Asset integrity validation detects:
- `ASSET_HASH_MISMATCH`
- `MISSING_REQUIRED_HASH`
- `MISSING_AUDIO_ASSET`
- `ZERO_BYTE_AUDIO`
- `CORRUPT_AUDIO`

### 5. Pre-Mix Audio QA

Before P23-A mixing, `PreMixAudioQAEvaluator` evaluates all supplied stems:
- Asset existence on disk and non-zero size.
- Exact SHA-256 hash match against registered library entry.
- License eligibility (`AudioLicensePolicy.is_eligible`).
- Attribution availability for `ATTRIBUTION_REQUIRED` assets.
- Timing validity (non-negative offsets, within production timeline).
- Narration stem presence (fails closed if narration is absent).

### 6. Post-Master Physical Technical Audio QA

`PostMasterAudioQAEvaluator` physically measures the rendered master audio / muxed video artifact via legitimate `ffprobe` / `ebur128`:
- **Integrated Loudness**: Target -16.0 LUFS with ±1.5 LUFS acceptance tolerance. Rejection: `LOUDNESS_TOO_LOW`, `LOUDNESS_TOO_HIGH`.
- **True Peak Ceiling**: Strictly `<= -1.5 dBTP`. Violations flagged as `TRUE_PEAK_EXCEEDED` (BLOCKER).
- **Silence QA**: Detects `ALL_SILENT_MASTER`, `MISSING_AUDIO_STREAM`, or `EXCESSIVE_SILENCE`. Intentional narrative pauses matching cue gaps are preserved and not flagged.
- **Audio Format QA**: Enforces canonical 48,000 Hz sample rate, 2 audio channels (stereo), and AAC codec in final video. Detects `WRONG_SAMPLE_RATE`, `WRONG_CHANNEL_LAYOUT`, `UNEXPECTED_CODEC`.
- **A/V Sync & Duration QA**: Compares planned duration vs audio stream duration vs container duration within 500 ms canonical tolerance. Detects `AUDIO_TOO_SHORT`, `AUDIO_TOO_LONG`, `AV_DURATION_MISMATCH`.
- **Narration Intelligibility Protection**: Verifies narration stem presence, ducking envelope application (-12.0 to -14.0 dB during speech), and SFX collision attenuation (-6.0 dB under speech).

### 7. AudioQAResult & Severity Policy

Findings are typed with `AudioQASeverity`:
- `BLOCKER`: Fundamental integrity failure, missing narration, unverified/blocked license, true-peak violation, corrupt master. Causes status `FAIL` and blocks render gate.
- `ERROR`: Major defect requiring correction (e.g. sample rate mismatch, excessive silence). Status `FAIL`.
- `WARNING`: Minor non-blocking issue (e.g. slight loudness deviation within margin). Status `REVISE`.
- `INFO`: Diagnostic notice. Status `PASS`.

Overall QA Status:
- `PASS`: Zero errors/blockers, no actionable revisions.
- `REVISE`: Technically valid but non-blocking recommendations exist.
- `FAIL`: One or more errors/blockers exist.

### 8. Finding Deduplication & Structured Recommendations

`FinalAudioQAService.deduplicate_findings` collapses duplicate findings for the same asset, finding code, and timeline range, merging their contributing authorities.
When status is `REVISE` or `FAIL`, structured remediation recommendations are emitted (e.g. replace unlicensed track, rerun mastering, restore narration stem). P23-D never automatically alters `AudioMixPlan`.

### 9. Final Audio Acceptance Gate & P20 Integration

`AudioRenderGate.verify_acceptance(qa_result)` acts as the acceptance seam:
- Only `AudioQAStatus.PASS` is allowed to proceed to `ProductionQAEngine`, `Guardian`, and `RuntimeTruth`.
- Any `AudioQAStatus.FAIL` or `REVISE` raises `AudioRenderGateError`, preventing publication or acceptance.
- P20 `ProductionQAEngine` and `Guardian` retain ultimate acceptance authority.

### 10. Canonical Authority Chain & P24 Boundary

```
NarrativePlan / Pacing (P21)
       ↓
Visual Beat Sequence / Camera Transitions (P22)
       ↓
P23-B Music Director (MusicArc, MusicCue, Selection)
       ↓
P23-C SFX Director (SFXIntent, SFXCue, Selection)
       ↓
P23-D Audio Library Governance & Pre-Mix QA
       ↓
P23-A AudioMixPlanner (AudioMixPlan, Envelopes)
       ↓
AudioMixRenderer / FFmpegRenderer (Master Audio & Mux)
       ↓
P23-D Post-Master Technical Audio QA
       ↓
P23-D AudioRenderGate (PASS required)
       ↓
ProductionQAEngine / Guardian (P20)
       ↓
RuntimeTruth Accepted Artifact
```

**P24 Handoff Boundary**: P23 delivers a fully governed, mastered, and QA-verified audiovisual artifact ready for channel assembly and delivery orchestration in P24.
