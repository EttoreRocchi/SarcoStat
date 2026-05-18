"""Typer/rich command-line interface for SarcoStat.

The package exposes a single :data:`app` object configured with:

- ``sarcostat sim chain | sarc | coverage | mde`` - simulation suite.
- ``sarcostat gui`` - launch the interactive Tk annotation window.
- ``sarcostat version`` - prints the installed package version.
"""

from .app import app

__all__ = ["app"]
