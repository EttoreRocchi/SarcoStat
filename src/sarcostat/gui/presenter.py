"""
Analysis presenter (controller) for SarcoStat GUI.

Coordinates between data model (session) and view (visualization).
This implements the business logic for the annotation workflow.
"""

from collections.abc import Callable

import numpy as np

from sarcostat.core.circular_stats import SequenceMetrics, analyze_segment_sequence
from sarcostat.core.sarcomere_morphometry import SarcomereGeometry, calculate_sarcomere_geometry

from .constants import VIZ
from .session import AnalysisMode, AnalysisSession, UserSegment
from .visualization import VisualizationManager


class AnalysisPresenter:
    """Controller coordinating analysis workflow between model and view."""

    def __init__(
        self,
        session: AnalysisSession,
        viz: VisualizationManager,
        on_status_change: Callable[[str], None],
        on_analysis_complete: Callable[[SequenceMetrics], None],
        on_sarcomere_complete: Callable[[SarcomereGeometry], None],
    ) -> None:
        self.session = session
        self.viz = viz
        self._on_status = on_status_change
        self._on_analysis = on_analysis_complete
        self._on_sarcomere = on_sarcomere_complete

    def add_linearity_point(self, x: float, y: float) -> bool:
        """
        Add point for linearity annotation.

        Returns True if segment is ready to confirm (2 points collected).
        """
        if len(self.session.pending_points) >= 2:
            return True

        self.session.pending_points.append((x, y))
        point_num = len(self.session.pending_points)

        # First point is red, second is blue
        color = VIZ.PENDING_POINT1_COLOR if point_num == 1 else VIZ.PENDING_POINT2_COLOR
        self.viz.draw_temp_point(x, y, color)

        if point_num == 2:
            p1, p2 = self.session.pending_points
            self.viz.draw_temp_line(p1, p2)
            self._on_status("Segment ready. Press 'c' or click 'Confirm Segment'.")
            return True

        self._on_status(f"Point 1 recorded at ({x:.1f}, {y:.1f}). Click second point.")
        return False

    def confirm_linearity_segment(self) -> str | None:
        """
        Confirm pending segment.

        Returns error message or None on success.
        """
        if len(self.session.pending_points) != 2:
            return "Need exactly 2 points to confirm segment"

        p1, p2 = self.session.pending_points

        if p1 == p2:
            return "Start and end points are identical"

        dist = np.hypot(p2[0] - p1[0], p2[1] - p1[1])
        if dist < 1.0:
            return "Points too close together (< 1 pixel)"

        seg = UserSegment(p1=p1, p2=p2)
        self.session.segments.append(seg)

        self.viz.clear_temp_artists()
        self.viz.draw_confirmed_segment(seg, len(self.session.segments))

        self.session.pending_points.clear()

        self._on_status(
            f"Segment {len(self.session.segments)} confirmed. "
            "Click to add more or 'Connect Midpoints'."
        )
        return None

    def connect_midpoints(self) -> str | None:
        """
        Connect midpoints with lines.

        Returns error message or None on success.
        """
        midpoints = self.session.midpoints
        if len(midpoints) < 2:
            return "Need at least 2 segments to connect midpoints"

        self.viz.draw_midpoint_links(midpoints)
        self._on_status(f"Connected {len(midpoints)} midpoints. Ready to analyze.")
        return None

    def run_analysis(self) -> str | None:
        """
        Run analysis on connected midpoints.

        Returns error message or None on success.
        """
        midpoints = self.session.midpoints
        if len(midpoints) < 2:
            return "Need at least 2 segments for analysis"

        pts = np.array(midpoints, dtype=float)
        metrics = analyze_segment_sequence(
            pts,
            pixel_to_unit=self.session.pixel_size,
            unit_name=self.session.unit_name,
        )

        if self.session.image_path:
            metrics.metadata = metrics.metadata or {}
            metrics.metadata["image_file"] = str(self.session.image_path)
            metrics.metadata["n_original_segments"] = len(self.session.segments)

        self.session.current_analysis = metrics
        self.session.analysis_history.append(metrics)
        self._on_analysis(metrics)
        self._on_status("Analysis complete. See Summary tab for results.")
        return None

    def add_sarcomere_point(self, x: float, y: float) -> bool:
        """
        Add point for sarcomere annotation.

        Points are: A, B (Z-line 1), C, D (M-line), E, F (Z-line 2)
        Returns True if all 6 points are collected.
        """
        if len(self.session.sarcomere_points) >= 6:
            return True

        self.session.sarcomere_points.append((x, y))
        point_index = len(self.session.sarcomere_points)

        self.viz.draw_sarcomere_point(x, y, point_index)

        if point_index in (2, 4, 6):
            idx = point_index - 2
            p1 = self.session.sarcomere_points[idx]
            p2 = self.session.sarcomere_points[idx + 1]
            if point_index == 2:
                color = VIZ.ZLINE1_COLOR
            elif point_index == 4:
                color = VIZ.MLINE_COLOR
            else:
                color = VIZ.ZLINE2_COLOR
            self.viz.draw_sarcomere_zline(p1, p2, color)

        if point_index == 6:
            self._on_status("6 points collected. Press 'c' to confirm sarcomere.")
            return True

        labels = ["A", "B", "C", "D", "E", "F"]
        next_label = labels[point_index] if point_index < 6 else "done"
        self._on_status(f"Point {labels[point_index - 1]} recorded. Click point {next_label}.")
        return False

    def confirm_sarcomere(self) -> str | None:
        """
        Confirm 6-point sarcomere annotation.

        Returns error message or None on success.
        """
        if len(self.session.sarcomere_points) != 6:
            return "Need exactly 6 points for sarcomere analysis"

        pts = self.session.sarcomere_points
        point_a, point_b = pts[0], pts[1]
        point_c, point_d = pts[2], pts[3]
        point_e, point_f = pts[4], pts[5]

        for p1, p2, name in [
            (point_a, point_b, "Z-line 1 (AB)"),
            (point_c, point_d, "M-line (CD)"),
            (point_e, point_f, "Z-line 2 (EF)"),
        ]:
            if np.hypot(p2[0] - p1[0], p2[1] - p1[1]) < 1.0:
                return f"{name} has near-zero length (< 1 pixel)"

        try:
            geom = calculate_sarcomere_geometry(
                point_a=point_a,
                point_b=point_b,
                point_c=point_c,
                point_d=point_d,
                point_e=point_e,
                point_f=point_f,
                sarcomere_id=len(self.session.sarcomere_geometries) + 1,
                pixel_to_unit=self.session.pixel_size,
                unit=self.session.unit_name,
            )
        except ValueError as e:
            return str(e)

        self.session.sarcomere_geometries.append(geom)
        self.viz.clear_temp_artists()
        self.viz.draw_sarcomere_overlay(geom)
        self.session.sarcomere_points.clear()
        self._on_sarcomere(geom)
        self._on_status(
            f"Sarcomere {geom.sarcomere_id} confirmed. Click to annotate another or export results."
        )
        return None

    def cancel_pending(self) -> None:
        """Cancel current pending annotation."""
        if self.session.mode == AnalysisMode.SINGLE_SARCOMERE:
            self.session.sarcomere_points.clear()
        else:
            self.session.pending_points.clear()
        self.viz.clear_temp_artists()
        self._on_status("Pending annotation cancelled.")

    def delete_last(self) -> None:
        """Delete last annotation based on current mode."""
        if self.session.mode == AnalysisMode.SINGLE_SARCOMERE:
            self._delete_last_sarcomere()
        else:
            self._delete_last_linearity()

    def _delete_last_linearity(self) -> None:
        """Delete last linearity annotation."""
        if self.session.pending_points:
            self.session.pending_points.clear()
            self.viz.clear_temp_artists()
            self._on_status("Pending points cleared.")
        elif self.session.segments:
            self.session.segments.pop()
            self.viz.redraw_all_segments(self.session.segments, self.session.midpoints)
            self._on_status(f"Deleted segment. {len(self.session.segments)} remaining.")
        else:
            self._on_status("Nothing to delete.")

    def _delete_last_sarcomere(self) -> None:
        """Delete last sarcomere annotation."""
        if self.session.sarcomere_points:
            self.session.sarcomere_points.clear()
            self.viz.clear_temp_artists()
            self._on_status("Pending points cleared.")
        elif self.session.sarcomere_geometries:
            self.session.sarcomere_geometries.pop()
            self.viz.redraw_all_sarcomeres(self.session.sarcomere_geometries)
            n = len(self.session.sarcomere_geometries)
            self._on_status(f"Deleted sarcomere. {n} remaining.")
        else:
            self._on_status("Nothing to delete.")

    def clear_all(self) -> None:
        """Clear all annotations for current mode."""
        self.session.clear_annotations()
        self.viz.clear_all_artists()
        self._on_status("All annotations cleared.")

    def on_mode_change(self, new_mode: str) -> None:
        """Handle mode change - clear pending annotations but preserve confirmed ones."""
        if self.session.mode == new_mode:
            return

        if self.session.mode == AnalysisMode.SINGLE_SARCOMERE:
            self.session.sarcomere_points.clear()
        else:
            self.session.pending_points.clear()

        self.viz.clear_temp_artists()
        self.session.mode = new_mode
        self.session.annotating = False

        self._on_status(f"Switched to {new_mode} mode.")
