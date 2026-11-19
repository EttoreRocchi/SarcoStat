"""Annotation uncertainty of the single-sarcomere shape indices.

Each landmark of the 6-point annotation (A-B on Z-line 1, C-D on the
M-line, E-F on Z-line 2) is placed with an error that has a component
along its line and one perpendicular to it. This module turns a click
error into:

- standard uncertainties of AsR, Cur and the Shape Index SI = Cur/AsR
  by first-order propagation (:func:`shape_uncertainty`);
- the probability that a re-annotation keeps the same shape class, by
  Monte Carlo on the six points (:func:`class_stability`);
- the distance of a sarcomere from the class boundaries in the
  (AsR, SI) plane, where the classifier is axis-aligned
  (:func:`classification_margin`);
- an estimate of the click error itself from repeated annotations of
  one sarcomere (:func:`estimate_annotation_sigma`), after putting the
  landmarks of every repeat in one canonical order
  (:func:`canonicalize_sarcomere_landmarks`, :func:`landmark_offsets`).

Only the along-line error changes the Z-line and M-line lengths to
first order; the sarcomere length depends on the error of the Z-line
midpoints along the sarcomere axis, i.e. on the perpendicular component
of the Z-line clicks. SI does not depend on the sarcomere length at all.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from .sarcomere_morphometry import (
    _BOUNDARY_SLACK,
    DEFAULT_SHAPE_TOLERANCE,
    SarcomereGeometry,
    ShapeClassification,
    euclidean_distance,
)

DEFAULT_N_DRAWS = 2000

_CLASS_ORDER = (
    ShapeClassification.SQUARE,
    ShapeClassification.RECTANGULAR,
    ShapeClassification.BARREL,
    ShapeClassification.HOURGLASS,
)


def _check_sigma(name: str, value: float) -> float:
    if not (np.isfinite(value) and value >= 0):
        raise ValueError(f"{name} must be a non-negative finite number, got {value}")
    return float(value)


def _resolve_sigmas(
    sigma_z: float, sigma_m: float | None, perpendicular_ratio: float
) -> tuple[float, float, float, float]:
    """Return ``(z_along, z_perp, m_along, m_perp)`` click errors."""
    sz = _check_sigma("sigma_z", sigma_z)
    sm = sz if sigma_m is None else _check_sigma("sigma_m", sigma_m)
    ratio = _check_sigma("perpendicular_ratio", perpendicular_ratio)
    return sz, sz * ratio, sm, sm * ratio


def _unit_scale(geom: SarcomereGeometry) -> float:
    """Physical units per pixel of the stored annotation."""
    a, b = geom.z_line_1_points
    return geom.z_line_1_length / euclidean_distance(a, b)


@dataclass
class ShapeUncertainty:
    """First-order standard uncertainties of the shape indices.

    Attributes
    ----------
    aspect_ratio, curviness, shape_index : float
        Values of AsR, Cur and SI.
    u_aspect_ratio, u_curviness, u_shape_index : float
        Their standard uncertainties.
    sigma_z_along, sigma_z_perp, sigma_m_along, sigma_m_perp : float
        Click errors used, in the unit of the geometry.
    unit : str
        Unit of the click errors.
    """

    aspect_ratio: float
    curviness: float
    shape_index: float
    u_aspect_ratio: float
    u_curviness: float
    u_shape_index: float
    sigma_z_along: float
    sigma_z_perp: float
    sigma_m_along: float
    sigma_m_perp: float
    unit: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dictionary view."""
        return asdict(self)


def shape_uncertainty(
    geom: SarcomereGeometry,
    sigma_z: float,
    sigma_m: float | None = None,
    perpendicular_ratio: float = 1.0,
) -> ShapeUncertainty:
    """Propagate a click error to AsR, Cur and SI.

    Parameters
    ----------
    geom : SarcomereGeometry
        Annotated sarcomere.
    sigma_z : float
        Standard deviation of the along-line click error on the Z-line
        endpoints, in the unit of ``geom``.
    sigma_m : float, optional
        Same for the M-line endpoints (default: ``sigma_z``).
    perpendicular_ratio : float, optional
        Ratio of the perpendicular to the along-line error (default 1,
        isotropic clicks).

    Returns
    -------
    ShapeUncertainty

    Notes
    -----
    With independent errors, ``var(mean Z) = sigma_z_along^2``,
    ``var(L_M) = 2 sigma_m_along^2`` and ``var(L_s) = sigma_z_perp^2``
    (Z-lines perpendicular to the sarcomere axis), so
    ``u(SI)/SI = sqrt(var(mean Z)/mean Z^2 + var(L_M)/L_M^2)``. For the
    euploid mouse values of Cisterna et al. (2021) this is about 0.4% per
    nm of isotropic click error.
    """
    z_al, z_pe, m_al, m_pe = _resolve_sigmas(sigma_z, sigma_m, perpendicular_ratio)
    mean_z = (geom.z_line_1_length + geom.z_line_2_length) / 2.0
    rel_z2 = z_al**2 / mean_z**2
    rel_m2 = 2.0 * m_al**2 / geom.m_line_length**2
    rel_s2 = z_pe**2 / geom.sarcomere_length**2
    return ShapeUncertainty(
        aspect_ratio=geom.aspect_ratio,
        curviness=geom.curviness,
        shape_index=geom.shape_index,
        u_aspect_ratio=float(geom.aspect_ratio * np.sqrt(rel_s2 + rel_z2)),
        u_curviness=float(geom.curviness * np.sqrt(rel_s2 + rel_m2)),
        u_shape_index=float(geom.shape_index * np.sqrt(rel_z2 + rel_m2)),
        sigma_z_along=z_al,
        sigma_z_perp=z_pe,
        sigma_m_along=m_al,
        sigma_m_perp=m_pe,
        unit=geom.unit,
    )


def classify_shape_arrays(
    aspect_ratio: np.ndarray,
    curviness: np.ndarray,
    tolerance: float = DEFAULT_SHAPE_TOLERANCE,
) -> np.ndarray:
    """Vectorised :func:`classify_sarcomere_shape`.

    Returns an integer array indexing ``(SQUARE, RECTANGULAR, BARREL,
    HOURGLASS)``; the rules, tolerance and boundary slack are the same as
    in the scalar classifier.
    """
    asr = np.asarray(aspect_ratio, dtype=float)
    cur = np.asarray(curviness, dtype=float)
    edge = tolerance + _BOUNDARY_SLACK
    near_diagonal = np.abs((cur - asr) / asr) <= edge
    square = near_diagonal & (np.abs(asr - 1.0) <= edge)
    out = np.where(cur < asr, 2, 3)
    out = np.where(near_diagonal, 1, out)
    return np.where(square, 0, out)


@dataclass
class ClassStability:
    """Monte Carlo stability of the shape class under re-annotation.

    Attributes
    ----------
    reference_class : str
        Class of the annotation as placed.
    stability : float
        Fraction of simulated re-annotations that keep the reference
        class. It is a repeatability measure, not the probability of the
        true class (which would need a prior on the population).
    class_frequencies : dict
        Fraction of re-annotations in each class.
    sd_aspect_ratio, sd_curviness, sd_shape_index : float
        Monte Carlo standard deviations of the indices.
    n_draws : int
        Number of simulated re-annotations.
    """

    reference_class: str
    stability: float
    class_frequencies: dict[str, float]
    sd_aspect_ratio: float
    sd_curviness: float
    sd_shape_index: float
    n_draws: int

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dictionary view."""
        return asdict(self)


def _perturb_line(
    p1: np.ndarray, p2: np.ndarray, s_along: float, s_perp: float, n: int, rng
) -> tuple[np.ndarray, np.ndarray]:
    """Perturb both endpoints of a line along and across its direction."""
    direction = (p2 - p1) / np.linalg.norm(p2 - p1)
    normal = np.array([-direction[1], direction[0]])
    out = []
    for p in (p1, p2):
        along = rng.normal(0.0, s_along, n) if s_along > 0 else np.zeros(n)
        perp = rng.normal(0.0, s_perp, n) if s_perp > 0 else np.zeros(n)
        out.append(p + along[:, None] * direction + perp[:, None] * normal)
    return out[0], out[1]


def class_stability(
    geom: SarcomereGeometry,
    sigma_z: float,
    sigma_m: float | None = None,
    perpendicular_ratio: float = 1.0,
    n_draws: int = DEFAULT_N_DRAWS,
    tolerance: float = DEFAULT_SHAPE_TOLERANCE,
    seed: int | None = None,
) -> ClassStability:
    """Probability that a re-annotation keeps the same shape class.

    Parameters
    ----------
    geom : SarcomereGeometry
        Annotated sarcomere.
    sigma_z, sigma_m, perpendicular_ratio : float
        Click errors as in :func:`shape_uncertainty`.
    n_draws : int, optional
        Number of simulated re-annotations (default 2000).
    tolerance : float, optional
        Classifier tolerance (default 0.05).
    seed : int, optional
        Seed for reproducibility.

    Returns
    -------
    ClassStability
    """
    if n_draws < 1:
        raise ValueError("n_draws must be positive")
    z_al, z_pe, m_al, m_pe = _resolve_sigmas(sigma_z, sigma_m, perpendicular_ratio)
    rng = np.random.default_rng(seed)
    scale = _unit_scale(geom)
    a, b = (np.asarray(p, dtype=float) * scale for p in geom.z_line_1_points)
    c, d = (np.asarray(p, dtype=float) * scale for p in geom.m_line_points)
    e, f = (np.asarray(p, dtype=float) * scale for p in geom.z_line_2_points)

    a_n, b_n = _perturb_line(a, b, z_al, z_pe, n_draws, rng)
    c_n, d_n = _perturb_line(c, d, m_al, m_pe, n_draws, rng)
    e_n, f_n = _perturb_line(e, f, z_al, z_pe, n_draws, rng)

    z1 = np.linalg.norm(b_n - a_n, axis=1)
    z2 = np.linalg.norm(f_n - e_n, axis=1)
    m = np.linalg.norm(d_n - c_n, axis=1)
    s = np.linalg.norm((e_n + f_n) / 2.0 - (a_n + b_n) / 2.0, axis=1)
    asr = s / ((z1 + z2) / 2.0)
    cur = s / m
    labels = classify_shape_arrays(asr, cur, tolerance)
    counts = Counter(labels.tolist())
    freqs = {cls.value: counts.get(i, 0) / n_draws for i, cls in enumerate(_CLASS_ORDER)}
    ref = geom.shape_classification.value
    return ClassStability(
        reference_class=ref,
        stability=float(freqs[ref]),
        class_frequencies=freqs,
        sd_aspect_ratio=float(np.std(asr, ddof=1)) if n_draws > 1 else 0.0,
        sd_curviness=float(np.std(cur, ddof=1)) if n_draws > 1 else 0.0,
        sd_shape_index=float(np.std(cur / asr, ddof=1)) if n_draws > 1 else 0.0,
        n_draws=int(n_draws),
    )


@dataclass
class ClassificationMargin:
    """Distance of a sarcomere from the class boundaries in (AsR, SI).

    In the (AsR, SI) plane the classifier is axis-aligned: the
    rectangular band is ``|SI - 1| <= tolerance`` and the square box adds
    ``|AsR - 1| <= tolerance``.

    Attributes
    ----------
    shape_index_margin : float
        Distance of SI from the nearer of ``1 - tolerance`` and
        ``1 + tolerance``: the margin that decides barrel, rectangular
        or hourglass.
    aspect_ratio_margin : float
        Distance of AsR from the nearer of ``1 - tolerance`` and
        ``1 + tolerance``; it matters only inside the rectangular band
        (square vs rectangular).
    """

    shape_index_margin: float
    aspect_ratio_margin: float

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dictionary view."""
        return asdict(self)


def classification_margin(
    aspect_ratio: float, curviness: float, tolerance: float = DEFAULT_SHAPE_TOLERANCE
) -> ClassificationMargin:
    """Distances of a sarcomere from the shape-class boundaries.

    Dividing ``shape_index_margin`` by the uncertainty of SI from
    :func:`shape_uncertainty` gives a standardised distance whose
    relation with :func:`class_stability` does not depend on the
    population of sarcomeres.
    """
    for name, value in (("aspect_ratio", aspect_ratio), ("curviness", curviness)):
        if not (np.isfinite(value) and value > 0):
            raise ValueError(f"{name} must be a positive finite number, got {value}")
    si = curviness / aspect_ratio
    return ClassificationMargin(
        shape_index_margin=float(min(abs(si - (1 - tolerance)), abs(si - (1 + tolerance)))),
        aspect_ratio_margin=float(
            min(abs(aspect_ratio - (1 - tolerance)), abs(aspect_ratio - (1 + tolerance)))
        ),
    )


@dataclass
class AnnotationError:
    """Click error estimated from repeated annotations of one sarcomere.

    Attributes
    ----------
    sigma_z_along, sigma_z_perp : float
        Pooled standard deviations of the Z-line endpoints along and
        across their line.
    sigma_m_along, sigma_m_perp : float
        Same for the M-line endpoints.
    n_repeats : int
        Number of annotations used.
    unit : str
        Unit of the estimates.
    """

    sigma_z_along: float
    sigma_z_perp: float
    sigma_m_along: float
    sigma_m_perp: float
    n_repeats: int
    unit: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dictionary view."""
        return asdict(self)


def _landmarks(geom: SarcomereGeometry) -> np.ndarray:
    """The six landmarks A-F of an annotation, in the unit of the geometry."""
    pts = (*geom.z_line_1_points, *geom.m_line_points, *geom.z_line_2_points)
    return np.asarray(pts, dtype=float) * _unit_scale(geom)


def _as_landmarks(points: Any) -> np.ndarray:
    pts = np.array(points, dtype=float)
    if pts.shape != (6, 2):
        raise ValueError(f"Expected the six landmarks A-F as a (6, 2) array, got shape {pts.shape}")
    if not np.isfinite(pts[[0, 1, 4, 5]]).all():
        raise ValueError("The Z-line landmarks A, B, E and F must be finite")
    return pts


def _check_axis(axis: Any) -> np.ndarray:
    u = np.asarray(axis, dtype=float)
    if u.shape != (2,) or not np.isfinite(u).all() or not u.any():
        raise ValueError(f"axis must be a non-zero, finite 2-vector, got {axis!r}")
    return u


def _default_axis(pts: np.ndarray) -> np.ndarray:
    """Axis without a target: through the Z-line midpoints, pointing right (or down)."""
    u: np.ndarray = pts[4:6].mean(axis=0) - pts[0:2].mean(axis=0)
    if not u.any():
        raise ValueError("The two Z-lines have the same midpoint")
    major = 0 if abs(u[0]) >= abs(u[1]) else 1
    return -u if u[major] < 0 else u


def _canonical(pts: np.ndarray, u: np.ndarray) -> np.ndarray:
    out: np.ndarray = pts.copy()
    if (pts[4:6].mean(axis=0) - pts[0:2].mean(axis=0)) @ u < 0:
        out = out[[4, 5, 2, 3, 0, 1]]
    right = np.array([-u[1], u[0]])
    for i in (0, 2, 4):
        if out[i + 1] @ right < out[i] @ right:
            out[[i, i + 1]] = out[[i + 1, i]]
    return out


def canonicalize_sarcomere_landmarks(points: Any, axis: Any = None) -> np.ndarray:
    """Put the six landmarks of a sarcomere in canonical order.

    An annotator may click either Z-line first and either end of each
    line first, and a rotated or mirrored display changes which one looks
    "first". The canonical order depends only on the positions:

    - Z-line 1 (A, B) is the Z-line at the start of ``axis``, Z-line 2
      (E, F) the one at its end, and the M-line (C, D) stays in between;
    - on each line the first endpoint (A, C, E) is the one on the left of
      the axis when looking along it on screen (image y pointing down),
      the second (B, D, F) the one on its right.

    Parameters
    ----------
    points : array_like, shape (6, 2)
        Landmarks A-F as clicked: one Z-line, the M-line, the other
        Z-line, in original image coordinates (or any common scaling of
        them). The M-line rows may be NaN when the M-line was skipped.
    axis : array_like, shape (2,), optional
        Direction of the myofibril axis from the Z-line 1 side to the
        Z-line 2 side, e.g. the reference axis of a study target. Without
        it, the axis runs through the two Z-line midpoints and points
        right in the image, or down if the sarcomere is closer to vertical
        than to horizontal; Z-line 1 is then the left (or upper) one.

    Returns
    -------
    numpy.ndarray, shape (6, 2)
        The landmarks in canonical order A-F (a new array).

    Notes
    -----
    Canonicalisation only reorders points, so for coordinates mapped back
    to original pixels it gives the same result under any of the eight
    rotations and reflections of the display.
    """
    pts = _as_landmarks(points)
    u = _default_axis(pts) if axis is None else _check_axis(axis)
    return _canonical(pts, u)


def _offsets(pts: np.ndarray, ref: np.ndarray, u: np.ndarray) -> np.ndarray:
    out = np.full((6, 2), np.nan)
    for i in (0, 2, 4):
        along = ref[i + 1] - ref[i]
        length = float(np.hypot(*along))
        if np.isnan(length):
            continue
        if length == 0:
            raise ValueError("A line of the reference annotation has zero length")
        along /= length
        across = np.array([-along[1], along[0]])
        if across @ u < 0:
            across = -across
        d1, d2 = pts[i] - ref[i], pts[i + 1] - ref[i + 1]
        out[i] = (-(d1 @ along), d1 @ across)
        out[i + 1] = (d2 @ along, d2 @ across)
    return out


def landmark_offsets(points: Any, reference: Any, axis: Any = None) -> np.ndarray:
    """Offsets of each landmark from a reference annotation, along and across its line.

    Both annotations are first put in canonical order
    (:func:`canonicalize_sarcomere_landmarks`) with the same axis. Each
    offset is then split in the frame of the reference line:

    - along the line, positive outward from the line's midpoint, so that
      a landmark placed beyond the reference endpoint has a positive
      offset at either end of the line. An annotator who clicks short of
      both ends gets negative offsets at both, instead of offsets of
      opposite sign that would cancel in an average;
    - across the line, positive towards Z-line 2 (the direction of the
      axis).

    Parameters
    ----------
    points, reference : array_like, shape (6, 2)
        Landmarks A-F of the annotation and of the reference, in the same
        coordinates; M-line rows may be NaN.
    axis : array_like, shape (2,), optional
        Myofibril axis as in :func:`canonicalize_sarcomere_landmarks`;
        without it, the axis is derived from ``reference``.

    Returns
    -------
    numpy.ndarray, shape (6, 2)
        Column 0 is the along-line offset, column 1 the across-line
        offset of landmarks A-F in canonical order; NaN for a skipped
        M-line.
    """
    pts = _as_landmarks(points)
    ref = _as_landmarks(reference)
    u = _default_axis(ref) if axis is None else _check_axis(axis)
    return _offsets(_canonical(pts, u), _canonical(ref, u), u)


def estimate_annotation_sigma(
    repeats: list[SarcomereGeometry], axis: Any = None
) -> AnnotationError:
    """Estimate the click error from repeated annotations of one sarcomere.

    The landmarks of every repeat are put in canonical order with one
    common axis (:func:`canonicalize_sarcomere_landmarks`), so repeats may
    start from either Z-line and either end of each line. Each landmark
    is then compared with its mean position over the repeats; the
    residuals are split along and across the landmark's mean line
    (:func:`landmark_offsets`) and pooled over the four Z-line endpoints
    and over the two M-line endpoints (``n - 1`` degrees of freedom per
    landmark).

    Parameters
    ----------
    repeats : list of SarcomereGeometry
        At least two annotations of the same sarcomere (three or more
        recommended), in the same unit.
    axis : array_like, shape (2,), optional
        Direction of the myofibril axis in image coordinates. Without it
        the axis is taken from the first repeat, through its Z-line
        midpoints and pointing right in the image (or down for a
        sarcomere closer to vertical); the estimate does not depend on
        this choice.

    Returns
    -------
    AnnotationError
    """
    if len(repeats) < 2:
        raise ValueError("At least 2 repeated annotations are required")
    units = {g.unit for g in repeats}
    if len(units) != 1:
        raise ValueError(f"Repeated annotations use different units: {sorted(units)}")
    landmarks = [_landmarks(g) for g in repeats]
    u = _default_axis(landmarks[0]) if axis is None else _check_axis(axis)
    canon = np.array([_canonical(p, u) for p in landmarks])
    mean = canon.mean(axis=0)
    off = np.array([_offsets(p, mean, u) for p in canon])
    dof = len(repeats) - 1

    def _sd(rows: list[int], comp: int) -> float:
        return float(np.sqrt(np.sum(off[:, rows, comp] ** 2) / (len(rows) * dof)))

    z, m = [0, 1, 4, 5], [2, 3]
    return AnnotationError(
        sigma_z_along=_sd(z, 0),
        sigma_z_perp=_sd(z, 1),
        sigma_m_along=_sd(m, 0),
        sigma_m_perp=_sd(m, 1),
        n_repeats=len(repeats),
        unit=repeats[0].unit,
    )


__all__ = [
    "DEFAULT_N_DRAWS",
    "AnnotationError",
    "ClassStability",
    "ClassificationMargin",
    "ShapeUncertainty",
    "class_stability",
    "classification_margin",
    "classify_shape_arrays",
    "canonicalize_sarcomere_landmarks",
    "estimate_annotation_sigma",
    "landmark_offsets",
    "shape_uncertainty",
]
