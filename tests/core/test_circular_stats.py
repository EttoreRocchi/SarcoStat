"""
Unit tests for core.circular_stats module.

Tests circular statistics functions against known analytical values
and edge cases.
"""

import numpy as np
import pytest

from sarcostat.core.circular_stats import (
    analyze_segment_sequence,
    calculate_circular_mean,
    calculate_circular_std,
    calculate_circular_variance,
    calculate_linearity_ratio,
    calculate_mean_vector_length,
    compute_circular_statistics_report,
    perform_rayleigh_test,
    perform_v_test,
    to_minuspi_pi,
    wrap_strict,
)


class TestAngleWrapping:
    """Tests for angle wrapping functions."""

    def test_wrap_strict_basic(self):
        """Test basic angle wrapping to [0, 2pi)."""
        angles = np.array([0, np.pi, 2 * np.pi, 3 * np.pi, -np.pi])
        wrapped = wrap_strict(angles, 0.0, 2 * np.pi)

        assert all(wrapped >= 0.0)
        assert all(wrapped < 2 * np.pi)

    def test_to_minuspi_pi_range(self):
        """Test normalization to (-pi, pi)."""
        angles = np.array([0, np.pi / 2, np.pi, 3 * np.pi / 2, 2 * np.pi, -np.pi / 2])
        normalized = to_minuspi_pi(angles)

        assert all(normalized > -np.pi)
        assert all(normalized < np.pi)

    def test_to_minuspi_pi_preserves_direction(self):
        """Test that normalization preserves direction."""
        angles = np.array([0.5, 1.0, 2.0])
        normalized = to_minuspi_pi(angles)

        assert all(normalized > 0)


class TestLinearityRatio:
    """Tests for linearity ratio calculation."""

    def test_straight_line(self):
        """Perfectly straight path should have ratio = 1."""
        ratio = calculate_linearity_ratio(head_to_tail=10.0, total_length=10.0)
        assert abs(ratio - 1.0) < 1e-10

    def test_circular_path(self):
        """Path returning to start should have ratio = 0."""
        ratio = calculate_linearity_ratio(head_to_tail=0.0, total_length=10.0)
        assert abs(ratio) < 1e-10

    def test_half_efficient_path(self):
        """Path with head_to_tail = half of total_length."""
        ratio = calculate_linearity_ratio(head_to_tail=5.0, total_length=10.0)
        assert abs(ratio - 0.5) < 1e-10

    def test_zero_total_length(self):
        """Zero total length should return 0 (avoid division by zero)."""
        ratio = calculate_linearity_ratio(head_to_tail=0.0, total_length=0.0)
        assert ratio == 0.0


class TestMeanVectorLength:
    """Tests for mean vector length (R) calculation."""

    def test_identical_angles(self):
        """All identical angles should give R = 1."""
        angles = np.array([0.5, 0.5, 0.5, 0.5])
        R = calculate_mean_vector_length(angles)
        assert abs(R - 1.0) < 1e-6

    def test_opposite_angles(self):
        """Two opposite angles should give R ~ 0."""
        angles = np.array([0, np.pi, 0, np.pi])
        R = calculate_mean_vector_length(angles)
        assert R < 0.1

    def test_empty_array(self):
        """Empty array should return np.nan (undefined)."""
        R = calculate_mean_vector_length(np.array([]))
        assert np.isnan(R)

    def test_mvl_bounds(self):
        """R should always be in [0, 1]."""
        rng = np.random.default_rng(42)
        for _ in range(10):
            angles = rng.uniform(-np.pi, np.pi, size=50)
            R = calculate_mean_vector_length(angles)
            assert 0.0 <= R <= 1.0


class TestCircularStatistics:
    """Tests for circular mean and standard deviation."""

    def test_circular_mean_concentrated(self):
        """Mean of angles near 0 should be near 0."""
        angles = np.array([0.1, -0.1, 0.05, -0.05])
        mean = calculate_circular_mean(angles)
        assert abs(mean) < 0.1

    def test_circular_std_concentrated(self):
        """Std of identical angles should be 0."""
        angles = np.array([1.0, 1.0, 1.0, 1.0])
        std = calculate_circular_std(angles)
        assert std < 0.01

    def test_circular_variance_concentrated(self):
        """Variance of identical angles should be 0."""
        angles = np.array([1.0, 1.0, 1.0, 1.0])
        var = calculate_circular_variance(angles)
        assert var < 0.01

    def test_circular_variance_uniform(self):
        """Variance of uniformly spread angles should approach 1."""
        angles = np.array([0, np.pi / 2, np.pi, 3 * np.pi / 2])
        var = calculate_circular_variance(angles)
        assert var > 0.5


class TestStatisticalTests:
    """Tests for Rayleigh and V-test."""

    def test_rayleigh_concentrated(self):
        """Rayleigh test on concentrated data should be significant."""
        angles = np.array([0.1, 0.15, 0.05, 0.08, 0.12, 0.09, 0.11, 0.07])
        z, p = perform_rayleigh_test(angles)
        assert p < 0.05

    def test_rayleigh_empty(self):
        """Empty array should return safe defaults."""
        z, p = perform_rayleigh_test(np.array([]))
        assert z == 0.0
        assert p == 1.0

    def test_v_test_empty(self):
        """Empty array should return safe defaults."""
        v, p = perform_v_test(np.array([]))
        assert v == 0.0
        assert p == 1.0

    def test_pingouin_rayleigh_z_convention(self):
        """Regression guard: Rayleigh effect-size recovery R = sqrt(z/n)
        relies on pingouin returning z = n * R^2. If a future pingouin
        release changes the convention, this test fails immediately
        instead of letting the silent corruption propagate to the
        :class:`RayleighInterpreter`.
        """
        import pingouin as pg

        from sarcostat.core.circular_stats import calculate_mean_vector_length

        rng = np.random.default_rng(0)
        angles = rng.vonmises(mu=0.0, kappa=4.0, size=200)
        z, _ = pg.circ_rayleigh(angles)
        r = calculate_mean_vector_length(angles)
        assert np.isclose(z, len(angles) * r**2, rtol=1e-10)


class TestSequenceAnalysis:
    """Tests for full sequence analysis."""

    def test_straight_horizontal_line(self):
        """Horizontal straight line should have linearity = 1, high R."""
        points = np.array(
            [
                [0, 0],
                [1, 0],
                [2, 0],
                [3, 0],
            ]
        )
        metrics = analyze_segment_sequence(points)

        assert abs(metrics.linearity_ratio - 1.0) < 1e-6
        assert metrics.mean_vector_length > 0.99

    def test_zigzag_path(self):
        """Zigzag path should have lower linearity than straight."""
        points = np.array(
            [
                [0, 0],
                [1, 1],
                [2, 0],
                [3, 1],
                [4, 0],
            ]
        )
        metrics = analyze_segment_sequence(points)

        assert metrics.linearity_ratio < 1.0
        assert metrics.n_segments == 4

    def test_minimum_points(self):
        """Should work with exactly 2 points."""
        points = np.array([[0, 0], [1, 1]])
        metrics = analyze_segment_sequence(points)

        assert metrics.n_segments == 1
        assert metrics.linearity_ratio == 1.0

    def test_single_point_raises(self):
        """Single point should raise ValueError."""
        with pytest.raises(ValueError):
            analyze_segment_sequence(np.array([[0, 0]]))

    def test_pixel_to_unit_conversion(self):
        """Pixel to unit conversion should be applied correctly."""
        points = np.array([[0, 0], [10, 0]])
        metrics_px = analyze_segment_sequence(points, pixel_to_unit=1.0)
        metrics_um = analyze_segment_sequence(points, pixel_to_unit=0.065)

        assert abs(metrics_um.total_length - metrics_px.total_length * 0.065) < 1e-6


def _arc_points(n_segments: int, turn_deg: float, step: float = 1.0) -> np.ndarray:
    """Polyline of equal-length segments turning by ``turn_deg`` at each joint."""
    headings = np.radians(turn_deg) * np.arange(n_segments)
    steps = step * np.column_stack([np.cos(headings), np.sin(headings)])
    return np.vstack([[0.0, 0.0], np.cumsum(steps, axis=0)])


class TestSequenceStatisticsOnHeadings:
    """MVL and the tests are computed on headings theta_i."""

    def test_lr_equals_mvl_for_equal_lengths(self):
        """For equal lengths LR = N^-1 ||sum u_i|| = MVL."""
        rng = np.random.default_rng(0)
        headings = rng.vonmises(0.3, 2.0, size=25)
        points = np.vstack(
            [[0.0, 0.0], np.cumsum(np.column_stack([np.cos(headings), np.sin(headings)]), axis=0)]
        )
        metrics = analyze_segment_sequence(points)
        assert metrics.mean_vector_length == pytest.approx(metrics.linearity_ratio, abs=1e-12)

    def test_smooth_arc_is_not_perfectly_aligned(self):
        """A 20 deg/step arc has MVL ~ 0.57, not the turning-angle MVL of 1."""
        metrics = analyze_segment_sequence(_arc_points(10, 20.0))
        assert metrics.linearity_ratio == pytest.approx(0.5672, abs=1e-3)
        assert metrics.mean_vector_length == pytest.approx(metrics.linearity_ratio, abs=1e-12)

    def test_right_angle_turn(self):
        metrics = analyze_segment_sequence(np.array([[0, 0], [1, 0], [1, 1]]))
        assert metrics.mean_vector_length == pytest.approx(np.sqrt(0.5))

    def test_statistics_are_rotation_invariant(self):
        points = _arc_points(8, 7.0)
        theta = 1.1
        rot = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
        a = analyze_segment_sequence(points)
        b = analyze_segment_sequence(points @ rot.T)
        assert a.mean_vector_length == pytest.approx(b.mean_vector_length)
        assert a.rayleigh_p == pytest.approx(b.rayleigh_p)
        assert a.circular_std == pytest.approx(b.circular_std)

    def test_turning_angles_still_reported_per_segment(self):
        metrics = analyze_segment_sequence(_arc_points(4, 20.0))
        rel = [np.degrees(s.angle_relative) for s in metrics.segments]
        assert rel == pytest.approx([0.0, 20.0, 20.0, 20.0])
        assert metrics.metadata["angle_basis"] == "heading"

    def test_v_test_skipped_without_expected_direction(self):
        metrics = analyze_segment_sequence(_arc_points(6, 5.0))
        assert metrics.v_statistic is None
        assert metrics.v_p_value is None
        assert "not run" in metrics.summary_string()

    def test_v_test_against_expected_direction(self):
        points = _arc_points(12, 1.0)
        along = analyze_segment_sequence(points, expected_direction=0.0)
        against = analyze_segment_sequence(points, expected_direction=np.pi)
        assert along.v_p_value < 0.05
        assert against.v_p_value > 0.95
        assert along.expected_direction == 0.0

    def test_summary_string_reports_units(self):
        metrics = analyze_segment_sequence(_arc_points(3, 0.0), pixel_to_unit=0.5, unit_name="um")
        text = metrics.summary_string()
        assert "1.50 um" in text
        assert metrics.unit == "um"


class TestSequenceChainStatistics:
    """Order- and length-aware statistics attached to SequenceMetrics."""

    def test_equal_lengths(self):
        metrics = analyze_segment_sequence(_arc_points(10, 2.0))
        assert metrics.n_eff == pytest.approx(10.0)
        expected = (10 * metrics.linearity_ratio**2 - 1) / 9
        assert metrics.rho_squared_corrected == pytest.approx(expected)

    def test_turning_angles_measure_curvature(self):
        metrics = analyze_segment_sequence(_arc_points(10, 2.0), pixel_to_unit=2.0, unit_name="um")
        assert np.degrees(metrics.turning_mean) == pytest.approx(2.0)
        assert metrics.turning_mvl == pytest.approx(1.0)
        assert np.degrees(metrics.curvature_per_unit) == pytest.approx(1.0)

    def test_tangent_correlation_and_persistence_of_arc(self):
        metrics = analyze_segment_sequence(_arc_points(12, 3.0))
        assert metrics.tangent_correlation[0] == pytest.approx(np.cos(np.radians(3.0)))
        assert metrics.persistence_amplitude is not None

    def test_unresolved_persistence_is_not_printed(self):
        """A fit far beyond the traced length is reported as not resolved, but kept."""
        straight = analyze_segment_sequence(_arc_points(6, 0.5))
        assert straight.persistence_length > 10 * straight.total_length
        assert "not resolved" in straight.chain_statistics_string()
        curved = analyze_segment_sequence(_arc_points(20, 10.0))
        assert curved.persistence_length < 10 * curved.total_length
        assert "not resolved" not in curved.chain_statistics_string()

    def test_serial_dependence_is_reproducible(self):
        points = _arc_points(12, 3.0)
        a = analyze_segment_sequence(points)
        b = analyze_segment_sequence(points)
        assert a.serial_dependence_p == b.serial_dependence_p
        assert a.serial_dependence_p < 0.05

    def test_short_chains_leave_fields_empty(self):
        metrics = analyze_segment_sequence(np.array([[0.0, 0.0], [1.0, 0.0]]))
        assert metrics.turning_mvl is None
        assert metrics.serial_dependence_p is None
        assert metrics.tangent_correlation is None
        assert metrics.persistence_length is None
        assert "n/a" in metrics.chain_statistics_string()

    def test_noise_floor_and_uniformity_p(self):
        metrics = analyze_segment_sequence(_arc_points(5, 0.0))
        assert metrics.lr_noise_floor == pytest.approx(np.sqrt(-np.log(0.05) / 5))
        assert metrics.lr_uniformity_p == pytest.approx(np.exp(-5.0))

    def test_von_mises_interval_contains_estimate(self):
        metrics = analyze_segment_sequence(_arc_points(10, 4.0))
        lo, hi = metrics.mvl_ci
        assert lo <= metrics.mean_vector_length <= hi
        assert "von Mises" in metrics.summary_string()

    def test_json_is_strict(self):
        from sarcostat.core._serialization import to_json_string

        metrics = analyze_segment_sequence(_arc_points(8, 0.0))
        text = to_json_string(metrics)
        assert "NaN" not in text and "Infinity" not in text


class TestSequenceInputValidation:
    def test_duplicate_points_rejected(self):
        points = np.array([[0, 0], [1, 1], [1, 1], [2, 2]])
        with pytest.raises(ValueError, match="Segment 1 has zero length"):
            analyze_segment_sequence(points)

    @pytest.mark.parametrize("bad", [np.nan, np.inf])
    def test_non_finite_points_rejected(self, bad):
        with pytest.raises(ValueError, match="finite"):
            analyze_segment_sequence(np.array([[0, 0], [1, bad], [2, 0]]))

    @pytest.mark.parametrize("factor", [0.0, -1.0, np.nan])
    def test_non_positive_pixel_to_unit_rejected(self, factor):
        with pytest.raises(ValueError, match="pixel_to_unit"):
            analyze_segment_sequence(np.array([[0, 0], [1, 0]]), pixel_to_unit=factor)

    def test_wrong_shape_rejected(self):
        with pytest.raises(ValueError, match="shape"):
            analyze_segment_sequence(np.array([0, 1, 2, 3]))


class TestStatisticsReport:
    """Tests for comprehensive statistics report."""

    def test_report_records_bootstrap_parameters(self):
        angles = np.linspace(-0.2, 0.2, 12)
        report = compute_circular_statistics_report(angles, confidence=0.8, n_bootstrap=50, seed=3)
        assert (report.confidence, report.n_bootstrap, report.seed) == (0.8, 50, 3)
        text = report.summary_string()
        assert "80% CI" in text
        assert "95% CI" not in text

    def test_report_generation(self):
        """Report generates correctly; small-sample bootstrap warning is asserted."""
        angles = np.array([0.1, 0.2, 0.15, 0.12, 0.18, 0.14])
        with pytest.warns(UserWarning, match="below recommended minimum"):
            report = compute_circular_statistics_report(angles, seed=42)

        assert report.n_observations == 6
        assert 0 <= report.mean_vector_length <= 1
        assert report.rayleigh_interpretation is not None

    def test_report_empty_angles(self):
        """Empty angles should produce valid report with NaN values."""
        report = compute_circular_statistics_report(np.array([]))

        assert report.n_observations == 0
        assert np.isnan(report.mean_vector_length)
        assert np.isnan(report.circular_mean)
        assert np.isnan(report.circular_std)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestAnalysisTimestamp:
    """Headless analyses carry no wall-clock time unless the caller sets one."""

    POINTS = np.array([[0, 0], [1, 0.2], [2, 0], [3, 0.3]])

    def test_default_is_none_and_reproducible(self):
        a = analyze_segment_sequence(self.POINTS)
        b = analyze_segment_sequence(self.POINTS)
        assert a.timestamp is None
        assert a.to_dict() == b.to_dict()
        assert "Timestamp" not in a.summary_string()

    def test_caller_sets_the_timestamp(self):
        stamp = "2026-11-02T09:00:00"
        metrics = analyze_segment_sequence(self.POINTS, timestamp=stamp)
        assert metrics.timestamp == stamp
        assert f"Timestamp: {stamp}" in metrics.summary_string()
