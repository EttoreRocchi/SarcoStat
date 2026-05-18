"""
Visualization manager for SarcoStat GUI.

Decouples matplotlib rendering from business logic for testability
and cleaner separation of concerns.
"""

from typing import Protocol

import numpy as np
from matplotlib.artist import Artist
from matplotlib.axes import Axes
from matplotlib.figure import Figure

from sarcostat.core.sarcomere_morphometry import SarcomereGeometry

from .constants import VIZ, get_sarcomere_point_color, get_sarcomere_point_label
from .session import Point, UserSegment


class CanvasProtocol(Protocol):
    """Protocol for canvas abstraction (enables testing without Tk)."""

    def draw_idle(self) -> None:
        """Schedule a redraw of the underlying canvas at the next idle tick."""
        ...


class VisualizationManager:
    """Manages all matplotlib visualization for the analysis tool."""

    def __init__(self, fig: Figure, ax: Axes, canvas: CanvasProtocol) -> None:
        self.fig = fig
        self.ax = ax
        self.canvas = canvas

        self.temp_artists: list[Artist] = []
        self.segment_line_artists: list[Artist] = []
        self.midpoint_artists: list[Artist] = []
        self.midpoint_link_artists: list[Artist] = []
        self.text_artists: list[Artist] = []
        self.sarcomere_artists: list[Artist] = []

    def display_image(self, image: np.ndarray) -> None:
        """Display image with auto-contrast."""
        vmin, vmax = np.percentile(image, [1, 99])
        self.ax.clear()
        self.ax.imshow(image, cmap="gray", vmin=vmin, vmax=vmax)
        self.ax.set_axis_off()
        self.canvas.draw_idle()

    def draw_temp_point(self, x: float, y: float, color: str) -> Artist:
        """Draw temporary point marker."""
        (pt,) = self.ax.plot(
            x,
            y,
            marker="o",
            markersize=VIZ.PENDING_MARKER_SIZE,
            markerfacecolor=color,
            markeredgecolor="white",
        )
        self.temp_artists.append(pt)
        self.canvas.draw_idle()
        return pt

    def draw_temp_line(self, p1: Point, p2: Point) -> Artist:
        """Draw temporary dashed line between points."""
        (ln,) = self.ax.plot(
            [p1[0], p2[0]],
            [p1[1], p2[1]],
            linewidth=VIZ.TEMP_LINE_WIDTH,
            color=VIZ.TEMP_LINE_COLOR,
            alpha=VIZ.TEMP_LINE_ALPHA,
            linestyle="--",
        )
        self.temp_artists.append(ln)
        self.canvas.draw_idle()
        return ln

    def clear_temp_artists(self) -> None:
        """Remove all temporary artists."""
        for art in self.temp_artists:
            try:
                art.remove()
            except (ValueError, AttributeError):
                pass
        self.temp_artists.clear()
        self.canvas.draw_idle()

    def draw_confirmed_segment(self, segment: UserSegment, index: int) -> None:
        """Draw confirmed segment with midpoint and label."""
        p1, p2 = segment.p1, segment.p2
        mx, my = segment.midpoint

        (line,) = self.ax.plot(
            [p1[0], p2[0]],
            [p1[1], p2[1]],
            linewidth=VIZ.SEGMENT_LINE_WIDTH,
            color=VIZ.SEGMENT_COLOR,
            alpha=VIZ.SEGMENT_ALPHA,
        )
        self.segment_line_artists.append(line)

        (mid,) = self.ax.plot(
            mx,
            my,
            marker="o",
            markersize=VIZ.MIDPOINT_MARKER_SIZE,
            markerfacecolor=VIZ.MIDPOINT_COLOR,
            markeredgecolor=VIZ.MIDPOINT_EDGE_COLOR,
        )
        self.midpoint_artists.append(mid)

        txt = self.ax.text(
            mx,
            my + VIZ.SEGMENT_LABEL_OFFSET_Y,
            f"S{index}",
            color="white",
            fontsize=VIZ.SEGMENT_LABEL_FONTSIZE,
            ha="center",
            va="top",
            bbox={
                "boxstyle": "round,pad=0.3",
                "facecolor": VIZ.MIDPOINT_EDGE_COLOR,
                "alpha": VIZ.LABEL_BOX_ALPHA,
            },
        )
        self.text_artists.append(txt)
        self.canvas.draw_idle()

    def draw_midpoint_links(self, midpoints: list[Point]) -> None:
        """Connect midpoints with lines."""
        self.clear_midpoint_links()
        for i in range(len(midpoints) - 1):
            (x0, y0), (x1, y1) = midpoints[i], midpoints[i + 1]
            (ln,) = self.ax.plot(
                [x0, x1],
                [y0, y1],
                linewidth=VIZ.MIDPOINT_LINK_WIDTH,
                color=VIZ.MIDPOINT_LINK_COLOR,
                alpha=VIZ.MIDPOINT_LINK_ALPHA,
            )
            self.midpoint_link_artists.append(ln)
        self.canvas.draw_idle()

    def clear_midpoint_links(self) -> None:
        """Remove midpoint connection lines."""
        for art in self.midpoint_link_artists:
            try:
                art.remove()
            except (ValueError, AttributeError):
                pass
        self.midpoint_link_artists.clear()

    def draw_sarcomere_point(self, x: float, y: float, point_index: int) -> tuple[Artist, Artist]:
        """Draw sarcomere annotation point with label."""
        color = get_sarcomere_point_color(point_index)
        label = get_sarcomere_point_label(point_index)

        (pt,) = self.ax.plot(
            x,
            y,
            marker="o",
            markersize=VIZ.POINT_MARKER_SIZE,
            markerfacecolor=color,
            markeredgecolor="white",
            linewidth=2,
        )
        self.temp_artists.append(pt)

        txt = self.ax.text(
            x + VIZ.POINT_LABEL_OFFSET_X,
            y + VIZ.POINT_LABEL_OFFSET_Y,
            label,
            color=color,
            fontsize=VIZ.POINT_LABEL_FONTSIZE,
            fontweight="bold",
            bbox={
                "boxstyle": "round,pad=0.2",
                "facecolor": "white",
                "alpha": VIZ.LABEL_BOX_ALPHA,
            },
        )
        self.temp_artists.append(txt)
        self.canvas.draw_idle()
        return pt, txt

    def draw_sarcomere_zline(self, p1: Point, p2: Point, color: str) -> Artist:
        """Draw a Z-line or M-line for sarcomere annotation."""
        (ln,) = self.ax.plot(
            [p1[0], p2[0]],
            [p1[1], p2[1]],
            linewidth=VIZ.ZLINE_WIDTH,
            color=color,
            alpha=0.8,
        )
        self.temp_artists.append(ln)
        self.canvas.draw_idle()
        return ln

    def draw_sarcomere_overlay(self, geom: SarcomereGeometry) -> None:
        """Draw permanent overlay for confirmed sarcomere."""
        mx1, my1 = geom.midpoint_z1
        mx2, my2 = geom.midpoint_z2

        (axis_line,) = self.ax.plot(
            [mx1, mx2],
            [my1, my2],
            linewidth=VIZ.SARCOMERE_AXIS_WIDTH,
            color=VIZ.SARCOMERE_AXIS_COLOR,
            alpha=0.8,
            linestyle="-.",
            marker="s",
            markersize=VIZ.SARCOMERE_MARKER_SIZE,
        )
        self.sarcomere_artists.append(axis_line)

        cx, cy = (mx1 + mx2) / 2, (my1 + my2) / 2
        label = (
            f"S{geom.sarcomere_id}\n"
            f"AsR={geom.aspect_ratio:.2f}\n"
            f"Cur={geom.curviness:.2f}\n"
            f"{geom.shape_classification.value}"
        )
        txt = self.ax.text(
            cx,
            cy,
            label,
            color="white",
            fontsize=VIZ.SARCOMERE_LABEL_FONTSIZE,
            ha="center",
            va="center",
            bbox={
                "boxstyle": "round,pad=0.4",
                "facecolor": VIZ.SARCOMERE_LABEL_BG,
                "alpha": VIZ.LABEL_BOX_ALPHA,
            },
        )
        self.sarcomere_artists.append(txt)
        self.canvas.draw_idle()

    def clear_sarcomere_artists(self) -> None:
        """Remove all sarcomere overlay artists."""
        for art in self.sarcomere_artists:
            try:
                art.remove()
            except (ValueError, AttributeError):
                pass
        self.sarcomere_artists.clear()
        self.canvas.draw_idle()

    def clear_all_artists(self) -> None:
        """Remove all drawn artists."""
        for group in (
            self.temp_artists,
            self.segment_line_artists,
            self.midpoint_artists,
            self.midpoint_link_artists,
            self.text_artists,
            self.sarcomere_artists,
        ):
            for art in group:
                try:
                    art.remove()
                except (ValueError, AttributeError):
                    pass
            group.clear()
        self.canvas.draw_idle()

    def clear_linearity_artists(self) -> None:
        """Clear artists specific to linearity mode."""
        for group in (
            self.temp_artists,
            self.segment_line_artists,
            self.midpoint_artists,
            self.midpoint_link_artists,
            self.text_artists,
        ):
            for art in group:
                try:
                    art.remove()
                except (ValueError, AttributeError):
                    pass
            group.clear()
        self.canvas.draw_idle()

    def redraw_all_segments(self, segments: list[UserSegment], midpoints: list[Point]) -> None:
        """Redraw all linearity annotations from scratch."""
        self.clear_linearity_artists()
        for i, seg in enumerate(segments, 1):
            self.draw_confirmed_segment(seg, i)
        if len(midpoints) >= 2:
            self.draw_midpoint_links(midpoints)

    def redraw_all_sarcomeres(self, geometries: list[SarcomereGeometry]) -> None:
        """Redraw all sarcomere overlays from scratch."""
        self.clear_sarcomere_artists()
        for geom in geometries:
            self.draw_sarcomere_overlay(geom)
