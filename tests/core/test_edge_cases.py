"""
Edge case tests for circular statistics.

Tests large arrays, NaN/Inf propagation, and extreme parameter values.
"""

import warnings

import numpy as np
import pytest

from sarcostat.core.circular_stats import (
    KAPPA_MAX,
    bootstrap_circular_mean_ci,
    bootstrap_mvl_ci,
    calculate_circular_std,
    calculate_circular_variance,
    calculate_concentration_parameter,
    calculate_mean_vector_length,
    compute_circular_statistics_report,
)


class TestLargeArrays:
    """Tests with large arrays (> 1M points)."""

    @pytest.mark.slow
    def test_mvl_large_array(self):
        """Test MVL calculation with 1M+ points."""
        rng = np.random.default_rng(42)
        angles = rng.vonmises(mu=0, kappa=5, size=1_000_000)

        R = calculate_mean_vector_length(angles)

        assert 0.0 <= R <= 1.0
        # For kappa=5, R should be around 0.89
        assert 0.85 < R < 0.95

    @pytest.mark.slow
    def test_circular_std_large_array(self):
        """Test circular std with large concentrated array."""
        rng = np.random.default_rng(42)
        angles = rng.vonmises(mu=0, kappa=10, size=1_000_000)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # Ignore any warnings
            std = calculate_circular_std(angles)

        assert std > 0
        assert not np.isinf(std)

    @pytest.mark.slow
    def test_concentration_large_array(self):
        """Test concentration parameter with large array."""
        rng = np.random.default_rng(42)
        angles = rng.vonmises(mu=0, kappa=5, size=500_000)

        kappa = calculate_concentration_parameter(angles)

        # Should recover approximately kappa=5
        assert 4.0 < kappa < 6.0


class TestNaNInfPropagation:
    """Tests for NaN and Inf handling."""

    def test_mvl_with_nan_in_input(self):
        """Test MVL behavior with NaN in input."""
        angles = np.array([0.1, 0.2, np.nan, 0.3])

        # pingouin should handle NaN - behavior may vary
        R = calculate_mean_vector_length(angles)
        # Either returns NaN or filters it
        assert np.isnan(R) or 0.0 <= R <= 1.0

    def test_circular_std_with_nan_in_input(self):
        """Test circular std with NaN in input."""
        angles = np.array([0.1, 0.2, np.nan, 0.3])

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            std = calculate_circular_std(angles)

        # Should propagate NaN or handle gracefully
        assert np.isnan(std) or std >= 0

    def test_empty_array_returns_nan(self):
        """Test empty array returns NaN for all functions."""
        empty = np.array([])

        assert np.isnan(calculate_mean_vector_length(empty))
        assert np.isnan(calculate_circular_variance(empty))
        assert np.isnan(calculate_concentration_parameter(empty))

    def test_bootstrap_empty_returns_nan(self):
        """Test bootstrap with empty array returns NaN."""
        empty = np.array([])

        mean, ci_low, ci_high = bootstrap_circular_mean_ci(empty)
        assert np.isnan(mean)
        assert np.isnan(ci_low)
        assert np.isnan(ci_high)

        mvl, ci_low, ci_high = bootstrap_mvl_ci(empty)
        assert np.isnan(mvl)
        assert np.isnan(ci_low)
        assert np.isnan(ci_high)


class TestExtremeKappa:
    """Tests for extreme concentration parameter values."""

    def test_very_high_kappa_capped(self):
        """Test that kappa is capped for near-point-mass data."""
        # Nearly identical angles - should produce very high R
        angles = np.array([0.0, 1e-10, -1e-10, 0.0, 0.0])

        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            kappa = calculate_concentration_parameter(angles)

            # Should be capped at KAPPA_MAX
            assert kappa <= KAPPA_MAX

            # May or may not warn depending on exact R value
            # Just verify it doesn't crash

    def test_zero_kappa_for_uniform(self):
        """Test kappa approaches 0 for uniform distribution."""
        # Perfect uniform: 4 points at 90-degree intervals
        angles = np.array([0, np.pi / 2, np.pi, 3 * np.pi / 2])

        kappa = calculate_concentration_parameter(angles)

        assert kappa < 0.1  # Should be near zero

    def test_moderate_kappa_recovery(self):
        """Test that moderate kappa values are recovered correctly."""
        rng = np.random.default_rng(42)
        true_kappa = 10

        # Sample from von Mises
        angles = rng.vonmises(mu=0, kappa=true_kappa, size=1000)

        estimated_kappa = calculate_concentration_parameter(angles)

        # Should be reasonably close to true value
        assert 7 < estimated_kappa < 15


class TestUniformDistribution:
    """Tests for uniform distribution handling."""

    def test_circular_std_uniform_returns_inf(self):
        """Test circular std returns inf for uniform data."""
        # Perfect uniform distribution
        angles = np.linspace(0, 2 * np.pi, 100, endpoint=False)

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            std = calculate_circular_std(angles)

            # Should return inf
            assert np.isinf(std)

            # Should emit warning
            assert len(w) >= 1
            assert "uniform" in str(w[0].message).lower()

    def test_circular_variance_uniform_near_one(self):
        """Test circular variance is near 1 for uniform data."""
        angles = np.linspace(0, 2 * np.pi, 100, endpoint=False)

        var = calculate_circular_variance(angles)

        # Variance should be close to 1 for uniform
        assert var > 0.95


class TestBootstrapSmallSamples:
    """Tests for bootstrap with small samples."""

    def test_bootstrap_n_equals_2(self):
        """Test bootstrap CI with minimum sample size."""
        angles = np.array([0.1, 0.2])

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            mean, ci_low, ci_high = bootstrap_circular_mean_ci(angles, seed=42)

            # Should warn about small sample
            warning_messages = [str(warning.message).lower() for warning in w]
            assert any("sample size" in msg or "n=" in msg for msg in warning_messages)

    def test_bootstrap_n_equals_1(self):
        """Test bootstrap CI with n=1 (degenerate case)."""
        angles = np.array([0.5])

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            mean, ci_low, ci_high = bootstrap_circular_mean_ci(angles, seed=42)

            # Should return point estimate for all
            assert mean == ci_low == ci_high

            # Should warn about degenerate case
            assert len(w) >= 1

    def test_bootstrap_mvl_small_sample(self):
        """Test MVL bootstrap with small sample."""
        angles = np.array([0.1, 0.2, 0.3])

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            mvl, ci_low, ci_high = bootstrap_mvl_ci(angles, seed=42)

            # Should warn
            assert len(w) >= 1

            # CI should be valid
            assert ci_low <= mvl <= ci_high


class TestStatisticsReport:
    """Tests for comprehensive statistics report edge cases."""

    def test_report_with_single_angle(self):
        """Test report generation with single angle."""
        angles = np.array([0.5])

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            report = compute_circular_statistics_report(angles, seed=42)

        assert report.n_observations == 1
        # Should handle gracefully

    def test_report_with_uniform_data(self):
        """Test report generation with uniform data."""
        angles = np.linspace(0, 2 * np.pi, 20, endpoint=False)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            report = compute_circular_statistics_report(angles, seed=42)

        assert report.n_observations == 20
        # MVL should be low
        assert report.mean_vector_length < 0.2


class TestNumericalStability:
    """Tests for numerical stability in edge cases."""

    def test_very_small_angles(self):
        """Test with very small angle differences."""
        angles = np.array([1e-15, 2e-15, 3e-15, 4e-15])

        R = calculate_mean_vector_length(angles)
        assert 0.0 <= R <= 1.0
        # Nearly identical angles should give R ~ 1
        assert R > 0.99

    def test_angles_near_boundary(self):
        """Test with angles near -pi/pi boundary."""
        angles = np.array([np.pi - 0.01, -np.pi + 0.01, np.pi - 0.02, -np.pi + 0.02])

        r = calculate_mean_vector_length(angles)
        mean = np.arctan2(np.sin(angles).mean(), np.cos(angles).mean())

        # R should be high (angles are clustered)
        assert r > 0.9
        # Mean should be near pi or -pi
        assert abs(abs(mean) - np.pi) < 0.1

    def test_bootstrap_near_boundary(self):
        """Test bootstrap CI near the -pi/pi boundary."""
        # Angles clustered near pi
        angles = np.array([np.pi - 0.1, np.pi - 0.05, -np.pi + 0.1, -np.pi + 0.05])

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            mean, ci_low, ci_high = bootstrap_circular_mean_ci(angles, n_bootstrap=100, seed=42)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
