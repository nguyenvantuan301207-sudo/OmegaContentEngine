# P25-B — Published Performance Analytics Architecture

## 1. Overview & Phase Goal

P25-B implements the **Performance Analytics** layer that transforms provider performance data into canonical, inspectable, production-linked analytics attached to the exact `PublishReceipt` / external media identity.

```
PublishReceipt / External Media Identity
       ↓
Provider Analytics Adapter (Read-Only)
       ↓
Raw Provider Metrics Snapshot (Immutable Provider Truth)
       ↓
Canonical Metric Normalization (Standard Units & Semantics)
       ↓
Time-Series Performance Snapshot (Historical Lineage)
       ↓
Derived Performance Metrics (Deltas, Velocity, Retention)
       ↓
Analytics Query / Reporting Layer (Inspectable Read Seam)
```

### Core Responsibility Boundary
- **P25-B Answers:** "How did a published production perform?"
- **Does NOT Answer:**
  - Which variant caused improvement? → **P25-C** (experiment attribution / causal comparison)
  - What should Omega change next? → **P25-D** (learning loop / recommendations)

---

## 2. Authority Boundaries

- **P20 Analytics:** Infrastructure and pipeline execution analytics (`PipelineAnalyticsRollup` for render reliability, performance, scheduler reliability, QA quality).
- **P25-B Performance Analytics:** Published content performance analytics (views, watch time, impressions, CTR, retention, engagement, subscriber impact attached to `PublishReceipt`).
- **P25-C Experiment Attribution:** Causal comparison between production variants.
- **P25-D Learning Loop:** Creative recommendations, ChannelDNA parameter updates, model adjustments.

---

## 3. External Media Identity Lineage

Analytics strictly attaches to the exact published provider object:
```
PerformanceSnapshot
  → PublishReceipt
    → PublishIntent
      → MediaArtifact
        → ProductionRequest
          → ChannelDNARevision
```
Analytics is **never** joined by video title, tags, or file name.

---

## 4. Provider Analytics Abstraction

The provider boundary is defined by the abstract read-only interface `PerformanceAnalyticsProvider` in `omega.application.analytics.performance_provider`:
- `fetch_media_metrics(external_media_id, as_of)`
- `fetch_retention_curve(external_media_id)`
- `fetch_channel_context(channel_account_id)`
- `check_media_availability(external_media_id)`

No domain or application service calls vendor SDKs directly. All operations are strictly read-only. Real provider mutations are prohibited.

### Error Taxonomy & Retry Semantics
- **Retryable (`RetryableAnalyticsError`):** Network timeouts, provider 5xx, rate limits, temporary pipeline lag (`TEMPORARY_LAG`). Handled with bounded exponential backoff.
- **Terminal (`TerminalAnalyticsError`):** Invalid credentials, missing scopes, deleted media, invalid media ID. Fails closed immediately without automatic retry.

---

## 5. Metric Semantics & Canonical Units

| Metric | Canonical Unit | Representation | Classification |
|---|---|---|---|
| `views` | Integer count | Whole number (>= 0) | Monotonic Counter |
| `impressions` | Integer count | Whole number (>= 0) | Monotonic Counter |
| `watch_time_seconds` | Seconds (float) | Seconds elapsed (>= 0.0) | Monotonic Counter |
| `average_view_duration_seconds` | Seconds (float) | Average duration (>= 0.0) | Rate / Average |
| `average_percentage_viewed` | Decimal ratio | Range [0.0, 1.0] (e.g. 0.60 = 60%) | Rate |
| `impressions_ctr` | Decimal ratio | Range [0.0, 1.0] (e.g. 0.05 = 5%) | Rate |
| `likes`, `comments`, `shares` | Integer count | Whole number (>= 0) | Monotonic Counter |
| `subscribers_gained`, `subscribers_lost` | Integer count | Whole number (>= 0) | Monotonic Counter |
| `net_subscribers` | Integer count | Gained - Lost | Derived Difference |

**Rate vs Counter Policy:** Monotonic cumulative counters are never summed across temporal snapshots; rates (CTR, average percentage viewed) are never aggregated across time windows.

---

## 6. Data Freshness Policy

Provider analytics processing may lag behind publication. Freshness is modeled explicitly via `DataFreshnessStatus`:
- `FRESH`: Data observed within normal provider processing latency.
- `DELAYED`: Provider analytics pipeline is processing; data is delayed. **Missing recent provider data is NEVER converted to zero views.**
- `STALE`: Data has not been updated within standard freshness window (> 7 days).
- `UNAVAILABLE`: Media has been deleted, privated, or removed by provider.

---

## 7. Counter Anomaly & Reconciliation Handling

Suspicious decreases in cumulative counters (e.g., views dropping from 1,200 to 900) are intercepted by `PerformanceNormalizer.detect_counter_anomalies`:
- Decreases are recorded as `CounterAnomalyRecord` with status `ANOMALY_DETECTED`.
- Suspicious decreases are **never** blindly converted into negative velocity.
- Emits structured event `analytics.counter.anomaly`.

---

## 8. Retention Curve & Derived Retention Metrics

When supported by the provider, audience retention is stored as a `RetentionCurve` of validated monotonic `RetentionPoint` elements:
- `relative_position`: Float in range [0.0, 1.0].
- `retention_ratio`: Float in range [0.0, 1.0].

### Deterministic Derived Metrics:
- `retention_at_50_percent`: Linear interpolation at position 0.50.
- `retention_at_90_percent`: Linear interpolation at position 0.90.
- `early_dropoff_rate`: Difference between start (0.0) and early dropoff milestone (0.10).
- `retention_at_30s`: Interpolated at `30.0 / duration_seconds` if video duration is known.

---

## 9. Delta & Velocity Metrics

`DeltaVelocityMetrics.compute(...)` derives bounded rate-of-change metrics between two comparable snapshots:
- `views_delta` & `views_per_hour`
- `watch_time_delta_seconds` & `watch_time_per_hour_seconds`
- `subscriber_delta`
- **Safeguards:** Requires a valid previous snapshot and strictly positive elapsed time interval (`delta_seconds > 0`). Avoids divide-by-zero or negative-time corruption.

---

## 10. Database Schema & Rollup Integration

### Schema Audit & Rule Compliance
- **Result:** `P25B_SCHEMA_CHANGE_REQUIRED = NO`.
- **Migration:** `MIGRATION_028_CREATED = NO`.
- Existing schema (revisions 026 and 027) contains all required tables:
  - `analytics_assets`: Tracks video asset linked to `publish_intent_id`, `media_artifact_id`, `platform_account_id`.
  - `analytics_provider_snapshots`: Immutable raw HTTP payload store with `snapshot_dedupe_key`.
  - `analytics_windows`: Temporal evaluation window anchors (`LIFETIME`, `CALENDAR_DAY`, relative windows).
  - `analytics_metric_observations`: Normalized immutable observation facts.
  - `analytics_metric_latest_pointer`: Mutable query projection pointer.
  - `analytics_computed_metrics`: Derived and velocity metric history.
  - `analytics_channel_snapshots`: Isolated channel-level aggregate context.

---

## 11. Idempotency & Checkpoint Recovery

- Snapshot deduplication is enforced by SHA-256 hash of `f"snap:{provider}:{external_media_id}:{payload_checksum}"`.
- Replaying an identical fetch payload skips re-insertion and returns the existing snapshot record without creating fake revisions.
- Pagination and sync cursors are tracked via `sync_checkpoint`.

---

## 12. Security & OAuth Secret Boundary

- No access tokens, refresh tokens, bearer headers, or client secrets are ever stored in `analytics_provider_snapshots` or `analytics_metric_observations`.
- Error messages and logging strictly sanitize credential strings.

---

## 13. Read-Only API Seam

Exposed via `omega.api.analytics`:
- `GET /api/v1/analytics/performance/{receipt_id}/latest`: Returns latest snapshot or status (`NO_DATA`, `DELAYED`, `AVAILABLE`, `STALE`, `PROVIDER_UNAVAILABLE`).
- `GET /api/v1/analytics/performance/{receipt_id}/history`: Returns full time-series sequence of snapshots.
- Endpoints are strictly read-only and never trigger external mutations.
