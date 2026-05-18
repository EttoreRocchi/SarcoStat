# Monte Carlo Simulation Pipeline

## Installation

```bash
pip install sarcostat
```

The CLI entry point `sarcostat` is registered automatically; no extras are required for the simulation pipelines beyond the default install.

## Overview

The `sarcostat.simulation` subpackage provides four Monte Carlo pipelines, all reachable through the unified `sarcostat sim` CLI:

| Subcommand | Pipeline | Validates |
|------------|----------|-----------|
| `sarcostat sim chain` | Chain Monte Carlo (LR / MVL across kappa x N) | Myofibril linearity metrics |
| `sarcostat sim sarc` | Single-sarcomere noise sensitivity + boundary classification | Morphometry (AsR, Cur, Shape Index) |
| `sarcostat sim coverage` | Bootstrap CI coverage validation | Calibration of percentile-bootstrap CIs for circular mean and MVL |
| `sarcostat sim mde` | Minimum detectable effect | Two-sample power for MVL, AsR, Cur |

Each subcommand accepts an optional `--config / -c` pointing at a YAML file. Without `--config`, the chain and sarc pipelines load the packaged defaults; the coverage and MDE pipelines fall back to the in-code dataclass defaults.

## Mathematical Framework

### Angular Distributions

Angle samplers are registered in `sarcostat.simulation._distributions.ANGLE_SAMPLERS`:

| Distribution | Description | Key Parameter |
|-------------|-------------|---------------|
| Uniform (`uniform`) | Flat over `[0, 2*pi)` (trivial sampler when `kappa = 0`) | - |
| von Mises (`vonmises`) | Circular analogue of Gaussian | kappa (concentration) |
| Wrapped normal (`wrapped_normal`) | Normal wrapped on the circle, sigma matched to the von Mises mean resultant length | kappa (concentration) |

The von Mises density is

$$f(\theta; \mu, \kappa) = \frac{e^{\kappa \cos(\theta - \mu)}}{2\pi I_0(\kappa)}$$

with kappa >= 0 controlling concentration: kappa = 0 yields uniformly distributed angles, larger kappa yields tighter concentration around the mean direction `mu`.

### Length Distributions

Length samplers are registered in `sarcostat.simulation._distributions.LENGTH_SAMPLERS`:

| Distribution | Description | Key Parameters |
|--------------|-------------|----------------|
| Truncated normal (`truncnorm`) | Gaussian truncated to `[length_min, length_max]` | `length_mean`, `length_std`, `length_min`, `length_max` |
| Log-normal (`lognorm`) | Method-of-moments matched log-normal, clipped to `[length_min, length_max]` | `length_mean`, `length_std`, `length_min`, `length_max` |

### Output Metrics (Chain)

For each Monte Carlo trial the chain pipeline records:

1. Mean Vector Length (R) - concentration of the angular distribution.
2. Linearity Ratio - end-to-end distance divided by total path length.
3. Rayleigh Z-statistic - test for uniformity (`H0`: uniform).
4. V-statistic - test against an expected mean direction.

# Chain Simulation

## Configuration

Default config: `src/sarcostat/simulation/config.yaml` (packaged).

A minimal example config lives at `examples/configs/config_minimal.yaml`:

```yaml
n_simulations: 5000
seed: 42
n_processes: 4

n_sarcomeres: [5, 10, 25, 50, 100]
kappa_values: [0, 1, 5, 10, 50]

length_distribution: 'truncnorm'
length_mean: 1.0
length_std: 0.25
length_min: 0.1
length_max: 2.0

angular_distribution: 'vonmises'
angular_mean: 0.0
angular_correlation: 0.0

enable_persistence: false
enable_curvature_bias: false

output_dir: 'paper/results/minimal'
save_raw_data: true
save_figures: true
figure_dpi: 300
```

### Key Parameters

| Parameter | Range | Description |
|-----------|-------|-------------|
| `n_simulations` | 100-10000 | Replicates per (kappa, N) cell |
| `n_sarcomeres` | 5-100+ | Chain-length sweep |
| `kappa_values` | 0+ | Concentration sweep |
| `angular_correlation` | [0, 1) | AR(1) lag-1 correlation between consecutive step angles |
| `enable_persistence` | bool | Enable heading-persistence dynamics |
| `enable_curvature_bias` | bool | Add a constant turning bias to each step |

## Usage

### Command Line

```bash
# Top-level help
sarcostat --help
sarcostat sim --help

# Chain simulation
sarcostat sim chain                                              # packaged default
sarcostat sim chain --config examples/configs/config_minimal.yaml
```

### Programmatic Interface

```python
from pathlib import Path

from sarcostat.simulation._artifacts import RunArtifacts
from sarcostat.simulation.chain import (
    ChainSimulator,
    Config,
    default_plotters,
    load_config,
    render_all,
    run_monte_carlo,
    summarise_results,
)

cfg: Config = load_config(Path("examples/configs/config_minimal.yaml"))
artifacts = RunArtifacts(cfg.output_dir, logger_name="my.run")

simulator = ChainSimulator.from_config(cfg)
results = run_monte_carlo(simulator, cfg, logger=artifacts.logger)

summary = summarise_results(results, cfg.kappa_values, cfg.n_sarcomeres)
artifacts.save_json("results_summary.json", summary)

if cfg.save_figures:
    figures = render_all(default_plotters(), results, cfg.kappa_values,
                        cfg.n_sarcomeres, dpi=cfg.figure_dpi)
    for name, fig in figures.items():
        artifacts.save_figure(fig, f"chain_{name}", dpi=cfg.figure_dpi)
```

## Chain Output Structure

`RunArtifacts` writes the run into `output_dir` (the chain command does not stamp the directory; pass a unique `output_dir` per run if you want parallel runs):

```
<output_dir>/
  data/
    simulation_parameters.json    # Subset of the chain config
    results_summary.json          # Aggregated mean/std per (metric, kappa, N)
  logs/
    simulation.log                # Detailed execution log
    errors.log                    # Warnings and errors only
  plots/
    chain_comprehensive.{pdf,png} # 4xK boxplot grid
    chain_circular.{pdf,png}      # 1xK polar step-angle distributions
    chain_heatmap.{pdf,png}       # 1x2 mean heatmaps (R and LR)
```

## Visualization Outputs

- BoxplotPlotter (`chain_comprehensive`): 4xK grid of boxplots over the metrics LR, MVL, Rayleigh-Z, V-statistic.
- PolarPlotter (`chain_circular`): 1xK polar plot of step-angle distributions for a representative chain length.
- HeatmapPlotter (`chain_heatmap`): 1x2 mean-value heatmaps for R and LR across the (kappa, N) grid, with mean and std annotations per cell.

Custom plotters can be registered by implementing the structural `sarcostat.simulation.chain.plots.Plotter` Protocol and passing them to `render_all`.


# Single-Sarcomere Simulation

## Overview

The `sarc` pipeline validates annotation-noise sensitivity and boundary classification for the morphometric parameters (AsR, Cur, Shape Index) defined by Cisterna et al. (2021). Two sub-pipelines run from one config:

- `NoiseSensitivityPipeline`: how RMSE / bias / classification accuracy scale with annotation noise sigma.
- `BoundaryClassificationPipeline`: misclassification rate near the `Cur ~ AsR` boundary as a function of `Cur - AsR` offset and noise.

## Configuration

Default config: `src/sarcostat/simulation/sarcomere_config.yaml`.
Smoke-test config: `examples/configs/config_sarc_minimal.yaml`.

```yaml
n_simulations: 1000
seed: 42

target_condition: 'euploid'  # 'euploid', 'trisomic', or 'custom'

cisterna_parameters:
  euploid:
    z_length: [414, 109]            # mean, std (nm)
    sarcomere_length: [1638, 114]
    m_line_length: [469, 77]
  trisomic:
    z_length: [350, 108]
    sarcomere_length: [1281, 89]
    m_line_length: [366, 60]

noise_sensitivity:
  enabled: true
  noise_levels: [0, 5, 10, 15, 20, 30, 50, 75, 100]  # nm
  noise_model: 'isotropic'                            # or 'anisotropic'
  n_replicates: 100

boundary_analysis:
  enabled: true
  tolerance: 0.05
  n_boundary_samples: 500
  offset_range: [-0.3, 0.3]

output_dir: 'outputs/sarcomere_simulation'
save_raw_data: true
save_figures: true
figure_dpi: 300
```

## Usage

```bash
sarcostat sim sarc                                             # packaged default
sarcostat sim sarc --config examples/configs/config_sarc_minimal.yaml

# Make-target shortcut for the smoke run:
make smoke
```

```python
from dataclasses import asdict
from sarcostat.simulation._artifacts import RunArtifacts
from sarcostat.simulation.sarc import (
    BoundaryClassificationPipeline,
    NoiseSensitivityPipeline,
    SarcomereGenerator,
    SingleSarcomereConfig,
    load_sarcomere_config,
)

cfg = SingleSarcomereConfig()  # or load_sarcomere_config(Path(...))
artifacts = RunArtifacts(cfg.output_dir, timestamped=True)
artifacts.save_parameters(asdict(cfg))

generator = SarcomereGenerator(cfg)
noise = NoiseSensitivityPipeline(cfg, generator, artifacts.logger).run()
artifacts.save_json("noise_sensitivity_summary.json", noise.to_dict())

if cfg.generate_boundary_cases:
    boundary = BoundaryClassificationPipeline(
        cfg, generator, artifacts.logger,
    ).run()
    artifacts.save_json("boundary_analysis_summary.json", boundary.to_dict())
```

## Output Structure

The sarc command stamps each run directory with `run_<YYYYMMDD_HHMMSS>`:

```
<output_dir>/run_<YYYYMMDD_HHMMSS>/
  data/
    simulation_parameters.json
    noise_sensitivity_summary.json
    boundary_analysis_summary.json
  logs/
    simulation.log
    errors.log
  plots/
    noise_sensitivity_combined.{pdf,png}
    boundary_misclassification.{pdf,png}
    example_sarcomeres.{pdf,png}
```

### Shape boundaries

Sarcomeres near the classification boundary (`|Cur - AsR| < boundary_analysis.tolerance`) are most sensitive to annotation noise. The boundary pipeline reports the critical separation `|Cur - AsR|` at which the misclassification rate drops below 10% as a function of noise sigma.

# Bootstrap CI Coverage

## Purpose

`sarcostat.simulation.rigor.coverage` measures the empirical coverage of percentile-bootstrap 95% CIs for the circular mean and the MVL, across a (kappa, N) grid of von Mises samples. The population mean direction is fixed at 0 and the population MVL is `rho(kappa) = I1(kappa) / I0(kappa)`. Empirical coverage close to the nominal 0.95 level indicates the bootstrap procedure is well calibrated; systematic under-coverage flags a methodological caveat to report alongside the inferred CIs.

## Configuration

Smoke-test config: `examples/configs/config_coverage_smoke.yaml`.

```yaml
n_iterations: 50
n_bootstrap: 200
confidence: 0.95
kappa_values: [1.0, 5.0]
n_values: [25, 100]
seed: 42

output_dir: paper/results/coverage_smoke
save_figures: true
figure_dpi: 150
```

## Usage

```bash
sarcostat sim coverage --config examples/configs/config_coverage_smoke.yaml
```

```python
from sarcostat.simulation.rigor.coverage import (
    CoverageConfig, load_coverage_config, run as run_coverage,
)

cfg = CoverageConfig()  # or load_coverage_config(path)
run_coverage(cfg)
```

# Minimum Detectable Effect (MDE)

## Purpose

`sarcostat.simulation.rigor.mde` estimates the smallest mean difference detectable at `alpha = 0.05` and `power = 0.80` by a two-sample, two-sided test (equal-variance Gaussian approximation):

$$\text{MDE} = (z_{\alpha/2} + z_{\beta}) \cdot \sigma \cdot \sqrt{2/N_{\text{per group}}}.$$

Three families of comparisons are supported:

1. MVL between two myofibril groups, parameterised by population concentration kappa and chain length M.
2. AsR between two sarcomere groups, parameterised by annotation noise (nm) and target condition (`euploid` / `trisomic`).
3. Cur between two sarcomere groups, parameterised as in (2).


## Configuration

Smoke-test config: `examples/configs/config_mde_smoke.yaml`.

```yaml
n_replicates_for_sd: 500

kappa_values: [2.0, 10.0]
chain_lengths: [25, 50]
group_sizes: [10, 25, 50]

sarcomere_conditions: [euploid]
annotation_noise_nm: [0.0, 5.0]
sarcomere_group_sizes: [10, 25, 50]

alpha: 0.05
power: 0.80
seed: 42

output_dir: paper/results/mde_smoke
save_figures: true
figure_dpi: 150
```

## Usage

```bash
sarcostat sim mde --config examples/configs/config_mde_smoke.yaml
```

```python
from sarcostat.simulation.rigor.mde import (
    MDEConfig, load_mde_config, run as run_mde,
)

cfg = MDEConfig()  # or load_mde_config(path)
run_mde(cfg)
```

# Reproducibility

- All four pipelines accept a `seed` parameter and derive per-cell RNG seeds deterministically from it.
- `RunArtifacts` writes a timestamped run directory containing a `data/simulation_parameters.json` snapshot of the config, a `logs/simulation.log` execution log, and a `plots/` folder. The combination of seed + config snapshot is sufficient to reproduce a run from source.

# References

- Cisterna, B., Malatesta, M., Zancanaro, C., & Boschi, F. (2021). A computational approach to quantitatively define sarcomere dimensions and arrangement in skeletal muscle. *Computer Methods and Programs in Biomedicine*, 211: 106437. https://doi.org/10.1016/j.cmpb.2021.106437
