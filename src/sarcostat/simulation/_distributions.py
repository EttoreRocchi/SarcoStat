"""Pluggable length and angle samplers for the chain simulator.

The chain simulator used to dispatch on a string parameter via long
if/elif chains in ``_sample_lengths`` and ``_sample_base_angles``. The
two registries below let callers (and future extensions) register a new
sampling strategy without touching the simulator (Open/Closed). Each
sampler exposes a single :meth:`~LengthSampler.sample` method that
accepts a sample size and a numpy ``Generator``.

Two length families are supported:

- ``truncnorm``: Gaussian truncated to ``[length_min, length_max]``
  (default for sarcomere length distributions).
- ``lognorm``: log-normal with mean and std matched to the supplied
  configuration (useful for skewed length distributions).

Three angle families are supported:

- ``uniform``: flat over ``[0, 2*pi)`` (used for ``kappa = 0``).
- ``vonmises``: von Mises with the requested concentration kappa.
- ``wrapped_normal``: wrapped normal whose sigma is matched to the
  von Mises mean resultant length (Mardia and Jupp 2000, Section
  3.5.4).

Add a new sampler via the :func:`register_length` or
:func:`register_angle` decorator.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np
from scipy.special import i0e, i1e
from scipy.stats import truncnorm, vonmises

TWO_PI = 2.0 * np.pi


def _wrap_strict(angles: np.ndarray, low: float, high: float) -> np.ndarray:
    """Local copy of :func:`sarcostat.core.circular_stats.wrap_strict`.

    Repeated here to keep this module free of an import edge against
    :mod:`sarcostat.core` (the simulation layer should not depend on
    the GUI/analysis layer's wrapping helper).
    """
    width = high - low
    out = (angles - low) % width + low
    high_minus = np.nextafter(high, -np.inf)
    return np.where(out >= high_minus, high_minus, out)


@runtime_checkable
class LengthSampler(Protocol):
    """Structural type for length samplers.

    Implementations are typically frozen dataclasses constructed once
    per simulation from a :class:`~sarcostat.simulation.chain.config.Config`
    and called many times by the simulator.
    """

    def sample(self, n: int, rng: np.random.Generator) -> np.ndarray:
        """Draw ``n`` non-negative length samples."""
        ...


@runtime_checkable
class AngleSampler(Protocol):
    """Structural type for base-angle samplers.

    The simulator passes the per-trial concentration parameter
    explicitly so that one sampler instance can serve a parameter
    sweep over kappa.
    """

    def sample(self, n: int, kappa: float, rng: np.random.Generator) -> np.ndarray:
        """Draw ``n`` angle samples in ``[0, 2*pi)`` at concentration kappa."""
        ...


@dataclass(frozen=True)
class TruncnormLengthSampler:
    """Truncated normal length sampler.

    Parameters
    ----------
    mean, std : float
        Pre-truncation mean and standard deviation of the underlying
        Gaussian.
    lower, upper : float
        Hard bounds of the truncated support.
    """

    mean: float
    std: float
    lower: float
    upper: float

    def sample(self, n: int, rng: np.random.Generator) -> np.ndarray:
        """Sample ``n`` lengths.

        For a degenerate ``std <= 0`` the sample collapses to ``mean``
        clipped to be non-negative; for a degenerate ``upper <= lower``
        the sampler returns the lower bound.
        """
        if self.std <= 0:
            return np.full(n, max(self.mean, 0.0))
        a = (self.lower - self.mean) / self.std
        b = (self.upper - self.mean) / self.std
        return np.asarray(
            truncnorm.rvs(a=a, b=b, loc=self.mean, scale=self.std, size=n, random_state=rng),
            dtype=float,
        )


@dataclass(frozen=True)
class LognormLengthSampler:
    """Log-normal length sampler matched to a target mean and std.

    The underlying normal parameters are obtained from method-of-moments
    matching: ``sigma^2 = ln(1 + (std/mean)^2)``,
    ``mu_n = ln(mean) - sigma^2 / 2``.

    Parameters
    ----------
    mean, std : float
        Target mean and std of the resulting (positive) variable.
    lower, upper : float
        Output is clipped to ``[lower, upper]`` after sampling.
    """

    mean: float
    std: float
    lower: float
    upper: float

    def sample(self, n: int, rng: np.random.Generator) -> np.ndarray:
        """Sample ``n`` lengths from the matched log-normal distribution."""
        if self.mean <= 0 or self.std <= 0:
            return np.full(n, max(self.mean, self.lower))
        sigma2 = np.log(1.0 + (self.std**2) / (self.mean**2))
        sigma = float(np.sqrt(sigma2))
        mu_n = float(np.log(self.mean) - sigma2 / 2.0)
        x = rng.lognormal(mean=mu_n, sigma=sigma, size=n)
        return np.clip(x, self.lower, self.upper)


@dataclass(frozen=True)
class UniformAngleSampler:
    """Uniform angular sampler over ``[0, 2*pi)``.

    Used as the trivial sampler when ``kappa == 0`` regardless of the
    nominal angular family.
    """

    def sample(self, n: int, kappa: float, rng: np.random.Generator) -> np.ndarray:
        """Return ``n`` angles drawn uniformly from ``[0, 2*pi)``."""
        return rng.uniform(0.0, TWO_PI, n)


@dataclass(frozen=True)
class VonMisesAngleSampler:
    """Von Mises angular sampler.

    Parameters
    ----------
    mean : float
        Mean direction in radians.
    """

    mean: float

    def sample(self, n: int, kappa: float, rng: np.random.Generator) -> np.ndarray:
        """Sample ``n`` angles at concentration ``kappa``.

        Falls back to the uniform sampler when ``kappa == 0``.
        """
        if kappa == 0:
            return rng.uniform(0.0, TWO_PI, n)
        return _wrap_strict(
            vonmises.rvs(max(kappa, 1e-6), loc=self.mean, size=n, random_state=rng),
            0.0,
            TWO_PI,
        )


@dataclass(frozen=True)
class WrappedNormalAngleSampler:
    """Wrapped normal angular sampler matched to a von Mises kappa.

    The wrapping sigma is derived from the equality of mean resultant
    lengths between the von Mises (``R = I1(kappa) / I0(kappa)``) and
    wrapped normal (``R = exp(-sigma^2 / 2)``) families: see Mardia and
    Jupp (2000) Section 3.5.4. Clipping to ``[1e-6, 0.999999]`` guards
    against Bessel-function inaccuracy at very small or very large
    kappa.

    Parameters
    ----------
    mean : float
        Mean direction in radians.
    """

    mean: float

    def sample(self, n: int, kappa: float, rng: np.random.Generator) -> np.ndarray:
        """Sample ``n`` angles at concentration ``kappa``.

        Falls back to the uniform sampler when ``kappa == 0``.
        """
        if kappa == 0:
            return rng.uniform(0.0, TWO_PI, n)
        R_vm = float(np.clip(i1e(kappa) / i0e(kappa), 1e-6, 0.999999))
        sigma = float(np.sqrt(-2.0 * np.log(R_vm)))
        base = rng.normal(loc=self.mean, scale=sigma, size=n)
        return _wrap_strict(base, 0.0, TWO_PI)


LengthFactory = Callable[..., LengthSampler]
AngleFactory = Callable[..., AngleSampler]

LENGTH_SAMPLERS: dict[str, LengthFactory] = {}
ANGLE_SAMPLERS: dict[str, AngleFactory] = {}


def register_length(name: str) -> Callable[[LengthFactory], LengthFactory]:
    """Register a length-sampler factory under ``name`` (decorator form)."""

    def _decorator(factory: LengthFactory) -> LengthFactory:
        LENGTH_SAMPLERS[name] = factory
        return factory

    return _decorator


def register_angle(name: str) -> Callable[[AngleFactory], AngleFactory]:
    """Register an angle-sampler factory under ``name`` (decorator form)."""

    def _decorator(factory: AngleFactory) -> AngleFactory:
        ANGLE_SAMPLERS[name] = factory
        return factory

    return _decorator


@register_length("truncnorm")
def _make_truncnorm(mean: float, std: float, lower: float, upper: float) -> LengthSampler:
    return TruncnormLengthSampler(mean=mean, std=std, lower=lower, upper=upper)


@register_length("lognorm")
def _make_lognorm(mean: float, std: float, lower: float, upper: float) -> LengthSampler:
    return LognormLengthSampler(mean=mean, std=std, lower=lower, upper=upper)


@register_angle("uniform")
def _make_uniform_angle(mean: float) -> AngleSampler:
    return UniformAngleSampler()


@register_angle("vonmises")
def _make_vonmises(mean: float) -> AngleSampler:
    return VonMisesAngleSampler(mean=mean)


@register_angle("wrapped_normal")
def _make_wrapped_normal(mean: float) -> AngleSampler:
    return WrappedNormalAngleSampler(mean=mean)


def make_length_sampler(
    name: str, *, mean: float, std: float, lower: float, upper: float
) -> LengthSampler:
    """Build a length sampler from a registered name.

    Parameters
    ----------
    name : str
        Registry key (``"truncnorm"`` or ``"lognorm"``).
    mean, std, lower, upper : float
        Distribution parameters; meaning depends on the sampler.

    Returns
    -------
    LengthSampler
        A frozen dataclass implementing :class:`LengthSampler`.

    Raises
    ------
    KeyError
        If ``name`` is not registered. The error message lists the
        available names.
    """
    try:
        factory = LENGTH_SAMPLERS[name]
    except KeyError as exc:
        available = ", ".join(sorted(LENGTH_SAMPLERS))
        raise KeyError(f"Unknown length distribution {name!r}; available: {available}") from exc
    return factory(mean=mean, std=std, lower=lower, upper=upper)


def make_angle_sampler(name: str, *, mean: float) -> AngleSampler:
    """Build an angle sampler from a registered name.

    Parameters
    ----------
    name : str
        Registry key (``"uniform"``, ``"vonmises"``, or ``"wrapped_normal"``).
    mean : float
        Mean direction in radians (ignored for ``uniform``).

    Returns
    -------
    AngleSampler
        A frozen dataclass implementing :class:`AngleSampler`.

    Raises
    ------
    KeyError
        If ``name`` is not registered. The error message lists the
        available names.
    """
    try:
        factory = ANGLE_SAMPLERS[name]
    except KeyError as exc:
        available = ", ".join(sorted(ANGLE_SAMPLERS))
        raise KeyError(f"Unknown angular distribution {name!r}; available: {available}") from exc
    return factory(mean=mean)


__all__ = [
    "LengthSampler",
    "AngleSampler",
    "TruncnormLengthSampler",
    "LognormLengthSampler",
    "UniformAngleSampler",
    "VonMisesAngleSampler",
    "WrappedNormalAngleSampler",
    "LENGTH_SAMPLERS",
    "ANGLE_SAMPLERS",
    "register_length",
    "register_angle",
    "make_length_sampler",
    "make_angle_sampler",
]
