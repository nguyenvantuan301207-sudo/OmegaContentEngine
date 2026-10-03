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

- **P24-B Creative Style Director**: Consumes `ResolvedChannelDNA` and production context to synthesize a production-specific `CreativeStylePlan`.
- **P24-C Packaging**: Consumes `PackagingStyleHints` and `ResolvedPackagingSpec` to generate titles, descriptions, chapters, and thumbnail briefs.
- **P24-D Creative QA**: Validates generated narrative, visual, audio, and packaging artifacts against the channel's `HardConstraints` and `AvoidPatterns`.

## P24-B Creative Style Director

`CreativeStyleDirector` is the coordination layer that answers: *How should THIS production express the pinned Channel DNA?*

### Authority Boundaries

- Canonical identity authority remains strictly `ChannelDNARevision`.
- `CreativeStylePlan` is **derived**, **production-specific**, **recomputable**, and **pinned to one ChannelDNARevision**.
- It never becomes a second editable channel root.
- It does not rewrite `NarrativePlan`, emit raw FFmpeg commands, generate titles or thumbnails, publish media, or perform final brand QA.

### Hard Constraint Propagation & Soft Preference Resolution

- **Hard Constraints**: Channel DNA v2 hard constraints (`no_fabricated_claims`, `no_misleading_clickbait`, `no_unsupported_medical_claims`, `no_profanity`, custom prohibitions) are strictly copied into `hard_constraints_binding`. They can never be weakened or overridden by format adaptations or creative choices.
- **Soft Preferences**: Evaluated contextually against format and production needs. For example, `prefer_cinematic_visuals` sets composition character to `CINEMATIC` while keeping graphic complexity bounded.

### Modality Directions

1. **Narrative Style (`NarrativeStyleDirection`)**:
   - Synthesizes `hook_intensity`, `context_depth`, `explanation_density`, `editorial_energy`, `payoff_emphasis`, `cta_intensity`, `formality`, `technicality`, `expressiveness`.
   - Clamps hook intensity when truthful, non-sensational rules are binding.
2. **Visual Style (`VisualStyleDirection`)**:
   - Derives `visual_density`, `evidence_emphasis`, `b_roll_tendency`, `diagram_tendency`, `document_treatment`, `comparison_treatment`, `motion_intensity`, `camera_restraint`, `transition_restraint`, `composition_character`.
3. **Camera Style (`CameraStyleDirection`)**:
   - Emits bounded camera intent (`STATIC_RESTRAINED`, `SUBTLE_MOTION`, `MODERATE_EDITORIAL_MOTION`, `ENERGETIC_MOTION`) and energy ceiling for P22-B without emitting renderer transforms.
4. **Graphic Style (`GraphicStyleDirection`)**:
   - Coordinates P22-C explanatory diagrams (`graphic_mode`, `annotation_density`).
5. **Audio Style (`AudioStyleDirection`)**:
   - Governs music usage tendency, target energy, energy bounds, vocal policy (`INSTRUMENTAL_ONLY`), SFX density, and silence tolerance.

### Cross-Modal Coherence & Style Continuity

- **Cross-Modal Coherence**: Evaluates relationships across modalities. Detects and flags conflicting combinations:
  - `CALM_NARRATION_HYPERACTIVE_CAMERA`: Calm voice paired with energetic camera motion.
  - `CALM_NARRATION_OVERACTIVE_AUDIO`: Calm voice paired with overpowered music or dense SFX.
  - `HIGH_ENERGY_HOOK_INERT_VISUALS`: High energy hook paired with static, low-density visuals.
  - `HARD_CONSTRAINT_STYLE_VIOLATION`: Any style hint that contradicts binding hard constraints.
- **Creative Intensity Model**: Normalizes modal energies (`narrative_energy`, `visual_energy`, `camera_energy`, `music_energy`, `sfx_energy`, `graphic_density`) into bounded [0.0, 1.0] scales.
- **Creative Arc (`CreativeArc`)**: Maps progressive style evolution across sections (`HOOK` -> `CONTEXT` -> `DEVELOPMENT` -> `ESCALATION` -> `PAYOFF` -> `TAKEAWAY` -> `CLOSING`), tracking justified escalation vs accidental drift (`StyleDriftCode`).
- **Bounded Variation Without Brand Drift**: Variations adapt deterministically to active content pillar, format profile (`SHORT`, `MEDIUM`, `LONG`), and narrative complexity.
- **Inspectable Provenance**: Every major stylistic adaptation is logged in `style_rationale` entries.

### Handoff Projections

- **P21 Handoff (`.to_p21_style_projection()`)**: Exposes voice dimensions, hook intensity, context depth, and narrative energy.
- **P22 Handoff (`.to_p22_style_projection()`)**: Exposes visual density, camera motion intent, max camera energy, transition restraint, and document treatment.
- **P23 Handoff (`.to_p23_style_projection()`)**: Exposes music tendency, energy ranges, vocal policy, SFX density, and restraint flags.
- **P24-C Packaging Seam (`.to_p24c_packaging_hints()`)**: Exposes title tone, title restraint, length tendency, thumbnail density, thumbnail text policy, and zero-tolerance clickbait constraints without generating text or images.
- **P24-D QA Context (`.to_p24d_qa_context()`)**: Exposes full lineage, binding constraints, coherence findings, and consistency findings for final acceptance evaluation.


## P24-C Packaging Engine

The Packaging Engine derives the complete, publication-ready, and truthful packaging bundle for an Omega production without publishing media.

### Single Authority Chain

The authority chain strictly flows in one direction:
`ChannelDNARevision` (Channel identity authority)
  ↓
`CreativeStylePlan` (Production-specific creative direction)
  ↓
`PackagingPlan` (Derived packaging artifact)

P24-C preserves and never mutates `ChannelDNARevision`, `CreativeStylePlan`, `NarrativePlan`, or `ScriptVersion`.

### PackagingPlan Domain Model

`PackagingPlan` is the canonical derived artifact produced by P24-C:
- `packaging_plan_id`: Unique derived bundle UUID.
- `channel_dna_revision_id` & `creative_style_plan_id`: Strict provenance lineage.
- `title_candidates`: At least 4 meaningfully different candidates across distinct strategies.
- `selected_title`: Deterministically selected provisional title.
- `thumbnail_concepts`: Distinct conceptual specifications with composition and visual hierarchy.
- `selected_thumbnail`: Primary selected concept.
- `physical_thumbnail_artifact`: Validated 1280x720 RGB PNG image file on disk.
- `description`: Structured, grounded outline with takeaways, timestamps, and optional CTA.
- `chapters`: Monotonic timeline chapters reconciled with physical video duration.
- `tags`: Bounded relevant keywords derived from content.
- `cta_metadata`: Conforming to ChannelDNA CTA preferences (`NO_CTA`, `SOFT_CTA`, `STANDARD_CTA`).
- `attribution_block`: Preserves audio and asset attribution obligations from P23-D.
- `provenance`: Generation metadata, provider details, renderer version, candidate rationale.
- `validation_findings`: Diagnostic structural and coherence findings.

### Title Candidate Generation, Grounding & Clickbait Guard

1. **Candidate Strategies**:
   - `FACTUAL`: Rigorous summary directly anchored to content objectives.
   - `EXPLAINER`: Structural breakdown ("How X Works: The Mechanism Behind Y").
   - `CURIOSITY`: Grounded inquiry stimulating audience curiosity without deception.
   - `QUESTION`: Engaging interrogative exploring core premise.
   - `OUTCOME`: Direct finding or payoff presentation.
   - `CONTRAST`: Comparison between conventional consensus and empirical evidence.
2. **Grounding Verification**:
   - Every title claim must trace back to `NarrativePlan`, `ScriptVersion`, or research evidence.
   - Detects and rejects:
     - `TITLE_UNSUPPORTED_CLAIM`: Facts absent from content.
     - `TITLE_NUMERIC_MISMATCH`: Numbers or percentages not present in grounded context.
     - `TITLE_EXAGGERATION`: Unsupported superlatives ("revolutionizes everything").
     - `TITLE_MISLEADING_CURIOSITY`: Fabricated urgency or deceptive framing.
3. **Clickbait Boundary**:
   - Strictly enforces `HardConstraints.no_misleading_clickbait` and ChannelDNA avoid patterns.
   - Banned phrases: "You Won't Believe", "Shocking", "Mind-Blowing", "Terrifying", "Insane", manufactured controversy, and fake urgency.
4. **Deterministic Ranking & Provisional Selection**:
   - Score = (Clarity * 0.40) + (Specificity * 0.40) + Style Fit - Clickbait Penalty.
   - Ungrounded candidates or candidates with hard constraint violations are disqualified.
   - Stable tie-break: Highest score, then shorter character count, then alphabetical.

### Thumbnail Concept, Composition & Physical Rendering

1. **ThumbnailConcept**:
   - Captures primary subject, secondary subject, visual hierarchy, composition, background treatment, contrast intent, and emotional intensity.
   - Safe areas: 10% margins on all borders; platform badge safe area in the bottom-right corner (X > 75%, Y > 80%) kept free of vital text or subject focus (`ThumbnailSafeZone.LEFT_WEIGHTED`).
2. **Text Policy**:
   - Respects ChannelDNA: `NO_TEXT` or `SHORT_TEXT` (max 3-4 impactful words, <= 30 chars).
   - Rejects full sentence overlays, duplicated titles, or unreadable copy (`THUMBNAIL_TEXT_OVERLOADED`).
3. **Physical Thumbnail Renderer (`ThumbnailRenderer`)**:
   - Generates genuine 1280x720 16:9 RGB PNG images.
   - Composites existing visual assets / frames from P22 when available, or renders cinematic dark gradients with hero subject focus cards and high-contrast text badges.
   - Uses FFmpeg filter chains where available, with an offline deterministic vector/raster fallback engine.
4. **Physical QA Validation**:
   - Verifies file exists on disk, file size > 1,000 bytes.
   - Verifies valid PNG header, IHDR dimensions (1280x720), 8-bit depth, RGB/RGBA color type.
   - Tests pixel standard deviation to ensure the image is not blank or solid.

### Structured Description, Chapters & Metadata

1. **Description Generation**:
   - Premise summary, bulleted key takeaways from payoff sections, timeline timestamps, CTA, and attribution block.
   - Grounded: No fabricated links, claims, credentials, or sponsors.
2. **Chapter Generation & Duration Reconciliation**:
   - Derived strictly from narrative section objectives and physical durations.
   - Strictly monotonic timestamps starting at 00:00.
   - Clamped within final media duration: No overflow past total duration.
   - Adjacent duplicate chapter titles are deduplicated.
   - For `SHORT` format profile, chapter listings are omitted matching platform conventions.
3. **Tags & Keywords**:
   - Bounded (typically 6-12 tags), content-derived.
   - Eliminates keyword stuffing, duplicates, or competitor name hijacking.

### Attribution & CTA Integration

- **Attribution (P23-D)**: Preserves required audio narration and music bed attributions (e.g. Kokoro TTS, Creative Commons music) in the description block.
- **CTA Policy**: Respects channel preferences (`prefer_concise_cta`). If soft CTA is requested, inserts gentle community discussion prompts rather than aggressive "like and subscribe" appeals.

### Packaging Coherence & Promise/Payoff Alignment

- Evaluates semantic consistency across modalities:
  - `TITLE_THUMBNAIL_PROMISE_MISMATCH`: Raised if the thumbnail subject contradicts the title.
  - `TITLE_DESCRIPTION_MISMATCH`: Raised if description diverges from the title premise.
  - `PACKAGING_PAYOFF_MISMATCH`: Raised if packaging promises outcomes not delivered by the video payoff.

### Model Assistance & Bounded Fallback

- **`PackagingModelClient`**: Optional structured proposal client.
- **Validation Pipeline**: Context -> Model Proposal -> Schema Validation -> Grounding Validation -> ChannelDNA Hard Constraints -> CreativeStylePlan Fit -> Ranking.
- **Bounded Retry**: Allows exactly 1 retry with error feedback if proposal fails validation.
- **Deterministic Fallback**: If model is unavailable or proposals fail validation, automatically falls back to `DeterministicPackagingGenerator`.

### Handoff & Platform Boundaries

- **P24-D QA Handoff (`.to_p24d_qa_package()`)**: Exposes complete candidate pool, selected title/thumbnail, physical PNG artifact, description, chapters, metadata, grounding references, and validation findings for final creative acceptance.
- **P25 Publishing Boundary (`.to_p25_publish_payload()`)**: Formats publish-ready candidate payload strictly for P25-A ingestion.
- **No Publishing**: P24-C has zero publishing capability; no live uploads, no API mutation, no scheduling.

