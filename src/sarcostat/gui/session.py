"""
Analysis session data model for SarcoStat GUI.

Separates state management from UI logic for testability and clarity.
"""

from dataclasses import dataclass, field
from pathlib import Path

from sarcostat.core.circular_stats import SequenceMetrics
from sarcostat.core.sarcomere_morphometry import SarcomereGeometry

Point = tuple[float, float]


class AnalysisMode:
    """Analysis mode enumeration."""

    MYOFIBRIL_LINEARITY = "Myofibril Linearity"
    SINGLE_SARCOMERE = "Single Sarcomere"


@dataclass(frozen=True)
class UserSegment:
    """A user-defined segment (two points) and its derived midpoint."""

    p1: Point
    p2: Point

    @property
    def midpoint(self) -> Point:
        """Return the midpoint of the segment as ``(x, y)``."""
        return ((self.p1[0] + self.p2[0]) / 2.0, (self.p1[1] + self.p2[1]) / 2.0)


@dataclass
class AnalysisSession:
    """Holds all state for a single analysis session."""

    image_path: Path | None = None
    pixel_size: float = 1.0
    unit_name: str = "um"

    mode: str = AnalysisMode.MYOFIBRIL_LINEARITY

    annotating: bool = False
    segments: list[UserSegment] = field(default_factory=list)
    pending_points: list[Point] = field(default_factory=list)

    sarcomere_points: list[Point] = field(default_factory=list)
    sarcomere_geometries: list[SarcomereGeometry] = field(default_factory=list)

    current_analysis: SequenceMetrics | None = None
    analysis_history: list[SequenceMetrics] = field(default_factory=list)

    @property
    def midpoints(self) -> list[Point]:
        """Compute midpoints from confirmed segments."""
        return [seg.midpoint for seg in self.segments]

    @property
    def has_image(self) -> bool:
        """Check if an image is loaded."""
        return self.image_path is not None

    def clear_linearity_annotations(self) -> None:
        """Reset Myofibril Linearity mode annotation state."""
        self.segments.clear()
        self.pending_points.clear()
        self.annotating = False

    def clear_sarcomere_annotations(self) -> None:
        """Reset Single Sarcomere mode annotation state."""
        self.sarcomere_points.clear()
        self.sarcomere_geometries.clear()

    def clear_annotations(self) -> None:
        """Reset all annotation state for both modes."""
        self.clear_linearity_annotations()
        self.clear_sarcomere_annotations()
        self.current_analysis = None

    def clear_all(self) -> None:
        """Reset all state including analysis history."""
        self.clear_annotations()
        self.analysis_history.clear()
