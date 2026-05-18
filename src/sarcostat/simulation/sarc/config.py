"""Configuration for the single-sarcomere validation suite.

The Cisterna et al. (2021) parameter sets (mean and std for Z-line,
sarcomere length, M-line, separately for the euploid and trisomic
phenotypes) are stored verbatim. Validators are split into focused
private methods so that no individual function exceeds CC 10.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml


class Condition(Enum):
    """Discrete reference conditions for Cisterna sampling.

    Members
    -------
    EUPLOID
        Healthy parameter set.
    TRISOMIC
        Trisomic parameter set (smaller sarcomeres).
    CUSTOM
        User-supplied AsR/Cur ranges; consumers must look up
        ``custom_asr_range`` and ``custom_cur_range`` on the config.
    """

    EUPLOID = "euploid"
    TRISOMIC = "trisomic"
    CUSTOM = "custom"


_VALID_NOISE_MODELS = ("isotropic", "anisotropic")


@dataclass
class SingleSarcomereConfig:
    """Configuration for the single-sarcomere validation pipelines.

    See the original module docstring for the full attribute list. All
    geometric values are in nanometres; ``noise_levels`` is a list of
    standard deviations applied during the noise-sensitivity sweep.
    """

    n_simulations: int = 1000
    seed: int = 42
    n_processes: int = 4

    target_condition: str = "euploid"

    euploid_z_length: tuple[float, float] = (414.0, 109.0)
    euploid_sarcomere_length: tuple[float, float] = (1638.0, 114.0)
    euploid_m_line_length: tuple[float, float] = (469.0, 77.0)

    trisomic_z_length: tuple[float, float] = (350.0, 108.0)
    trisomic_sarcomere_length: tuple[float, float] = (1281.0, 89.0)
    trisomic_m_line_length: tuple[float, float] = (366.0, 60.0)

    custom_asr_range: tuple[float, float] = (3.0, 5.0)
    custom_cur_range: tuple[float, float] = (2.8, 4.5)

    noise_levels: list[float] = field(
        default_factory=lambda: [0.0, 5.0, 10.0, 15.0, 20.0, 30.0, 50.0, 75.0, 100.0]
    )
    noise_model: str = "isotropic"
    n_replicates: int = 100

    anisotropic_sigma_along: float = 10.0
    anisotropic_sigma_perpendicular: float = 20.0

    generate_boundary_cases: bool = True
    boundary_tolerance: float = 0.05
    n_boundary_samples: int = 500
    boundary_offset_range: tuple[float, float] = (-0.3, 0.3)

    output_dir: str = "outputs/sarcomere_simulation"
    save_raw_data: bool = True
    save_figures: bool = True
    figure_dpi: int = 300

    def __post_init__(self) -> None:
        """Run all validators after dataclass initialisation."""
        self._validate_basic_ranges()
        self._validate_categorical_choices()

    def _validate_basic_ranges(self) -> None:
        """Reject negative counts and noise levels."""
        if self.n_simulations < 1:
            raise ValueError(f"n_simulations must be positive, got {self.n_simulations}")
        if self.n_replicates < 1:
            raise ValueError(f"n_replicates must be positive, got {self.n_replicates}")
        if any(s < 0 for s in self.noise_levels):
            raise ValueError(f"noise_levels must be non-negative, got {self.noise_levels}")

    def _validate_categorical_choices(self) -> None:
        """Reject unknown categorical values."""
        if self.target_condition not in {c.value for c in Condition}:
            raise ValueError(
                f"target_condition must be one of {[c.value for c in Condition]}, "
                f"got {self.target_condition}"
            )
        if self.noise_model not in _VALID_NOISE_MODELS:
            raise ValueError(
                f"noise_model must be one of {_VALID_NOISE_MODELS}, got {self.noise_model}"
            )


def _tuple2(value, default: tuple[float, float]) -> tuple[float, float]:
    """Coerce ``value`` to a ``(a, b)`` float tuple with the given default."""
    if value is None:
        return default
    return (float(value[0]), float(value[1]))


def load_sarcomere_config(path: Path) -> SingleSarcomereConfig:
    """Load a :class:`SingleSarcomereConfig` from a YAML file.

    The YAML schema mirrors the previous module: top-level keys for
    simple parameters, plus ``cisterna_parameters.{euploid,trisomic}``
    nested groups for the per-condition Cisterna means/stds, and
    ``noise_sensitivity`` and ``boundary_analysis`` groups for the
    pipeline parameters.
    """
    with open(path) as f:
        cfg = yaml.safe_load(f) or {}

    cisterna = cfg.get("cisterna_parameters", {}) or {}
    euploid = cisterna.get("euploid", {}) or {}
    trisomic = cisterna.get("trisomic", {}) or {}
    noise_cfg = cfg.get("noise_sensitivity", {}) or {}
    boundary_cfg = cfg.get("boundary_analysis", {}) or {}

    return SingleSarcomereConfig(
        n_simulations=int(cfg.get("n_simulations", 1000)),
        seed=int(cfg.get("seed", 42)),
        n_processes=int(cfg.get("n_processes", 4)),
        target_condition=str(cfg.get("target_condition", "euploid")),
        euploid_z_length=_tuple2(euploid.get("z_length"), (414.0, 109.0)),
        euploid_sarcomere_length=_tuple2(euploid.get("sarcomere_length"), (1638.0, 114.0)),
        euploid_m_line_length=_tuple2(euploid.get("m_line_length"), (469.0, 77.0)),
        trisomic_z_length=_tuple2(trisomic.get("z_length"), (350.0, 108.0)),
        trisomic_sarcomere_length=_tuple2(trisomic.get("sarcomere_length"), (1281.0, 89.0)),
        trisomic_m_line_length=_tuple2(trisomic.get("m_line_length"), (366.0, 60.0)),
        custom_asr_range=_tuple2(cfg.get("custom_asr_range"), (3.0, 5.0)),
        custom_cur_range=_tuple2(cfg.get("custom_cur_range"), (2.8, 4.5)),
        noise_levels=list(noise_cfg.get("noise_levels", [0, 5, 10, 15, 20, 30, 50, 75, 100])),
        noise_model=str(noise_cfg.get("noise_model", "isotropic")),
        n_replicates=int(noise_cfg.get("n_replicates", 100)),
        generate_boundary_cases=bool(boundary_cfg.get("enabled", True)),
        boundary_tolerance=float(boundary_cfg.get("tolerance", 0.05)),
        n_boundary_samples=int(boundary_cfg.get("n_boundary_samples", 500)),
        boundary_offset_range=_tuple2(boundary_cfg.get("offset_range"), (-0.3, 0.3)),
        output_dir=str(cfg.get("output_dir", "outputs/sarcomere_simulation")),
        save_raw_data=bool(cfg.get("save_raw_data", True)),
        save_figures=bool(cfg.get("save_figures", True)),
        figure_dpi=int(cfg.get("figure_dpi", 300)),
    )


__all__ = ["Condition", "SingleSarcomereConfig", "load_sarcomere_config"]
