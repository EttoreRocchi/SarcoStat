# GUI User Guide

## Overview

SarcoStat ships an interactive window for annotating sarcomeric structures from microscopy images. The application supports two complementary modes:

1. Myofibril Linearity Mode - quantify alignment and straightness of sarcomeric chains from a sequence of Z-line midpoints.
2. Single Sarcomere Mode - measure individual sarcomere morphometry (aspect ratio, curviness, shape classification) following Cisterna et al. (2021).

## System Requirements

- Python >= 3.10
- Display resolution: 1280x720 minimum (1920x1080 recommended)
- Supported image formats: TIFF, JPEG, PNG

## Installation

SarcoStat is published on PyPI; the GUI ships in the same package as the core library and CLI:

```bash
pip install sarcostat
```

For development:

```bash
git clone https://github.com/EttoreRocchi/SarcoStat.git
cd SarcoStat
pip install -e ".[dev]"
```

## Launching

The GUI is exposed as a subcommand of the CLI:

```bash
sarcostat gui
```

Equivalent programmatic launch:

```python
from sarcostat.gui.gui import main
main()
```

## Interface Layout

### Toolbar Row 1: Analysis Controls

| Control | Function |
|---------|----------|
| Mode | Switch between Myofibril Linearity and Single Sarcomere modes |
| Load Image | Open image file (TIFF, JPEG, PNG) |
| Start Annotating | Enable point placement on the image |
| Confirm Segment | Validate current two-point segment (Myofibril mode) |
| Connect Midpoints | Link segment midpoints to form the analysis chain |
| Analyze | Compute statistics for the annotated sequence |
| Undo | Remove last annotation |
| Clear All | Reset all annotations |

### Toolbar Row 2: View and Export

| Control | Function |
|---------|----------|
| Zoom In/Out | Adjust image magnification |
| Reset View | Return to original zoom and pan state |
| Scale | Set pixel-to-physical-unit conversion |
| Export Results | Save current analysis to JSON or CSV |
| Export History | Save complete session history |

### Results Panel

Tabbed interface displaying:

- Summary: overview of current analysis metrics
- Segments: per-segment measurements
- Morphometry: sarcomere shape parameters (Single Sarcomere mode)
- History: accumulated analyses from the session

## Myofibril Linearity Mode

### Purpose

Quantify the straightness and angular alignment of sarcomeric chains by analyzing the sequence of Z-line midpoints.

### Workflow

1. Load Image: open a microscopy image.
2. Set Scale (optional): configure pixel-to-unit conversion.
3. Start Annotating: enable annotation mode.
4. Define Segments: click two points to mark each Z-line.
5. Confirm Segment: press `S` or click the button to validate.
6. Repeat for every Z-line in the myofibril.
7. Connect Midpoints: press `C` to link segment centers.
8. Analyze: press `A` to compute statistics.

### Output Metrics

#### Linearity Ratio

$$LR = \frac{d_{\text{end-to-end}}}{L_{\text{total}}}$$

LR is the reciprocal of the alignment index defined in Rocchi et al. (2022): values close to 1 indicate a straight chain.

#### Statistical Tests

- Rayleigh test (`H0`: angles are uniformly distributed); `p < 0.05` indicates a significant directional preference.
- V-test (alignment with an expected direction, default 0); `p < 0.05` indicates significant alignment with the reference.

## Single Sarcomere Mode

### Purpose

Measure individual sarcomere geometry following Cisterna et al. (2021), computing Z-line / M-line / sarcomere lengths, aspect ratio, curviness, and shape classification.

### Workflow

1. Load Image: open a microscopy image.
2. Select Mode: choose "Single Sarcomere" from the dropdown.
3. Annotate 6 Points in order:
   - Points A, B - first Z-line (red)
   - Points C, D - M-line (blue)
   - Points E, F - second Z-line (green)
4. View Results: metrics appear automatically in the Morphometry tab.

### Output Metrics

#### Primary Measurements

| Metric | Definition |
|--------|------------|
| Z-line 1 length | Distance A-B |
| Z-line 2 length | Distance E-F |
| M-line length | Distance C-D |
| Sarcomere length | Distance between Z-line midpoints (MN) |

#### Derived Parameters

Aspect Ratio (AsR):

$$\text{AsR} = \frac{MN}{(|AB| + |EF|)/2}$$

Curviness (Cur):

$$\text{Cur} = \frac{MN}{|CD|}$$

Shape Index:

$$\text{SI} = \frac{\text{Cur}}{\text{AsR}}$$

#### Shape Classification

| Condition | Shape | Description |
|-----------|-------|-------------|
| AsR ~ 1 | SQUARE | Equal length and width |
| Cur ~ AsR | RECTANGULAR | Parallel lateral boundaries |
| Cur < AsR | BARREL | M-line wider than Z-lines (convex sides) |
| Cur > AsR | HOURGLASS | Z-lines wider than M-line (concave sides) |

The classification tolerance is configurable via `sarcostat.core.sarcomere_morphometry.DEFAULT_SHAPE_TOLERANCE`.

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `S` | Confirm current segment |
| `C` | Connect midpoints |
| `A` | Analyze sequence |
| `Delete` | Remove last point or segment |
| `Escape` | Clear all annotations |
| `Space` (hold) | Pan mode (drag to move view) |
| Scroll wheel | Zoom in / out |

## Scale Calibration

Accurate physical measurements require proper scale calibration:

1. Enter the pixel size in the first field (e.g. `0.05`).
2. Enter the unit name in the second field (e.g. `um`).
3. Click Apply.

## Export Formats

### JSON Export

Structured data including segment coordinates, computed metrics with confidence intervals, analysis parameters, timestamp, and SarcoStat version.

### CSV Export

One row per analysis, with metric columns (LR, R, Rayleigh / V test p-values, AsR, Cur, shape) and optional segment-level rows.

## References

- Cisterna, B., Malatesta, M., Zancanaro, C., & Boschi, F. (2021). A computational approach to quantitatively define sarcomere dimensions and arrangement in skeletal muscle. *Computer Methods and Programs in Biomedicine*, 211: 106437. https://doi.org/10.1016/j.cmpb.2021.106437
- Rocchi, E., Peluso, S., Amatori, S., & Sisti, D. (2022). New indexes for myofibril linearity in muscle image analysis. *European Journal of Translational Myology*, 32(4): 10736. https://doi.org/10.4081/ejtm.2022.10736
