# OMEGA Production Metrics Scrape & Alert Contract (H6)

## 1. Metrics Endpoint Specifications

- **Endpoint**: `GET /metrics`
- **Port**: `8000` (internal network or loopback `127.0.0.1`)
- **Protocol**: HTTP/1.1 (plain HTTP internal to host or reverse proxy TLS)
- **Format**: Prometheus text format (0.0.4)
- **Authentication**: Mandatory HTTP Bearer token in `Authorization: Bearer <METRICS_AUTH_TOKEN>`. Fails closed (HTTP 403) in production if token is absent or invalid.
- **Latency / Performance Bounds**:
  - Response time: < 50ms typical
  - Scrape timeout: 5s
  - Collector cache TTL: 5s (prevents database connection thrashing)
- **Recommended Scrape Interval**: 30s (evaluation interval: 15s)

---

## 2. Minimum Production Alert Rules

```yaml
groups:
  - name: omega_production_alerts
    rules:
      # 1. Service Health & Readiness
      - alert: OmegaServiceDown
        expr: up{job="omega-api"} == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "OMEGA API service is unreachable"

      - alert: OmegaReadinessDegraded
        expr: omega_system_readiness_status != 1
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "OMEGA readiness probe reports degraded state"

      # 2. Celery Beat Staleness
      - alert: OmegaBeatStale
        expr: (time() - omega_beat_last_tick_timestamp_seconds) > 60
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Celery Beat loop lag exceeds 60 seconds"

      # 3. Scheduler Backlog / Stale Occurrences
      - alert: OmegaSchedulerBacklogHigh
        expr: omega_scheduler_backlog_total > 50
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Recurring scheduler backlog exceeds threshold"

      # 4. Analytics Rollup Failures
      - alert: OmegaAnalyticsRollupFailure
        expr: rate(omega_analytics_rollup_failures_total[5m]) > 0
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "Analytics rollup service failed to acquire lock or execute rollup"

      # 5. Worker Lease Expiry
      - alert: OmegaWorkerLeaseExpired
        expr: rate(omega_worker_lease_expired_total[5m]) > 0
        for: 1m
        labels:
          severity: warning
        annotations:
          summary: "Active worker lease expired without renewal (potential hung task)"

      # 6. Dispatch Dead Letters / Exhaustion
      - alert: OmegaDispatchDeadLetters
        expr: rate(omega_dispatch_outbox_dead_letters_total[5m]) > 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Durable dispatch outbox has dead-lettered intents"

      # 7. Telemetry & Dependency Source Failures
      - alert: OmegaTelemetrySourceFailure
        expr: omega_source_health == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Database or Redis probe failed during metrics collection"

      # 8. Publisher Unexpected Presence (Safety Guard)
      - alert: OmegaPublisherUnexpectedPresence
        expr: omega_publisher_active_workers > 0 or omega_publisher_queue_depth > 0
        for: 0m
        labels:
          severity: page
        annotations:
          summary: "Publisher worker or queue messages detected while publishing is disabled"
```
