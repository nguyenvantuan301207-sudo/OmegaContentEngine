# ADR-001: Foundation Architecture

**Date**: 2026-08-20
**Status**: Accepted

## Context

OMEGA requires a clean, modular foundation with:
- FastAPI (async) backend
- Celery (sync) worker
- PostgreSQL database
- Redis message broker
- Next.js frontend

## Decisions

### 1. Layered Architecture

```
API → Application → Domain → Infrastructure
```

- **Domain** has zero infrastructure dependencies (pure Python models/enums)
- **Application** orchestrates domain logic with infrastructure
- **Infrastructure** handles database, Redis, Celery connections
- **API** handles HTTP concerns only

### 2. Dual Database Engine Strategy

**Problem**: FastAPI uses async SQLAlchemy (asyncpg). Celery runs tasks
in a synchronous thread/process pool with no running event loop.

**Decision**: Maintain two separate SQLAlchemy engines:

| Module | Engine | Driver | Used By |
|--------|--------|--------|---------|
| `database.py` | `create_async_engine` | asyncpg | FastAPI |
| `database_sync.py` | `create_engine` | psycopg2 | Celery |

**Rationale**:
- Celery's prefork concurrency model does not provide an event loop
- Manually creating event loops inside tasks is fragile
- psycopg2 is the most battle-tested sync PostgreSQL driver
- Clean separation eliminates entire categories of async/sync bugs

**Rejected alternatives**:
- `asyncio.run()` inside Celery tasks — fragile, nested loop issues
- Shared async engine with `run_in_executor` — event loop not guaranteed

### 3. Health Check Design

Two distinct endpoints:

- `GET /health` — **Liveness probe**. Returns `{"status": "ok"}` immediately.
  No dependency checks. Answers: "Is the process alive?"

- `GET /api/v1/system/status` — **Deep readiness check**. Verifies
  PostgreSQL, Redis, and Celery worker connectivity with latency measurements.
  Answers: "Can this instance serve requests?"

**Worker health**: Uses `celery inspect ping` with a bounded timeout
(configurable via `WORKER_HEALTH_TIMEOUT`, default 3s). This verifies
actual Celery worker responsiveness, not merely container process existence.

### 4. Alembic Migration Strategy

- **Development**: Auto-migration on API startup (`AUTO_MIGRATE=true`)
- **Production**: Explicit migration only (`AUTO_MIGRATE=false`)

Auto-migration is dangerous in production because multiple API instances
could race on schema changes. Production deployments must run
`alembic upgrade head` as a single, serialized deployment step.

### 5. Structured Logging

JSON-formatted logs via structlog with correlation fields:

| Field | Source |
|-------|--------|
| `timestamp` | structlog (ISO 8601) |
| `level` | structlog |
| `event` | explicit |
| `service` | config (omega-api / omega-worker) |
| `request_id` | middleware (per-request UUID) |
| `job_id` | explicit (when applicable) |

**Security**: Secrets, credentials, and tokens are never logged.
Error sanitization strips connection strings and sensitive patterns
before persistence or client response.

### 6. Celery Dispatch Failure Handling

If a job is created but Celery dispatch fails:
1. Job is marked `FAILED` (never left as `QUEUED`)
2. Sanitized error message is persisted in `Job.error`
3. Full diagnostic error is logged via structured logging
4. No automatic retry engine (deferred to future task)

### 7. Error Sanitization

`Job.error` never contains raw tracebacks or secret-containing exceptions.
A sanitization function:
- Extracts only the exception type and first line of message
- Redacts patterns that may contain secrets (connection strings, tokens, passwords)
- Truncates oversized messages
- Logs full diagnostic details to structured logs only

### 8. CORS Configuration

Origins are configured via `CORS_ORIGINS` environment variable.
No wildcard (`*`) default. Origins must be explicitly listed.

## Consequences

- Clear separation of async (API) and sync (worker) code
- No fragile event-loop hacking in Celery tasks
- Observable system via structured logging with correlation
- Safe error handling that never leaks secrets
- Foundation ready for future features without architectural changes
