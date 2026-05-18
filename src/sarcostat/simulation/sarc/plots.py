"""Plotters for the single-sarcomere validation pipelines.

Each plotter is a small dataclass that implements the
:class:`~sarcostat.simulation.chain.plots.Plotter`-style ``render``
method. The shapes differ from the chain plotters (results dataclasses
rather than nested dicts) so the Protocol is local to this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import matplotlib.pyplot as plt
import numpy as np

from sarcostat.core.sarcomere_morphometry import Point

from .config import SingleSarcomereConfig
from .generator import SarcomereGenerator
from .noise import GaussianNoise
from .results import BoundaryClassificationResults, NoiseSensitivityResults


@runtime_checkable
class SarcPlotter(Protocol):
    """Structural type for sarc plotters."""

    name: str


@dataclass(frozen=True)
class NoiseSensitivityPlot:
    """Four-panel figure: RMSE, bias, classification, precision."""

    name: str = "noise_sensitivity"

    def render(self, results: NoiseSensitivityResults) -> plt.Figure:
        """Build the four-panel sensitivity figure."""
        fig, axes = plt.subplots(2, 2, figsize=(12, 10))
        noise = results.noise_levels

        ax = axes[0, 0]
        ax.plot(noise, results.asr_rmse, "o-", label="AsR", linewidth=2, markersize=6)
        ax.plot(noise, results.cur_rmse, "s-", label="Cur", linewidth=2, markersize=6)
        ax.plot(
            noise, results.shape_index_rmse, "^-", label="Shape Index", linewidth=2, markersize=6
        )
        ax.axhline(y=0.1, color="red", linestyle="--", alpha=0.7, label="0.1 threshold")
        ax.set_xlabel("Annotation noise sigma (nm)")
        ax.set_ylabel("RMSE")
        ax.set_title("Metric RMSE vs annotation noise")
        ax.legend()
        ax.grid(True, alpha=0.3)

        ax = axes[0, 1]
        ax.plot(noise, results.asr_bias, "o-", label="AsR bias", linewidth=2, markersize=6)
        ax.plot(noise, results.cur_bias, "s-", label="Cur bias", linewidth=2, markersize=6)
        ax.axhline(y=0, color="black", linestyle="-", alpha=0.5)
        ax.set_xlabel("Annotation noise sigma (nm)")
        ax.set_ylabel("Mean bias")
        ax.set_title("Metric bias vs annotation noise")
        ax.legend()
        ax.grid(True, alpha=0.3)

        ax = axes[1, 0]
        ax.plot(
            noise,
            results.classification_agreement * 100,
            "o-",
            color="green",
            linewidth=2,
            markersize=6,
        )
        ax.axhline(y=95, color="red", linestyle="--", alpha=0.7, label="95% threshold")
        ax.set_xlabel("Annotation noise sigma (nm)")
        ax.set_ylabel("Classification accuracy (%)")
        ax.set_title("Shape classification accuracy vs noise")
        ax.set_ylim([0, 105])
        ax.legend()
        ax.grid(True, alpha=0.3)

        ax = axes[1, 1]
        thresholds = [0.05, 0.1, 0.15, 0.2]
        asr_prec = [results.minimum_precision_requirement(t, float("inf")) for t in thresholds]
        cur_prec = [results.minimum_precision_requirement(float("inf"), t) for t in thresholds]
        x = np.arange(len(thresholds))
        width = 0.35
        ax.bar(x - width / 2, asr_prec, width, label="AsR", alpha=0.8)
        ax.bar(x + width / 2, cur_prec, width, label="Cur", alpha=0.8)
        ax.set_xticks(x)
        ax.set_xticklabels([f"{t:.2f}" for t in thresholds])
        ax.set_xlabel("Max acceptable RMSE")
        ax.set_ylabel("Max noise sigma (nm)")
        ax.set_title("Annotation precision requirements")
        ax.legend()
        ax.grid(True, alpha=0.3, axis="y")

        plt.tight_layout()
        return fig


@dataclass(frozen=True)
class BoundaryAnalysisPlot:
    """Two-panel figure: misclassification heatmap + critical-width curve."""

    name: str = "boundary_analysis"

    def render(self, results: BoundaryClassificationResults) -> plt.Figure:
        """Build the two-panel boundary figure."""
        fig, axes = plt.subplots(1, 2, figsize=(14, 5))

        ax = axes[0]
        im = ax.pcolormesh(
            results.boundary_offsets,
            results.noise_levels,
            results.misclassification_rate,
            cmap="YlOrRd",
            vmin=0,
            vmax=1,
        )
        ax.set_xlabel("Boundary offset (Cur - AsR)")
        ax.set_ylabel("Annotation noise sigma (nm)")
        ax.set_title("Misclassification rate at shape boundaries")
        plt.colorbar(im, ax=ax, label="Misclassification rate")
        cs = ax.contour(
            results.boundary_offsets,
            results.noise_levels,
            results.misclassification_rate,
            levels=[0.1, 0.25, 0.5],
            colors="black",
            linewidths=1,
        )
        ax.clabel(cs, fmt="%.2f", fontsize=8)

        ax = axes[1]
        noise_levels = sorted(results.critical_boundary_width.keys())
        widths = [results.critical_boundary_width[n] for n in noise_levels]
        ax.plot(noise_levels, widths, "o-", linewidth=2, markersize=8, color="darkblue")
        ax.set_xlabel("Annotation noise sigma (nm)")
        ax.set_ylabel("Critical boundary width |Cur - AsR|")
        ax.set_title("Minimum separation for reliable classification (<10% error)")
        ax.grid(True, alpha=0.3)

        plt.tight_layout()
        return fig


def _draw_sarcomere(ax: plt.Axes, points: dict[str, Point], color: str) -> None:
    """Render the three lines (Z1, M, Z2) of one sarcomere on ``ax``."""
    z1 = (points["A"], points["B"])
    m = (points["C"], points["D"])
    z2 = (points["E"], points["F"])
    for (start, end), label in zip((z1, m, z2), ("Z1", "M", "Z2"), strict=False):
        ax.plot(
            [start[0], end[0]],
            [start[1], end[1]],
            "-o",
            color=color,
            label=label,
            markersize=4,
            linewidth=1.5,
        )


@dataclass(frozen=True)
class ExampleSarcomeresPlot:
    """3x3 grid of (BARREL, RECTANGULAR, HOURGLASS) at three noise levels."""

    name: str = "example_sarcomeres"
    noise_levels: tuple[float, ...] = (0.0, 20.0, 50.0)

    def render(
        self,
        cfg: SingleSarcomereConfig,
        generator: SarcomereGenerator,
    ) -> plt.Figure:
        """Build a 3x3 figure illustrating noise progression on three shapes."""
        fig, axes = plt.subplots(3, 3, figsize=(12, 12))
        rng = np.random.default_rng(cfg.seed)
        offsets = {-0.3: "BARREL", 0.0: "RECTANGULAR", 0.3: "HOURGLASS"}
        for col, (offset, label) in enumerate(offsets.items()):
            points, _ = generator.at_boundary(boundary_offset=offset, rng=rng)
            for row, sigma in enumerate(self.noise_levels):
                ax = axes[row, col]
                noisy = GaussianNoise(sigma=sigma).apply(points, rng)
                _draw_sarcomere(ax, noisy, color="C0")
                ax.set_aspect("equal")
                ax.set_title(f"{label}, sigma = {sigma:.0f} nm", fontsize=10)
                ax.grid(True, alpha=0.3)
        fig.suptitle("Example sarcomeres at three noise levels", fontsize=14)
        plt.tight_layout()
        return fig


def default_sarc_plotters() -> tuple[
    NoiseSensitivityPlot, BoundaryAnalysisPlot, ExampleSarcomeresPlot
]:
    """Return the default plotter trio."""
    return (NoiseSensitivityPlot(), BoundaryAnalysisPlot(), ExampleSarcomeresPlot())


__all__ = [
    "SarcPlotter",
    "NoiseSensitivityPlot",
    "BoundaryAnalysisPlot",
    "ExampleSarcomeresPlot",
    "default_sarc_plotters",
]
