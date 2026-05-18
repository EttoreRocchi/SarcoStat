"""Tests for :class:`sarcostat.simulation._artifacts.RunArtifacts`.

These tests cover the consolidated run-artifact bundle that replaced
``OutputManager`` and ``SarcomereOutputManager`` in Phase 2A.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import pytest
import yaml

from sarcostat.simulation._artifacts import RunArtifacts


@pytest.fixture
def artifacts(tmp_path: Path) -> RunArtifacts:
    """Build a :class:`RunArtifacts` rooted in ``tmp_path``."""
    return RunArtifacts(tmp_path / "out", logger_name="test.run")


class TestDirectoryLayout:
    """Tests for the standard directory layout."""

    def test_creates_three_subdirectories(self, artifacts: RunArtifacts) -> None:
        """``data/``, ``plots/``, ``logs/`` are created up front."""
        assert artifacts.output_dir.exists()
        assert artifacts.data_dir.exists()
        assert artifacts.plots_dir.exists()
        assert artifacts.logs_dir.exists()

    def test_timestamped_directory(self, tmp_path: Path) -> None:
        """``timestamped=True`` nests under a ``run_*`` directory."""
        ra = RunArtifacts(tmp_path / "out", timestamped=True, logger_name="test.ts")
        assert ra.output_dir.parent == tmp_path / "out"
        assert ra.output_dir.name.startswith("run_")

    def test_path_helpers_return_subdir_path(self, artifacts: RunArtifacts) -> None:
        """The ``data_path`` / ``plots_path`` / ``logs_path`` helpers compose paths."""
        assert artifacts.data_path("foo.json") == artifacts.data_dir / "foo.json"
        assert artifacts.plots_path("foo.png") == artifacts.plots_dir / "foo.png"
        assert artifacts.logs_path("foo.log") == artifacts.logs_dir / "foo.log"


class TestJsonAndYaml:
    """Tests for :meth:`RunArtifacts.save_json` and :meth:`save_yaml`."""

    def test_save_json_round_trip(self, artifacts: RunArtifacts) -> None:
        """Payload survives a round-trip through ``save_json``."""
        path = artifacts.save_json("payload.json", {"a": 1, "b": [1, 2, 3]})
        assert path.exists()
        assert json.loads(path.read_text()) == {"a": 1, "b": [1, 2, 3]}

    def test_save_json_default_callable_handles_paths(self, artifacts: RunArtifacts) -> None:
        """The ``default=str`` fallback rescues non-JSON-encodable values."""
        path = artifacts.save_json("paths.json", {"p": Path("/tmp/foo")})
        loaded = json.loads(path.read_text())
        assert loaded["p"] == "/tmp/foo"

    def test_save_parameters_writes_canonical_filename(self, artifacts: RunArtifacts) -> None:
        """``save_parameters`` writes to ``simulation_parameters.json``."""
        path = artifacts.save_parameters({"seed": 42})
        assert path.name == "simulation_parameters.json"
        assert path.parent == artifacts.data_dir

    def test_save_yaml_round_trip(self, artifacts: RunArtifacts) -> None:
        """Payload survives a round-trip through ``save_yaml``."""
        path = artifacts.save_yaml("conf.yaml", {"a": 1, "kappa": [0, 1, 5]})
        loaded = yaml.safe_load(path.read_text())
        assert loaded == {"a": 1, "kappa": [0, 1, 5]}


class TestCsv:
    """Tests for :meth:`RunArtifacts.save_csv`."""

    def test_save_csv_writes_dataframe(self, artifacts: RunArtifacts) -> None:
        """The DataFrame is written to ``data/`` as CSV."""
        df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
        path = artifacts.save_csv("table.csv", df)
        loaded = pd.read_csv(path)
        pd.testing.assert_frame_equal(loaded, df)


class TestFigure:
    """Tests for :meth:`RunArtifacts.save_figure`."""

    def test_save_figure_writes_two_formats_by_default(self, artifacts: RunArtifacts) -> None:
        """A figure is saved in PDF and PNG by default."""
        fig, ax = plt.subplots()
        ax.plot([0, 1], [0, 1])
        paths = artifacts.save_figure(fig, "demo")
        plt.close(fig)
        assert {p.suffix for p in paths} == {".pdf", ".png"}
        for p in paths:
            assert p.exists()

    def test_save_figure_respects_formats_argument(self, artifacts: RunArtifacts) -> None:
        """A custom ``formats`` argument restricts the produced files."""
        fig, ax = plt.subplots()
        ax.plot([0, 1], [0, 1])
        paths = artifacts.save_figure(fig, "demo_png", formats=("png",))
        plt.close(fig)
        assert len(paths) == 1
        assert paths[0].suffix == ".png"
