"""Configuration container and loader for the chain simulation.

This module centralizes the chain-simulator parameters and their YAML
loader. The :class:`Config` validators are split into focused private
methods so that no individual function exceeds CC 10.

Length distributions are now restricted to the two families registered
in :mod:`sarcostat.simulation._distributions`. The ``gamma`` and
``uniform`` length families have been removed; their YAML keys are
rejected with a clear error message.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .._distributions import ANGLE_SAMPLERS, LENGTH_SAMPLERS


def _coerce_to_list(value: Any, default: list) -> list:
    """Return ``list(value)`` or ``default`` for missing or non-iterable input."""
    if value is None:
        return list(default)
    return list(value)


@dataclass
class Config:
    """Chain simulation configuration.

    Attributes
    ----------
    n_simulations : int
        Number of Monte Carlo replicates per (kappa, n) cell.
    seed : int
        Master RNG seed; per-cell seeds are derived deterministically.
    n_processes : int
        Worker pool size for parallel execution; 1 disables multiprocessing.
    n_sarcomeres : list of int
        Chain-length axis swept during the simulation.
    kappa_values : list of float
        Concentration axis swept during the simulation.
    length_distribution : str
        Registered name from :data:`sarcostat.simulation._distributions.LENGTH_SAMPLERS`.
    length_mean, length_std, length_min, length_max : float
        Length-distribution parameters; meaning depends on the sampler.
    angular_distribution : str
        Registered name from :data:`sarcostat.simulation._distributions.ANGLE_SAMPLERS`.
    angular_mean : float
        Mean direction in radians.
    angular_correlation : float
        AR(1) lag-1 correlation applied to the step-angle series.
    enable_persistence : bool
        Whether to apply the heading-persistence dynamics.
    persistence_decay : float
        Persistence-decay factor in [0, 1).
    enable_curvature_bias : bool
        Whether to add a constant turning bias to each step.
    curvature_strength : float
        Per-step curvature offset (radians).
    output_dir : str
        Base directory used by :class:`sarcostat.simulation._artifacts.RunArtifacts`.
    save_raw_data : bool
        If True, the runner writes per-cell ``.npz`` archives.
    save_figures : bool
        If True, the standard plot suite is rendered and saved.
    figure_dpi : int
        DPI used for matplotlib output.
    """

    n_simulations: int
    seed: int
    n_processes: int
    n_sarcomeres: list[int]
    kappa_values: list[float]
    length_distribution: str
    length_mean: float
    length_std: float
    length_min: float
    length_max: float
    angular_distribution: str
    angular_mean: float
    angular_correlation: float
    enable_persistence: bool
    persistence_decay: float
    enable_curvature_bias: bool
    curvature_strength: float
    output_dir: str
    save_raw_data: bool
    save_figures: bool
    figure_dpi: int

    def __post_init__(self) -> None:
        """Run all validators after dataclass initialisation."""
        self._validate_distribution_names()
        self._validate_kappa_correlation_combo()
        self._validate_persistence_decay()
        self._validate_parameter_ranges()

    def _validate_distribution_names(self) -> None:
        """Reject length or angle distribution names not in the registry."""
        if self.length_distribution not in LENGTH_SAMPLERS:
            available = ", ".join(sorted(LENGTH_SAMPLERS))
            raise ValueError(
                f"length_distribution {self.length_distribution!r} is not registered; "
                f"available: {available}"
            )
        if self.angular_distribution not in ANGLE_SAMPLERS:
            available = ", ".join(sorted(ANGLE_SAMPLERS))
            raise ValueError(
                f"angular_distribution {self.angular_distribution!r} is not registered; "
                f"available: {available}"
            )

    def _validate_kappa_correlation_combo(self) -> None:
        """Warn when angular correlation is set together with kappa = 0."""
        has_zero_kappa = any(k == 0 or k == 0.0 for k in self.kappa_values)
        if has_zero_kappa and self.angular_correlation > 0:
            warnings.warn(
                f"Configuration contains kappa=0 with angular_correlation="
                f"{self.angular_correlation}. Angular correlation has no effect "
                "when kappa=0. Consider angular_correlation=0 or removing kappa=0.",
                UserWarning,
                stacklevel=3,
            )

    def _validate_persistence_decay(self) -> None:
        """Warn on suspicious persistence-decay values."""
        if not self.enable_persistence and self.persistence_decay != 0.9:
            warnings.warn(
                f"persistence_decay={self.persistence_decay} has no effect when "
                "enable_persistence=False.",
                UserWarning,
                stacklevel=3,
            )
            return
        if not self.enable_persistence:
            return
        if self.persistence_decay <= 0.0:
            warnings.warn(
                f"persistence_decay={self.persistence_decay} effectively disables "
                "persistence; consider enable_persistence=False.",
                UserWarning,
                stacklevel=3,
            )
        elif self.persistence_decay >= 0.99:
            warnings.warn(
                f"persistence_decay={self.persistence_decay} is very high and may "
                "cause numerical instability or unrealistic persistence behavior.",
                UserWarning,
                stacklevel=3,
            )

    def _validate_parameter_ranges(self) -> None:
        """Raise on out-of-range numeric parameters."""
        if not (0.0 <= self.angular_correlation < 1.0):
            raise ValueError(
                f"angular_correlation must be in [0, 1), got {self.angular_correlation}"
            )
        if self.enable_persistence and not (0.0 <= self.persistence_decay < 1.0):
            raise ValueError(
                f"persistence_decay must be in [0, 1) when enabled, got {self.persistence_decay}"
            )
        if any(k < 0 for k in self.kappa_values):
            raise ValueError(f"kappa_values must be non-negative, got {self.kappa_values}")
        if any(n < 1 for n in self.n_sarcomeres):
            raise ValueError(f"n_sarcomeres must be positive, got {self.n_sarcomeres}")


_DEFAULT_N_SARCOMERES: list[int] = [5, 10, 15, 25, 50, 75, 100]
_DEFAULT_KAPPA: list[float] = [0, 1, 5, 10, 50]


def load_config(path: Path) -> Config:
    """Load a :class:`Config` from a YAML file.

    Parameters
    ----------
    path : pathlib.Path
        YAML configuration path.

    Returns
    -------
    Config
        Parsed configuration with validators applied.
    """
    with open(path) as f:
        cfg = yaml.safe_load(f) or {}

    return Config(
        n_simulations=int(cfg.get("n_simulations", 1000)),
        seed=int(cfg.get("seed", 42)),
        n_processes=int(cfg.get("n_processes", 1)),
        n_sarcomeres=_coerce_to_list(cfg.get("n_sarcomeres"), _DEFAULT_N_SARCOMERES),
        kappa_values=_coerce_to_list(cfg.get("kappa_values"), _DEFAULT_KAPPA),
        length_distribution=str(cfg.get("length_distribution", "truncnorm")).lower(),
        length_mean=float(cfg.get("length_mean", 1.0)),
        length_std=float(cfg.get("length_std", 0.25)),
        length_min=float(cfg.get("length_min", 0.1)),
        length_max=float(cfg.get("length_max", 2.0)),
        angular_distribution=str(cfg.get("angular_distribution", "vonmises")).lower(),
        angular_mean=float(cfg.get("angular_mean", 0.0)),
        angular_correlation=float(cfg.get("angular_correlation", 0.0)),
        enable_persistence=bool(cfg.get("enable_persistence", False)),
        persistence_decay=float(cfg.get("persistence_decay", 0.9)),
        enable_curvature_bias=bool(cfg.get("enable_curvature_bias", False)),
        curvature_strength=float(cfg.get("curvature_strength", 0.1)),
        output_dir=str(cfg.get("output_dir", "simulation_results")),
        save_raw_data=bool(cfg.get("save_raw_data", False)),
        save_figures=bool(cfg.get("save_figures", True)),
        figure_dpi=int(cfg.get("figure_dpi", 300)),
    )


__all__ = ["Config", "load_config"]
