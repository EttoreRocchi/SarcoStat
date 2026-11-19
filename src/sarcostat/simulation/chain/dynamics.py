"""Step-angle adjustments applied on top of the base sampler.

The :class:`AngularDynamics` Protocol abstracts the post-processing of
raw step angles produced by an :class:`~sarcostat.simulation._distributions.AngleSampler`.
The concrete :class:`StandardDynamics` reproduces the previous chain
simulator behaviour: an AR(1) correlation on the step series, an
optional heading-persistence pull toward the previous heading, and an
optional curvature term: either a constant rotation of every heading
(``offset``: a mean-direction offset that leaves LR and MVL unchanged) or
a progressive bend that turns the chain by the same angle at every
segment (``arc``: a myofibril of constant curvature).

Each effect is a separate small helper so that no function exceeds CC
10. Callers can compose dynamics by constructing
:class:`StandardDynamics` with selected toggles, or implement a custom
``apply`` method to swap the heading model entirely.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np

from sarcostat.core.circular_stats import to_minuspi_pi, wrap_strict

TWO_PI = 2.0 * np.pi


@runtime_checkable
class AngularDynamics(Protocol):
    """Structural type for step-angle dynamics."""

    def apply(self, raw_angles: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        """Return adjusted step angles wrapped to ``[0, 2*pi)``."""
        ...


def _ar1_correlation(angles: np.ndarray, mean: float, rho: float) -> np.ndarray:
    """AR(1) correlation on the deviation from the mean direction.

    Parameters
    ----------
    angles : numpy.ndarray
        Raw step angles.
    mean : float
        Mean step direction (radians).
    rho : float
        Lag-1 correlation in [0, 1).
    """
    eps = to_minuspi_pi(angles - mean)
    for i in range(1, len(eps)):
        eps[i] = rho * eps[i - 1] + np.sqrt(1.0 - rho**2) * eps[i]
    return mean + eps


def _heading_persistence(angles: np.ndarray, decay: float) -> np.ndarray:
    """Bias each step angle toward the previous step angle.

    Implements a first-order persistent recursion
    ``phi_i = (1 - decay) * theta_i + decay * phi_{i-1}``, with
    ``phi_0 = theta_0``. The ``decay`` factor in [0, 1) controls the
    persistence strength: 0 disables persistence; values close to 1
    produce strong heading inertia.
    """
    n = len(angles)
    if n == 0:
        return angles.copy()
    out = np.zeros(n)
    out[0] = float(angles[0])
    for i in range(1, n):
        delta = float(to_minuspi_pi(np.array([float(angles[i]) - out[i - 1]]))[0])
        out[i] = out[i - 1] + (1.0 - decay) * delta
    return out


@dataclass(frozen=True)
class StandardDynamics:
    """Standard chain dynamics: correlation, persistence, curvature.

    Parameters
    ----------
    angular_mean : float
        Mean step direction (radians).
    correlation : float
        AR(1) lag-1 correlation; must be in [0, 1).
    enable_persistence : bool
        Whether to apply heading persistence.
    persistence_decay : float
        Persistence-decay factor in [0, 1).
    enable_curvature : bool
        Whether to apply the curvature term.
    curvature_strength : float
        Heading offset (``offset`` mode) or turning angle per segment
        (``arc`` mode), in radians.
    curvature_mode : {"offset", "arc"}, optional
        ``offset`` (default) rotates every heading by the same angle;
        ``arc`` adds ``i * curvature_strength`` to the i-th heading.
    """

    angular_mean: float
    correlation: float
    enable_persistence: bool
    persistence_decay: float
    enable_curvature: bool
    curvature_strength: float
    curvature_mode: str = "offset"

    def apply(self, raw_angles: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        """Compose the active effects in fixed order.

        The order matches the original chain simulator: correlation,
        then persistence, then curvature. Returns angles wrapped to
        ``[0, 2*pi)``.
        """
        angles = raw_angles.copy()
        rho = float(np.clip(self.correlation, 0.0, 0.999))
        if rho > 0:
            angles = _ar1_correlation(angles, self.angular_mean, rho)
        if self.enable_persistence:
            decay = float(np.clip(self.persistence_decay, 0.0, 0.999))
            angles = _heading_persistence(angles, decay)
        if self.enable_curvature and self.curvature_strength != 0:
            if self.curvature_mode == "arc":
                angles = angles + self.curvature_strength * np.arange(len(angles))
            else:
                angles = angles + self.curvature_strength
        return wrap_strict(angles, 0.0, TWO_PI)


__all__ = ["AngularDynamics", "StandardDynamics"]
