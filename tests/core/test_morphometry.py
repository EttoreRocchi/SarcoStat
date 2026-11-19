"""
Unit tests for core.sarcomere_morphometry module.

Tests sarcomere geometry calculations against known analytical values
following Cisterna et al. (2021) definitions.
"""

import numpy as np
import pytest

from sarcostat.core.sarcomere_morphometry import (
    ShapeClassification,
    analyze_sarcomere_chain,
    calculate_alpha_angle,
    calculate_h_parameter,
    calculate_sarcomere_geometry,
    calculate_z_staircase_alignment,
    classify_sarcomere_shape,
    euclidean_distance,
    line_midpoint,
    perpendicular_distance_point_to_line,
    quick_sarcomere_analysis,
)


class TestBasicGeometry:
    """Tests for basic geometric functions."""

    def test_euclidean_distance_horizontal(self):
        """Horizontal distance calculation."""
        d = euclidean_distance((0, 0), (3, 0))
        assert abs(d - 3.0) < 1e-10

    def test_euclidean_distance_vertical(self):
        """Vertical distance calculation."""
        d = euclidean_distance((0, 0), (0, 4))
        assert abs(d - 4.0) < 1e-10

    def test_euclidean_distance_345_triangle(self):
        """Classic 3-4-5 triangle."""
        d = euclidean_distance((0, 0), (3, 4))
        assert abs(d - 5.0) < 1e-10

    def test_line_midpoint(self):
        """Midpoint calculation."""
        mid = line_midpoint((0, 0), (4, 6))
        assert abs(mid[0] - 2.0) < 1e-10
        assert abs(mid[1] - 3.0) < 1e-10


class TestPerpendicularDistance:
    """Tests for perpendicular distance calculation (H parameter)."""

    def test_point_on_horizontal_line(self):
        """Point above horizontal line."""
        d = perpendicular_distance_point_to_line((1, 2), (0, 0), (4, 0))
        assert abs(d - 2.0) < 1e-10

    def test_point_on_vertical_line(self):
        """Point beside vertical line."""
        d = perpendicular_distance_point_to_line((3, 1), (0, 0), (0, 4))
        assert abs(d - 3.0) < 1e-10

    def test_point_on_line(self):
        """Point exactly on the line should have distance 0."""
        d = perpendicular_distance_point_to_line((2, 2), (0, 0), (4, 4))
        assert d < 1e-10

    def test_degenerate_line(self):
        """Degenerate line (points coincide) returns distance to point."""
        d = perpendicular_distance_point_to_line((3, 4), (0, 0), (0, 0))
        assert abs(d - 5.0) < 1e-10


class TestAlphaAngle:
    """Tests for alpha angle between sarcomere axes."""

    def test_parallel_axes(self):
        """Parallel axes should have angle ~ 0."""
        axis1 = ((0, 0), (1, 0))
        axis2 = ((1, 0), (2, 0))
        alpha = calculate_alpha_angle(axis1, axis2)
        assert alpha < 0.01

    def test_perpendicular_axes(self):
        """Perpendicular axes should have angle = pi/2."""
        axis1 = ((0, 0), (1, 0))
        axis2 = ((1, 0), (1, 1))
        alpha = calculate_alpha_angle(axis1, axis2)
        assert abs(alpha - np.pi / 2) < 0.01

    def test_small_angle(self):
        """Small deviation angle."""
        axis1 = ((0, 0), (10, 0))  # Horizontal
        axis2 = ((10, 0), (20, 1))  # Slight upward
        alpha = calculate_alpha_angle(axis1, axis2)
        # Small angle, should be less than 10 degrees
        assert np.degrees(alpha) < 10

    def test_reversed_axis_is_not_a_half_turn(self):
        """Axes are undirected: annotating a sarcomere's Z-lines in reverse is still collinear."""
        axis1 = ((0, 0), (10, 0))
        reversed_axis2 = ((20, 1), (10, 0))
        forward = calculate_alpha_angle(axis1, ((10, 0), (20, 1)))
        assert calculate_alpha_angle(axis1, reversed_axis2) == pytest.approx(forward)
        assert calculate_alpha_angle(axis1, ((20, 0), (10, 0))) == pytest.approx(0.0)

    def test_alpha_range(self):
        rng = np.random.default_rng(1)
        for _ in range(50):
            a, b, c, d = rng.normal(size=(4, 2))
            alpha = calculate_alpha_angle((tuple(a), tuple(b)), (tuple(c), tuple(d)))
            assert 0.0 <= alpha <= np.pi / 2 + 1e-12


class TestHParameter:
    def test_independent_of_endpoint_order(self):
        z1 = ((0.0, 0.0), (0.0, 10.0))
        z2 = ((1.0, 12.0), (3.0, 22.0))
        z2_swapped = (z2[1], z2[0])
        assert calculate_h_parameter(z1, z2) == pytest.approx(calculate_h_parameter(z1, z2_swapped))
        assert calculate_h_parameter(z1, z2) == pytest.approx(2.0)  # midpoint x = 2

    def test_parallel_lines_give_their_distance(self):
        assert calculate_h_parameter(((0, 0), (0, 10)), ((5, 3), (5, 8))) == pytest.approx(5.0)


class TestShapeClassification:
    """Tests for sarcomere shape classification."""

    def test_square_classification(self):
        """AsR ~ 1 should classify as SQUARE."""
        shape = classify_sarcomere_shape(1.0, 1.0)
        assert shape == ShapeClassification.SQUARE

    def test_rectangular_classification(self):
        """Cur ~ AsR (but AsR > 1) should classify as RECTANGULAR."""
        shape = classify_sarcomere_shape(4.0, 4.0)
        assert shape == ShapeClassification.RECTANGULAR

    def test_barrel_classification(self):
        """Cur < AsR should classify as BARREL."""
        shape = classify_sarcomere_shape(4.0, 3.5)
        assert shape == ShapeClassification.BARREL

    def test_hourglass_classification(self):
        """Cur > AsR should classify as HOURGLASS."""
        shape = classify_sarcomere_shape(4.0, 4.5)
        assert shape == ShapeClassification.HOURGLASS

    def test_square_requires_cur_close_to_asr(self):
        """SQUARE requires AsR ~ 1 AND Cur ~ AsR (Cisterna et al. 2021).

        A sarcomere with AsR ~ 1 but a very different Cur is not SQUARE,
        it falls back to BARREL/HOURGLASS along the AsR/Cur ordering.
        """
        # AsR ~ 1 but Cur far from 1 -> should NOT be SQUARE
        assert classify_sarcomere_shape(1.0, 2.0) == ShapeClassification.HOURGLASS
        assert classify_sarcomere_shape(1.0, 0.5) == ShapeClassification.BARREL
        # Both AsR ~ 1 and Cur ~ 1 -> SQUARE (priority over RECTANGULAR)
        assert classify_sarcomere_shape(1.02, 1.01) == ShapeClassification.SQUARE

    def test_boundary_uses_non_strict_inequalities(self):
        """Just inside the rectangular wedge -> RECTANGULAR.

        The classifier uses non-strict ``<= epsilon`` for the
        rectangular wedge. We probe just inside the boundary because the
        exact ``Cur = AsR * (1 +/- epsilon)`` is unrepresentable in
        floating point and results vary with rounding.
        """
        tol = 0.05
        # Just inside the upper rectangular boundary:
        shape = classify_sarcomere_shape(4.0, 4.0 * (1.0 + tol) - 1e-9, tolerance=tol)
        assert shape == ShapeClassification.RECTANGULAR
        # Just inside the lower rectangular boundary:
        shape = classify_sarcomere_shape(4.0, 4.0 * (1.0 - tol) + 1e-9, tolerance=tol)
        assert shape == ShapeClassification.RECTANGULAR

    @pytest.mark.parametrize(
        ("asr", "cur", "expected"),
        [
            (1.0, 1.05, ShapeClassification.SQUARE),
            (1.05, 1.05, ShapeClassification.SQUARE),
            (0.95, 0.95, ShapeClassification.SQUARE),
            (4.0, 4.2, ShapeClassification.RECTANGULAR),
            (4.0, 3.8, ShapeClassification.RECTANGULAR),
            (4.0, 4.2 + 1e-6, ShapeClassification.HOURGLASS),
            (4.0, 3.8 - 1e-6, ShapeClassification.BARREL),
        ],
    )
    def test_exact_boundaries_are_inclusive(self, asr, cur, expected):
        """Values exactly on the class edges belong to the <= epsilon class."""
        assert classify_sarcomere_shape(asr, cur) == expected

    def test_integer_pixel_annotation_on_square_edge(self):
        """Z = 20 px, sarcomere length = 21 px, M = 20 px gives AsR = Cur = 1.05 -> SQUARE."""
        geom = calculate_sarcomere_geometry(
            point_a=(0, 0),
            point_b=(0, 20),
            point_c=(10.5, 0),
            point_d=(10.5, 20),
            point_e=(21, 0),
            point_f=(21, 20),
        )
        assert geom.shape_classification == ShapeClassification.SQUARE

    @pytest.mark.parametrize(("asr", "cur"), [(0.0, 1.0), (1.0, -1.0), (float("nan"), 1.0)])
    def test_invalid_ratios_rejected(self, asr, cur):
        with pytest.raises(ValueError):
            classify_sarcomere_shape(asr, cur)


class TestSarcomereGeometry:
    """Tests for full sarcomere geometry calculation."""

    def test_square_sarcomere(self):
        """Square sarcomere (equal sides)."""
        geom = calculate_sarcomere_geometry(
            point_a=(0, 0),
            point_b=(0, 100),  # Z-line 1: vertical, length 100
            point_c=(50, 0),
            point_d=(50, 100),  # M-line: vertical, length 100
            point_e=(100, 0),
            point_f=(100, 100),  # Z-line 2: vertical, length 100
        )

        # Z-lines should be 100
        assert abs(geom.z_line_1_length - 100) < 1e-6
        assert abs(geom.z_line_2_length - 100) < 1e-6

        # Sarcomere length (MN) should be 100 (horizontal distance between midpoints)
        assert abs(geom.sarcomere_length - 100) < 1e-6

        # AsR should be 1 for square
        assert abs(geom.aspect_ratio - 1.0) < 0.01

    def test_elongated_sarcomere(self):
        """Elongated sarcomere (length > width)."""
        geom = calculate_sarcomere_geometry(
            point_a=(0, 0),
            point_b=(0, 50),  # Z-line 1: length 50
            point_c=(100, 0),
            point_d=(100, 50),  # M-line: length 50
            point_e=(200, 0),
            point_f=(200, 50),  # Z-line 2: length 50
        )

        # Sarcomere length should be 200
        assert abs(geom.sarcomere_length - 200) < 1e-6

        # AsR should be 4 (200 / 50)
        assert abs(geom.aspect_ratio - 4.0) < 0.01

    def test_barrel_shape(self):
        """Barrel-shaped sarcomere (M-line > Z-lines)."""
        geom = calculate_sarcomere_geometry(
            point_a=(0, 10),
            point_b=(0, 90),  # Z-line 1: length 80
            point_c=(50, 0),
            point_d=(50, 100),  # M-line: length 100 (wider)
            point_e=(100, 10),
            point_f=(100, 90),  # Z-line 2: length 80
        )

        # Cur < AsR indicates barrel shape
        assert geom.curviness < geom.aspect_ratio
        assert geom.shape_classification == ShapeClassification.BARREL

    def test_pixel_to_unit_conversion(self):
        """Test unit conversion."""
        geom_px = calculate_sarcomere_geometry(
            point_a=(0, 0),
            point_b=(0, 100),
            point_c=(50, 0),
            point_d=(50, 100),
            point_e=(100, 0),
            point_f=(100, 100),
            pixel_to_unit=1.0,
            unit="pixels",
        )

        geom_um = calculate_sarcomere_geometry(
            point_a=(0, 0),
            point_b=(0, 100),
            point_c=(50, 0),
            point_d=(50, 100),
            point_e=(100, 0),
            point_f=(100, 100),
            pixel_to_unit=0.065,
            unit="um",
        )

        # Lengths should scale, but ratios should stay the same
        assert abs(geom_um.z_line_1_length - geom_px.z_line_1_length * 0.065) < 1e-6
        assert abs(geom_um.aspect_ratio - geom_px.aspect_ratio) < 1e-6

    def test_zero_length_z_line_raises(self):
        """Zero-length Z-line should raise ValueError."""
        with pytest.raises(ValueError):
            calculate_sarcomere_geometry(
                point_a=(0, 0),
                point_b=(0, 0),  # Same point!
                point_c=(50, 0),
                point_d=(50, 100),
                point_e=(100, 0),
                point_f=(100, 100),
            )

    _SQUARE = {
        "point_a": (0, 0),
        "point_b": (0, 100),
        "point_c": (50, 0),
        "point_d": (50, 100),
        "point_e": (100, 0),
        "point_f": (100, 100),
    }

    def test_tiny_conversion_factor_is_valid(self):
        """Degeneracy is checked in pixels, so scaling px -> metres stays valid."""
        geom = calculate_sarcomere_geometry(**self._SQUARE, pixel_to_unit=1e-12, unit="m")
        assert geom.z_line_1_length == pytest.approx(1e-10)
        assert geom.shape_classification == ShapeClassification.SQUARE

    @pytest.mark.parametrize("factor", [0.0, -1.0, float("nan")])
    def test_invalid_conversion_factor_rejected(self, factor):
        with pytest.raises(ValueError, match="pixel_to_unit"):
            calculate_sarcomere_geometry(**self._SQUARE, pixel_to_unit=factor)

    def test_non_finite_point_rejected(self):
        with pytest.raises(ValueError, match="finite"):
            calculate_sarcomere_geometry(**{**self._SQUARE, "point_c": (float("nan"), 0)})


class TestSarcomereChain:
    def test_reversed_annotation_order_does_not_inflate_alpha(self):
        """Two collinear sarcomeres, the second annotated F..A instead of A..F."""
        first = calculate_sarcomere_geometry(
            (0, 0), (0, 10), (5, 0), (5, 10), (10, 0), (10, 10), sarcomere_id=0
        )
        second_reversed = calculate_sarcomere_geometry(
            (20, 0), (20, 10), (15, 0), (15, 10), (10, 0), (10, 10), sarcomere_id=1
        )
        chain = analyze_sarcomere_chain([first, second_reversed])
        assert chain.alpha_angles == [pytest.approx(0.0)]
        assert chain.alpha_mean == pytest.approx(0.0)

    def test_single_sarcomere_has_no_alpha(self):
        geom = calculate_sarcomere_geometry((0, 0), (0, 10), (5, 0), (5, 10), (10, 0), (10, 10))
        chain = analyze_sarcomere_chain([geom])
        assert chain.alpha_angles == []
        assert (chain.alpha_mean, chain.alpha_std) == (0.0, 0.0)


class TestZLineAlignment:
    """Tests for Z-line alignment (H parameter) calculation."""

    def test_perfectly_aligned(self):
        """Perfectly aligned Z-lines should have H ~ 0.

        H measures perpendicular distance from an endpoint of one Z-line
        to the line through the previous Z-line. For H~0, endpoints must
        lie on the same line (collinear Z-lines).
        """
        # Horizontal collinear Z-lines - endpoints lie on same line y=0
        z_lines = [
            ((0, 0), (10, 0)),
            ((5, 0), (15, 0)),  # Point (5,0) lies on line through first Z-line
            ((10, 0), (20, 0)),  # Point (10,0) lies on line through second Z-line
        ]
        alignment = calculate_z_staircase_alignment(z_lines)
        assert alignment.h_mean < 0.1

    def test_misaligned_z_lines(self):
        """Misaligned Z-lines should have H > 0."""
        z_lines = [
            ((0, 0), (0, 10)),
            ((5, 5), (5, 15)),  # Shifted vertically
            ((10, 0), (10, 10)),
        ]
        alignment = calculate_z_staircase_alignment(z_lines)
        assert alignment.h_mean > 1.0

    def test_minimum_z_lines(self):
        """Need at least 2 Z-lines."""
        with pytest.raises(ValueError):
            calculate_z_staircase_alignment([((0, 0), (0, 10))])


class TestQuickAnalysis:
    """Tests for quick sarcomere analysis function."""

    def test_quick_analysis(self):
        """Test quick analysis from pre-measured lengths."""
        result = quick_sarcomere_analysis(
            z1_length=100, z2_length=100, m_length=100, sarcomere_length=100
        )

        assert abs(result["aspect_ratio"] - 1.0) < 0.01
        assert abs(result["curviness"] - 1.0) < 0.01
        assert abs(result["shape_index"] - 1.0) < 0.01

    def test_quick_analysis_literature_values(self):
        """Test with values from Cisterna et al. 2021 euploid tissue."""
        # Mean values reported by Cisterna et al. 2021: Z=414nm, M=469nm, sarc=1638nm
        result = quick_sarcomere_analysis(
            z1_length=414, z2_length=414, m_length=469, sarcomere_length=1638
        )

        # Expected AsR ~ 4.0, Cur ~ 3.5
        assert 3.5 < result["aspect_ratio"] < 4.5
        assert 3.0 < result["curviness"] < 4.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
