"""Maintenance CLI for Credential Vault Key Rotation.

Usage:
    # Dry-run evaluation:
    python -m omega.maintenance.rotate_vault_keys --target-version 2 --platform-account-id <uuid> --dry-run

    # Execute transactional rotation:
    python -m omega.maintenance.rotate_vault_keys --target-version 2 --platform-account-id <uuid> --execute
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from uuid import UUID

from omega.application.vault_key_rotation import (
    VaultKeyRotationService,
    VaultRotationReport,
)


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        prog="python -m omega.maintenance.rotate_vault_keys",
        description="Canonical Credential Vault Key Rotation Maintenance CLI.",
    )
    parser.add_argument(
        "--target-version",
        type=int,
        required=True,
        help="Destination encryption key version (must exist in configured keyring).",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help="Perform transactional re-encryption and commit to database (defaults to false / dry-run).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Run in read-only verification mode without mutating the database.",
    )
    parser.add_argument(
        "--platform-account-id",
        type=str,
        default=None,
        help="Filter candidate rows by specific platform account UUID.",
    )
    parser.add_argument(
        "--current-version",
        type=int,
        default=None,
        help="Filter candidate rows by specific current key version.",
    )
    return parser.parse_args(args)


def format_report(report: VaultRotationReport) -> str:
    """Format sanitized ASCII report for console output."""
    lines: list[str] = []
    lines.append("=" * 80)
    lines.append(f"OMEGA CREDENTIAL VAULT KEY ROTATION — {report.mode}")
    lines.append("=" * 80)
    lines.append(
        f"Target Version: {report.target_version} | "
        f"Active Configured Version: {report.active_configured_version}"
    )
    lines.append(f"Filter Platform Account: {report.filter_platform_account_id or 'None'}")
    lines.append(f"Filter Current Version: {report.filter_current_version or 'None'}")
    lines.append("-" * 80)
    lines.append(
        f"Evaluated: {report.total_evaluated} | "
        f"Eligible: {report.eligible_count} | "
        f"Rotated: {report.rotated_count} | "
        f"Already Current: {report.already_current_count} | "
        f"Failed: {report.failed_count}"
    )
    lines.append("-" * 80)

    if not report.entries:
        lines.append("No matching CredentialVault entries evaluated.")
    else:
        for entry in report.entries:
            acc_str = "OK" if entry.access_token_decryptable else "FAIL"
            ref_str = (
                "OK"
                if entry.refresh_token_decryptable is True
                else ("FAIL" if entry.refresh_token_decryptable is False else "N/A")
            )
            v_transition = f"v{entry.current_key_version} -> v{entry.target_key_version}"

            status_line = (
                f"[{entry.status.value}] Vault: {entry.vault_id} | "
                f"Account: {entry.platform_account_id} | {v_transition} | "
                f"Access: {acc_str}, Refresh: {ref_str}"
            )
            if entry.error_message:
                status_line += f" | Reason: {entry.error_message}"
            lines.append(status_line)

    lines.append("=" * 80)
    return "\n".join(lines)


async def main_async(args: argparse.Namespace) -> int:
    """Execute maintenance rotation service asynchronously."""
    # Validate platform_account_id if provided
    platform_account_uuid: UUID | None = None
    if args.platform_account_id:
        try:
            platform_account_uuid = UUID(args.platform_account_id)
        except ValueError:
            print(f"Error: Invalid UUID for --platform-account-id: '{args.platform_account_id}'", file=sys.stderr)
            return 1

    # Determine execute mode: explicitly requires --execute and not --dry-run
    execute_mode = args.execute and not args.dry_run

    service = VaultKeyRotationService()
    try:
        report = await service.rotate_keys(
            target_version=args.target_version,
            execute=execute_mode,
            platform_account_id=platform_account_uuid,
            current_version=args.current_version,
        )
        print(format_report(report))
        return 0 if report.failed_count == 0 else 1
    except Exception as exc:
        print(f"Error executing key rotation: {exc}", file=sys.stderr)
        return 1


def main() -> None:
    """Entry point for python -m omega.maintenance.rotate_vault_keys."""
    args = parse_args()
    exit_code = asyncio.run(main_async(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
