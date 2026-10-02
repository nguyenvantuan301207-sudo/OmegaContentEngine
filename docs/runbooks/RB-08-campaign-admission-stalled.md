# RB-08: Campaign Admission Stalled / Concurrency Saturation

---

## 1. Symptoms
- Content campaigns in `RUNNING` status do not materialize new missions.
- Campaign items remain in `PENDING` admission state.
- Telemetry counter `omega_campaign_admissions_total{status="CAPACITY_FULL"}` rises rapidly.

## 2. Signals
- `CampaignAdmissionService.admit_next_campaign_item` returns `"CAPACITY_FULL"`.
- `campaign_active >= max_concurrent_missions` or `channel_active >= campaign_channel_max_active_missions`.
- `/ops/status` reports high active missions under `campaigns`.

## 3. Read-Only Diagnosis
1. Query active campaigns:
   ```bash
   docker exec omega-postgres psql -U omega -d omega -c "SELECT id, channel_id, title, status, max_concurrent_missions FROM content_campaigns WHERE status = 'RUNNING';"
   ```
2. Query pending vs admitted items for a campaign:
   ```bash
   docker exec omega-postgres psql -U omega -d omega -c "SELECT campaign_id, admission_state, count(*) FROM content_campaign_items GROUP BY campaign_id, admission_state;"
   ```
3. Check in-flight active missions:
   ```bash
   docker exec omega-postgres psql -U omega -d omega -c "SELECT channel_id, count(*) FROM missions WHERE state IN ('READY', 'RUNNING') GROUP BY channel_id;"
   ```

## 4. Safe Recovery
1. Capacity starvation is expected when concurrent mission limits are reached.
2. Once in-flight missions finish (succeed or fail), capacity is naturally freed.
3. If in-flight missions are hung, inspect mission runbooks.
4. When capacity frees, the periodic sweep `campaign-reconciliation` automatically admits the next item.

## 5. Escalation
If concurrency limit is too restrictive for business goals, update `CAMPAIGN_CHANNEL_MAX_ACTIVE_MISSIONS` in configuration after review.

## 6. What NOT to Mutate Manually
- DO NOT manually update `admission_state` to `ADMITTED` in SQL without a linked `mission_id` binding.
- DO NOT set campaign status directly to `SUCCEEDED` or `FAILED`.
