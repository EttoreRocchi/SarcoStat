"""Tests for :mod:`sarcostat.core._serialization`.

The serialization helpers must round-trip every dataclass-like container
in :mod:`sarcostat.core` through JSON without losing information that
``to_dict`` chose to expose.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from sarcostat.core._serialization import dump_json, to_json_string
from sarcostat.core.circular_stats import (
    SegmentData,
    SequenceMetrics,
    analyze_segment_sequence,
    compute_circular_statistics_report,
)
from sarcostat.core.sarcomere_morphometry import (
    SarcomereChainMetrics,
    analyze_sarcomere_chain,
    calculate_sarcomere_geometry,
)


@pytest.fixture
def sequence_metrics() -> SequenceMetrics:
    """Realistic SequenceMetrics from a 4-segment polyline."""
    points = np.array([[0.0, 0.0], [1.0, 0.1], [2.0, 0.05], [3.0, 0.2], [4.0, 0.15]])
    return analyze_segment_sequence(points)


@pytest.fixture
def chain_metrics() -> SarcomereChainMetrics:
    """Realistic chain metrics from three square sarcomeres."""
    sarcomeres = [
        calculate_sarcomere_geometry(
            point_a=(0 + dx, 0),
            point_b=(0 + dx, 100),
            point_c=(50 + dx, 0),
            point_d=(50 + dx, 100),
            point_e=(100 + dx, 0),
            point_f=(100 + dx, 100),
            sarcomere_id=i,
        )
        for i, dx in enumerate([0, 100, 200])
    ]
    return analyze_sarcomere_chain(sarcomeres)


class TestStrictJson:
    """Output is standard JSON: NaN/inf become null instead of bare tokens."""

    def test_empty_report_has_no_nan_tokens(self) -> None:
        text = to_json_string(compute_circular_statistics_report(np.array([])))
        assert "NaN" not in text
        assert json.loads(text)["mean_vector_length"] is None

    def test_infinity_becomes_null(self) -> None:
        text = to_json_string({"a": float("inf"), "b": [1.0, float("-inf")], "c": {"d": 2}})
        assert json.loads(text) == {"a": None, "b": [1.0, None], "c": {"d": 2}}


class TestToJsonString:
    """Tests for :func:`to_json_string`."""

    def test_dict_passthrough(self) -> None:
        """A plain dict serialises directly without ``to_dict``."""
        payload = {"a": 1, "b": [1, 2, 3]}
        assert json.loads(to_json_string(payload)) == payload

    def test_segment_data_round_trip(self) -> None:
        """A simple :class:`SegmentData` round-trips faithfully."""
        seg = SegmentData(
            segment_id=0,
            start_point=(0.0, 0.0),
            end_point=(1.0, 0.0),
            length=1.0,
            angle_absolute=0.0,
            angle_relative=0.0,
            cumulative_angle=0.0,
        )
        decoded = json.loads(to_json_string(seg))
        assert decoded["segment_id"] == 0
        assert decoded["length"] == 1.0

    def test_indent_is_configurable(self) -> None:
        """``indent`` is forwarded to :func:`json.dumps`."""
        payload = {"a": 1}
        compact = to_json_string(payload, indent=0)
        assert compact != to_json_string(payload, indent=4)


class TestDumpJson:
    """Tests for :func:`dump_json`."""

    def test_writes_file(self, tmp_path: Path, sequence_metrics: SequenceMetrics) -> None:
        """The file is created and contains the expected top-level keys."""
        out = dump_json(sequence_metrics, tmp_path / "metrics.json")
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["n_segments"] == sequence_metrics.n_segments
        assert "linearity_ratio" in data

    def test_returns_path(self, tmp_path: Path) -> None:
        """The returned :class:`pathlib.Path` matches the input."""
        target = tmp_path / "x.json"
        out = dump_json({"a": 1}, target)
        assert out == target

    def test_chain_metrics_round_trip(
        self, tmp_path: Path, chain_metrics: SarcomereChainMetrics
    ) -> None:
        """Chain metrics with nested sarcomeres survive a JSON round-trip."""
        out = dump_json(chain_metrics, tmp_path / "chain.json")
        decoded = json.loads(out.read_text())
        assert decoded["n_sarcomeres"] == chain_metrics.n_sarcomeres
        assert len(decoded["sarcomeres"]) == chain_metrics.n_sarcomeres
        assert decoded["dominant_shape"] == chain_metrics.dominant_shape.value

    def test_circular_statistics_report_round_trip(self, tmp_path: Path) -> None:
        """``CircularStatisticsReport`` is a Serializable too."""
        rng = np.random.default_rng(0)
        angles = rng.normal(0.0, 0.3, size=50)
        report = compute_circular_statistics_report(angles, seed=0)
        out = dump_json(report, tmp_path / "report.json")
        decoded = json.loads(out.read_text())
        assert decoded["n_observations"] == 50
        assert "rayleigh_interpretation" in decoded
