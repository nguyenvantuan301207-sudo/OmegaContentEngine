"""Maintenance CLI for Dirty Data Lifecycle Disposition.

Applies authorized lifecycle transitions to the 125 dirty manifest entries:
- PlatformAccounts: ACTIVE -> REVOKED
- PublishIntents: DRAFT/APPROVED -> CANCELLED
- Tasks: READY -> CANCELLED
- Missions: RUNNING -> CANCELLED
- CredentialVault: Physically retained (ZERO delete, ZERO key_version repair).

Usage:
    # Dry-run evaluation:
    python -m omega.maintenance.dispose_dirty_data --manifest <path> --expected-sha256 <hash> --dry-run

    # Execute atomic disposition:
    python -m omega.maintenance.dispose_dirty_data --manifest <path> --expected-sha256 <hash> --execute
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from omega.application.disposition_service import (
    DirtyDataDispositionService,
    DispositionError,
    DispositionReport,
    DispositionStatus,
)


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="python -m omega.maintenance.dispose_dirty_data",
        description="Production Dirty Data Lifecycle Disposition Maintenance CLI.",
    )
    parser.add_argument(
        "--manifest",
        type=str,
        required=True,
        help="Path to authoritative dirty_data_manifest.json file.",
    )
    parser.add_argument(
        "--expected-sha256",
        type=str,
        default=None,
        help="Expected SHA-256 hash of the authorized manifest file (mandatory for --execute).",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help="Perform transactional lifecycle transitions and commit to DB (defaults to false / dry-run).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Run in read-only audit mode without mutating the database.",
    )
    parser.add_argument(
        "--output-exclusions",
        type=str,
        default=None,
        help="Optional path to output the JSON list of 125 excluded credential vault UUIDs.",
    )
    return parser.parse_args(args)


def format_report(report: DispositionReport) -> str:
    """Format sanitized ASCII report for operator output."""
    lines: list[str] = []
    lines.append("=" * 80)
    lines.append(f"OMEGA DIRTY DATA LIFECYCLE DISPOSITION — {report.mode}")
    lines.append("=" * 80)
    lines.append(f"Status: {report.status.value}")
    lines.append(f"Manifest Path: {report.manifest_path}")
    lines.append(f"Manifest SHA-256: {report.manifest_hash}")
    lines.append(f"Manifest Hash Bound / Matched: {'YES' if report.manifest_hash_match else 'NO'}")
    lines.append("-" * 80)
    lines.append(f"Total Manifest Entries: {report.total_manifest_entries}")
    lines.append(f"Platform Accounts Targeted: {report.accounts_targeted} | Revoked: {report.accounts_revoked}")
    lines.append(f"Publish Intents Targeted: {report.intents_targeted} | Cancelled: {report.intents_cancelled}")
    lines.append(f"Tasks Targeted: {report.tasks_targeted} | Cancelled: {report.tasks_cancelled}")
    lines.append(f"Missions Targeted: {report.missions_targeted} | Cancelled: {report.missions_cancelled}")
    lines.append(f"CredentialVault Rows Retained: {report.credentials_retained} (Zero Deleted)")
    lines.append(f"Audit Records Emitted: {report.audit_records_emitted}")
    lines.append("-" * 80)
    lines.append(f"Zero Deletes Verified: {'YES' if report.zero_deletes_verified else 'NO'}")
    lines.append(f"Zero Provider Calls: {'YES' if report.zero_provider_calls_verified else 'NO'}")
    lines.append(f"Zero Publisher Handoff: {'YES' if report.zero_publisher_handoffs_verified else 'NO'}")
    if report.error_message:
        lines.append(f"Error: {report.error_message}")
    lines.append("=" * 80)
    return "\n".join(lines)


async def main_async(args: argparse.Namespace) -> int:
    """Execute disposition service asynchronously."""
    execute_mode = args.execute and not args.dry_run

    if execute_mode and not args.expected_sha256:
        print(
            "Error: --expected-sha256 is strictly required when running with --execute.",
            file=sys.stderr,
        )
        return 1

    service = DirtyDataDispositionService()

    try:
        report = await service.execute_disposition(
            manifest_path=args.manifest,
            expected_sha256=args.expected_sha256,
            execute=execute_mode,
        )
        print(format_report(report))

        if args.output_exclusions:
            exclusions = service.get_excluded_vault_ids(args.manifest)
            out_p = Path(args.output_exclusions)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_text(json.dumps(exclusions, indent=2), encoding="utf-8")
            print(f"Exported {len(exclusions)} excluded vault IDs to {out_p}")

        if report.status in (DispositionStatus.PLANNED, DispositionStatus.EXECUTED, DispositionStatus.ALREADY_COMPLETED):
            return 0
        return 1

    except DispositionError as exc:
        print(f"Disposition Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Unexpected Error during disposition: {exc}", file=sys.stderr)
        return 1


def main() -> None:
    """Synchronous CLI entrypoint."""
    args = parse_args()
    code = asyncio.run(main_async(args))
    sys.exit(code)


if __name__ == "__main__":
    main()
