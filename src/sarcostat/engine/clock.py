"""Time sources of a study session.

A study session reads two clocks: a monotonic one, which measures how long
an annotator works on an item, and the wall clock, which dates the session.
:class:`~sarcostat.engine.study.StudyPresenter` reads the wall clock once,
when the session opens, and dates every later moment by adding monotonic
seconds to that anchor, so a wall clock that jumps (a time-zone change, a
synchronisation) cannot reorder the timestamps of a session.

- :class:`SystemClock` reads the clocks of the running process (a desktop
  session or a test).
- :class:`ExplicitClock` holds the times the browser captured at the input
  event, ``performance.now() / 1000`` and ``Date.now()``, and is set before
  each study command, so the times recorded are those of the click, not
  those of its processing in the worker.
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class Clock(Protocol):
    """Time source of a session (replaced by a fake clock in tests)."""

    def monotonic(self) -> float:
        """Seconds from an arbitrary origin that never decrease."""
        ...

    def utc_now(self) -> datetime:
        """Current wall-clock time, timezone-aware UTC."""
        ...


class SystemClock:
    """The process clocks."""

    def monotonic(self) -> float:
        """:func:`time.monotonic`."""
        return time.monotonic()

    def utc_now(self) -> datetime:
        """Current UTC time."""
        return datetime.now(timezone.utc)


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, got {value!r}.")
    return float(value)


def _wall(wall_ms: Any) -> datetime:
    milliseconds = _number(wall_ms, "wall_ms")
    try:
        return _EPOCH + timedelta(milliseconds=milliseconds)
    except OverflowError:
        raise ValueError(f"wall_ms {wall_ms!r} is outside the supported dates.") from None


def _mono(mono_s: Any) -> float:
    seconds = _number(mono_s, "mono_s")
    if seconds < 0:
        raise ValueError(f"mono_s must be >= 0, got {mono_s!r}.")
    return seconds


class ExplicitClock:
    """Clock readings given by the caller, as captured at the input event.

    Parameters
    ----------
    mono_s : float
        Monotonic seconds (``performance.now() / 1000`` in the browser), >= 0.
    wall_ms : float
        Wall-clock milliseconds since 1970-01-01 UTC (``Date.now()``).

    Notes
    -----
    :meth:`monotonic` never decreases: a ``mono_s`` smaller than the last one
    is clamped to the last one and counted in :attr:`clamped`. The wall clock
    is returned as given; the presenter anchors it once per session.

    Raises
    ------
    ValueError
        If a value is not a finite number, ``mono_s`` is negative, or
        ``wall_ms`` is outside the dates :class:`datetime.datetime` holds.
    """

    def __init__(self, mono_s: float, wall_ms: float) -> None:
        self._mono = _mono(mono_s)
        self._utc = _wall(wall_ms)
        self.clamped = 0

    def set(self, mono_s: float, wall_ms: float) -> None:
        """Record the times of the next command (both checked before either is kept)."""
        mono = _mono(mono_s)
        utc = _wall(wall_ms)
        if mono < self._mono:
            self.clamped += 1
        else:
            self._mono = mono
        self._utc = utc

    def monotonic(self) -> float:
        """The last ``mono_s``, never less than an earlier one."""
        return self._mono

    def utc_now(self) -> datetime:
        """The last ``wall_ms`` as a timezone-aware UTC time."""
        return self._utc


__all__ = ["Clock", "ExplicitClock", "SystemClock"]
