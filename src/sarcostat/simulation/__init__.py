"""Monte Carlo simulation tools for SarcoStat.

The subpackage is organised as:

- :mod:`sarcostat.simulation._artifacts` - shared run-artifact bundle.
- :mod:`sarcostat.simulation._distributions` - pluggable length and
  angle samplers.
- :mod:`sarcostat.simulation.chain` - chain Monte Carlo (LR vs MVL across
  ``(kappa, n)``).
- :mod:`sarcostat.simulation.sarc` - single-sarcomere validation
  (generator, noise models, sensitivity / boundary pipelines).
- :mod:`sarcostat.simulation.rigor` - rigor analyses (CI coverage,
  minimum detectable effect).
"""

from ._artifacts import RunArtifacts
from .chain import (
    ChainSimulator,
)
from .chain import (
    Config as ChainConfig,
)
from .chain import (
    load_config as load_chain_config,
)
from .chain import (
    run_monte_carlo as run_chain_monte_carlo,
)
from .chain import (
    summarise_results as summarise_chain_results,
)
from .rigor.coverage import (
    CoverageConfig,
    load_coverage_config,
)
from .rigor.coverage import (
    run as run_coverage_analysis,
)
from .rigor.mde import (
    MDEConfig,
    load_mde_config,
)
from .rigor.mde import (
    run as run_mde_analysis,
)
from .sarc import (
    BoundaryClassificationPipeline,
    BoundaryClassificationResults,
    NoiseSensitivityPipeline,
    NoiseSensitivityResults,
    SarcomereGenerator,
    SingleSarcomereConfig,
    load_sarcomere_config,
)

__all__ = [
    "RunArtifacts",
    "ChainConfig",
    "ChainSimulator",
    "load_chain_config",
    "run_chain_monte_carlo",
    "summarise_chain_results",
    "SingleSarcomereConfig",
    "SarcomereGenerator",
    "NoiseSensitivityPipeline",
    "BoundaryClassificationPipeline",
    "NoiseSensitivityResults",
    "BoundaryClassificationResults",
    "load_sarcomere_config",
    "CoverageConfig",
    "load_coverage_config",
    "run_coverage_analysis",
    "MDEConfig",
    "load_mde_config",
    "run_mde_analysis",
]
