"""Command-line interface of SarcoStat.

The console script ``sarcostat`` starts in :mod:`sarcostat.cli._entry`, which needs only the
standard library: ``sarcostat --version`` works on a core-only install. Every other command
runs the Typer application in :mod:`sarcostat.cli.app`, which needs the ``[cli]`` extra
(``sim`` needs ``[sim]``). Nothing is imported here, so importing :mod:`sarcostat.cli` loads
neither typer nor rich.
"""

__all__: list[str] = []
