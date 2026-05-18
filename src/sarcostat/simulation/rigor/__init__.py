"""Rigor analyses: empirical CI coverage and minimum detectable effect.

The two modules in this subpackage are validation studies rather than
parameter-grid simulators:

- :mod:`sarcostat.simulation.rigor.coverage` measures the empirical
  coverage of percentile-bootstrap confidence intervals for the
  circular mean and the mean vector length.
- :mod:`sarcostat.simulation.rigor.mde` estimates the minimum
  detectable effect for a two-sample comparison on the MVL, AsR,
  and Cur metrics.
"""

from .coverage import CoverageConfig, load_coverage_config
from .coverage import run as run_coverage
from .mde import MDEConfig, load_mde_config
from .mde import run as run_mde

__all__ = [
    "CoverageConfig",
    "load_coverage_config",
    "run_coverage",
    "MDEConfig",
    "load_mde_config",
    "run_mde",
]
