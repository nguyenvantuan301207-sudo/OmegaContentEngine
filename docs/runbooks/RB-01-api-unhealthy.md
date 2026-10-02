# RB-01: API Unhealthy / Liveness Failure

---

## 1. Symptoms
- `/health` or `/health/live` probe fails or times out.
- Container orchestrator reports `omega-api` container as unhealthy or restarts it.
- HTTP clients receive 502/504 Bad Gateway from ingress proxy.

## 2. Signals
- Prometheus metric `omega_http_requests_total` rate drops to zero.
- Docker healthcheck exits with code 1.
- In-container Python asyncio event loop is blocked or unhandled memory error occurred.

## 3. Read-Only Diagnosis
1. Inspect container logs:
   ```bash
   docker logs --tail 200 omega-api
   ```
2. Check if process is running inside container:
   ```bash
   docker top omega-api
   ```
3. Test local liveness curl inside container:
   ```bash
   docker exec omega-api python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health/live').read())"
   ```

## 4. Safe Recovery
1. If the container process hung due to memory starvation, check host memory:
   ```bash
   docker stats --no-stream omega-api
   ```
2. Restart the API container:
   ```bash
   docker restart omega-api
   ```
3. Verify `/health/live` returns HTTP 200 within 15s.

## 5. Escalation
If `omega-api` fails to start after restart, check for syntax or configuration import errors in logs. Escalate to Engineering on-call.

## 6. What NOT to Mutate Manually
- DO NOT disable the liveness probe in Docker Compose to "mask" the outage.
- DO NOT edit environment variables directly inside the running container.
