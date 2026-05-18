"""Tests for the canonical session data model.

These tests run without a Tk root and exercise only the dataclasses in
:mod:`sarcostat.gui.session` and the constants in
:mod:`sarcostat.gui.constants`. They are designed to keep the
state-management contract green even before the wider gui.py monolith
is fully wired through the presenter.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sarcostat.gui.session import AnalysisMode, AnalysisSession, UserSegment


class TestUserSegment:
    """Tests for :class:`UserSegment`."""

    def test_midpoint_axis_aligned(self) -> None:
        """The midpoint of an axis-aligned segment is the arithmetic mean."""
        seg = UserSegment(p1=(0.0, 0.0), p2=(10.0, 4.0))
        assert seg.midpoint == (5.0, 2.0)

    def test_segment_is_frozen(self) -> None:
        """``UserSegment`` is immutable (frozen dataclass)."""
        from dataclasses import FrozenInstanceError

        seg = UserSegment(p1=(0.0, 0.0), p2=(1.0, 1.0))
        with pytest.raises(FrozenInstanceError):
            seg.p1 = (5.0, 5.0)


class TestAnalysisSessionState:
    """Tests for the state-mutation methods on :class:`AnalysisSession`."""

    def test_default_state(self) -> None:
        """A new session starts empty in linearity mode."""
        s = AnalysisSession()
        assert s.mode == AnalysisMode.MYOFIBRIL_LINEARITY
        assert not s.has_image
        assert s.segments == []
        assert s.midpoints == []
        assert s.sarcomere_points == []

    def test_midpoints_property_is_derived(self) -> None:
        """``midpoints`` is recomputed from ``segments`` on each access."""
        s = AnalysisSession()
        s.segments.append(UserSegment(p1=(0.0, 0.0), p2=(2.0, 0.0)))
        s.segments.append(UserSegment(p1=(2.0, 0.0), p2=(4.0, 0.0)))
        assert s.midpoints == [(1.0, 0.0), (3.0, 0.0)]

    def test_has_image_with_path(self) -> None:
        """``has_image`` flips True once an image_path is set."""
        s = AnalysisSession()
        s.image_path = Path("/tmp/some.tif")
        assert s.has_image is True

    def test_clear_linearity_annotations(self) -> None:
        """``clear_linearity_annotations`` resets only the linearity state."""
        s = AnalysisSession()
        s.segments.append(UserSegment(p1=(0.0, 0.0), p2=(1.0, 0.0)))
        s.pending_points.append((10.0, 10.0))
        s.annotating = True
        s.sarcomere_points.append((5.0, 5.0))
        s.clear_linearity_annotations()
        assert s.segments == []
        assert s.pending_points == []
        assert s.annotating is False
        assert s.sarcomere_points == [(5.0, 5.0)]

    def test_clear_sarcomere_annotations(self) -> None:
        """``clear_sarcomere_annotations`` resets only the sarcomere state."""
        s = AnalysisSession()
        s.sarcomere_points.append((5.0, 5.0))
        s.segments.append(UserSegment(p1=(0.0, 0.0), p2=(1.0, 0.0)))
        s.clear_sarcomere_annotations()
        assert s.sarcomere_points == []
        assert len(s.segments) == 1

    def test_clear_annotations_keeps_history(self) -> None:
        """``clear_annotations`` clears live state but preserves history."""
        s = AnalysisSession()
        s.segments.append(UserSegment(p1=(0.0, 0.0), p2=(1.0, 0.0)))
        s.analysis_history.append(object())
        s.clear_annotations()
        assert s.segments == []
        assert len(s.analysis_history) == 1

    def test_clear_all_clears_history(self) -> None:
        """``clear_all`` also clears the analysis history."""
        s = AnalysisSession()
        s.segments.append(UserSegment(p1=(0.0, 0.0), p2=(1.0, 0.0)))
        s.analysis_history.append(object())
        s.clear_all()
        assert s.segments == []
        assert s.analysis_history == []
