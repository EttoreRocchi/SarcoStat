"""Tests for the chain Monte Carlo simulator after the Phase 2C decomposition.

The chain pipeline now lives in :mod:`sarcostat.simulation.chain`. This
file exercises the chain-specific config validators, the
:class:`ChainSimulator` (with the strategies derived from
:meth:`ChainSimulator.from_config`), and the deterministic seeding of
:func:`run_monte_carlo`. Sampler-only and run-artifact concerns live in
``test_distributions.py`` and ``test_artifacts.py``.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import numpy as np
import pytest

from sarcostat.simulation.chain import (
    ChainSimulator,
    Config,
    TrialOutcome,
    load_config,
    run_monte_carlo,
)

_VALID_YAML = textwrap.dedent(
    """
    n_simulations: 50
    seed: 42
    n_processes: 1
    n_sarcomeres: [5, 10]
    kappa_values: [1, 5]
    length_distribution: truncnorm
    length_mean: 1.0
    length_std: 0.25
    length_min: 0.1
    length_max: 2.0
    angular_distribution: vonmises
    angular_mean: 0.0
    angular_correlation: 0.0
    enable_persistence: false
    persistence_decay: 0.9
    enable_curvature_bias: false
    curvature_strength: 0.1
    output_dir: test_output
    save_raw_data: false
    save_figures: false
    figure_dpi: 100
    """
).strip()


def _write_yaml(tmp_path: Path, body: str) -> Path:
    """Write a YAML config file and return its path."""
    target = tmp_path / "config.yaml"
    target.write_text(body)
    return target


def _basic_config() -> Config:
    """Build a small but valid :class:`Config` for unit tests."""
    return Config(
        n_simulations=10,
        seed=42,
        n_processes=1,
        n_sarcomeres=[5],
        kappa_values=[5],
        length_distribution="truncnorm",
        length_mean=1.0,
        length_std=0.25,
        length_min=0.5,
        length_max=1.5,
        angular_distribution="vonmises",
        angular_mean=0.0,
        angular_correlation=0.0,
        enable_persistence=False,
        persistence_decay=0.9,
        enable_curvature_bias=False,
        curvature_strength=0.1,
        output_dir="test",
        save_raw_data=False,
        save_figures=False,
        figure_dpi=100,
    )


class TestConfigLoading:
    """Tests for :func:`load_config` and the :class:`Config` validators."""

    def test_load_valid_config(self, tmp_path: Path) -> None:
        """A valid YAML round-trips into the matching dataclass fields."""
        cfg = load_config(_write_yaml(tmp_path, _VALID_YAML))
        assert cfg.n_simulations == 50
        assert cfg.seed == 42
        assert cfg.kappa_values == [1, 5]
        assert cfg.length_distribution == "truncnorm"

    def test_zero_kappa_with_correlation_warns(self, tmp_path: Path) -> None:
        """A nonzero correlation paired with kappa=0 emits a UserWarning."""
        body = _VALID_YAML.replace("kappa_values: [1, 5]", "kappa_values: [0, 1]").replace(
            "angular_correlation: 0.0", "angular_correlation: 0.5"
        )
        with pytest.warns(UserWarning, match="kappa=0"):
            load_config(_write_yaml(tmp_path, body))

    def test_unknown_length_distribution_raises(self, tmp_path: Path) -> None:
        """A length distribution not in the registry is rejected by the loader.

        Gamma was removed in this refactor; the loader must surface the
        change as an immediate ValueError rather than dispatching to a
        silent fallback.
        """
        body = _VALID_YAML.replace("length_distribution: truncnorm", "length_distribution: gamma")
        with pytest.raises(ValueError, match="length_distribution"):
            load_config(_write_yaml(tmp_path, body))

    def test_negative_kappa_raises(self, tmp_path: Path) -> None:
        """Negative kappa is rejected by the range validator."""
        body = _VALID_YAML.replace("kappa_values: [1, 5]", "kappa_values: [-1, 1]")
        with pytest.raises(ValueError, match="kappa_values"):
            load_config(_write_yaml(tmp_path, body))

    def test_angular_correlation_out_of_range(self, tmp_path: Path) -> None:
        """``angular_correlation`` outside [0, 1) is rejected."""
        body = _VALID_YAML.replace("angular_correlation: 0.0", "angular_correlation: 1.5")
        with pytest.raises(ValueError, match="angular_correlation"):
            load_config(_write_yaml(tmp_path, body))


class TestChainSimulator:
    """Tests for :class:`ChainSimulator` constructed via :meth:`from_config`."""

    def test_from_config_builds_strategies(self) -> None:
        """``from_config`` resolves the named samplers from the registry."""
        sim = ChainSimulator.from_config(_basic_config())
        assert sim.length_sampler is not None
        assert sim.angle_sampler is not None
        assert sim.dynamics is not None

    def test_simulate_trial_returns_summary(self) -> None:
        """A single trial produces a :class:`TrialOutcome` with valid ranges."""
        sim = ChainSimulator.from_config(_basic_config())
        rng = np.random.default_rng(42)
        outcome = sim.simulate_trial(n=5, kappa=5.0, rng=rng)
        assert isinstance(outcome, TrialOutcome)
        assert outcome.angles.shape == (5,)
        assert 0.0 <= outcome.linearity_ratio <= 1.0
        assert 0.0 <= outcome.mean_vector_length <= 1.0
        assert outcome.rayleigh_z >= 0.0

    def test_simulate_trial_is_seed_deterministic(self) -> None:
        """Two simulators with the same seed produce identical trials."""
        sim_a = ChainSimulator.from_config(_basic_config())
        sim_b = ChainSimulator.from_config(_basic_config())
        rng_a = np.random.default_rng(123)
        rng_b = np.random.default_rng(123)
        out_a = sim_a.simulate_trial(n=10, kappa=3.0, rng=rng_a)
        out_b = sim_b.simulate_trial(n=10, kappa=3.0, rng=rng_b)
        np.testing.assert_allclose(out_a.angles, out_b.angles)
        assert out_a.linearity_ratio == pytest.approx(out_b.linearity_ratio)


class TestRunMonteCarlo:
    """Tests for :func:`run_monte_carlo` (in-process, deterministic)."""

    def test_returns_full_metric_grid(self) -> None:
        """The result dict has one entry per metric, kappa, and chain length."""
        cfg = _basic_config()
        sim = ChainSimulator.from_config(cfg)
        results = run_monte_carlo(sim, cfg)
        for metric in (
            "linearity_ratio",
            "mean_vector_length",
            "rayleigh_z",
            "rayleigh_p",
            "v_statistic",
            "v_p_value",
            "angles",
        ):
            assert metric in results
            for k in cfg.kappa_values:
                for n in cfg.n_sarcomeres:
                    assert isinstance(results[metric][k][n], np.ndarray)

    def test_progress_callback_fires_per_cell(self) -> None:
        """The optional progress callback is invoked once per cell."""
        cfg = _basic_config()
        cfg.kappa_values = [1, 5]
        cfg.n_sarcomeres = [5, 10]
        sim = ChainSimulator.from_config(cfg)
        calls: list[tuple[int, int]] = []
        run_monte_carlo(sim, cfg, progress=lambda d, t: calls.append((d, t)))
        assert len(calls) == 4
        assert calls[-1] == (4, 4)


class TestLinearityRatioFormula:
    """Regression guards for the iid-heading model behind manuscript eq. (4).

    For sarcomeres of equal length and von Mises iid headings, the
    second moment of the Linearity Ratio satisfies
    ``E[LR^2] = rho^2 + (1 - rho^2) / N`` with rho = I_1(kappa)/I_0(kappa).
    These tests pin down the simulator's agreement with that identity
    and prevent the historical ``cumsum`` regression from coming back.
    """

    @staticmethod
    def _equal_length_simulator(kappa: float, n_sarc: int) -> ChainSimulator:
        cfg = Config(
            n_simulations=1,
            seed=0,
            n_processes=1,
            n_sarcomeres=[n_sarc],
            kappa_values=[kappa],
            length_distribution="truncnorm",
            length_mean=1.0,
            length_std=0.0,  # collapses to mean -> exactly equal lengths
            length_min=0.0,
            length_max=2.0,
            angular_distribution="vonmises",
            angular_mean=0.0,
            angular_correlation=0.0,
            enable_persistence=False,
            persistence_decay=0.9,
            enable_curvature_bias=False,
            curvature_strength=0.0,
            output_dir="test_output",
            save_raw_data=False,
            save_figures=False,
            figure_dpi=100,
        )
        return ChainSimulator.from_config(cfg)

    def test_lr_equals_mvl_for_equal_lengths(self) -> None:
        """For equal-length chains, LR = MVL exactly (paper eq. 2 -> 3)."""
        sim = self._equal_length_simulator(kappa=2.0, n_sarc=20)
        rng = np.random.default_rng(123)
        for _ in range(20):
            out = sim.simulate_trial(20, 2.0, rng)
            assert out.linearity_ratio == pytest.approx(out.mean_vector_length, abs=1e-12)

    def test_e_lr_squared_matches_paper_formula(self) -> None:
        """E[LR^2] = rho^2 + (1 - rho^2) / N at moderate kappa."""
        from scipy.special import i0, i1

        kappa, n_sarc, n_trials = 2.0, 50, 4000
        rho = float(i1(kappa) / i0(kappa))
        predicted = rho**2 + (1.0 - rho**2) / n_sarc

        sim = self._equal_length_simulator(kappa=kappa, n_sarc=n_sarc)
        rng = np.random.default_rng(42)
        lr2 = np.array(
            [sim.simulate_trial(n_sarc, kappa, rng).linearity_ratio ** 2 for _ in range(n_trials)]
        )
        # Monte Carlo SE ~ sigma(LR^2)/sqrt(n_trials); 0.01 is comfortable.
        assert abs(lr2.mean() - predicted) < 0.01


class TestHeadingPersistence:
    """Persistence dynamics actually pulls toward the previous heading."""

    def test_high_decay_dominates_previous_heading(self) -> None:
        """With high decay each step is mostly the previous heading.

        The recursion ``phi_i = decay * phi_{i-1} + (1 - decay) * theta_i``
        means a single step deviation is at most ``(1 - decay) * pi``.
        """
        from sarcostat.simulation.chain.dynamics import _heading_persistence

        decay = 0.95
        raw = np.array([0.5, -1.2, 2.0, -0.8, 1.5])
        out = _heading_persistence(raw, decay=decay)
        # Each step deviates from the previous heading by at most (1 - decay)*pi.
        max_step = float((1.0 - decay) * np.pi)
        for i in range(1, len(out)):
            assert abs(out[i] - out[i - 1]) <= max_step + 1e-12

    def test_zero_decay_passthrough(self) -> None:
        """With decay = 0, persistence is the identity."""
        from sarcostat.simulation.chain.dynamics import _heading_persistence

        raw = np.array([0.1, -0.2, 0.5, 1.0, -1.5])
        out = _heading_persistence(raw, decay=0.0)
        np.testing.assert_allclose(out, raw)
