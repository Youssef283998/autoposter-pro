#!/usr/bin/env python3
"""Install helper for AutoPoster Pro."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path


def main() -> None:
    """Install AutoPoster Pro and its dependencies."""
    print("AutoPoster Pro — Install Helper")
    print()

    # Check Python version.
    if sys.version_info < (3, 10):
        print("Error: Python 3.10+ is required.")
        sys.exit(1)

    # Install the package.
    print("Installing AutoPoster Pro...")
    subprocess.run([sys.executable, "-m", "pip", "install", "-e", "."], check=True)

    # Create config.
    print()
    print("Creating initial config...")
    subprocess.run([sys.executable, "-m", "autoposter.cli.main", "init"], check=True)

    print()
    print("Installation complete!")
    print()
    print("Next steps:")
    print("  1. autoposter setup telegram   # connect a platform")
    print("  2. autoposter demo             # try the full pipeline")
    print("  3. autoposter post add         # schedule a post")
    print("  4. autoposter run              # start the scheduler")


if __name__ == "__main__":
    main()
