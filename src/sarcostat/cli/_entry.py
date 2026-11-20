"""Console entry point of ``sarcostat``, importable with the standard library only.

``sarcostat --version`` and ``sarcostat version`` print the version without importing typer, so
they work on a core-only install. The ``gui`` command, with any options, prints the address of the
web app (:data:`GUI_URL`), which replaces the desktop window, and exits 0, also on a core-only
install. Every other command runs the Typer application of :mod:`sarcostat.cli.app`. When the
extra a command needs is not installed, one line names the install command and the exit status
is 1: ``sim`` needs ``sarcostat[sim]``, every other command needs ``sarcostat[cli]``.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Sequence

from sarcostat import __version__

GUI_URL = "https://ettorerocchi.github.io/SarcoStat/"
CLI_MODULES = ("typer", "rich")
SIM_MODULES = ("typer", "rich", "yaml", "pandas", "matplotlib", "seaborn")
CLI_HINT = 'sarcostat: this command needs the command-line extra: pip install "sarcostat[cli]"'
SIM_HINT = 'sarcostat: this command needs the simulation extra: pip install "sarcostat[sim]"'


def _missing(modules: Sequence[str]) -> list[str]:
    """Return the names in ``modules`` that cannot be imported."""
    return [name for name in modules if importlib.util.find_spec(name) is None]


def _exit_status(code: object) -> int:
    """Map the code of a :class:`SystemExit` to a process exit status."""
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    print(code, file=sys.stderr)
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    """Run ``sarcostat`` with ``argv`` (default: the process arguments) and return its status."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args in (["--version"], ["version"]):
        print(f"sarcostat {__version__}")
        return 0
    if args and args[0] == "gui":
        print(GUI_URL)
        return 0
    needs_sim = bool(args) and args[0] == "sim"
    if _missing(SIM_MODULES if needs_sim else CLI_MODULES):
        print(SIM_HINT if needs_sim else CLI_HINT, file=sys.stderr)
        return 1
    from sarcostat.cli.app import app

    try:
        app(args=args, prog_name="sarcostat")
    except SystemExit as exc:
        return _exit_status(exc.code)
    return 0


if __name__ == "__main__":
    sys.exit(main())
