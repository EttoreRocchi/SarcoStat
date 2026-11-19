"""
Unit tests for core.chain_statistics module.

Checks the closed-form results (effective sample size, corrected
estimator, noise floor, turning angles, tangent correlation) on
analytical cases and their statistical behaviour on seeded simulations.
"""

import numpy as np
import pytest

from sarcostat.core.chain_statistics import (
    corrected_rho_squared,
    effective_sample_size,
    lr_noise_floor,
    lr_uniformity_p,
    persistence_length,
    serial_dependence_test,
    tangent_correlation,
    turning_angle_statistics,
)


def _lr(headings, lengths):
    """Linearity Ratio of a chain from its headings and lengths."""
    vec = np.sum(lengths * np.exp(1j * headings))
    return float(np.abs(vec) / np.sum(lengths))


def _crw_headings(rng, n, turn_sd):
    return np.concatenate([[0.0], np.cumsum(rng.normal(0.0, turn_sd, n - 1))])


class TestEffectiveSampleSize:
    """Tests for the Kish effective number of segments."""

    def test_equal_lengths_give_n(self):
        assert effective_sample_size(np.full(7, 2.5)) == pytest.approx(7.0)

    def test_unequal_lengths(self):
        assert effective_sample_size(np.array([1.0, 1.0, 2.0])) == pytest.approx(16.0 / 6.0)

    @pytest.mark.parametrize("bad", [[], [1.0, 0.0], [1.0, -2.0], [1.0, np.nan]])
    def test_invalid_lengths_raise(self, bad):
        with pytest.raises(ValueError):
            effective_sample_size(np.array(bad, dtype=float))


class TestCorrectedRhoSquared:
    """Tests for the unbiased estimator of rho^2."""

    def test_equal_lengths_reduce_to_kutil(self):
        n, r = 5, 0.8
        expected = (n * r**2 - 1) / (n - 1)
        assert corrected_rho_squared(r, np.ones(n)) == pytest.approx(expected)

    def test_single_segment_is_nan(self):
        assert np.isnan(corrected_rho_squared(1.0, np.array([3.0])))

    def test_unbiased_with_heterogeneous_lengths(self):
        rng = np.random.default_rng(0)
        kappa, n, reps = 2.0, 10, 4000
        from scipy.special import i0e, i1e

        rho2 = (i1e(kappa) / i0e(kappa)) ** 2
        estimates = []
        for _ in range(reps):
            theta = rng.vonmises(0.0, kappa, n)
            lengths = rng.lognormal(0.0, 0.8, n)
            estimates.append(corrected_rho_squared(_lr(theta, lengths), lengths))
        assert np.mean(estimates) == pytest.approx(rho2, abs=0.015)

    def test_non_finite_lr_raises(self):
        with pytest.raises(ValueError):
            corrected_rho_squared(np.nan, np.ones(3))


class TestNoiseFloor:
    """Tests for the Linearity Ratio expected from unaligned headings."""

    def test_closed_form(self):
        assert lr_noise_floor(np.ones(5), alpha=0.05) == pytest.approx(np.sqrt(-np.log(0.05) / 5))

    def test_decreases_with_chain_length(self):
        floors = [lr_noise_floor(np.ones(n)) for n in (5, 10, 25, 50)]
        assert all(a > b for a, b in zip(floors, floors[1:], strict=False))

    def test_matches_uniform_simulation(self):
        rng = np.random.default_rng(1)
        n = 25
        lrs = [_lr(rng.uniform(-np.pi, np.pi, n), np.ones(n)) for _ in range(20000)]
        assert np.quantile(lrs, 0.95) == pytest.approx(lr_noise_floor(np.ones(n)), rel=0.05)

    def test_uniformity_p_at_floor_equals_alpha(self):
        lengths = np.ones(12)
        floor = lr_noise_floor(lengths, alpha=0.01)
        assert lr_uniformity_p(floor, lengths) == pytest.approx(0.01)

    @pytest.mark.parametrize("alpha", [0.0, 1.0, -0.1])
    def test_invalid_alpha(self, alpha):
        with pytest.raises(ValueError):
            lr_noise_floor(np.ones(4), alpha=alpha)


class TestTurningAngles:
    """Tests for turning-angle statistics."""

    def test_straight_chain(self):
        stats = turning_angle_statistics(np.full(6, 0.3))
        assert stats.n_turns == 5
        assert stats.mean_vector_length == pytest.approx(1.0)
        assert stats.mean_turning == pytest.approx(0.0, abs=1e-12)
        assert stats.circular_std == pytest.approx(0.0, abs=1e-6)

    def test_constant_curvature(self):
        c = np.deg2rad(2.0)
        headings = np.arange(10) * c
        stats = turning_angle_statistics(headings, lengths=np.full(10, 2.0))
        assert stats.mean_turning == pytest.approx(c)
        assert stats.mean_vector_length == pytest.approx(1.0)
        assert stats.curvature_per_unit == pytest.approx(c / 2.0)

    def test_wraps_across_pi(self):
        stats = turning_angle_statistics(np.array([np.pi - 0.01, -np.pi + 0.01]))
        assert stats.mean_turning == pytest.approx(0.02)

    def test_single_segment_gives_nan(self):
        stats = turning_angle_statistics(np.array([0.1]))
        assert stats.n_turns == 0
        assert np.isnan(stats.mean_vector_length)

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError):
            turning_angle_statistics(np.zeros(4), lengths=np.ones(3))

    def test_iid_headings_give_rho_squared(self):
        rng = np.random.default_rng(2)
        kappa = 10.0
        from scipy.special import i0e, i1e

        rho = i1e(kappa) / i0e(kappa)
        stats = turning_angle_statistics(rng.vonmises(0.0, kappa, 20000))
        assert stats.mean_vector_length == pytest.approx(rho**2, abs=0.01)


class TestTangentCorrelation:
    """Tests for the tangent correlation function."""

    def test_arc_follows_cosine(self):
        c = 0.1
        corr = tangent_correlation(np.arange(20) * c, max_lag=4)
        assert corr.lags == [1, 2, 3, 4]
        assert corr.n_pairs == [19, 18, 17, 16]
        np.testing.assert_allclose(corr.values, np.cos(c * np.arange(1, 5)))

    def test_short_chain(self):
        corr = tangent_correlation(np.array([0.0]))
        assert corr.lags == [] and corr.values == []

    def test_invalid_max_lag(self):
        with pytest.raises(ValueError):
            tangent_correlation(np.zeros(5), max_lag=0)


class TestPersistenceLength:
    """Tests for the exponential fit of the tangent correlation."""

    def test_recovers_worm_like_chain(self):
        rng = np.random.default_rng(3)
        turn_sd, seg = 0.05, 1.6
        headings = _crw_headings(rng, 4000, turn_sd)
        fit = persistence_length(headings, np.full(headings.size, seg), max_lag=20)
        expected = -seg / np.log(np.exp(-(turn_sd**2) / 2))
        assert fit.persistence_length == pytest.approx(expected, rel=0.15)
        assert fit.amplitude == pytest.approx(1.0, abs=0.02)

    def test_independent_headings_do_not_decay(self):
        rng = np.random.default_rng(4)
        headings = rng.normal(0.0, 0.1, 4000)
        fit = persistence_length(headings, np.ones(headings.size), max_lag=10)
        assert fit.persistence_length > 1e3 or np.isinf(fit.persistence_length)
        assert fit.amplitude == pytest.approx(np.exp(-(0.1**2)), abs=0.01)

    def test_too_short_is_nan(self):
        fit = persistence_length(np.array([0.0, 0.1]), np.ones(2))
        assert np.isnan(fit.persistence_length)


class TestSerialDependence:
    """Tests for the serial-dependence test."""

    def test_detects_persistent_chain(self):
        rng = np.random.default_rng(5)
        headings = _crw_headings(rng, 40, 0.15)
        result = serial_dependence_test(headings, seed=0)
        assert result.p_value < 0.05
        assert result.statistic > 0

    def test_size_under_independence(self):
        rng = np.random.default_rng(6)
        pvals = [
            serial_dependence_test(rng.vonmises(0.0, 5.0, 15), n_resamples=199, seed=i).p_value
            for i in range(300)
        ]
        assert np.mean(np.array(pvals) <= 0.05) == pytest.approx(0.05, abs=0.035)

    def test_parametric_method(self):
        rng = np.random.default_rng(7)
        result = serial_dependence_test(
            _crw_headings(rng, 40, 0.15), method="parametric", n_resamples=499, seed=1
        )
        assert result.method == "parametric"
        assert result.p_value < 0.05

    def test_reproducible_with_seed(self):
        headings = np.random.default_rng(8).vonmises(0.0, 3.0, 12)
        a = serial_dependence_test(headings, seed=42)
        b = serial_dependence_test(headings, seed=42)
        assert a.p_value == b.p_value

    def test_invalid_inputs(self):
        with pytest.raises(ValueError):
            serial_dependence_test(np.zeros(2))
        with pytest.raises(ValueError):
            serial_dependence_test(np.zeros(5), method="bootstrap")


class TestMvlConfidenceInterval:
    """Von Mises (Neyman) confidence interval for the population resultant."""

    def test_nominal_coverage_for_short_chains(self):
        from scipy.special import i0e, i1e

        from sarcostat.core.chain_statistics import mvl_confidence_interval

        rng = np.random.default_rng(9)
        kappa, n, reps = 10.0, 6, 600
        rho = i1e(kappa) / i0e(kappa)
        r = np.abs(np.exp(1j * rng.vonmises(0.0, kappa, (reps, n))).mean(axis=1))
        hits = [lo <= rho <= hi for lo, hi in (mvl_confidence_interval(x, n) for x in r)]
        assert np.mean(hits) == pytest.approx(0.95, abs=0.03)

    def test_edges_and_monotonicity(self):
        from sarcostat.core.chain_statistics import mvl_confidence_interval

        assert mvl_confidence_interval(0.05, 10)[0] == 0.0
        assert mvl_confidence_interval(1.0, 10)[1] == 1.0
        lows = [mvl_confidence_interval(r, 12)[0] for r in (0.3, 0.6, 0.9, 0.99)]
        assert lows == sorted(lows)
        assert mvl_confidence_interval(0.7, 1) == (0.0, 1.0)

    @pytest.mark.parametrize("args", [(0.5, 10, 1.0), (1.5, 10, 0.95), (np.nan, 10, 0.95)])
    def test_invalid(self, args):
        from sarcostat.core.chain_statistics import mvl_confidence_interval

        with pytest.raises(ValueError):
            mvl_confidence_interval(*args)


class TestPooledPersistenceLength:
    """Pooling the tangent correlation of several short chains."""

    def test_recovers_worm_like_chain_from_short_chains(self):
        from sarcostat.core.chain_statistics import pooled_persistence_length

        rng = np.random.default_rng(10)
        turn_sd, seg, n = np.sqrt(2) * np.deg2rad(3.0), 1.638, 15
        chains = [(_crw_headings(rng, n, turn_sd), np.full(n, seg)) for _ in range(40)]
        fit = pooled_persistence_length(chains)
        expected = -seg / np.log(np.exp(-(turn_sd**2) / 2))
        assert fit.persistence_length == pytest.approx(expected, rel=0.25)
        assert fit.lags_used == [1, 2, 3, 4, 5]

    def test_validates_inputs(self):
        from sarcostat.core.chain_statistics import pooled_persistence_length

        with pytest.raises(ValueError):
            pooled_persistence_length([])
        with pytest.raises(ValueError):
            pooled_persistence_length([(np.zeros(5), np.ones(4))])
