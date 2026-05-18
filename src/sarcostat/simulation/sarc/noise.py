"""Noise models for synthetic sarcomere annotations.

The :class:`NoiseModel` Protocol defines the single
:meth:`~NoiseModel.apply` method consumed by the metrics layer and the
two pipelines. Two concrete implementations are provided:

- :class:`GaussianNoise` adds isotropic 2D Gaussian noise with the same
  sigma in x and y.
- :class:`AnisotropicNoise` adds independent noise along and
  perpendicular to a (user-supplied or inferred) line direction, which
  matches the empirical pattern of human annotation error.

Use :func:`make_noise_model` to build the right model from a
:class:`SingleSarcomereConfig` without dispatching on a string at
every call site.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np

from sarcostat.core.sarcomere_morphometry import Point

from .config import SingleSarcomereConfig


@runtime_checkable
class NoiseModel(Protocol):
    """Structural type for sarcomere-annotation noise models."""

    def apply(
        self,
        points: dict[str, Point],
        rng: np.random.Generator,
        *,
        line_direction: float | None = None,
    ) -> dict[str, Point]:
        """Return a fresh dict with each point perturbed by the model."""
        ...


@dataclass(frozen=True)
class GaussianNoise:
    """Isotropic 2D Gaussian noise.

    Parameters
    ----------
    sigma : float
        Standard deviation in both axes (in the same units as the
        input coordinates). ``sigma <= 0`` returns the input unchanged.
    """

    sigma: float

    def apply(
        self,
        points: dict[str, Point],
        rng: np.random.Generator,
        *,
        line_direction: float | None = None,
    ) -> dict[str, Point]:
        """Perturb each point with independent N(0, sigma) noise.

        The ``line_direction`` argument is accepted for Protocol
        compatibility but ignored.
        """
        if self.sigma <= 0:
            return dict(points)
        return {
            key: (x + float(rng.normal(0.0, self.sigma)), y + float(rng.normal(0.0, self.sigma)))
            for key, (x, y) in points.items()
        }


def _infer_line_direction(points: dict[str, Point]) -> float:
    """Estimate the sarcomere axis from the Z-line midpoints."""
    ax, ay = points["A"]
    bx, by = points["B"]
    ex, ey = points["E"]
    fx, fy = points["F"]
    m1 = ((ax + bx) / 2.0, (ay + by) / 2.0)
    m2 = ((ex + fx) / 2.0, (ey + fy) / 2.0)
    return float(np.arctan2(m2[1] - m1[1], m2[0] - m1[0]))


@dataclass(frozen=True)
class AnisotropicNoise:
    """Anisotropic Gaussian noise along/perpendicular to a line.

    Parameters
    ----------
    sigma_along, sigma_perpendicular : float
        Standard deviations in the line-aligned coordinate system. If
        both are non-positive the input is returned unchanged.
    """

    sigma_along: float
    sigma_perpendicular: float

    def apply(
        self,
        points: dict[str, Point],
        rng: np.random.Generator,
        *,
        line_direction: float | None = None,
    ) -> dict[str, Point]:
        """Perturb each point in the line-aligned frame, then rotate back.

        If ``line_direction`` is ``None`` the sarcomere axis is inferred
        from the Z-line midpoints (A/B and E/F).
        """
        if self.sigma_along <= 0 and self.sigma_perpendicular <= 0:
            return dict(points)
        theta = line_direction if line_direction is not None else _infer_line_direction(points)
        cos_t, sin_t = float(np.cos(theta)), float(np.sin(theta))
        out: dict[str, Point] = {}
        for key, (x, y) in points.items():
            n_along = float(rng.normal(0.0, self.sigma_along)) if self.sigma_along > 0 else 0.0
            n_perp = (
                float(rng.normal(0.0, self.sigma_perpendicular))
                if self.sigma_perpendicular > 0
                else 0.0
            )
            out[key] = (
                x + (n_along * cos_t - n_perp * sin_t),
                y + (n_along * sin_t + n_perp * cos_t),
            )
        return out


def make_noise_model(cfg: SingleSarcomereConfig, sigma: float) -> NoiseModel:
    """Build the noise model that matches ``cfg.noise_model``.

    The isotropic model takes the per-call ``sigma`` directly; the
    anisotropic model uses the ``anisotropic_sigma_along`` and
    ``anisotropic_sigma_perpendicular`` parameters from the config and
    ignores ``sigma`` (matching the original behaviour).
    """
    if cfg.noise_model == "isotropic":
        return GaussianNoise(sigma=sigma)
    return AnisotropicNoise(
        sigma_along=cfg.anisotropic_sigma_along,
        sigma_perpendicular=cfg.anisotropic_sigma_perpendicular,
    )


__all__ = ["NoiseModel", "GaussianNoise", "AnisotropicNoise", "make_noise_model"]
