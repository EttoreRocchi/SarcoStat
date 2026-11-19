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
from typing import Any

import numpy as np
import pingouin as pg

from . import chain_statistics as _chain
from ._interpretation import interpret_rayleigh_test, interpret_v_test

TWO_PI = 2.0 * np.pi
SIGNIFICANCE_LEVEL = 0.05
BOOTSTRAP_MIN_RECOMMENDED = 10
UNIFORM_R_THRESHOLD = 0.01
KAPPA_MAX = 1.0e6
_KAHAN_THRESHOLD = 100
_LP_RESOLVED_MAX_TRACES = 10.0


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


def _finite_angles(angles: np.ndarray) -> np.ndarray:
    """Return ``angles`` as a 1-D float array, rejecting NaN and inf.

    pingouin silently drops NaN in some circular routines but not in
    others, so a single non-finite angle would otherwise give mutually
    inconsistent statistics (e.g. a Rayleigh Z computed with the wrong n).

    Raises
    ------
    ValueError
        If any angle is NaN or infinite.
    """
    arr = np.asarray(angles, dtype=float).ravel()
    if not np.all(np.isfinite(arr)):
        raise ValueError("Angles must be finite; got NaN or inf")
    return arr


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

    Raises
    ------
    ValueError
        If either input is NaN or infinite.
    """
    if not (np.isfinite(head_to_tail) and np.isfinite(total_length)):
        raise ValueError("head_to_tail and total_length must be finite")
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
    angles = _finite_angles(angles)
    if angles.size == 0:
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
    angles = _finite_angles(angles)
    if angles.size == 0:
        return 0.0, 1.0
    z_stat, p_val = pg.circ_rayleigh(to_minuspi_pi(angles))
    return float(z_stat), float(p_val)


def perform_v_test(angles: np.ndarray, expected_dir: float = 0.0) -> tuple[float, float]:
    """Run the V-test for concentration about an expected mean direction.

    The V-test is one-sided: the alternative hypothesis is that the
    angles are concentrated *around* ``expected_dir``. A small p-value
    is evidence for that concentration; a large p-value means
    uniformity could not be rejected in favour of it.

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
    angles = _finite_angles(angles)
    if angles.size == 0:
        return 0.0, 1.0
    if not np.isfinite(expected_dir):
        raise ValueError("expected_dir must be finite")
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
    angles = _finite_angles(angles)
    if angles.size == 0:
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
    angles = _finite_angles(angles)
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
    return float(np.sqrt(max(0.0, -2.0 * np.log(R))))


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
    angles = _finite_angles(angles)
    if angles.size == 0:
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
    angles = _finite_angles(angles)
    if angles.size == 0:
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


def _validate_bootstrap_args(confidence: float, n_bootstrap: int) -> None:
    """Reject confidence levels outside (0, 1) and non-positive resample counts."""
    if not (0.0 < confidence < 1.0):
        raise ValueError(f"confidence must be in (0, 1), got {confidence}")
    if int(n_bootstrap) != n_bootstrap or n_bootstrap < 1:
        raise ValueError(f"n_bootstrap must be a positive integer, got {n_bootstrap}")


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

    Raises
    ------
    ValueError
        If ``confidence`` is outside (0, 1), ``n_bootstrap`` is not a
        positive integer, or any angle is non-finite.

    Notes
    -----
    Percentiles are computed in a coordinate system centred on the
    point estimate to keep CIs from straddling the -pi/pi discontinuity.
    """
    _validate_bootstrap_args(confidence, n_bootstrap)
    angles = _finite_angles(angles)
    n = angles.size
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

    Raises
    ------
    ValueError
        If ``confidence`` is outside (0, 1), ``n_bootstrap`` is not a
        positive integer, or any angle is non-finite.
    """
    _validate_bootstrap_args(confidence, n_bootstrap)
    angles = _finite_angles(angles)
    n = angles.size
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
        Lower and upper bounds of the bootstrap CI for the circular
        mean (radians), at level ``confidence``.
    mean_vector_length : float
        Mean resultant length R, used as an effect size.
    mvl_ci : tuple of float
        Lower and upper bounds of the percentile-bootstrap CI for R, at
        level ``confidence``. It covers the population resultant far less
        often than nominal for small samples; prefer ``mvl_ci_vonmises``.
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
    confidence : float, optional
        Confidence level of both bootstrap CIs (default 0.95).
    n_bootstrap : int, optional
        Bootstrap resample count used for both CIs (default 1000).
    seed : int, optional
        Seed of the circular-mean bootstrap; the MVL bootstrap uses
        ``seed + 1``. ``None`` when the run was not seeded.
    mvl_ci_vonmises : tuple of float or None, optional
        Confidence interval for the population resultant under von Mises
        headings, by test inversion
        (:func:`sarcostat.core.chain_statistics.mvl_confidence_interval`);
        nominal coverage for any sample size under the model.
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
    confidence: float = 0.95
    n_bootstrap: int = 1000
    seed: int | None = None
    mvl_ci_vonmises: tuple[float, float] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dictionary view of the report."""
        return asdict(self)

    def summary_string(self) -> str:
        """Return a human-readable plain-text summary."""
        ray = self.rayleigh_interpretation
        v_label = "Significant" if self.v_interpretation["significant"] else "Not significant"
        ci_low_deg = float(np.degrees(self.circular_mean_ci[0]))
        ci_high_deg = float(np.degrees(self.circular_mean_ci[1]))
        level = f"{100.0 * self.confidence:g}% CI"
        return (
            f"Circular statistics report\n"
            f"Sample size: n = {self.n_observations}\n"
            f"Circular mean: {self.circular_mean_deg:.2f} deg "
            f"({level}: [{ci_low_deg:.2f}, {ci_high_deg:.2f}])\n"
            f"Mean vector length R: {self.mean_vector_length:.4f} "
            f"({level} bootstrap: [{self.mvl_ci[0]:.4f}, {self.mvl_ci[1]:.4f}]"
            + (
                ""
                if self.mvl_ci_vonmises is None
                else f"; von Mises: [{self.mvl_ci_vonmises[0]:.4f}, {self.mvl_ci_vonmises[1]:.4f}]"
            )
            + ")\n"
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

    Raises
    ------
    ValueError
        On non-finite angles or invalid bootstrap arguments.
    """
    _validate_bootstrap_args(confidence, n_bootstrap)
    angles = _finite_angles(angles)
    n = angles.size

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
            confidence=confidence,
            n_bootstrap=n_bootstrap,
            seed=seed,
        )

    circ_mean = calculate_circular_mean(angles)
    mvl = calculate_mean_vector_length(angles)
    circ_std = calculate_circular_std(angles)
    circ_var = calculate_circular_variance(angles)
    kappa = calculate_concentration_parameter(angles)

    _, mean_ci_low, mean_ci_high = bootstrap_circular_mean_ci(angles, confidence, n_bootstrap, seed)
    mvl_seed = None if seed is None else seed + 1
    _, mvl_ci_low, mvl_ci_high = bootstrap_mvl_ci(angles, confidence, n_bootstrap, mvl_seed)
    mvl_ci_vm = _chain.mvl_confidence_interval(mvl, n, confidence)

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
        confidence=confidence,
        n_bootstrap=n_bootstrap,
        seed=seed,
        mvl_ci_vonmises=mvl_ci_vm,
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
        Mean resultant length R of the segment headings (MVL). For equal
        segment lengths it equals the linearity ratio.
    circular_mean : float
        Circular mean of the segment headings (radians, image frame).
    circular_std : float
        Circular standard deviation of the headings (radians).
    rayleigh_z, rayleigh_p : float
        Rayleigh test outputs on the headings.
    v_statistic, v_p_value : float or None
        V-test outputs on the headings against ``expected_direction``;
        ``None`` when no expected direction was supplied.
    timestamp : str or None
        Time of the analysis as set by the caller (e.g. ISO 8601), or
        ``None``, the default, so that a headless analysis is reproducible.
    expected_direction : float or None, optional
        Hypothesised heading (radians, image frame) used by the V-test.
    metadata : dict, optional
        Free-form metadata; populated with the unit, the conversion
        factor and the angle basis by :func:`analyze_segment_sequence`.
    n_eff : float or None, optional
        Effective number of segments of the length-weighted resultant
        (:func:`sarcostat.core.chain_statistics.effective_sample_size`).
    rho_squared_corrected : float or None, optional
        Unbiased estimate of the squared population resultant from the
        linearity ratio; can be negative for weakly aligned chains.
    lr_noise_floor : float or None, optional
        Linearity ratio exceeded with probability 0.05 by chains of the
        same lengths with uniformly distributed headings.
    lr_uniformity_p : float or None, optional
        Length-weighted Rayleigh p-value of the linearity ratio.
    turning_mvl : float or None, optional
        Mean resultant length of the turning angles (local regularity).
    turning_mean : float or None, optional
        Circular mean turning angle (radians): curvature per segment.
    turning_circular_std : float or None, optional
        Circular standard deviation of the turning angles (radians).
    curvature_per_unit : float or None, optional
        Mean turning angle per unit length (radians per unit).
    serial_dependence_statistic, serial_dependence_p : float or None, optional
        ``D = R_turn - R_head^2`` and its permutation p-value (three or
        more segments).
    tangent_correlation : list of float or None, optional
        ``C(k)`` for lags 1, 2, ...
    persistence_length : float or None, optional
        Persistence length from the fit of ``C(k)``, in length units;
        ``inf`` when ``C(k)`` does not decay (written as ``null`` in
        JSON, together with a finite ``persistence_amplitude``).
    persistence_amplitude : float or None, optional
        Amplitude ``A`` of that fit (local order).
    mvl_ci : tuple of float or None, optional
        95% confidence interval for the population resultant of the
        headings under a von Mises model (test inversion; nominal
        coverage for any number of segments).
    """

    n_segments: int
    segments: list[SegmentData]
    total_length: float
    head_to_tail_distance: float
    linearity_ratio: float
    mean_vector_length: float
    circular_mean: float
    circular_std: float
    rayleigh_z: float
    rayleigh_p: float
    v_statistic: float | None
    v_p_value: float | None
    timestamp: str | None
    expected_direction: float | None = None
    metadata: dict[str, Any] | None = None
    n_eff: float | None = None
    rho_squared_corrected: float | None = None
    lr_noise_floor: float | None = None
    lr_uniformity_p: float | None = None
    turning_mvl: float | None = None
    turning_mean: float | None = None
    turning_circular_std: float | None = None
    curvature_per_unit: float | None = None
    serial_dependence_statistic: float | None = None
    serial_dependence_p: float | None = None
    tangent_correlation: list[float] | None = None
    persistence_length: float | None = None
    persistence_amplitude: float | None = None
    mvl_ci: tuple[float, float] | None = None

    @property
    def unit(self) -> str:
        """Unit label the lengths are expressed in (``"pixels"`` if unset)."""
        return str((self.metadata or {}).get("unit", "pixels"))

    def chain_statistics_string(self) -> str:
        """Plain-text block with the order- and length-aware statistics."""

        def _fmt(value: float | None, spec: str = ".4f", scale: float = 1.0) -> str:
            if value is None or not np.isfinite(value):
                return "n/a"
            return format(value * scale, spec)

        unit = self.unit
        if self.persistence_length is None:
            lp = "n/a"
        elif np.isinf(self.persistence_length):
            lp = "no decay (independent headings)"
        elif self.persistence_length > _LP_RESOLVED_MAX_TRACES * self.total_length:
            lp = f"not resolved, above {_LP_RESOLVED_MAX_TRACES:g} times the traced length"
        else:
            lp = f"{self.persistence_length:.1f} {unit}"
        if self.serial_dependence_p is None:
            drift = "n/a (fewer than 3 segments)"
        else:
            drift = (
                f"D = {_fmt(self.serial_dependence_statistic, '.4f')}, "
                f"p = {_fmt(self.serial_dependence_p, '.4f')} (permutation)"
            )
        return (
            f"Effective segments N_eff: {_fmt(self.n_eff, '.2f')}\n"
            f"rho^2 (bias-corrected): {_fmt(self.rho_squared_corrected)}\n"
            f"LR expected without alignment (95%): {_fmt(self.lr_noise_floor)}"
            f" (p = {_fmt(self.lr_uniformity_p)})\n"
            f"Turning angles: MVL {_fmt(self.turning_mvl)}, "
            f"circular std {_fmt(self.turning_circular_std, '.2f', 180 / np.pi)} deg, "
            f"mean {_fmt(self.turning_mean, '.2f', 180 / np.pi)} deg per segment\n"
            f"Serial dependence of headings: {drift}\n"
            f"Persistence length: {lp} (amplitude {_fmt(self.persistence_amplitude)})"
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dictionary view (segments included)."""
        return asdict(self)

    def summary_string(self) -> str:
        """Return a human-readable plain-text summary."""
        unit = self.unit
        if self.expected_direction is None or self.v_p_value is None:
            v_line = "V-test: not run (no expected direction given)"
        else:
            v_line = (
                f"V-test (vs {np.degrees(self.expected_direction):.1f} deg): "
                f"V = {self.v_statistic:.3f}, p = {self.v_p_value:.4f}"
            )
        return (
            "Sequence metrics summary\n"
            + ("" if self.timestamp is None else f"Timestamp: {self.timestamp}\n")
            + f"Number of segments: {self.n_segments}\n"
            f"Total length: {self.total_length:.2f} {unit}\n"
            f"Head-to-tail distance: {self.head_to_tail_distance:.2f} {unit}\n"
            f"Linearity ratio: {self.linearity_ratio:.4f}\n"
            f"Mean vector length R (headings): {self.mean_vector_length:.4f}"
            + (
                ""
                if self.mvl_ci is None
                else f" (95% CI, von Mises: [{self.mvl_ci[0]:.4f}, {self.mvl_ci[1]:.4f}])"
            )
            + "\n"
            f"Circular mean heading: {np.degrees(self.circular_mean):.2f} deg\n"
            f"Circular std: {np.degrees(self.circular_std):.2f} deg\n"
            f"Rayleigh test: Z = {self.rayleigh_z:.3f}, p = {self.rayleigh_p:.4f}\n"
            f"{v_line}\n"
            f"{self.chain_statistics_string()}"
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

    Raises
    ------
    ValueError
        If two consecutive points coincide. A zero-length segment has no
        heading (``arctan2(0, 0)`` would silently report 0 rad).
    """
    n_segments = len(points) - 1
    lengths: list[float] = []
    angles_absolute: list[float] = []
    angles_relative: list[float] = []

    for i in range(n_segments):
        dx = points[i + 1][0] - points[i][0]
        dy = points[i + 1][1] - points[i][1]
        length_px = float(np.hypot(dx, dy))
        if length_px < 1e-10:
            raise ValueError(
                f"Segment {i} has zero length (points {i} and {i + 1} coincide); "
                "remove the duplicate point"
            )
        lengths.append(length_px * pixel_to_unit)
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


def _aggregate_heading_stats(
    angles_absolute: list[float], expected_direction: float | None
) -> dict[str, Any]:
    """Summarise the segment headings with circular statistics.

    The headings theta_i are the angles of the sarcomere unit vectors,
    so that the MVL equals the linearity ratio for equal lengths; MVL,
    CSD and the Rayleigh test are rotation-invariant, while the V-test
    needs an explicit expected direction in the same (image) frame.

    Returns
    -------
    dict
        Keys: ``mean_vector_length``, ``circular_mean``, ``circular_std``,
        ``rayleigh_z``, ``rayleigh_p``, ``v_statistic``, ``v_p_value``.
        The two V-test entries are ``None`` when ``expected_direction``
        is ``None``.
    """
    arr = np.asarray(angles_absolute, dtype=float)
    rayleigh_z, rayleigh_p = perform_rayleigh_test(arr)
    v_stat: float | None = None
    v_p: float | None = None
    if expected_direction is not None:
        v_stat, v_p = perform_v_test(arr, expected_direction)
    return {
        "mean_vector_length": float(calculate_mean_vector_length(arr)),
        "circular_mean": float(calculate_circular_mean(arr)),
        "circular_std": float(calculate_circular_std(arr)),
        "rayleigh_z": float(rayleigh_z),
        "rayleigh_p": float(rayleigh_p),
        "v_statistic": v_stat,
        "v_p_value": v_p,
    }


def _finite_or_none(value: float) -> float | None:
    """Map NaN to ``None``; keep finite values and infinities."""
    return None if np.isnan(value) else float(value)


def _chain_level_stats(
    lengths: list[float],
    angles_absolute: list[float],
    linearity_ratio: float,
    n_permutations: int,
    seed: int | None,
    max_lag: int | None,
) -> dict[str, Any]:
    """Order- and length-aware statistics of the chain (see :mod:`.chain_statistics`)."""
    lens = np.asarray(lengths, dtype=float)
    theta = np.asarray(angles_absolute, dtype=float)
    out: dict[str, Any] = {
        "n_eff": _chain.effective_sample_size(lens),
        "rho_squared_corrected": _finite_or_none(
            _chain.corrected_rho_squared(linearity_ratio, lens)
        ),
        "lr_noise_floor": _chain.lr_noise_floor(lens),
        "lr_uniformity_p": _chain.lr_uniformity_p(linearity_ratio, lens),
    }
    turning = _chain.turning_angle_statistics(theta, lens)
    out["turning_mvl"] = _finite_or_none(turning.mean_vector_length)
    out["turning_mean"] = _finite_or_none(turning.mean_turning)
    out["turning_circular_std"] = _finite_or_none(turning.circular_std)
    out["curvature_per_unit"] = _finite_or_none(turning.curvature_per_unit)

    out["serial_dependence_statistic"] = None
    out["serial_dependence_p"] = None
    if theta.size >= 3:
        test = _chain.serial_dependence_test(theta, n_resamples=n_permutations, seed=seed)
        out["serial_dependence_statistic"] = test.statistic
        out["serial_dependence_p"] = test.p_value

    corr = _chain.tangent_correlation(theta, max_lag)
    out["tangent_correlation"] = corr.values or None
    fit = _chain.persistence_length(theta, lens, max_lag)
    out["persistence_length"] = _finite_or_none(fit.persistence_length)
    out["persistence_amplitude"] = _finite_or_none(fit.amplitude)
    out["mvl_ci"] = None
    if theta.size >= 2:
        r_head = float(np.abs(np.mean(np.exp(1j * theta))))
        out["mvl_ci"] = _chain.mvl_confidence_interval(r_head, int(theta.size))
    return out


def _validated_points(points: np.ndarray, pixel_to_unit: float) -> np.ndarray:
    """Return ``points`` as an ``(n, 2)`` float array after input checks."""
    arr = np.asarray(points, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != 2:
        raise ValueError(f"points must have shape (n_points, 2), got {arr.shape}")
    if len(arr) < 2:
        raise ValueError("At least 2 points are required for analysis")
    if not np.all(np.isfinite(arr)):
        raise ValueError("points must be finite; got NaN or inf")
    if not (np.isfinite(pixel_to_unit) and pixel_to_unit > 0):
        raise ValueError(f"pixel_to_unit must be a positive finite number, got {pixel_to_unit}")
    return arr


def analyze_segment_sequence(
    points: np.ndarray,
    pixel_to_unit: float = 1.0,
    unit_name: str = "pixels",
    expected_direction: float | None = None,
    n_permutations: int = _chain.DEFAULT_N_PERMUTATIONS,
    seed: int | None = 0,
    max_lag: int | None = None,
    timestamp: str | None = None,
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
    expected_direction : float, optional
        Hypothesised heading in radians, in the image frame of
        ``points`` (``arctan2(dy, dx)``). When given, the V-test is run
        on the headings against it; when ``None`` (default) the V-test
        is skipped, because a fixed reference is meaningless for an
        arbitrarily oriented micrograph.
    n_permutations : int, optional
        Permutations of the serial-dependence test (default 999).
    seed : int, optional
        Seed of that test (default 0, so repeated analyses of the same
        annotation give the same p-value).
    max_lag : int, optional
        Largest lag of the tangent correlation (default: all lags).
    timestamp : str, optional
        Time of the analysis to store on the result, set by the caller
        (the GUI passes the current time). Default ``None``: no wall-clock
        time, so the same input always gives the same result.

    Returns
    -------
    SequenceMetrics
        Aggregate metrics including per-segment data, linearity ratio,
        circular statistics on the segment headings, and the order- and
        length-aware statistics of :mod:`sarcostat.core.chain_statistics`
        (effective segments, bias-corrected rho^2, turning angles, serial
        dependence, tangent correlation and persistence length).

    Raises
    ------
    ValueError
        If fewer than two points are supplied, ``points`` is not an
        ``(n, 2)`` array of finite values, ``pixel_to_unit`` is not
        positive, or two consecutive points coincide.

    Notes
    -----
    Circular statistics are computed on the absolute headings theta_i,
    so that LR == MVL for equal segment lengths. Turning angles between consecutive segments are
    still reported per segment as ``angle_relative``. For sequences with
    more than :data:`_KAHAN_THRESHOLD` segments the cumulative angle is
    computed with the Kahan compensated sum to bound the floating-point
    drift.
    """
    points = _validated_points(points, pixel_to_unit)
    if expected_direction is not None and not np.isfinite(expected_direction):
        raise ValueError("expected_direction must be finite")

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

    stats = _aggregate_heading_stats(angles_absolute, expected_direction)
    chain = _chain_level_stats(
        lengths, angles_absolute, float(linearity_ratio), n_permutations, seed, max_lag
    )

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
        timestamp=timestamp,
        expected_direction=expected_direction,
        metadata={
            "unit": unit_name,
            "conversion_factor": pixel_to_unit,
            "angle_basis": "heading",
        },
        **chain,
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
