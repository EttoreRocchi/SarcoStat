"""Chain Monte Carlo subpackage.

The chain simulator is split across four modules:

- :mod:`sarcostat.simulation.chain.config` - YAML loader and validators.
- :mod:`sarcostat.simulation.chain.dynamics` - step-angle adjustment strategies.
- :mod:`sarcostat.simulation.chain.simulator` - per-trial simulator.
- :mod:`sarcostat.simulation.chain.runner` - parameter-grid orchestration.
- :mod:`sarcostat.simulation.chain.plots` - boxplot/polar/heatmap plotters.
"""

from .config import Config, load_config
from .dynamics import AngularDynamics, StandardDynamics
from .plots import (
    BoxplotPlotter,
    HeatmapPlotter,
    Plotter,
    PolarPlotter,
    default_plotters,
    render_all,
)
from .runner import ProgressCallback, run_monte_carlo, summarise_results
from .simulator import ChainSimulator, TrialOutcome

__all__ = [
    "Config",
    "load_config",
    "AngularDynamics",
    "StandardDynamics",
    "ChainSimulator",
    "TrialOutcome",
    "run_monte_carlo",
    "summarise_results",
    "ProgressCallback",
    "Plotter",
    "BoxplotPlotter",
    "PolarPlotter",
    "HeatmapPlotter",
    "default_plotters",
    "render_all",
]
