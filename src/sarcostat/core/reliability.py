"""Variance components of a nested observer study, with MLS intervals.

The design has ``A`` annotators, each with ``S`` sessions of their own
(sessions nested in annotators), in which every annotator measures the
same ``T`` targets once per session. Some targets are shown a second time
within a session (duplicates). With all effects random except the mean
(unrestricted parametrisation), an observation is

    y = mu + tau_t + alpha_a + beta_s(a) + (alpha tau)_at + (beta tau)_s(a)t + eps

with variances ``sigma^2_t``, ``sigma^2_a``, ``sigma^2_s(a)``,
``sigma^2_at``, ``sigma^2_bt`` and ``sigma^2_e``. With one observation
per cell, ``sigma^2_bt`` and ``sigma^2_e`` are confounded in the residual
``sigma^2_res = sigma^2_bt + sigma^2_e``; the duplicates separate them,
because a repeat within a session shares ``(beta tau)_s(a)t`` and differs
only by ``eps``.

The balanced-ANOVA strata and their expected mean squares are

==================== ======================= ===========================================
Stratum              Degrees of freedom      E[MS]
==================== ======================= ===========================================
annotator            A - 1                   res + S at + T s(a) + S T a
session (in annot.)  A (S - 1)               res + T s(a)
target               T - 1                   res + S at + A S t
annotator x target   (A - 1)(T - 1)          res + S at
residual             A (S - 1)(T - 1)        res
pure error           one per duplicate pair  e
==================== ======================= ===========================================

(:func:`ems_coefficients`). The repeatability of a single annotator is

    sigma^2_eff = sigma^2_s(a) + sigma^2_at + sigma^2_res
                = MS_session / T + MS_axt / S + (1 - 1/T - 1/S) MS_residual,

a non-negative combination of mean squares, so it has a modified
large-sample (MLS) interval (:func:`mls_interval`; Graybill & Wang 1980).
The annotator offset ``sigma^2_a`` is reported but not part of it. The
duplicates' pure error estimates ``sigma^2_e`` only, which is smaller than
``sigma^2_res``, so ``sigma^2_eff`` never uses it.

References
----------
Graybill, F. A. & Wang, C.-M. (1980). Confidence intervals on nonnegative
linear combinations of variances. Journal of the American Statistical
Association, 75(372), 869-873. doi:10.1080/01621459.1980.10477565

Burdick, R. K. & Graybill, F. A. (1992). Confidence Intervals on Variance
Components. Marcel Dekker, New York.

Burdick, R. K., Borror, C. M. & Montgomery, D. C. (2005). Design and
Analysis of Gauge R&R Studies: Making Decisions with Confidence Intervals
in Random and Mixed ANOVA Models. ASA-SIAM, Philadelphia.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

STRATA = ("annotator", "session", "target", "annotator_x_target", "residual")


def ems_coefficients(
    n_annotators: int, n_sessions: int, n_targets: int
) -> dict[str, dict[str, int]]:
    """Coefficients of the variance components in each expected mean square.

    Returns
    -------
    dict
        ``{stratum: {component: coefficient}}`` for the strata in
        :data:`STRATA`; a component's name is that of the stratum it
        defines (``"residual"`` is ``sigma^2_bt + sigma^2_e``).
    """
    a, s, t = n_annotators, n_sessions, n_targets
    return {
        "annotator": {"residual": 1, "annotator_x_target": s, "session": t, "annotator": s * t},
        "session": {"residual": 1, "session": t},
        "target": {"residual": 1, "annotator_x_target": s, "target": a * s},
        "annotator_x_target": {"residual": 1, "annotator_x_target": s},
        "residual": {"residual": 1},
    }


@dataclass(frozen=True)
class BalancedCore:
    """Complete-case data of the observer design.

    Attributes
    ----------
    values : numpy.ndarray, shape (A, S, T)
        First presentations, indexed by annotator, session and target.
    annotators : tuple
        Annotator labels, sorted.
    sessions : tuple of tuple
        Session labels of each annotator, sorted.
    targets : tuple
        Targets observed in every annotator and session, sorted.
    dropped_targets : tuple
        Targets left out because at least one first presentation is
        missing.
    duplicate_pairs : numpy.ndarray, shape (n_pairs, 2)
        First and second presentation of every target shown twice in the
        same session, whether or not the target is in ``targets``.
    """

    values: np.ndarray
    annotators: tuple[Any, ...]
    sessions: tuple[tuple[Any, ...], ...]
    targets: tuple[Any, ...]
    dropped_targets: tuple[Any, ...]
    duplicate_pairs: np.ndarray


def complete_cases(
    annotator: Sequence[Any],
    session: Sequence[Any],
    target: Sequence[Any],
    value: Sequence[float],
    presentation: Sequence[int] | None = None,
) -> BalancedCore:
    """Arrange long-format observations into the balanced complete-case core.

    Parameters
    ----------
    annotator, session, target : sequence
        Labels of each observation. Session labels are read within each
        annotator; every annotator must have the same number of sessions.
        Labels of one kind must be mutually sortable (e.g. all strings).
    value : sequence of float
        The measurements.
    presentation : sequence of int, optional
        1 for the first presentation of a target in a session, 2 for its
        repeat (duplicates). Default: all 1.

    Returns
    -------
    BalancedCore
        Targets missing in any annotator-session cell are dropped from the
        core and listed in ``dropped_targets``.

    Raises
    ------
    ValueError
        On inputs of different lengths, non-finite values, a presentation
        other than 1 or 2, the same presentation recorded twice, or
        annotators with different numbers of sessions.
    """
    n = len(value)
    pres = [1] * n if presentation is None else list(presentation)
    if not len(annotator) == len(session) == len(target) == len(pres) == n:
        raise ValueError("annotator, session, target, value and presentation must have one length")
    vals = np.asarray(value, dtype=float)
    if not np.isfinite(vals).all():
        raise ValueError("Values must be finite")
    cells: dict[tuple[Any, Any, Any, int], float] = {}
    for a, s, t, p, v in zip(annotator, session, target, pres, vals, strict=True):
        if p not in (1, 2):
            raise ValueError(f"presentation must be 1 or 2, got {p!r}")
        key = (a, s, t, p)
        if key in cells:
            raise ValueError(f"Observation {key} recorded twice")
        cells[key] = float(v)

    annotators = tuple(sorted({a for a, _s, _t, _p in cells}))
    sessions = tuple(tuple(sorted({s for a, s, _t, _p in cells if a == ann})) for ann in annotators)
    if len({len(s) for s in sessions}) != 1:
        raise ValueError(
            "Every annotator must have the same number of sessions, got "
            f"{dict(zip(annotators, (len(s) for s in sessions), strict=True))}"
        )
    all_targets = sorted({t for _a, _s, t, _p in cells})
    grid = [(a, s) for a, ses in zip(annotators, sessions, strict=True) for s in ses]
    kept = tuple(t for t in all_targets if all((a, s, t, 1) in cells for a, s in grid))
    dropped = tuple(t for t in all_targets if t not in kept)
    values = np.array(
        [
            [[cells[(a, s, t, 1)] for t in kept] for s in ses]
            for a, ses in zip(annotators, sessions, strict=True)
        ],
        dtype=float,
    ).reshape(len(annotators), len(sessions[0]), len(kept))
    pairs = np.array(
        [
            (cells[(a, s, t, 1)], v)
            for (a, s, t, p), v in cells.items()
            if p == 2 and (a, s, t, 1) in cells
        ],
        dtype=float,
    ).reshape(-1, 2)
    return BalancedCore(values, annotators, sessions, kept, dropped, pairs)


def _mean_squares(y: np.ndarray) -> tuple[dict[str, float], dict[str, int]]:
    """Mean squares and degrees of freedom of a balanced ``(A, S, T)`` array."""
    a, s, t = y.shape
    m = y.mean()
    m_a = y.mean(axis=(1, 2))
    m_as = y.mean(axis=2)
    m_t = y.mean(axis=(0, 1))
    m_at = y.mean(axis=1)
    ss = {
        "annotator": s * t * np.sum((m_a - m) ** 2),
        "session": t * np.sum((m_as - m_a[:, None]) ** 2),
        "target": a * s * np.sum((m_t - m) ** 2),
        "annotator_x_target": s * np.sum((m_at - m_a[:, None] - m_t[None, :] + m) ** 2),
        "residual": np.sum((y - m_as[:, :, None] - m_at[:, None, :] + m_a[:, None, None]) ** 2),
    }
    df = {
        "annotator": a - 1,
        "session": a * (s - 1),
        "target": t - 1,
        "annotator_x_target": (a - 1) * (t - 1),
        "residual": a * (s - 1) * (t - 1),
    }
    return {k: float(ss[k] / df[k]) for k in STRATA}, df


@dataclass(frozen=True)
class VarianceComponents:
    """ANOVA (method-of-moments) estimates of the observer variance components.

    Attributes
    ----------
    n_annotators, n_sessions, n_targets : int
        Size of the balanced core.
    mean_squares, df : dict
        Mean square and degrees of freedom of each stratum (see the module
        docstring), plus ``"pure_error"`` when there are duplicate pairs.
    components : dict
        Unbiased estimates (they can be negative) of ``annotator``,
        ``session``, ``target``, ``annotator_x_target`` and ``residual``,
        and with duplicates also ``pure_error`` (sigma^2_e) and
        ``session_x_target`` (sigma^2_bt = residual - pure error).
    """

    n_annotators: int
    n_sessions: int
    n_targets: int
    mean_squares: dict[str, float]
    df: dict[str, int]
    components: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dictionary view."""
        return asdict(self)


def _solve_components(
    mean_squares: dict[str, Any], n_annotators: int, n_sessions: int, n_targets: int
) -> dict[str, Any]:
    coef = ems_coefficients(n_annotators, n_sessions, n_targets)
    k = np.array([[coef[row].get(col, 0) for col in STRATA] for row in STRATA], dtype=float)
    ms = np.array([np.asarray(mean_squares[q], dtype=float) for q in STRATA])
    sol = np.linalg.solve(k, ms.reshape(len(STRATA), -1)).reshape(ms.shape)
    out = {name: sol[i] for i, name in enumerate(STRATA)}
    if "pure_error" in mean_squares:
        out["pure_error"] = np.asarray(mean_squares["pure_error"], dtype=float)
        out["session_x_target"] = out["residual"] - out["pure_error"]
    return out


def variance_components(core: BalancedCore) -> VarianceComponents:
    """Estimate the variance components of the balanced core.

    The components solve the expected-mean-square equations of
    :func:`ems_coefficients`; with duplicate pairs, the pure error
    ``sum (y1 - y2)^2 / (2 n_pairs)`` estimates ``sigma^2_e`` and
    ``residual - pure error`` estimates ``sigma^2_bt``.

    Parameters
    ----------
    core : BalancedCore
        Output of :func:`complete_cases`, with at least two annotators,
        sessions and targets.

    Returns
    -------
    VarianceComponents
    """
    a, s, t = core.values.shape
    if a < 2 or s < 2 or t < 2:
        raise ValueError(
            f"Need at least 2 annotators, 2 sessions and 2 complete targets, got {a}, {s} and {t}"
        )
    ms, df = _mean_squares(core.values)
    n_pairs = len(core.duplicate_pairs)
    if n_pairs:
        diff = core.duplicate_pairs[:, 0] - core.duplicate_pairs[:, 1]
        ms["pure_error"] = float(np.sum(diff**2) / (2 * n_pairs))
        df["pure_error"] = n_pairs
    comps = {k: float(v) for k, v in _solve_components(ms, a, s, t).items()}
    return VarianceComponents(a, s, t, ms, df, comps)


def _effective_coefficients(n_sessions: int, n_targets: int) -> dict[str, float]:
    """Coefficients of sigma^2_s(a) + sigma^2_at + sigma^2_res on the mean squares."""
    return {
        "session": 1.0 / n_targets,
        "annotator_x_target": 1.0 / n_sessions,
        "residual": 1.0 - 1.0 / n_targets - 1.0 / n_sessions,
    }


def mls_interval(
    coefficients: Sequence[float],
    mean_squares: Sequence[float],
    df: Sequence[float],
    confidence: float = 0.95,
) -> tuple[float, float]:
    """Graybill-Wang MLS interval for a non-negative combination of expected mean squares.

    For ``theta = sum_q c_q E[MS_q]`` with ``c_q >= 0`` and independent
    mean squares ``MS_q ~ E[MS_q] chi^2_{n_q} / n_q``, the estimate is
    ``theta_hat = sum_q c_q MS_q`` and

        L = theta_hat - sqrt(sum_q G_q^2 c_q^2 MS_q^2),
        U = theta_hat + sqrt(sum_q H_q^2 c_q^2 MS_q^2),

    with ``G_q = 1 - n_q / chi2_{1-alpha/2}(n_q)`` and
    ``H_q = n_q / chi2_{alpha/2}(n_q) - 1``, where ``chi2_p(n)`` is the
    quantile with probability ``p`` below it and ``alpha = 1 -
    confidence`` (Graybill & Wang 1980; Burdick, Borror & Montgomery
    2005). With a single mean square the interval is the exact chi-square
    interval.

    Parameters
    ----------
    coefficients, mean_squares, df : sequence of float
        ``c_q >= 0``, ``MS_q >= 0`` and ``n_q > 0`` of each term.
    confidence : float, optional
        Two-sided confidence level (default 0.95).

    Returns
    -------
    tuple of float
        ``(L, U)``; ``L > 0`` whenever ``theta_hat > 0``.
    """
    from scipy.stats import chi2

    c = np.asarray(coefficients, dtype=float)
    ms = np.asarray(mean_squares, dtype=float)
    n = np.asarray(df, dtype=float)
    if not (c.ndim == 1 and c.shape == ms.shape == n.shape and c.size > 0):
        raise ValueError("coefficients, mean_squares and df must be 1-D and of one length")
    if not (np.isfinite(c).all() and np.isfinite(ms).all() and np.isfinite(n).all()):
        raise ValueError("coefficients, mean_squares and df must be finite")
    if (c < 0).any() or (ms < 0).any() or (n <= 0).any():
        raise ValueError("Need coefficients >= 0, mean squares >= 0 and df > 0")
    if not 0 < confidence < 1:
        raise ValueError(f"confidence must be in (0, 1), got {confidence}")
    alpha = 1.0 - confidence
    g = 1.0 - n / chi2.ppf(1.0 - alpha / 2.0, n)
    h = n / chi2.ppf(alpha / 2.0, n) - 1.0
    est = float(c @ ms)
    lower = est - float(np.sqrt(np.sum((g * c * ms) ** 2)))
    upper = est + float(np.sqrt(np.sum((h * c * ms) ** 2)))
    return lower, upper


@dataclass(frozen=True)
class EffectiveSigma:
    """Single-annotator repeatability sigma_eff with its MLS interval.

    Attributes
    ----------
    variance, sd : float
        ``sigma^2_eff = sigma^2_s(a) + sigma^2_at + sigma^2_res`` and its
        square root.
    variance_ci, sd_ci : tuple of float
        MLS interval of the variance and its square root.
    coefficients : dict
        Coefficients of ``sigma^2_eff`` on the mean squares.
    confidence : float
        Confidence level of the intervals.
    """

    variance: float
    sd: float
    variance_ci: tuple[float, float]
    sd_ci: tuple[float, float]
    coefficients: dict[str, float]
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dictionary view."""
        return asdict(self)


def sigma_effective(vc: VarianceComponents, confidence: float = 0.95) -> EffectiveSigma:
    """Repeatability of a single annotator, ``sigma^2_s(a) + sigma^2_at + sigma^2_res``.

    It is ``MS_session/T + MS_axt/S + (1 - 1/T - 1/S) MS_residual``, with
    a Graybill-Wang MLS interval (:func:`mls_interval`). The duplicates'
    pure error is not used: it misses ``sigma^2_bt``.
    """
    coef = _effective_coefficients(vc.n_sessions, vc.n_targets)
    names = list(coef)
    est = sum(coef[q] * vc.mean_squares[q] for q in names)
    lo, hi = mls_interval(
        [coef[q] for q in names],
        [vc.mean_squares[q] for q in names],
        [vc.df[q] for q in names],
        confidence,
    )
    return EffectiveSigma(
        variance=float(est),
        sd=float(np.sqrt(est)),
        variance_ci=(lo, hi),
        sd_ci=(float(np.sqrt(max(lo, 0.0))), float(np.sqrt(hi))),
        coefficients=coef,
        confidence=confidence,
    )


@dataclass(frozen=True)
class BootstrapInterval:
    """Parametric-bootstrap percentile interval of a derived quantity.

    Attributes
    ----------
    estimate : float
        The quantity at the observed mean squares.
    ci : tuple of float
        Percentile interval.
    std_error : float
        Standard deviation of the bootstrap values.
    n_boot : int
        Number of bootstrap draws.
    confidence : float
        Confidence level of ``ci``.
    seed : int or None
        Seed of the draws.
    """

    estimate: float
    ci: tuple[float, float]
    std_error: float
    n_boot: int
    confidence: float
    seed: int | None


def _component_view(vc: VarianceComponents, ms: dict[str, Any]) -> dict[str, Any]:
    comps = _solve_components(ms, vc.n_annotators, vc.n_sessions, vc.n_targets)
    coef = _effective_coefficients(vc.n_sessions, vc.n_targets)
    comps["effective"] = sum(coef[q] * np.asarray(ms[q], dtype=float) for q in coef)
    return comps


def bootstrap_derived(
    fn: Callable[..., float],
    *fits: VarianceComponents,
    n_boot: int = 1000,
    confidence: float = 0.95,
    seed: int | None = 0,
) -> BootstrapInterval:
    """Parametric-bootstrap interval of a quantity derived from one or more fits.

    Each mean square is redrawn as ``MS_q chi^2_{n_q} / n_q``,
    independently, which is the sampling distribution of balanced-ANOVA
    mean squares under normality with the observed mean squares as their
    expectations; the components are then re-solved and passed to ``fn``.

    Parameters
    ----------
    fn : callable
        Called as ``fn(c_1, ..., c_k)`` with one dict per fit holding the
        components of :class:`VarianceComponents` plus ``"effective"``
        (``sigma^2_eff``); it returns a float. Components can be negative.
    *fits : VarianceComponents
        One or more fits, e.g. the Z-line and the M-line endpoints.
    n_boot : int, optional
        Number of draws (default 1000).
    confidence : float, optional
        Level of the percentile interval (default 0.95).
    seed : int, optional
        Seed (default 0).

    Returns
    -------
    BootstrapInterval
    """
    if not fits:
        raise ValueError("Give at least one VarianceComponents fit")
    if isinstance(n_boot, bool) or not isinstance(n_boot, (int, np.integer)) or n_boot < 2:
        raise ValueError(f"n_boot must be an integer >= 2, got {n_boot!r}")
    if not 0 < confidence < 1:
        raise ValueError(f"confidence must be in (0, 1), got {confidence}")
    rng = np.random.default_rng(seed)
    observed = [_component_view(vc, vc.mean_squares) for vc in fits]
    estimate = float(fn(*({k: float(v) for k, v in c.items()} for c in observed)))
    drawn = []
    for vc in fits:
        ms = {q: vc.mean_squares[q] * rng.chisquare(vc.df[q], n_boot) / vc.df[q] for q in vc.df}
        drawn.append(_component_view(vc, ms))
    values = np.array(
        [fn(*({k: float(v[b]) for k, v in c.items()} for c in drawn)) for b in range(int(n_boot))],
        dtype=float,
    )
    if not (np.isfinite(values).all() and np.isfinite(estimate)):
        raise ValueError("fn returned a non-finite value")
    tail = 100.0 * (1.0 - confidence) / 2.0
    lo, hi = np.percentile(values, [tail, 100.0 - tail])
    return BootstrapInterval(
        estimate=estimate,
        ci=(float(lo), float(hi)),
        std_error=float(values.std(ddof=1)),
        n_boot=int(n_boot),
        confidence=confidence,
        seed=seed,
    )


__all__ = [
    "STRATA",
    "BalancedCore",
    "BootstrapInterval",
    "EffectiveSigma",
    "VarianceComponents",
    "bootstrap_derived",
    "complete_cases",
    "ems_coefficients",
    "mls_interval",
    "sigma_effective",
    "variance_components",
]
