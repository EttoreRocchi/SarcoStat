"""Import weight of the package and the console entry point (AC-P1, AC-P2).

The numerical core and the file formats load numpy only: scipy is imported lazily by the few
functions that need it, and nothing from the command-line or simulation stacks is loaded. The
console entry point needs the standard library only.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from sarcostat import __version__
from sarcostat.cli import _entry

ROOT = Path(__file__).resolve().parents[1]

HEAVY = ("pingouin", "matplotlib", "pandas", "seaborn", "typer", "rich", "yaml", "PIL", "tifffile")


def _loaded_roots(statement: str) -> set[str]:
    """Run ``statement`` in a fresh interpreter and return the top-level modules it loaded."""
    code = f"import json, sys\n{statement}\nprint(json.dumps(sorted(sys.modules)))"
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=ROOT
    )
    return {name.split(".")[0] for name in json.loads(result.stdout.strip().splitlines()[-1])}


@pytest.mark.needs_cpython
def test_core_and_io_load_numpy_only():
    roots = _loaded_roots(
        "import sarcostat.core, sarcostat.io, sarcostat.io.annotations, "
        "sarcostat.io.provenance, sarcostat.io.study"
    )
    assert "numpy" in roots
    assert not roots & {*HEAVY, "scipy"}


@pytest.mark.needs_cpython
def test_console_entry_loads_standard_library_only():
    roots = _loaded_roots("import sarcostat.cli, sarcostat.cli._entry")
    assert not roots & {*HEAVY, "numpy", "scipy", "click"}


@pytest.mark.needs_cpython
def test_cli_app_loads_without_the_simulation_stack():
    pytest.importorskip("typer")
    roots = _loaded_roots("import sarcostat.cli.app")
    assert {"typer", "rich"} <= roots
    assert not roots & {"pingouin", "matplotlib", "pandas", "seaborn", "yaml", "PIL", "tifffile"}


@pytest.mark.parametrize("argv", [["--version"], ["version"]])
def test_version_needs_no_extra(monkeypatch, capsys, argv):
    monkeypatch.setitem(sys.modules, "typer", None)
    monkeypatch.setitem(sys.modules, "rich", None)
    assert _entry.main(argv) == 0
    assert capsys.readouterr().out == f"sarcostat {__version__}\n"


@pytest.mark.parametrize("argv", [["analyze", "x"], ["gui"], [], ["--help"]])
def test_commands_without_the_cli_extra_name_it(monkeypatch, capsys, argv):
    monkeypatch.setitem(sys.modules, "typer", None)
    assert _entry.main(argv) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == _entry.CLI_HINT + "\n"
    assert 'pip install "sarcostat[cli]"' in captured.err


@pytest.mark.parametrize("blocked", ["pandas", "matplotlib", "seaborn", "yaml", "typer"])
def test_sim_without_the_sim_extra_names_it(monkeypatch, capsys, blocked):
    monkeypatch.setitem(sys.modules, blocked, None)
    assert _entry.main(["sim", "chain"]) == 1
    assert capsys.readouterr().err == _entry.SIM_HINT + "\n"


@pytest.mark.needs_sim
def test_entry_runs_the_typer_application(capsys):
    pytest.importorskip("typer")
    assert _entry.main(["--help"]) == 0
    assert "sim" in capsys.readouterr().out
    assert _entry.main(["sim", "--help"]) == 0
    assert "chain" in capsys.readouterr().out
    assert _entry.main(["no-such-command"]) == 2


def _pyproject() -> dict:
    """Return the parsed ``pyproject.toml`` of the source tree."""
    try:
        import tomllib
    except ModuleNotFoundError:
        tomllib = pytest.importorskip("tomli")
    path = ROOT / "pyproject.toml"
    if not path.is_file():
        pytest.skip("pyproject.toml is not part of this tree")
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _requirement_names(name: str) -> set[str]:
    """Return the distribution names listed in the requirements file ``name``."""
    lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
    names = set()
    for line in lines:
        line = line.strip()
        if line:
            names.add(line.split(";")[0].split("[")[0].split("<")[0].split(">")[0].split("=")[0])
    return {entry.strip().lower() for entry in names}


def test_console_script_and_extras_are_declared():
    project = _pyproject()
    assert project["project"]["scripts"] == {"sarcostat": "sarcostat.cli._entry:main"}
    dynamic = project["tool"]["setuptools"]["dynamic"]
    assert dynamic["dependencies"] == {"file": ["requirements.txt"]}
    assert dynamic["optional-dependencies"] == {
        "cli": {"file": ["requirements-cli.txt"]},
        "sim": {"file": ["requirements-cli.txt", "requirements-sim.txt"]},
        "dev": {"file": ["requirements-dev.txt"]},
    }


def test_core_requirements_are_numpy_and_scipy():
    if not (ROOT / "requirements.txt").is_file():
        pytest.skip("requirements files are not part of this tree")
    assert _requirement_names("requirements.txt") == {"numpy", "scipy"}
    assert _requirement_names("requirements-cli.txt") == {"typer", "rich"}
    assert _requirement_names("requirements-sim.txt") == {
        "pandas",
        "matplotlib",
        "seaborn",
        "pyyaml",
    }
    assert "pingouin" in _requirement_names("requirements-dev.txt")
