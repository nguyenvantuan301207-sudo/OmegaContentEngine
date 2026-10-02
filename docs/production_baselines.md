# OMEGA Production Baselines & Data Quality Disclosures (H7)

## 1. Pinned Feature Defaults & Timing Baselines

The following values are established as reviewed production baselines in `docker-compose.prod.yml`:

| Configuration Parameter | Pinned Baseline | Safety Rationale |
|---|---|---|
| `PRODUCTION_DISPATCH_TIMEOUT_SECONDS` | `30` | Bounds outbox intent transmission window before retry |
| `CAMPAIGN_RECONCILIATION_INTERVAL_SECONDS` | `5` | Ensures tight convergence of Campaign state without database overload |
| `PRODUCTION_LEASE_SWEEP_INTERVAL_SECONDS` | `60` | Reclaims abandoned worker leases within 1 minute |
| `PRODUCTION_LEASE_TIMEOUT_SECONDS` | `300` | 5-minute lease grace period for long-running video render tasks |
| `RECURRING_SCHEDULER_INTERVAL_SECONDS` | `60` | Matches migration 025 hard schema bound (`chk_interval_minimum >= 60`) |
| `RECURRING_SCHEDULER_CATCH_UP_CAP` | `5` | Well within migration 025 hard bound (`chk_max_catch_up_range <= 10`) |
| `ANALYTICS_ROLLUP_INTERVAL_MINUTES` | `60` | Hourly rollup aggregation cadence |
| `ANALYTICS_ROLLUP_LOOKBACK_HOURS` | `168` | 7-day lookback window for late-arriving metrics reconciliation |

---

## 2. Historical Lifecycle Data Quality Disclosure

### Terminal RenderJob Analysis:
- **Total historical terminal render jobs**: 301
- **Render jobs lacking `completed_at`**: 293
- **Render jobs with valid `completed_at`**: 8

### Architectural Rule:
The 293 historical rows were created before the `completed_at` timestamp column was added to the schema.
- **DO NOT** backfill or rewrite these rows with synthetic completion timestamps.
- In analytics aggregations and rollups, these jobs are intentionally classified and reported as **legacy-unattributed**.
- Any acceptance or verification canary comparing source counts to rollup sums must account for legacy-unattributed rows.

### Unmanaged & Unenrolled Historical Rows:
- Historical unmanaged `RUNNING` render jobs: 3
- Legacy unenrolled `QUEUED` render jobs: 19
These rows are preserved as historical compatibility artifacts and are intentionally bypassed by modern managed cohort selectors.
