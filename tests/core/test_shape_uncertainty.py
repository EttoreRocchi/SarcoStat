"""
Unit tests for core.shape_uncertainty module.

Uses a sarcomere with the euploid mouse dimensions of Cisterna et al.
(2021): Z-lines 414 nm, M-line 469 nm, sarcomere length 1638 nm.
"""

import numpy as np
import pytest

from sarcostat.core.sarcomere_morphometry import (
    ShapeClassification,
    calculate_sarcomere_geometry,
    classify_sarcomere_shape,
)
from sarcostat.core.shape_uncertainty import (
    canonicalize_sarcomere_landmarks,
    class_stability,
    classification_margin,
    classify_shape_arrays,
    estimate_annotation_sigma,
    landmark_offsets,
    shape_uncertainty,
)

LZ, LM, LS = 414.0, 469.0, 1638.0


def _points(lz=LZ, lm=LM, ls=LS, scale=1.0):
    pts = [
        (0.0, -lz / 2),
        (0.0, lz / 2),
        (ls / 2, -lm / 2),
        (ls / 2, lm / 2),
        (ls, -lz / 2),
        (ls, lz / 2),
    ]
    return [(x * scale, y * scale) for x, y in pts]


def _geom(lz=LZ, lm=LM, ls=LS, pixel_to_unit=1.0):
    pts = _points(lz, lm, ls, scale=1.0 / pixel_to_unit)
    return calculate_sarcomere_geometry(*pts, pixel_to_unit=pixel_to_unit, unit="nm")


class TestShapeUncertainty:
    """First-order propagation."""

    def test_shape_index_relative_uncertainty(self):
        u = shape_uncertainty(_geom(), sigma_z=1.0)
        expected = np.sqrt(1 / LZ**2 + 2 / LM**2)
        assert u.u_shape_index / u.shape_index == pytest.approx(expected)

    def test_band_is_filled_near_13_nm(self):
        u = shape_uncertainty(_geom(), sigma_z=13.0)
        assert 0.045 < u.u_shape_index / u.shape_index < 0.055

    def test_zero_sigma(self):
        u = shape_uncertainty(_geom(), sigma_z=0.0)
        assert u.u_aspect_ratio == u.u_curviness == u.u_shape_index == 0.0

    def test_perpendicular_component_only_affects_sarcomere_length(self):
        iso = shape_uncertainty(_geom(), sigma_z=10.0)
        along_only = shape_uncertainty(_geom(), sigma_z=10.0, perpendicular_ratio=0.0)
        assert along_only.u_shape_index == pytest.approx(iso.u_shape_index)
        assert along_only.u_aspect_ratio < iso.u_aspect_ratio

    def test_separate_m_line_error(self):
        u = shape_uncertainty(_geom(), sigma_z=5.0, sigma_m=20.0)
        assert u.sigma_m_along == 20.0 and u.sigma_z_along == 5.0

    @pytest.mark.parametrize("bad", [-1.0, np.nan, np.inf])
    def test_invalid_sigma(self, bad):
        with pytest.raises(ValueError):
            shape_uncertainty(_geom(), sigma_z=bad)


class TestVectorisedClassifier:
    """The array classifier must agree with the scalar one."""

    def test_matches_scalar_classifier(self):
        rng = np.random.default_rng(0)
        asr = np.concatenate([rng.uniform(0.5, 4.0, 3000), [1.05, 1.0, 0.95, 2.0]])
        cur = np.concatenate([asr[:3000] * rng.uniform(0.85, 1.15, 3000), [1.05, 1.0, 0.95, 2.1]])
        order = [
            ShapeClassification.SQUARE,
            ShapeClassification.RECTANGULAR,
            ShapeClassification.BARREL,
            ShapeClassification.HOURGLASS,
        ]
        labels = classify_shape_arrays(asr, cur)
        expected = [
            order.index(classify_sarcomere_shape(a, c)) for a, c in zip(asr, cur, strict=True)
        ]
        np.testing.assert_array_equal(labels, expected)


class TestClassStability:
    """Monte Carlo repeatability of the shape class."""

    def test_no_error_keeps_class(self):
        stab = class_stability(_geom(), sigma_z=0.0, n_draws=200, seed=0)
        assert stab.reference_class == "barrel"
        assert stab.stability == 1.0

    def test_monte_carlo_matches_delta_method(self):
        geom = _geom()
        u = shape_uncertainty(geom, sigma_z=10.0)
        stab = class_stability(geom, sigma_z=10.0, n_draws=20000, seed=1)
        assert stab.sd_shape_index == pytest.approx(u.u_shape_index, rel=0.05)
        assert stab.sd_aspect_ratio == pytest.approx(u.u_aspect_ratio, rel=0.05)
        assert stab.sd_curviness == pytest.approx(u.u_curviness, rel=0.05)

    def test_on_boundary_is_coin_flip(self):
        lm = LZ / 0.95  # SI exactly on the barrel/rectangular edge
        stab = class_stability(_geom(lm=lm), sigma_z=10.0, n_draws=20000, seed=2)
        assert stab.stability == pytest.approx(0.5, abs=0.03)

    def test_frequencies_sum_to_one(self):
        stab = class_stability(_geom(), sigma_z=30.0, n_draws=500, seed=3)
        assert sum(stab.class_frequencies.values()) == pytest.approx(1.0)

    def test_independent_of_pixel_scale(self):
        a = class_stability(_geom(pixel_to_unit=1.0), sigma_z=15.0, n_draws=3000, seed=4)
        b = class_stability(_geom(pixel_to_unit=2.5), sigma_z=15.0, n_draws=3000, seed=4)
        assert a.sd_shape_index == pytest.approx(b.sd_shape_index, rel=1e-9)


class TestClassificationMargin:
    """Distances from the class edges in (AsR, SI)."""

    def test_euploid_margin(self):
        geom = _geom()
        margin = classification_margin(geom.aspect_ratio, geom.curviness)
        assert margin.shape_index_margin == pytest.approx(abs(LZ / LM - 0.95))

    def test_invalid(self):
        with pytest.raises(ValueError):
            classification_margin(0.0, 1.0)


class TestEstimateAnnotationSigma:
    """Click error from repeated annotations."""

    def _repeat(self, rng, s_along, s_perp, swap=False):
        base = np.array(_points())
        dirs = [np.array([0.0, 1.0])] * 6
        noisy = []
        for p, d in zip(base, dirs, strict=True):
            n = np.array([-d[1], d[0]])
            noisy.append(p + rng.normal(0, s_along) * d + rng.normal(0, s_perp) * n)
        if swap:
            noisy[0], noisy[1] = noisy[1], noisy[0]
        return calculate_sarcomere_geometry(*map(tuple, noisy), unit="nm")

    def test_recovers_along_and_perpendicular_error(self):
        rng = np.random.default_rng(5)
        repeats = [self._repeat(rng, 6.0, 2.0, swap=(i % 3 == 0)) for i in range(400)]
        est = estimate_annotation_sigma(repeats)
        assert est.sigma_z_along == pytest.approx(6.0, rel=0.1)
        assert est.sigma_z_perp == pytest.approx(2.0, rel=0.1)
        assert est.sigma_m_along == pytest.approx(6.0, rel=0.15)
        assert est.n_repeats == 400

    def test_needs_two_repeats(self):
        with pytest.raises(ValueError):
            estimate_annotation_sigma([_geom()])

    def test_mixed_units_raise(self):
        other = calculate_sarcomere_geometry(*_points(), unit="px")
        with pytest.raises(ValueError):
            estimate_annotation_sigma([_geom(), other])


# The eight dihedral display transforms of a w x h crop, (x, y) -> (x', y'),
# pixel centres at integers (as in sarcostat.io.study).
_TRANSFORMS = {
    "identity": lambda x, y, w, h: (x, y),
    "rot90": lambda x, y, w, h: (y, w - 1 - x),
    "rot180": lambda x, y, w, h: (w - 1 - x, h - 1 - y),
    "rot270": lambda x, y, w, h: (h - 1 - y, x),
    "flip_h": lambda x, y, w, h: (w - 1 - x, y),
    "flip_v": lambda x, y, w, h: (x, h - 1 - y),
    "transpose": lambda x, y, w, h: (y, x),
    "anti_transpose": lambda x, y, w, h: (h - 1 - y, w - 1 - x),
}
_CROP = (4096, 4096)


def _rotate(points, degrees, centre=(2000.0, 2000.0)):
    t = np.radians(degrees)
    rot = np.array([[np.cos(t), -np.sin(t)], [np.sin(t), np.cos(t)]])
    return (np.asarray(points, dtype=float) @ rot.T) + np.asarray(centre)


def _clicked_on_screen(original, transform):
    """Order in which an annotator clicks the six landmarks on screen.

    The Z-line on the left of the screen (or on top, if the sarcomere is
    vertical on screen) comes first, and each line is clicked from its upper
    (or left) end. Returns the original-pixel points in that click order,
    which is what the inverse display transform gives back.
    """
    original = np.asarray(original, dtype=float)
    shown = np.array([_TRANSFORMS[transform](x, y, *_CROP) for x, y in original])
    lines = [(0, 1), (2, 3), (4, 5)]
    z_a, z_b = shown[0:2].mean(0), shown[4:6].mean(0)
    axis = np.argmax(np.abs(z_b - z_a))  # 0: horizontal on screen
    if z_b[axis] < z_a[axis]:
        lines = lines[::-1]
    order = []
    for i, j in lines:
        across = 1 - axis
        order += [i, j] if shown[i][across] <= shown[j][across] else [j, i]
    return original[order]


class TestCanonicalizeLandmarks:
    """Canonical landmark order from a myofibril axis."""

    def test_canonical_input_is_unchanged(self):
        out = canonicalize_sarcomere_landmarks(_points(), axis=(1.0, 0.0))
        np.testing.assert_array_equal(out, np.array(_points()))

    def test_restores_swapped_z_lines_and_reversed_endpoints(self):
        pts = np.array(_points())
        clicked = pts[[5, 4, 3, 2, 1, 0]]
        out = canonicalize_sarcomere_landmarks(clicked, axis=(1.0, 0.0))
        np.testing.assert_array_equal(out, pts)

    def test_axis_direction_sets_z_line_1(self):
        pts = np.array(_points())
        out = canonicalize_sarcomere_landmarks(pts, axis=(-2.0, 0.0))
        # Z-line 1 is now the right one; endpoints run left to right of the
        # reversed axis, i.e. from the bottom of the image up.
        np.testing.assert_array_equal(out, pts[[5, 4, 3, 2, 1, 0]])

    @pytest.mark.parametrize("degrees", [0.0, 30.0, 90.0, 135.0, 250.0])
    @pytest.mark.parametrize("use_axis", [True, False])
    def test_invariant_under_the_eight_transforms(self, degrees, use_axis):
        rng = np.random.default_rng(int(degrees))
        truth = _rotate(_points(), degrees) + rng.normal(0, 3.0, (6, 2))
        axis = _rotate([(1.0, 0.0)], degrees, centre=(0.0, 0.0))[0] if use_axis else None
        results = {
            name: canonicalize_sarcomere_landmarks(_clicked_on_screen(truth, name), axis=axis)
            for name in _TRANSFORMS
        }
        reference = results["identity"]
        for name, out in results.items():
            np.testing.assert_array_equal(out, reference, err_msg=name)
        if use_axis:
            np.testing.assert_array_equal(reference, truth)

    def test_without_axis_z_line_1_is_left_in_the_image(self):
        pts = np.array(_points())
        out = canonicalize_sarcomere_landmarks(pts[[4, 5, 2, 3, 0, 1]])
        np.testing.assert_array_equal(out, pts)

    def test_without_axis_vertical_sarcomere_starts_at_the_top(self):
        pts = _rotate(_points(), -90.0)  # clicked Z-line 1 at the bottom of the image
        assert pts[0:2, 1].mean() > pts[4:6, 1].mean()
        out = canonicalize_sarcomere_landmarks(pts)
        np.testing.assert_array_equal(out[0:2], pts[[5, 4]])
        assert out[0:2, 1].mean() < out[4:6, 1].mean()

    def test_skipped_m_line_stays_nan(self):
        pts = np.array(_points())[[5, 4, 2, 3, 1, 0]]
        pts[2:4] = np.nan
        out = canonicalize_sarcomere_landmarks(pts, axis=(1.0, 0.0))
        np.testing.assert_array_equal(out[[0, 1, 4, 5]], np.array(_points())[[0, 1, 4, 5]])
        assert np.isnan(out[2:4]).all()

    @pytest.mark.parametrize(
        ("points", "axis"),
        [
            (np.zeros((5, 2)), (1.0, 0.0)),
            (_points(), (0.0, 0.0)),
            (_points(), (np.nan, 1.0)),
            ([(0, 0), (0, 1), (0, 0), (0, 1), (0, 0), (0, 1)], None),
        ],
    )
    def test_invalid(self, points, axis):
        with pytest.raises(ValueError):
            canonicalize_sarcomere_landmarks(points, axis=axis)


class TestLandmarkOffsets:
    """Along (outward) and across offsets from a reference annotation."""

    def test_outward_sign_inward_bias_does_not_cancel(self):
        b = 7.0
        ref = np.array(_points())
        inward = ref.copy()
        inward[[0, 2, 4], 1] += b  # first endpoints moved down, towards the midpoint
        inward[[1, 3, 5], 1] -= b  # second endpoints moved up, towards the midpoint
        off = landmark_offsets(inward, ref, axis=(1.0, 0.0))
        np.testing.assert_allclose(off[:, 0], -b)
        np.testing.assert_allclose(off[:, 1], 0.0, atol=1e-12)
        # with one along direction per line the bias would cancel
        assert off[[0, 1], 0].mean() == pytest.approx(-b)

    def test_across_is_positive_towards_z_line_2(self):
        ref = _rotate(_points(), 40.0)
        axis = _rotate([(1.0, 0.0)], 40.0, centre=(0.0, 0.0))[0]
        moved = ref + 2.5 * axis
        off = landmark_offsets(moved, ref, axis=axis)
        np.testing.assert_allclose(off[:, 1], 2.5)
        np.testing.assert_allclose(off[:, 0], 0.0, atol=1e-9)

    def test_annotation_order_does_not_matter(self):
        rng = np.random.default_rng(3)
        ref = np.array(_points())
        ann = ref + rng.normal(0, 4.0, (6, 2))
        a = landmark_offsets(ann, ref, axis=(1.0, 0.0))
        b = landmark_offsets(ann[[5, 4, 3, 2, 1, 0]], ref[[1, 0, 2, 3, 5, 4]], axis=(1.0, 0.0))
        np.testing.assert_allclose(a, b, rtol=0, atol=1e-12)

    def test_skipped_m_line_gives_nan(self):
        ref = np.array(_points())
        ann = ref.copy()
        ann[2:4] = np.nan
        off = landmark_offsets(ann, ref, axis=(1.0, 0.0))
        assert np.isnan(off[2:4]).all() and np.isfinite(off[[0, 1, 4, 5]]).all()


def _px_geom(points):
    return calculate_sarcomere_geometry(*map(tuple, points), unit="px")


class TestEstimateWithSwappedRepeats:
    """K-M3: repeats that start from the other Z-line."""

    SQUARE = np.array([(10, 10), (10, 30), (20, 10), (20, 30), (30, 10), (30, 30)], dtype=float)

    def _repeats(self, n=3, sigma=0.05, seed=0):
        rng = np.random.default_rng(seed)
        return [self.SQUARE + rng.normal(0, sigma, (6, 2)) for _ in range(n)]

    def test_swapped_repeat_k_m3(self):
        reps = self._repeats()
        reps[2] = reps[2][[4, 5, 3, 2, 0, 1]]  # starts from the other Z-line, M-line reversed
        est = estimate_annotation_sigma([_px_geom(p) for p in reps])
        assert est.sigma_z_perp <= 0.1  # 11.5 px before the fix
        assert est.sigma_z_perp / est.sigma_z_along <= 3.0  # about 270 before the fix

    def test_swap_gives_the_same_estimate(self):
        reps = self._repeats(n=4, seed=1)
        plain = estimate_annotation_sigma([_px_geom(p) for p in reps])
        mixed = [p[[5, 4, 2, 3, 1, 0]] if i % 2 else p for i, p in enumerate(reps)]
        est = estimate_annotation_sigma([_px_geom(p) for p in mixed])
        for field in ("sigma_z_along", "sigma_z_perp", "sigma_m_along", "sigma_m_perp"):
            assert getattr(est, field) == pytest.approx(getattr(plain, field), rel=1e-12)

    @pytest.mark.parametrize("use_axis", [True, False])
    def test_invariant_under_the_eight_transforms(self, use_axis):
        rng = np.random.default_rng(9)
        truth = _rotate(_points(), 25.0)
        repeats = [truth + rng.normal(0, 4.0, (6, 2)) for _ in range(len(_TRANSFORMS))]
        axis = _rotate([(1.0, 0.0)], 25.0, centre=(0.0, 0.0))[0] if use_axis else None
        plain = estimate_annotation_sigma([_px_geom(p) for p in repeats], axis=axis)
        for shift in range(len(_TRANSFORMS)):
            names = list(_TRANSFORMS)[shift:] + list(_TRANSFORMS)[:shift]
            shown = [_clicked_on_screen(p, name) for p, name in zip(repeats, names, strict=True)]
            est = estimate_annotation_sigma([_px_geom(p) for p in shown], axis=axis)
            assert est.sigma_z_perp == pytest.approx(plain.sigma_z_perp, rel=1e-12)
            assert est.sigma_m_along == pytest.approx(plain.sigma_m_along, rel=1e-12)

    def test_axis_in_physical_units(self):
        """The axis is a direction, so the pixel size does not matter."""
        reps = self._repeats(n=3, seed=2)
        geoms = [
            calculate_sarcomere_geometry(*map(tuple, p), pixel_to_unit=5.0, unit="nm") for p in reps
        ]
        a = estimate_annotation_sigma(geoms, axis=(1.0, 0.0))
        b = estimate_annotation_sigma(geoms)
        assert a.sigma_z_along == pytest.approx(b.sigma_z_along, rel=1e-12)
        assert a.unit == "nm"
