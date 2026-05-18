"""Single-sarcomere validation subpackage.

- :mod:`sarcostat.simulation.sarc.config` - YAML loader and validators.
- :mod:`sarcostat.simulation.sarc.results` - result dataclasses.
- :mod:`sarcostat.simulation.sarc.generator` - ground-truth synthetic
  sarcomere generation.
- :mod:`sarcostat.simulation.sarc.noise` - noise models (Gaussian and
  anisotropic) behind a :class:`NoiseModel` Protocol.
- :mod:`sarcostat.simulation.sarc.metrics` - stateless point-to-metric
  helpers.
- :mod:`sarcostat.simulation.sarc.pipelines` - noise-sensitivity and
  boundary-classification pipelines.
- :mod:`sarcostat.simulation.sarc.plots` - per-result plotters.
"""

from .config import Condition, SingleSarcomereConfig, load_sarcomere_config
from .generator import SarcomereGenerator
from .metrics import compute_metrics_from_points, compute_metrics_with_noise
from .noise import AnisotropicNoise, GaussianNoise, NoiseModel, make_noise_model
from .pipelines import BoundaryClassificationPipeline, NoiseSensitivityPipeline
from .plots import (
    BoundaryAnalysisPlot,
    ExampleSarcomeresPlot,
    NoiseSensitivityPlot,
    SarcPlotter,
    default_sarc_plotters,
)
from .results import BoundaryClassificationResults, NoiseSensitivityResults

__all__ = [
    "Condition",
    "SingleSarcomereConfig",
    "load_sarcomere_config",
    "SarcomereGenerator",
    "compute_metrics_from_points",
    "compute_metrics_with_noise",
    "NoiseModel",
    "GaussianNoise",
    "AnisotropicNoise",
    "make_noise_model",
    "NoiseSensitivityPipeline",
    "BoundaryClassificationPipeline",
    "NoiseSensitivityResults",
    "BoundaryClassificationResults",
    "SarcPlotter",
    "NoiseSensitivityPlot",
    "BoundaryAnalysisPlot",
    "ExampleSarcomeresPlot",
    "default_sarc_plotters",
]
