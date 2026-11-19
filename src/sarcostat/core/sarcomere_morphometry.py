"""Single-sarcomere morphometry following Cisterna et al. (2021).

The module exposes pure-numerical primitives for analysing single
sarcomere shape from the standard 6-point annotation (A-B for the first
Z-line, C-D for the M-line, E-F for the second Z-line). It provides:

- the 6-point geometry routine :func:`calculate_sarcomere_geometry`,
- :func:`classify_sarcomere_shape` with a configurable tolerance,
- the alpha-angle and H-parameter helpers used by chain-level analyses,
- chain-level aggregation in :func:`analyze_sarcomere_chain`.

The module has no I/O or plotting dependencies. JSON persistence is
provided by :mod:`sarcostat.core._serialization`.

References
----------
Cisterna B., Malatesta M., Zancanaro C., Boschi F. (2021). A
computational approach to quantitatively define sarcomere dimensions and
arrangement in skeletal muscle. Computer Methods and Programs in
Biomedicine, 211, 106437. https://doi.org/10.1016/j.cmpb.2021.106437
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import numpy as np

Point = tuple[float, float]
LineSegment = tuple[Point, Point]

DEFAULT_SHAPE_TOLERANCE = 0.05

# Absolute slack on the dimensionless classifier comparisons, so that
# ratios that equal the tolerance in exact arithmetic (e.g. AsR = 21/20 =
# 1.05 from integer-pixel annotations) land inside the "<= tolerance"
# class despite floating-point rounding.
_BOUNDARY_SLACK = 1e-9
_DEGENERATE_LENGTH_PX = 1e-10


class ShapeClassification(Enum):
    """Sarcomere shape classes based on the AsR/Cur relationship.

    Members
    -------
    RECTANGULAR
        Cur is approximately equal to AsR (cylindrical in 3D).
    BARREL
        Cur < AsR (convex lateral sides).
    HOURGLASS
        Cur > AsR (concave lateral sides).
    SQUARE
        AsR is approximately equal to 1.
    """

    RECTANGULAR = "rectangular"
    BARREL = "barrel"
    HOURGLASS = "hourglass"
    SQUARE = "square"


def euclidean_distance(p1: Point, p2: Point) -> float:
    """Euclidean distance between two 2D points.

    Parameters
    ----------
    p1, p2 : Point
        Points as ``(x, y)`` tuples.

    Returns
    -------
    float
        Euclidean distance.

    Examples
    --------
    >>> euclidean_distance((0, 0), (3, 4))
    5.0
    """
    return float(np.hypot(p2[0] - p1[0], p2[1] - p1[1]))


def line_midpoint(p1: Point, p2: Point) -> Point:
    """Midpoint of the segment between two 2D points.

    Examples
    --------
    >>> line_midpoint((0, 0), (4, 6))
    (2.0, 3.0)
    """
    return ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)


def perpendicular_distance_point_to_line(point: Point, line_start: Point, line_end: Point) -> float:
    """Perpendicular distance from ``point`` to the infinite line ``line_start-line_end``.

    Implements the H parameter from Cisterna et al. (2021), Section 2.6.
    Falls back to a point-to-point distance if the supplied line is
    degenerate (length below 1e-10).

    Parameters
    ----------
    point : Point
        Reference point as ``(x, y)``.
    line_start, line_end : Point
        Two points defining the line.

    Returns
    -------
    float
        Perpendicular distance.

    Examples
    --------
    >>> perpendicular_distance_point_to_line((1, 1), (0, 0), (2, 0))
    1.0
    """
    x0, y0 = point
    x1, y1 = line_start
    x2, y2 = line_end
    line_length = euclidean_distance(line_start, line_end)
    if line_length < 1e-10:
        return euclidean_distance(point, line_start)
    numerator = abs((x2 - x1) * (y1 - y0) - (x1 - x0) * (y2 - y1))
    return float(numerator / line_length)


def angle_between_vectors(v1: tuple[float, float], v2: tuple[float, float]) -> float:
    """Angle in [0, pi] between two 2D vectors.

    Parameters
    ----------
    v1, v2 : tuple of float
        Vectors as ``(dx, dy)``.

    Returns
    -------
    float
        Angle in radians; 0 for any vector with norm below 1e-10.

    Examples
    --------
    >>> import numpy as np
    >>> angle_between_vectors((1, 0), (0, 1))
    1.5707963267948966
    """
    norm1 = np.hypot(v1[0], v1[1])
    norm2 = np.hypot(v2[0], v2[1])
    if norm1 < 1e-10 or norm2 < 1e-10:
        return 0.0
    dot = (v1[0] * v2[0] + v1[1] * v2[1]) / (norm1 * norm2)
    dot = float(np.clip(dot, -1.0, 1.0))
    return float(np.arccos(dot))


def calculate_alpha_angle(axis1: LineSegment, axis2: LineSegment) -> float:
    """Alpha angle between consecutive sarcomere axes.

    The alpha angle measures deviation from a straight myofibril line
    (Cisterna et al. 2021, Section 2.5). The absolute value is used so
    that opposing deviations do not cancel in the chain mean.

    Sarcomere axes are undirected lines: which Z-line the user annotated
    first only flips the vector, so the angle is folded onto
    ``[0, pi/2]`` (an axis entered in reverse order gives 0, not pi).

    Parameters
    ----------
    axis1, axis2 : LineSegment
        Sarcomere axes as ``((x1, y1), (x2, y2))``.

    Returns
    -------
    float
        Angle in radians, in [0, pi/2].

    Examples
    --------
    >>> axis1 = ((0, 0), (1, 0))
    >>> axis2 = ((1, 0), (2, 0.1))
    >>> alpha = calculate_alpha_angle(axis1, axis2)
    >>> np.degrees(alpha) < 10
    True
    """
    v1 = (axis1[1][0] - axis1[0][0], axis1[1][1] - axis1[0][1])
    v2 = (axis2[1][0] - axis2[0][0], axis2[1][1] - axis2[0][1])
    angle = angle_between_vectors(v1, v2)
    return float(min(angle, np.pi - angle))


@dataclass
class SarcomereGeometry:
    """Full geometric and morphometric record for a single sarcomere.

    Attributes
    ----------
    sarcomere_id : int
        Zero-based identifier within a chain.
    z_line_1_points, z_line_2_points : tuple of Point
        Points defining the two Z-lines (A-B and E-F).
    m_line_points : tuple of Point
        Points defining the M-line (C-D).
    z_line_1_length, z_line_2_length, m_line_length : float
        Line lengths in physical units.
    sarcomere_length : float
        Distance between the Z-line midpoints (M-N).
    aspect_ratio : float
        AsR = sarcomere_length / mean(Z-line lengths).
    curviness : float
        Cur = sarcomere_length / m_line_length.
    shape_index : float
        Cur / AsR ratio.
    shape_classification : ShapeClassification
        Discrete shape class.
    midpoint_z1, midpoint_z2 : Point
        Midpoints M and N of the two Z-lines.
    unit : str, optional
        Physical unit (default ``"pixels"``).

    References
    ----------
    Cisterna et al. (2021), Section 2.4.
    """

    sarcomere_id: int
    z_line_1_points: tuple[Point, Point]
    z_line_2_points: tuple[Point, Point]
    m_line_points: tuple[Point, Point]
    z_line_1_length: float
    z_line_2_length: float
    m_line_length: float
    sarcomere_length: float
    aspect_ratio: float
    curviness: float
    shape_index: float
    shape_classification: ShapeClassification
    midpoint_z1: Point
    midpoint_z2: Point
    unit: str = "pixels"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dict view, with the enum reduced to its value."""
        data = asdict(self)
        data["shape_classification"] = self.shape_classification.value
        return data

    def summary_string(self) -> str:
        """Return a human-readable plain-text summary."""
        mean_z = (self.z_line_1_length + self.z_line_2_length) / 2.0
        return (
            f"Sarcomere morphometry (id {self.sarcomere_id})\n"
            f"Z-line 1 (AB): {self.z_line_1_length:.2f} {self.unit}\n"
            f"Z-line 2 (EF): {self.z_line_2_length:.2f} {self.unit}\n"
            f"Mean Z-line:   {mean_z:.2f} {self.unit}\n"
            f"M-line (CD):   {self.m_line_length:.2f} {self.unit}\n"
            f"Sarcomere length (MN): {self.sarcomere_length:.2f} {self.unit}\n"
            f"AsR: {self.aspect_ratio:.3f}\n"
            f"Cur: {self.curviness:.3f}\n"
            f"Shape index (Cur/AsR): {self.shape_index:.3f}\n"
            f"Classification: {self.shape_classification.value.upper()}"
        )


def classify_sarcomere_shape(
    aspect_ratio: float,
    curviness: float,
    tolerance: float = DEFAULT_SHAPE_TOLERANCE,
) -> ShapeClassification:
    """Classify a sarcomere shape from AsR and Cur.

    Parameters
    ----------
    aspect_ratio : float
        AsR = sarcomere_length / mean(Z-line lengths).
    curviness : float
        Cur = sarcomere_length / m_line_length.
    tolerance : float, optional
        Relative tolerance for ``SQUARE`` (``|AsR - 1| <= tolerance``)
        and ``RECTANGULAR`` (``|Cur - AsR| / AsR <= tolerance``)
        decisions. Defaults to :data:`DEFAULT_SHAPE_TOLERANCE`.

    Returns
    -------
    ShapeClassification
        One of SQUARE, RECTANGULAR, BARREL, HOURGLASS.

    Raises
    ------
    ValueError
        If ``aspect_ratio`` or ``curviness`` is not a positive finite
        number, or ``tolerance`` is negative.

    Notes
    -----
    AsR ~ 1 AND Cur ~ AsR -> SQUARE (priority over RECTANGULAR).
    Cur ~ AsR             -> RECTANGULAR (cylindrical in 3D).
    Cur < AsR             -> BARREL (convex lateral sides).
    Cur > AsR             -> HOURGLASS (concave lateral sides).

    The classes follow Cisterna et al. (2021); comparisons use
    non-strict inequalities (``<=``), so a sample at exactly the
    tolerance edge is included in the closer (less restrictive) class.
    A slack of :data:`_BOUNDARY_SLACK` absorbs floating-point rounding
    at the edge.

    Examples
    --------
    >>> classify_sarcomere_shape(4.0, 4.0)
    <ShapeClassification.RECTANGULAR: 'rectangular'>
    >>> classify_sarcomere_shape(4.0, 3.5)
    <ShapeClassification.BARREL: 'barrel'>
    >>> classify_sarcomere_shape(1.05, 1.05)
    <ShapeClassification.SQUARE: 'square'>
    """
    for name, value in (("aspect_ratio", aspect_ratio), ("curviness", curviness)):
        if not (np.isfinite(value) and value > 0):
            raise ValueError(f"{name} must be a positive finite number, got {value}")
    if tolerance < 0:
        raise ValueError(f"tolerance must be non-negative, got {tolerance}")
    edge = tolerance + _BOUNDARY_SLACK
    relative_diff = (curviness - aspect_ratio) / aspect_ratio
    near_diagonal = abs(relative_diff) <= edge
    if abs(aspect_ratio - 1.0) <= edge and near_diagonal:
        return ShapeClassification.SQUARE
    if near_diagonal:
        return ShapeClassification.RECTANGULAR
    if curviness < aspect_ratio:
        return ShapeClassification.BARREL
    return ShapeClassification.HOURGLASS


def calculate_sarcomere_geometry(
    point_a: Point,
    point_b: Point,
    point_c: Point,
    point_d: Point,
    point_e: Point,
    point_f: Point,
    sarcomere_id: int = 0,
    pixel_to_unit: float = 1.0,
    unit: str = "pixels",
) -> SarcomereGeometry:
    """Compute the full sarcomere geometry from the 6 reference points.

    Parameters
    ----------
    point_a, point_b : Point
        Endpoints of Z-line 1.
    point_c, point_d : Point
        Endpoints of the M-line.
    point_e, point_f : Point
        Endpoints of Z-line 2.
    sarcomere_id : int, optional
        Identifier within a chain (default 0).
    pixel_to_unit : float, optional
        Conversion from pixels to physical units (default 1.0).
    unit : str, optional
        Unit label (default ``"pixels"``).

    Returns
    -------
    SarcomereGeometry
        Geometry record including AsR, Cur, shape classification.

    Raises
    ------
    ValueError
        If any point is non-finite, ``pixel_to_unit`` is not positive,
        or any of the three lines or the sarcomere length collapse to
        zero (checked in pixel units, so that a valid annotation stays
        valid under any unit conversion).

    References
    ----------
    Cisterna et al. (2021), Sections 2.3 and 2.4.

    Examples
    --------
    >>> geom = calculate_sarcomere_geometry(
    ...     point_a=(0, 0), point_b=(0, 100),
    ...     point_c=(50, 0), point_d=(50, 100),
    ...     point_e=(100, 0), point_f=(100, 100),
    ... )
    >>> abs(geom.aspect_ratio - 1.0) < 0.01
    True
    """
    if not (np.isfinite(pixel_to_unit) and pixel_to_unit > 0):
        raise ValueError(f"pixel_to_unit must be a positive finite number, got {pixel_to_unit}")
    all_points = (point_a, point_b, point_c, point_d, point_e, point_f)
    if not np.all(np.isfinite(np.asarray(all_points, dtype=float))):
        raise ValueError("Annotation points must be finite; got NaN or inf")

    z_line_1_px = euclidean_distance(point_a, point_b)
    z_line_2_px = euclidean_distance(point_e, point_f)
    m_line_px = euclidean_distance(point_c, point_d)
    if z_line_1_px < _DEGENERATE_LENGTH_PX:
        raise ValueError("Z-line 1 has zero length (points A and B coincide)")
    if z_line_2_px < _DEGENERATE_LENGTH_PX:
        raise ValueError("Z-line 2 has zero length (points E and F coincide)")
    if m_line_px < _DEGENERATE_LENGTH_PX:
        raise ValueError("M-line has zero length (points C and D coincide)")

    midpoint_m = line_midpoint(point_a, point_b)
    midpoint_n = line_midpoint(point_e, point_f)
    sarcomere_px = euclidean_distance(midpoint_m, midpoint_n)
    if sarcomere_px < _DEGENERATE_LENGTH_PX:
        raise ValueError("Sarcomere length is zero (midpoints M and N coincide)")

    z_line_1_length = z_line_1_px * pixel_to_unit
    z_line_2_length = z_line_2_px * pixel_to_unit
    m_line_length = m_line_px * pixel_to_unit
    sarcomere_length = sarcomere_px * pixel_to_unit

    mean_z_line_length = (z_line_1_length + z_line_2_length) / 2.0
    aspect_ratio = sarcomere_length / mean_z_line_length
    curviness = sarcomere_length / m_line_length
    shape_index = curviness / aspect_ratio
    shape_class = classify_sarcomere_shape(aspect_ratio, curviness)

    return SarcomereGeometry(
        sarcomere_id=sarcomere_id,
        z_line_1_points=(point_a, point_b),
        z_line_2_points=(point_e, point_f),
        m_line_points=(point_c, point_d),
        z_line_1_length=z_line_1_length,
        z_line_2_length=z_line_2_length,
        m_line_length=m_line_length,
        sarcomere_length=sarcomere_length,
        aspect_ratio=aspect_ratio,
        curviness=curviness,
        shape_index=shape_index,
        shape_classification=shape_class,
        midpoint_z1=midpoint_m,
        midpoint_z2=midpoint_n,
        unit=unit,
    )


@dataclass
class ZLineAlignment:
    """Z-line alignment statistics for a Z-staircase.

    Attributes
    ----------
    h_values : list of float
        Per-pair H values along the staircase.
    h_mean : float
        Mean H (``Hmean`` in the reference).
    h_std : float
        Standard deviation of H values.
    n_measurements : int
        Number of pairs contributing to the staircase.
    unit : str, optional
        Physical unit (default ``"pixels"``).

    References
    ----------
    Cisterna et al. (2021), Section 2.6.
    """

    h_values: list[float]
    h_mean: float
    h_std: float
    n_measurements: int
    unit: str = "pixels"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dictionary view."""
        return asdict(self)

    def summary_string(self) -> str:
        """Return a human-readable plain-text summary."""
        return (
            f"Z-line alignment (H parameter)\n"
            f"Number of measurements: {self.n_measurements}\n"
            f"Mean H (Hmean): {self.h_mean:.2f} {self.unit}\n"
            f"Std H: {self.h_std:.2f} {self.unit}"
        )


def calculate_h_parameter(
    z_line_1: LineSegment, z_line_2: LineSegment, pixel_to_unit: float = 1.0
) -> float:
    """H parameter between two adjacent Z-lines.

    Parameters
    ----------
    z_line_1, z_line_2 : LineSegment
        Z-lines as ``((x1, y1), (x2, y2))``.
    pixel_to_unit : float, optional
        Conversion factor (default 1.0).

    Returns
    -------
    float
        Perpendicular distance from the midpoint of ``z_line_2`` to the
        line through ``z_line_1``, in physical units. Using the midpoint
        makes H independent of the order in which the endpoints of
        ``z_line_2`` were annotated; for parallel Z-lines it equals the
        distance from either endpoint.

    References
    ----------
    Cisterna et al. (2021), Section 2.6.
    """
    point = line_midpoint(z_line_2[0], z_line_2[1])
    return perpendicular_distance_point_to_line(point, z_line_1[0], z_line_1[1]) * pixel_to_unit


def calculate_z_staircase_alignment(
    z_lines: list[LineSegment], pixel_to_unit: float = 1.0, unit: str = "pixels"
) -> ZLineAlignment:
    """Aggregate H values along a sequence of Z-lines (Z-staircase).

    Parameters
    ----------
    z_lines : list of LineSegment
        Ordered Z-lines from adjacent myofibrils.
    pixel_to_unit : float, optional
        Conversion factor (default 1.0).
    unit : str, optional
        Unit label (default ``"pixels"``).

    Returns
    -------
    ZLineAlignment
        Mean and standard deviation of H values.

    Raises
    ------
    ValueError
        If fewer than two Z-lines are supplied.

    References
    ----------
    Cisterna et al. (2021), Section 2.6.

    Examples
    --------
    >>> z_lines = [((0, 0), (0, 10)), ((5, 0), (5, 10)), ((10, 0), (10, 10))]
    >>> alignment = calculate_z_staircase_alignment(z_lines)
    >>> alignment.h_mean < 0.01
    True
    """
    if len(z_lines) < 2:
        raise ValueError("At least 2 Z-lines required for alignment calculation")

    h_values = [
        calculate_h_parameter(z_lines[i], z_lines[i + 1], pixel_to_unit)
        for i in range(len(z_lines) - 1)
    ]
    h_array = np.array(h_values)
    return ZLineAlignment(
        h_values=h_values,
        h_mean=float(np.mean(h_array)),
        h_std=float(np.std(h_array, ddof=1)) if len(h_values) > 1 else 0.0,
        n_measurements=len(h_values),
        unit=unit,
    )


@dataclass
class SarcomereChainMetrics:
    """Aggregate metrics for a chain of sarcomeres.

    Attributes
    ----------
    n_sarcomeres : int
        Number of sarcomeres in the chain.
    sarcomeres : list of SarcomereGeometry
        Per-sarcomere geometry records.
    alpha_angles : list of float
        Inter-sarcomere alpha angles (length ``n_sarcomeres - 1``).
    alpha_mean, alpha_std : float
        Summary of the absolute alpha angles, in radians.
    mean_aspect_ratio, std_aspect_ratio : float
        Summary of AsR across the chain.
    mean_curviness, std_curviness : float
        Summary of Cur across the chain.
    mean_shape_index, std_shape_index : float
        Summary of the Cur/AsR ratio.
    dominant_shape : ShapeClassification
        Most frequent shape class.
    timestamp : str
        ISO-8601 timestamp at which the analysis ran.
    unit : str, optional
        Physical unit (default ``"pixels"``).

    References
    ----------
    Cisterna et al. (2021), Section 2.5.
    """

    n_sarcomeres: int
    sarcomeres: list[SarcomereGeometry]
    alpha_angles: list[float]
    alpha_mean: float
    alpha_std: float
    mean_aspect_ratio: float
    std_aspect_ratio: float
    mean_curviness: float
    std_curviness: float
    mean_shape_index: float
    std_shape_index: float
    dominant_shape: ShapeClassification
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    unit: str = "pixels"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dictionary view of the chain metrics."""
        return {
            "n_sarcomeres": self.n_sarcomeres,
            "sarcomeres": [s.to_dict() for s in self.sarcomeres],
            "alpha_angles": self.alpha_angles,
            "alpha_angles_degrees": [float(np.degrees(a)) for a in self.alpha_angles],
            "alpha_mean": self.alpha_mean,
            "alpha_mean_degrees": float(np.degrees(self.alpha_mean)),
            "alpha_std": self.alpha_std,
            "alpha_std_degrees": float(np.degrees(self.alpha_std)),
            "mean_aspect_ratio": self.mean_aspect_ratio,
            "std_aspect_ratio": self.std_aspect_ratio,
            "mean_curviness": self.mean_curviness,
            "std_curviness": self.std_curviness,
            "mean_shape_index": self.mean_shape_index,
            "std_shape_index": self.std_shape_index,
            "dominant_shape": self.dominant_shape.value,
            "timestamp": self.timestamp,
            "unit": self.unit,
        }

    def summary_string(self) -> str:
        """Return a human-readable plain-text summary."""
        return (
            f"Sarcomere chain analysis\n"
            f"Timestamp: {self.timestamp}\n"
            f"Number of sarcomeres: {self.n_sarcomeres}\n"
            f"Aspect ratio (AsR): {self.mean_aspect_ratio:.3f} +/- "
            f"{self.std_aspect_ratio:.3f}\n"
            f"Curviness (Cur):    {self.mean_curviness:.3f} +/- "
            f"{self.std_curviness:.3f}\n"
            f"Shape index:        {self.mean_shape_index:.3f} +/- "
            f"{self.std_shape_index:.3f}\n"
            f"Dominant shape:     {self.dominant_shape.value.upper()}\n"
            f"Mean |alpha|: {np.degrees(self.alpha_mean):.2f} deg +/- "
            f"{np.degrees(self.alpha_std):.2f} deg\n"
            f"Number of alpha measurements: {len(self.alpha_angles)}"
        )


def _alpha_angles_along_chain(sarcomeres: list[SarcomereGeometry]) -> list[float]:
    """Return alpha angles between consecutive sarcomere axes."""
    return [
        calculate_alpha_angle(
            (sarcomeres[i].midpoint_z1, sarcomeres[i].midpoint_z2),
            (sarcomeres[i + 1].midpoint_z1, sarcomeres[i + 1].midpoint_z2),
        )
        for i in range(len(sarcomeres) - 1)
    ]


def _summary_mean_std(values: np.ndarray) -> tuple[float, float]:
    """Mean and (sample) standard deviation; std is 0 when n < 2."""
    if values.size == 0:
        return 0.0, 0.0
    mean = float(np.mean(values))
    std = float(np.std(values, ddof=1)) if values.size > 1 else 0.0
    return mean, std


def analyze_sarcomere_chain(sarcomeres: list[SarcomereGeometry]) -> SarcomereChainMetrics:
    """Aggregate per-sarcomere records into chain-level metrics.

    Parameters
    ----------
    sarcomeres : list of SarcomereGeometry
        Per-sarcomere records ordered along the myofibril.

    Returns
    -------
    SarcomereChainMetrics
        Chain summary including alpha-angle and morphometry statistics.

    Raises
    ------
    ValueError
        If ``sarcomeres`` is empty.
    """
    if len(sarcomeres) < 1:
        raise ValueError("At least 1 sarcomere required")

    n = len(sarcomeres)
    unit = sarcomeres[0].unit

    alpha_angles = _alpha_angles_along_chain(sarcomeres)
    asr_mean, asr_std = _summary_mean_std(np.array([s.aspect_ratio for s in sarcomeres]))
    cur_mean, cur_std = _summary_mean_std(np.array([s.curviness for s in sarcomeres]))
    si_mean, si_std = _summary_mean_std(np.array([s.shape_index for s in sarcomeres]))
    alpha_mean, alpha_std = _summary_mean_std(np.asarray(alpha_angles))

    shape_counts = Counter(s.shape_classification for s in sarcomeres)
    dominant_shape = shape_counts.most_common(1)[0][0]

    return SarcomereChainMetrics(
        n_sarcomeres=n,
        sarcomeres=sarcomeres,
        alpha_angles=alpha_angles,
        alpha_mean=alpha_mean,
        alpha_std=alpha_std,
        mean_aspect_ratio=asr_mean,
        std_aspect_ratio=asr_std,
        mean_curviness=cur_mean,
        std_curviness=cur_std,
        mean_shape_index=si_mean,
        std_shape_index=si_std,
        dominant_shape=dominant_shape,
        unit=unit,
    )


def quick_sarcomere_analysis(
    z1_length: float, z2_length: float, m_length: float, sarcomere_length: float
) -> dict[str, Any]:
    """Compute AsR, Cur, shape index from pre-measured line lengths.

    Parameters
    ----------
    z1_length, z2_length : float
        Lengths of the two Z-lines.
    m_length : float
        Length of the M-line.
    sarcomere_length : float
        Distance between Z-line midpoints.

    Returns
    -------
    dict
        Keys: ``aspect_ratio``, ``curviness``, ``shape_index``,
        ``shape_classification`` (lower-case string), ``mean_z_line_length``.

    Examples
    --------
    >>> result = quick_sarcomere_analysis(
    ...     z1_length=414, z2_length=414,
    ...     m_length=469, sarcomere_length=1638,
    ... )
    >>> 3.9 < result['aspect_ratio'] < 4.0
    True
    """
    mean_z = (z1_length + z2_length) / 2.0
    aspect_ratio = sarcomere_length / mean_z
    curviness = sarcomere_length / m_length
    shape_index = curviness / aspect_ratio
    classification = classify_sarcomere_shape(aspect_ratio, curviness)
    return {
        "aspect_ratio": aspect_ratio,
        "curviness": curviness,
        "shape_index": shape_index,
        "shape_classification": classification.value,
        "mean_z_line_length": mean_z,
    }


__all__ = [
    "DEFAULT_SHAPE_TOLERANCE",
    "Point",
    "LineSegment",
    "ShapeClassification",
    "euclidean_distance",
    "line_midpoint",
    "perpendicular_distance_point_to_line",
    "angle_between_vectors",
    "calculate_alpha_angle",
    "SarcomereGeometry",
    "classify_sarcomere_shape",
    "calculate_sarcomere_geometry",
    "ZLineAlignment",
    "calculate_h_parameter",
    "calculate_z_staircase_alignment",
    "SarcomereChainMetrics",
    "analyze_sarcomere_chain",
    "quick_sarcomere_analysis",
]
