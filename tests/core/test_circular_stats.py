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

        # Small positive angles should stay positive
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
        # 4 angles at 0, pi/2, pi, 3pi/2
        angles = np.array([0, np.pi / 2, np.pi, 3 * np.pi / 2])
        var = calculate_circular_variance(angles)
        # Should be high but may not be exactly 1
        assert var > 0.5


class TestStatisticalTests:
    """Tests for Rayleigh and V-test."""

    def test_rayleigh_concentrated(self):
        """Rayleigh test on concentrated data should be significant."""
        angles = np.array([0.1, 0.15, 0.05, 0.08, 0.12, 0.09, 0.11, 0.07])
        z, p = perform_rayleigh_test(angles)
        assert p < 0.05  # Should reject uniformity

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
        assert metrics.mean_vector_length > 0.99  # Nearly 1

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

        # Total length should scale by conversion factor
        assert abs(metrics_um.total_length - metrics_px.total_length * 0.065) < 1e-6


class TestStatisticsReport:
    """Tests for comprehensive statistics report."""

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
