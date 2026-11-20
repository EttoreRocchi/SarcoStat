"""Tests of ``sarcostat analyze``: the results of an annotation file on the command line."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sarcostat.analysis import analyze_file
from sarcostat.cli import _entry
from sarcostat.cli.app import app
from sarcostat.engine.reports import report_lines
from sarcostat.export import export_file_names, results_csv, results_json
from sarcostat.io.annotations import sha256_file

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "tests" / "io" / "data"
GOLDEN = sorted(DATA.glob("golden_v1_*.json"))
EXAMPLES = ROOT / "examples"
RUNNER = CliRunner()


def _invoke(*args: str):
    return RUNNER.invoke(app, ["analyze", *args])


def test_there_are_golden_files() -> None:
    assert [p.name for p in GOLDEN] == ["golden_v1_gui.json", "golden_v1_study.json"]


@pytest.mark.parametrize("path", GOLDEN, ids=lambda p: p.name)
def test_json_is_the_results_json_byte_for_byte(path: Path) -> None:
    result = _invoke("--json", str(path))
    assert result.exit_code == 0, result.output
    expected = results_json(analyze_file(path))
    assert result.stdout_bytes == expected.encode("ascii")
    assert result.stderr == ""


@pytest.mark.parametrize("path", GOLDEN, ids=lambda p: p.name)
def test_the_console_entry_prints_the_same_text(path: Path, capsys) -> None:
    assert _entry.main(["analyze", "--json", str(path)]) == 0
    assert capsys.readouterr().out == results_json(analyze_file(path))


@pytest.mark.needs_cpython(reason="runs a subprocess; Emscripten has no processes")
@pytest.mark.parametrize("path", GOLDEN, ids=lambda p: p.name)
def test_the_installed_command_writes_the_exact_bytes(path: Path, tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "sarcostat.cli._entry", "analyze", "--json", str(path)],
        capture_output=True,
        check=False,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == results_json(analyze_file(path)).encode("ascii")


@pytest.mark.needs_cpython(reason="runs a subprocess; Emscripten has no processes")
def test_analyze_loads_no_simulation_or_image_stack(tmp_path: Path) -> None:
    code = (
        "import contextlib, io, json, sys\n"
        "from sarcostat.cli import _entry\n"
        "with contextlib.redirect_stdout(io.StringIO()):\n"
        f"    status = _entry.main(['analyze', {str(DATA / 'golden_v1_study.json')!r}])\n"
        "print(json.dumps([status, sorted({m.split('.')[0] for m in sys.modules})]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=tmp_path
    )
    status, roots = json.loads(result.stdout.strip().splitlines()[-1])
    assert status == 0
    assert not set(roots) & {"pandas", "matplotlib", "seaborn", "yaml", "PIL", "pingouin"}


@pytest.mark.parametrize("path", GOLDEN, ids=lambda p: p.name)
def test_csv_is_the_results_csv(path: Path) -> None:
    result = _invoke(str(path), "--csv")
    assert result.exit_code == 0, result.output
    assert result.stdout == results_csv(analyze_file(path))


@pytest.mark.parametrize("path", GOLDEN, ids=lambda p: p.name)
def test_without_a_flag_it_prints_the_report_sentences(path: Path) -> None:
    result = _invoke(str(path))
    assert result.exit_code == 0, result.output
    lines = report_lines(analyze_file(path))
    assert result.stdout == "\n".join(lines) + "\n"
    assert "timestamp" not in result.stdout.lower()


def test_a_file_with_two_images_reports_both() -> None:
    result = _invoke(str(DATA / "golden_v1_study.json"))
    assert result.exit_code == 0, result.output
    headers = [line for line in result.stdout.splitlines() if "(image " in line]
    assert headers == [
        "human_skeletal_muscle_tem_1.jpg (image tem1)",
        "human_skeletal_muscle_tem_3.jpg (image tem3)",
    ]


def test_out_writes_the_exports_and_prints_their_names(tmp_path: Path) -> None:
    path = DATA / "golden_v1_study.json"
    out = tmp_path / "exports"
    result = _invoke(str(path), "--out", str(out))
    assert result.exit_code == 0, result.output
    names = export_file_names(path.stem)
    expected = [names["results_json"], names["results_csv"], names["provenance"]]
    assert result.stdout.splitlines() == expected
    assert sorted(p.name for p in out.iterdir()) == sorted(expected)
    results = analyze_file(path)
    assert (out / names["results_json"]).read_text(encoding="utf-8") == results_json(results)
    assert (out / names["results_csv"]).read_text(encoding="utf-8") == results_csv(results)
    sidecar = json.loads((out / names["provenance"]).read_text(encoding="utf-8"))
    assert sidecar["outputs"] == expected[:2]
    assert sidecar["provenance"]["files"][0] == {
        "file_name": path.name,
        "sha256": sha256_file(path),
    }
    for exported in out.iterdir():
        text = exported.read_text(encoding="utf-8")
        assert str(tmp_path) not in text and str(ROOT) not in text


def test_out_with_one_flag_writes_that_export_only(tmp_path: Path) -> None:
    path = DATA / "golden_v1_gui.json"
    result = _invoke(str(path), "--json", "--out", str(tmp_path))
    assert result.exit_code == 0, result.output
    names = export_file_names(path.stem)
    assert result.stdout.splitlines() == [names["results_json"], names["provenance"]]


def test_image_dir_checks_the_images(tmp_path: Path) -> None:
    path = DATA / "golden_v1_study.json"
    result = _invoke(str(path), "--image-dir", str(EXAMPLES))
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    result = _invoke(str(path), "--image-dir", str(tmp_path))
    assert result.exit_code == 0, result.output
    warnings = result.stderr.splitlines()
    assert len(warnings) == 2 and all("was not found" in w for w in warnings)


def _one_error_line(result) -> str:
    assert result.exit_code == 2, result.output
    assert result.stdout == ""
    lines = result.stderr.splitlines()
    assert len(lines) == 1 and lines[0].startswith("sarcostat analyze: "), result.stderr
    assert "Traceback" not in result.output
    line: str = lines[0]
    return line


def test_errors_exit_2_with_one_line(tmp_path: Path) -> None:
    assert "no such file" in _one_error_line(_invoke(str(tmp_path / "absent.json")))
    assert "z_lines" in _one_error_line(_invoke(str(DATA / "invalid" / "chain_one_z_line.json")))
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert "not valid JSON" in _one_error_line(_invoke(str(broken)))
    golden = str(DATA / "golden_v1_gui.json")
    assert "--json or --csv" in _one_error_line(_invoke(golden, "--json", "--csv"))
    assert "does not exist" in _one_error_line(
        _invoke(golden, "--image-dir", str(tmp_path / "none"))
    )
    (tmp_path / "file").write_text("x", encoding="utf-8")
    assert "is a file" in _one_error_line(_invoke(golden, "--out", str(tmp_path / "file")))
    images = tmp_path / "images"
    images.mkdir()
    (images / "human_skeletal_muscle_tem_3.jpg").write_bytes(b"not the image")
    assert "SHA-256" in _one_error_line(_invoke(golden, "--image-dir", str(images)))


def test_analyze_is_listed_in_the_help() -> None:
    result = RUNNER.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "analyze" in result.stdout
