"""Configuration for the single-sarcomere validation suite.

The Cisterna et al. (2021) parameter sets (mean and std for Z-line,
sarcomere length, M-line, separately for the euploid and trisomic
phenotypes) are stored verbatim. Validators are split into focused
private methods so that no individual function exceeds CC 10.

:func:`load_sarcomere_config` is strict: unknown keys raise
``ValueError`` with a did-you-mean hint, booleans must be YAML booleans,
and keys retired in 0.2.0 (``n_processes``, ``custom_asr_range``,
``custom_cur_range``) are accepted with a :class:`FutureWarning`.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .._config_io import (
    as_bool,
    as_float,
    as_int,
    as_list,
    as_pair,
    as_str,
    check_keys,
    read_yaml_mapping,
    section,
)


class Condition(Enum):
    """Reference conditions for Cisterna sampling.

    Members
    -------
    EUPLOID
        Healthy parameter set.
    TRISOMIC
        Trisomic parameter set (smaller sarcomeres).
    """

    EUPLOID = "euploid"
    TRISOMIC = "trisomic"


_VALID_NOISE_MODELS = ("isotropic", "anisotropic")


@dataclass
class SingleSarcomereConfig:
    """Configuration for the single-sarcomere validation pipelines.

    All geometric values are in nanometres.

    Attributes
    ----------
    n_simulations : int
        Ground-truth sarcomeres drawn for the noise-sensitivity sweep.
    seed : int
        Master RNG seed.
    target_condition : str
        ``"euploid"`` or ``"trisomic"``.
    euploid_z_length, euploid_sarcomere_length, euploid_m_line_length : tuple
        ``(mean, std)`` of the euploid Cisterna parameters.
    trisomic_z_length, trisomic_sarcomere_length, trisomic_m_line_length : tuple
        ``(mean, std)`` of the trisomic Cisterna parameters.
    run_noise_sensitivity : bool
        Whether the CLI runs the noise-sensitivity pipeline.
    noise_levels : list of float
        Annotation-noise levels sigma swept by both pipelines.
    noise_model : str
        ``"isotropic"`` or ``"anisotropic"``.
    n_replicates : int
        Noisy replicates per ground-truth sarcomere.
    anisotropic_along_weight, anisotropic_perpendicular_weight : float
        Relative noise along and perpendicular to the sarcomere axis for
        the anisotropic model. Only their ratio matters: at each swept
        sigma the two standard deviations are rescaled so that the total
        per-point variance equals the isotropic model's ``2 * sigma**2``.
    generate_boundary_cases : bool
        Whether the CLI runs the boundary-classification pipeline.
    boundary_tolerance : float
        Shape tolerance used both for the ground truth and the classifier
        in the boundary pipeline.
    n_boundary_samples : int
        Samples per ``(offset, sigma)`` cell of the boundary sweep.
    boundary_offset_range : tuple of float
        ``(min, max)`` of the ``Cur - AsR`` offsets swept.
    output_dir : str
        Base directory for the timestamped run folder.
    save_raw_data : bool
        If True, the CLI also writes the per-replicate error tensors to
        ``data/noise_sensitivity_raw.npz``.
    save_figures : bool
        If True, the standard figures are rendered.
    figure_dpi : int
        DPI used for matplotlib output.
    """

    n_simulations: int = 1000
    seed: int = 42

    target_condition: str = "euploid"

    euploid_z_length: tuple[float, float] = (414.0, 109.0)
    euploid_sarcomere_length: tuple[float, float] = (1638.0, 114.0)
    euploid_m_line_length: tuple[float, float] = (469.0, 118.0)

    trisomic_z_length: tuple[float, float] = (350.0, 108.0)
    trisomic_sarcomere_length: tuple[float, float] = (1281.0, 89.0)
    trisomic_m_line_length: tuple[float, float] = (478.0, 123.0)

    run_noise_sensitivity: bool = True
    noise_levels: list[float] = field(
        default_factory=lambda: [0.0, 5.0, 10.0, 15.0, 20.0, 30.0, 50.0, 75.0, 100.0]
    )
    noise_model: str = "isotropic"
    n_replicates: int = 100

    anisotropic_along_weight: float = 1.0
    anisotropic_perpendicular_weight: float = 2.0

    generate_boundary_cases: bool = True
    boundary_tolerance: float = 0.05
    n_boundary_samples: int = 500
    boundary_offset_range: tuple[float, float] = (-0.3, 0.3)

    output_dir: str = "outputs/sarcomere_simulation"
    save_raw_data: bool = False
    save_figures: bool = True
    figure_dpi: int = 300

    def __post_init__(self) -> None:
        """Run all validators after dataclass initialisation."""
        self._validate_basic_ranges()
        self._validate_categorical_choices()
        self._validate_boundary_settings()

    def _validate_basic_ranges(self) -> None:
        """Reject non-positive counts and negative noise levels."""
        if self.n_simulations < 1:
            raise ValueError(f"n_simulations must be positive, got {self.n_simulations}")
        if self.n_replicates < 1:
            raise ValueError(f"n_replicates must be positive, got {self.n_replicates}")
        if not self.noise_levels:
            raise ValueError("noise_levels must not be empty")
        if any(s < 0 for s in self.noise_levels):
            raise ValueError(f"noise_levels must be non-negative, got {self.noise_levels}")
        if self.figure_dpi < 1:
            raise ValueError(f"figure_dpi must be positive, got {self.figure_dpi}")
        weights = (self.anisotropic_along_weight, self.anisotropic_perpendicular_weight)
        if min(weights) < 0 or max(weights) <= 0:
            raise ValueError(
                "anisotropic weights must be non-negative and not both zero, got "
                f"along={weights[0]}, perpendicular={weights[1]}"
            )

    def _validate_categorical_choices(self) -> None:
        """Reject unknown categorical values."""
        if self.target_condition not in {c.value for c in Condition}:
            raise ValueError(
                f"target_condition must be one of {[c.value for c in Condition]}, "
                f"got {self.target_condition!r}"
            )
        if self.noise_model not in _VALID_NOISE_MODELS:
            raise ValueError(
                f"noise_model must be one of {_VALID_NOISE_MODELS}, got {self.noise_model!r}"
            )

    def _validate_boundary_settings(self) -> None:
        """Reject inconsistent boundary-analysis parameters."""
        if self.boundary_tolerance < 0:
            raise ValueError(
                f"boundary_tolerance must be non-negative, got {self.boundary_tolerance}"
            )
        if self.n_boundary_samples < 1:
            raise ValueError(f"n_boundary_samples must be positive, got {self.n_boundary_samples}")
        low, high = self.boundary_offset_range
        if not low < high:
            raise ValueError(f"boundary_offset_range must be (min, max), got {(low, high)}")


_TOP_LEVEL_KEYS = {
    "n_simulations",
    "seed",
    "target_condition",
    "cisterna_parameters",
    "noise_sensitivity",
    "boundary_analysis",
    "output_dir",
    "save_raw_data",
    "save_figures",
    "figure_dpi",
}
_RETIRED_TOP_LEVEL_KEYS = {
    "n_processes": "the sarc pipelines are single-threaded",
    "custom_asr_range": "the 'custom' target condition was removed in 0.2.0",
    "custom_cur_range": "the 'custom' target condition was removed in 0.2.0",
}
_CONDITION_KEYS = {"z_length", "sarcomere_length", "m_line_length"}
_NOISE_KEYS = {"enabled", "noise_levels", "noise_model", "n_replicates", "anisotropic"}
_ANISOTROPIC_KEYS = {"along_weight", "perpendicular_weight"}
_LEGACY_ANISOTROPIC_KEYS = {
    "sigma_along_line": "along_weight",
    "sigma_perpendicular": "perpendicular_weight",
}
_BOUNDARY_KEYS = {"enabled", "tolerance", "n_boundary_samples", "offset_range"}


def _condition_pairs(cisterna: dict[str, Any], name: str) -> dict[str, tuple[float, float]]:
    """Parse one ``cisterna_parameters.<condition>`` group into ``(mean, std)`` pairs."""
    where = f"cisterna_parameters.{name}"
    group = section(cisterna, name, "cisterna_parameters")
    check_keys(group, _CONDITION_KEYS, where=where)
    return {
        f"{name}_{key}": as_pair(value, f"{where}.{key}")
        for key, value in group.items()
        if value is not None
    }


def _anisotropic_weights(noise: dict[str, Any]) -> dict[str, float]:
    """Parse ``noise_sensitivity.anisotropic``, mapping the pre-0.2.0 sigma keys."""
    where = "noise_sensitivity.anisotropic"
    group = section(noise, "anisotropic", "noise_sensitivity")
    check_keys(group, _ANISOTROPIC_KEYS | set(_LEGACY_ANISOTROPIC_KEYS), where=where)
    out: dict[str, float] = {}
    for key, value in group.items():
        if key in _LEGACY_ANISOTROPIC_KEYS:
            new_key = _LEGACY_ANISOTROPIC_KEYS[key]
            warnings.warn(
                f"{where}.{key} is deprecated; it is read as the relative weight "
                f"{where}.{new_key} (the noise scale now follows the swept noise level)",
                FutureWarning,
                stacklevel=3,
            )
            key = new_key
        out[f"anisotropic_{key}"] = as_float(value, f"{where}.{key}")
    return out


def load_sarcomere_config(path: str | Path) -> SingleSarcomereConfig:
    """Load a :class:`SingleSarcomereConfig` from a YAML file.

    The YAML schema has top-level keys for simple parameters, plus
    ``cisterna_parameters.{euploid,trisomic}`` groups for the
    per-condition Cisterna means/stds, and ``noise_sensitivity`` and
    ``boundary_analysis`` groups for the pipeline parameters. Missing
    keys take the :class:`SingleSarcomereConfig` defaults.

    Raises
    ------
    ValueError
        On unknown keys, values of the wrong type, or out-of-range
        parameters.
    """
    raw = read_yaml_mapping(path)
    check_keys(raw, _TOP_LEVEL_KEYS, retired=_RETIRED_TOP_LEVEL_KEYS)
    cisterna = section(raw, "cisterna_parameters")
    check_keys(cisterna, {c.value for c in Condition}, where="cisterna_parameters")
    noise = section(raw, "noise_sensitivity")
    check_keys(noise, _NOISE_KEYS, where="noise_sensitivity")
    boundary = section(raw, "boundary_analysis")
    check_keys(boundary, _BOUNDARY_KEYS, where="boundary_analysis")

    kwargs: dict[str, Any] = {}

    def put(field_name: str, source: dict[str, Any], key: str, convert, where: str = "") -> None:
        value = source.get(key)
        if value is not None:
            kwargs[field_name] = convert(value, f"{where}.{key}" if where else key)

    def floats(value, name):
        return as_list(value, name, as_float)

    put("n_simulations", raw, "n_simulations", as_int)
    put("seed", raw, "seed", as_int)
    put("target_condition", raw, "target_condition", as_str)
    put("output_dir", raw, "output_dir", as_str)
    put("save_raw_data", raw, "save_raw_data", as_bool)
    put("save_figures", raw, "save_figures", as_bool)
    put("figure_dpi", raw, "figure_dpi", as_int)

    for condition in Condition:
        kwargs.update(_condition_pairs(cisterna, condition.value))

    put("run_noise_sensitivity", noise, "enabled", as_bool, "noise_sensitivity")
    put("noise_levels", noise, "noise_levels", floats, "noise_sensitivity")
    put("noise_model", noise, "noise_model", as_str, "noise_sensitivity")
    put("n_replicates", noise, "n_replicates", as_int, "noise_sensitivity")
    kwargs.update(_anisotropic_weights(noise))

    put("generate_boundary_cases", boundary, "enabled", as_bool, "boundary_analysis")
    put("boundary_tolerance", boundary, "tolerance", as_float, "boundary_analysis")
    put("n_boundary_samples", boundary, "n_boundary_samples", as_int, "boundary_analysis")
    put("boundary_offset_range", boundary, "offset_range", as_pair, "boundary_analysis")

    return SingleSarcomereConfig(**kwargs)


__all__ = ["Condition", "SingleSarcomereConfig", "load_sarcomere_config"]
