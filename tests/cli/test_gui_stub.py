"""The ``gui`` command prints the address of the web app; the desktop window is gone.

The console entry answers ``gui`` before Typer is imported, so the stub
works on a core-only install; the Typer application lists ``gui`` in its
help and gives the same answer.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sarcostat.cli import _entry
from sarcostat.cli.app import app

URL = "https://ettorerocchi.github.io/SarcoStat/"
ARGVS = [
    ["gui"],
    ["gui", "--study", "kit"],
    ["gui", "--output", "out"],
    ["gui", "--study", "kit", "--output", "out"],
    ["gui", "--help"],
    ["gui", "anything", "--else"],
]


def test_the_address_is_the_web_app() -> None:
    assert _entry.GUI_URL == URL


@pytest.mark.parametrize("argv", ARGVS, ids=" ".join)
def test_gui_prints_the_address_without_any_extra(monkeypatch, capsys, argv) -> None:
    for name in ("typer", "rich", "click"):
        monkeypatch.setitem(sys.modules, name, None)
    assert _entry.main(argv) == 0
    captured = capsys.readouterr()
    assert captured.out == URL + "\n"
    assert captured.err == ""


@pytest.mark.needs_cpython(reason="runs a subprocess; Emscripten has no processes")
@pytest.mark.parametrize("argv", ARGVS[:3], ids=" ".join)
def test_the_console_entry_prints_one_line_and_loads_no_typer(tmp_path: Path, argv) -> None:
    code = (
        "import sys\nfrom sarcostat.cli import _entry\n"
        f"status = _entry.main({argv!r})\n"
        "assert 'typer' not in sys.modules and 'click' not in sys.modules\n"
        "sys.exit(status)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False, cwd=tmp_path
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout + result.stderr == URL + "\n"


@pytest.mark.parametrize("argv", ARGVS[:4], ids=" ".join)
def test_the_typer_application_gives_the_same_answer(argv) -> None:
    result = CliRunner().invoke(app, argv)
    assert result.exit_code == 0, result.output
    assert result.stdout == URL + "\n"


def test_gui_is_listed_in_the_help() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "gui" in result.stdout and "web app" in result.stdout


def test_the_desktop_package_is_gone() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("sarcostat.gui")
    assert not (Path(_entry.__file__).resolve().parents[1] / "gui").exists()
