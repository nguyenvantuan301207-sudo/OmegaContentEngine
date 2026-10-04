# P25-C — Experimentation & Causal Attribution Architecture

## 1. Architectural Authority & Flow
Omega maintains strict separation of concerns across the content generation, delivery, and analytics lifecycle:
- **P24**: Creative & Brand Acceptance Authority (evaluates finished creative package against ChannelDNA, packaging rules, and CreativeQA).
- **P25-A**: Publishing Authority (delivers accepted assets to external platforms under audited idempotency and token isolation).
- **P25-B**: Performance Analytics Authority (descriptive analytics: *"What happened?"* ingesting provider snapshots and computing deltas).
- **P25-C**: Experimentation & Causal Attribution Authority (*"Under a valid controlled comparison, what difference can be attributed to the tested variant?"*).
- **P25-D**: Learning & Recommendation Authority (*"What should Omega learn or change?"* - deferred to P25-D; strictly out of scope for P25-C).

```mermaid
graph TD
    A[Accepted Production P24] --> B[Packaging / Creative Variants]
    B --> C[ExperimentDefinition P25-C]
    C --> D[Deterministic Assignment Policy]
    D --> E[Exposure Model]
    E --> F[Publishing Identity P25-A]
    F --> G[PerformanceSnapshots P25-B]
    G --> H[Outcome Observation Window]
    H --> I[ExperimentAnalysis]
    I --> J[AttributionResult]
    J -.-> K[P25-D Future Learning Loop - Inactive]
```

---

## 2. Core Concepts & Boundaries

### 2.1 Experiment Authority
`AttributionService` acts as the single canonical entry point for experiment lifecycle management (`create_experiment`, `validate_experiment`, `start_experiment`, `record_exposure`, `analyze_experiment`, `complete_experiment`, `invalidate_experiment`). In-memory dictionaries (`_cache_*`) operate strictly as ephemeral `TEST_ONLY` / `CACHE_ONLY` structures. All authoritative experiment state is persisted in and queried from the relational database via `ExperimentRepository`.

### 2.2 Variant Semantics & Creative Acceptance Gate
Each variant (`ExperimentVariant`) models controlled differences across dimensions (`TITLE`, `THUMBNAIL`, `TITLE_AND_THUMBNAIL`, `DESCRIPTION`, `PACKAGING_BUNDLE`).
- **Independent Acceptance**: Every variant must independently satisfy all P24 acceptance criteria (`is_accepted_p24 = True` and `creative_qa_status = CreativeQAStatus.PASS`). Unaccepted variants cannot enter an experiment.
- **Treatment Isolation**: For packaging experiments, the underlying video artifact identity (`media_artifact_id`) must remain identical between control and treatment variants. Any variation in the underlying render constitutes an invalid, confounded experiment (`INVALID_EXPERIMENT`).

### 2.3 Assignment Unit & Deterministic Policy
Experiments explicitly declare their experimental unit (`IMPRESSION`, `VIEW`, `TIME_BUCKET`, `PUBLICATION`).
- **Deterministic Allocation**: Assignments use cryptographically salted SHA-256 hashing of the subject identity against a persisted `randomization_seed` and `control_allocation_ratio`. `Math.random()` or unrecorded ephemeral assignments are strictly disallowed.

### 2.4 Exposure Model & Temporal Boundary
An assignment is not an exposure. Deliveries must explicitly record when a subject receives a variant (`ExperimentExposure`).
- Pre-exposure performance leakage is prevented by enforcing `exposure_time <= outcome_time`. Aggregate provider metrics are handled explicitly without fabricating artificial user-level impressions.

### 2.5 Primary Metric Lock
Before transitioning to `RUNNING`, an experiment must lock exactly one `primary_metric` (e.g. `impressions_ctr`, `average_view_duration`, `average_percentage_viewed`). Secondary metrics may be monitored, but cannot replace the primary metric post-hoc to prevent p-hacking.

### 2.6 Data Maturity Gate
P25-C respects P25-B freshness states:
- `DELAYED`: Result remains `WAITING_FOR_MATURITY` / `INSUFFICIENT_DATA`.
- `UNAVAILABLE` or `INVALID`: Experiment is marked `INVALID_EXPERIMENT`.
- An experiment never declares a winner under incomplete or stale data.

### 2.7 Sample Sufficiency & Effect Sizes
- Sample sizes must meet or exceed the predefined `minimum_sample_size` for both control and treatment.
- Effect size calculations include `absolute_difference = treatment_value - control_value` and `relative_lift = absolute_difference / control_value`. Ratios protect against division by zero (returning `None` for undefined lift).

### 2.8 Statistical Inference & Confidence Intervals
- Two-proportion pooled z-tests are applied to rate metrics (e.g. CTR), providing two-tailed p-values alongside unpooled Wald 95% confidence intervals.
- If raw distributions or normality assumptions are not met, the engine returns `INFERENCE_NOT_AVAILABLE` rather than fabricating artificial precision.
- Multiple treatment variants apply Bonferroni corrections ($\alpha / k$).

### 2.9 No Forced Winner Policy
`INCONCLUSIVE` and `NO_MEANINGFUL_DIFFERENCE` are first-class, expected attribution classifications when differences fail to reach statistical significance. Omega never forces a winner without evidence.

### 2.10 Confounding Guard, Overlap Guard & Variant Immutability
- **Confounding Guard**: Mismatched video renders, unaccepted variants, or divergent observation windows trigger an immediate `INVALID_EXPERIMENT` classification.
- **Overlap Guard**: Active experiments manipulating overlapping creative dimensions on the same target scope are deterministically blocked at creation.
- **Variant Immutability**: Variant snapshots are frozen upon `start_experiment`. Any modification to an active variant invalidates the experiment.

### 2.11 Historical Preservation & Replay Idempotency
Completed and mature attribution results (`AttributionResult`) are immutable historical records. Repeatedly analyzing a completed experiment returns the identical `result_id` and attribution facts without creating duplicate analysis rows.

### 2.12 Authority Boundaries: P25-B and P25-D
- **P25-B Input Boundary**: P25-C ingests canonical metric definitions (`CTR`, `watch_time`, `views`) and snapshot freshness states from P25-B without redefining metric semantics.
- **P25-D Output Boundary**: P25-C reports descriptive causal findings (e.g. `TREATMENT_BETTER for impressions_ctr with +24.0% lift`). It **never** mutates ChannelDNA, generates recommendations, or modifies creative strategies. Those responsibilities are strictly deferred to P25-D.

---

## 3. Durable Relational Persistence Architecture (Migration 028)

### 3.1 Single Durable Experiment Authority
Authoritative state does not live in process memory. `AttributionService` delegates all persistence and query operations to `ExperimentRepository`. In-memory dictionaries exist strictly as non-authoritative caches (`_cache_*`).

### 3.2 Durable Revision Model
Experiment configuration history is durable and immutable across revisions:
- **`experiment_roots`**: Stable root identity (`id`), channel ownership (`channel_id`), target scope (`target_scope_type`, `target_scope_id`), operational status (`DRAFT`, `READY`, `RUNNING`, `PAUSED`, `COMPLETED`, `CANCELLED`, `INVALIDATED`), `start_at`, `end_at`, and pointer to `current_revision_id`.
- **`experiment_revisions`**: Immutable revision record pinning:
  - `experiment_root_id`
  - `revision_number` (strictly positive: `>= 1`)
  - `supersedes_revision_id` (chaining historical revisions)
  - `experiment_type`
  - `hypothesis`
  - `primary_metric` & `secondary_metrics`
  - `assignment_policy`
  - `experiment_unit`
  - `minimum_sample_size`
  - `analysis_window_hours`
  - `provenance`

### 3.3 Normalized Variant Authority
Variant membership is canonical and normalized:
- **`experiment_variants`**:
  - `id`: Variant identity
  - `experiment_revision_id`: Pinned to the immutable revision
  - `role`: `CONTROL` or `TREATMENT`
  - `change_dimension`: Single manipulated dimension
  - `media_artifact_id`: Foreign key to `media_artifacts(id)`
  - `title`, `thumbnail_concept_id`, `thumbnail_ref`: Creative snapshots
  - `is_accepted_p24`: Boolean gate
  - `creative_qa_status`: CreativeQA status gate
  - `variant_snapshot_hash`: Deterministic SHA-256 fingerprint of creative payload
  - `provenance`: P24 acceptance and QA evidence snapshot
  - **Database Constraint**: Unique partial index `uq_variant_single_control` (`UNIQUE (experiment_revision_id) WHERE role = 'CONTROL'`) enforces exactly one control variant per revision.

### 3.4 P24 Evidence Snapshotting (No Invented FKs)
Entities such as `PackagingPlan`, `CreativeQAResult`, `CreativeStylePlan`, and thumbnail concepts are ephemeral domain models without backing relational tables. Migration 028 **does not** create dangling foreign keys to non-existent tables. Instead, immutable evidence (acceptance signatures, QA status, reviewer provenance, and SHA-256 content hashes) is snapshotted into `variant_snapshot_hash` and `provenance`.

### 3.5 Target Scope & Normalized Overlap Guard
Every experiment defines an explicit target scope (`target_scope_type`, `target_scope_id`).
- For packaging experiments, `target_scope_type = "MEDIA_ARTIFACT"` and `target_scope_id` pins the underlying video artifact.
- Overlap detection does not use a simplistic channel-wide uniqueness rule. Instead, creative dimensions are normalized in `experiment_revision_dimensions`.
- Active experiments (`status IN ('READY', 'RUNNING')`) are checked for overlapping dimensions on the same target scope. Two experiments on different publications/artifacts proceed concurrently; two experiments altering `TITLE` (or `TITLE_AND_THUMBNAIL`) on the same publication are deterministically rejected.

### 3.6 Durable Exposure Model & Privacy
- **`experiment_exposures`**:
  - `experiment_revision_id` & `variant_id`
  - `exposure_mode`: `INDIVIDUAL`, `AGGREGATE`, or `PROVIDER_NATIVE`
  - `subject_key`: Optional pseudonymous identifier (e.g. SHA-256 hash with salt). Zero raw PII (emails, names, IP addresses) is stored.
  - `sample_count`: Non-negative sample count (`>= 1`)
  - `exposed_at` & `source_lineage`
  - In `AGGREGATE` and `PROVIDER_NATIVE` modes, no synthetic viewer identities are invented.

### 3.7 Analysis Input Lineage
Historical attribution analyses pin the exact P25-B performance data used:
- **`experiment_analysis_inputs`**:
  - `attribution_result_id`: Foreign key to `experiment_attribution_results(id)`
  - `provider_snapshot_id`: Foreign key to `analytics_provider_snapshots(id)`
  - `retrieved_at` & `lineage_metadata`
  - Enables 100% bitwise-reproducible historical re-evaluation.

### 3.8 Attribution Result Identity & Replay Idempotency
- **`experiment_attribution_results`**:
  - Append-only immutable record.
  - Deterministic unique constraint:
    `UNIQUE (experiment_revision_id, control_variant_id, treatment_variant_id, metric, analysis_window, input_lineage_fingerprint)`
  - Replay with identical inputs returns the existing result row without inserting duplicates.
  - Re-evaluation with updated window or newer provider snapshots inserts a new immutable historical row.

---

## 4. Data Growth Policy & Indexing Strategy

| Table | Expected Volume / Rate | Indexing Strategy | Retention / Growth Policy |
| :--- | :--- | :--- | :--- |
| `experiment_roots` | ~1,000 / year | PK (`id`), `idx_experiment_roots_channel` (`channel_id`), `idx_experiment_roots_scope` (`target_scope_type`, `target_scope_id`) | Indefinite retention (canonical business metadata) |
| `experiment_revisions` | ~1,500 / year | PK (`id`), `idx_experiment_revisions_root` (`experiment_root_id`), `uq_experiment_revisions_root_num` | Immutable append-only history |
| `experiment_revision_dimensions` | ~3,000 / year | `idx_exp_dim_lookup` (`experiment_revision_id`, `dimension`) | Cascade deletes with root in test teardown; persistent in prod |
| `experiment_variants` | ~3,000 / year | PK (`id`), `idx_experiment_variants_revision` (`experiment_revision_id`), `uq_variant_single_control` | Immutable variant records |
| `experiment_exposures` (Aggregate) | ~50,000 / year | `idx_exposures_rev_variant` (`experiment_revision_id`, `variant_id`), `idx_exposures_time` (`exposed_at`) | Aggregate mode keeps volume bounded; individual mode requires partitioning if enabled |
| `experiment_attribution_results` | ~5,000 / year | PK (`id`), `idx_exp_results_revision` (`experiment_revision_id`), `uq_exp_attribution_replay_identity` | Append-only historical causal findings |
| `experiment_analysis_inputs` | ~10,000 / year | `idx_exp_analysis_inputs_pair` (`attribution_result_id`, `provider_snapshot_id`) | Normalized snapshot lineage |

---

## 5. Production Boundary & Migration Rule

- **Authorized Scope**: Migration 028 is strictly authorized and applied **ONLY** to development and isolated test databases (`postgresql+asyncpg://omega:omega_isolated_pw@localhost:5433/p20c_final_test`).
- **Production Environment**:
  - `PRODUCTION_DB_REVISION = 026`
  - Production image: `omega:p20-d2-db-hardened-fd26f244`
  - Publisher absent
  - P21–P25 undeployed
  - Zero external platform mutation
  - `P25C_028_PRODUCTION_MUTATED = NO`
