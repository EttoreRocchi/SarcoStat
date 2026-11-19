"""Order- and length-aware statistics for traced myofibrils.

A traced myofibril is a chain of segments with lengths ``l_i`` and
headings ``theta_i``. The summaries in :mod:`sarcostat.core.circular_stats`
treat the headings as an unordered sample; this module adds the
quantities that depend on the segment lengths and on their order:

- the effective number of segments of the length-weighted resultant
  behind the Linearity Ratio (:func:`effective_sample_size`), the
  unbiased estimator of the squared population resultant built on it
  (:func:`corrected_rho_squared`) and the Linearity Ratio expected from
  unaligned headings (:func:`lr_noise_floor`, :func:`lr_uniformity_p`);
- the turning angles between consecutive segments, which measure local
  regularity and curvature (:func:`turning_angle_statistics`);
- the tangent correlation function and the persistence length of one
  chain or of a sample of chains (:func:`tangent_correlation`,
  :func:`persistence_length`, :func:`pooled_persistence_length`);
- a test of serial dependence of the headings
  (:func:`serial_dependence_test`).

For segments of equal length the Linearity Ratio equals the mean
resultant length R of the headings, and with independent headings
``E[LR^2] = rho^2 + (1 - rho^2) / N_eff``. The module depends only on
numpy.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any

import numpy as np

DEFAULT_ALPHA = 0.05
DEFAULT_N_PERMUTATIONS = 999
_MIN_PAIRS_FOR_FIT = 3


def _wrap(angles: np.ndarray) -> np.ndarray:
    """Wrap angles to (-pi, pi]."""
    wrapped: np.ndarray = np.angle(np.exp(1j * np.asarray(angles, dtype=float)))
    return wrapped


def _resultant_length(angles: np.ndarray) -> float:
    """Mean resultant length of ``angles`` (NaN for empty input)."""
    angles = np.asarray(angles, dtype=float)
    if angles.size == 0:
        return float("nan")
    return float(np.abs(np.mean(np.exp(1j * angles))))


def _validated_lengths(lengths: np.ndarray) -> np.ndarray:
    arr = np.asarray(lengths, dtype=float).ravel()
    if arr.size == 0:
        raise ValueError("lengths must contain at least one segment")
    if not np.all(np.isfinite(arr)) or np.any(arr <= 0):
        raise ValueError("lengths must be positive and finite")
    return arr


def _validated_headings(headings: np.ndarray) -> np.ndarray:
    arr = np.asarray(headings, dtype=float).ravel()
    if not np.all(np.isfinite(arr)):
        raise ValueError("headings must be finite")
    return arr


def effective_sample_size(lengths: np.ndarray) -> float:
    """Kish effective number of segments of a length-weighted resultant.

    Parameters
    ----------
    lengths : numpy.ndarray
        Positive segment lengths.

    Returns
    -------
    float
        ``N_eff = (sum l)^2 / sum l^2 = 1 / sum w_i^2`` with
        ``w_i = l_i / sum l``. It equals the number of segments when the
        lengths are equal and is about ``N / (1 + CV^2)`` otherwise.
    """
    arr = _validated_lengths(lengths)
    return float(arr.sum() ** 2 / np.sum(arr**2))


def corrected_rho_squared(linearity_ratio: float, lengths: np.ndarray) -> float:
    """Unbiased estimate of the squared population resultant ``rho^2``.

    With headings drawn independently of the lengths from a distribution
    with mean resultant length ``rho``,
    ``E[LR^2 | lengths] = rho^2 + (1 - rho^2) S`` with ``S = 1 / N_eff``,
    so ``(LR^2 - S) / (1 - S)`` is unbiased for ``rho^2``. For equal
    lengths it reduces to ``(N R^2 - 1) / (N - 1)``.

    Parameters
    ----------
    linearity_ratio : float
        Observed Linearity Ratio (head-to-tail distance / contour length).
    lengths : numpy.ndarray
        Positive segment lengths of the same chain.

    Returns
    -------
    float
        The estimate. It can be negative for weakly aligned chains, as
        any unbiased estimator of a non-negative quantity; NaN for a
        single segment.
    """
    if not np.isfinite(linearity_ratio):
        raise ValueError("linearity_ratio must be finite")
    n_eff = effective_sample_size(lengths)
    if n_eff <= 1.0:
        return float("nan")
    s = 1.0 / n_eff
    return float((linearity_ratio**2 - s) / (1.0 - s))


def lr_noise_floor(lengths: np.ndarray, alpha: float = DEFAULT_ALPHA) -> float:
    """Linearity Ratio exceeded with probability ``alpha`` by unaligned chains.

    Under uniformly distributed headings ``2 LR^2 / S`` is approximately
    chi-squared with two degrees of freedom (``S = 1 / N_eff``), so
    ``P(LR > c) = exp(-c^2 / S)`` and ``c = sqrt(-S ln alpha)``. A
    Linearity Ratio below this value is compatible with no preferred
    direction at all; for five equal segments and ``alpha = 0.05`` the
    floor is about 0.77.

    Parameters
    ----------
    lengths : numpy.ndarray
        Positive segment lengths.
    alpha : float, optional
        Tail probability in (0, 1) (default 0.05).

    Returns
    -------
    float
        The threshold, capped at 1.
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    s = 1.0 / effective_sample_size(lengths)
    return float(min(1.0, np.sqrt(-s * np.log(alpha))))


def lr_uniformity_p(linearity_ratio: float, lengths: np.ndarray) -> float:
    """P-value of a length-weighted Rayleigh test of uniform headings.

    Uses the same chi-squared approximation as :func:`lr_noise_floor`:
    ``p = exp(-LR^2 N_eff)``. For equal lengths this is the first-order
    Rayleigh approximation ``exp(-N R^2)``.

    Parameters
    ----------
    linearity_ratio : float
        Observed Linearity Ratio.
    lengths : numpy.ndarray
        Positive segment lengths.

    Returns
    -------
    float
        Approximate p-value in [0, 1].
    """
    if not np.isfinite(linearity_ratio):
        raise ValueError("linearity_ratio must be finite")
    n_eff = effective_sample_size(lengths)
    return float(np.clip(np.exp(-(linearity_ratio**2) * n_eff), 0.0, 1.0))


@dataclass
class TurningStatistics:
    """Summary of the turning angles between consecutive segments.

    Attributes
    ----------
    n_turns : int
        Number of turning angles (segments minus one).
    mean_vector_length : float
        Mean resultant length of the turning angles: local regularity of
        the chain, independent of how many segments are traced.
    mean_turning : float
        Circular mean turning angle (radians): the curvature per segment.
    circular_std : float
        Circular standard deviation of the turning angles (radians).
    curvature_per_unit : float
        ``mean_turning`` divided by the mean segment length (radians per
        length unit); NaN when lengths are not given.
    """

    n_turns: int
    mean_vector_length: float
    mean_turning: float
    circular_std: float
    curvature_per_unit: float

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dictionary view."""
        return asdict(self)


def turning_angle_statistics(
    headings: np.ndarray, lengths: np.ndarray | None = None
) -> TurningStatistics:
    """Circular summaries of the turning angles of a chain.

    Parameters
    ----------
    headings : numpy.ndarray
        Segment headings in radians, in chain order.
    lengths : numpy.ndarray, optional
        Segment lengths, used only for ``curvature_per_unit``.

    Returns
    -------
    TurningStatistics
        All fields are NaN when the chain has fewer than two segments.

    Notes
    -----
    With independent headings of mean resultant length ``rho`` the
    expected mean resultant length of the turning angles is ``rho^2``;
    with serially correlated headings (persistent or curved myofibrils)
    it stays fixed while the Linearity Ratio decreases with chain length.
    """
    theta = _validated_headings(headings)
    turns = _wrap(np.diff(theta))
    if turns.size == 0:
        nan = float("nan")
        return TurningStatistics(0, nan, nan, nan, nan)
    resultant = np.mean(np.exp(1j * turns))
    r = float(min(1.0, np.abs(resultant)))
    mean_turn = float(np.angle(resultant))
    csd = float(np.sqrt(-2.0 * np.log(r))) if r > 0 else float("inf")
    curvature = float("nan")
    if lengths is not None:
        arr = _validated_lengths(lengths)
        if arr.size != theta.size:
            raise ValueError("lengths and headings must have the same size")
        curvature = mean_turn / float(arr.mean())
    return TurningStatistics(
        n_turns=int(turns.size),
        mean_vector_length=r,
        mean_turning=mean_turn,
        circular_std=csd,
        curvature_per_unit=curvature,
    )


@dataclass
class TangentCorrelation:
    """Tangent correlation function ``C(k) = mean cos(theta_{i+k} - theta_i)``.

    Attributes
    ----------
    lags : list of int
        Lags ``k`` in segments, starting at 1.
    values : list of float
        ``C(k)`` for each lag.
    n_pairs : list of int
        Number of segment pairs averaged at each lag.
    """

    lags: list[int]
    values: list[float]
    n_pairs: list[int]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dictionary view."""
        return asdict(self)


def tangent_correlation(headings: np.ndarray, max_lag: int | None = None) -> TangentCorrelation:
    """Tangent correlation function of a chain.

    ``C(k)`` is flat for independent headings (``C(k) = rho^2`` for every
    ``k >= 1``), decays geometrically for a persistent (worm-like) chain
    and oscillates as ``cos(k c)`` for a chain of constant curvature
    ``c`` per segment.

    Parameters
    ----------
    headings : numpy.ndarray
        Segment headings in radians, in chain order.
    max_lag : int, optional
        Largest lag (default: number of segments minus one).

    Returns
    -------
    TangentCorrelation
        Empty lists for chains of fewer than two segments.
    """
    if max_lag is not None and max_lag < 1:
        raise ValueError("max_lag must be at least 1")
    theta = _validated_headings(headings)
    n = theta.size
    top = n - 1 if max_lag is None else min(int(max_lag), n - 1)
    lags, values, pairs = [], [], []
    for k in range(1, top + 1):
        diffs = theta[k:] - theta[:-k]
        lags.append(k)
        values.append(float(np.mean(np.cos(diffs))))
        pairs.append(int(diffs.size))
    return TangentCorrelation(lags=lags, values=values, n_pairs=pairs)


@dataclass
class PersistenceFit:
    """Exponential fit ``C(k) = A exp(-k L / l_p)`` of the tangent correlation.

    Attributes
    ----------
    persistence_length : float
        ``l_p`` in the unit of the segment lengths; ``inf`` when ``C(k)``
        does not decay (independent headings), NaN when it cannot be
        fitted.
    amplitude : float
        ``A``: the correlation left at short lags, i.e. local order
        (``rho^2`` for independent headings, close to 1 for a smooth
        worm-like chain).
    mean_segment_length : float
        ``L`` used to convert lags into lengths.
    lags_used : list of int
        Lags entering the fit.
    """

    persistence_length: float
    amplitude: float
    mean_segment_length: float
    lags_used: list[int]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dictionary view."""
        return asdict(self)


def persistence_length(
    headings: np.ndarray,
    lengths: np.ndarray,
    max_lag: int | None = None,
    min_pairs: int = _MIN_PAIRS_FOR_FIT,
) -> PersistenceFit:
    """Persistence length of a chain from its tangent correlation function.

    Fits ``log C(k) = log A - k L / l_p`` by least squares weighted by
    the number of pairs at each lag, using the lags with ``C(k) > 0`` and
    at least ``min_pairs`` pairs.

    Parameters
    ----------
    headings : numpy.ndarray
        Segment headings in radians, in chain order.
    lengths : numpy.ndarray
        Segment lengths (same size as ``headings``).
    max_lag : int, optional
        Largest lag used (default: a third of the number of segments,
        at least 2; longer lags rest on too few pairs).
    min_pairs : int, optional
        Minimum number of pairs for a lag to enter the fit (default 3).

    Returns
    -------
    PersistenceFit
        NaN fields when fewer than two lags qualify.

    Notes
    -----
    For a worm-like chain the fit recovers ``l_p = -L / log c`` with
    ``c`` the mean cosine of the turning angles. Fitting a single lag
    cannot tell a persistent chain from independent headings with the
    same local wobble, which is why at least two lags are required.
    When ``l_p`` is much longer than the traced chain, as for nearly
    straight myofibrils, the estimate from one chain is imprecise and
    biased upward; :func:`pooled_persistence_length` combines several
    myofibrils of a sample.
    """
    theta = _validated_headings(headings)
    arr = _validated_lengths(lengths)
    if arr.size != theta.size:
        raise ValueError("lengths and headings must have the same size")
    if max_lag is None:
        max_lag = max(2, theta.size // 3)
    corr = tangent_correlation(theta, max_lag)
    return _fit_persistence(corr, float(arr.mean()), min_pairs)


def _fit_persistence(corr: TangentCorrelation, mean_len: float, min_pairs: int) -> PersistenceFit:
    keep = [
        (k, c, m)
        for k, c, m in zip(corr.lags, corr.values, corr.n_pairs, strict=True)
        if c > 0 and m >= min_pairs
    ]
    if len(keep) < 2:
        return PersistenceFit(float("nan"), float("nan"), mean_len, [k for k, _, _ in keep])
    k = np.array([row[0] for row in keep], dtype=float)
    y = np.log([row[1] for row in keep])
    w = np.array([row[2] for row in keep], dtype=float)
    slope, intercept = np.polyfit(k, y, 1, w=np.sqrt(w))
    lp = float("inf") if slope >= 0 else float(-mean_len / slope)
    return PersistenceFit(
        persistence_length=lp,
        amplitude=float(np.exp(intercept)),
        mean_segment_length=mean_len,
        lags_used=[int(v) for v in k],
    )


def pooled_persistence_length(
    chains: list[tuple[np.ndarray, np.ndarray]],
    max_lag: int | None = None,
    min_pairs: int = _MIN_PAIRS_FOR_FIT,
) -> PersistenceFit:
    """Persistence length of a sample of myofibrils from their pooled ``C(k)``.

    The tangent correlations of the chains are averaged lag by lag,
    weighted by their numbers of pairs, and fitted as in
    :func:`persistence_length`. For nearly straight myofibrils, whose
    persistence length is much longer than one traced chain, pooling ten
    or more chains gives a nearly unbiased estimate where single chains
    do not.

    Parameters
    ----------
    chains : list of (headings, lengths)
        One pair of arrays per myofibril.
    max_lag : int, optional
        Largest lag (default: a third of the shortest chain, at least 2).
    min_pairs : int, optional
        Minimum pooled number of pairs per lag (default 3).

    Returns
    -------
    PersistenceFit
    """
    if not chains:
        raise ValueError("At least one chain is required")
    parsed = []
    for headings, lengths in chains:
        theta = _validated_headings(headings)
        arr = _validated_lengths(lengths)
        if arr.size != theta.size:
            raise ValueError("lengths and headings must have the same size")
        parsed.append((theta, arr))
    if max_lag is None:
        max_lag = max(2, min(t.size for t, _ in parsed) // 3)
    sums: dict[int, list[float]] = {}
    for theta, _ in parsed:
        corr = tangent_correlation(theta, max_lag)
        for k, c, m in zip(corr.lags, corr.values, corr.n_pairs, strict=True):
            acc = sums.setdefault(k, [0.0, 0])
            acc[0] += c * m
            acc[1] += m
    lags = sorted(sums)
    pooled = TangentCorrelation(
        lags=lags,
        values=[sums[k][0] / sums[k][1] for k in lags],
        n_pairs=[int(sums[k][1]) for k in lags],
    )
    total_len = sum(float(arr.sum()) for _, arr in parsed)
    total_n = sum(arr.size for _, arr in parsed)
    return _fit_persistence(pooled, total_len / total_n, min_pairs)


@dataclass
class SerialDependenceResult:
    """Test of serial dependence of the headings of a chain.

    Attributes
    ----------
    statistic : float
        ``D = R_turn - R_head^2``: about 0 for independent headings,
        positive when consecutive headings are correlated.
    turning_mvl : float
        Mean resultant length of the turning angles.
    heading_mvl : float
        Mean resultant length of the headings.
    p_value : float
        One-sided p-value for serial dependence.
    method : str
        ``"permutation"`` or ``"parametric"``.
    n_resamples : int
        Number of permutations or parametric replicates.
    """

    statistic: float
    turning_mvl: float
    heading_mvl: float
    p_value: float
    method: str
    n_resamples: int

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dictionary view."""
        return asdict(self)


def _turning_r_rows(theta: np.ndarray) -> np.ndarray:
    """Mean resultant length of turning angles for each row of ``theta``."""
    r: np.ndarray = np.abs(np.mean(np.exp(1j * np.diff(theta, axis=-1)), axis=-1))
    return r


def _heading_r_rows(theta: np.ndarray) -> np.ndarray:
    r: np.ndarray = np.abs(np.mean(np.exp(1j * theta), axis=-1))
    return r


def serial_dependence_test(
    headings: np.ndarray,
    method: str = "permutation",
    n_resamples: int = DEFAULT_N_PERMUTATIONS,
    seed: int | None = None,
) -> SerialDependenceResult:
    """Test whether consecutive headings are more alike than chance.

    ``permutation`` (default) compares the turning-angle resultant of the
    observed order with that of random re-orderings of the same headings.
    The Linearity Ratio does not depend on the order, so the null is
    exact under exchangeable headings and needs no distributional
    assumption. ``parametric`` compares ``D`` with chains of independent
    von Mises headings with the observed mean direction and
    concentration; it is slightly more powerful when that model holds.

    Parameters
    ----------
    headings : numpy.ndarray
        Segment headings in radians, in chain order (at least three).
    method : {"permutation", "parametric"}, optional
        Null distribution (default ``"permutation"``).
    n_resamples : int, optional
        Number of permutations or replicates (default 999).
    seed : int, optional
        Seed for reproducibility.

    Returns
    -------
    SerialDependenceResult
    """
    theta = _validated_headings(headings)
    if theta.size < 3:
        raise ValueError("At least 3 headings are required to test serial dependence")
    if method not in ("permutation", "parametric"):
        raise ValueError("method must be 'permutation' or 'parametric'")
    if n_resamples < 1:
        raise ValueError("n_resamples must be positive")
    rng = np.random.default_rng(seed)
    r_turn = float(_turning_r_rows(theta))
    r_head = float(_heading_r_rows(theta))
    d_obs = r_turn - r_head**2

    if method == "permutation":
        order = np.argsort(rng.random((n_resamples, theta.size)), axis=1)
        null_turn = _turning_r_rows(theta[order])
        exceed = int(np.sum(null_turn >= r_turn - 1e-12))
    else:
        from .circular_stats import calculate_circular_mean, calculate_concentration_parameter

        kappa = calculate_concentration_parameter(theta)
        mu = calculate_circular_mean(theta)
        sims = rng.vonmises(mu, max(kappa, 1e-12), size=(n_resamples, theta.size))
        null_d = _turning_r_rows(sims) - _heading_r_rows(sims) ** 2
        exceed = int(np.sum(null_d >= d_obs - 1e-12))

    return SerialDependenceResult(
        statistic=float(d_obs),
        turning_mvl=r_turn,
        heading_mvl=r_head,
        p_value=(1 + exceed) / (n_resamples + 1),
        method=method,
        n_resamples=int(n_resamples),
    )


_BELT_KAPPAS = np.concatenate([[0.0], np.logspace(-2.0, 4.5, 159)])
DEFAULT_BELT_SIMULATIONS = 2000


def _rho_of_kappa(kappa: np.ndarray) -> np.ndarray:
    """Population resultant ``I1(kappa) / I0(kappa)`` of a von Mises distribution."""
    from scipy.special import i0e, i1e

    kappa = np.asarray(kappa, dtype=float)
    return np.where(kappa > 0, i1e(kappa) / i0e(np.maximum(kappa, 1e-300)), 0.0)


@lru_cache(maxsize=64)
def _confidence_belt(
    n: int, confidence: float, n_sim: int, seed: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Quantiles of R for von Mises samples of size ``n`` over a grid of kappa.

    Returns ``(rho_grid, q_low, q_high)``; both quantile curves are made
    non-decreasing in kappa.
    """
    alpha = 1.0 - confidence
    q_low = np.empty(_BELT_KAPPAS.size)
    q_high = np.empty(_BELT_KAPPAS.size)
    for i, kappa in enumerate(_BELT_KAPPAS):
        rng = np.random.default_rng([seed, i])
        if kappa == 0:
            theta = rng.uniform(-np.pi, np.pi, (n_sim, n))
        else:
            theta = rng.vonmises(0.0, kappa, (n_sim, n))
        r = np.abs(np.mean(np.exp(1j * theta), axis=1))
        q_low[i], q_high[i] = np.quantile(r, [alpha / 2.0, 1.0 - alpha / 2.0])
    return (
        _rho_of_kappa(_BELT_KAPPAS),
        np.maximum.accumulate(q_low),
        np.maximum.accumulate(q_high),
    )


def _crossing(rho: np.ndarray, curve: np.ndarray, target: float) -> float:
    """Rho at which the non-decreasing ``curve`` first reaches ``target``."""
    i = int(np.searchsorted(curve, target, side="left"))
    if i == 0:
        return float(rho[0])
    if i >= curve.size:
        return float(rho[-1])
    c0, c1 = curve[i - 1], curve[i]
    w = 0.0 if c1 == c0 else (target - c0) / (c1 - c0)
    return float(rho[i - 1] + w * (rho[i] - rho[i - 1]))


def mvl_confidence_interval(
    mean_vector_length: float,
    n: int,
    confidence: float = 0.95,
    n_sim: int = DEFAULT_BELT_SIMULATIONS,
    seed: int = 0,
) -> tuple[float, float]:
    """Confidence interval for the population resultant ``rho`` (von Mises model).

    The interval inverts the sampling distribution of R under von Mises
    headings (a Neyman construction): it contains every ``rho`` for which
    the observed R lies within the central ``confidence`` range of R. R
    is sufficient for the concentration, so the coverage is nominal under
    the model for any ``n``. Percentile-bootstrap intervals of R, by
    contrast, inherit its upward bias and cover ``rho`` far less often
    than nominal for short chains (about 55-75% for 5-10 sarcomeres).

    Parameters
    ----------
    mean_vector_length : float
        Observed mean resultant length R of ``n`` headings.
    n : int
        Number of headings.
    confidence : float, optional
        Confidence level in (0, 1) (default 0.95).
    n_sim : int, optional
        Monte Carlo samples per grid value of the concentration used to
        build the belt (default 2000); the belt is cached per ``n``.
    seed : int, optional
        Seed of the belt simulation (default 0).

    Returns
    -------
    tuple of float
        ``(lower, upper)`` in [0, 1]; ``(0, 1)`` for fewer than two headings.
    """
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be in (0, 1)")
    if not (np.isfinite(mean_vector_length) and 0.0 <= mean_vector_length <= 1.0 + 1e-12):
        raise ValueError("mean_vector_length must be in [0, 1]")
    if n < 2:
        return 0.0, 1.0
    rho, q_low, q_high = _confidence_belt(int(n), float(confidence), int(n_sim), int(seed))
    r = float(min(mean_vector_length, 1.0))
    lower = _crossing(rho, q_high, r)
    upper = 1.0 if r >= q_low[-1] else _crossing(rho, q_low, r)
    return lower, max(lower, upper)


__all__ = [
    "DEFAULT_ALPHA",
    "DEFAULT_BELT_SIMULATIONS",
    "DEFAULT_N_PERMUTATIONS",
    "mvl_confidence_interval",
    "PersistenceFit",
    "SerialDependenceResult",
    "TangentCorrelation",
    "TurningStatistics",
    "corrected_rho_squared",
    "effective_sample_size",
    "lr_noise_floor",
    "lr_uniformity_p",
    "persistence_length",
    "pooled_persistence_length",
    "serial_dependence_test",
    "tangent_correlation",
    "turning_angle_statistics",
]
