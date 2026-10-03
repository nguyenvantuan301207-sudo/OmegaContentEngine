# P21-A Narrative Architecture Specification & Persistence Authority

## 1. Executive Summary & Canonical Flow

P21-A establishes `NarrativePlan` as the authoritative, pre-script story structure domain entity within the Omega Content Engine, backed by first-class PostgreSQL persistence.

Historically, narrative structure was loosely represented inside `ContentOutline` JSON or directly interleaved with final narration prose in `ScriptVersion`. P21-A decouples **story structure (WHAT happens)** from **script prose (HOW it is phrased)**.

The canonical flow is:
```text
Topic / Research
      ↓
Narrative Director (P21-B)
      ↓
NarrativePlan (P21-A Authority)
      ↓
Script / ScriptVersion
      ↓
Storyboard / Production
```

---

## 2. PostgreSQL Authoritative Storage & Schema (Migration 027)

`NarrativePlan` persistence authority is provided by PostgreSQL via Alembic Migration `027_create_narrative_plan_tables.py` (revising `026_create_pipeline_analytics_rollups.py`).

### 2.1 Database Tables & Relational Structure

1. **`narrative_plans`**:
   - `id`: UUID (Primary Key).
   - `content_generation_request_id`: UUID (FK to `content_generation_requests.id`, CASCADE).
   - `topic_candidate_id`: UUID (FK to `topic_candidates.id`, SET NULL, nullable).
   - `research_brief_id`: UUID (FK to `research_briefs.id`, SET NULL, nullable).
   - `channel_dna_revision_id`: UUID (FK to `channel_dna_revisions.id`, RESTRICT).
   - `version`: Integer (default 1, monotonic).
   - `is_current`: Boolean (default True).
   - `supersedes_plan_id`: UUID (FK self-reference to `narrative_plans.id`, SET NULL).
   - `status`: String (`DRAFT`, `VALIDATED`, `APPROVED`, `REJECTED`, `SUPERSEDED`).
   - `format_profile`: String (`SHORT`, `MEDIUM`, `LONG`).
   - `target_duration_seconds`: Integer (> 0).
   - `estimated_duration_seconds`: Integer (>= 0).
   - `schema_version`: Integer (default 1).
   - `metadata`: JSONB.
   - `created_at` / `updated_at`: TIMESTAMPTZ with `clock_timestamp()`.

2. **`narrative_sections`**:
   - `id`: UUID (Primary Key).
   - `narrative_plan_id`: UUID (FK to `narrative_plans.id`, CASCADE).
   - `section_order`: Integer (1-indexed, > 0).
   - `role`: String (`HOOK`, `PROMISE`, `CONTEXT`, `DEVELOPMENT`, `ESCALATION`, `PAYOFF`, `TAKEAWAY`, `CLOSING`, `CTA`).
   - `objective`: String(500).
   - `key_information`: JSONB (list of key factual points).
   - `target_duration_seconds`: Integer (> 0).
   - `target_information_density`: String (`LOW`, `MEDIUM`, `HIGH`).
   - `open_loop_intent`: String(300, nullable).
   - `promise_id`: String(100, nullable).
   - `payoff_reference`: String(100, nullable).
   - `notes`: Text (nullable).
   - `created_at`: TIMESTAMPTZ.

3. **`narrative_grounding_citations`**:
   - `id`: UUID (Primary Key).
   - `narrative_section_id`: UUID (FK to `narrative_sections.id`, CASCADE).
   - `research_brief_id`: UUID (FK to `research_briefs.id`, CASCADE).
   - `claim_id`: UUID (FK to `research_claims.id`, CASCADE, nullable).
   - `evidence_id`: UUID (FK to `claim_evidence.id`, CASCADE, nullable).
   - `source_id`: UUID (FK to `research_sources.id`, CASCADE, nullable).
   - `grounding_type`: String (`FACTUAL`, `BACKGROUND`, `DATA_POINT`, `QUOTE`).
   - `description`: String(500, nullable).
   - `created_at`: TIMESTAMPTZ.

4. **`script_versions.narrative_plan_id`**:
   - Nullable UUID FK linking `script_versions` to `narrative_plans.id` (SET NULL).
   - Indexed via partial index `ix_script_versions_narrative_plan_id` (`WHERE narrative_plan_id IS NOT NULL`).

---

## 3. Database Constraints & Single-Current Invariant

Consistency is guaranteed at the database engine level:

1. **Single-Current Guarantee**:
   ```sql
   CREATE UNIQUE INDEX uq_narrative_plan_single_current
   ON narrative_plans (content_generation_request_id)
   WHERE is_current = true;
   ```
   Ensures that no matter how many concurrent workers or processes operate, at most **one** plan can be current for a given `ContentGenerationRequest`.

2. **Monotonic Version Uniqueness**:
   ```sql
   CONSTRAINT uq_narrative_plan_version
   UNIQUE (content_generation_request_id, version);
   ```

3. **Section Sequence Uniqueness**:
   ```sql
   CONSTRAINT uq_narrative_section_order
   UNIQUE (narrative_plan_id, section_order);
   ```

4. **Promise ID Uniqueness within Plan**:
   ```sql
   CREATE UNIQUE INDEX uq_narrative_section_promise_id
   ON narrative_sections (narrative_plan_id, promise_id)
   WHERE promise_id IS NOT NULL;
   ```

---

## 4. Grounding Relational Integrity

Grounding citations do NOT store unvalidated string UUIDs or duplicate research text. They are normalized relational references to authoritative research entities:
- `research_brief_id` → `research_briefs.id`
- `claim_id` → `research_claims.id`
- `evidence_id` → `claim_evidence.id`
- `source_id` → `research_sources.id`

This ensures that research provenance remains immutable lineage while allowing downstream consumers to query supporting evidence without content drift.

---

## 5. ScriptVersion Lineage Model (Option A: PLAN_ID_ONLY)

To prevent denormalization discrepancies, Option A (`PLAN_ID_ONLY`) was selected:
- `script_versions` persists **only** `narrative_plan_id`.
- Because each `NarrativePlan` revision is an immutable row with a unique UUID, pinning `narrative_plan_id` uniquely identifies the exact revision row.
- Downstream DTOs and API responses expose `narrative_plan_version` via the relationship property `script_version.narrative_plan_version` (or reading `narrative_plan.version`).
- **Legacy Compatibility**: Historical scripts created prior to P21 remain valid with `narrative_plan_id = NULL`. No destructive backfill is required.

---

## 6. Cross-Process Version Locking & Atomic Transactions

`PostgresNarrativePlanRepository` implements atomic revision management using PostgreSQL row-level locks:

For both initial plan creation and revision creation:
1. Begin database transaction.
2. Lock owning `ContentGenerationRequest` parent row:
   ```sql
   SELECT id FROM content_generation_requests WHERE id = :req_id FOR UPDATE;
   ```
3. Load current plans with `FOR UPDATE` to determine monotonic `next_version = max(version) + 1`.
4. Demote all existing current plans:
   `is_current = False`, `status = 'SUPERSEDED'`.
5. Insert new revision model (`version = next_version`, `is_current = True`, `supersedes_plan_id = base_plan.id`).
6. Insert ordered sections and grounding citations.
7. Commit transaction atomically.

This serializes concurrent attempts across distributed Celery workers and API processes without relying on process-local locks, while database constraints serve as the final boundary.

---

## 7. Historical Revision Immutability Contract

Once a revision is superseded or historical (`is_current = False` or `status = 'SUPERSEDED'`):
- Ordinary repository operations are prohibited from mutating narrative content (format profile, target duration, sections, objectives).
- Attempting to mutate content on a superseded plan raises `HistoricalRevisionImmutableError`.
- Any narrative evolution must create a new revision `N + 1`.
- Permitted non-content mutations (e.g. administrative status tags) are strictly segregated from narrative content changes.

---

## 8. Removal of Production File Authority (Fail-Closed Wiring)

`PostgresNarrativePlanRepository` is the sole authorized production repository.
- `InMemoryNarrativePlanRepository` is retained exclusively for isolated unit tests.
- `FileBackedNarrativePlanRepository` is permitted solely for local non-production exploration.
- If `settings.environment == "production"` and a `FileBackedNarrativePlanRepository` is configured or injected, `NarrativePlanService` **fails closed** at initialization with `RuntimeError`.

---

## 9. Downstream Storyboard Lineage Trace

Downstream consumers preserve and propagate narrative lineage:
```text
StoryboardPlan (narrative_plan_id, narrative_plan_version)
      ↑
ScriptVersion (narrative_plan_id, narrative_plan_version)
      ↑
NarrativePlan (id, version)
      ↑
ResearchBrief & TopicCandidate
```
`StoryboardEngine` records `narrative_plan_id` and `narrative_plan_version` into `StoryboardPlan`, providing end-to-end retrospective auditability from final video render back to original research claims.

---

## 10. P21-B Narrative Director Specification

### 10.1 NarrativeDirector Contract
`NarrativeDirectorProtocol` defines the application boundary:
```python
class NarrativeDirectorProtocol(Protocol):
    def generate_candidates(
        self,
        content_request_id: UUID,
        channel_dna: dict[str, Any],
        research_brief: dict[str, Any],
        content_intent: dict[str, Any],
        topic_title: str,
        format_profile: NarrativeFormatProfile,
        target_duration_seconds: int,
        topic_summary: str | None = None,
        candidate_count: int = 3,
    ) -> list[NarrativePlanDraft]: ...
```
The Director produces transient `NarrativePlanDraft` instances and **never persists directly**, preserving authority boundaries with `NarrativePlanService` and `NarrativePlanValidator`.

### 10.2 NarrativeStrategy Taxonomy
Ten typed story structures in `STRATEGY_CATALOG`:
1. `HOW_IT_WORKS`: Technical mechanism deconstruction into sequential functional mechanics.
2. `MYTH_REALITY`: Challenges widespread misconceptions with empirical counter-proofs.
3. `PROBLEM_SOLUTION`: Establishes a critical pain point/bottleneck, evaluates failures, presents the optimal solution.
4. `MYSTERY_REVEAL`: Opens a compelling paradox or anomaly, delays resolution with clues, resolves in a surprising payoff.
5. `QUESTION_ANSWER`: Direct authoritative answer to a fundamental question supported by factual evidence.
6. `CAUSE_EFFECT`: Traces causal mechanisms and cascading downstream consequences.
7. `CHRONOLOGICAL`: Timeline progression, historical origins, milestone events, and modern culmination.
8. `CONTRAST_COMPARISON`: Evaluates trade-offs between competing paradigms or technologies across objective dimensions.
9. `ESCALATION_PAYOFF`: Progressively raises stakes or technical tension until a dramatic breakthrough.
10. `CASE_STUDY`: Analyzes real-world incident/outage postmortems to extract engineering principles.

### 10.3 Layered Strategy Selection
`NarrativeStrategySelector` evaluates strategies across:
1. **Deterministic Eligibility**: Filters out strategies whose bounds disallow the requested format profile.
2. **Topic & Intent Matching**: Regex keyword matching on title/summary and semantic intent signals.
3. **Research Shape Alignment**: Rewards `MYTH_REALITY` and `CONTRAST_COMPARISON` when research briefs contain contradictions; rewards `HOW_IT_WORKS` when central question or title queries system mechanics.
4. **Channel DNA Influence**: Adds bounded preference weight based on pinned `ChannelDNARevision` without overriding research truth.

### 10.4 Candidate Generation & Selection
- Multi-candidate generation produces up to N transient `NarrativePlanDraft` candidates.
- `CandidateSelectionEngine` runs `NarrativePlanValidator` against all candidates. Invalid candidates are disqualified.
- Scored deterministically based on:
  - Grounding coverage (factual claim citations).
  - Promise/payoff loop coherence.
  - Duration fidelity (proximity of section sum to target duration).
- Exactly **one** winning candidate is selected and persisted as the authoritative current revision in PostgreSQL.

### 10.5 Research Grounding & Lineage
- Factual sections (`CONTEXT`, `DEVELOPMENT`, `PAYOFF`) carry explicit `GroundingReference` citations to verified claims in `ResearchBrief`.
- Uncertain claims and unverified evidence are excluded from factual citations.
- Invented IDs are forbidden; citations point directly to existing `ResearchClaim`, `ClaimEvidence`, and `ResearchSource` IDs.

### 10.6 Promise → Payoff Open Loops
- Strategies with `has_open_loop=True` open an explicit viewer contract via `promise_id` in early sections (`HOOK` or `PROMISE`).
- The loop is resolved in a subsequent `PAYOFF` section referencing `payoff_reference = promise_id`.
- The selection engine and validator guarantee no unresolved promises or orphan payoffs.

### 10.7 Fallback & Bounded Retry
- `NarrativePlanningService` encapsulates bounded retries (default 2 attempts) for candidate generation failures (timeouts, schema mismatches).
- If retries are exhausted, the service engages a deterministic fallback strategy (`DeterministicNarrativeDirector`), which constructs a compliant plan guaranteed to pass P21-A validation.

### 10.8 Script Generation Handoff
- Once persisted, `NarrativePlanScriptAdapter.prepare_generation_context` packages the authoritative plan, channel DNA, research brief, and content intent into `ScriptGenerationContext`.
- `map_plan_to_script_outline` translates the plan into structured outlines consumed by script generators, preserving roles and open loops.
- Resulting `ScriptVersion` records `narrative_plan_id` in PostgreSQL.

---

## 11. Extension Seams for P21-C and P21-D

1. **P21-C (Retention & Pacing Intelligence)**:
   - Dynamic narrative tension curves and retention drop-off risk mitigation.
   - Intelligent pacing allocation across section durations based on cognitive load and viewer drop-off analytics.
   - Multi-tier nested curiosity loops for longform profiles.
2. **P21-D (Narrative QA & Policy Verification)**:
   - Automated editorial tone consistency verification against channel DNA.
   - Guardian validation for narrative coherence and claim fidelity prior to script generation.
