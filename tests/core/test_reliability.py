"""Unit tests for core.reliability: variance components of a nested observer study.

The design is A annotators x S sessions (nested in annotators) x T targets,
one observation per cell, plus duplicate presentations of a few targets in
every session. Reference values are closed-form: the expected mean squares
come from the indicator vectors of each random effect, and a single mean
square gives the exact chi-square interval.
"""

from __future__ import annotations

import itertools

import numpy as np
import pytest
from scipy.stats import chi2

from sarcostat.core.reliability import _mean_squares as mean_squares
from sarcostat.core.reliability import (
    bootstrap_derived,
    complete_cases,
    ems_coefficients,
    mls_interval,
    sigma_effective,
    variance_components,
)

TRUE = {
    "target": 4.0,
    "annotator": 0.5,
    "session": 0.6,
    "annotator_x_target": 0.8,
    "session_x_target": 1.0,
    "pure_error": 0.5,
}


def _simulate(rng, n_a=3, n_s=3, n_t=30, n_dup=6, comps=TRUE, mu=10.0):
    """Long-format data of the observer model, with ``n_dup`` duplicated targets."""
    sd = {k: np.sqrt(v) for k, v in comps.items()}
    tau = rng.normal(0, sd["target"], n_t)
    alpha = rng.normal(0, sd["annotator"], n_a)
    beta = rng.normal(0, sd["session"], (n_a, n_s))
    at = rng.normal(0, sd["annotator_x_target"], (n_a, n_t))
    bt = rng.normal(0, sd["session_x_target"], (n_a, n_s, n_t))
    rows = []
    for a, s, t in itertools.product(range(n_a), range(n_s), range(n_t)):
        base = mu + tau[t] + alpha[a] + beta[a, s] + at[a, t] + bt[a, s, t]
        rows.append((f"A{a}", f"S{s}", f"T{t:02d}", base + rng.normal(0, sd["pure_error"]), 1))
        if t < n_dup:
            rows.append((f"A{a}", f"S{s}", f"T{t:02d}", base + rng.normal(0, sd["pure_error"]), 2))
    annotator, session, target, value, presentation = map(list, zip(*rows, strict=True))
    return annotator, session, target, np.array(value), presentation


def _sigma_eff_true(comps=TRUE):
    return (
        comps["session"]
        + comps["annotator_x_target"]
        + comps["session_x_target"]
        + comps["pure_error"]
    )


class TestExpectedMeanSquares:
    """Exact EMS of the balanced core."""

    def test_protocol_table(self):
        coef = ems_coefficients(3, 3, 30)
        assert coef["annotator"] == {
            "residual": 1,
            "annotator_x_target": 3,
            "session": 30,
            "annotator": 90,
        }
        assert coef["session"] == {"residual": 1, "session": 30}
        assert coef["annotator_x_target"] == {"residual": 1, "annotator_x_target": 3}
        assert coef["residual"] == {"residual": 1}
        assert coef["target"] == {"residual": 1, "annotator_x_target": 3, "target": 9}

    @pytest.mark.parametrize(("n_a", "n_s", "n_t"), [(3, 3, 30), (2, 4, 5), (4, 2, 7)])
    def test_coefficients_from_indicator_vectors(self, n_a, n_s, n_t):
        """E[y'Qy] = sum_c sigma_c^2 tr(Q Z_c Z_c'), and tr(Q Z_c Z_c') = sum of MS(z) over levels."""
        shape = (n_a, n_s, n_t)
        indicators = {
            "annotator": [
                np.broadcast_to(np.eye(n_a)[a][:, None, None], shape) for a in range(n_a)
            ],
            "session": [
                np.einsum("i,j,k->ijk", np.eye(n_a)[a], np.eye(n_s)[s], np.ones(n_t))
                for a in range(n_a)
                for s in range(n_s)
            ],
            "target": [np.broadcast_to(np.eye(n_t)[t][None, None, :], shape) for t in range(n_t)],
            "annotator_x_target": [
                np.einsum("i,j,k->ijk", np.eye(n_a)[a], np.ones(n_s), np.eye(n_t)[t])
                for a in range(n_a)
                for t in range(n_t)
            ],
            "residual": [np.eye(n_a * n_s * n_t)[i].reshape(shape) for i in range(n_a * n_s * n_t)],
        }
        exact = {stratum: {} for stratum in indicators}
        for component, vectors in indicators.items():
            totals = dict.fromkeys(indicators, 0.0)
            for z in vectors:
                for stratum, ms in mean_squares(np.asarray(z, dtype=float))[0].items():
                    totals[stratum] += ms
            for stratum, total in totals.items():
                if abs(total) > 1e-9:
                    exact[stratum][component] = total
        coef = ems_coefficients(n_a, n_s, n_t)
        for stratum in indicators:
            assert set(exact[stratum]) == set(coef[stratum]), stratum
            for component, value in coef[stratum].items():
                assert exact[stratum][component] == pytest.approx(value, rel=1e-12)

    def test_constant_has_zero_mean_squares(self):
        ms, df = mean_squares(np.full((3, 3, 30), 7.25))
        assert all(abs(v) < 1e-20 for v in ms.values())
        assert df == {
            "annotator": 2,
            "session": 6,
            "target": 29,
            "annotator_x_target": 58,
            "residual": 174,
        }


class TestCompleteCases:
    def test_balanced_input(self):
        rng = np.random.default_rng(0)
        core = complete_cases(*_simulate(rng))
        assert core.values.shape == (3, 3, 30)
        assert core.annotators == ("A0", "A1", "A2")
        assert core.sessions == (("S0", "S1", "S2"),) * 3
        assert core.targets[0] == "T00" and core.dropped_targets == ()
        assert core.duplicate_pairs.shape == (54, 2)

    def test_values_land_in_their_cells(self):
        ann = ["b", "a", "a", "b", "a", "b", "a", "b"]
        ses = [2, 1, 2, 1, 1, 2, 2, 1]
        tgt = ["x", "x", "x", "x", "y", "y", "y", "y"]
        val = [4.0, 1.0, 2.0, 3.0, 5.0, 8.0, 6.0, 7.0]
        core = complete_cases(ann, ses, tgt, val)
        np.testing.assert_array_equal(
            core.values, [[[1.0, 5.0], [2.0, 6.0]], [[3.0, 7.0], [4.0, 8.0]]]
        )
        assert core.sessions == ((1, 2), (1, 2))

    def test_drops_targets_with_a_missing_cell(self):
        rng = np.random.default_rng(1)
        ann, ses, tgt, val, pres = _simulate(rng, n_t=5, n_dup=2)
        drop = next(i for i, (t, p) in enumerate(zip(tgt, pres, strict=True)) if t == "T03")
        keep = [i for i in range(len(val)) if i != drop]
        core = complete_cases(*([x[i] for i in keep] for x in (ann, ses, tgt, val, pres)))
        assert core.targets == ("T00", "T01", "T02", "T04")
        assert core.dropped_targets == ("T03",)
        assert core.values.shape == (3, 3, 4)

    def test_duplicate_pairs_do_not_need_a_complete_target(self):
        rng = np.random.default_rng(2)
        ann, ses, tgt, val, pres = _simulate(rng, n_t=4, n_dup=1)
        drop = next(
            i for i, (t, p) in enumerate(zip(tgt, pres, strict=True)) if t == "T00" and p == 1
        )
        keep = [i for i in range(len(val)) if i != drop]
        core = complete_cases(*([x[i] for i in keep] for x in (ann, ses, tgt, val, pres)))
        assert "T00" in core.dropped_targets
        assert core.duplicate_pairs.shape == (8, 2)

    @pytest.mark.parametrize(
        ("change", "match"),
        [
            (lambda d: d[3].__setitem__(0, np.nan), "finite"),
            (lambda d: d[4].__setitem__(0, 3), "presentation"),
            (lambda d: [x.append(x[0]) for x in d], "twice"),
            (lambda d: [x.pop() for x in d], None),
        ],
    )
    def test_invalid_input(self, change, match):
        rng = np.random.default_rng(3)
        data = [list(x) for x in _simulate(rng, n_t=3, n_dup=0)]
        change(data)
        if match is None:
            core = complete_cases(*data)
            assert len(core.dropped_targets) == 1
        else:
            with pytest.raises(ValueError, match=match):
                complete_cases(*data)

    def test_unequal_sessions_raise(self):
        with pytest.raises(ValueError, match="sessions"):
            complete_cases(["a", "a", "b"], [1, 2, 1], ["x", "x", "x"], [1.0, 2.0, 3.0])

    def test_lengths_must_match(self):
        with pytest.raises(ValueError, match="length"):
            complete_cases(["a"], [1, 2], ["x"], [1.0])


class TestVarianceComponents:
    def test_annotator_df_is_two(self):
        vc = variance_components(complete_cases(*_simulate(np.random.default_rng(4))))
        assert vc.df["annotator"] == 2
        assert vc.df == {
            "annotator": 2,
            "session": 6,
            "target": 29,
            "annotator_x_target": 58,
            "residual": 174,
            "pure_error": 54,
        }

    def test_moment_equations(self):
        vc = variance_components(complete_cases(*_simulate(np.random.default_rng(5))))
        ms, c = vc.mean_squares, vc.components
        assert c["residual"] == pytest.approx(ms["residual"])
        assert c["session"] == pytest.approx((ms["session"] - ms["residual"]) / 30)
        assert c["annotator_x_target"] == pytest.approx(
            (ms["annotator_x_target"] - ms["residual"]) / 3
        )
        assert c["annotator"] == pytest.approx(
            (ms["annotator"] - ms["session"] - ms["annotator_x_target"] + ms["residual"]) / 90
        )
        assert c["pure_error"] == pytest.approx(ms["pure_error"])
        assert c["session_x_target"] == pytest.approx(ms["residual"] - ms["pure_error"])

    def test_without_duplicates(self):
        vc = variance_components(complete_cases(*_simulate(np.random.default_rng(6), n_dup=0)))
        assert "pure_error" not in vc.components and "pure_error" not in vc.df

    def test_unbiased_and_beta_t_recovered(self):
        """sigma^2_bt (twice sigma^2_e) is recovered, and every component is unbiased."""
        rng = np.random.default_rng(7)
        draws = [
            variance_components(complete_cases(*_simulate(rng))).components for _ in range(400)
        ]
        for name in ("session", "annotator_x_target", "session_x_target", "pure_error"):
            values = np.array([d[name] for d in draws])
            se = values.std(ddof=1) / np.sqrt(len(values))
            assert abs(values.mean() - TRUE[name]) < 4 * se, name
        bt = np.array([d["session_x_target"] for d in draws])
        assert bt.mean() == pytest.approx(2 * TRUE["pure_error"], rel=0.1)

    def test_needs_two_targets(self):
        rng = np.random.default_rng(8)
        ann, ses, tgt, val, _ = _simulate(rng, n_t=1, n_dup=0)
        with pytest.raises(ValueError, match="targets"):
            variance_components(complete_cases(ann, ses, tgt, val))


class TestSigmaEffective:
    def test_coefficients_on_the_mean_squares(self):
        vc = variance_components(complete_cases(*_simulate(np.random.default_rng(9))))
        eff = sigma_effective(vc)
        assert eff.coefficients == pytest.approx(
            {"session": 1 / 30, "annotator_x_target": 1 / 3, "residual": 1 - 1 / 30 - 1 / 3}
        )
        c = vc.components
        assert eff.variance == pytest.approx(c["session"] + c["annotator_x_target"] + c["residual"])
        assert eff.sd == pytest.approx(np.sqrt(eff.variance))
        assert eff.variance_ci[0] < eff.variance < eff.variance_ci[1]
        assert eff.sd_ci == pytest.approx(tuple(np.sqrt(eff.variance_ci)))

    def test_general_design_sizes(self):
        vc = variance_components(
            complete_cases(*_simulate(np.random.default_rng(10), n_a=4, n_s=2, n_t=12, n_dup=0))
        )
        eff = sigma_effective(vc)
        assert eff.coefficients == pytest.approx(
            {"session": 1 / 12, "annotator_x_target": 1 / 2, "residual": 1 - 1 / 12 - 1 / 2}
        )

    def test_regression_guard_pure_error(self):
        """sigma_eff includes sigma^2_bt: computing it from the duplicates' pure error fails."""
        rng = np.random.default_rng(11)
        est = np.array(
            [
                sigma_effective(variance_components(complete_cases(*_simulate(rng)))).variance
                for _ in range(300)
            ]
        )
        se = est.std(ddof=1) / np.sqrt(len(est))
        truth = _sigma_eff_true()
        pure_error_version = truth - TRUE["session_x_target"]
        assert abs(est.mean() - truth) < 4 * se
        assert abs(est.mean() - pure_error_version) > 10 * se
        vc = variance_components(complete_cases(*_simulate(rng)))
        assert "pure_error" not in sigma_effective(vc).coefficients


class TestMLSInterval:
    @pytest.mark.parametrize(("ms", "df"), [(2.5, 6), (0.7, 174), (11.0, 2)])
    def test_single_mean_square_is_the_exact_chi_square_interval(self, ms, df):
        lo, hi = mls_interval([1.0], [ms], [df], confidence=0.95)
        assert lo == pytest.approx(df * ms / chi2.ppf(0.975, df), rel=1e-12)
        assert hi == pytest.approx(df * ms / chi2.ppf(0.025, df), rel=1e-12)

    def test_graybill_wang_form(self):
        """Two mean squares: L and U combine G_q and H_q in quadrature."""
        c, ms, df = np.array([1 / 30, 0.6]), np.array([3.0, 1.2]), np.array([6, 174])
        g = 1 - df / chi2.ppf(0.95, df)
        h = df / chi2.ppf(0.05, df) - 1
        est = float(c @ ms)
        lo, hi = mls_interval(c, ms, df, confidence=0.90)
        assert lo == pytest.approx(est - np.sqrt(np.sum((g * c * ms) ** 2)), rel=1e-12)
        assert hi == pytest.approx(est + np.sqrt(np.sum((h * c * ms) ** 2)), rel=1e-12)
        assert 0 < lo < est < hi

    def test_zero_coefficient_is_ignored(self):
        assert mls_interval([1.0, 0.0], [2.0, 50.0], [10, 3]) == pytest.approx(
            mls_interval([1.0], [2.0], [10])
        )

    @pytest.mark.parametrize(
        ("c", "ms", "df", "conf"),
        [
            ([-1.0, 1.0], [1.0, 1.0], [3, 3], 0.95),
            ([1.0], [-1.0], [3], 0.95),
            ([1.0], [1.0], [0], 0.95),
            ([1.0, 1.0], [1.0], [3], 0.95),
            ([1.0], [1.0], [3], 1.0),
        ],
    )
    def test_invalid(self, c, ms, df, conf):
        with pytest.raises(ValueError):
            mls_interval(c, ms, df, confidence=conf)


class TestBootstrapDerived:
    def test_linear_function_matches_the_analytic_spread(self):
        vc = variance_components(complete_cases(*_simulate(np.random.default_rng(12))))
        eff = sigma_effective(vc)
        res = bootstrap_derived(lambda c: c["effective"], vc, n_boot=20000, seed=1)
        analytic = np.sqrt(
            sum((k * vc.mean_squares[q]) ** 2 * 2 / vc.df[q] for q, k in eff.coefficients.items())
        )
        assert res.estimate == pytest.approx(eff.variance)
        assert res.std_error == pytest.approx(analytic, rel=0.03)
        assert res.ci[0] < res.estimate < res.ci[1]

    def test_reproducible_and_two_fits(self):
        rng = np.random.default_rng(13)
        z = variance_components(complete_cases(*_simulate(rng)))
        m = variance_components(complete_cases(*_simulate(rng)))

        def ratio(cz, cm):
            return np.sqrt(cm["effective"] / cz["effective"])

        a = bootstrap_derived(ratio, z, m, n_boot=500, seed=3)
        b = bootstrap_derived(ratio, z, m, n_boot=500, seed=3)
        assert a == b
        assert a.estimate == pytest.approx(
            np.sqrt(sigma_effective(m).variance / sigma_effective(z).variance)
        )

    def test_non_finite_values_raise(self):
        vc = variance_components(complete_cases(*_simulate(np.random.default_rng(14))))
        with pytest.raises(ValueError, match="finite"):
            bootstrap_derived(lambda c: np.nan, vc, n_boot=10)

    def test_needs_a_fit(self):
        with pytest.raises(ValueError):
            bootstrap_derived(lambda: 0.0)


@pytest.mark.slow
class TestSimulationProperties:
    def test_complete_core_unbiased_with_15_percent_skips(self):
        rng = np.random.default_rng(20)
        est = []
        while len(est) < 2000:
            ann, ses, tgt, val, pres = _simulate(rng)
            keep = rng.random(len(val)) >= 0.15
            data = ([x[i] for i in np.flatnonzero(keep)] for x in (ann, ses, tgt, val, pres))
            core = complete_cases(*data)
            if len(core.targets) >= 2:
                est.append(sigma_effective(variance_components(core)).variance)
        est = np.array(est)
        se = est.std(ddof=1) / np.sqrt(len(est))
        assert abs(est.mean() - _sigma_eff_true()) < 4 * se

    def test_mls_coverage(self):
        rng = np.random.default_rng(21)
        truth = _sigma_eff_true()
        hits = [
            lo <= truth <= hi
            for lo, hi in (
                sigma_effective(variance_components(complete_cases(*_simulate(rng)))).variance_ci
                for _ in range(2000)
            )
        ]
        coverage = float(np.mean(hits))
        mc_se = np.sqrt(coverage * (1 - coverage) / len(hits))
        print(f"MLS coverage {coverage:.3f} (MC SE {mc_se:.3f}, {len(hits)} simulations)")
        assert 0.93 <= coverage <= 0.97


class TestEdgeCases:
    def test_to_dict(self):
        vc = variance_components(complete_cases(*_simulate(np.random.default_rng(15))))
        assert vc.to_dict()["df"]["annotator"] == 2
        assert sigma_effective(vc).to_dict()["coefficients"]["residual"] == pytest.approx(
            1 - 1 / 30 - 1 / 3
        )

    def test_mls_rejects_non_finite(self):
        with pytest.raises(ValueError, match="finite"):
            mls_interval([1.0], [np.inf], [3])

    @pytest.mark.parametrize(("n_boot", "conf"), [(1, 0.95), (10.0, 0.95), (True, 0.95), (10, 0.0)])
    def test_bootstrap_arguments(self, n_boot, conf):
        vc = variance_components(complete_cases(*_simulate(np.random.default_rng(16))))
        with pytest.raises(ValueError):
            bootstrap_derived(lambda c: c["effective"], vc, n_boot=n_boot, confidence=conf)
