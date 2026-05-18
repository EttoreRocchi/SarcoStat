"""
GUI constants for SarcoStat.

Centralizes magic numbers for consistency, maintainability, and configurability.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class VisualizationConstants:
    """Constants for matplotlib visualization."""

    POINT_MARKER_SIZE: int = 8
    MIDPOINT_MARKER_SIZE: int = 8
    SARCOMERE_MARKER_SIZE: int = 5
    PENDING_MARKER_SIZE: int = 6

    SEGMENT_LINE_WIDTH: float = 2.0
    MIDPOINT_LINK_WIDTH: float = 3.0
    SARCOMERE_AXIS_WIDTH: float = 2.0
    TEMP_LINE_WIDTH: float = 1.0
    ZLINE_WIDTH: float = 2.0

    SEGMENT_ALPHA: float = 0.75
    MIDPOINT_LINK_ALPHA: float = 0.85
    TEMP_LINE_ALPHA: float = 0.6
    LABEL_BOX_ALPHA: float = 0.7
    OVERLAY_LABEL_ALPHA: float = 0.5

    SEGMENT_LABEL_FONTSIZE: int = 8
    OVERLAY_LABEL_FONTSIZE: int = 8
    SARCOMERE_LABEL_FONTSIZE: int = 8
    POINT_LABEL_FONTSIZE: int = 10

    SEGMENT_LABEL_OFFSET_Y: float = -6.0
    POINT_LABEL_OFFSET_X: float = 5.0
    POINT_LABEL_OFFSET_Y: float = -5.0

    SEGMENT_COLOR: str = "yellow"
    MIDPOINT_COLOR: str = "lime"
    MIDPOINT_EDGE_COLOR: str = "darkgreen"
    MIDPOINT_LINK_COLOR: str = "cyan"
    TEMP_LINE_COLOR: str = "cyan"

    ZLINE1_COLOR: str = "red"
    MLINE_COLOR: str = "blue"
    ZLINE2_COLOR: str = "green"
    SARCOMERE_AXIS_COLOR: str = "cyan"
    SARCOMERE_LABEL_BG: str = "purple"

    PENDING_POINT1_COLOR: str = "red"
    PENDING_POINT2_COLOR: str = "blue"


@dataclass(frozen=True)
class NavigationConstants:
    """Constants for view navigation."""

    ZOOM_STEP: float = 1.2
    PAN_FRAC: float = 0.10
    PAN_FRAC_FAST: float = 0.25


@dataclass(frozen=True)
class UIConstants:
    """Constants for UI layout."""

    DEFAULT_WINDOW_WIDTH: int = 1400
    DEFAULT_WINDOW_HEIGHT: int = 860
    FIGURE_DPI: int = 96
    FIGURE_SIZE: tuple[int, int] = (6, 6)
    LOGO_HEIGHT: int = 40


VIZ = VisualizationConstants()
NAV = NavigationConstants()
UI = UIConstants()

SARCOMERE_POINT_LABELS = ["A", "B", "C", "D", "E", "F"]


def get_sarcomere_point_color(point_index: int) -> str:
    """Get the color for a sarcomere annotation point (1-indexed)."""
    if point_index <= 2:
        return VIZ.ZLINE1_COLOR
    elif point_index <= 4:
        return VIZ.MLINE_COLOR
    else:
        return VIZ.ZLINE2_COLOR


def get_sarcomere_point_label(point_index: int) -> str:
    """Get the label for a sarcomere annotation point (1-indexed)."""
    return SARCOMERE_POINT_LABELS[point_index - 1]
