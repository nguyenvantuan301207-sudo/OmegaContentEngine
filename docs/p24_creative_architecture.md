# P24 Creative Architecture

## P24-A Channel DNA v2

`ChannelDNA` and `ChannelDNARevision` represent the canonical immutable identity, policy, and creative preference description for an Omega channel.

### Single Canonical Authority Rule

There is strictly one canonical creative identity authority in Omega: `ChannelDNARevision`.
No competing mutable roots (e.g. `BrandProfile`, `ChannelStyle`, `CreativeIdentity`) exist.
All downstream stages (P21 Narrative, P22 Visual, P23 Audio, P24-B Creative Style Director, P24-C Packaging, and P24-D Creative QA) derive their policies and preferences directly from a pinned `ChannelDNARevision`.

### Structural Composition of Channel DNA v2

Channel DNA v2 extends the immutable revision model with rich, typed, and inspectable domain models:

1. **Channel Positioning (`ChannelPositioning`)**:
   - `channel_purpose`: The reason the channel exists.
   - `content_promise`: The core commitment made to viewers.
   - `distinctive_angle`: The unique perspective or unfair advantage.
   - `primary_domain`: The primary subject domain.
   - `secondary_domains`: Complementary or related domains.
   - `audience_benefit`: Concrete takeaway for the target audience.

2. **Audience Model (`AudienceProfile`)**:
   - `primary_audience`: General audience descriptor.
   - `knowledge_level`: Viewer familiarity level (`BEGINNER`, `INTERMEDIATE`, `ADVANCED`, `EXPERT`).
   - `expected_context`: Context expected from viewers.
   - `desired_depth`: Desired exploration depth (`SURVEY`, `BALANCED`, `IN_DEPTH`, `EXHAUSTIVE`).
   - `preferred_complexity`: Pacing and structural complexity (`ACCESSIBLE`, `MODERATE`, `CHALLENGING`).
   - `jargon_sensitivity`: Willingness to tolerate specialized jargon (`AVOID`, `EXPLAIN`, `EMBRACE`).
   - `content_expectations`: Concrete viewer expectations.

3. **Editorial Voice (`EditorialVoice`)**:
   - Bounded typed dimensions:
     - `formality`: `FORMAL`, `BALANCED`, `CONVERSATIONAL`
     - `depth`: `CONCISE`, `BALANCED`, `EXPLANATORY`
     - `energy`: `CALM`, `MODERATE`, `ENERGETIC`
     - `expressiveness`: `NEUTRAL`, `MEASURED`, `EXPRESSIVE`
     - `technicality`: `ACCESSIBLE`, `BALANCED`, `TECHNICAL`
     - `character`: `SERIOUS`, `BALANCED`, `PLAYFUL`
   - `style_notes`: Supplementary context without superseding typed bounds.

4. **Hard Constraints vs. Soft Preferences**:
   - **Hard Constraints (`HardConstraints`)**: Non-negotiable boundaries that downstream generators and validators MUST NOT violate.
     - `no_fabricated_claims`: Absolute truthfulness and verification requirement.
     - `no_misleading_clickbait`: Strict ban on deceptive titles/thumbnails.
     - `no_unsupported_medical_claims`: Prohibits unverified medical or health assertions.
     - `no_profanity`: Strict profanity filter.
     - `custom_prohibitions`: Additional channel-mandated non-negotiable prohibitions.
   - **Soft Preferences (`SoftPreferences`)**: Tunable creative biases that downstream directors optimize within constraint boundaries.
     - `cinematic_visuals`: Preference for cinematic composition.
     - `moderate_pacing`: Preference for balanced rhythm.
     - `concise_cta`: Preference for brief calls-to-action.
     - `instrumental_music`: Preference for non-vocal audio tracks.
     - `custom_preferences`: Additional channel creative preferences.

5. **Narrative Preferences (`NarrativePreferences`)**:
   - Directly guides P21 Narrative Director and Retention Pacing.
   - `preferred_strategies`: Prioritized narrative frameworks (e.g. `MYSTERY_INVESTIGATION`, `COMPARATIVE_BREAKDOWN`).
   - `hook_style`: `QUESTION`, `SURPRISING_FACT`, `COLD_OPEN`, `PARADOX`, `CHALLENGE`.
   - `context_depth`: `MINIMAL`, `BALANCED`, `DETAILED`.
   - `preferred_pacing`: `RAPID`, `BALANCED`, `MEASURED`.
   - `open_loop_tolerance`: `STRICT`, `BALANCED`, `GENEROUS`.
   - `payoff_style`: `DIRECT`, `GRADUAL`, `TWIST`.
   - `educational_depth`: `INTRODUCTORY`, `PRACTICAL`, `DEEP_DIVE`.
   - `cta_style`: `NONE`, `SUBTLE`, `DIRECT`, `ENGAGING`.

6. **Visual Preferences (`VisualPreferences`)**:
   - Guides P22 Visual Director and Editorial QA.
   - `visual_density`: `MINIMAL`, `BALANCED`, `HIGH`.
   - `b_roll_usage`: `LOW`, `BALANCED`, `HIGH`.
   - `diagram_frequency`: `NEVER`, `SELECTIVE`, `FREQUENT`.
   - `evidence_emphasis`: `LOW`, `BALANCED`, `HIGH`.
   - `camera_motion_intensity`: `STILL`, `SUBTLE`, `DYNAMIC`.
   - `transition_restraint`: `STRICT`, `MODERATE`, `DYNAMIC`.
   - `typography_character`: Descriptive text style guidance.
   - `graphic_complexity`: `SIMPLE`, `BALANCED`, `COMPLEX`.
   - `comparison_treatment`: `SIDE_BY_SIDE`, `SEQUENTIAL`, `SPLIT_SCREEN`.

7. **Audio Preferences (`AudioPreferences`)**:
   - Guides P23 Music and SFX Director.
   - `music_usage_tendency`: `ALWAYS`, `BALANCED`, `MINIMAL`, `NEVER`.
   - `preferred_energy_min` / `preferred_energy_max`: Numeric bounds in [0.0, 1.0].
   - `vocal_policy`: `INSTRUMENTAL_ONLY`, `ANY`.
   - `sfx_density`: `SPARSE`, `BALANCED`, `DENSE`, `OFF`.
   - `audio_restraint`: `STRICT`, `BALANCED`, `EXPRESSIVE`.
   - `character`: `CINEMATIC`, `MINIMAL`, `TECHNICAL`, `DYNAMIC`.

8. **Packaging Preferences & P24-C Seam (`PackagingPreferences`, `ResolvedPackagingSpec`)**:
   - Provides clear parameters for future P24-C packaging generation without generating titles/thumbnails in P24-A.
   - `title_tone`: `INFORMATIVE`, `INTRIGUING`, `BOLD`, `MINIMAL`.
   - `title_length_tendency`: `CONCISE`, `BALANCED`, `DESCRIPTIVE`.
   - `thumbnail_density`: `MINIMAL`, `BALANCED`, `DETAILED`.
   - `thumbnail_text_policy`: `NO_TEXT`, `MINIMAL`, `HEADER_ONLY`, `EXPRESSIVE`.
   - `description_style`: `CONCISE`, `STRUCTURED`, `COMPREHENSIVE`.
   - `chapter_style`: `CLEAN_TOPIC`, `TIME_BASED`, `DESCRIPTIVE`.
   - `metadata_voice`: Editorial voice override or inheritance.
   - `allow_curiosity_gap`: Bounded intrigue permitted only when non-misleading.

9. **Content Pillars (`ContentPillar`)**:
   - Structured themes with `pillar_id`, `name`, `description`, `priority_weight` (0.0 to 1.0), `allowed_subtopics`, and `exclusions`.

10. **Avoid Patterns (`AvoidPatterns`)**:
    - Explicit anti-patterns: `sensationalized_claims`, `repetitive_hooks`, `excessive_memes`, `overactive_camera`, `constant_sfx`, `generic_cta`, `overloaded_thumbnails`, `prohibited_phrases`.

11. **Format Overrides & Deterministic Resolution (`FormatOverride`, `ResolvedChannelDNA`)**:
    - Precedence order:
      1. Global Hard Constraints
      2. Format-specific Hard Constraints (accumulated union)
      3. Format-specific Soft Preferences
      4. Global Soft Preferences
    - Hard constraints can never be overridden or loosened by format-specific preferences.

### Immutability, Versioning, and Single-Current Invariant

- `ChannelDNARevision` is strictly immutable once created.
- `ChannelDNARevisionService` creates new revisions with atomic promotion:
  1. Validates candidate schema and consistency via `ChannelDNAValidator`.
  2. Queries current max revision number within a transaction.
  3. Demotes any existing `is_current = True` revision to `is_current = False`.
  4. Inserts new immutable revision with `revision_number = max + 1` and `is_current = True`.
  5. Updates `channels.dna` snapshot with the new configuration.
- Backward compatibility: Legacy v1 revisions lack v2 sub-models; Pydantic defaults provide deterministic v1 interpretation without requiring retroactive migration or data loss.

### P24 Seams and Future Downstream Phases

- **P24-B Creative Style Director**: Consumes `ResolvedChannelDNA` to synthesize concrete visual styles, color palettes, and typographic scales tailored to the channel.
- **P24-C Packaging**: Consumes `ResolvedPackagingSpec` (via `channel_dna.resolve_packaging(...)`) to generate titles, descriptions, chapters, and thumbnail briefs.
- **P24-D Creative QA**: Validates generated narrative, visual, audio, and packaging artifacts against the channel's `HardConstraints` and `AvoidPatterns`.
