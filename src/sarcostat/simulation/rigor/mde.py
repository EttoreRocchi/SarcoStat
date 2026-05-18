#!/usr/bin/env python3
"""Minimum detectable effect (MDE) analysis for SarcoStat metrics.

For each metric we estimate the population standard deviation under the
specified condition by Monte Carlo simulation, then compute the smallest
mean difference detectable at alpha = 0.05 and power = 0.80 by a two-sample
two-sided test (equal-variance t approximation), using

    MDE = (z_{alpha/2} + z_beta) x sigma x sqrt(2 / N_per_group).

Three families of comparisons are supported:

  1. MVL between two myofibril groups, parameterised by population
     concentration kappa and per-myofibril chain length M_chain.
  2. AsR between two sarcomere groups, parameterised by annotation
     noise sigma_anno (nm) and target reference condition (euploid /
     trisomic).
  3. Cur between two sarcomere groups, parameterised as in (2).

The output is a tidy table the manuscript can quote directly:
"to detect a difference of Delta in <metric> with 80% power and alpha = 0.05
requires N >= <...> sarcomeres per group."
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
from scipy.stats import norm, vonmises

from sarcostat.simulation.sarc import (
    Condition,
    GaussianNoise,
    SarcomereGenerator,
    SingleSarcomereConfig,
    compute_metrics_from_points,
)

warnings.filterwarnings("ignore")
plt.style.use("seaborn-v0_8-darkgrid")
sns.set_palette("husl")

Z_ALPHA_TWO = float(norm.ppf(1 - 0.025))  # 1.9600
Z_BETA_80 = float(norm.ppf(0.80))  # 0.8416


@dataclass
class MDEConfig:
    """Configuration for the minimum-detectable-effect analysis.

    Attributes
    ----------
    n_replicates_for_sd : int
        Monte Carlo replicates used to estimate the population SD of
        each metric.
    kappa_values : list of float
        Concentration axis for the MVL family of comparisons.
    chain_lengths : list of int
        Chain-length axis ``M`` for the MVL family.
    group_sizes : list of int
        Per-group sample sizes ``N`` for the MVL family.
    sarcomere_conditions : list of str
        Reference conditions to evaluate (``'euploid'`` and/or
        ``'trisomic'``).
    annotation_noise_nm : list of float
        Isotropic annotation noise standard deviations (nm).
    sarcomere_group_sizes : list of int
        Per-group sample sizes ``N`` for the AsR / Cur families.
    alpha : float
        Two-sided significance level used in the MDE formula.
    power : float
        Target statistical power.
    seed : int
        Master RNG seed.
    output_dir : str
        Base directory where the run-stamped output folder is created.
    save_figures : bool
        If True, the summary figure is rendered to PDF and PNG.
    figure_dpi : int
        DPI used for matplotlib output.
    """

    n_replicates_for_sd: int = 5000

    kappa_values: list[float] = field(default_factory=lambda: [1.0, 2.0, 5.0, 10.0])
    chain_lengths: list[int] = field(default_factory=lambda: [10, 25, 50, 100])
    group_sizes: list[int] = field(default_factory=lambda: [10, 25, 50, 100])

    sarcomere_conditions: list[str] = field(default_factory=lambda: ["euploid", "trisomic"])
    annotation_noise_nm: list[float] = field(default_factory=lambda: [0.0, 5.0, 10.0, 20.0])
    sarcomere_group_sizes: list[int] = field(default_factory=lambda: [10, 25, 50, 100])

    alpha: float = 0.05
    power: float = 0.80
    seed: int = 42

    output_dir: str = "outputs/mde_analysis"
    save_figures: bool = True
    figure_dpi: int = 300


def load_mde_config(path: Path) -> MDEConfig:
    """Load an :class:`MDEConfig` from a YAML file; unknown keys are ignored."""
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    valid = set(MDEConfig.__dataclass_fields__.keys())
    filtered = {k: v for k, v in raw.items() if k in valid}
    return MDEConfig(**filtered)


def mde_two_sample(sigma: float, n_per_group: int, alpha: float, power: float) -> float:
    """Return the two-sample minimum detectable effect.

    The formula assumes a two-sided test under the equal-variance
    Gaussian approximation and returns the absolute mean difference
    detectable at ``power`` with significance ``alpha``.
    """
    z_alpha = float(norm.ppf(1 - alpha / 2))
    z_beta = float(norm.ppf(power))
    return float((z_alpha + z_beta) * sigma * np.sqrt(2.0 / n_per_group))


def simulate_mvl_sigma(
    kappa: float,
    chain_length: int,
    n_replicates: int,
    rng: np.random.Generator,
) -> tuple[float, float]:
    """Return ``(mean, sd)`` of sample MVL for a myofibril of length ``M``.

    Step angles are drawn from von Mises(0, ``kappa``); the SD is the
    Monte Carlo standard deviation across ``n_replicates`` chains.
    """
    if kappa <= 0:
        sample_angles = rng.uniform(-np.pi, np.pi, (n_replicates, chain_length))
    else:
        sample_angles = vonmises.rvs(
            kappa,
            loc=0.0,
            size=(n_replicates, chain_length),
            random_state=rng,
        )
    sample_angles = (sample_angles + np.pi) % (2 * np.pi) - np.pi
    cos_a = np.cos(sample_angles)
    sin_a = np.sin(sample_angles)
    mvls = np.sqrt(cos_a.mean(axis=1) ** 2 + sin_a.mean(axis=1) ** 2)
    return float(mvls.mean()), float(mvls.std(ddof=1))


def _build_generator(condition: str, seed: int) -> SarcomereGenerator:
    """Build a :class:`SarcomereGenerator` for one MDE simulation."""
    cfg = SingleSarcomereConfig(
        n_simulations=1,
        seed=seed,
        target_condition=condition,
        save_figures=False,
        generate_boundary_cases=False,
    )
    return SarcomereGenerator(cfg)


def simulate_morphometry_sigma(
    condition: str,
    sigma_anno_nm: float,
    n_replicates: int,
    rng: np.random.Generator,
) -> dict[str, tuple[float, float]]:
    """Estimate mean and SD of AsR and Cur under isotropic annotation noise.

    Parameters
    ----------
    condition : str
        ``'euploid'`` or ``'trisomic'``.
    sigma_anno_nm : float
        Isotropic annotation noise standard deviation (nm). ``0`` means
        the ground truth is reused unchanged.
    n_replicates : int
        Number of replicates to draw.
    rng : numpy.random.Generator
        Master RNG used to seed the per-call generator and worker RNG.

    Returns
    -------
    dict
        ``{"AsR": (mean, std), "Cur": (mean, std)}`` over the replicates
        whose perturbed geometry is non-degenerate.
    """
    seed = int(rng.integers(0, 2**31 - 1))
    generator = _build_generator(condition, seed)
    work_rng = np.random.default_rng(seed)
    noise = GaussianNoise(sigma=sigma_anno_nm) if sigma_anno_nm > 0 else None

    asr_vals: list[float] = []
    cur_vals: list[float] = []
    for _ in range(n_replicates):
        truth_pts, _ = generator.cisterna(Condition(condition), work_rng)
        noisy_pts = noise.apply(truth_pts, work_rng) if noise is not None else truth_pts
        geom = compute_metrics_from_points(noisy_pts)
        if geom is None:
            continue
        asr_vals.append(float(geom.aspect_ratio))
        cur_vals.append(float(geom.curviness))

    asr_arr = np.asarray(asr_vals)
    cur_arr = np.asarray(cur_vals)
    return {
        "AsR": (float(asr_arr.mean()), float(asr_arr.std(ddof=1))),
        "Cur": (float(cur_arr.mean()), float(cur_arr.std(ddof=1))),
    }


def run_mvl_mde(cfg: MDEConfig, logger: logging.Logger) -> pd.DataFrame:
    """Sweep the (kappa, chain length, group size) grid for MVL MDE.

    Returns a tidy DataFrame with one row per ``(kappa, M, N_per_group)``
    combination, including the per-myofibril SD, sample mean MVL, and
    the corresponding minimum detectable effect.
    """
    rng = np.random.default_rng(cfg.seed)
    rows: list[dict] = []
    for kappa in cfg.kappa_values:
        for chain_length in cfg.chain_lengths:
            mu, sd = simulate_mvl_sigma(
                kappa=kappa,
                chain_length=chain_length,
                n_replicates=cfg.n_replicates_for_sd,
                rng=rng,
            )
            for n in cfg.group_sizes:
                mde = mde_two_sample(sd, n, cfg.alpha, cfg.power)
                rows.append(
                    {
                        "metric": "MVL",
                        "kappa": kappa,
                        "chain_length": chain_length,
                        "n_per_group": n,
                        "sigma": sd,
                        "sample_mean": mu,
                        "mde": mde,
                    }
                )
            logger.info(f"MVL  kappa={kappa:>4} M={chain_length:>3}  mu={mu:.4f} sigma={sd:.4f}")
    return pd.DataFrame(rows)


def run_morphometry_mde(cfg: MDEConfig, logger: logging.Logger) -> pd.DataFrame:
    """Sweep (condition, annotation noise, group size) for AsR/Cur MDE.

    Returns a tidy DataFrame with one row per
    ``(metric, condition, annotation_noise_nm, N_per_group)``, including
    the per-sarcomere SD, sample mean, and the corresponding minimum
    detectable effect.
    """
    rng = np.random.default_rng(cfg.seed + 1)
    rows: list[dict] = []
    for condition in cfg.sarcomere_conditions:
        for sigma_anno in cfg.annotation_noise_nm:
            stats = simulate_morphometry_sigma(
                condition=condition,
                sigma_anno_nm=sigma_anno,
                n_replicates=cfg.n_replicates_for_sd,
                rng=rng,
            )
            for metric_name, (mu, sd) in stats.items():
                for n in cfg.sarcomere_group_sizes:
                    mde = mde_two_sample(sd, n, cfg.alpha, cfg.power)
                    rows.append(
                        {
                            "metric": metric_name,
                            "condition": condition,
                            "annotation_noise_nm": sigma_anno,
                            "n_per_group": n,
                            "sigma": sd,
                            "sample_mean": mu,
                            "mde": mde,
                        }
                    )
            logger.info(
                f"{condition:<8} sigma_anno={sigma_anno:>5} nm  "
                f"AsR(mu={stats['AsR'][0]:.3f} sigma={stats['AsR'][1]:.3f})  "
                f"Cur(mu={stats['Cur'][0]:.3f} sigma={stats['Cur'][1]:.3f})"
            )
    return pd.DataFrame(rows)


def plot_mde_summary(
    mvl_df: pd.DataFrame,
    morpho_df: pd.DataFrame,
    cfg: MDEConfig,
    output_dir: Path,
) -> Path:
    """Render the three-panel MDE summary figure to PDF and PNG."""
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))

    palette_mvl = sns.color_palette("viridis", n_colors=len(cfg.chain_lengths))
    ax = axes[0]
    sub = mvl_df[mvl_df["kappa"] == max(cfg.kappa_values, default=10.0)]
    for color, M in zip(palette_mvl, sorted(sub["chain_length"].unique()), strict=False):
        s2 = sub[sub["chain_length"] == M].sort_values("n_per_group")
        ax.plot(
            s2["n_per_group"],
            s2["mde"],
            marker="o",
            color=color,
            label=f"M = {M}",
        )
    ax.set_xlabel("Myofibrils per group (N)")
    ax.set_ylabel("MDE for MVL difference")
    ax.set_title(f"MVL MDE - kappa = {max(cfg.kappa_values, default=10.0)}")
    ax.set_xscale("log")
    ax.legend(fontsize=8)

    palette_anno = sns.color_palette("magma", n_colors=len(cfg.annotation_noise_nm))
    for ax_idx, metric in enumerate(("AsR", "Cur"), start=1):
        ax = axes[ax_idx]
        sub = morpho_df[(morpho_df["metric"] == metric) & (morpho_df["condition"] == "euploid")]
        for color, sigma_anno in zip(
            palette_anno, sorted(sub["annotation_noise_nm"].unique()), strict=False
        ):
            s2 = sub[sub["annotation_noise_nm"] == sigma_anno].sort_values("n_per_group")
            ax.plot(
                s2["n_per_group"],
                s2["mde"],
                marker="o",
                color=color,
                label=f"sigma_anno = {sigma_anno:g} nm",
            )
        ax.set_xlabel("Sarcomeres per group (N)")
        ax.set_ylabel(f"MDE for {metric} difference")
        ax.set_title(f"{metric} MDE - euploid")
        ax.set_xscale("log")
        ax.legend(fontsize=8)

    fig.suptitle(f"Minimum detectable effect (alpha = {cfg.alpha}, power = {cfg.power})")
    fig.tight_layout()
    out = output_dir / "fig_mde.pdf"
    fig.savefig(out, dpi=cfg.figure_dpi, bbox_inches="tight")
    fig.savefig(out.with_suffix(".png"), dpi=cfg.figure_dpi, bbox_inches="tight")
    plt.close(fig)
    return out


def setup_logging(output_dir: Path) -> logging.Logger:
    """Configure a console + file logger writing under ``output_dir``."""
    output_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("mde_analysis")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
    fh = logging.FileHandler(output_dir / "mde_analysis.log", mode="w")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def run(cfg: MDEConfig) -> Path:
    """Execute the MDE analysis and return the run directory."""
    output_dir = Path(cfg.output_dir) / datetime.now().strftime("run_%Y%m%d_%H%M%S")
    logger = setup_logging(output_dir)
    logger.info("=" * 60)
    logger.info("MINIMUM DETECTABLE EFFECT ANALYSIS")
    logger.info("=" * 60)
    logger.info(f"alpha={cfg.alpha}, power={cfg.power}")
    logger.info(f"replicates for sigma estimation: {cfg.n_replicates_for_sd}")

    logger.info("\n--- MVL MDE ---")
    mvl_df = run_mvl_mde(cfg, logger)
    mvl_df.to_csv(output_dir / "mde_mvl.csv", index=False)

    logger.info("\n--- AsR / Cur MDE ---")
    morpho_df = run_morphometry_mde(cfg, logger)
    morpho_df.to_csv(output_dir / "mde_morphometry.csv", index=False)

    with open(output_dir / "config.json", "w") as f:
        json.dump(asdict(cfg), f, indent=2)

    if cfg.save_figures:
        fig_path = plot_mde_summary(mvl_df, morpho_df, cfg, output_dir)
        logger.info(f"Figure -> {fig_path}")

    logger.info("\nMVL summary (kappa x M, n_per_group=25):")
    sub = mvl_df[mvl_df["n_per_group"] == 25]
    if not sub.empty:
        pivot = sub.pivot(index="kappa", columns="chain_length", values="mde")
        logger.info("\n" + pivot.to_string(float_format="%.4f"))

    logger.info("\nMorphometry summary (euploid, n_per_group=25):")
    sub = morpho_df[(morpho_df["condition"] == "euploid") & (morpho_df["n_per_group"] == 25)]
    if not sub.empty:
        pivot = sub.pivot(index="annotation_noise_nm", columns="metric", values="mde")
        logger.info("\n" + pivot.to_string(float_format="%.4f"))

    return output_dir


def main() -> None:
    """CLI entry point for ``python -m sarcostat.simulation.rigor.mde``."""
    parser = argparse.ArgumentParser(description="MDE analysis for SarcoStat metrics")
    parser.add_argument("--config", "-c", type=Path, default=None)
    args = parser.parse_args()
    cfg = load_mde_config(args.config) if args.config else MDEConfig()
    out = run(cfg)
    print(f"\nMDE analysis complete. Output: {out}")


if __name__ == "__main__":
    main()
