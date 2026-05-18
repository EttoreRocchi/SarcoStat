#!/usr/bin/env python3
"""Bootstrap CI coverage validation for circular statistics.

For each (kappa, N) condition we draw `n_iterations` von Mises samples with
known population parameters, compute percentile-bootstrap 95% CIs for the
circular mean and the mean vector length (MVL), and check how often the
true population value falls inside the CI. Empirical coverage close to the
nominal 0.95 level indicates the bootstrap procedure is well-calibrated;
systematic under-coverage flags a methodological caveat that should be
reported alongside the inferred CIs.

The population mean direction is fixed at 0; the population MVL is
rho(kappa) = I1(kappa) / I0(kappa).
"""

from __future__ import annotations

import argparse
import json
import logging
import warnings
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
import yaml
from scipy.special import i0, i1
from scipy.stats import vonmises

from sarcostat.core.circular_stats import to_minuspi_pi

warnings.filterwarnings("ignore")
plt.style.use("seaborn-v0_8-darkgrid")
sns.set_palette("husl")


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
        Master RNG seed; per-iteration seeds are derived deterministically.
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


def load_coverage_config(path: Path) -> CoverageConfig:
    """Load a :class:`CoverageConfig` from a YAML file; unknown keys are ignored."""
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    valid = set(CoverageConfig.__dataclass_fields__.keys())
    filtered = {k: v for k, v in raw.items() if k in valid}
    return CoverageConfig(**filtered)


def population_mvl(kappa: float) -> float:
    """Return the population mean resultant length for a von Mises distribution."""
    if kappa <= 0:
        return 0.0
    return float(i1(kappa) / i0(kappa))


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


def run_coverage_analysis(cfg: CoverageConfig, logger: logging.Logger) -> pd.DataFrame:
    """Run the (kappa, N) coverage sweep and return the tidy result frame.

    For every cell the routine draws ``cfg.n_iterations`` von Mises samples,
    builds percentile-bootstrap CIs for the circular mean and MVL, and
    records empirical coverage plus mean CI width.
    """
    parent_rng = np.random.default_rng(cfg.seed)
    rows: list[dict] = []
    total = len(cfg.kappa_values) * len(cfg.n_values)
    done = 0

    for kappa in cfg.kappa_values:
        rho_true = population_mvl(kappa)
        for n in cfg.n_values:
            done += 1
            logger.info(f"[{done}/{total}] kappa={kappa}, N={n}, rho_true={rho_true:.4f}")
            mean_hits = 0
            mvl_hits = 0
            mean_widths: list[float] = []
            mvl_widths: list[float] = []
            mvl_below = 0
            mvl_above = 0

            for _ in range(cfg.n_iterations):
                sample_seed = int(parent_rng.integers(0, 2**31 - 1))
                rng = np.random.default_rng(sample_seed)
                if kappa <= 0:
                    angles = rng.uniform(-np.pi, np.pi, n)
                else:
                    angles = vonmises.rvs(kappa, loc=0.0, size=n, random_state=rng)
                angles = to_minuspi_pi(angles)

                boot_rng = np.random.default_rng(int(parent_rng.integers(0, 2**31 - 1)))
                _, mean_lo, mean_hi, _, mvl_lo, mvl_hi = _vectorized_bootstrap_cis(
                    angles,
                    n_bootstrap=cfg.n_bootstrap,
                    confidence=cfg.confidence,
                    rng=boot_rng,
                )

                if kappa > 0 and is_in_circular_ci(0.0, mean_lo, mean_hi):
                    mean_hits += 1
                mean_widths.append(circular_arc_length(mean_lo, mean_hi))

                if mvl_lo <= rho_true <= mvl_hi:
                    mvl_hits += 1
                else:
                    if rho_true < mvl_lo:
                        mvl_below += 1
                    else:
                        mvl_above += 1
                mvl_widths.append(mvl_hi - mvl_lo)

            mean_cov = mean_hits / cfg.n_iterations if kappa > 0 else float("nan")
            mvl_cov = mvl_hits / cfg.n_iterations
            rows.append(
                {
                    "kappa": kappa,
                    "n": n,
                    "rho_true": rho_true,
                    "n_iterations": cfg.n_iterations,
                    "n_bootstrap": cfg.n_bootstrap,
                    "mean_coverage": mean_cov,
                    "mvl_coverage": mvl_cov,
                    "mvl_miss_below": mvl_below / cfg.n_iterations,
                    "mvl_miss_above": mvl_above / cfg.n_iterations,
                    "mean_ci_width_rad": float(np.mean(mean_widths)),
                    "mean_ci_width_deg": float(np.degrees(np.mean(mean_widths))),
                    "mvl_ci_width": float(np.mean(mvl_widths)),
                }
            )
    return pd.DataFrame(rows)


def plot_coverage(df: pd.DataFrame, cfg: CoverageConfig, output_dir: Path) -> Path:
    """Render the two-panel coverage summary figure to PDF and PNG."""
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    nominal = cfg.confidence

    palette = sns.color_palette("viridis", n_colors=len(cfg.kappa_values))
    for ax, metric, ylabel, title in [
        (axes[0], "mean_coverage", "Coverage of circular mean", "Circular mean CI"),
        (axes[1], "mvl_coverage", "Coverage of MVL", "MVL CI"),
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
        ax.set_ylim(0.5, 1.02)
        ax.legend(fontsize=8, loc="lower right")
    fig.suptitle(
        f"Bootstrap CI coverage ({cfg.n_iterations} iterations x {cfg.n_bootstrap} resamples)"
    )
    fig.tight_layout()
    out = output_dir / "fig_coverage.pdf"
    fig.savefig(out, dpi=cfg.figure_dpi, bbox_inches="tight")
    fig.savefig(out.with_suffix(".png"), dpi=cfg.figure_dpi, bbox_inches="tight")
    plt.close(fig)
    return out


def setup_logging(output_dir: Path) -> logging.Logger:
    """Configure a console + file logger writing under ``output_dir``."""
    output_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("coverage_analysis")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
    fh = logging.FileHandler(output_dir / "coverage_analysis.log", mode="w")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def run(cfg: CoverageConfig) -> Path:
    """Execute the coverage validation and return the run directory."""
    output_dir = Path(cfg.output_dir) / datetime.now().strftime("run_%Y%m%d_%H%M%S")
    logger = setup_logging(output_dir)
    logger.info("=" * 60)
    logger.info("CI COVERAGE VALIDATION")
    logger.info("=" * 60)
    logger.info(f"kappa_values: {cfg.kappa_values}")
    logger.info(f"n_values:     {cfg.n_values}")
    logger.info(f"n_iterations: {cfg.n_iterations}")
    logger.info(f"n_bootstrap:  {cfg.n_bootstrap}")
    logger.info(f"confidence:   {cfg.confidence}")
    logger.info(f"seed:         {cfg.seed}")

    df = run_coverage_analysis(cfg, logger)
    csv = output_dir / "coverage_results.csv"
    df.to_csv(csv, index=False)
    logger.info(f"Results table -> {csv}")

    cfg_dump = output_dir / "config.json"
    with open(cfg_dump, "w") as f:
        json.dump(asdict(cfg), f, indent=2)

    if cfg.save_figures:
        fig_path = plot_coverage(df, cfg, output_dir)
        logger.info(f"Figure -> {fig_path}")

    nominal = cfg.confidence
    logger.info(f"\nSummary (coverage relative to nominal {nominal:.2f}):")
    summary = df.assign(
        mean_dev=df["mean_coverage"] - nominal,
        mvl_dev=df["mvl_coverage"] - nominal,
    )[["kappa", "n", "rho_true", "mean_coverage", "mean_dev", "mvl_coverage", "mvl_dev"]]
    logger.info("\n" + summary.to_string(index=False, float_format="%.4f"))

    return output_dir


def main() -> None:
    """CLI entry point for ``python -m sarcostat.simulation.rigor.coverage``."""
    parser = argparse.ArgumentParser(description="CI coverage validation")
    parser.add_argument("--config", "-c", type=Path, default=None)
    args = parser.parse_args()
    cfg = load_coverage_config(args.config) if args.config else CoverageConfig()
    out = run(cfg)
    print(f"\nCoverage analysis complete. Output: {out}")


if __name__ == "__main__":
    main()
