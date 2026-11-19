"""Portable invariants of the simulation pipelines.

Exact "golden" values would break whenever numpy/scipy change a random
stream, so these tests pin properties instead: reproducibility, serial
vs parallel equality, independence from grid order, agreement with
theory, and the absence of import-time side effects.
"""

from __future__ import annotations

import dataclasses
import logging
import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest
from scipy.special import i0e, i1e

from sarcostat.simulation._distributions import make_angle_sampler
from sarcostat.simulation.chain import ChainSimulator, Config, run_monte_carlo
from sarcostat.simulation.rigor.coverage import CoverageConfig, compute_coverage_grid
from sarcostat.simulation.rigor.mde import MDEConfig, run_morphometry_mde, run_mvl_mde
from sarcostat.simulation.sarc import (
    BoundaryClassificationPipeline,
    SarcomereGenerator,
    SingleSarcomereConfig,
    make_noise_model,
)


def _chain_config(**overrides) -> Config:
    base = {
        "n_simulations": 20,
        "seed": 11,
        "n_processes": 1,
        "n_sarcomeres": [5, 12],
        "kappa_values": [0, 2.0],
        "length_distribution": "lognorm",
        "length_mean": 1.0,
        "length_std": 0.8,
        "length_min": 0.05,
        "length_max": 10.0,
        "angular_distribution": "vonmises",
        "angular_mean": 0.0,
        "angular_correlation": 0.0,
        "enable_persistence": False,
        "persistence_decay": 0.9,
        "enable_curvature_bias": False,
        "curvature_strength": 0.1,
        "output_dir": "unused",
        "save_raw_data": False,
        "save_figures": False,
        "figure_dpi": 72,
    }
    base.update(overrides)
    return Config(**base)


def _assert_same_results(a: dict, b: dict) -> None:
    assert a.keys() == b.keys()
    for metric in a:
        for k in a[metric]:
            for n in a[metric][k]:
                np.testing.assert_array_equal(a[metric][k][n], b[metric][k][n])


class TestChainReproducibility:
    def test_rerun_is_identical(self) -> None:
        cfg = _chain_config()
        sim = ChainSimulator.from_config(cfg)
        _assert_same_results(run_monte_carlo(sim, cfg), run_monte_carlo(sim, cfg))

    def test_serial_equals_parallel(self) -> None:
        cfg = _chain_config()
        sim = ChainSimulator.from_config(cfg)
        serial = run_monte_carlo(sim, cfg)
        parallel = run_monte_carlo(sim, dataclasses.replace(cfg, n_processes=2))
        _assert_same_results(serial, parallel)

    def test_cell_independent_of_grid(self) -> None:
        cfg_full = _chain_config()
        cfg_one = _chain_config(kappa_values=[2.0], n_sarcomeres=[12])
        sim = ChainSimulator.from_config(cfg_full)
        full = run_monte_carlo(sim, cfg_full)
        one = run_monte_carlo(sim, cfg_one)
        np.testing.assert_array_equal(
            full["linearity_ratio"][2.0][12], one["linearity_ratio"][2.0][12]
        )

    def test_v_test_uses_angular_mean(self) -> None:
        """With headings centred on angular_mean, the V-test against it is significant."""
        cfg = _chain_config(angular_mean=2.0, kappa_values=[10.0], n_sarcomeres=[20])
        sim = ChainSimulator.from_config(cfg)
        assert sim.expected_direction == 2.0
        p_values = run_monte_carlo(sim, cfg)["v_p_value"][10.0][20]
        assert np.median(p_values) < 1e-3


class TestWrappedNormal:
    @pytest.mark.parametrize("kappa", [0.5, 2.0, 50.0, 800.0])
    def test_matches_von_mises_resultant(self, kappa: float) -> None:
        """The wrapped normal's mean resultant length equals I1(k)/I0(k)."""
        sampler = make_angle_sampler("wrapped_normal", mean=0.4)
        angles = sampler.sample(200_000, kappa, np.random.default_rng(0))
        mvl = np.hypot(np.cos(angles).mean(), np.sin(angles).mean())
        assert mvl == pytest.approx(i1e(kappa) / i0e(kappa), abs=5e-3)


class TestRigorSeeding:
    def test_coverage_cell_independent_of_grid_order(self) -> None:
        base = CoverageConfig(
            n_iterations=15, n_bootstrap=50, kappa_values=[1.0, 5.0], n_values=[10]
        )
        swapped = dataclasses.replace(base, kappa_values=[5.0, 1.0])
        log = logging.getLogger("test")
        a = compute_coverage_grid(base, log).set_index(["kappa", "n"])
        b = compute_coverage_grid(swapped, log).set_index(["kappa", "n"])
        assert a.loc[(5.0, 10)].equals(b.loc[(5.0, 10)])

    def test_mde_cells_independent_of_grid_order(self) -> None:
        log = logging.getLogger("test")
        base = MDEConfig(
            n_replicates_for_sd=30,
            kappa_values=[1.0, 5.0],
            chain_lengths=[10],
            group_sizes=[10],
            sarcomere_conditions=["euploid", "trisomic"],
            annotation_noise_nm=[0.0, 20.0],
            sarcomere_group_sizes=[10],
        )
        swapped = dataclasses.replace(
            base,
            kappa_values=[5.0, 1.0],
            sarcomere_conditions=["trisomic", "euploid"],
            annotation_noise_nm=[20.0, 0.0],
        )
        mvl_a = run_mvl_mde(base, log).set_index("kappa")["sigma"]
        mvl_b = run_mvl_mde(swapped, log).set_index("kappa")["sigma"]
        assert mvl_a[5.0] == mvl_b[5.0]
        key = ["metric", "condition", "annotation_noise_nm"]
        mor_a = run_morphometry_mde(base, log).set_index(key)["sigma"]
        mor_b = run_morphometry_mde(swapped, log).set_index(key)["sigma"]
        assert mor_a[("AsR", "trisomic", 20.0)] == mor_b[("AsR", "trisomic", 20.0)]

    def test_mde_noise_free_matches_population_sd_across_levels(self) -> None:
        """The same ground-truth population is reused at every noise level."""
        cfg = MDEConfig(
            n_replicates_for_sd=200,
            kappa_values=[1.0],
            chain_lengths=[10],
            group_sizes=[10],
            sarcomere_conditions=["euploid"],
            annotation_noise_nm=[0.0, 1e-9],
            sarcomere_group_sizes=[10],
        )
        df = run_morphometry_mde(cfg, logging.getLogger("test"))
        asr = df[df["metric"] == "AsR"].set_index("annotation_noise_nm")["sample_mean"]
        assert asr[0.0] == pytest.approx(asr[1e-9], rel=1e-9)


class TestSarcInvariants:
    def test_zero_noise_boundary_has_no_misclassification(self) -> None:
        cfg = SingleSarcomereConfig(
            n_simulations=1,
            noise_levels=[0.0],
            n_boundary_samples=5,
            boundary_offset_range=(-0.3, 0.3),
        )
        res = BoundaryClassificationPipeline(cfg=cfg, generator=SarcomereGenerator(cfg)).run()
        assert np.nanmax(res.misclassification_rate) == 0.0
        assert res.critical_boundary_width[0.0] == 0.0
        assert res.class_edges[1] == pytest.approx(0.05 * 1638.0 / 414.0)

    def test_anisotropic_noise_scales_with_sigma(self) -> None:
        cfg = SingleSarcomereConfig(noise_model="anisotropic")
        assert make_noise_model(cfg, 0.0).apply({"A": (1.0, 2.0)}, np.random.default_rng(0)) == {
            "A": (1.0, 2.0)
        }
        model = make_noise_model(cfg, 10.0)
        total_var = model.sigma_along**2 + model.sigma_perpendicular**2
        assert total_var == pytest.approx(2 * 10.0**2)
        assert model.sigma_perpendicular == pytest.approx(2 * model.sigma_along)


class TestNoImportSideEffects:
    def test_import_keeps_warning_filters_and_style(self) -> None:
        code = textwrap.dedent(
            """
            import warnings
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            import seaborn  # noqa: F401
            filters = list(warnings.filters)
            rc = dict(plt.rcParams)
            import sarcostat.simulation  # noqa: F401
            import sarcostat.cli.app  # noqa: F401
            assert warnings.filters == filters, "warning filters changed"
            changed = [k for k in rc if plt.rcParams[k] != rc[k]]
            assert not changed, f"rcParams changed: {changed[:5]}"
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, cwd=Path(__file__).parent
        )
        assert result.returncode == 0, result.stderr
