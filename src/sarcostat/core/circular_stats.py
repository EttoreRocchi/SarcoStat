"""Circular statistics primitives for sarcomeric sequences.

The module provides:

- moment-based summaries (mean vector length, circular mean, circular
  standard deviation, circular variance, von Mises concentration),
- Rayleigh and V tests (uniformity and direction-of-concentration),
- bootstrap confidence intervals for the circular mean and the mean
  vector length,
- a comprehensive :class:`CircularStatisticsReport` summary container,
- :class:`SequenceMetrics` for per-sequence analysis built by
  :func:`analyze_segment_sequence`.

The module has no I/O or plotting dependencies. JSON persistence is
provided by :mod:`sarcostat.core._serialization`. Hypothesis-test
interpretation is provided by :mod:`sarcostat.core._interpretation`.
"""

from __future__ import annotations

import warnings
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import numpy as np
import pingouin as pg

from ._interpretation import interpret_rayleigh_test, interpret_v_test

TWO_PI = 2.0 * np.pi
SIGNIFICANCE_LEVEL = 0.05
BOOTSTRAP_MIN_RECOMMENDED = 10
UNIFORM_R_THRESHOLD = 0.01
KAPPA_MAX = 1.0e6
_KAHAN_THRESHOLD = 100


def wrap_strict(angles: np.ndarray, low: float, high: float) -> np.ndarray:
    """Wrap angles to the half-open interval [low, high).

    Parameters
    ----------
    angles : numpy.ndarray
        Array of angles in radians.
    low : float
        Inclusive lower bound.
    high : float
        Exclusive upper bound.

    Returns
    -------
    numpy.ndarray
        Wrapped angles, with values exactly equal to ``high`` nudged just
        below it via :func:`numpy.nextafter` to enforce strict exclusion.
    """
    width = high - low
    out = (angles - low) % width + low
    high_minus = np.nextafter(high, -np.inf)
    return np.where(out >= high_minus, high_minus, out)


def to_minuspi_pi(angles: np.ndarray) -> np.ndarray:
    """Normalise angles to the open interval (-pi, pi).

    Parameters
    ----------
    angles : numpy.ndarray
        Angles in any range.

    Returns
    -------
    numpy.ndarray
        Angles wrapped into (-pi, pi). The boundaries are nudged inward
        with :func:`numpy.nextafter` to keep both endpoints open.
    """
    a01 = wrap_strict(angles, 0.0, TWO_PI)
    api = (a01 + np.pi) % TWO_PI - np.pi
    pi_minus = np.nextafter(np.pi, -np.inf)
    api = np.where(api >= pi_minus, pi_minus, api)
    minus_pi_plus = np.nextafter(-np.pi, np.inf)
    return np.where(api <= -np.pi, minus_pi_plus, api)


def calculate_linearity_ratio(head_to_tail: float, total_length: float) -> float:
    """Compute the linearity ratio LR = head_to_tail / total_length.

    Parameters
    ----------
    head_to_tail : float
        Euclidean distance from the start to the end of the sequence.
    total_length : float
        Sum of the individual segment lengths.

    Returns
    -------
    float
        The ratio in [0, 1]; returns 0 for a zero-length sequence.
    """
    return head_to_tail / total_length if total_length > 0 else 0.0


def calculate_mean_vector_length(angles: np.ndarray) -> float:
    """Compute the mean vector length R using pingouin.

    Parameters
    ----------
    angles : numpy.ndarray
        Angles in radians (any range; normalised internally).

    Returns
    -------
    float
        Mean resultant length in [0, 1], or NaN for empty input.
    """
    if len(angles) == 0:
        return float("nan")
    return float(pg.circ_r(to_minuspi_pi(angles)))


def perform_rayleigh_test(angles: np.ndarray) -> tuple[float, float]:
    """Run the Rayleigh test for uniformity on a circle.

    Parameters
    ----------
    angles : numpy.ndarray
        Angles in radians.

    Returns
    -------
    tuple of float
        ``(z_statistic, p_value)``. Empty input returns ``(0.0, 1.0)``.
    """
    if len(angles) == 0:
        return 0.0, 1.0
    z_stat, p_val = pg.circ_rayleigh(to_minuspi_pi(angles))
    return float(z_stat), float(p_val)


def perform_v_test(angles: np.ndarray, expected_dir: float = 0.0) -> tuple[float, float]:
    """Run the V-test for concentration about an expected mean direction.

    Parameters
    ----------
    angles : numpy.ndarray
        Angles in radians.
    expected_dir : float, optional
        Hypothesised mean direction in radians (default 0.0).

    Returns
    -------
    tuple of float
        ``(v_statistic, p_value)``. Empty input returns ``(0.0, 1.0)``.
    """
    if len(angles) == 0:
        return 0.0, 1.0
    ang = to_minuspi_pi(angles)
    mu0 = to_minuspi_pi(np.array([expected_dir]))[0]
    v_stat, p_val = pg.circ_vtest(ang, dir=mu0)
    return float(v_stat), float(p_val)


def calculate_circular_mean(angles: np.ndarray) -> float:
    """Compute the circular mean of a set of angles.

    Parameters
    ----------
    angles : numpy.ndarray
        Angles in radians.

    Returns
    -------
    float
        Circular mean in radians; 0.0 for empty input.
    """
    if len(angles) == 0:
        return 0.0
    return float(pg.circ_mean(to_minuspi_pi(angles)))


def calculate_circular_std(angles: np.ndarray) -> float:
    """Compute the circular standard deviation sigma = sqrt(-2 ln R).

    Parameters
    ----------
    angles : numpy.ndarray
        Angles in radians.

    Returns
    -------
    float
        Circular standard deviation in radians; ``inf`` for distributions
        whose mean resultant length R falls below
        :data:`UNIFORM_R_THRESHOLD` (the population is essentially uniform
        and sigma is mathematically undefined); NaN for empty input.
    """
    if angles.size == 0:
        return float("nan")

    a = to_minuspi_pi(angles)
    sin_sum = np.sin(a).sum(dtype=np.float64)
    cos_sum = np.cos(a).sum(dtype=np.float64)
    R = np.hypot(sin_sum, cos_sum) / a.size

    if R < UNIFORM_R_THRESHOLD:
        warnings.warn(
            f"Data appears uniform (R={R:.4f} < {UNIFORM_R_THRESHOLD}). "
            "Circular std is undefined for uniform distributions, returning inf.",
            UserWarning,
            stacklevel=2,
        )
        return float("inf")

    R = float(np.clip(R, UNIFORM_R_THRESHOLD, 1.0))
    return float(np.sqrt(-2.0 * np.log(R)))


def calculate_circular_variance(angles: np.ndarray) -> float:
    """Compute the circular variance V = 1 - R.

    Parameters
    ----------
    angles : numpy.ndarray
        Angles in radians.

    Returns
    -------
    float
        Circular variance in [0, 1]; NaN for empty input. V=0 indicates
        perfect concentration; V=1 indicates a uniform distribution.
    """
    if len(angles) == 0:
        return float("nan")
    R = calculate_mean_vector_length(angles)
    if np.isnan(R):
        return float("nan")
    return 1.0 - R


def calculate_concentration_parameter(angles: np.ndarray) -> float:
    """Estimate the von Mises concentration parameter kappa.

    Uses the smooth Banerjee et al. (2005) approximation
    ``kappa_hat = R * (2 - R^2) / (1 - R^2)``, which is monotonic and
    continuous on [0, 1) and avoids the breakpoint discontinuities of
    the Fisher (1993) piecewise estimator. Caps kappa at
    :data:`KAPPA_MAX` for numerical stability when R approaches 1.

    Parameters
    ----------
    angles : numpy.ndarray
        Angles in radians.

    Returns
    -------
    float
        Estimated kappa, with kappa=0 for uniform data; NaN for empty
        input. Capped at :data:`KAPPA_MAX`.

    References
    ----------
    Banerjee, A., Dhillon, I. S., Ghosh, J., Sra, S. (2005).
    Clustering on the unit hypersphere using von Mises-Fisher
    distributions. JMLR 6, 1345-1382.
    """
    if len(angles) == 0:
        return float("nan")

    R = calculate_mean_vector_length(angles)

    if np.isnan(R):
        return float("nan")
    if R < 1e-10:
        return 0.0

    R = float(min(R, 1.0 - 1e-12))
    kappa = R * (2.0 - R**2) / (1.0 - R**2)
    kappa = max(0.0, float(kappa))

    if kappa > KAPPA_MAX:
        warnings.warn(
            f"Concentration parameter kappa={kappa:.2e} exceeds maximum. "
            f"Capping at {KAPPA_MAX:.0e}. Data is nearly perfectly concentrated.",
            UserWarning,
            stacklevel=2,
        )
        return KAPPA_MAX

    return kappa


def _circular_percentile(angles: np.ndarray, q: float, reference: float) -> float:
    """Circular percentile relative to a reference angle.

    Avoids distortion across the -pi/pi discontinuity by computing the
    percentile in a reference-centred coordinate system and mapping back.
    """
    centered = to_minuspi_pi(angles - reference)
    percentile_centered = float(np.percentile(centered, q))
    return float(to_minuspi_pi(np.array([percentile_centered + reference]))[0])


def bootstrap_circular_mean_ci(
    angles: np.ndarray,
    confidence: float = 0.95,
    n_bootstrap: int = 1000,
    seed: int | None = None,
) -> tuple[float, float, float]:
    """Percentile-bootstrap confidence interval for the circular mean.

    Parameters
    ----------
    angles : numpy.ndarray
        Angles in radians.
    confidence : float, optional
        Confidence level in (0, 1) (default 0.95).
    n_bootstrap : int, optional
        Number of bootstrap resamples (default 1000).
    seed : int, optional
        Seed for :func:`numpy.random.default_rng`.

    Returns
    -------
    tuple of float
        ``(circular_mean, ci_lower, ci_upper)`` in radians. NaN tuples
        for empty input. Emits a UserWarning when n is below
        :data:`BOOTSTRAP_MIN_RECOMMENDED`.

    Notes
    -----
    Percentiles are computed in a coordinate system centred on the
    point estimate to keep CIs from straddling the -pi/pi discontinuity.
    """
    n = len(angles)
    if n == 0:
        return float("nan"), float("nan"), float("nan")

    if n == 1:
        mean = calculate_circular_mean(angles)
        warnings.warn(
            "Bootstrap CI with n=1 is degenerate (no variability).",
            UserWarning,
            stacklevel=2,
        )
        return mean, mean, mean

    if n < BOOTSTRAP_MIN_RECOMMENDED:
        warnings.warn(
            f"Sample size n={n} is below recommended minimum ({BOOTSTRAP_MIN_RECOMMENDED}) "
            "for bootstrap confidence intervals. Results may be unreliable.",
            UserWarning,
            stacklevel=2,
        )

    rng = np.random.default_rng(seed)
    ang = to_minuspi_pi(angles)

    bootstrap_means = np.array(
        [float(pg.circ_mean(rng.choice(ang, size=n, replace=True))) for _ in range(n_bootstrap)]
    )

    mean = calculate_circular_mean(angles)
    alpha = 1.0 - confidence
    ci_lower = _circular_percentile(bootstrap_means, 100.0 * alpha / 2.0, mean)
    ci_upper = _circular_percentile(bootstrap_means, 100.0 * (1.0 - alpha / 2.0), mean)
    return float(mean), ci_lower, ci_upper


def bootstrap_mvl_ci(
    angles: np.ndarray,
    confidence: float = 0.95,
    n_bootstrap: int = 1000,
    seed: int | None = None,
) -> tuple[float, float, float]:
    """Percentile-bootstrap confidence interval for the mean vector length R.

    Parameters
    ----------
    angles : numpy.ndarray
        Angles in radians.
    confidence : float, optional
        Confidence level in (0, 1) (default 0.95).
    n_bootstrap : int, optional
        Number of bootstrap resamples (default 1000).
    seed : int, optional
        Seed for :func:`numpy.random.default_rng`.

    Returns
    -------
    tuple of float
        ``(mvl, ci_lower, ci_upper)``. NaN tuples for empty input.
        Emits a UserWarning when n is below
        :data:`BOOTSTRAP_MIN_RECOMMENDED`.
    """
    n = len(angles)
    if n == 0:
        return float("nan"), float("nan"), float("nan")

    if n == 1:
        mvl = calculate_mean_vector_length(angles)
        warnings.warn(
            "Bootstrap CI with n=1 is degenerate (no variability).",
            UserWarning,
            stacklevel=2,
        )
        return mvl, mvl, mvl

    if n < BOOTSTRAP_MIN_RECOMMENDED:
        warnings.warn(
            f"Sample size n={n} is below recommended minimum ({BOOTSTRAP_MIN_RECOMMENDED}) "
            "for bootstrap confidence intervals. Results may be unreliable.",
            UserWarning,
            stacklevel=2,
        )

    rng = np.random.default_rng(seed)
    ang = to_minuspi_pi(angles)
    bootstrap_mvls = np.array(
        [float(pg.circ_r(rng.choice(ang, size=n, replace=True))) for _ in range(n_bootstrap)]
    )

    alpha = 1.0 - confidence
    ci_lower = float(np.percentile(bootstrap_mvls, 100.0 * alpha / 2.0))
    ci_upper = float(np.percentile(bootstrap_mvls, 100.0 * (1.0 - alpha / 2.0)))
    mvl = calculate_mean_vector_length(angles)
    return float(mvl), ci_lower, ci_upper


@dataclass
class CircularStatisticsReport:
    """Publication-ready circular-statistics summary container.

    Attributes
    ----------
    n_observations : int
        Number of angle measurements.
    circular_mean : float
        Mean direction (radians).
    circular_mean_deg : float
        Mean direction (degrees).
    circular_mean_ci : tuple of float
        Lower and upper bounds of the 95 percent CI for the circular
        mean (radians).
    mean_vector_length : float
        Mean resultant length R, used as an effect size.
    mvl_ci : tuple of float
        Lower and upper bounds of the 95 percent CI for R.
    circular_std : float
        Circular standard deviation (radians).
    circular_std_deg : float
        Circular standard deviation (degrees).
    circular_variance : float
        Circular variance V = 1 - R.
    concentration_kappa : float
        Estimated von Mises concentration parameter.
    rayleigh_z : float
        Rayleigh Z statistic.
    rayleigh_p : float
        Rayleigh p-value.
    rayleigh_interpretation : dict
        Bundle returned by
        :func:`sarcostat.core._interpretation.interpret_rayleigh_test`.
    v_statistic : float
        V-test statistic.
    v_p_value : float
        V-test p-value.
    v_interpretation : dict
        Bundle returned by
        :func:`sarcostat.core._interpretation.interpret_v_test`.
    expected_direction : float, optional
        Hypothesised mean direction used for the V-test (default 0.0).
    """

    n_observations: int
    circular_mean: float
    circular_mean_deg: float
    circular_mean_ci: tuple[float, float]
    mean_vector_length: float
    mvl_ci: tuple[float, float]
    circular_std: float
    circular_std_deg: float
    circular_variance: float
    concentration_kappa: float
    rayleigh_z: float
    rayleigh_p: float
    rayleigh_interpretation: dict[str, Any]
    v_statistic: float
    v_p_value: float
    v_interpretation: dict[str, Any]
    expected_direction: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dictionary view of the report."""
        return asdict(self)

    def summary_string(self) -> str:
        """Return a human-readable plain-text summary."""
        ray = self.rayleigh_interpretation
        v_label = "Significant" if self.v_interpretation["significant"] else "Not significant"
        ci_low_deg = float(np.degrees(self.circular_mean_ci[0]))
        ci_high_deg = float(np.degrees(self.circular_mean_ci[1]))
        return (
            f"Circular statistics report\n"
            f"Sample size: n = {self.n_observations}\n"
            f"Circular mean: {self.circular_mean_deg:.2f} deg "
            f"(95% CI: [{ci_low_deg:.2f}, {ci_high_deg:.2f}])\n"
            f"Mean vector length R: {self.mean_vector_length:.4f} "
            f"(95% CI: [{self.mvl_ci[0]:.4f}, {self.mvl_ci[1]:.4f}])\n"
            f"Circular std: {self.circular_std_deg:.2f} deg\n"
            f"Circular variance: {self.circular_variance:.4f}\n"
            f"Concentration kappa: {self.concentration_kappa:.2f}\n"
            f"Rayleigh test: Z = {self.rayleigh_z:.3f}, p = {self.rayleigh_p:.4f} "
            f"-> {ray['recommendation']}\n"
            f"  Effect size: R = {ray['effect_size']:.3f} ({ray['effect_description']})\n"
            f"V-test (vs {np.degrees(self.expected_direction):.1f} deg): "
            f"V = {self.v_statistic:.3f}, p = {self.v_p_value:.4f} -> {v_label}"
        )


def compute_circular_statistics_report(
    angles: np.ndarray,
    expected_direction: float = 0.0,
    confidence: float = 0.95,
    n_bootstrap: int = 1000,
    seed: int | None = None,
) -> CircularStatisticsReport:
    """Compute a complete circular-statistics report.

    Parameters
    ----------
    angles : numpy.ndarray
        Angles in radians.
    expected_direction : float, optional
        Mean direction tested by the V-test (default 0.0).
    confidence : float, optional
        Confidence level in (0, 1) (default 0.95).
    n_bootstrap : int, optional
        Bootstrap resample count (default 1000).
    seed : int, optional
        Random seed for reproducibility.

    Returns
    -------
    CircularStatisticsReport
        Populated report. For empty input, all numeric fields are NaN
        and the test outputs default to (0, 1).
    """
    n = len(angles)

    if n == 0:
        nan = float("nan")
        return CircularStatisticsReport(
            n_observations=0,
            circular_mean=nan,
            circular_mean_deg=nan,
            circular_mean_ci=(nan, nan),
            mean_vector_length=nan,
            mvl_ci=(nan, nan),
            circular_std=nan,
            circular_std_deg=nan,
            circular_variance=nan,
            concentration_kappa=nan,
            rayleigh_z=0.0,
            rayleigh_p=1.0,
            rayleigh_interpretation=interpret_rayleigh_test(0.0, 1.0, 0),
            v_statistic=0.0,
            v_p_value=1.0,
            v_interpretation=interpret_v_test(0.0, 1.0, expected_direction),
            expected_direction=expected_direction,
        )

    circ_mean = calculate_circular_mean(angles)
    mvl = calculate_mean_vector_length(angles)
    circ_std = calculate_circular_std(angles)
    circ_var = calculate_circular_variance(angles)
    kappa = calculate_concentration_parameter(angles)

    _, mean_ci_low, mean_ci_high = bootstrap_circular_mean_ci(angles, confidence, n_bootstrap, seed)
    mvl_seed = None if seed is None else seed + 1
    _, mvl_ci_low, mvl_ci_high = bootstrap_mvl_ci(angles, confidence, n_bootstrap, mvl_seed)

    rayleigh_z, rayleigh_p = perform_rayleigh_test(angles)
    v_stat, v_p = perform_v_test(angles, expected_direction)

    return CircularStatisticsReport(
        n_observations=n,
        circular_mean=circ_mean,
        circular_mean_deg=float(np.degrees(circ_mean)),
        circular_mean_ci=(mean_ci_low, mean_ci_high),
        mean_vector_length=mvl,
        mvl_ci=(mvl_ci_low, mvl_ci_high),
        circular_std=circ_std,
        circular_std_deg=float(np.degrees(circ_std)),
        circular_variance=circ_var,
        concentration_kappa=kappa,
        rayleigh_z=rayleigh_z,
        rayleigh_p=rayleigh_p,
        rayleigh_interpretation=interpret_rayleigh_test(rayleigh_z, rayleigh_p, n),
        v_statistic=v_stat,
        v_p_value=v_p,
        v_interpretation=interpret_v_test(v_stat, v_p, expected_direction),
        expected_direction=expected_direction,
    )


@dataclass
class SegmentData:
    """Geometric and angular data for one segment of a sequence.

    Attributes
    ----------
    segment_id : int
        Zero-based segment index.
    start_point : tuple of float
        Start coordinate ``(x, y)``.
    end_point : tuple of float
        End coordinate ``(x, y)``.
    length : float
        Euclidean length in physical units.
    angle_absolute : float
        Heading of the segment in the global frame (radians).
    angle_relative : float
        Turning angle relative to the previous segment (radians); 0
        for the first segment.
    cumulative_angle : float
        Cumulative turning from the first segment (radians).
    """

    segment_id: int
    start_point: tuple[float, float]
    end_point: tuple[float, float]
    length: float
    angle_absolute: float
    angle_relative: float
    cumulative_angle: float

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dictionary view of the segment."""
        return asdict(self)


@dataclass
class SequenceMetrics:
    """Aggregate metrics for a sarcomeric sequence.

    Attributes
    ----------
    n_segments : int
        Number of segments (one less than the number of points).
    segments : list of SegmentData
        Per-segment data.
    total_length : float
        Sum of segment lengths in physical units.
    head_to_tail_distance : float
        Euclidean distance between the first and last point.
    linearity_ratio : float
        head_to_tail_distance / total_length, in [0, 1].
    mean_vector_length : float
        Mean resultant length R of the relative-angle distribution.
    circular_mean : float
        Circular mean of relative angles (radians).
    circular_std : float
        Circular standard deviation of relative angles (radians).
    rayleigh_z, rayleigh_p : float
        Rayleigh test outputs on relative angles.
    v_statistic, v_p_value : float
        V-test outputs on relative angles.
    timestamp : str
        ISO-8601 timestamp at which the analysis ran.
    metadata : dict, optional
        Free-form metadata; populated with the unit and conversion
        factor by :func:`analyze_segment_sequence`.
    """

    n_segments: int
    segments: list
    total_length: float
    head_to_tail_distance: float
    linearity_ratio: float
    mean_vector_length: float
    circular_mean: float
    circular_std: float
    rayleigh_z: float
    rayleigh_p: float
    v_statistic: float
    v_p_value: float
    timestamp: str
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dictionary view, expanding nested segments."""
        data = asdict(self)
        if isinstance(data["segments"], list) and len(data["segments"]) > 0:
            if hasattr(data["segments"][0], "to_dict"):
                data["segments"] = [seg.to_dict() for seg in self.segments]
        return data

    def summary_string(self) -> str:
        """Return a human-readable plain-text summary."""
        return (
            f"Sequence metrics summary\n"
            f"Timestamp: {self.timestamp}\n"
            f"Number of segments: {self.n_segments}\n"
            f"Total length: {self.total_length:.2f}\n"
            f"Head-to-tail distance: {self.head_to_tail_distance:.2f}\n"
            f"Linearity ratio: {self.linearity_ratio:.4f}\n"
            f"Mean vector length R: {self.mean_vector_length:.4f}\n"
            f"Circular mean: {np.degrees(self.circular_mean):.2f} deg\n"
            f"Circular std: {np.degrees(self.circular_std):.2f} deg"
        )


def _kahan_cumsum(values: list[float]) -> list[float]:
    """Compensated cumulative sum (Kahan algorithm).

    Parameters
    ----------
    values : list of float
        Input series.

    Returns
    -------
    list of float
        Running totals with reduced floating-point error. Used by
        :func:`analyze_segment_sequence` for sequences longer than
        :data:`_KAHAN_THRESHOLD` to stop drift in the cumulative angle.
    """
    result: list[float] = []
    total = 0.0
    compensation = 0.0
    for val in values:
        y = val - compensation
        t = total + y
        compensation = (t - total) - y
        total = t
        result.append(total)
    return result


def _per_segment_geometry(
    points: np.ndarray, pixel_to_unit: float
) -> tuple[list[float], list[float], list[float]]:
    """Compute per-segment lengths and angles from a polyline.

    Parameters
    ----------
    points : numpy.ndarray
        Array of shape ``(n_points, 2)`` of (x, y) coordinates.
    pixel_to_unit : float
        Conversion factor applied to lengths.

    Returns
    -------
    tuple of list of float
        ``(lengths, angles_absolute, angles_relative)``. Each list has
        length ``n_points - 1``. ``angles_relative[0]`` is 0.
    """
    n_segments = len(points) - 1
    lengths: list[float] = []
    angles_absolute: list[float] = []
    angles_relative: list[float] = []

    for i in range(n_segments):
        dx = points[i + 1][0] - points[i][0]
        dy = points[i + 1][1] - points[i][1]
        lengths.append(float(np.hypot(dx, dy) * pixel_to_unit))
        angle_abs = float(np.arctan2(dy, dx))
        angles_absolute.append(angle_abs)
        if i == 0:
            angles_relative.append(0.0)
        else:
            wrapped = float(to_minuspi_pi(np.array([angle_abs - angles_absolute[i - 1]]))[0])
            angles_relative.append(wrapped)

    return lengths, angles_absolute, angles_relative


def _cumulative_angles(angles_relative: list[float]) -> list[float]:
    """Cumulative-sum the relative-angle series with FP-safe summation.

    For sequences with more than :data:`_KAHAN_THRESHOLD` segments the
    Kahan compensated sum is used; shorter sequences use a plain sum.
    The first cumulative angle is always 0 (the first segment has no
    reference).
    """
    if len(angles_relative) <= 1:
        return [0.0] if angles_relative else []

    if len(angles_relative) - 1 > _KAHAN_THRESHOLD:
        return [0.0] + _kahan_cumsum(angles_relative[1:])

    cumulative = [0.0]
    running = 0.0
    for angle in angles_relative[1:]:
        running += angle
        cumulative.append(running)
    return cumulative


def _build_segment_records(
    points: np.ndarray,
    lengths: list[float],
    angles_absolute: list[float],
    angles_relative: list[float],
    cumulative_angles: list[float],
) -> list[SegmentData]:
    """Assemble per-segment :class:`SegmentData` records."""
    segments: list[SegmentData] = []
    for i in range(len(lengths)):
        segments.append(
            SegmentData(
                segment_id=i,
                start_point=(float(points[i][0]), float(points[i][1])),
                end_point=(float(points[i + 1][0]), float(points[i + 1][1])),
                length=float(lengths[i]),
                angle_absolute=float(angles_absolute[i]),
                angle_relative=float(angles_relative[i]),
                cumulative_angle=float(cumulative_angles[i]),
            )
        )
    return segments


def _aggregate_relative_stats(angles_relative: list[float]) -> dict[str, float]:
    """Summarise the relative-angle series with circular statistics.

    The first segment has no reference and is excluded. For sequences
    of length 0 or 1 the summary is the trivial "perfectly linear" case.

    Returns
    -------
    dict of float
        Keys: ``mean_vector_length``, ``circular_mean``, ``circular_std``,
        ``rayleigh_z``, ``rayleigh_p``, ``v_statistic``, ``v_p_value``.
    """
    if len(angles_relative) <= 1:
        return {
            "mean_vector_length": 1.0,
            "circular_mean": 0.0,
            "circular_std": 0.0,
            "rayleigh_z": 0.0,
            "rayleigh_p": 1.0,
            "v_statistic": 0.0,
            "v_p_value": 1.0,
        }

    arr = np.array(angles_relative[1:])
    rayleigh_z, rayleigh_p = perform_rayleigh_test(arr)
    v_stat, v_p = perform_v_test(arr)
    return {
        "mean_vector_length": float(calculate_mean_vector_length(arr)),
        "circular_mean": float(calculate_circular_mean(arr)),
        "circular_std": float(calculate_circular_std(arr)),
        "rayleigh_z": float(rayleigh_z),
        "rayleigh_p": float(rayleigh_p),
        "v_statistic": float(v_stat),
        "v_p_value": float(v_p),
    }


def analyze_segment_sequence(
    points: np.ndarray, pixel_to_unit: float = 1.0, unit_name: str = "pixels"
) -> SequenceMetrics:
    """Analyse a polyline as a sequence of sarcomere segments.

    Parameters
    ----------
    points : numpy.ndarray
        Array of shape ``(n_points, 2)`` of (x, y) coordinates. At
        least two points are required.
    pixel_to_unit : float, optional
        Conversion factor from pixels to physical units (default 1.0).
    unit_name : str, optional
        Unit label stored on the result (default ``"pixels"``).

    Returns
    -------
    SequenceMetrics
        Aggregate metrics including per-segment data, linearity ratio,
        and circular statistics on the relative-angle distribution.

    Raises
    ------
    ValueError
        If fewer than two points are supplied.

    Notes
    -----
    For sequences with more than :data:`_KAHAN_THRESHOLD` segments the
    cumulative angle is computed with the Kahan compensated sum to
    bound the floating-point drift.
    """
    if len(points) < 2:
        raise ValueError("At least 2 points are required for analysis")

    lengths, angles_absolute, angles_relative = _per_segment_geometry(points, pixel_to_unit)
    cumulative_angles = _cumulative_angles(angles_relative)
    segments = _build_segment_records(
        points, lengths, angles_absolute, angles_relative, cumulative_angles
    )

    total_length = float(sum(lengths))
    head_to_tail = float(
        np.hypot(points[-1][0] - points[0][0], points[-1][1] - points[0][1]) * pixel_to_unit
    )
    linearity_ratio = calculate_linearity_ratio(head_to_tail, total_length)

    stats = _aggregate_relative_stats(angles_relative)

    return SequenceMetrics(
        n_segments=len(segments),
        segments=segments,
        total_length=total_length,
        head_to_tail_distance=head_to_tail,
        linearity_ratio=float(linearity_ratio),
        mean_vector_length=stats["mean_vector_length"],
        circular_mean=stats["circular_mean"],
        circular_std=stats["circular_std"],
        rayleigh_z=stats["rayleigh_z"],
        rayleigh_p=stats["rayleigh_p"],
        v_statistic=stats["v_statistic"],
        v_p_value=stats["v_p_value"],
        timestamp=datetime.now().isoformat(),
        metadata={"unit": unit_name, "conversion_factor": pixel_to_unit},
    )


__all__ = [
    "TWO_PI",
    "SIGNIFICANCE_LEVEL",
    "BOOTSTRAP_MIN_RECOMMENDED",
    "UNIFORM_R_THRESHOLD",
    "KAPPA_MAX",
    "wrap_strict",
    "to_minuspi_pi",
    "calculate_linearity_ratio",
    "calculate_mean_vector_length",
    "perform_rayleigh_test",
    "perform_v_test",
    "calculate_circular_mean",
    "calculate_circular_std",
    "calculate_circular_variance",
    "calculate_concentration_parameter",
    "bootstrap_circular_mean_ci",
    "bootstrap_mvl_ci",
    "CircularStatisticsReport",
    "compute_circular_statistics_report",
    "SegmentData",
    "SequenceMetrics",
    "analyze_segment_sequence",
]
