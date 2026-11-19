"""Tests for :mod:`sarcostat.core._interpretation`.

Interpreters expose two extension seams: a custom effect-size band scale
(Rayleigh) and a custom significance level (both). The tests verify that
substituting either changes the labels predictably and that the default
behaviour matches a hand-computed table of expected outputs.
"""

from __future__ import annotations

import math

import pytest

from sarcostat.core._interpretation import (
    DEFAULT_EFFECT_BANDS,
    EffectSizeBand,
    RayleighInterpreter,
    VTestInterpreter,
    interpret_rayleigh_test,
    interpret_v_test,
)


class TestRayleighInterpreterDefault:
    """Behaviour of the default :class:`RayleighInterpreter`."""

    @pytest.mark.parametrize(
        "z_stat, n, expected_label",
        [
            (0.04, 100, "negligible"),
            (1.0, 25, "weak"),
            (5.0, 25, "moderate"),
            (12.0, 25, "strong"),
            (24.0, 25, "very strong"),
        ],
    )
    def test_effect_label_matches_band(self, z_stat: float, n: int, expected_label: str) -> None:
        """Effect labels match the Cohen-style bands.

        R is recovered from Z and n via ``R = sqrt(Z / n)``; the band
        boundaries 0.2, 0.4, 0.6, 0.8 partition R into five labels.
        """
        out = interpret_rayleigh_test(z_stat=z_stat, p_value=0.001, n=n)
        assert out["effect_description"] == expected_label

    def test_significance_threshold(self) -> None:
        """``p < 0.05`` flips the ``significant`` flag."""
        sig = interpret_rayleigh_test(z_stat=5.0, p_value=0.01, n=25)
        not_sig = interpret_rayleigh_test(z_stat=5.0, p_value=0.5, n=25)
        assert sig["significant"] is True
        assert not_sig["significant"] is False

    def test_zero_n_yields_zero_effect(self) -> None:
        """A zero sample size collapses the effect size to zero."""
        out = interpret_rayleigh_test(z_stat=0.0, p_value=1.0, n=0)
        assert out["effect_size"] == 0.0
        assert out["effect_description"] == "negligible"

    def test_recovered_mvl_matches_formula(self) -> None:
        """The recovered R equals ``sqrt(Z / n)`` to within float precision."""
        out = interpret_rayleigh_test(z_stat=12.5, p_value=0.001, n=50)
        assert math.isclose(out["effect_size"], math.sqrt(12.5 / 50))


class TestRayleighInterpreterCustom:
    """Custom :class:`RayleighInterpreter` configurations."""

    def test_custom_bands_change_label(self) -> None:
        """A coarser band scale produces different labels at the same R."""
        bands = (
            EffectSizeBand(0.5, "small"),
            EffectSizeBand(float("inf"), "large"),
        )
        interpreter = RayleighInterpreter(bands=bands)
        out = interpreter.interpret(z_stat=10.0, p_value=0.01, n=25)
        assert out["effect_description"] == "large"

    def test_custom_significance_level(self) -> None:
        """Tightening alpha can flip a previously-significant call."""
        strict = RayleighInterpreter(significance_level=0.001)
        out = strict.interpret(z_stat=5.0, p_value=0.01, n=25)
        assert out["significant"] is False

    def test_default_bands_are_immutable(self) -> None:
        """The default band tuple is frozen and shareable."""
        assert isinstance(DEFAULT_EFFECT_BANDS, tuple)
        assert all(isinstance(b, EffectSizeBand) for b in DEFAULT_EFFECT_BANDS)


class TestVTestInterpreter:
    """Behaviour of :class:`VTestInterpreter`."""

    def test_significance_threshold(self) -> None:
        """``p < 0.05`` flips the ``significant`` flag."""
        sig = interpret_v_test(v_stat=2.0, p_value=0.01, expected_dir=0.0)
        not_sig = interpret_v_test(v_stat=2.0, p_value=0.5, expected_dir=0.0)
        assert sig["significant"] is True
        assert not_sig["significant"] is False

    def test_expected_direction_in_degrees(self) -> None:
        """The output reports the expected direction in degrees."""
        out = interpret_v_test(v_stat=1.0, p_value=0.5, expected_dir=math.pi / 2)
        assert math.isclose(out["expected_direction_deg"], 90.0)

    def test_custom_significance_level(self) -> None:
        """Custom alpha is honoured by the interpreter instance."""
        relaxed = VTestInterpreter(significance_level=0.5)
        out = relaxed.interpret(v_stat=1.0, p_value=0.4, expected_dir=0.0)
        assert out["significant"] is True

    def test_text_matches_one_sided_alternative(self) -> None:
        """Angles at ~90 deg tested against 90 deg are concentrated *around* it.

        The V-test alternative is concentration around the expected direction,
        so a significant result must not be described as "differs from".
        """
        from sarcostat.core.circular_stats import perform_v_test

        angles = [math.radians(a) for a in (85, 88, 90, 91, 93, 95, 87, 89)]
        v_stat, p_val = perform_v_test(angles, expected_dir=math.pi / 2)
        text = interpret_v_test(v_stat, p_val, math.pi / 2)["interpretation"]
        assert p_val < 0.05
        assert "concentrated around the expected direction" in text
        assert "differs" not in text

        v_stat, p_val = perform_v_test(angles, expected_dir=-math.pi / 2)
        text = interpret_v_test(v_stat, p_val, -math.pi / 2)["interpretation"]
        assert p_val > 0.05
        assert "no evidence" in text
        assert "consistent with" not in text


class TestInterpreterContract:
    """Cross-cutting contract: keys present, types are JSON-friendly."""

    def test_rayleigh_keys(self) -> None:
        """Default Rayleigh output has the documented keys."""
        out = interpret_rayleigh_test(z_stat=5.0, p_value=0.01, n=25)
        expected_keys = {
            "significant",
            "p_value",
            "z_statistic",
            "effect_size",
            "effect_description",
            "n",
            "interpretation",
            "recommendation",
        }
        assert expected_keys.issubset(out.keys())

    def test_v_test_keys(self) -> None:
        """Default V-test output has the documented keys."""
        out = interpret_v_test(v_stat=2.0, p_value=0.01, expected_dir=0.0)
        expected_keys = {
            "significant",
            "p_value",
            "v_statistic",
            "expected_direction_rad",
            "expected_direction_deg",
            "interpretation",
        }
        assert expected_keys.issubset(out.keys())
