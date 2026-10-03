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

## 10. Extension Seams for P21-B, P21-C, P21-D

1. **P21-B (AI Narrative Director)**:
   - Uses `NarrativePlanService.create_plan()` and `NarrativePlanScriptAdapter.prepare_generation_context()`.
   - The Director will generate `NarrativePlan` instances from research insights before calling script generation.
2. **P21-C (Pacing & Retention Intelligence)**:
   - Extends section-level `target_information_density`, `open_loop_intent`, and duration distributions.
3. **P21-D (Narrative QA & Policy Verification)**:
   - Extends `NarrativePlanValidator` rules and adds Guardian checkpoints for narrative structure prior to script generation.
