# P21-A Narrative Architecture Specification

## 1. Executive Summary & Canonical Flow

P21-A establishes `NarrativePlan` as the authoritative, pre-script story structure domain entity within the Omega Content Engine.

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

## 2. NarrativePlan Authority & Domain Model

- **Location**: `omega.domain.narrative_plan.NarrativePlan`
- **Identity & Provenance**:
  - `id`: Globally unique identifier (`UUID`).
  - `content_generation_request_id`: Owning request identifier (`UUID`).
  - `topic_candidate_id`: Source topic idea (`UUID | None`).
  - `research_brief_id`: Research lineage identifier (`UUID | None`).
  - `channel_dna_revision_id`: Pinned brand identity & voice (`UUID`).
  - `version`: Monotonically increasing revision integer (1, 2, ...).
  - `is_current`: Boolean indicating active status for the owning request.
  - `supersedes_plan_id`: Prior plan revision ID if created via revision.
  - `status`: `DRAFT`, `VALIDATED`, `APPROVED`, `REJECTED`, `SUPERSEDED`.
  - `format_profile`: `SHORT`, `MEDIUM`, `LONG`.
  - `target_duration_seconds`: Total runtime target.
  - `estimated_duration_seconds`: Sum of section durations.
  - `schema_version`: Immutable schema version (currently `1`).
  - `sections`: Ordered sequence of `NarrativeSection` entities.

---

## 3. Canonical Narrative Section Roles

Every `NarrativeSection` fulfills a semantic narrative role:
1. `HOOK`: Captures initial attention and establishes immediate tension/paradox.
2. `PROMISE`: Explicit or implicit viewer contract regarding what will be revealed.
3. `CONTEXT`: Foundational background, history, or problem framing.
4. `DEVELOPMENT`: Progressive unpacking of facts, mechanics, or narrative progression.
5. `ESCALATION`: Crisis point, unexpected complexity, or dramatic tension increase.
6. `PAYOFF`: Resolution of an earlier open loop or viewer promise.
7. `TAKEAWAY`: Distilled insight, principle, or actionable lesson.
8. `CLOSING`: Concluding narrative wrap-up and synthesis.
9. `CTA`: Explicit call to action (optional, restricted to bounds).

Each section carries:
- `objective`: Crisp editorial objective (3–500 characters).
- `key_information`: Factual points to be covered.
- `grounding_references`: Lineage linking section directly to research artifacts.
- `target_duration_seconds`: Allocated time window.
- `target_information_density`: `LOW`, `MEDIUM`, or `HIGH`.
- `promise_id` / `payoff_reference`: Explicit curiosity loop linkages.

---

## 4. Format Profiles & Deterministic Constraints

Format profiles enforce structural guardrails without encoding channel voice (channel voice remains `ChannelDNA` responsibility):

| Parameter | SHORT (Shorts / Reels) | MEDIUM (Explainer / Deep Dive) | LONG (Mini-Doc / Masterclass) |
|---|---|---|---|
| **Target Runtime** | 15s – 60s (Default: 45s) | 180s – 480s (Default: 300s) | 480s – 1800s (Default: 720s) |
| **Section Count** | 3 – 6 | 5 – 14 | 8 – 30 |
| **Required Roles** | `HOOK`, `PAYOFF`, `TAKEAWAY` | `HOOK`, `DEVELOPMENT`, `PAYOFF`, `TAKEAWAY`, `CLOSING` | `HOOK`, `PROMISE`, `CONTEXT`, `DEVELOPMENT`, `ESCALATION`, `PAYOFF`, `TAKEAWAY`, `CLOSING` |
| **Max Hook Runtime**| 10s | 30s | 60s |
| **Duration Tolerance**| ±20% | ±15% | ±15% |
| **CTA Max Count** | 1 | 2 | 3 |

---

## 5. Promise → Payoff Contract

Narrative engagement is maintained through deterministic promise and payoff tracking:
- Any section may declare a curiosity loop via `promise_id` (unique string).
- Any subsequent section may resolve the loop via `payoff_reference` pointing to the `promise_id`.
- **Validation Invariants**:
  - `UNRESOLVED_PROMISE`: Fails if a `promise_id` has no matching payoff section.
  - `ORPHAN_PAYOFF`: Fails if a `payoff_reference` does not match any declared promise.
  - `PAYOFF_BEFORE_PROMISE`: Fails if the payoff appears before or in the same section as the promise.
  - `DUPLICATE_PROMISE_ID`: Fails if the same `promise_id` is declared more than once.
  - `DUPLICATE_PAYOFF_LINK`: Fails if multiple payoffs reference the same promise without explicit multi-resolution authorization.

---

## 6. Source Grounding Lineage

Sections preserve research provenance without copying bulky research text:
- Each `GroundingReference` references:
  - `research_brief_id: UUID`
  - `claim_id: UUID | None`
  - `evidence_id: UUID | None`
  - `source_id: UUID | None`
  - `grounding_type`: `FACTUAL`, `BACKGROUND`, `DATA_POINT`, `QUOTE`
- In strict mode, factual sections (`DEVELOPMENT`, `PAYOFF`, `CONTEXT`) must carry at least one valid research citation.

---

## 7. Versioning & Immutability

- `NarrativePlan` records are append-only.
- Updating an existing plan creates a new revision `version = N + 1` with `supersedes_plan_id` set to the previous plan ID.
- The previous plan is marked `is_current = False` and `status = SUPERSEDED`.
- Generated `ScriptVersion` records explicitly pin `narrative_plan_id` and `narrative_plan_version`, guaranteeing full retrospective reproducibility.

---

## 8. Backward Compatibility & Storyboard Lineage

- **Legacy Scripts**: Historical `ScriptVersion` entities without a `NarrativePlan` remain fully valid (`narrative_plan_id = None`).
- **Downstream Lineage**:
  ```text
  StoryboardPlan (narrative_plan_id, narrative_plan_version)
        ↑
  ScriptVersion (narrative_plan_id, narrative_plan_version)
        ↑
  NarrativePlan (id, version)
        ↑
  ResearchBrief & TopicCandidate
  ```
- Visual rendering and scene generation continue to execute deterministically via `StoryboardEngine` without breaking changes.

---

## 9. Extension Seams for P21-B, P21-C, P21-D

1. **P21-B (AI Narrative Director)**:
   - Uses `NarrativePlanService.create_plan()` and `NarrativePlanScriptAdapter.prepare_generation_context()`.
   - The Director will generate `NarrativePlan` instances from research insights before calling script generation.
2. **P21-C (Pacing & Retention Intelligence)**:
   - Extends section-level `target_information_density`, `open_loop_intent`, and duration distributions.
3. **P21-D (Narrative QA & Policy Verification)**:
   - Extends `NarrativePlanValidator` rules and adds Guardian checkpoints for narrative structure prior to script generation.
