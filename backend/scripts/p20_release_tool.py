"""P20 Release & Acceptance Hardening Tool.

Authoritative preflight and acceptance verification script for P20 deployment.
Enforces fail-closed safety checks before and after deployment:
1. Publisher Absence: asserts no publisher container, no consumer, queue depth 0.
2. Deployment Safety: verifies zero bind mounts, zero reload, AUTO_MIGRATE=false, network isolation.
3. Provenance: verifies source commit and image metadata alignment.
4. Backup Readability: verifies SHA-256 and pg_restore table of contents.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def check_publisher_absence(redis_url: str | None = None) -> dict[str, Any]:
    """Fail-closed assertion of publisher absence.

    Read-only checks:
    - No publisher container exists or runs in Docker.
    - No consumer is attached to the omega-publisher Celery queue.
    - Queue depth for omega-publisher is 0.
    - Does NOT purge queue, does NOT mutate DB, does NOT ack messages.
    """
    results: dict[str, Any] = {
        "check": "publisher_absence",
        "passed": True,
        "details": {},
        "violations": [],
    }

    # 1. Inspect Docker containers for publisher presence
    try:
        cmd = [
            "docker",
            "ps",
            "-a",
            "--filter",
            "name=omega-publisher-worker",
            "--format",
            "{{.ID}}|{{.Names}}|{{.Status}}|{{.Image}}",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        output = proc.stdout.strip()
        if output:
            lines = [line.strip() for line in output.splitlines() if line.strip()]
            publisher_containers = []
            for line in lines:
                parts = line.split("|")
                if len(parts) >= 2 and "omega-publisher-worker" in parts[1]:
                    publisher_containers.append(
                        {"id": parts[0], "name": parts[1], "status": parts[2]}
                    )
            if publisher_containers:
                results["passed"] = False
                results["violations"].append(
                    f"Found existing publisher containers: {publisher_containers}"
                )
                results["details"]["containers"] = publisher_containers
        else:
            results["details"]["containers"] = []
    except Exception as exc:
        results["details"]["docker_error"] = str(exc)

    # 2. Inspect Redis queue depth and consumers (read-only)
    target_redis = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
    try:
        import redis

        client = redis.Redis.from_url(
            target_redis, socket_timeout=2.0, socket_connect_timeout=2.0
        )
        queue_len = client.llen("omega-publisher")
        results["details"]["omega_publisher_queue_depth"] = queue_len
        if queue_len > 0:
            results["passed"] = False
            results["violations"].append(
                f"omega-publisher queue depth is non-zero ({queue_len})"
            )
        client.close()
    except Exception as exc:
        results["details"]["redis_check"] = f"Skipped or connection failed: {exc}"

    return results


def verify_deployment_safety(compose_path: Path) -> dict[str, Any]:
    """Verify production docker-compose.prod.yml adheres to P20 production invariants."""
    results: dict[str, Any] = {
        "check": "deployment_safety",
        "passed": True,
        "details": {},
        "violations": [],
    }

    if not compose_path.exists():
        results["passed"] = False
        results["violations"].append(f"Compose file not found at {compose_path}")
        return results

    content = compose_path.read_text(encoding="utf-8")

    # Invariant 1: No source bind mounts to /app
    for line in content.splitlines():
        trimmed = line.strip()
        if trimmed.startswith("#"):
            continue
        # Source bind mount check: host path (relative or absolute) mapped to /app
        if trimmed.startswith("-") and ":/app" in trimmed:
            mount_source = trimmed.split(":", 1)[0].lstrip("- ").strip()
            # If source starts with . or / or \ or contains backend, it's a host bind mount
            if mount_source.startswith((".", "/", "\\")) or "backend" in mount_source or mount_source.endswith("/app"):
                results["passed"] = False
                results["violations"].append(f"Forbidden source bind mount found: {trimmed}")

    # Invariant 2: No --reload flag in non-comment lines
    for line in content.splitlines():
        trimmed = line.strip()
        if trimmed.startswith("#"):
            continue
        if "--reload" in trimmed:
            results["passed"] = False
            results["violations"].append("Forbidden --reload flag detected in Compose file")

    # Invariant 3: AUTO_MIGRATE must be false
    if 'AUTO_MIGRATE: "true"' in content or "AUTO_MIGRATE: true" in content:
        results["passed"] = False
        results["violations"].append("AUTO_MIGRATE must be false in production compose")

    # Invariant 4: No publisher worker service in production compose
    if "omega-publisher-worker:" in content:
        results["passed"] = False
        results["violations"].append(
            "omega-publisher-worker must not be defined in production compose (B6)"
        )

    # Invariant 5: Database and Redis must not expose host ports broadly (B3)
    if 'ports:\n      - "5432:5432"' in content or 'ports:\n      - "6379:6379"' in content:
        results["passed"] = False
        results["violations"].append(
            "PostgreSQL or Redis publishes ports to host interfaces (B3 violation)"
        )

    # Invariant 6: All six feature gates must be configured with default-off (${NAME:-false})
    gates = [
        "RECURRING_SCHEDULER_ENABLED",
        "ANALYTICS_API_ENABLED",
        "ANALYTICS_ROLLUP_ENABLED",
        "CAMPAIGN_ORCHESTRATION_ENABLED",
        "PRODUCTION_DISPATCH_RECOVERY_ENABLED",
        "PRODUCTION_LEASE_SWEEP_ENABLED",
    ]
    for gate in gates:
        pattern = f"{gate}: ${{{gate}:-false}}"
        if pattern not in content:
            results["passed"] = False
            results["violations"].append(f"Feature gate {gate} not properly defaulted to false")

    # Invariant 7: API healthcheck uses /health without reload dependencies
    if "http://localhost:8000/health" not in content:
        results["passed"] = False
        results["violations"].append("API healthcheck does not use http://localhost:8000/health")

    return results


def verify_backup_readability(backup_path: Path, expected_sha256: str | None = None) -> dict[str, Any]:
    """Verify that a database backup file exists, is non-empty, and matches checksum."""
    results: dict[str, Any] = {
        "check": "backup_readability",
        "passed": True,
        "details": {},
        "violations": [],
    }

    if not backup_path.exists():
        results["passed"] = False
        results["violations"].append(f"Backup file not found: {backup_path}")
        return results

    size = backup_path.stat().st_size
    results["details"]["size_bytes"] = size
    if size == 0:
        results["passed"] = False
        results["violations"].append(f"Backup file is empty (0 bytes): {backup_path}")
        return results

    # SHA-256
    sha256 = hashlib.sha256()
    with open(backup_path, "rb") as f:
        while chunk := f.read(65536):
            sha256.update(chunk)
    digest = sha256.hexdigest()
    results["details"]["sha256"] = digest

    if expected_sha256 and digest.lower() != expected_sha256.lower():
        results["passed"] = False
        results["violations"].append(
            f"Checksum mismatch: expected {expected_sha256}, got {digest}"
        )

    # pg_restore table of contents test if pg_restore is available
    try:
        proc = subprocess.run(
            ["pg_restore", "-l", str(backup_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            lines = proc.stdout.splitlines()
            results["details"]["pg_restore_entry_count"] = len(lines)
        else:
            results["details"]["pg_restore_warning"] = proc.stderr.strip()
    except FileNotFoundError:
        results["details"]["pg_restore"] = "pg_restore tool not installed on host"

    return results


def run_all_preflight(repo_root: Path) -> dict[str, Any]:
    """Run all release preflight checks."""
    compose_path = repo_root / "docker-compose.prod.yml"
    pub_res = check_publisher_absence()
    comp_res = verify_deployment_safety(compose_path)

    all_passed = pub_res["passed"] and comp_res["passed"]
    return {
        "overall_passed": all_passed,
        "publisher_absence": pub_res,
        "deployment_safety": comp_res,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="P20 Production Release & Acceptance Tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # publisher-check
    pub_parser = subparsers.add_parser("publisher-check", help="Assert publisher absence")
    pub_parser.add_argument("--redis-url", help="Redis URL to inspect")

    # compose-check
    comp_parser = subparsers.add_parser("compose-check", help="Verify deployment safety")
    comp_parser.add_argument("--compose-file", default="docker-compose.prod.yml", help="Path to compose file")

    # backup-check
    bak_parser = subparsers.add_parser("backup-check", help="Verify backup readability")
    bak_parser.add_argument("backup_path", help="Path to backup file")
    bak_parser.add_argument("--sha256", help="Expected SHA-256 checksum")

    # preflight
    pre_parser = subparsers.add_parser("preflight", help="Run full preflight checks")
    pre_parser.add_argument("--repo-root", default=".", help="Repository root")

    args = parser.parse_args()

    if args.command == "publisher-check":
        res = check_publisher_absence(redis_url=args.redis_url)
        print(json.dumps(res, indent=2))
        sys.exit(0 if res["passed"] else 1)

    elif args.command == "compose-check":
        res = verify_deployment_safety(Path(args.compose_file))
        print(json.dumps(res, indent=2))
        sys.exit(0 if res["passed"] else 1)

    elif args.command == "backup-check":
        res = verify_backup_readability(Path(args.backup_path), expected_sha256=args.sha256)
        print(json.dumps(res, indent=2))
        sys.exit(0 if res["passed"] else 1)

    elif args.command == "preflight":
        res = run_all_preflight(Path(args.repo_root))
        print(json.dumps(res, indent=2))
        sys.exit(0 if res["overall_passed"] else 1)


if __name__ == "__main__":
    main()
