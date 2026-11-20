"""Moment-based circular statistics in numpy.

The four routines compute the mean resultant length, the circular mean
direction, the Rayleigh test of uniformity and the V-test of
concentration about a given direction (Berens 2009, CircStat; Zar 1999,
Biostatistical Analysis, ch. 27). Every resultant is formed in complex
form as ``nansum(w * exp(1j * a))`` and every array operation runs in a
fixed order, so the results are reproducible to the last bit on a given
platform. The only scipy call (the normal distribution function of the
V-test) is imported when the V-test runs.

Angles are in radians; their spread ``max - min`` (NaN ignored) must not
exceed ``2 * pi``. NaN angles are dropped from the resultant sums and,
for :func:`circ_r`, from the sum of weights; the sample size of the
unweighted Rayleigh and V tests is ``len(angles)``. Callers that need a
consistent n must reject NaN first, as :mod:`sarcostat.core.circular_stats`
does.
"""

from __future__ import annotations

from typing import Any

import numpy as np

_RADIANS_MESSAGE = (
    "Angles must be in radians with a spread of at most 2*pi; "
    "wrap them to [-pi, pi) before computing circular statistics."
)


def _check_radians(angles: np.ndarray) -> None:
    """Raise ValueError when the spread of ``angles`` exceeds ``2 * pi``.

    NaN values are ignored; an empty or all-NaN input raises as well.
    """
    spread = np.nanmax(angles) - np.nanmin(angles)
    if not (spread <= 2 * np.pi).all():
        raise ValueError(_RADIANS_MESSAGE)


def _weights(angles: np.ndarray, w: Any) -> np.ndarray:
    """Return ``w`` as an array of the shape of ``angles`` (ones when ``w`` is None)."""
    weights = np.asarray(w) if w is not None else np.ones(angles.shape)
    if angles.shape != weights.shape:
        raise ValueError("Input dimensions do not match")
    return weights


def circ_mean(angles: Any, w: Any = None, axis: int = 0) -> Any:
    """Circular mean direction ``arg(sum_j w_j * exp(i * a_j))``.

    Parameters
    ----------
    angles : array_like
        Angles in radians.
    w : array_like, optional
        Weights of the same shape as ``angles`` (default: all ones).
    axis : int, optional
        Axis along which the resultant is summed (default 0).

    Returns
    -------
    numpy.float64 or numpy.ndarray
        Mean direction in [-pi, pi].
    """
    angles = np.asarray(angles)
    _check_radians(angles)
    weights = _weights(angles, w)
    return np.angle(np.nansum(np.multiply(weights, np.exp(1j * angles)), axis=axis))


def circ_r(angles: Any, w: Any = None, d: float | None = None, axis: int = 0) -> Any:
    """Mean resultant length ``R = |sum_j w_j * exp(i * a_j)| / sum_j w_j``.

    Parameters
    ----------
    angles : array_like
        Angles in radians.
    w : array_like, optional
        Weights of the same shape as ``angles`` (default: all ones); the
        weight of a NaN angle is dropped.
    d : float, optional
        Bin spacing in radians for binned data; R is multiplied by the
        correction ``c = (d / 2) / sin(d / 2)``.
    axis : int, optional
        Axis along which the resultant is summed (default 0).

    Returns
    -------
    numpy.float64 or numpy.ndarray
        Mean resultant length in [0, 1] (above 1 is possible only with ``d``).
    """
    angles = np.asarray(angles)
    _check_radians(angles)
    weights = _weights(angles, w).astype(float)
    weights[np.isnan(angles)] = np.nan
    r = np.nansum(np.multiply(weights, np.exp(1j * angles)), axis=axis)
    r = np.abs(r) / np.nansum(weights, axis=axis)
    if d is not None:
        c = d / 2 / np.sin(d / 2)
        r = c * r
    return r


def _resultant_and_n(angles: np.ndarray, w: Any, d: float | None) -> tuple[Any, Any]:
    """Return ``(R, n)``: R from :func:`circ_r` and n = len(angles) or sum(w)."""
    if w is None:
        return circ_r(angles), len(angles)
    if len(angles) != len(w):
        raise ValueError("Input dimensions do not match")
    return circ_r(angles, w, d), np.sum(w)


def circ_rayleigh(angles: Any, w: Any = None, d: float | None = None) -> tuple[Any, Any]:
    """Rayleigh test of uniformity against a unimodal alternative.

    With the resultant ``Rn = n * R``, the statistic is ``z = Rn**2 / n``
    (that is ``n * R**2``) and the p-value is Zar's approximation
    ``exp(sqrt(1 + 4n + 4(n**2 - Rn**2)) - (1 + 2n))``.

    Parameters
    ----------
    angles : array_like
        One-dimensional angles in radians.
    w : array_like, optional
        Weights, for instance bin counts of binned data; n is then ``sum(w)``.
    d : float, optional
        Bin spacing in radians, applied together with ``w`` (see :func:`circ_r`).

    Returns
    -------
    tuple
        ``(z, p_value)``.
    """
    angles = np.asarray(angles)
    _check_radians(angles)
    r, n = _resultant_and_n(angles, w, d)
    resultant = n * r
    z = (resultant**2) / n
    pval = np.exp(np.sqrt(1 + 4 * n + 4 * (n**2 - resultant**2)) - (1 + 2 * n))
    return z, pval


def circ_vtest(
    angles: Any, direction: float = 0.0, w: Any = None, d: float | None = None
) -> tuple[Any, Any]:
    """V-test of concentration about a given direction.

    With the resultant ``Rn = n * R`` and the mean direction ``mu``, the
    statistic is ``V = Rn * cos(mu - direction)``; the one-sided p-value
    is ``1 - Phi(V * sqrt(2 / n))`` with ``Phi`` the standard normal
    distribution function.

    Parameters
    ----------
    angles : array_like
        One-dimensional angles in radians.
    direction : float, optional
        Hypothesised mean direction in radians (default 0.0).
    w : array_like, optional
        Weights, for instance bin counts of binned data; n is then ``sum(w)``.
    d : float, optional
        Bin spacing in radians, applied to R together with ``w``.

    Returns
    -------
    tuple
        ``(V, p_value)``.
    """
    from scipy.special import ndtr

    angles = np.asarray(angles)
    r, n = _resultant_and_n(angles, w, d)
    mu = circ_mean(angles) if w is None else circ_mean(angles, w)
    resultant = n * r
    v = resultant * np.cos(mu - direction)
    u = v * np.sqrt(2 / n)
    pval = 1 - ndtr(u)
    return v, pval
