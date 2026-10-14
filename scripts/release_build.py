#!/usr/bin/env python3
"""Release build script for AutoPoster Pro."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> None:
    """Build a release: clean, test, lint, typecheck, build wheel."""
    print("AutoPoster Pro — Release Build")
    print()

    steps = [
        ("Clean", [sys.executable, "-m", "pip", "uninstall", "-y", "autoposter"]),
        ("Install dev deps", [sys.executable, "-m", "pip", "install", "-e", ".[dev]"]),
        ("Ruff", [sys.executable, "-m", "ruff", "check", "autoposter", "tests"]),
        ("Mypy", [sys.executable, "-m", "mypy", "autoposter"]),
        ("Pytest", [sys.executable, "-m", "pytest", "tests/", "-q"]),
        ("Build wheel", [sys.executable, "-m", "pip", "wheel", ".", "-w", "dist/", "--no-deps"]),
    ]

    for name, cmd in steps:
        print(f"[{name}]")
        result = subprocess.run(cmd)
        if result.returncode != 0:
            print(f"Failed at step: {name}")
            sys.exit(1)
        print()

    print("Release build complete!")
    print("Artifacts in dist/")


if __name__ == "__main__":
    main()
