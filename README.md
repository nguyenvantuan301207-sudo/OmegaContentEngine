# OMEGA — Autonomous Content Operating System

Foundation layer for the OMEGA project.

## Architecture

```
Frontend (Next.js)
    ↓
API (FastAPI)
    ↓
Application Layer
    ↓
Domain Layer
    ↓
Infrastructure Layer (PostgreSQL, Redis)

Long-running tasks:
API → Redis → Celery Worker
```

### Layered Architecture

| Layer | Purpose | Dependencies |
|-------|---------|-------------|
| **API** | HTTP endpoints, middleware, error handling | Application |
| **Application** | Business logic orchestration | Domain, Infrastructure |
| **Domain** | Core models, enums, schemas | None |
| **Infrastructure** | Database, Redis, Celery, ORM | External services |

## Services

| Service | Description | Port |
|---------|-------------|------|
| `omega-api` | FastAPI backend | 8000 |
| `omega-web` | Next.js frontend | 3000 |
| `omega-postgres` | PostgreSQL 16 | 5432 |
| `omega-redis` | Redis 7 | 6379 |
| `omega-worker` | Celery worker | — |

## Quick Start

### Prerequisites

- Docker
- Docker Compose

### Setup

```bash
# 1. Clone and enter the project
cd OmegaContentEngine

# 2. Copy environment configuration
cp .env.example .env

# 3. Start all services
docker compose up --build

# 4. Verify
curl http://localhost:8000/health
curl http://localhost:8000/api/v1/system/status
open http://localhost:3000
```

### Stop

```bash
# Stop services (preserve data)
docker compose down

# Stop services and remove all data
docker compose down -v
```

## API Endpoints

### Health Check — Liveness

```
GET /health
```

Lightweight liveness probe. Returns `{"status": "ok"}` immediately.
No dependency checks. Use this for container orchestrator liveness probes.

### System Status — Deep Readiness

```
GET /api/v1/system/status
```

Deep readiness check. Verifies PostgreSQL, Redis, and Celery worker
connectivity. Use this for readiness probes and monitoring dashboards.

Returns per-component status with latency measurements.
Overall status is `"healthy"` only if all checks pass.

### System Info

```
GET /api/v1/system/info
```

Returns application metadata: name, version, environment.
Lightweight, no dependency checks.

### Jobs

```
POST /api/v1/jobs/test     # Create a test job
GET  /api/v1/jobs/{job_id}  # Get job status
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `ENVIRONMENT` | Runtime environment | `development` |
| `LOG_LEVEL` | Logging level | `INFO` |
| `AUTO_MIGRATE` | Run Alembic on startup | `true` |
| `DATABASE_URL` | Async PostgreSQL URL | (see .env.example) |
| `DATABASE_URL_SYNC` | Sync PostgreSQL URL | (see .env.example) |
| `REDIS_URL` | Redis connection URL | (see .env.example) |
| `CORS_ORIGINS` | Allowed CORS origins | `http://localhost:3000` |
| `NEXT_PUBLIC_API_BASE_URL` | Frontend API base URL | `http://localhost:8000` |

## Database Migrations

### Local Development

Migrations run automatically on API startup when `AUTO_MIGRATE=true` (default).

### Production

⚠️ **Auto-migration must be disabled in production** (`AUTO_MIGRATE=false`).

Run migrations explicitly as a single deployment step:

```bash
alembic upgrade head
```

Running migrations from every API instance concurrently can cause
race conditions and schema corruption.

## Running Tests

```bash
# Unit tests (no infrastructure needed)
docker compose exec omega-api pytest tests/unit -v

# Integration tests (requires running services)
docker compose exec omega-api pytest tests/integration -v

# Smoke test (full stack required)
docker compose exec omega-api pytest tests/smoke -v

# All tests
docker compose exec omega-api pytest -v

# Code quality
docker compose exec omega-api ruff check src/ tests/
docker compose exec omega-web npm run lint
```

## Data Persistence

PostgreSQL data is stored in a named Docker volume (`omega-postgres-data`).

- `docker compose down` — data persists
- `docker compose down -v` — data removed

## Tech Stack

- **Backend**: Python 3.12, FastAPI, SQLAlchemy (async), Alembic
- **Frontend**: Next.js 15, TypeScript, React 19
- **Database**: PostgreSQL 16
- **Queue**: Redis 7, Celery 5
- **Containerization**: Docker Compose
- **Testing**: Pytest, httpx
- **Code Quality**: Ruff (backend), ESLint + Prettier (frontend)
- **Logging**: structlog (JSON structured)
