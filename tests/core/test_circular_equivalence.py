"""Bit-for-bit equivalence of :mod:`sarcostat.core._circular` with pingouin 0.5.x.

The numpy routines repeat the operations of pingouin's ``circ_r``,
``circ_mean``, ``circ_rayleigh`` and ``circ_vtest`` in the same order, so in
CPython the resultant length, the mean direction, the Rayleigh z and the V
statistic must be equal to the last bit; p-values are held to the AC-P3
floor of 1e-12. The public :mod:`sarcostat.core.circular_stats` functions
are compared with the values the pingouin-based implementation returned.
The module is skipped where pingouin is not installed (e.g. Pyodide).
"""

import subprocess
import sys
from functools import lru_cache

import numpy as np
import pytest

from sarcostat.core import _circular
from sarcostat.core.circular_stats import (
    _circular_percentile,
    bootstrap_circular_mean_ci,
    bootstrap_mvl_ci,
    calculate_circular_mean,
    calculate_mean_vector_length,
    perform_rayleigh_test,
    perform_v_test,
    to_minuspi_pi,
)

pg = pytest.importorskip("pingouin")

P_TOLERANCE = 1e-12
FIXED_N = (1, 2, 3, 5, 20, 200)
FIXED_SEED = 7
RANDOM_SEED = 20261120
N_RANDOM = 1000
DIRECTIONS = (0.0, 0.7, -2.5, 3.0)


def _bits(value) -> bytes:
    """Raw float64 bytes of a scalar, array or tuple (sign of zero and NaN included)."""
    return np.asarray(value, dtype=np.float64).tobytes()


def _p_close(actual, expected) -> bool:
    """Return True when two p-values agree within 1e-12 absolute or relative."""
    a = float(actual)
    b = float(expected)
    if np.isnan(b):
        return bool(np.isnan(a))
    return abs(a - b) <= P_TOLERANCE * max(1.0, abs(b))


def _fixed_cases() -> dict[str, np.ndarray]:
    """Named fixed inputs: concentrated and uniform samples for each n, plus special sets."""
    rng = np.random.default_rng(FIXED_SEED)
    cases: dict[str, np.ndarray] = {}
    for n in FIXED_N:
        cases[f"concentrated-n{n}"] = rng.vonmises(0.8, 20.0, n)
        cases[f"uniform-n{n}"] = rng.uniform(-np.pi, np.pi, n)
    cases["antipodal"] = np.array([0.3, 0.3 + np.pi, 1.1, 1.1 + np.pi])
    cases["near-pi"] = np.array([np.pi - 0.1, -np.pi + 0.1, np.pi - 0.05])
    cases["identical"] = np.full(9, -1.25)
    return cases


FIXED_CASES = _fixed_cases()


@lru_cache(maxsize=1)
def _random_cases() -> tuple[tuple[np.ndarray, np.ndarray, float], ...]:
    """1000 seeded von Mises samples of varied size and concentration.

    Each entry is ``(angles, shifted, direction)``: the draw in [-pi, pi],
    the same draw shifted by a whole number of turns, and a test direction.
    """
    rng = np.random.default_rng(RANDOM_SEED)
    cases = []
    for _ in range(N_RANDOM):
        n = int(rng.integers(1, 401))
        kappa = 0.0 if rng.random() < 0.15 else float(10.0 ** rng.uniform(-2.0, 3.0))
        mu = float(rng.uniform(-np.pi, np.pi))
        angles = rng.vonmises(mu, kappa, n)
        shifted = angles + 2.0 * np.pi * float(rng.integers(-2, 3))
        direction = float(rng.uniform(-np.pi, np.pi))
        cases.append((angles, shifted, direction))
    return tuple(cases)


def _form(angles: np.ndarray, shifted: np.ndarray, form: str) -> np.ndarray:
    """Select the normalised, raw or shifted form of a random case."""
    if form == "normalised":
        return to_minuspi_pi(angles)
    if form == "raw":
        return angles
    return shifted


def _assert_unweighted_equal(a: np.ndarray, direction: float, label: str) -> None:
    """Assert R, mean, z and V are bit-equal to pingouin and p-values within the floor."""
    assert _bits(_circular.circ_r(a)) == _bits(pg.circ_r(a)), label
    assert _bits(_circular.circ_mean(a)) == _bits(pg.circ_mean(a)), label
    z, pz = _circular.circ_rayleigh(a)
    z_ref, pz_ref = pg.circ_rayleigh(a)
    assert _bits(z) == _bits(z_ref), label
    assert _p_close(pz, pz_ref), label
    v, pv = _circular.circ_vtest(a, direction)
    v_ref, pv_ref = pg.circ_vtest(a, dir=direction)
    assert _bits(v) == _bits(v_ref), label
    assert _p_close(pv, pv_ref), label


class TestFixedInputs:
    """Fixed inputs: n in {1, 2, 3, 5, 20, 200}, concentrated, uniform and special sets."""

    @pytest.mark.parametrize("form", ["normalised", "raw"])
    @pytest.mark.parametrize("name", sorted(FIXED_CASES))
    def test_matches_pingouin(self, name, form):
        """Every statistic agrees with pingouin on the raw and the normalised angles."""
        angles = FIXED_CASES[name]
        a = to_minuspi_pi(angles) if form == "normalised" else angles
        for direction in DIRECTIONS:
            _assert_unweighted_equal(a, direction, f"{name}/{form}/{direction}")

    def test_antipodal_set_is_balanced(self):
        """The antipodal set has a resultant at rounding level, identical to pingouin's."""
        a = to_minuspi_pi(FIXED_CASES["antipodal"])
        r = float(_circular.circ_r(a))
        assert r < 1e-15
        assert _bits(r) == _bits(pg.circ_r(a))

    @pytest.mark.parametrize("name", ["concentrated-n20", "uniform-n200", "antipodal"])
    def test_weighted_and_binned_match_pingouin(self, name):
        """Weights and the bin-spacing correction follow the same operations."""
        a = to_minuspi_pi(FIXED_CASES[name])
        rng = np.random.default_rng(11)
        weights = rng.integers(1, 6, a.size).astype(float)
        spacing = 2.0 * np.pi / 36.0
        assert _bits(_circular.circ_r(a, weights)) == _bits(pg.circ_r(a, weights))
        assert _bits(_circular.circ_r(a, weights, spacing)) == _bits(pg.circ_r(a, weights, spacing))
        assert _bits(_circular.circ_mean(a, weights)) == _bits(pg.circ_mean(a, weights))
        z, pz = _circular.circ_rayleigh(a, weights, spacing)
        z_ref, pz_ref = pg.circ_rayleigh(a, weights, spacing)
        assert _bits(z) == _bits(z_ref)
        assert _p_close(pz, pz_ref)
        v, pv = _circular.circ_vtest(a, 0.4, weights, spacing)
        v_ref, pv_ref = pg.circ_vtest(a, dir=0.4, w=weights, d=spacing)
        assert _bits(v) == _bits(v_ref)
        assert _p_close(pv, pv_ref)

    def test_nan_handling_matches_pingouin(self):
        """NaN is dropped from the sums and the weights as pingouin drops it."""
        a = np.array([0.1, np.nan, 0.5, 1.2, -0.4])
        weights = np.array([1.0, 2.0, 3.0, 1.0, 2.0])
        assert _bits(_circular.circ_r(a)) == _bits(pg.circ_r(a))
        assert _bits(_circular.circ_r(a, weights)) == _bits(pg.circ_r(a, weights))
        assert _bits(_circular.circ_mean(a)) == _bits(pg.circ_mean(a))
        assert _bits(_circular.circ_mean(a, weights)) == _bits(pg.circ_mean(a, weights))
        z, pz = _circular.circ_rayleigh(a)
        z_ref, pz_ref = pg.circ_rayleigh(a)
        assert _bits(z) == _bits(z_ref)
        assert _p_close(pz, pz_ref)
        v, pv = _circular.circ_vtest(a, 0.2)
        v_ref, pv_ref = pg.circ_vtest(a, dir=0.2)
        assert _bits(v) == _bits(v_ref)
        assert _p_close(pv, pv_ref)

    @pytest.mark.parametrize(
        "func",
        [
            calculate_mean_vector_length,
            perform_rayleigh_test,
            perform_v_test,
            calculate_circular_mean,
            bootstrap_circular_mean_ci,
            bootstrap_mvl_ci,
        ],
    )
    @pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
    def test_public_functions_reject_non_finite(self, func, bad):
        """The public functions still reject NaN and inf up front."""
        with pytest.raises(ValueError, match="finite"):
            func(np.array([0.1, bad, 0.5]))

    @pytest.mark.parametrize("angles", [np.array([0.0, 7.0]), np.array([])])
    def test_out_of_range_and_empty_raise_like_pingouin(self, angles):
        """A spread above 2*pi and an empty input raise ValueError in both."""
        for func in ("circ_r", "circ_mean"):
            with pytest.raises(ValueError):
                getattr(pg, func)(angles)
            with pytest.raises(ValueError):
                getattr(_circular, func)(angles)
        with pytest.raises(ValueError):
            pg.circ_rayleigh(angles)
        with pytest.raises(ValueError):
            _circular.circ_rayleigh(angles)
        with pytest.raises(ValueError):
            pg.circ_vtest(angles)
        with pytest.raises(ValueError):
            _circular.circ_vtest(angles)

    def test_mismatched_weights_raise(self):
        """Weights of another shape are refused."""
        with pytest.raises(ValueError, match="dimensions"):
            _circular.circ_r(np.array([0.1, 0.2]), np.array([1.0]))
        with pytest.raises(ValueError, match="dimensions"):
            _circular.circ_mean(np.array([0.1, 0.2]), np.array([1.0]))
        with pytest.raises(ValueError, match="dimensions"):
            _circular.circ_rayleigh(np.array([0.1, 0.2]), np.array([1.0]))
        with pytest.raises(ValueError, match="dimensions"):
            _circular.circ_vtest(np.array([0.1, 0.2]), 0.0, np.array([1.0]))


class TestRandomInputs:
    """1000 seeded random inputs of varied size and concentration."""

    @pytest.mark.parametrize("form", ["normalised", "raw", "shifted"])
    def test_matches_pingouin(self, form):
        """R, mean, z and V are bit-equal; p-values agree within 1e-12."""
        for idx, (angles, shifted, direction) in enumerate(_random_cases()):
            a = _form(angles, shifted, form)
            _assert_unweighted_equal(a, direction, f"case {idx} ({form}, n={a.size})")

    def test_case_mix(self):
        """The random set spans small and large n and includes uniform draws."""
        sizes = np.array([c[0].size for c in _random_cases()])
        assert len(sizes) == N_RANDOM
        assert sizes.min() <= 5
        assert sizes.max() >= 350


def _old_finite(angles) -> np.ndarray:
    """The input conversion the public functions apply before any statistic."""
    return np.asarray(angles, dtype=float).ravel()


def _old_bootstrap_mean(angles, confidence, n_bootstrap, seed) -> tuple[float, float, float]:
    """The pingouin-based bootstrap of the circular mean, as released."""
    a = _old_finite(angles)
    n = a.size
    rng = np.random.default_rng(seed)
    ang = to_minuspi_pi(a)
    means = np.array(
        [float(pg.circ_mean(rng.choice(ang, size=n, replace=True))) for _ in range(n_bootstrap)]
    )
    mean = float(pg.circ_mean(to_minuspi_pi(a)))
    alpha = 1.0 - confidence
    lower = _circular_percentile(means, 100.0 * alpha / 2.0, mean)
    upper = _circular_percentile(means, 100.0 * (1.0 - alpha / 2.0), mean)
    return float(mean), lower, upper


def _old_bootstrap_mvl(angles, confidence, n_bootstrap, seed) -> tuple[float, float, float]:
    """The pingouin-based bootstrap of the mean vector length, as released."""
    a = _old_finite(angles)
    n = a.size
    rng = np.random.default_rng(seed)
    ang = to_minuspi_pi(a)
    mvls = np.array(
        [float(pg.circ_r(rng.choice(ang, size=n, replace=True))) for _ in range(n_bootstrap)]
    )
    alpha = 1.0 - confidence
    lower = float(np.percentile(mvls, 100.0 * alpha / 2.0))
    upper = float(np.percentile(mvls, 100.0 * (1.0 - alpha / 2.0)))
    mvl = float(pg.circ_r(to_minuspi_pi(a)))
    return float(mvl), lower, upper


def _public_inputs() -> list[tuple[str, np.ndarray, float]]:
    """Fixed cases plus the first 200 random cases, each with a direction."""
    inputs = [(name, angles, 0.7) for name, angles in sorted(FIXED_CASES.items())]
    for idx, (_, shifted, direction) in enumerate(_random_cases()[:200]):
        inputs.append((f"random-{idx}", shifted, direction))
    return inputs


class TestPublicFunctionsUnchanged:
    """The public functions return exactly what the pingouin-based versions returned."""

    def test_point_statistics(self):
        """Mean vector length, circular mean, Rayleigh and V-test results are unchanged."""
        for label, angles, direction in _public_inputs():
            ang = to_minuspi_pi(_old_finite(angles))
            assert _bits(calculate_mean_vector_length(angles)) == _bits(float(pg.circ_r(ang))), (
                label
            )
            assert _bits(calculate_circular_mean(angles)) == _bits(float(pg.circ_mean(ang))), label
            z, pz = perform_rayleigh_test(angles)
            z_ref, pz_ref = pg.circ_rayleigh(ang)
            assert isinstance(z, float) and isinstance(pz, float), label
            assert _bits(z) == _bits(float(z_ref)), label
            assert _p_close(pz, pz_ref), label
            mu0 = to_minuspi_pi(np.array([direction]))[0]
            v, pv = perform_v_test(angles, direction)
            v_ref, pv_ref = pg.circ_vtest(ang, dir=mu0)
            assert isinstance(v, float) and isinstance(pv, float), label
            assert _bits(v) == _bits(float(v_ref)), label
            assert _p_close(pv, pv_ref), label

    @pytest.mark.parametrize(
        ("name", "confidence", "n_bootstrap", "seed"),
        [
            ("concentrated-n20", 0.95, 1000, 42),
            ("uniform-n20", 0.90, 300, 3),
            ("concentrated-n200", 0.95, 200, 2026),
            ("uniform-n200", 0.99, 200, 0),
        ],
    )
    def test_bootstrap_intervals(self, name, confidence, n_bootstrap, seed):
        """Bootstrap CIs of the mean and of R are bit-equal with a fixed seed."""
        angles = FIXED_CASES[name]
        got_mean = bootstrap_circular_mean_ci(angles, confidence, n_bootstrap, seed)
        got_mvl = bootstrap_mvl_ci(angles, confidence, n_bootstrap, seed)
        assert _bits(got_mean) == _bits(_old_bootstrap_mean(angles, confidence, n_bootstrap, seed))
        assert _bits(got_mvl) == _bits(_old_bootstrap_mvl(angles, confidence, n_bootstrap, seed))

    def test_bootstrap_small_sample(self):
        """A sample below the recommended size still warns and is bit-equal."""
        angles = FIXED_CASES["concentrated-n5"]
        with pytest.warns(UserWarning, match="below recommended"):
            got_mean = bootstrap_circular_mean_ci(angles, 0.95, 400, 9)
        with pytest.warns(UserWarning, match="below recommended"):
            got_mvl = bootstrap_mvl_ci(angles, 0.95, 400, 9)
        assert _bits(got_mean) == _bits(_old_bootstrap_mean(angles, 0.95, 400, 9))
        assert _bits(got_mvl) == _bits(_old_bootstrap_mvl(angles, 0.95, 400, 9))


def test_core_imports_neither_pingouin_nor_scipy():
    """Importing :mod:`sarcostat.core` loads neither pingouin nor scipy."""
    code = (
        "import sys\n"
        "import sarcostat.core\n"
        "import sarcostat.core.circular_stats\n"
        "loaded = sorted({m.split('.')[0] for m in sys.modules} & {'pingouin', 'scipy'})\n"
        "print(','.join(loaded))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert out == ""
