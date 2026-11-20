"""Open, annotate, analyse and export through the engine.

Port of ``tests/integration/test_workflow.py``, which drove the desktop
session model; here the annotations live in an annotation session and the
metrics come from :mod:`sarcostat.analysis` on its document. Chains enter
the session as ``chain`` items of an opened file (the chain flow comes
later); sarcomeres are clicked.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest

from sarcostat.core._serialization import dump_json
from sarcostat.core.circular_stats import SequenceMetrics
from sarcostat.engine.annotate import AnnotationSession
from sarcostat.engine.document import ImageInfo, SessionSettings, build_document
from sarcostat.io.annotations import dumps_annotations

from .test_annotate import FIXED_UTC, SHA_A, checked, confirm_shape

WIDE = {"file_name": "wide.png", "sha256": SHA_A, "width_px": 1000, "height_px": 400}
Segment = tuple[tuple[float, float], tuple[float, float]]


def _chain_item(item_id: str, segments: Sequence[Segment]) -> dict:
    return {
        "item_id": item_id,
        "target_id": None,
        "image_id": "wide",
        "type": "chain",
        "display_transform": "identity",
        "kit_crop_offset": [0, 0],
        "kit_crop_size": [WIDE["width_px"], WIDE["height_px"]],
        "raw_clicks": [],
        "points": {"z_lines": [[list(p1), list(p2)] for p1, p2 in segments]},
        "started_utc": None,
        "finished_utc": None,
        "active_seconds": None,
        "skipped": False,
        "skip_reason": None,
        "skipped_parts": [],
    }


def chain_session(
    segments: Sequence[Segment], pixel_size: float | None = None, unit: str = "px"
) -> AnnotationSession:
    """A session opened on a file with one chain item made of ``segments``."""
    doc = build_document(
        image=ImageInfo.from_json(WIDE),
        image_id="wide",
        settings=SessionSettings(),
        items=[_chain_item("myofibril-1", segments)],
        last_mode_shape=False,
        open_myofibril=False,
        created_utc="2026-01-01T00:00:00Z",
    )
    session = AnnotationSession(utc_now=lambda: FIXED_UTC)
    checked(session.doc_load_v1(dumps_annotations(doc), WIDE, False))
    if pixel_size is not None:
        assert session.doc_set_scale(pixel_size, unit)["notice"] is None
    return session


def chain_metrics(session: AnnotationSession) -> SequenceMetrics:
    results = session.analysis()
    assert results is not None
    metrics = results.chains[0].metrics
    assert metrics is not None
    return metrics


def _shape_session() -> AnnotationSession:
    session = AnnotationSession(utc_now=lambda: FIXED_UTC)
    checked(session.doc_new(WIDE))
    return session


class TestLinearityWorkflow:
    """Linearity analysis of a chain item opened in the session."""

    def test_straight_line_workflow(self) -> None:
        segments = [((i * 100.0, 50.0), ((i + 1) * 100.0, 50.0)) for i in range(5)]
        metrics = chain_metrics(chain_session(segments, pixel_size=0.065, unit="um"))
        assert metrics.n_segments == 4
        assert metrics.linearity_ratio > 0.99
        assert metrics.mean_vector_length > 0.99
        assert metrics.unit == "um"

    def test_zigzag_workflow(self) -> None:
        """Headings alternate between +45 and -45 deg: MVL = LR = sqrt(5) / 3."""
        segments = [
            ((0.0, 0.0), (100.0, 0.0)),
            ((50.0, 50.0), (150.0, 50.0)),
            ((100.0, 0.0), (200.0, 0.0)),
            ((150.0, 50.0), (250.0, 50.0)),
        ]
        metrics = chain_metrics(chain_session(segments))
        assert metrics.linearity_ratio < 0.95
        assert metrics.n_segments == 3
        assert metrics.mean_vector_length == pytest.approx(metrics.linearity_ratio)
        assert metrics.mean_vector_length == pytest.approx(np.sqrt(5.0) / 3.0)

    def test_workflow_with_export(self, tmp_path: Path) -> None:
        segments = [((x, 0.0), (x + 100.0, 0.0)) for x in (0.0, 100.0, 200.0)]
        metrics = chain_metrics(chain_session(segments))
        json_path = tmp_path / "test_analysis.json"
        dump_json(metrics, json_path)
        assert json_path.exists()
        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert data["n_segments"] == 2
        assert "linearity_ratio" in data

    def test_scale_factor_affects_lengths(self) -> None:
        segments = [((0.0, 0.0), (100.0, 0.0)), ((100.0, 0.0), (200.0, 0.0))]
        metrics_px = chain_metrics(chain_session(segments))
        metrics_nm = chain_metrics(chain_session(segments, pixel_size=65.0, unit="nm"))
        assert abs(metrics_nm.total_length - metrics_px.total_length * 65.0) < 0.1
        assert abs(metrics_nm.linearity_ratio - metrics_px.linearity_ratio) < 1e-10


class TestSarcomereWorkflow:
    """Single-sarcomere morphometry of clicked sarcomeres."""

    def test_square_sarcomere_workflow(self) -> None:
        session = _shape_session()
        confirm_shape(session, [(0, 0), (0, 100), (50, 0), (50, 100), (100, 0), (100, 100)])
        results = session.analysis()
        assert results is not None
        (geom,) = results.geometries
        assert len(session.items) == 1
        assert abs(geom.aspect_ratio - 1.0) < 0.01
        assert geom.shape_classification.value == "square"

    def test_rectangular_sarcomere_workflow(self) -> None:
        session = _shape_session()
        confirm_shape(session, [(0, 0), (0, 50), (100, 0), (100, 50), (200, 0), (200, 50)])
        results = session.analysis()
        assert results is not None
        (geom,) = results.geometries
        assert geom.aspect_ratio > 1.5
        assert geom.shape_classification.value == "rectangular"


class TestModeSwitch:
    """Clearing the shape annotations keeps the image, the scale and the other items."""

    def test_mode_switch_preserves_image_state(self) -> None:
        session = chain_session(
            [((0.0, 0.0), (100.0, 0.0)), ((100.0, 0.0), (200.0, 0.0))],
            pixel_size=0.065,
            unit="um",
        )
        confirm_shape(session, [(300, 10), (300, 30), (310, 10), (310, 30), (320, 10), (320, 30)])
        state = checked(session.shape_clear())
        assert session.image == ImageInfo.from_json(WIDE)
        assert session.settings.pixel_size == 0.065
        assert [i["type"] for i in state["items"]] == ["chain"]

    def test_mode_switch_clears_pending(self) -> None:
        session = _shape_session()
        session.shape_click(10.0, 20.0)
        state = checked(session.shape_clear())
        assert state["pending"]["marks"] == [] and session.flow.clicks == []

    def test_clear_all_resets_history(self) -> None:
        """The engine keeps no analysis history: results always describe the items on screen."""
        session = _shape_session()
        confirm_shape(session, [(0, 0), (0, 100), (50, 0), (50, 100), (100, 0), (100, 100)])
        session.doc_set_click_error(1.0)
        session.shape_clear()
        results = session.analysis()
        assert results is not None
        assert results.shapes == () and results.uncertainties == ()
        assert session.items == []


class TestSessionProperties:
    def test_midpoints_property(self) -> None:
        """The Z-line midpoints of a chain item are the points its analysis joins."""
        session = chain_session([((0.0, 0.0), (100.0, 0.0)), ((100.0, 0.0), (200.0, 0.0))])
        metrics = chain_metrics(session)
        (segment,) = metrics.segments
        assert segment.start_point == (50.0, 0.0)
        assert segment.end_point == (150.0, 0.0)

    def test_has_image_property(self) -> None:
        session = AnnotationSession()
        assert session.image is None
        session.doc_new(WIDE)
        assert session.image is not None
