"""A scripted, headless study session driven through the study engine.

The script drives :class:`~sarcostat.engine.study.StudyPresenter` the way a
window does, without one: it places landmarks at fixed fractions of each
item's box, undoes one click, marks one M-line as not identifiable and skips
one item with a reason. It is the engine port of the study-mode demo script
of the desktop app and makes the same presenter calls in the same order, so
with the same clock it gives the same session file.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from sarcostat.engine.clock import Clock
from sarcostat.engine.study import SessionStore, StudyPresenter
from sarcostat.io.study import (
    DisplayMapping,
    SessionKit,
    load_session_kit,
    quantize_coordinate,
)

SHAPE = [(0.2, 0.2), (0.2, 0.8), (0.5, 0.2), (0.5, 0.8), (0.8, 0.2), (0.8, 0.8)]
"""Sarcomere landmarks A-F as fractions ``(fx, fy)`` of the item box, in original-image orientation."""
CHAIN = [((0.15 + 0.2 * k, 0.2), (0.15 + 0.2 * k, 0.8)) for k in range(4)]
PAIR = [
    [((0.2 + 0.3 * k, 0.1), (0.2 + 0.3 * k, 0.45)) for k in range(3)],
    [((0.2 + 0.3 * k, 0.55), (0.2 + 0.3 * k, 0.9)) for k in range(3)],
]
M_SKIP_ITEM = 3
"""1-based position of the sarcomere whose M-line is skipped."""
SKIPPED_ITEM = 6
"""1-based position of the item skipped with a reason."""
SKIP_REASON = "Demo: skipped on purpose"

Tick = Callable[[], None]


class QuietView:
    """A view that ignores everything (the session runs headless)."""

    def show_status(self, message: str) -> None:
        """Ignore."""

    def show_item(self, kit_file: str, roi: Any, title: str) -> None:
        """Ignore."""

    def show_marks(self, marks: Any, strokes: Any) -> None:
        """Ignore."""

    def show_controls(self, controls: Any) -> None:
        """Ignore."""

    def show_paused(self) -> None:
        """Ignore."""

    def show_finished(self, file_name: str) -> None:
        """Ignore."""


def _check(problem: str | None) -> None:
    if problem:
        raise RuntimeError(problem)


def _call(tick: Tick | None, action: Callable[[], str | None]) -> None:
    """Run one presenter action, after ``tick`` (which can set an explicit clock)."""
    if tick is not None:
        tick()
    _check(action())


def _click(presenter: StudyPresenter, fx: float, fy: float, tick: Tick | None) -> None:
    """Click the display position of a fraction of the open item's box."""
    item = presenter.current_item
    assert item is not None
    x_min, y_min, x_max, y_max = item["roi"]
    point = [
        quantize_coordinate(x_min + fx * (x_max - x_min)),
        quantize_coordinate(y_min + fy * (y_max - y_min)),
    ]
    x, y = DisplayMapping.from_item(item).to_display([point])[0]
    _call(tick, lambda: presenter.add_click(float(x), float(y)))


def annotate_open_item(presenter: StudyPresenter, position: int, tick: Tick | None = None) -> None:
    """Annotate (or skip) the open item, the ``position``-th of the session."""
    item = presenter.current_item
    assert item is not None
    if position == SKIPPED_ITEM:
        _call(tick, lambda: presenter.skip_item(SKIP_REASON))
        return
    if item["type"] == "shape":
        landmarks = SHAPE
        if position == M_SKIP_ITEM:
            landmarks = SHAPE[:2] + SHAPE[4:]
        for k, (fx, fy) in enumerate(landmarks):
            if position == M_SKIP_ITEM and k == 2:
                _call(tick, presenter.skip_m_line)
            if position == 1 and k == 1:
                _click(presenter, 0.9, 0.9, tick)
                _call(tick, presenter.undo)
            _click(presenter, fx, fy, tick)
        _call(tick, presenter.confirm_item)
    elif item["type"] == "chain":
        for p1, p2 in CHAIN:
            _click(presenter, *p1, tick)
            _click(presenter, *p2, tick)
        _call(tick, lambda: presenter.confirm_item(branch_end=False))
    else:
        for fibril, zlines in enumerate(PAIR):
            if fibril:
                _call(tick, presenter.next_myofibril)
            for p1, p2 in zlines:
                _click(presenter, *p1, tick)
                _click(presenter, *p2, tick)
        _call(tick, presenter.confirm_item)


def run_scripted_session(
    kit: SessionKit | str | Path,
    store_or_dir: SessionStore | str | Path,
    clock: Clock | None = None,
    stop_after: int | None = None,
    tick: Tick | None = None,
    view: Any = None,
) -> StudyPresenter:
    """Annotate a kit headless and return the presenter.

    With ``stop_after`` the session stops (as after a crash) once that many
    items are done; running it again on the same store resumes it from the
    autosave. ``tick`` runs before every presenter action.
    """
    loaded = kit if isinstance(kit, SessionKit) else load_session_kit(kit)
    presenter = StudyPresenter(loaded, store_or_dir, view or QuietView(), clock=clock)
    _call(tick, presenter.confirm_identity)
    while not presenter.finished:
        position = len(presenter.records) + 1
        if stop_after is not None and position > stop_after:
            break
        annotate_open_item(presenter, position, tick)
    return presenter
