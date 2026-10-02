"""Container-local process liveness independent of DB, Redis and task traffic."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def process_alive(role: str, proc_root: Path = Path("/proc")) -> bool:
    for entry in proc_root.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            args = (entry / "cmdline").read_bytes().split(b"\0")
            if any(b"celery" in arg for arg in args) and role.encode() in args:
                return True
        except (OSError, ValueError):
            continue
    return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=["worker", "beat"])
    raise SystemExit(0 if process_alive(parser.parse_args().role) else 1)
