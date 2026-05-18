"""Synthetic single-sarcomere generator with known ground truth.

:class:`SarcomereGenerator` is a small class that owns no state besides
a configuration object and exposes three independent generation
methods: :meth:`~SarcomereGenerator.from_morphometry`,
:meth:`~SarcomereGenerator.cisterna`, and
:meth:`~SarcomereGenerator.at_boundary`. Each method has CC well below
10 thanks to the shared :func:`_apply_rigid_transform` helper.
"""

from __future__ import annotations

import numpy as np
from scipy.stats import truncnorm

from sarcostat.core.sarcomere_morphometry import (
    Point,
    SarcomereGeometry,
    calculate_sarcomere_geometry,
)

from .config import Condition, SingleSarcomereConfig


def _sample_truncated_normal(
    mean: float,
    std: float,
    rng: np.random.Generator,
    *,
    min_val: float | None = None,
    max_val: float | None = None,
    size: int = 1,
) -> np.ndarray:
    """Sample from a truncated normal with sensible defaults.

    For ``std <= 0`` the sample collapses to ``mean``. For unspecified
    bounds the support is ``[mean - 3*std, mean + 3*std]``, with the
    lower bound additionally clipped to ``0.01`` to guarantee strictly
    positive lengths.
    """
    if std <= 0:
        return np.full(size, mean)
    lo = max(mean - 3 * std, 0.01) if min_val is None else min_val
    hi = mean + 3 * std if max_val is None else max_val
    a = (lo - mean) / std
    b = (hi - mean) / std
    return truncnorm.rvs(a=a, b=b, loc=mean, scale=std, size=size, random_state=rng)


def _canonical_six_points(
    sarcomere_length: float, mean_z_length: float, m_line_length: float, z_asymmetry: float
) -> dict[str, Point]:
    """Build canonical (axis-aligned) 6-point coordinates."""
    half_sarc = sarcomere_length / 2.0
    z1 = mean_z_length + z_asymmetry / 2.0
    z2 = mean_z_length - z_asymmetry / 2.0
    return {
        "A": (-half_sarc, -z1 / 2.0),
        "B": (-half_sarc, +z1 / 2.0),
        "C": (0.0, -m_line_length / 2.0),
        "D": (0.0, +m_line_length / 2.0),
        "E": (+half_sarc, -z2 / 2.0),
        "F": (+half_sarc, +z2 / 2.0),
    }


def _apply_rigid_transform(
    points: dict[str, Point], orientation: float, center: tuple[float, float]
) -> dict[str, Point]:
    """Apply rotation by ``orientation`` then translation by ``center``."""
    cos_t, sin_t = float(np.cos(orientation)), float(np.sin(orientation))
    cx, cy = center
    rotated: dict[str, Point] = {}
    for key, (x, y) in points.items():
        rx = x * cos_t - y * sin_t
        ry = x * sin_t + y * cos_t
        rotated[key] = (rx + cx, ry + cy)
    return rotated


def _geometry_from_points(points: dict[str, Point]) -> SarcomereGeometry:
    """Wrap :func:`calculate_sarcomere_geometry` with the standard 6 keys."""
    return calculate_sarcomere_geometry(
        point_a=points["A"],
        point_b=points["B"],
        point_c=points["C"],
        point_d=points["D"],
        point_e=points["E"],
        point_f=points["F"],
        unit="nm",
    )


class SarcomereGenerator:
    """Generate ground-truth synthetic sarcomeres.

    Parameters
    ----------
    cfg : SingleSarcomereConfig
        Source of the Cisterna parameters and (when relevant) the
        boundary-analysis defaults.
    """

    def __init__(self, cfg: SingleSarcomereConfig) -> None:
        self.cfg = cfg

    def from_morphometry(
        self,
        sarcomere_length: float,
        target_asr: float,
        target_cur: float,
        *,
        z_line_asymmetry: float = 0.0,
        center: tuple[float, float] = (0.0, 0.0),
        orientation: float = 0.0,
    ) -> dict[str, Point]:
        """Reverse-engineer 6 points that produce the target morphometry.

        Parameters
        ----------
        sarcomere_length : float
            Target distance between Z-line midpoints.
        target_asr : float
            Target aspect ratio (sarcomere_length / mean Z-length).
        target_cur : float
            Target curviness (sarcomere_length / M-line length).
        z_line_asymmetry : float, optional
            Difference between Z1 and Z2 lengths (default 0).
        center : tuple of float, optional
            Translation applied after rotation (default origin).
        orientation : float, optional
            Rotation angle in radians (default 0).
        """
        mean_z_length = sarcomere_length / target_asr
        m_line_length = sarcomere_length / target_cur
        canonical = _canonical_six_points(
            sarcomere_length, mean_z_length, m_line_length, z_line_asymmetry
        )
        return _apply_rigid_transform(canonical, orientation, center)

    def _condition_parameters(
        self, condition: Condition
    ) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
        """Return ``(z_params, sarc_params, m_params)`` for the condition."""
        if condition is Condition.EUPLOID:
            return (
                self.cfg.euploid_z_length,
                self.cfg.euploid_sarcomere_length,
                self.cfg.euploid_m_line_length,
            )
        if condition is Condition.TRISOMIC:
            return (
                self.cfg.trisomic_z_length,
                self.cfg.trisomic_sarcomere_length,
                self.cfg.trisomic_m_line_length,
            )
        raise ValueError(f"Cisterna sampling does not support condition {condition!r}")

    def cisterna(
        self, condition: Condition, rng: np.random.Generator
    ) -> tuple[dict[str, Point], SarcomereGeometry]:
        """Sample a ground-truth sarcomere from the Cisterna parameter set.

        Parameters
        ----------
        condition : Condition
            ``EUPLOID`` or ``TRISOMIC``. ``CUSTOM`` is unsupported here;
            use :meth:`from_morphometry` directly.
        rng : numpy.random.Generator
            RNG used for the four truncated-normal samples and the
            random orientation.
        """
        z_params, sarc_params, m_params = self._condition_parameters(condition)
        z1 = float(_sample_truncated_normal(*z_params, rng=rng)[0])
        z2 = float(_sample_truncated_normal(*z_params, rng=rng)[0])
        m_length = float(_sample_truncated_normal(*m_params, rng=rng)[0])
        sarc_length = float(_sample_truncated_normal(*sarc_params, rng=rng)[0])
        mean_z = (z1 + z2) / 2.0
        target_asr = sarc_length / mean_z
        target_cur = sarc_length / m_length
        points = self.from_morphometry(
            sarcomere_length=sarc_length,
            target_asr=target_asr,
            target_cur=target_cur,
            z_line_asymmetry=z1 - z2,
            orientation=float(rng.uniform(0.0, 2 * np.pi)),
        )
        return points, _geometry_from_points(points)

    def at_boundary(
        self,
        boundary_offset: float,
        rng: np.random.Generator,
        *,
        base_asr: float = 4.0,
        sarcomere_length: float = 1638.0,
    ) -> tuple[dict[str, Point], SarcomereGeometry]:
        """Generate a sarcomere at the AsR / Cur classification boundary.

        Parameters
        ----------
        boundary_offset : float
            ``Cur - AsR``; positive sits in HOURGLASS territory, negative
            in BARREL territory, exactly zero on the RECTANGULAR line.
        rng : numpy.random.Generator
            Used for the random orientation only.
        base_asr : float, optional
            Aspect ratio at the boundary (default 4.0).
        sarcomere_length : float, optional
            Sarcomere length in nanometres (default 1638.0).
        """
        target_asr = base_asr
        target_cur = base_asr + boundary_offset
        points = self.from_morphometry(
            sarcomere_length=sarcomere_length,
            target_asr=target_asr,
            target_cur=target_cur,
            orientation=float(rng.uniform(0.0, 2 * np.pi)),
        )
        return points, _geometry_from_points(points)


__all__ = ["SarcomereGenerator"]
