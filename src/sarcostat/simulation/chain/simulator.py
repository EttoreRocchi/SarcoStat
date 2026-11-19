"""Single-sequence chain simulator.

The :class:`ChainSimulator` is responsible for one Monte Carlo trial:
sample step angles, apply dynamics, sample lengths, and reduce to the
five observables (linearity ratio, mean vector length, Rayleigh test
outputs, V-test outputs). Multi-cell coordination lives in
:mod:`sarcostat.simulation.chain.runner`.

The simulator depends on three pluggable abstractions:

- a :class:`~sarcostat.simulation._distributions.LengthSampler`,
- an :class:`~sarcostat.simulation._distributions.AngleSampler`,
- an :class:`~sarcostat.simulation.chain.dynamics.AngularDynamics`.

This is the Dependency Inversion seam: the orchestrator holds
references to abstract protocols, never to concrete implementations.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from sarcostat.core.circular_stats import (
    calculate_mean_vector_length,
    perform_rayleigh_test,
    perform_v_test,
)

from .._distributions import (
    AngleSampler,
    LengthSampler,
    make_angle_sampler,
    make_length_sampler,
)
from .config import Config
from .dynamics import AngularDynamics, StandardDynamics

TWO_PI = 2.0 * np.pi


@dataclass(frozen=True)
class TrialOutcome:
    """One Monte Carlo trial reduced to its observable summaries.

    Attributes
    ----------
    angles : numpy.ndarray
        Step angles of the trial (after dynamics, wrapped to ``[0, 2*pi)``).
    linearity_ratio : float
        Head-to-tail distance divided by the total path length.
    mean_vector_length : float
        Mean resultant length R of the step-angle distribution.
    rayleigh_z, rayleigh_p : float
        Rayleigh-test outputs.
    v_statistic, v_p_value : float
        V-test outputs against the simulator's ``expected_direction``.
    """

    angles: np.ndarray
    linearity_ratio: float
    mean_vector_length: float
    rayleigh_z: float
    rayleigh_p: float
    v_statistic: float
    v_p_value: float


def _linearity_ratio(head_to_tail: float, total_length: float) -> float:
    """Linearity ratio with the trivial guard against zero length."""
    return head_to_tail / total_length if total_length > 0 else 0.0


def _displacement_summary(steps: np.ndarray, lengths: np.ndarray) -> tuple[float, float]:
    """Return ``(head_to_tail, total_length)`` from steps and lengths.

    The ``steps`` are interpreted as the absolute heading angles of the
    sarcomere unit vectors (consistent with the iid-heading model behind
    the finite-sample bias ``E[LR^2] = rho^2 + (1 - rho^2)/N``).
    """
    dx = lengths * np.cos(steps)
    dy = lengths * np.sin(steps)
    return float(np.hypot(np.sum(dx), np.sum(dy))), float(np.sum(lengths))


class ChainSimulator:
    """One-trial Monte Carlo simulator for a sarcomere chain.

    Parameters
    ----------
    length_sampler : LengthSampler
        Length-sampling strategy.
    angle_sampler : AngleSampler
        Angle-sampling strategy.
    dynamics : AngularDynamics
        Step-angle adjuster (correlation, persistence, curvature).
    expected_direction : float, optional
        Direction (radians) the V-test is run against; :meth:`from_config`
        sets it to the configured ``angular_mean``. Default 0.0.

    Notes
    -----
    Use :meth:`from_config` to build a simulator with the strategies
    derived from a :class:`Config`. Direct construction is provided so
    that tests can pass mock samplers.
    """

    def __init__(
        self,
        length_sampler: LengthSampler,
        angle_sampler: AngleSampler,
        dynamics: AngularDynamics,
        expected_direction: float = 0.0,
    ) -> None:
        self.length_sampler = length_sampler
        self.angle_sampler = angle_sampler
        self.dynamics = dynamics
        self.expected_direction = expected_direction

    @classmethod
    def from_config(cls, cfg: Config) -> ChainSimulator:
        """Build a :class:`ChainSimulator` from a :class:`Config`."""
        length_sampler = make_length_sampler(
            cfg.length_distribution,
            mean=cfg.length_mean,
            std=cfg.length_std,
            lower=cfg.length_min,
            upper=cfg.length_max,
        )
        angle_sampler = make_angle_sampler(cfg.angular_distribution, mean=cfg.angular_mean)
        dynamics = StandardDynamics(
            angular_mean=cfg.angular_mean,
            correlation=cfg.angular_correlation,
            enable_persistence=cfg.enable_persistence,
            persistence_decay=cfg.persistence_decay,
            enable_curvature=cfg.enable_curvature_bias,
            curvature_strength=cfg.curvature_strength,
            curvature_mode=cfg.curvature_mode,
        )
        return cls(length_sampler, angle_sampler, dynamics, expected_direction=cfg.angular_mean)

    def simulate_trial(self, n: int, kappa: float, rng: np.random.Generator) -> TrialOutcome:
        """Simulate a single trial and return the reduced :class:`TrialOutcome`."""
        base_steps = self.angle_sampler.sample(n, kappa, rng)
        steps = self.dynamics.apply(base_steps, rng)
        lengths = self.length_sampler.sample(n, rng)
        head_to_tail, total_length = _displacement_summary(steps, lengths)

        rayleigh_z, rayleigh_p = perform_rayleigh_test(steps)
        v_stat, v_p = perform_v_test(steps, self.expected_direction)
        return TrialOutcome(
            angles=steps,
            linearity_ratio=_linearity_ratio(head_to_tail, total_length),
            mean_vector_length=float(calculate_mean_vector_length(steps)),
            rayleigh_z=float(rayleigh_z),
            rayleigh_p=float(rayleigh_p),
            v_statistic=float(v_stat),
            v_p_value=float(v_p),
        )


__all__ = ["ChainSimulator", "TrialOutcome"]
