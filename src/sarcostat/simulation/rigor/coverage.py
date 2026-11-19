"""Bootstrap CI coverage validation for circular statistics.

For each (kappa, N) condition we draw `n_iterations` von Mises samples with
known population parameters, compute percentile-bootstrap CIs for the
circular mean and the mean vector length (MVL), and check how often the
true population value falls inside the CI. Empirical coverage close to the
nominal level indicates the bootstrap procedure is well-calibrated;
systematic under-coverage flags a methodological caveat that should be
reported alongside the inferred CIs.

The population mean direction is fixed at 0; the population MVL is
rho(kappa) = I1(kappa) / I0(kappa). Each (kappa, N) cell draws from its
own seed stream, so a cell's result does not depend on the rest of the
grid.
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from scipy.special import i0e, i1e
from scipy.stats import vonmises

from sarcostat.core.chain_statistics import mvl_confidence_interval
from sarcostat.core.circular_stats import to_minuspi_pi

from .._artifacts import RunArtifacts, run_metadata
from .._config_io import (
    as_bool,
    as_float,
    as_int,
    as_list,
    as_number,
    as_str,
    load_flat_dataclass,
    require_unique,
)
from .._plotting import simulation_style
from .._seeding import per_cell_seed


@dataclass
class CoverageConfig:
    """Configuration for the bootstrap CI coverage validation.

    Attributes
    ----------
    n_iterations : int
        Monte Carlo iterations per (kappa, N) cell.
    n_bootstrap : int
        Bootstrap resamples used to build each CI.
    confidence : float
        Nominal confidence level for the CIs.
    kappa_values : list of float
        Concentration axis swept during the validation.
    n_values : list of int
        Sample-size axis swept during the validation.
    seed : int
        Master RNG seed; per-cell seed streams are derived from it.
    output_dir : str
        Base directory where the run-stamped output folder is created.
    save_figures : bool
        If True, the coverage summary figure is rendered to PDF and PNG.
    figure_dpi : int
        DPI used for matplotlib output.
    """

    n_iterations: int = 1000
    n_bootstrap: int = 1000
    confidence: float = 0.95
    kappa_values: list[float] = field(default_factory=lambda: [0.5, 1.0, 2.0, 5.0, 10.0])
    n_values: list[int] = field(default_factory=lambda: [10, 25, 50, 100])
    seed: int = 42
    output_dir: str = "outputs/coverage_analysis"
    save_figures: bool = True
    figure_dpi: int = 300

    def __post_init__(self) -> None:
        """Reject out-of-range parameters."""
        if self.n_iterations < 1:
            raise ValueError(f"n_iterations must be positive, got {self.n_iterations}")
        if self.n_bootstrap < 1:
            raise ValueError(f"n_bootstrap must be positive, got {self.n_bootstrap}")
        if not (0.0 < self.confidence < 1.0):
            raise ValueError(f"confidence must be in (0, 1), got {self.confidence}")
        if not self.kappa_values or not self.n_values:
            raise ValueError("kappa_values and n_values must not be empty")
        if any(k < 0 for k in self.kappa_values):
            raise ValueError(f"kappa_values must be non-negative, got {self.kappa_values}")
        if any(n < 2 for n in self.n_values):
            raise ValueError(f"n_values must be at least 2, got {self.n_values}")
        require_unique(list(self.kappa_values), "kappa_values")
        require_unique(list(self.n_values), "n_values")


_COVERAGE_CONVERTERS = {
    "n_iterations": as_int,
    "n_bootstrap": as_int,
    "confidence": as_float,
    "kappa_values": lambda v, name: as_list(v, name, as_number),
    "n_values": lambda v, name: as_list(v, name, as_int),
    "seed": as_int,
    "output_dir": as_str,
    "save_figures": as_bool,
    "figure_dpi": as_int,
}


def load_coverage_config(path: str | Path) -> CoverageConfig:
    """Load a :class:`CoverageConfig` from a YAML file; unknown keys raise ``ValueError``."""
    return load_flat_dataclass(path, CoverageConfig, _COVERAGE_CONVERTERS)


def population_mvl(kappa: float) -> float:
    """Return the population mean resultant length for a von Mises distribution."""
    if kappa <= 0:
        return 0.0
    return float(i1e(kappa) / i0e(kappa))


def circular_distance(a: float, b: float) -> float:
    """Geodesic distance between two angles in radians, in [0, pi]."""
    diff = (a - b + np.pi) % (2 * np.pi) - np.pi
    return float(abs(diff))


def is_in_circular_ci(true_mean: float, ci_lower: float, ci_upper: float) -> bool:
    """Check whether ``true_mean`` lies inside the circular CI ``[ci_lower, ci_upper]``.

    The CI is a directed arc going counter-clockwise from ``ci_lower`` to
    ``ci_upper`` in ``(-pi, pi]``; it wraps the discontinuity when
    ``ci_lower > ci_upper``.
    """
    tm = float(to_minuspi_pi(np.array([true_mean]))[0])
    lo = float(to_minuspi_pi(np.array([ci_lower]))[0])
    hi = float(to_minuspi_pi(np.array([ci_upper]))[0])
    if lo <= hi:
        return lo <= tm <= hi
    return tm >= lo or tm <= hi


def circular_arc_length(ci_lower: float, ci_upper: float) -> float:
    """Counter-clockwise arc length from ci_lower to ci_upper, in [0, 2pi)."""
    lo = float(to_minuspi_pi(np.array([ci_lower]))[0])
    hi = float(to_minuspi_pi(np.array([ci_upper]))[0])
    return float((hi - lo) % (2 * np.pi))


def _vectorized_bootstrap_cis(
    angles: np.ndarray,
    n_bootstrap: int,
    confidence: float,
    rng: np.random.Generator,
) -> tuple[float, float, float, float, float, float]:
    """Vectorized percentile bootstrap CIs for circular mean and MVL.

    Mirrors the algorithm in `core.circular_stats.bootstrap_circular_mean_ci`
    and `bootstrap_mvl_ci` but resamples all bootstrap replicates at once
    using NumPy, dropping per-call Python overhead.

    Returns
    -------
    (mean_pt, mean_lo, mean_hi, mvl_pt, mvl_lo, mvl_hi) - all in (-pi, pi] for
    angles, [0, 1] for MVL.
    """
    n = angles.size
    alpha = 1.0 - confidence

    cos_a = np.cos(angles)
    sin_a = np.sin(angles)
    mean_cos_pt = cos_a.mean()
    mean_sin_pt = sin_a.mean()
    mean_pt = float(np.arctan2(mean_sin_pt, mean_cos_pt))

    idx = rng.integers(0, n, size=(n_bootstrap, n))
    boot_cos = cos_a[idx].mean(axis=1)
    boot_sin = sin_a[idx].mean(axis=1)
    boot_means = np.arctan2(boot_sin, boot_cos)
    boot_mvls = np.sqrt(boot_cos**2 + boot_sin**2)

    centered = (boot_means - mean_pt + np.pi) % (2 * np.pi) - np.pi
    lo_centered = np.percentile(centered, 100 * alpha / 2)
    hi_centered = np.percentile(centered, 100 * (1 - alpha / 2))
    mean_lo = float(((lo_centered + mean_pt + np.pi) % (2 * np.pi)) - np.pi)
    mean_hi = float(((hi_centered + mean_pt + np.pi) % (2 * np.pi)) - np.pi)

    mvl_pt = float(np.sqrt(mean_cos_pt**2 + mean_sin_pt**2))
    mvl_lo = float(np.percentile(boot_mvls, 100 * alpha / 2))
    mvl_hi = float(np.percentile(boot_mvls, 100 * (1 - alpha / 2)))

    return mean_pt, mean_lo, mean_hi, mvl_pt, mvl_lo, mvl_hi


def _coverage_cell(cfg: CoverageConfig, kappa: float, n: int) -> dict[str, float]:
    """Coverage statistics for one (kappa, N) cell, from that cell's own seed stream."""
    rng = np.random.default_rng(per_cell_seed(cfg.seed, kappa, n))
    rho_true = population_mvl(kappa)
    mean_hits = 0
    mvl_hits = 0
    mvl_below = 0
    mean_widths: list[float] = []
    mvl_widths: list[float] = []
    vm_hits = 0
    vm_below = 0
    vm_widths: list[float] = []

    for _ in range(cfg.n_iterations):
        if kappa <= 0:
            angles = rng.uniform(-np.pi, np.pi, n)
        else:
            angles = vonmises.rvs(kappa, loc=0.0, size=n, random_state=rng)
        angles = to_minuspi_pi(angles)

        _, mean_lo, mean_hi, _, mvl_lo, mvl_hi = _vectorized_bootstrap_cis(
            angles, n_bootstrap=cfg.n_bootstrap, confidence=cfg.confidence, rng=rng
        )
        if kappa > 0 and is_in_circular_ci(0.0, mean_lo, mean_hi):
            mean_hits += 1
        mean_widths.append(circular_arc_length(mean_lo, mean_hi))

        if mvl_lo <= rho_true <= mvl_hi:
            mvl_hits += 1
        elif rho_true < mvl_lo:
            mvl_below += 1
        mvl_widths.append(mvl_hi - mvl_lo)

        r_obs = float(np.abs(np.mean(np.exp(1j * angles))))
        vm_lo, vm_hi = mvl_confidence_interval(r_obs, n, cfg.confidence)
        if vm_lo <= rho_true <= vm_hi:
            vm_hits += 1
        elif rho_true < vm_lo:
            vm_below += 1
        vm_widths.append(vm_hi - vm_lo)

    iters = cfg.n_iterations
    return {
        "kappa": kappa,
        "n": n,
        "rho_true": rho_true,
        "n_iterations": iters,
        "n_bootstrap": cfg.n_bootstrap,
        "mean_coverage": mean_hits / iters if kappa > 0 else float("nan"),
        "mvl_coverage": mvl_hits / iters,
        "mvl_miss_below": mvl_below / iters,
        "mvl_miss_above": (iters - mvl_hits - mvl_below) / iters,
        "mean_ci_width_rad": float(np.mean(mean_widths)),
        "mean_ci_width_deg": float(np.degrees(np.mean(mean_widths))),
        "mvl_ci_width": float(np.mean(mvl_widths)),
        "mvl_vonmises_coverage": vm_hits / iters,
        "mvl_vonmises_miss_below": vm_below / iters,
        "mvl_vonmises_ci_width": float(np.mean(vm_widths)),
    }


def compute_coverage_grid(cfg: CoverageConfig, logger: logging.Logger) -> pd.DataFrame:
    """Run the (kappa, N) coverage sweep and return the tidy result frame.

    For every cell the routine draws ``cfg.n_iterations`` von Mises samples,
    builds percentile-bootstrap CIs for the circular mean and MVL, and
    records empirical coverage plus mean CI width.
    """
    rows: list[dict[str, float]] = []
    total = len(cfg.kappa_values) * len(cfg.n_values)
    for done, (kappa, n) in enumerate(
        ((k, n) for k in cfg.kappa_values for n in cfg.n_values), start=1
    ):
        logger.info(f"[{done}/{total}] kappa={kappa}, N={n}, rho_true={population_mvl(kappa):.4f}")
        rows.append(_coverage_cell(cfg, kappa, n))
    return pd.DataFrame(rows)


def plot_coverage(df: pd.DataFrame, cfg: CoverageConfig) -> plt.Figure:
    """Build the two-panel coverage summary figure (call inside :func:`simulation_style`)."""
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))
    nominal = cfg.confidence

    palette = sns.color_palette("viridis", n_colors=len(cfg.kappa_values))
    for ax, metric, ylabel, title in [
        (axes[0], "mean_coverage", "Coverage of circular mean", "Circular mean, bootstrap CI"),
        (axes[1], "mvl_coverage", "Coverage of MVL", "MVL, percentile bootstrap CI"),
        (axes[2], "mvl_vonmises_coverage", "Coverage of MVL", "MVL, von Mises CI"),
    ]:
        for color, kappa in zip(palette, cfg.kappa_values, strict=False):
            sub = df[df["kappa"] == kappa].sort_values("n")
            if metric == "mean_coverage" and kappa <= 0:
                continue
            ax.plot(
                sub["n"],
                sub[metric],
                marker="o",
                color=color,
                label=f"kappa = {kappa}",
            )
        ax.axhline(nominal, ls="--", color="black", lw=1, label=f"nominal {nominal:.2f}")
        ax.set_xlabel("Chain length N")
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.set_ylim(0.4, 1.02)
        ax.legend(fontsize=8, loc="lower right")
    fig.suptitle(
        f"CI coverage ({cfg.n_iterations} iterations; bootstrap: {cfg.n_bootstrap} resamples)"
    )
    fig.tight_layout()
    return fig


def run(
    cfg: CoverageConfig,
    *,
    config_path: str | Path | None = None,
    console_handler: logging.Handler | None = None,
) -> Path:
    """Execute the coverage validation and return the run directory.

    Output follows the :class:`~sarcostat.simulation._artifacts.RunArtifacts`
    layout: ``data/coverage_results.csv`` and
    ``data/simulation_parameters.json``, ``plots/fig_coverage.{pdf,png}``,
    and ``logs/``.
    """
    artifacts = RunArtifacts(
        cfg.output_dir,
        timestamped=True,
        logger_name="sarcostat.simulation.coverage",
        console_handler=console_handler,
    )
    logger = artifacts.logger
    artifacts.save_parameters({**asdict(cfg), **run_metadata(config_path)})
    logger.info("CI coverage validation: kappa=%s, N=%s", cfg.kappa_values, cfg.n_values)
    logger.info(
        "n_iterations=%d, n_bootstrap=%d, confidence=%s, seed=%d",
        cfg.n_iterations,
        cfg.n_bootstrap,
        cfg.confidence,
        cfg.seed,
    )

    with warnings.catch_warnings():
        # scipy/numpy numerical warnings from extreme cells are expected noise here.
        warnings.simplefilter("ignore", RuntimeWarning)
        df = compute_coverage_grid(cfg, logger)
    artifacts.save_csv("coverage_results.csv", df)

    if cfg.save_figures:
        with simulation_style():
            fig = plot_coverage(df, cfg)
            artifacts.save_figure(fig, "fig_coverage", dpi=cfg.figure_dpi)
            plt.close(fig)

    nominal = cfg.confidence
    summary = df.assign(
        mean_dev=df["mean_coverage"] - nominal,
        mvl_dev=df["mvl_coverage"] - nominal,
    )[["kappa", "n", "rho_true", "mean_coverage", "mean_dev", "mvl_coverage", "mvl_dev"]]
    logger.info(
        "Coverage relative to nominal %.2f:\n%s",
        nominal,
        summary.to_string(index=False, float_format="%.4f"),
    )
    return artifacts.output_dir


__all__ = [
    "CoverageConfig",
    "load_coverage_config",
    "population_mvl",
    "circular_distance",
    "is_in_circular_ci",
    "circular_arc_length",
    "compute_coverage_grid",
    "plot_coverage",
    "run",
]
