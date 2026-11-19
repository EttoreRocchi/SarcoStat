"""Minimum detectable effect (MDE) analysis for SarcoStat metrics.

For each metric we estimate the population standard deviation under the
specified condition by Monte Carlo simulation, then compute the smallest
mean difference detectable at alpha = 0.05 and power = 0.80 by a two-sample
two-sided test (equal-variance normal (z) approximation), using

    MDE = (z_{alpha/2} + z_beta) x sigma x sqrt(2 / N_per_group).

Three families of comparisons are supported:

  1. MVL between two myofibril groups, parameterised by population
     concentration kappa and per-myofibril chain length M_chain.
  2. AsR between two sarcomere groups, parameterised by annotation
     noise sigma_anno (nm) and target reference condition (euploid /
     trisomic).
  3. Cur between two sarcomere groups, parameterised as in (2).

The output is a tidy table that can be reported directly:
"to detect a difference of Delta in <metric> with 80% power and alpha = 0.05
requires N >= <...> sarcomeres per group."

Every grid cell draws from its own seed stream, so results do not depend
on the order or content of the rest of the grid. For AsR/Cur the same
ground-truth population is reused at every annotation-noise level of a
condition (common random numbers), so differences across noise levels
reflect the noise rather than a fresh population draw.
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
from scipy.stats import norm, vonmises

from sarcostat.simulation.sarc import (
    Condition,
    GaussianNoise,
    SarcomereGenerator,
    SingleSarcomereConfig,
    compute_metrics_from_points,
)

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

# Stable integer code per condition, used to derive per-condition seed streams.
_CONDITION_CODES = {c.value: i for i, c in enumerate(Condition)}


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

    def __post_init__(self) -> None:
        """Reject out-of-range parameters."""
        if self.n_replicates_for_sd < 2:
            raise ValueError(
                f"n_replicates_for_sd must be at least 2, got {self.n_replicates_for_sd}"
            )
        if not (0.0 < self.alpha < 1.0):
            raise ValueError(f"alpha must be in (0, 1), got {self.alpha}")
        if not (0.0 < self.power < 1.0):
            raise ValueError(f"power must be in (0, 1), got {self.power}")
        if any(k < 0 for k in self.kappa_values):
            raise ValueError(f"kappa_values must be non-negative, got {self.kappa_values}")
        if any(s < 0 for s in self.annotation_noise_nm):
            raise ValueError(
                f"annotation_noise_nm must be non-negative, got {self.annotation_noise_nm}"
            )
        for name in ("chain_lengths", "group_sizes", "sarcomere_group_sizes"):
            values = getattr(self, name)
            if any(v < 1 for v in values):
                raise ValueError(f"{name} must be positive, got {values}")
        unknown = [c for c in self.sarcomere_conditions if c not in _CONDITION_CODES]
        if unknown:
            raise ValueError(
                f"sarcomere_conditions must be among {sorted(_CONDITION_CODES)}, got {unknown}"
            )
        for name in (
            "kappa_values",
            "chain_lengths",
            "group_sizes",
            "sarcomere_conditions",
            "annotation_noise_nm",
            "sarcomere_group_sizes",
        ):
            require_unique(list(getattr(self, name)), name)


def _numbers(value, name):
    return as_list(value, name, as_number)


def _ints(value, name):
    return as_list(value, name, as_int)


_MDE_CONVERTERS = {
    "n_replicates_for_sd": as_int,
    "kappa_values": _numbers,
    "chain_lengths": _ints,
    "group_sizes": _ints,
    "sarcomere_conditions": lambda v, name: as_list(v, name, as_str),
    "annotation_noise_nm": _numbers,
    "sarcomere_group_sizes": _ints,
    "alpha": as_float,
    "power": as_float,
    "seed": as_int,
    "output_dir": as_str,
    "save_figures": as_bool,
    "figure_dpi": as_int,
}


def load_mde_config(path: str | Path) -> MDEConfig:
    """Load an :class:`MDEConfig` from a YAML file; unknown keys raise ``ValueError``."""
    return load_flat_dataclass(path, MDEConfig, _MDE_CONVERTERS)


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


def _build_generator(condition: str) -> SarcomereGenerator:
    """Build a :class:`SarcomereGenerator` for one MDE simulation."""
    cfg = SingleSarcomereConfig(
        n_simulations=1,
        target_condition=condition,
        save_figures=False,
        generate_boundary_cases=False,
    )
    return SarcomereGenerator(cfg)


def simulate_morphometry_sigma(
    condition: str,
    sigma_anno_nm: float,
    n_replicates: int,
    truth_rng: np.random.Generator,
    noise_rng: np.random.Generator,
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
    truth_rng : numpy.random.Generator
        RNG for the ground-truth sarcomeres. Passing generators with the
        same seed at every noise level reuses one population.
    noise_rng : numpy.random.Generator
        RNG for the annotation noise.

    Returns
    -------
    dict
        ``{"AsR": (mean, std), "Cur": (mean, std)}`` over the replicates
        whose perturbed geometry is non-degenerate.
    """
    generator = _build_generator(condition)
    noise = GaussianNoise(sigma=sigma_anno_nm) if sigma_anno_nm > 0 else None

    asr_vals: list[float] = []
    cur_vals: list[float] = []
    for _ in range(n_replicates):
        truth_pts, _ = generator.cisterna(Condition(condition), truth_rng)
        noisy_pts = noise.apply(truth_pts, noise_rng) if noise is not None else truth_pts
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
    rows: list[dict] = []
    for kappa in cfg.kappa_values:
        for chain_length in cfg.chain_lengths:
            mu, sd = simulate_mvl_sigma(
                kappa=kappa,
                chain_length=chain_length,
                n_replicates=cfg.n_replicates_for_sd,
                rng=np.random.default_rng(per_cell_seed(cfg.seed, kappa, chain_length)),
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
    rows: list[dict] = []
    for condition in cfg.sarcomere_conditions:
        code = _CONDITION_CODES[condition]
        for sigma_anno in cfg.annotation_noise_nm:
            stats = simulate_morphometry_sigma(
                condition=condition,
                sigma_anno_nm=sigma_anno,
                n_replicates=cfg.n_replicates_for_sd,
                truth_rng=np.random.default_rng(per_cell_seed(cfg.seed + 1, 0.0, code)),
                noise_rng=np.random.default_rng(per_cell_seed(cfg.seed + 2, sigma_anno, code)),
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
) -> plt.Figure:
    """Build the three-panel MDE summary figure (call inside :func:`simulation_style`)."""
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
    return fig


def run(
    cfg: MDEConfig,
    *,
    config_path: str | Path | None = None,
    console_handler: logging.Handler | None = None,
) -> Path:
    """Execute the MDE analysis and return the run directory.

    Output follows the :class:`~sarcostat.simulation._artifacts.RunArtifacts`
    layout: ``data/mde_mvl.csv``, ``data/mde_morphometry.csv`` and
    ``data/simulation_parameters.json``, ``plots/fig_mde.{pdf,png}``, and
    ``logs/``.
    """
    artifacts = RunArtifacts(
        cfg.output_dir,
        timestamped=True,
        logger_name="sarcostat.simulation.mde",
        console_handler=console_handler,
    )
    logger = artifacts.logger
    artifacts.save_parameters({**asdict(cfg), **run_metadata(config_path)})
    logger.info("MDE analysis: alpha=%s, power=%s", cfg.alpha, cfg.power)
    logger.info("Replicates for sigma estimation: %d", cfg.n_replicates_for_sd)

    with warnings.catch_warnings():
        # scipy/numpy numerical warnings from extreme cells are expected noise here.
        warnings.simplefilter("ignore", RuntimeWarning)
        mvl_df = run_mvl_mde(cfg, logger)
        morpho_df = run_morphometry_mde(cfg, logger)
    artifacts.save_csv("mde_mvl.csv", mvl_df)
    artifacts.save_csv("mde_morphometry.csv", morpho_df)

    if cfg.save_figures:
        with simulation_style():
            fig = plot_mde_summary(mvl_df, morpho_df, cfg)
            artifacts.save_figure(fig, "fig_mde", dpi=cfg.figure_dpi)
            plt.close(fig)

    sub = mvl_df[mvl_df["n_per_group"] == 25]
    if not sub.empty:
        pivot = sub.pivot(index="kappa", columns="chain_length", values="mde")
        logger.info(
            "MVL summary (kappa x M, n_per_group=25):\n%s", pivot.to_string(float_format="%.4f")
        )
    sub = morpho_df[(morpho_df["condition"] == "euploid") & (morpho_df["n_per_group"] == 25)]
    if not sub.empty:
        pivot = sub.pivot(index="annotation_noise_nm", columns="metric", values="mde")
        logger.info(
            "Morphometry summary (euploid, n_per_group=25):\n%s",
            pivot.to_string(float_format="%.4f"),
        )
    return artifacts.output_dir


__all__ = [
    "MDEConfig",
    "load_mde_config",
    "mde_two_sample",
    "simulate_mvl_sigma",
    "simulate_morphometry_sigma",
    "run_mvl_mde",
    "run_morphometry_mde",
    "plot_mde_summary",
    "run",
]
