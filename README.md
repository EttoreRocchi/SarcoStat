# SarcoStat

<p align="center">
  <img src="docs/logo.png" alt="SarcoStat Logo" width="300"/>
</p>

A toolkit for analyzing sarcomeric sequences and morphometry, featuring interactive image analysis, circular statistics, and Monte Carlo simulations.

## Overview

SarcoStat provides two complementary analysis modes:

1. **Myofibril Linearity Analysis** - Quantify alignment and straightness of sarcomeric chains using circular statistics
2. **Single Sarcomere Morphometry** - Measure individual sarcomere geometry (aspect ratio, curviness, shape index) following Cisterna et al. (2021)

## Documentation

For detailed usage instructions, see:

- **[GUI User Guide](docs/gui.md)** - Complete interface documentation, workflows, and keyboard shortcuts
- **[Simulation Pipeline](docs/simulation.md)** - Monte Carlo methods, configuration, and output interpretation

## Features

### GUI Analysis Tool
- **Dual-mode operation**: Switch between myofibril linearity and single sarcomere analysis
- **Multi-format support**: TIFF, JPEG, PNG images
- **Interactive annotation**: Point-and-click interface with visual feedback
- **Real-time metrics**: Instant calculation and display of all parameters
- **Export options**: JSON, CSV, and text reports

### Sarcomere Morphometry
Following Cisterna et al. (2021), implements all 9 morphometric parameters:

| Parameter | Description |
|-----------|-------------|
| Z-line lengths | Sarcomere boundary lengths (AB, EF) |
| M-line length | Central line length (CD) |
| Sarcomere length | Distance between Z-line midpoints (MN) |
| Aspect Ratio (AsR) | `MN / mean(Z-lines)` - elongation measure |
| Curviness (Cur) | `MN / M-line` - lateral bulging measure |
| Shape Index | `Cur / AsR` - combined shape metric |
| Alpha angle | Inter-sarcomere axis deviation |
| H parameter | Z-line alignment between myofibrils |
| H std | Alignment variability |

**Shape Classification:**
- **SQUARE**: AsR ≈ 1 (equal length and width)
- **RECTANGULAR**: Cur ≈ AsR (parallel sides)
- **BARREL**: Cur < AsR (M-line wider than Z-lines)
- **HOURGLASS**: Cur > AsR (Z-lines wider than M-line)

### Circular Statistics
- Mean Vector Length (R) with bootstrap confidence intervals
- Rayleigh test with effect size interpretation
- V-test for expected direction
- Linearity ratio calculation
- Concentration parameter estimation

### Simulation Pipeline
- Monte Carlo simulations with configurable length and angle distributions
- Boxplot, polar, and heatmap visualisations across (kappa, N)
- Bootstrap CI coverage validation
- Minimum detectable effect (MDE) power analysis

## Installation

### From PyPI (recommended)
```bash
pip install sarcostat
```

The package is published on PyPI: <https://pypi.org/project/sarcostat/>.

### Development Installation
```bash
# Clone the repository
git clone https://github.com/EttoreRocchi/SarcoStat.git
cd SarcoStat

pip install -e ".[dev]"
```

## Quick Start

### Running the GUI

```bash
sarcostat gui
```

### Running Simulations

```bash
# Show available simulation modes
sarcostat sim --help

# Chain simulation (myofibril linearity validation)
sarcostat sim chain                           # With packaged default config
sarcostat sim chain --config custom.yaml      # With custom config

# Single sarcomere simulation (morphometry validation)
sarcostat sim sarc                            # With packaged default config
sarcostat sim sarc --config custom.yaml       # With custom config

# Rigor analyses
sarcostat sim coverage --config examples/configs/config_coverage_smoke.yaml
sarcostat sim mde --config examples/configs/config_mde_smoke.yaml
```

**Chain simulation** validates LR, MVL, and Rayleigh test across (κ, N) parameter space.

**Sarc simulation** tests noise sensitivity of AsR, Cur, and shape classification.

## GUI Analysis Workflows

### Myofibril Linearity Mode

Analyze the straightness and alignment of sarcomeric chains:

1. **Load Image**: File → Open (supports TIFF, JPEG, PNG)
2. **Set Scale** (optional): Configure pixel-to-unit conversion
3. **Select Mode**: Choose "Myofibril Linearity" from the mode selector
4. **Annotate**: Click to place points along the myofibril chain
5. **Connect**: Press 'c' or click "Connect Points" to visualize segments
6. **Analyze**: Press 'a' or click "Analyze Sequence"
7. **Export**: Save results to JSON/CSV/TXT

**Keyboard Shortcuts:**
- `s` - Confirm current segment
- `c` - Connect midpoints
- `a` - Analyze sequence
- `Delete` - Remove last point or segment
- `Escape` - Clear all annotations

<p align="center">
  <img src="docs/gui_linearity.png" alt="Myofibril linearity GUI screenshot" width="600"/>
  <br/>
  <em>Multi-sarcomere linearity mode. Electron micrograph: Histology Guide, EM 358.</em>
</p>

### Single Sarcomere Mode

Measure individual sarcomere geometry using 6-point annotation:

1. **Load Image**: File → Open
2. **Select Mode**: Choose "Single Sarcomere" from the mode selector
3. **Annotate 6 points** in order:
   - Points A, B → Z-line 1 (red)
   - Points C, D → M-line (blue)
   - Points E, F → Z-line 2 (green)
4. **View Results**: Morphometry tab shows all 9 parameters
5. **Export**: Save sarcomere metrics

<p align="center">
  <img src="docs/gui_single_sarcomere.png" alt="Single-sarcomere morphometry GUI screenshot" width="600"/>
  <br/>
  <em>Single-sarcomere morphometry mode. Electron micrograph: Histology Guide, EM 358.</em>
</p>

## Metrics Reference

### Circular Statistics

| Metric | Formula | Range | Interpretation |
|--------|---------|-------|----------------|
| Linearity Ratio | `end_to_end / total_length` | 0-1 | 1 = straight, 0 = circular |
| Mean Vector Length (R) | Resultant of unit vectors | 0-1 | 1 = aligned, 0 = random |
| Rayleigh p-value | Test for uniformity | 0-1 | <0.05 = directional |
| V-test p-value | Test vs expected direction | 0-1 | <0.05 = matches expected |

### Morphometry (Cisterna et al. 2021)

| Metric | Formula |
|--------|---------|
| AsR | `MN / ((AB+EF)/2)` |
| Cur | `MN / CD` |
| Shape Index | `Cur / AsR` |

## Programmatic Usage

### Analyzing a Segment Sequence

```python
from sarcostat.core.circular_stats import analyze_segment_sequence
import numpy as np

# Define points (x, y coordinates)
points = np.array([
    [0, 0], [1, 0.2], [2, 0.5], [3, 0.9]
])

# Analyze
metrics = analyze_segment_sequence(points, pixel_to_unit=0.065, unit_name="µm")
print(metrics.summary_string())
```

### Computing Sarcomere Geometry

```python
from sarcostat.core.sarcomere_morphometry import calculate_sarcomere_geometry

# 6-point annotation
geometry = calculate_sarcomere_geometry(
    point_a=(0, 0), point_b=(0, 100),      # Z-line 1
    point_c=(50, 0), point_d=(50, 100),    # M-line
    point_e=(100, 0), point_f=(100, 100),  # Z-line 2
    pixel_to_unit=0.065,
    unit="µm"
)

print(f"Aspect Ratio: {geometry.aspect_ratio:.2f}")
print(f"Curviness: {geometry.curviness:.2f}")
print(f"Shape: {geometry.shape_classification.name}")
```

### Circular Statistics with Confidence Intervals

```python
from sarcostat.core.circular_stats import (
    compute_circular_statistics_report,
    bootstrap_circular_mean_ci,
    bootstrap_mvl_ci
)
import numpy as np

# Relative angles (radians)
angles = np.array([0.1, -0.05, 0.15, 0.0, -0.1])

# Full statistical report
report = compute_circular_statistics_report(angles)
print(f"Mean: {report.circular_mean_deg:.1f}°")
print(f"MVL: {report.mean_vector_length:.3f} (95% CI: {report.mvl_ci})")
print(f"Rayleigh p: {report.rayleigh_p:.4f} ({report.rayleigh_interpretation['recommendation']})")

# Or individual CIs
mean_ci = bootstrap_circular_mean_ci(angles, confidence=0.95, n_bootstrap=10000)
mvl_ci = bootstrap_mvl_ci(angles, confidence=0.95, n_bootstrap=10000)
```

### Running Simulations Programmatically

```python
# Chain simulation (myofibril linearity)
from sarcostat.simulation import (
    ChainSimulator,
    RunArtifacts,
    load_chain_config,
    run_chain_monte_carlo,
    summarise_chain_results,
)

cfg = load_chain_config("examples/configs/config_minimal.yaml")
artifacts = RunArtifacts(cfg.output_dir)
simulator = ChainSimulator.from_config(cfg)
results = run_chain_monte_carlo(simulator, cfg, logger=artifacts.logger)
summary = summarise_chain_results(results, cfg.kappa_values, cfg.n_sarcomeres)

# Single sarcomere simulation (morphometry validation)
from sarcostat.simulation import (
    NoiseSensitivityPipeline,
    SarcomereGenerator,
    SingleSarcomereConfig,
)

cfg = SingleSarcomereConfig(
    n_simulations=1000,
    noise_levels=[0, 10, 20, 50],
    target_condition="euploid",
)
noise_results = NoiseSensitivityPipeline(
    cfg=cfg, generator=SarcomereGenerator(cfg)
).run()
```

## Configuration

### Chain Simulation Config (config.yaml)

```yaml
# Basic parameters
n_simulations: 1000
n_sarcomeres: [5, 10, 25, 50, 100]
kappa_values: [0, 1, 5, 10, 50]

# Length distribution
length_distribution: 'truncnorm'
length_mean: 1.0
length_std: 0.25

# Angular distribution
angular_distribution: 'vonmises'
angular_correlation: 0.0

# Advanced options
enable_persistence: false
enable_curvature_bias: false
```

### Sarcomere Simulation Config (sarcomere_config.yaml)

```yaml
# Simulation settings
n_simulations: 1000
target_condition: 'euploid'  # 'euploid', 'trisomic', or 'custom'

# Cisterna et al. (2021) reference values (nm)
cisterna_parameters:
  euploid:
    z_length: [414, 109]
    sarcomere_length: [1638, 114]
  trisomic:
    z_length: [350, 108]
    sarcomere_length: [1281, 89]

# Noise sensitivity analysis
noise_sensitivity:
  enabled: true
  noise_levels: [0, 5, 10, 15, 20, 30, 50, 75, 100]  # nm
  n_replicates: 100

# Shape boundary analysis
boundary_analysis:
  enabled: true
  n_boundary_samples: 500
```

## License

MIT License - See LICENSE file for details.

## Contributing

Contributions welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Add tests for new features
4. Run the test suite
5. Submit a pull request

## Contact

For questions or issues, please open a GitHub issue.
