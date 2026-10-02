# RB-05: Redis Outage / Broker Reconnect Loop

---

## 1. Symptoms
- `/health/ready` returns HTTP 503 with `"redis": {"status": "UNAVAILABLE"}`.
- Celery worker and API logs report broker connection errors.
- Async tasks cannot be enqueued or received.

## 2. Signals
- Redis `PING` probe fails or exceeds 1.0s timeout.
- Worker logs report: `Consumer: Connection to broker lost. Trying to re-establish...`
- Prometheus source gauge `omega_observability_source_up{source="redis"}` is zero.

## 3. Read-Only Diagnosis
1. Check Redis container status:
   ```bash
   docker ps -f name=omega-redis
   docker exec omega-redis redis-cli ping
   ```
2. Check Redis memory usage:
   ```bash
   docker exec omega-redis redis-cli info memory
   ```
3. Check Redis clients:
   ```bash
   docker exec omega-redis redis-cli info clients
   ```

## 4. Safe Recovery
1. If Redis container is stopped, start it:
   ```bash
   docker start omega-redis
   ```
2. Once Redis responds with `PONG`, Celery workers and API will automatically reconnect.
3. Verify `/health/ready` recovers to HTTP 200 within 15 seconds.

## 5. Escalation
If Redis crashes due to OOM (Out Of Memory), inspect memory limit and evict non-essential cache keys.

## 6. What NOT to Mutate Manually
- DO NOT run `FLUSHALL` or `FLUSHDB` — this deletes the Celery message queue and in-flight durable intents.
