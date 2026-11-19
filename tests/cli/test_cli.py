"""Tests for the typer/rich CLI in :mod:`sarcostat.cli.app`."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sarcostat import __version__
from sarcostat.cli.app import app

_RUNNER = CliRunner()


_SARC_SMOKE_YAML = textwrap.dedent(
    """
    n_simulations: 5
    seed: 0
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


def _write(tmp_path: Path, name: str, body: str) -> Path:
    target = tmp_path / name
    target.write_text(textwrap.dedent(body).strip().format(out=tmp_path / "out") + "\n")
    return target


class TestSimChain:
    def test_chain_smoke_records_full_parameters(self, tmp_path: Path) -> None:
        config = _write(
            tmp_path,
            "chain.yaml",
            """
            n_simulations: 5
            n_sarcomeres: [5]
            kappa_values: [0, 2]
            output_dir: {out}
            save_raw_data: true
            save_figures: false
            """,
        )
        result = _RUNNER.invoke(app, ["sim", "chain", "--config", str(config)])
        assert result.exit_code == 0, result.output
        data = tmp_path / "out" / "data"
        assert (data / "raw_k0_N5.npz").exists()
        assert (data / "raw_k2_N5.npz").exists()
        params = json.loads((data / "simulation_parameters.json").read_text())
        assert params["length_std"] == 0.25
        assert params["sarcostat_version"] == __version__
        assert params["config_file"] == str(config.resolve())


class TestRigorCommands:
    def test_coverage_smoke(self, tmp_path: Path) -> None:
        config = _write(
            tmp_path,
            "coverage.yaml",
            """
            n_iterations: 3
            n_bootstrap: 20
            kappa_values: [2.0]
            n_values: [10]
            output_dir: {out}
            save_figures: false
            """,
        )
        result = _RUNNER.invoke(app, ["sim", "coverage", "--config", str(config)])
        assert result.exit_code == 0, result.output
        (run_dir,) = (tmp_path / "out").glob("run_*")
        assert (run_dir / "data" / "coverage_results.csv").exists()
        assert (run_dir / "data" / "simulation_parameters.json").exists()
        assert (run_dir / "logs" / "simulation.log").exists()

    def test_mde_smoke(self, tmp_path: Path) -> None:
        config = _write(
            tmp_path,
            "mde.yaml",
            """
            n_replicates_for_sd: 20
            kappa_values: [2.0]
            chain_lengths: [10]
            group_sizes: [25]
            sarcomere_conditions: [euploid]
            annotation_noise_nm: [0.0]
            sarcomere_group_sizes: [25]
            output_dir: {out}
            save_figures: false
            """,
        )
        result = _RUNNER.invoke(app, ["sim", "mde", "--config", str(config)])
        assert result.exit_code == 0, result.output
        (run_dir,) = (tmp_path / "out").glob("run_*")
        assert (run_dir / "data" / "mde_mvl.csv").exists()
        assert (run_dir / "data" / "mde_morphometry.csv").exists()


class TestBadConfig:
    @pytest.mark.parametrize(
        ("command", "body", "fragment"),
        [
            ("chain", "n_simulation: 5", "did you mean 'n_simulations'"),
            ("chain", "length_distribution: weibull", "not registered"),
            ("sarc", "target_condition: custom", "target_condition"),
            ("coverage", "confidence: 95", "confidence"),
            ("mde", "power: 80", "power"),
        ],
    )
    def test_invalid_config_exits_2_with_message(
        self, tmp_path: Path, command: str, body: str, fragment: str
    ) -> None:
        config = _write(tmp_path, "bad.yaml", body)
        result = _RUNNER.invoke(app, ["sim", command, "--config", str(config)])
        assert result.exit_code == 2
        assert "Traceback" not in result.output
        # Rich wraps the message in a box; drop the border glyphs before matching.
        text = " ".join(result.output.replace("│", " ").split())
        assert fragment in text
