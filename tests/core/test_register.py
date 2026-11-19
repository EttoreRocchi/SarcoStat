"""
Unit tests for core.register module.

Two parallel myofibrils are built from Z-line positions along an axis;
the register of the second relative to the first is checked on exact
geometric cases and on seeded simulations of coupled and drifting
myofibrils.
"""

import numpy as np
import pytest

from sarcostat.core.register import (
    expected_register_correlation,
    register_decay_length,
    z_line_register,
)

L = 1638.0


def _zlines(positions, lateral, angle=0.0, half_width=200.0):
    """Z-lines perpendicular to an axis at ``angle``, centred ``lateral`` across it."""
    u = np.array([np.cos(angle), np.sin(angle)])
    n = np.array([-u[1], u[0]])
    lines = []
    for s in positions:
        c = s * u + lateral * n
        lines.append((tuple(c - half_width * n), tuple(c + half_width * n)))
    return lines


def _positions(n, start=0.0):
    return start + L * np.arange(n)


class TestExactGeometry:
    """Deterministic cases."""

    def test_perfect_register(self):
        reg = z_line_register(_zlines(_positions(8), 0.0), _zlines(_positions(8), 600.0))
        assert reg.n_pairs == 8
        assert reg.register_index == pytest.approx(1.0)
        assert reg.h_mean == pytest.approx(0.0, abs=1e-9)
        assert reg.register_spread == pytest.approx(0.0, abs=1e-6)
        assert reg.lateral_distance == pytest.approx(600.0)
        assert reg.h_unregistered == pytest.approx(L / 4)

    def test_quarter_stagger(self):
        a = _zlines(_positions(8), 0.0)
        b = _zlines(_positions(7, start=L / 4), 600.0)
        reg = z_line_register(a, b)
        assert reg.mean_phase == pytest.approx(np.pi / 2)
        assert reg.h_mean == pytest.approx(L / 4)
        assert reg.register_index == pytest.approx(1.0)

    def test_half_stagger_wraps(self):
        a = _zlines(_positions(8), 0.0)
        b = _zlines(_positions(7, start=L / 2 + 1e-6), 600.0)
        reg = z_line_register(a, b)
        assert abs(reg.mean_phase) == pytest.approx(np.pi, abs=1e-6)
        assert reg.register_index == pytest.approx(1.0)

    def test_rotation_and_order_invariance(self):
        a = _zlines(_positions(8), 0.0)
        b = _zlines(_positions(7, start=300.0), 600.0)
        ref = z_line_register(a, b)
        a_rot = _zlines(_positions(8), 0.0, angle=0.6)
        b_rot = _zlines(_positions(7, start=300.0), 600.0, angle=0.6)[::-1]
        rot = z_line_register(a_rot[::-1], b_rot)
        np.testing.assert_allclose(rot.phases, ref.phases, atol=1e-9)
        assert rot.h_mean == pytest.approx(ref.h_mean)

    def test_pixel_scale(self):
        a = _zlines(_positions(6) / 2.0, 0.0)
        b = _zlines(_positions(6, start=200.0) / 2.0, 300.0)
        reg = z_line_register(a, b, pixel_to_unit=2.0, unit="nm")
        assert reg.h_mean == pytest.approx(200.0)
        assert reg.unit == "nm"

    def test_z_lines_outside_span_are_dropped(self):
        a = _zlines(_positions(5), 0.0)
        b = _zlines(np.concatenate([_positions(5), [10 * L]]), 600.0)
        assert z_line_register(a, b).n_pairs == 5

    def test_invalid_inputs(self):
        with pytest.raises(ValueError):
            z_line_register(_zlines([0.0], 0.0), _zlines([0.0], 600.0))
        with pytest.raises(ValueError):
            z_line_register(_zlines(_positions(4), 0.0), _zlines([50 * L], 600.0))
        with pytest.raises(ValueError):
            z_line_register(
                _zlines(_positions(4), 0.0), _zlines(_positions(4), 600.0), pixel_to_unit=0
            )


class TestStatisticalBehaviour:
    """Coupled vs drifting myofibrils."""

    def test_theory_helpers(self):
        assert register_decay_length(0.07) == pytest.approx(1 / (2 * np.pi * 0.07) ** 2)
        assert np.isinf(register_decay_length(0.0))
        assert expected_register_correlation(np.array([0]), 0.1)[0] == pytest.approx(1.0)

    def test_drift_detected_and_coupling_not(self):
        rng = np.random.default_rng(0)
        n, cv = 30, 0.07
        lengths_a = rng.normal(L, cv * L, n)
        pos_a = np.concatenate([[0.0], np.cumsum(lengths_a)])
        drift = np.concatenate([[0.0], np.cumsum(rng.normal(L, cv * L, n))])
        coupled = pos_a + rng.normal(0.0, 150.0, pos_a.size)
        reg_drift = z_line_register(_zlines(pos_a, 0.0), _zlines(drift, 600.0))
        reg_coupled = z_line_register(_zlines(pos_a, 0.0), _zlines(coupled, 600.0))
        assert reg_drift.serial_dependence_p < 0.05
        assert reg_coupled.serial_dependence_p > 0.05
        assert reg_coupled.v_p_value < 0.01

    def test_correlation_matches_theory_for_drift(self):
        rng = np.random.default_rng(1)
        cv, n, reps = 0.07, 12, 600
        g1 = []
        for _ in range(reps):
            pos_a = np.concatenate([[0.0], np.cumsum(rng.normal(L, cv * L, n))])
            pos_b = np.concatenate([[0.0], np.cumsum(rng.normal(L, cv * L, n))])
            reg = z_line_register(
                _zlines(pos_a, 0.0), _zlines(pos_b, 600.0), n_permutations=1, seed=0
            )
            g1.append(reg.correlation_values[0])
        assert np.mean(g1) == pytest.approx(
            expected_register_correlation(np.array([1]), cv)[0], abs=0.05
        )
