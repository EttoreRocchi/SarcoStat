"""Configuration container and loader for the chain simulation.

This module centralizes the chain-simulator parameters and their YAML
loader. The :class:`Config` validators are split into focused private
methods so that no individual function exceeds CC 10.

Length distributions are restricted to the families registered in
:mod:`sarcostat.simulation._distributions`.

:func:`load_config` is strict: unknown keys raise ``ValueError`` with a
did-you-mean hint, and booleans must be YAML booleans.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, fields
from pathlib import Path

from .._config_io import (
    as_bool,
    as_float,
    as_int,
    as_list,
    as_number,
    as_str,
    check_keys,
    read_yaml_mapping,
    require_unique,
)
from .._distributions import ANGLE_SAMPLERS, LENGTH_SAMPLERS

# Warnings raised from validators run inside the dataclass-generated
# ``__init__``; stacklevel 4 points at the caller that built the Config.
_WARN_STACKLEVEL = 4


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
        Whether to rotate every heading by ``curvature_strength``. Despite
        the name this is a constant offset of the mean direction, not a
        progressive bend: LR and MVL are unchanged, and only the V-test
        against ``angular_mean`` responds (V shrinks by
        ``cos(curvature_strength)``).
    curvature_strength : float
        Heading offset in radians applied when ``enable_curvature_bias``
        (``offset`` mode), or turning angle per segment (``arc`` mode).
    output_dir : str
        Base directory used by :class:`sarcostat.simulation._artifacts.RunArtifacts`.
    save_raw_data : bool
        If True, the runner writes per-cell ``.npz`` archives.
    save_figures : bool
        If True, the standard plot suite is rendered and saved.
    figure_dpi : int
        DPI used for matplotlib output.
    curvature_mode : {"offset", "arc"}
        ``offset`` (default, the historical behaviour) rotates every
        heading by ``curvature_strength``; ``arc`` bends the chain by
        ``curvature_strength`` at every segment, so LR and the heading
        MVL decrease with chain length while the turning-angle MVL does
        not.
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
    curvature_mode: str = "offset"

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
        """Warn when angular correlation is set together with kappa = 0.

        The AR(1) model acts on deviations from ``angular_mean`` wrapped to
        (-pi, pi], so at kappa = 0 it pulls the otherwise uniform headings
        toward ``angular_mean`` and the kappa = 0 cells stop being uniform.
        """
        has_zero_kappa = any(k == 0 for k in self.kappa_values)
        if has_zero_kappa and self.angular_correlation > 0:
            warnings.warn(
                f"Configuration contains kappa=0 with angular_correlation="
                f"{self.angular_correlation}. The AR(1) correlation acts on deviations "
                "from angular_mean, so the kappa=0 headings are no longer uniform "
                "(their MVL grows with the correlation). Consider angular_correlation=0 "
                "or removing kappa=0.",
                UserWarning,
                stacklevel=_WARN_STACKLEVEL,
            )

    def _validate_persistence_decay(self) -> None:
        """Warn on suspicious persistence-decay values."""
        if not self.enable_persistence and self.persistence_decay != 0.9:
            warnings.warn(
                f"persistence_decay={self.persistence_decay} has no effect when "
                "enable_persistence=False.",
                UserWarning,
                stacklevel=_WARN_STACKLEVEL,
            )
            return
        if not self.enable_persistence:
            return
        if self.persistence_decay <= 0.0:
            warnings.warn(
                f"persistence_decay={self.persistence_decay} effectively disables "
                "persistence; consider enable_persistence=False.",
                UserWarning,
                stacklevel=_WARN_STACKLEVEL,
            )
        elif self.persistence_decay >= 0.99:
            warnings.warn(
                f"persistence_decay={self.persistence_decay} is very high and may "
                "cause numerical instability or unrealistic persistence behavior.",
                UserWarning,
                stacklevel=_WARN_STACKLEVEL,
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
        if self.n_simulations < 1:
            raise ValueError(f"n_simulations must be positive, got {self.n_simulations}")
        if self.n_processes < 1:
            raise ValueError(f"n_processes must be positive, got {self.n_processes}")
        if not self.kappa_values or not self.n_sarcomeres:
            raise ValueError("kappa_values and n_sarcomeres must not be empty")
        require_unique(list(self.kappa_values), "kappa_values")
        require_unique(list(self.n_sarcomeres), "n_sarcomeres")
        if self.length_std < 0:
            raise ValueError(f"length_std must be non-negative, got {self.length_std}")
        if not (0.0 <= self.length_min < self.length_max):
            raise ValueError(
                "length bounds must satisfy 0 <= length_min < length_max, got "
                f"length_min={self.length_min}, length_max={self.length_max}"
            )
        if self.figure_dpi < 1:
            raise ValueError(f"figure_dpi must be positive, got {self.figure_dpi}")
        if self.curvature_mode not in ("offset", "arc"):
            raise ValueError(
                f"curvature_mode must be 'offset' or 'arc', got {self.curvature_mode!r}"
            )


_DEFAULT_N_SARCOMERES: list[int] = [5, 10, 15, 25, 50, 75, 100]
_DEFAULT_KAPPA: list[float] = [0, 1, 5, 10, 50]


def load_config(path: str | Path) -> Config:
    """Load a :class:`Config` from a YAML file.

    Parameters
    ----------
    path : str or pathlib.Path
        YAML configuration path.

    Returns
    -------
    Config
        Parsed configuration with validators applied.

    Raises
    ------
    ValueError
        On unknown keys, values of the wrong type, or out-of-range
        parameters.
    """
    cfg = read_yaml_mapping(path)
    check_keys(cfg, {f.name for f in fields(Config)})

    def get(key, convert, default):
        value = cfg.get(key)
        return default if value is None else convert(value, key)

    def numbers(value, name):
        return as_list(value, name, as_number)

    def ints(value, name):
        return as_list(value, name, as_int)

    return Config(
        n_simulations=get("n_simulations", as_int, 1000),
        seed=get("seed", as_int, 42),
        n_processes=get("n_processes", as_int, 1),
        n_sarcomeres=get("n_sarcomeres", ints, list(_DEFAULT_N_SARCOMERES)),
        kappa_values=get("kappa_values", numbers, list(_DEFAULT_KAPPA)),
        length_distribution=get("length_distribution", as_str, "truncnorm").lower(),
        length_mean=get("length_mean", as_float, 1.0),
        length_std=get("length_std", as_float, 0.25),
        length_min=get("length_min", as_float, 0.1),
        length_max=get("length_max", as_float, 2.0),
        angular_distribution=get("angular_distribution", as_str, "vonmises").lower(),
        angular_mean=get("angular_mean", as_float, 0.0),
        angular_correlation=get("angular_correlation", as_float, 0.0),
        enable_persistence=get("enable_persistence", as_bool, False),
        persistence_decay=get("persistence_decay", as_float, 0.9),
        enable_curvature_bias=get("enable_curvature_bias", as_bool, False),
        curvature_strength=get("curvature_strength", as_float, 0.1),
        output_dir=get("output_dir", as_str, "simulation_results"),
        save_raw_data=get("save_raw_data", as_bool, False),
        save_figures=get("save_figures", as_bool, True),
        figure_dpi=get("figure_dpi", as_int, 300),
        curvature_mode=get("curvature_mode", as_str, "offset").lower(),
    )


__all__ = ["Config", "load_config"]
