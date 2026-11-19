"""Interpreters for circular-statistics test results.

The two extensible interpreters in this module turn raw Rayleigh-test and
V-test outputs into the human-readable bundles consumed by reports and
GUIs. Each interpreter is parametrized by a thresholds dataclass at
construction time, so callers can substitute their own effect-size scale
without monkey-patching the analysis layer (Dependency Inversion: the
high-level analysis depends on the abstract interpreter contract, not on
hard-coded thresholds inside :mod:`sarcostat.core.circular_stats`).

Module-level functions :func:`interpret_rayleigh_test` and
:func:`interpret_v_test` retain the same input/output shape used elsewhere
in the codebase; they delegate to the default interpreter instances.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

SIGNIFICANCE_LEVEL = 0.05


@dataclass(frozen=True)
class EffectSizeBand:
    """One row of an ordered effect-size scale.

    Attributes
    ----------
    upper_bound : float
        Exclusive upper bound on the magnitude (mean vector length) for
        which this band applies: the first band whose ``upper_bound``
        is strictly greater than the observed value wins, so a value
        exactly on a bound gets the next band's label. Bands are
        ordered from smallest to largest.
    label : str
        Short adjective (``"weak"``, ``"strong"``, ...).
    """

    upper_bound: float
    label: str


DEFAULT_EFFECT_BANDS: tuple[EffectSizeBand, ...] = (
    EffectSizeBand(0.2, "negligible"),
    EffectSizeBand(0.4, "weak"),
    EffectSizeBand(0.6, "moderate"),
    EffectSizeBand(0.8, "strong"),
    EffectSizeBand(float("inf"), "very strong"),
)


def _label_from_bands(value: float, bands: Sequence[EffectSizeBand]) -> str:
    """Resolve an effect-size label by walking an ordered band sequence."""
    for band in bands:
        if value < band.upper_bound:
            return band.label
    return bands[-1].label


@dataclass(frozen=True)
class RayleighInterpreter:
    """Convert Rayleigh-test outputs to a labelled interpretation bundle.

    The Rayleigh test detects a single mean direction in circular data.
    This interpreter recovers the mean vector length R from the test's
    Z statistic and sample size (R = sqrt(Z / n)) and labels the
    associated effect size against an ordered band scale.

    Parameters
    ----------
    bands : sequence of EffectSizeBand, optional
        Ordered scale (small to large) used to label R. Defaults to
        :data:`DEFAULT_EFFECT_BANDS` (Cohen-style: <0.2 negligible, ...,
        >=0.8 very strong).
    significance_level : float, optional
        Alpha used to flag a result as significant (default 0.05).
    """

    bands: tuple[EffectSizeBand, ...] = DEFAULT_EFFECT_BANDS
    significance_level: float = SIGNIFICANCE_LEVEL

    def effect_label(self, mean_vector_length: float) -> str:
        """Return the effect-size adjective for a given mean vector length."""
        return _label_from_bands(mean_vector_length, self.bands)

    def interpret(self, z_stat: float, p_value: float, n: int) -> dict[str, Any]:
        """Turn raw Rayleigh outputs into a labelled bundle.

        Parameters
        ----------
        z_stat : float
            Rayleigh Z statistic.
        p_value : float
            p-value of the test against uniformity.
        n : int
            Sample size used to compute Z.

        Returns
        -------
        dict
            Keys: ``significant``, ``p_value``, ``z_statistic``,
            ``effect_size`` (R recovered from Z and n),
            ``effect_description``, ``n``, ``interpretation``,
            ``recommendation``.
        """
        if n > 0 and z_stat >= 0:
            mvl = float(np.sqrt(z_stat / n))
        else:
            mvl = 0.0

        significant = p_value < self.significance_level
        label = self.effect_label(mvl)

        if significant:
            interpretation = (
                f"The Rayleigh test is significant (p = {p_value:.4f}), indicating that "
                f"the angles are NOT uniformly distributed. There is evidence of directional "
                f"preference with {label} concentration (R = {mvl:.3f})."
            )
            recommendation = "The data shows significant angular clustering."
        else:
            interpretation = (
                f"The Rayleigh test is not significant (p = {p_value:.4f}), suggesting that "
                f"the angles may be uniformly distributed with no clear directional preference."
            )
            recommendation = "No significant deviation from uniform circular distribution."

        return {
            "significant": significant,
            "p_value": p_value,
            "z_statistic": z_stat,
            "effect_size": mvl,
            "effect_description": label,
            "n": n,
            "interpretation": interpretation,
            "recommendation": recommendation,
        }


@dataclass(frozen=True)
class VTestInterpreter:
    """Convert V-test outputs to a labelled interpretation bundle.

    The V-test refines the Rayleigh test with a one-sided alternative:
    the angles are concentrated *around* a hypothesised mean direction.
    A significant result is evidence for that concentration; a
    non-significant one only means that uniformity could not be
    rejected in favour of it (it is not evidence that the mean direction
    matches, nor that it differs).

    Parameters
    ----------
    significance_level : float, optional
        One-sided alpha (default 0.05).
    """

    significance_level: float = SIGNIFICANCE_LEVEL

    def interpret(
        self,
        v_stat: float,
        p_value: float,
        expected_dir: float,
    ) -> dict[str, Any]:
        """Turn raw V-test outputs into a labelled bundle.

        Parameters
        ----------
        v_stat : float
            V statistic.
        p_value : float
            One-sided p-value.
        expected_dir : float
            Expected mean direction (radians).

        Returns
        -------
        dict
            Keys: ``significant``, ``p_value``, ``v_statistic``,
            ``expected_direction_rad``, ``expected_direction_deg``,
            ``interpretation``.
        """
        significant = p_value < self.significance_level
        expected_deg = float(np.degrees(expected_dir))

        if significant:
            interpretation = (
                f"The V-test is significant (p = {p_value:.4f}), indicating that "
                f"the angles are concentrated around the expected direction "
                f"of {expected_deg:.1f} deg."
            )
        else:
            interpretation = (
                f"The V-test is not significant (p = {p_value:.4f}): there is no "
                f"evidence that the angles are concentrated around the expected "
                f"direction of {expected_deg:.1f} deg (uniformity is not rejected)."
            )

        return {
            "significant": significant,
            "p_value": p_value,
            "v_statistic": v_stat,
            "expected_direction_rad": expected_dir,
            "expected_direction_deg": expected_deg,
            "interpretation": interpretation,
        }


_DEFAULT_RAYLEIGH = RayleighInterpreter()
_DEFAULT_V_TEST = VTestInterpreter()


def interpret_rayleigh_test(z_stat: float, p_value: float, n: int) -> dict[str, Any]:
    """Interpret Rayleigh-test results using the default scale.

    Thin wrapper around :class:`RayleighInterpreter` with the default
    Cohen-style effect-size bands. Construct a custom interpreter
    directly to use other thresholds.

    Parameters
    ----------
    z_stat : float
        Rayleigh Z statistic.
    p_value : float
        p-value of the test against uniformity.
    n : int
        Sample size.

    Returns
    -------
    dict
        See :meth:`RayleighInterpreter.interpret`.
    """
    return _DEFAULT_RAYLEIGH.interpret(z_stat, p_value, n)


def interpret_v_test(v_stat: float, p_value: float, expected_dir: float) -> dict[str, Any]:
    """Interpret V-test results using the default significance level.

    Parameters
    ----------
    v_stat : float
        V statistic.
    p_value : float
        One-sided p-value.
    expected_dir : float
        Expected mean direction (radians).

    Returns
    -------
    dict
        See :meth:`VTestInterpreter.interpret`.
    """
    return _DEFAULT_V_TEST.interpret(v_stat, p_value, expected_dir)


__all__ = [
    "SIGNIFICANCE_LEVEL",
    "EffectSizeBand",
    "DEFAULT_EFFECT_BANDS",
    "RayleighInterpreter",
    "VTestInterpreter",
    "interpret_rayleigh_test",
    "interpret_v_test",
]
