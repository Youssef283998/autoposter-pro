#!/usr/bin/env python3
"""Backup helper for AutoPoster Pro."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> None:
    """Create a backup of the AutoPoster data directory."""
    print("AutoPoster Pro — Backup")
    print()

    result = subprocess.run(
        [sys.executable, "-m", "autoposter.cli.main", "backup"],
        capture_output=False,
    )
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
