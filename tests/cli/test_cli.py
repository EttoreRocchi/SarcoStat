"""Tests for the typer/rich CLI in :mod:`sarcostat.cli.app`."""

from __future__ import annotations

import textwrap
from pathlib import Path

from typer.testing import CliRunner

from sarcostat import __version__
from sarcostat.cli.app import app

_RUNNER = CliRunner()


_SARC_SMOKE_YAML = textwrap.dedent(
    """
    n_simulations: 5
    seed: 0
    n_processes: 1
    target_condition: euploid
    noise_sensitivity:
      noise_levels: [0.0, 10.0]
      n_replicates: 5
      noise_model: isotropic
    boundary_analysis:
      enabled: false
    output_dir: {output_dir}
    save_raw_data: false
    save_figures: false
    figure_dpi: 80
    """
).strip()


def _write_sarc_smoke_yaml(tmp_path: Path) -> Path:
    """Write a small sarc smoke configuration to ``tmp_path``."""
    body = _SARC_SMOKE_YAML.format(output_dir=str(tmp_path / "sarc_out"))
    target = tmp_path / "sarc_smoke.yaml"
    target.write_text(body)
    return target


class TestVersionCommand:
    """``sarcostat version`` prints the installed version."""

    def test_version_command_prints_installed_version(self) -> None:
        """The output ends with the value of ``sarcostat.__version__``."""
        result = _RUNNER.invoke(app, ["version"])
        assert result.exit_code == 0
        assert __version__ in result.stdout


class TestSimGroup:
    """``sarcostat sim`` exposes the four expected subcommands."""

    def test_sim_help_lists_subcommands(self) -> None:
        """Help text mentions every subcommand we registered."""
        result = _RUNNER.invoke(app, ["sim", "--help"])
        assert result.exit_code == 0
        for cmd in ("chain", "sarc", "coverage", "mde"):
            assert cmd in result.stdout


class TestSimSarc:
    """End-to-end smoke test for ``sarcostat sim sarc``."""

    def test_sarc_smoke(self, tmp_path: Path) -> None:
        """The sarc subcommand exits 0 and writes the expected JSON."""
        config_path = _write_sarc_smoke_yaml(tmp_path)
        result = _RUNNER.invoke(app, ["sim", "sarc", "--config", str(config_path)])
        assert result.exit_code == 0, result.output
        runs = list((tmp_path / "sarc_out").glob("run_*"))
        assert runs, "No timestamped run directory created"
        run_dir = runs[0]
        assert (run_dir / "data" / "noise_sensitivity_summary.json").exists()
        assert (run_dir / "data" / "simulation_parameters.json").exists()
