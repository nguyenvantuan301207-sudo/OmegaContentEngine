# RB-12: Emergency Deployment Rollback Procedure

---

## 1. Symptoms
- Severe regression, unhandled exception loop, or data corruption detected immediately post-deployment.
- `/health/ready` remains in 503 DEGRADED despite service restarts.
- Immediate rollback authorized by Incident Commander.

## 2. Signals
- Multiple RB alerts firing concurrently (`RB-01`, `RB-02`, `RB-06`).
- Deployment error rate > 5% on `/metrics`.

## 3. Read-Only Diagnosis
1. Identify currently running image tag:
   ```bash
   docker inspect omega-api --format '{{.Config.Image}}'
   ```
2. Identify previous known-good image tag (e.g. `omega:1ec61e14`).
3. Check if database migrations were applied during the failed release:
   ```bash
   docker exec omega-postgres psql -U omega -d omega -c "SELECT version_num FROM alembic_version;"
   ```

## 4. Safe Recovery
1. **Container Image Rollback**:
   Re-deploy previous known-good immutable image tag via Compose:
   ```bash
   IMAGE_TAG=<previous_good_sha> docker compose -f docker-compose.prod.yml up -d
   ```
2. **Database State Verification**:
   - If previous image is backward-compatible with deployed schema (e.g. additive schema changes only), keep database intact.
   - If schema compatibility is uncertain, stop and escalate to the incident commander and database owner. Restore a verified snapshot first into a separate recovery database, validate it, and obtain an explicit cutover decision. Do not run pg_restore --clean against the live shared database from this runbook.
3. **Verify Health**:
   Check `/health/live` and `/health/ready` return HTTP 200 on all instances.

## 5. Escalation
Notify Incident Commander and post root-cause analysis to post-mortem channel.

## 6. What NOT to Mutate Manually
- DO NOT run destructive `alembic downgrade` commands without having a verified snapshot backup.
- DO NOT edit running container files directly to test hotfixes during an active rollback.
