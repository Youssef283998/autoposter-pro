"""autoposter version — print version."""

from __future__ import annotations

import typer

from autoposter import __version__


def version_command() -> None:
    """Print the AutoPoster version."""
    typer.echo(__version__)
