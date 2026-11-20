"""Plotters for the chain Monte Carlo results.

Three plotters are provided: a 4xK boxplot grid
(:class:`BoxplotPlotter`), a 1xK polar circular distribution
(:class:`PolarPlotter`), and a 1x2 mean/std heatmap
(:class:`HeatmapPlotter`). Each implements the simple :class:`Plotter`
Protocol so that callers can compose any subset (and users can register
their own without touching this module).

The 3D matplotlib landscape and Plotly HTML plotters that lived in the
old ``Visualizer`` have been removed; they were specific to single figures
and required a heavy ``plotly`` dependency.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Circle

from sarcostat.core import _circular
from sarcostat.core.circular_stats import to_minuspi_pi

_BOX_COLORS: dict[str, str] = {
    "linearity_ratio": "#3498db",
    "mean_vector_length": "#2ecc71",
    "rayleigh_z": "#e74c3c",
    "v_statistic": "#9b59b6",
}
_BOX_TITLES: dict[str, str] = {
    "linearity_ratio": "Linearity Ratio",
    "mean_vector_length": "Mean Vector Length (R)",
    "rayleigh_z": "Rayleigh Z-statistic",
    "v_statistic": "V-test Statistic",
}
_BOX_Y_LABELS: dict[str, str] = {
    "linearity_ratio": "Linearity Ratio",
    "mean_vector_length": "Mean Vector Length",
    "rayleigh_z": "Z-statistic",
    "v_statistic": "V-statistic",
}


@runtime_checkable
class Plotter(Protocol):
    """Structural type for chain-result plotters."""

    @property
    def name(self) -> str:
        """Stem used for the saved figure files."""
        ...

    def render(
        self,
        results: dict,
        kappa_values: list[float],
        n_sarcomeres: list[int],
        *,
        dpi: int = 300,
    ) -> plt.Figure:
        """Build and return a matplotlib :class:`Figure`."""
        ...


def _style_metric_axes(
    ax: plt.Axes,
    metric: str,
    n_sarcomeres: list[int],
    is_top_row: bool,
    is_bottom_row: bool,
    is_left_col: bool,
    kappa_value: float,
) -> None:
    """Apply the boxplot per-cell axis styling."""
    if is_top_row:
        ax.set_title(f"kappa = {kappa_value}", fontsize=14, fontweight="bold", pad=10)
    if is_left_col:
        ax.set_ylabel(_BOX_Y_LABELS[metric], fontsize=11, fontweight="bold")
    if is_bottom_row:
        ax.set_xlabel("N (sarcomeres)", fontsize=11)
        ax.set_xticks(range(1, len(n_sarcomeres) + 1))
        ax.set_xticklabels([str(n) for n in n_sarcomeres], rotation=45)
    else:
        ax.set_xticks(range(1, len(n_sarcomeres) + 1))
        ax.set_xticklabels([])
    if metric in {"linearity_ratio", "mean_vector_length"}:
        ax.set_ylim(0, 1.05)
    elif metric == "rayleigh_z":
        ax.set_ylim(bottom=0)
    ax.grid(True, alpha=0.2, linestyle="--")
    ax.set_axisbelow(True)
    ax.set_facecolor("#f8f9fa")


@dataclass(frozen=True)
class BoxplotPlotter:
    """Boxplot grid: rows=metrics, cols=kappa values."""

    name: str = "comprehensive"

    def render(
        self,
        results: dict,
        kappa_values: list[float],
        n_sarcomeres: list[int],
        *,
        dpi: int = 300,
    ) -> plt.Figure:
        """Build the 4xK boxplot figure."""
        metrics = ["linearity_ratio", "mean_vector_length", "rayleigh_z", "v_statistic"]
        n_metrics = len(metrics)
        n_kappa = len(kappa_values)
        fig, axes = plt.subplots(
            n_metrics,
            n_kappa,
            figsize=(4 * n_kappa, 4 * n_metrics),
            dpi=dpi,
            squeeze=False,
        )
        for r, metric in enumerate(metrics):
            for c, k in enumerate(kappa_values):
                ax = axes[r, c]
                data_to_plot = [results[metric][k][n] for n in n_sarcomeres]
                ax.boxplot(
                    data_to_plot,
                    patch_artist=True,
                    widths=0.6,
                    boxprops={"facecolor": _BOX_COLORS[metric], "alpha": 0.6},
                    medianprops={"color": "darkred", "linewidth": 2},
                    flierprops={
                        "marker": "o",
                        "markerfacecolor": "gray",
                        "markersize": 3,
                        "alpha": 0.5,
                    },
                    whiskerprops={"linewidth": 1.5},
                    capprops={"linewidth": 1.5},
                )
                _style_metric_axes(
                    ax,
                    metric,
                    n_sarcomeres,
                    is_top_row=r == 0,
                    is_bottom_row=r == n_metrics - 1,
                    is_left_col=c == 0,
                    kappa_value=k,
                )
        plt.tight_layout()
        return fig


def _draw_circular_mean(
    ax: plt.Axes, angles: np.ndarray, mean_angle: float, r_length: float
) -> None:
    """Draw the unit circle, each angle as a marker on it and the mean resultant vector."""
    ax.add_patch(Circle((0, 0), 1, edgecolor="k", facecolor="none", linewidth=2))
    ax.axvline(0, lw=1, ls=":", color="slategrey")
    ax.axhline(0, lw=1, ls=":", color="slategrey")
    ax.plot(
        np.cos(angles),
        np.sin(angles),
        ls="None",
        color="tab:blue",
        marker="o",
        mfc="none",
        ms=5,
        alpha=0.7,
    )
    ax.arrow(
        0,
        0,
        r_length * np.cos(mean_angle),
        r_length * np.sin(mean_angle),
        width=0.01,
        head_width=0.1,
        head_length=0.1,
        fc="tab:red",
        ec="tab:red",
    )
    ax.set_xticks([])
    ax.set_yticks([])
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)
    ax.text(1.2, 0, "0", verticalalignment="center")
    ax.text(-1.3, 0, r"$\pi$", verticalalignment="center")
    ax.text(0, 1.2, r"$+\pi/2$", horizontalalignment="center")
    ax.text(0, -1.3, r"$-\pi/2$", horizontalalignment="center")
    ax.set_aspect("equal")


@dataclass(frozen=True)
class PolarPlotter:
    """Single-row polar plot of step-angle distributions per kappa."""

    name: str = "circular"

    def render(
        self,
        results: dict,
        kappa_values: list[float],
        n_sarcomeres: list[int],
        *,
        dpi: int = 300,
    ) -> plt.Figure:
        """Build the 1xK polar figure for a representative chain length."""
        n_repr = 50 if 50 in n_sarcomeres else n_sarcomeres[len(n_sarcomeres) // 2]
        n_kappa = len(kappa_values)
        fig = plt.figure(figsize=(3.6 * n_kappa, 6), dpi=dpi)
        fig.suptitle(
            f"Circular distribution of step angles (N={n_repr} sarcomeres)",
            fontsize=16,
            fontweight="bold",
        )
        for idx, k in enumerate(kappa_values):
            ax = plt.subplot(1, n_kappa, idx + 1)
            angles_array = np.asarray(results["angles"][k][n_repr])
            if angles_array.size > 0:
                ang = to_minuspi_pi(angles_array)
                mean_angle = float(_circular.circ_mean(ang))
                r_length = float(_circular.circ_r(ang))
                _draw_circular_mean(ax, ang, mean_angle, r_length)
                ax.set_title(
                    f"kappa = {k}\nMean: {np.degrees(mean_angle):.1f} deg, R = {r_length:.3f}\n",
                    fontsize=10,
                    pad=10,
                )
        plt.tight_layout()
        return fig


def _build_mean_std_matrices(
    results: dict, metric: str, kappa_values: list[float], n_sarcomeres: list[int]
) -> tuple[np.ndarray, np.ndarray]:
    """Return mean and std matrices indexed by (n_sarcomeres, kappa_values)."""
    mean_matrix = np.zeros((len(n_sarcomeres), len(kappa_values)))
    std_matrix = np.zeros_like(mean_matrix)
    for i, n in enumerate(n_sarcomeres):
        for j, k in enumerate(kappa_values):
            data = np.asarray(results[metric][k][n])
            mean_matrix[i, j] = float(np.mean(data))
            std_matrix[i, j] = float(np.std(data))
    return mean_matrix, std_matrix


def _annotate_heatmap(
    ax: plt.Axes,
    mean_matrix: np.ndarray,
    std_matrix: np.ndarray,
    n_sarcomeres: list[int],
    kappa_values: list[float],
) -> None:
    """Write mean +/- std into each heatmap cell."""
    for i in range(len(n_sarcomeres)):
        for j in range(len(kappa_values)):
            mean_val = mean_matrix[i, j]
            std_val = std_matrix[i, j]
            text_color = "white" if mean_val < 0.5 else "black"
            ax.text(
                j,
                i,
                f"{mean_val:.3f}\n({std_val:.3f})",
                ha="center",
                va="center",
                color=text_color,
                fontsize=6,
                linespacing=0.9,
            )


@dataclass(frozen=True)
class HeatmapPlotter:
    """1x2 heatmap of mean R and mean linearity ratio across kappa x n."""

    name: str = "heatmap"

    def render(
        self,
        results: dict,
        kappa_values: list[float],
        n_sarcomeres: list[int],
        *,
        dpi: int = 300,
    ) -> plt.Figure:
        """Build the 1x2 mean/std heatmap figure."""
        fig, axes = plt.subplots(1, 2, figsize=(14, 5), dpi=dpi)
        fig.suptitle(
            "Parameter space heatmaps: mean values across kappa x N",
            fontsize=14,
            fontweight="bold",
            y=1.02,
        )
        metrics = [
            ("mean_vector_length", "Mean Vector Length (R)", "viridis"),
            ("linearity_ratio", "Linearity Ratio", "plasma"),
        ]
        for idx, (metric, title, cmap) in enumerate(metrics):
            ax = axes[idx]
            mean_matrix, std_matrix = _build_mean_std_matrices(
                results, metric, kappa_values, n_sarcomeres
            )
            x_edges = np.arange(-0.5, len(kappa_values) + 0.5, 1)
            y_edges = np.arange(-0.5, len(n_sarcomeres) + 0.5, 1)
            im = ax.pcolormesh(
                x_edges,
                y_edges,
                mean_matrix,
                cmap=cmap,
                vmin=0,
                vmax=1,
                shading="flat",
                edgecolors="none",
                linewidth=0,
            )
            ax.set_xticks(np.arange(len(kappa_values)))
            ax.set_yticks(np.arange(len(n_sarcomeres)))
            ax.set_xticklabels([f"{k}" for k in kappa_values])
            ax.set_yticklabels([f"{n}" for n in n_sarcomeres])
            ax.set_xlabel("kappa (concentration)", fontsize=11)
            ax.set_ylabel("N (sarcomeres)", fontsize=11)
            ax.set_title(title, fontsize=12, fontweight="bold")
            ax.grid(False)
            ax.set_axisbelow(False)
            _annotate_heatmap(ax, mean_matrix, std_matrix, n_sarcomeres, kappa_values)
            cbar = plt.colorbar(im, ax=ax)
            cbar.set_label(title, fontsize=10)
        plt.tight_layout()
        return fig


def default_plotters() -> tuple[Plotter, ...]:
    """Return the default plotter suite (boxplot, polar, heatmap)."""
    return (BoxplotPlotter(), PolarPlotter(), HeatmapPlotter())


def render_all(
    plotters: Iterable[Plotter],
    results: dict,
    kappa_values: list[float],
    n_sarcomeres: list[int],
    *,
    dpi: int = 300,
) -> dict[str, plt.Figure]:
    """Render every plotter into a ``{name: Figure}`` mapping."""
    return {
        plotter.name: plotter.render(results, kappa_values, n_sarcomeres, dpi=dpi)
        for plotter in plotters
    }


__all__ = [
    "Plotter",
    "BoxplotPlotter",
    "PolarPlotter",
    "HeatmapPlotter",
    "default_plotters",
    "render_all",
]
