"""Z-line register between adjacent myofibrils as a circular phase.

Each Z-line of a myofibril B is paired with the nearest Z-line of the
adjacent myofibril A along their common axis. The signed offset
``delta`` between the two, divided by the local sarcomere length ``L``,
is a phase ``phi = 2 pi delta / L`` on the circle: Z-lines in register
have ``phi = 0`` and a half-sarcomere stagger has ``phi = pi``. The phase
is therefore summarised with circular statistics:

- the register index (mean resultant length of ``phi``) and the register
  spread in length units;
- a V-test against 0 (perfect register), whose reference direction is
  fixed by the geometry rather than chosen by the user;
- the mean absolute offset, which is the H parameter of Cisterna et al.
  (2021) and equals ``L/4`` on average for Z-lines out of register;
- the register correlation ``G(k) = mean cos(phi_{i+k} - phi_i)`` and a
  permutation test of serial dependence. Laterally coupled myofibrils
  keep a stationary offset (flat ``G``); without coupling, independent
  sarcomere lengths make the offset drift like a random walk and
  ``G(k) = exp(-(2 pi CV)^2 k)`` (:func:`expected_register_correlation`).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from .chain_statistics import (
    DEFAULT_N_PERMUTATIONS,
    serial_dependence_test,
    tangent_correlation,
)
from .circular_stats import perform_rayleigh_test, perform_v_test

Point = tuple[float, float]
LineSegment = tuple[Point, Point]


def expected_register_correlation(lag: np.ndarray, cv: float) -> np.ndarray:
    """Register correlation expected without lateral coupling.

    With independent sarcomere lengths of coefficient of variation ``cv``
    in both myofibrils, the offset after ``k`` sarcomeres has variance
    ``2 k (cv L)^2`` and ``G(k) = exp(-(2 pi cv)^2 k)``.
    """
    if not (np.isfinite(cv) and cv >= 0):
        raise ValueError("cv must be a non-negative finite number")
    g: np.ndarray = np.exp(-((2.0 * np.pi * cv) ** 2) * np.asarray(lag, dtype=float))
    return g


def register_decay_length(cv: float) -> float:
    """Number of sarcomeres over which uncoupled myofibrils lose register.

    Returns ``1 / (2 pi cv)^2``, the lag at which
    :func:`expected_register_correlation` falls to ``1/e``; about five
    sarcomeres for ``cv = 0.07``. Infinite for ``cv = 0``.
    """
    if not (np.isfinite(cv) and cv >= 0):
        raise ValueError("cv must be a non-negative finite number")
    return float("inf") if cv == 0 else float(1.0 / (2.0 * np.pi * cv) ** 2)


@dataclass
class RegisterMetrics:
    """Register of the Z-lines of myofibril B relative to myofibril A.

    Attributes
    ----------
    n_pairs : int
        Number of paired Z-lines.
    phases : list of float
        Phases ``phi`` in radians, in (-pi, pi], ordered along the axis.
        The axis points left to right in the image (top to bottom for a
        vertical myofibril); a positive phase means that the Z-line of B
        lies further along the axis than its partner in A.
    offsets : list of float
        Signed offsets ``delta`` in length units.
    local_sarcomere_lengths : list of float
        Sarcomere length of A used to convert each offset into a phase.
    register_index : float
        Mean resultant length of the phases (1 = perfect, constant
        offset; 0 = no register).
    mean_phase : float
        Circular mean phase (radians): the systematic stagger.
    register_spread : float
        Circular standard deviation of the phases converted to length
        units, ``L / (2 pi) * sqrt(-2 ln R)``.
    v_statistic, v_p_value : float
        V-test of the phases against 0 (perfect register).
    rayleigh_z, rayleigh_p : float
        Rayleigh test of the phases (any register vs none).
    h_mean : float
        Mean absolute offset, i.e. the H parameter of Cisterna et al.
        (2021) for these pairs.
    h_unregistered : float
        ``L / 4``: the expected ``h_mean`` for Z-lines out of register.
    lateral_distance : float
        Mean distance between the two myofibrils across the axis.
    correlation_lags, correlation_values : list
        Register correlation ``G(k)``.
    serial_dependence_statistic, serial_dependence_p : float or None
        ``D`` and permutation p-value for drifting register; ``None``
        for fewer than three pairs.
    unit : str
        Length unit.
    """

    n_pairs: int
    phases: list[float]
    offsets: list[float]
    local_sarcomere_lengths: list[float]
    register_index: float
    mean_phase: float
    register_spread: float
    v_statistic: float
    v_p_value: float
    rayleigh_z: float
    rayleigh_p: float
    h_mean: float
    h_unregistered: float
    lateral_distance: float
    correlation_lags: list[int]
    correlation_values: list[float]
    serial_dependence_statistic: float | None
    serial_dependence_p: float | None
    unit: str = "pixels"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dictionary view."""
        return asdict(self)

    def summary_string(self) -> str:
        """Return a human-readable plain-text summary."""
        if self.serial_dependence_p is None:
            drift = "Drift test: not run (fewer than 3 pairs)"
        else:
            drift = f"Drift test (permutation): D = {self.serial_dependence_statistic:.3f}, p = {self.serial_dependence_p:.4f}"
        return (
            f"Z-line register\n"
            f"Paired Z-lines: {self.n_pairs}\n"
            f"Register index R: {self.register_index:.4f}\n"
            f"Mean phase: {np.degrees(self.mean_phase):.1f} deg\n"
            f"Register spread: {self.register_spread:.2f} {self.unit}\n"
            f"Mean |offset| (H): {self.h_mean:.2f} {self.unit} "
            f"(out of register: {self.h_unregistered:.2f})\n"
            f"V-test vs 0: V = {self.v_statistic:.3f}, p = {self.v_p_value:.4f}\n"
            f"Rayleigh: Z = {self.rayleigh_z:.3f}, p = {self.rayleigh_p:.4f}\n"
            f"{drift}"
        )


def _midpoints(zlines: list[LineSegment]) -> np.ndarray:
    arr = np.asarray(zlines, dtype=float)
    if arr.ndim != 3 or arr.shape[1:] != (2, 2):
        raise ValueError("Z-lines must be a list of ((x1, y1), (x2, y2)) segments")
    if not np.all(np.isfinite(arr)):
        raise ValueError("Z-line coordinates must be finite")
    midpoints: np.ndarray = arr.mean(axis=1)
    return midpoints


def _orient(axis: np.ndarray) -> np.ndarray:
    """Point ``axis`` left to right in the image (top to bottom if vertical)."""
    if axis[0] < -1e-12 or (abs(axis[0]) <= 1e-12 and axis[1] < 0):
        return -axis
    return axis


def _chain_direction(mid: np.ndarray) -> np.ndarray:
    """Principal axis of the Z-line midpoints (independent of their order)."""
    centred = mid - mid.mean(axis=0)
    if np.allclose(centred, 0.0):
        raise ValueError("Z-lines of a myofibril must not all coincide")
    _, _, vt = np.linalg.svd(centred, full_matrices=False)
    return _orient(vt[0])


def z_line_register(
    zlines_a: list[LineSegment],
    zlines_b: list[LineSegment],
    pixel_to_unit: float = 1.0,
    unit: str = "pixels",
    n_permutations: int = DEFAULT_N_PERMUTATIONS,
    seed: int | None = 0,
    max_lag: int | None = None,
) -> RegisterMetrics:
    """Register of the Z-lines of myofibril B relative to the adjacent myofibril A.

    Parameters
    ----------
    zlines_a : list of LineSegment
        Z-lines of the reference myofibril (at least two), as annotated
        in linearity mode; order and endpoint order do not matter.
    zlines_b : list of LineSegment
        Z-lines of the adjacent myofibril (at least one inside the span
        of A).
    pixel_to_unit : float, optional
        Conversion from pixels to physical units (default 1.0).
    unit : str, optional
        Unit label (default ``"pixels"``).
    n_permutations : int, optional
        Permutations of the drift test (default 999).
    seed : int, optional
        Seed of the drift test (default 0, reproducible).
    max_lag : int, optional
        Largest lag of the register correlation.

    Returns
    -------
    RegisterMetrics

    Raises
    ------
    ValueError
        If A has fewer than two Z-lines, no Z-line of B falls within
        half a sarcomere of the span of A, or the inputs are malformed.
    """
    if not (np.isfinite(pixel_to_unit) and pixel_to_unit > 0):
        raise ValueError("pixel_to_unit must be a positive finite number")
    mid_a = _midpoints(zlines_a) * pixel_to_unit
    mid_b = _midpoints(zlines_b) * pixel_to_unit
    if len(mid_a) < 2:
        raise ValueError("Myofibril A needs at least 2 Z-lines")

    axis = _chain_direction(mid_a)
    if len(mid_b) >= 2:
        dir_b = _chain_direction(mid_b)
        resultant = axis + (dir_b if np.dot(dir_b, axis) >= 0 else -dir_b)
        axis = _orient(resultant / np.linalg.norm(resultant))
    normal = np.array([-axis[1], axis[0]])

    s_a = np.sort(mid_a @ axis)
    order_b = np.argsort(mid_b @ axis)
    s_b = (mid_b @ axis)[order_b]
    spacing = np.diff(s_a)
    if np.any(spacing <= 0):
        raise ValueError("Z-lines of myofibril A must be distinct along the axis")

    lo, hi = s_a[0] - spacing[0] / 2.0, s_a[-1] + spacing[-1] / 2.0
    keep = (s_b >= lo) & (s_b <= hi)
    s_b = s_b[keep]
    if s_b.size == 0:
        raise ValueError("No Z-line of myofibril B lies alongside myofibril A")

    nearest = np.abs(s_b[:, None] - s_a[None, :]).argmin(axis=1)
    offsets = s_b - s_a[nearest]
    # Local sarcomere length: the A spacing on the side of the offset.
    idx = np.clip(np.where(offsets >= 0, nearest, nearest - 1), 0, spacing.size - 1)
    local_len = spacing[idx]
    phases = np.angle(np.exp(2j * np.pi * offsets / local_len))

    resultant = np.mean(np.exp(1j * phases))
    r = float(min(1.0, np.abs(resultant)))
    mean_len = float(np.mean(local_len))
    spread = mean_len / (2.0 * np.pi) * np.sqrt(-2.0 * np.log(r)) if r > 0 else float("inf")
    v_stat, v_p = perform_v_test(phases, 0.0)
    ray_z, ray_p = perform_rayleigh_test(phases)
    lateral = float(np.mean(mid_b @ normal) - np.mean(mid_a @ normal))
    corr = tangent_correlation(phases, max_lag) if phases.size >= 2 else None

    d_stat: float | None = None
    d_p: float | None = None
    if phases.size >= 3:
        test = serial_dependence_test(phases, n_resamples=n_permutations, seed=seed)
        d_stat, d_p = test.statistic, test.p_value

    return RegisterMetrics(
        n_pairs=int(phases.size),
        phases=[float(p) for p in phases],
        offsets=[float(o) for o in offsets],
        local_sarcomere_lengths=[float(v) for v in local_len],
        register_index=r,
        mean_phase=float(np.angle(resultant)),
        register_spread=float(spread),
        v_statistic=float(v_stat),
        v_p_value=float(v_p),
        rayleigh_z=float(ray_z),
        rayleigh_p=float(ray_p),
        h_mean=float(np.mean(np.abs(offsets))),
        h_unregistered=mean_len / 4.0,
        lateral_distance=abs(lateral),
        correlation_lags=list(corr.lags) if corr else [],
        correlation_values=list(corr.values) if corr else [],
        serial_dependence_statistic=d_stat,
        serial_dependence_p=d_p,
        unit=unit,
    )


__all__ = [
    "RegisterMetrics",
    "expected_register_correlation",
    "register_decay_length",
    "z_line_register",
]
