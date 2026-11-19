"""Tests for the length and angle sampler registries.

These tests cover the OCP seam introduced in Phase 2B: any consumer of
:func:`make_length_sampler` or :func:`make_angle_sampler` should never
need a separate code path per distribution; the registry resolves the
factory and returns a sampler that satisfies the structural Protocol.
"""

from __future__ import annotations

import numpy as np
import pytest

from sarcostat.simulation._distributions import (
    ANGLE_SAMPLERS,
    LENGTH_SAMPLERS,
    AngleSampler,
    LengthSampler,
    LognormLengthSampler,
    TruncnormLengthSampler,
    UniformAngleSampler,
    VonMisesAngleSampler,
    WrappedNormalAngleSampler,
    make_angle_sampler,
    make_length_sampler,
    register_angle,
    register_length,
)

TWO_PI = 2.0 * np.pi


class TestLengthRegistry:
    """Tests for :data:`LENGTH_SAMPLERS` and :func:`make_length_sampler`."""

    def test_registry_contains_paper_defaults(self) -> None:
        """The two surviving length families are registered."""
        assert "truncnorm" in LENGTH_SAMPLERS
        assert "lognorm" in LENGTH_SAMPLERS

    def test_make_truncnorm(self) -> None:
        """The factory returns the matching concrete class."""
        sampler = make_length_sampler("truncnorm", mean=1.0, std=0.25, lower=0.5, upper=1.5)
        assert isinstance(sampler, TruncnormLengthSampler)
        assert isinstance(sampler, LengthSampler)

    def test_make_lognorm(self) -> None:
        """The factory returns the matching concrete class."""
        sampler = make_length_sampler("lognorm", mean=1.0, std=0.25, lower=0.1, upper=10.0)
        assert isinstance(sampler, LognormLengthSampler)

    def test_unknown_name_raises_with_helpful_message(self) -> None:
        """Unregistered names produce a descriptive KeyError."""
        with pytest.raises(KeyError, match="weibull.*available"):
            make_length_sampler("weibull", mean=1.0, std=0.25, lower=0.5, upper=1.5)


class TestTruncnormSampler:
    """Behavioural tests for :class:`TruncnormLengthSampler`."""

    def test_respects_bounds(self) -> None:
        """All samples lie inside ``[lower, upper]``."""
        sampler = TruncnormLengthSampler(mean=1.0, std=0.25, lower=0.5, upper=1.5)
        rng = np.random.default_rng(0)
        samples = sampler.sample(2000, rng)
        assert np.all(samples >= 0.5)
        assert np.all(samples <= 1.5)

    def test_seed_deterministic(self) -> None:
        """Two RNGs seeded identically produce the same samples."""
        sampler = TruncnormLengthSampler(mean=1.0, std=0.25, lower=0.5, upper=1.5)
        a = sampler.sample(100, np.random.default_rng(42))
        b = sampler.sample(100, np.random.default_rng(42))
        np.testing.assert_array_equal(a, b)

    def test_zero_std_collapses_to_mean(self) -> None:
        """A degenerate ``std <= 0`` returns ``mean`` for every draw."""
        sampler = TruncnormLengthSampler(mean=0.7, std=0.0, lower=0.0, upper=1.0)
        rng = np.random.default_rng(0)
        samples = sampler.sample(50, rng)
        np.testing.assert_array_equal(samples, np.full(50, 0.7))


class TestLognormSampler:
    """Behavioural tests for :class:`LognormLengthSampler`."""

    def test_clipped_to_bounds(self) -> None:
        """Samples are clipped to ``[lower, upper]``."""
        sampler = LognormLengthSampler(mean=1.0, std=0.5, lower=0.5, upper=1.5)
        rng = np.random.default_rng(0)
        samples = sampler.sample(2000, rng)
        assert np.all(samples >= 0.5)
        assert np.all(samples <= 1.5)


class TestAngleRegistry:
    """Tests for :data:`ANGLE_SAMPLERS` and :func:`make_angle_sampler`."""

    def test_registered_families(self) -> None:
        """All three angle families are present."""
        assert {"uniform", "vonmises", "wrapped_normal"} <= set(ANGLE_SAMPLERS)

    def test_make_uniform(self) -> None:
        """The uniform factory ignores the supplied mean."""
        sampler = make_angle_sampler("uniform", mean=1.0)
        assert isinstance(sampler, UniformAngleSampler)
        assert isinstance(sampler, AngleSampler)

    def test_make_vonmises(self) -> None:
        """The von Mises factory carries the mean."""
        sampler = make_angle_sampler("vonmises", mean=0.5)
        assert isinstance(sampler, VonMisesAngleSampler)
        assert sampler.mean == 0.5

    def test_make_wrapped_normal(self) -> None:
        """The wrapped-normal factory carries the mean."""
        sampler = make_angle_sampler("wrapped_normal", mean=-0.3)
        assert isinstance(sampler, WrappedNormalAngleSampler)
        assert sampler.mean == -0.3

    def test_unknown_name_raises(self) -> None:
        """Unregistered names produce a descriptive KeyError."""
        with pytest.raises(KeyError, match="cardioid.*available"):
            make_angle_sampler("cardioid", mean=0.0)


class TestVonMisesSampler:
    """Behavioural tests for :class:`VonMisesAngleSampler`."""

    def test_respects_kappa_zero(self) -> None:
        """``kappa=0`` falls through to the uniform sampler."""
        sampler = VonMisesAngleSampler(mean=0.0)
        rng = np.random.default_rng(0)
        samples = sampler.sample(5000, kappa=0.0, rng=rng)
        sin_sum, cos_sum = float(np.sin(samples).sum()), float(np.cos(samples).sum())
        R = np.hypot(sin_sum, cos_sum) / len(samples)
        assert R < 0.1

    def test_high_kappa_concentrates(self) -> None:
        """High kappa produces a concentrated distribution (R > 0.9)."""
        sampler = VonMisesAngleSampler(mean=0.0)
        rng = np.random.default_rng(0)
        samples = sampler.sample(5000, kappa=50.0, rng=rng)
        sin_sum, cos_sum = float(np.sin(samples).sum()), float(np.cos(samples).sum())
        R = np.hypot(sin_sum, cos_sum) / len(samples)
        assert R > 0.9

    def test_samples_within_zero_to_two_pi(self) -> None:
        """Samples lie strictly in ``[0, 2*pi)``."""
        sampler = VonMisesAngleSampler(mean=0.0)
        rng = np.random.default_rng(0)
        samples = sampler.sample(1000, kappa=5.0, rng=rng)
        assert np.all(samples >= 0.0)
        assert np.all(samples < TWO_PI)


class TestWrappedNormalSampler:
    """Behavioural tests for :class:`WrappedNormalAngleSampler`."""

    def test_kappa_zero_produces_uniform(self) -> None:
        """As with von Mises, ``kappa=0`` collapses to uniform."""
        sampler = WrappedNormalAngleSampler(mean=0.0)
        rng = np.random.default_rng(0)
        samples = sampler.sample(5000, kappa=0.0, rng=rng)
        sin_sum, cos_sum = float(np.sin(samples).sum()), float(np.cos(samples).sum())
        R = np.hypot(sin_sum, cos_sum) / len(samples)
        assert R < 0.1


class TestRegistration:
    """Tests for the registration decorators."""

    def test_register_length_decorator_adds_to_registry(self) -> None:
        """Using ``register_length`` registers a custom factory.

        The custom name is removed at the end of the test so that the
        global registry remains clean for downstream tests.
        """

        @register_length("test_constant")
        def _make_constant(mean: float, std: float, lower: float, upper: float):
            class _ConstantSampler:
                def sample(self_inner, n, rng):
                    return np.full(n, mean)

            return _ConstantSampler()

        try:
            sampler = make_length_sampler("test_constant", mean=2.5, std=0.0, lower=0.0, upper=10.0)
            samples = sampler.sample(10, np.random.default_rng(0))
            np.testing.assert_array_equal(samples, np.full(10, 2.5))
        finally:
            LENGTH_SAMPLERS.pop("test_constant", None)

    def test_register_angle_decorator_adds_to_registry(self) -> None:
        """Using ``register_angle`` registers a custom factory."""

        @register_angle("test_pi")
        def _make_pi(mean: float):
            class _PiSampler:
                def sample(self_inner, n, kappa, rng):
                    return np.full(n, np.pi)

            return _PiSampler()

        try:
            sampler = make_angle_sampler("test_pi", mean=0.0)
            samples = sampler.sample(5, kappa=1.0, rng=np.random.default_rng(0))
            np.testing.assert_allclose(samples, np.full(5, np.pi))
        finally:
            ANGLE_SAMPLERS.pop("test_pi", None)
