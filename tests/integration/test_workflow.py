"""Integration tests for the load -> annotate -> analyze -> export pipeline."""

import json
from pathlib import Path

import numpy as np
import pytest

from sarcostat.core._serialization import dump_json
from sarcostat.core.circular_stats import analyze_segment_sequence
from sarcostat.core.sarcomere_morphometry import calculate_sarcomere_geometry
from sarcostat.gui.session import AnalysisSession, UserSegment


class TestLinearityWorkflow:
    """Integration tests for Myofibril Linearity analysis workflow."""

    def test_straight_line_workflow(self):
        """Test complete workflow with straight line segments."""
        # Create session
        session = AnalysisSession(pixel_size=0.065, unit_name="um")

        # Simulate annotations (5 horizontal segments)
        for i in range(5):
            seg = UserSegment(p1=(i * 100.0, 50.0), p2=((i + 1) * 100.0, 50.0))
            session.segments.append(seg)

        # Run analysis
        midpoints = np.array(session.midpoints, dtype=float)
        metrics = analyze_segment_sequence(
            midpoints, pixel_to_unit=session.pixel_size, unit_name=session.unit_name
        )

        # Verify results
        assert metrics.n_segments == 4  # 5 segments -> 4 midpoint connections
        assert metrics.linearity_ratio > 0.99  # Nearly straight
        assert metrics.mean_vector_length > 0.99  # Highly concentrated

    def test_zigzag_workflow(self):
        """A zigzag of midpoints: headings alternate between +45 and -45 deg.

        Statistics are computed on the headings, so
        for these equal-length segments MVL = LR = |2 e^{i pi/4} + e^{-i pi/4}| / 3
        = sqrt(5) / 3.
        """
        session = AnalysisSession()

        session.segments.append(UserSegment(p1=(0.0, 0.0), p2=(100.0, 0.0)))
        session.segments.append(UserSegment(p1=(50.0, 50.0), p2=(150.0, 50.0)))
        session.segments.append(UserSegment(p1=(100.0, 0.0), p2=(200.0, 0.0)))
        session.segments.append(UserSegment(p1=(150.0, 50.0), p2=(250.0, 50.0)))

        midpoints = np.array(session.midpoints, dtype=float)
        metrics = analyze_segment_sequence(midpoints)

        assert metrics.linearity_ratio < 0.95
        assert metrics.n_segments == 3
        assert metrics.mean_vector_length == pytest.approx(metrics.linearity_ratio)
        assert metrics.mean_vector_length == pytest.approx(np.sqrt(5.0) / 3.0)

    def test_workflow_with_export(self, tmp_path):
        """Round-trip metrics through ``dump_json`` and verify the file."""
        session = AnalysisSession()

        session.segments.append(UserSegment(p1=(0.0, 0.0), p2=(100.0, 0.0)))
        session.segments.append(UserSegment(p1=(100.0, 0.0), p2=(200.0, 0.0)))
        session.segments.append(UserSegment(p1=(200.0, 0.0), p2=(300.0, 0.0)))

        midpoints = np.array(session.midpoints, dtype=float)
        metrics = analyze_segment_sequence(midpoints)

        json_path = tmp_path / "test_analysis.json"
        dump_json(metrics, json_path)

        assert json_path.exists()
        with open(json_path) as f:
            data = json.load(f)
        assert data["n_segments"] == 2
        assert "linearity_ratio" in data

    def test_scale_factor_affects_lengths(self):
        """Test that scale factor correctly affects length calculations."""
        session = AnalysisSession()

        # Same segments, different scales
        session.segments.append(UserSegment(p1=(0.0, 0.0), p2=(100.0, 0.0)))
        session.segments.append(UserSegment(p1=(100.0, 0.0), p2=(200.0, 0.0)))

        midpoints = np.array(session.midpoints, dtype=float)

        metrics_px = analyze_segment_sequence(midpoints, pixel_to_unit=1.0)
        metrics_nm = analyze_segment_sequence(midpoints, pixel_to_unit=65.0)  # 65nm/px

        # Length should scale
        assert abs(metrics_nm.total_length - metrics_px.total_length * 65.0) < 0.1

        # Ratios should be identical
        assert abs(metrics_nm.linearity_ratio - metrics_px.linearity_ratio) < 1e-10


class TestSarcomereWorkflow:
    """Integration tests for Single Sarcomere analysis workflow."""

    def test_square_sarcomere_workflow(self):
        """Test workflow with square sarcomere."""
        session = AnalysisSession()

        # Square sarcomere: 100x100
        geom = calculate_sarcomere_geometry(
            point_a=(0.0, 0.0),
            point_b=(0.0, 100.0),
            point_c=(50.0, 0.0),
            point_d=(50.0, 100.0),
            point_e=(100.0, 0.0),
            point_f=(100.0, 100.0),
            sarcomere_id=1,
        )
        session.sarcomere_geometries.append(geom)

        assert len(session.sarcomere_geometries) == 1
        assert abs(geom.aspect_ratio - 1.0) < 0.01
        assert geom.shape_classification.value == "square"

    def test_rectangular_sarcomere_workflow(self):
        """Test workflow with rectangular sarcomere."""
        session = AnalysisSession()

        # Rectangular sarcomere: wider than tall
        geom = calculate_sarcomere_geometry(
            point_a=(0.0, 0.0),
            point_b=(0.0, 50.0),
            point_c=(100.0, 0.0),
            point_d=(100.0, 50.0),
            point_e=(200.0, 0.0),
            point_f=(200.0, 50.0),
            sarcomere_id=1,
        )
        session.sarcomere_geometries.append(geom)

        assert geom.aspect_ratio > 1.5  # Width > height
        assert geom.shape_classification.value == "rectangular"


class TestModeSwitch:
    """Tests for mode switching behavior."""

    def test_mode_switch_preserves_image_state(self):
        """Switching modes should preserve image path and scale."""
        session = AnalysisSession(
            image_path=Path("/path/to/image.tif"), pixel_size=0.065, unit_name="um"
        )

        # Add linearity annotations
        session.segments.append(UserSegment(p1=(0.0, 0.0), p2=(100.0, 0.0)))

        # Simulate mode switch (clear annotations but preserve image)
        session.clear_linearity_annotations()

        # Image state preserved
        assert session.image_path == Path("/path/to/image.tif")
        assert session.pixel_size == 0.065

        # Annotations cleared
        assert len(session.segments) == 0

    def test_mode_switch_clears_pending(self):
        """Switching modes should clear pending points."""
        session = AnalysisSession()
        session.pending_points = [(10.0, 20.0)]

        session.clear_linearity_annotations()

        assert len(session.pending_points) == 0

    def test_clear_all_resets_history(self):
        """Clear all should reset analysis history."""
        session = AnalysisSession()

        # Add some history
        session.segments.append(UserSegment(p1=(0.0, 0.0), p2=(100.0, 0.0)))
        session.segments.append(UserSegment(p1=(100.0, 0.0), p2=(200.0, 0.0)))

        midpoints = np.array(session.midpoints, dtype=float)
        metrics = analyze_segment_sequence(midpoints)
        session.analysis_history.append(metrics)

        # Clear all
        session.clear_all()

        assert len(session.segments) == 0
        assert len(session.analysis_history) == 0


class TestSessionProperties:
    """Tests for AnalysisSession properties and methods."""

    def test_midpoints_property(self):
        """Test midpoints computed property."""
        session = AnalysisSession()
        session.segments.append(UserSegment(p1=(0.0, 0.0), p2=(100.0, 0.0)))
        session.segments.append(UserSegment(p1=(100.0, 0.0), p2=(200.0, 0.0)))

        midpoints = session.midpoints
        assert len(midpoints) == 2
        assert midpoints[0] == (50.0, 0.0)
        assert midpoints[1] == (150.0, 0.0)

    def test_has_image_property(self):
        """Test has_image property."""
        session = AnalysisSession()
        assert not session.has_image

        session.image_path = Path("/some/path.tif")
        assert session.has_image


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
