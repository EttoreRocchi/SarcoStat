"""Study mode: blinded annotation sessions driven by a session kit.

The :class:`StudyPresenter` runs one session of one annotator from a
:class:`~sarcostat.io.study.SessionKit`:

- the annotator confirms the IDs read from the manifest before any item is
  shown;
- items come one at a time, in manifest order, each showing only its
  8-bit kit image (fixed 0-255 grey scale) and its ROI box;
- no metric is computed or shown: this module imports nothing from
  :mod:`sarcostat.core`, and neither the view protocol nor
  :meth:`StudyPresenter.view_state` has a channel for numbers other than
  display coordinates, counts and indexes;
- undo works only inside the current item and is counted; there is no way
  back to a confirmed item;
- a skipped item needs a reason; on a sarcomere the M-line alone can be
  marked as not identifiable, keeping both Z-lines;
- every action is saved to a :class:`SessionStore` as an autosave document
  with an increasing ``seq``, from which a crashed session resumes;
- the finished session is one annotation file of format v1, which is never
  overwritten.

Item images are checked from their bytes (:meth:`SessionKit.read_file` and
:func:`~sarcostat.io.study.check_item_image`) when the session opens and
whenever an item is shown; they are never decoded here, since the view draws
them. Marks and strokes carry roles, never colours, and prompts and notices
are :class:`Notice` codes whose English sentences come from the one table
:data:`NOTICES`.

Timestamps come from the monotonic clock, anchored to the wall clock when
the session opens, so they never run backwards (:mod:`sarcostat.engine.clock`).
"""

from __future__ import annotations

import copy
import functools
import math
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Concatenate, ParamSpec, Protocol, TypeAlias

from sarcostat import __version__
from sarcostat.io.annotations import (
    FORMAT_NAME,
    FORMAT_VERSION,
    dumps_annotations,
    validate_annotations,
)
from sarcostat.io.provenance import source_digest
from sarcostat.io.study import (
    DisplayMapping,
    SessionKit,
    StudyError,
    check_item_image,
    dumps_canonical,
    loads_strict,
    quantize_coordinate,
    write_bytes_atomic,
    write_bytes_exclusive,
)

from .clock import Clock, ExplicitClock, SystemClock

M_LINE_SKIP_REASON = "M-line not identifiable"

_UTC_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
_AUTOSAVE_FORMAT = "sarcostat-study-autosave"
_AUTOSAVE_VERSION = "1.0"
_MIN_LINE_PX = 1.0
_LANDMARK_LETTERS = {1: "A", 2: "B", 3: "C", 4: "D", 5: "E", 6: "F"}
_SHAPE_STROKES = {2: "z1", 4: "m", 6: "z2"}

ParamValue: TypeAlias = str | int | bool

_IDENTITY = (
    "Study: {study_id}\nAnnotator: {annotator_id}\nSession: {session_id}\n\n"
    "Are you annotator {annotator_id}, and is this session {session_id}? "
    "Answer No if this kit was not given to you."
)

NOTICES: dict[str, str] = {
    "identity": _IDENTITY,
    "identity_resumed": _IDENTITY
    + "\n\nThis session was interrupted; it resumes at item {item} of {total}.",
    "item_title": "Item {index} of {total}",
    "shape_click": "Sarcomere: click {landmark} ({index} of {total}).",
    "shape_click_or_skip_m_line": (
        "Sarcomere: click {landmark} ({index} of {total}). "
        "If the M-line is not identifiable, press 'Skip M-line'."
    ),
    "shape_complete": "All points placed: press Confirm (Enter) to save this item, or Undo.",
    "chain_second_end": "Chain: click the second end of Z-line {zline}.",
    "chain_first_end": (
        "Chain: click the first end of Z-line {zline} ({done} done). "
        "Press Confirm when the chain ends; tick 'Ends at a branch' if it stops before one."
    ),
    "pair_myofibril_1": (
        "Pair, myofibril 1: click the {end} end of Z-line {zline} ({done} done). "
        "Press 'Next myofibril' when myofibril 1 is complete."
    ),
    "pair_myofibril_2": (
        "Pair, myofibril 2: click the {end} end of Z-line {zline} ({done} done). "
        "Press Confirm when myofibril 2 is complete."
    ),
    "paused": "Paused: the item is hidden and its clock is stopped. Press Resume.",
    "session_complete": (
        "Session complete: give {file_name} to the coordinator. You can close the window."
    ),
    "nothing_to_undo": "Nothing to undo in this item; confirmed items cannot be changed.",
    "session_finished": "The session is finished.",
    "session_already_open": "The session is already open.",
    "identity_not_confirmed": "Confirm your identity first.",
    "session_paused": "The session is paused: press Resume.",
    "no_open_item": "No item is open.",
    "session_not_paused": "The session is not paused.",
    "click_outside_image": "Click inside the image.",
    "shape_points_placed": "All points of this sarcomere are placed: confirm it, or undo.",
    "m_line_skip_not_shape": "Only a sarcomere item has an M-line to skip.",
    "m_line_already_skipped": "The M-line is already marked as not identifiable.",
    "m_line_started": "The M-line is already started: undo its points first.",
    "not_a_pair": "Only a pair item has a second myofibril.",
    "myofibril_2_open": "Myofibril 2 is already open; a pair has exactly two myofibrils.",
    "zline_unfinished": "{part}: finish the Z-line you started, or undo it.",
    "zlines_too_few": "{part} needs at least 2 Z-lines.",
    "zline_too_short": (
        "{part}: the ends of Z-line {zline} are less than 1 pixel apart; undo and click it again."
    ),
    "shape_points_missing": "Place all {expected} points of the sarcomere first ({placed} placed).",
    "shape_line_too_short": (
        "The ends of {line} are less than 1 pixel apart; undo and click them again."
    ),
    "pair_needs_next_myofibril": (
        "Press 'Next myofibril' after myofibril 1, then trace myofibril 2."
    ),
    "skip_needs_reason": "A skipped item needs a reason.",
    "finish_failed": (
        "The session file could not be written: {error} "
        "Your work is kept in {autosave}; tell the coordinator."
    ),
}
"""English sentence of every prompt and notice code; ``{name}`` is a parameter."""

PARAM_WORDS: dict[str, dict[str, str]] = {
    "landmark": {
        "A": "A (Z-line 1 start)",
        "B": "B (Z-line 1 end)",
        "C": "C (M-line start)",
        "D": "D (M-line end)",
        "E": "E (Z-line 2 start)",
        "F": "F (Z-line 2 end)",
    },
    "line": {"z1": "Z-line 1 (A-B)", "m": "M-line (C-D)", "z2": "Z-line 2 (E-F)"},
    "part": {"chain": "The chain", "myofibril_1": "Myofibril 1", "myofibril_2": "Myofibril 2"},
}
"""English words of the enumerated parameter values (other values are written as given)."""


def notice_text(code: str, params: Mapping[str, ParamValue] | None = None) -> str:
    """The English sentence of ``code`` with ``params`` filled in (:data:`NOTICES`)."""
    words = {
        name: PARAM_WORDS.get(name, {}).get(value, value) if isinstance(value, str) else value
        for name, value in (params or {}).items()
    }
    return NOTICES[code].format(**words)


@dataclass(frozen=True)
class Notice:
    """A prompt or a notice as a code and its parameters (never coordinates or times)."""

    code: str
    params: Mapping[str, ParamValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.code not in NOTICES:
            raise KeyError(f"unknown notice code {self.code!r}")

    @property
    def text(self) -> str:
        """The English sentence (:func:`notice_text`)."""
        return notice_text(self.code, self.params)

    def to_json(self) -> dict[str, Any]:
        """``{"code", "params"}`` for the view state."""
        return {"code": self.code, "params": dict(self.params)}


@dataclass(frozen=True)
class Mark:
    """A clicked point to draw, in display coordinates.

    ``role`` is a shape landmark ``"A"`` to ``"F"``, ``"chain"``, or
    ``"myofibril_1"``/``"myofibril_2"`` for the two myofibrils of a pair.
    """

    x: float
    y: float
    role: str

    def to_json(self) -> dict[str, Any]:
        """``{"x", "y", "role"}``."""
        return {"x": self.x, "y": self.y, "role": self.role}


@dataclass(frozen=True)
class Stroke:
    """A line between two clicked points, in display coordinates.

    ``role`` is ``"z1"``, ``"m"`` (drawn dashed) or ``"z2"`` for a sarcomere,
    ``"chain"``, or ``"myofibril_1"``/``"myofibril_2"``.
    """

    p1: tuple[float, float]
    p2: tuple[float, float]
    role: str

    def to_json(self) -> dict[str, Any]:
        """``{"p1": [x, y], "p2": [x, y], "role"}``."""
        return {"p1": list(self.p1), "p2": list(self.p2), "role": self.role}


@dataclass(frozen=True)
class StudyControls:
    """Which actions the window should offer for the current state."""

    can_confirm: bool
    can_undo: bool
    can_skip_m_line: bool
    can_next_myofibril: bool
    shows_branch_end: bool
    item_type: str | None

    def to_json(self) -> dict[str, Any]:
        """The six fields as an object."""
        return {
            "can_confirm": self.can_confirm,
            "can_undo": self.can_undo,
            "can_skip_m_line": self.can_skip_m_line,
            "can_next_myofibril": self.can_next_myofibril,
            "shows_branch_end": self.shows_branch_end,
            "item_type": self.item_type,
        }


_IDLE = StudyControls(False, False, False, False, False, None)

VIEW_STATE_KEYS = frozenset(
    {
        "item_file",
        "roi",
        "title",
        "marks",
        "strokes",
        "controls",
        "step",
        "progress",
        "notice",
        "paused",
        "finished",
    }
)
"""The keys of :meth:`StudyPresenter.view_state`, and no other."""


class StudyView(Protocol):
    """What the study presenter needs from its window.

    There is deliberately no method that takes a measurement: study mode
    shows images, clicks, instructions and progress only.
    """

    def show_status(self, message: str) -> None:
        """Show the next step or a notice."""
        ...

    def show_item(self, kit_file: str, roi: tuple[float, float, float, float], title: str) -> None:
        """Show the kit image ``kit_file`` with its ROI box ``(x_min, y_min, x_max, y_max)``."""
        ...

    def show_marks(self, marks: list[Mark], strokes: list[Stroke]) -> None:
        """Replace the drawn clicks of the current item."""
        ...

    def show_controls(self, controls: StudyControls) -> None:
        """Enable or disable the actions."""
        ...

    def show_paused(self) -> None:
        """Hide the item while the session is paused."""
        ...

    def show_finished(self, file_name: str) -> None:
        """Tell the annotator that the session file ``file_name`` is complete."""
        ...


class SessionStore(Protocol):
    """Where a session keeps its autosave and its finished file.

    The presenter calls :meth:`bind` once, before any other method, with the
    session stem ``<study>_<annotator>_<session>``.
    """

    def bind(self, stem: str) -> None:
        """Attach the store to the session ``stem``."""
        ...

    def load_autosave(self) -> tuple[int, bytes] | None:
        """``(seq, bytes)`` of the stored autosave, or ``None``."""
        ...

    def put_autosave(self, seq: int, data: bytes) -> None:
        """Store ``data`` as the autosave ``seq``; refuse a ``seq`` not above the stored one."""
        ...

    def finished_exists(self) -> bool:
        """Whether the finished session file exists."""
        ...

    def put_finished(self, file_name: str, data: bytes) -> None:
        """Store the finished file; never replace one."""
        ...

    def discard_autosave(self) -> None:
        """Remove the autosave (after the finished file is stored)."""
        ...


def _check_seq(seq: Any) -> int:
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
        raise StudyError(f"seq must be an integer >= 0, got {seq!r}.")
    return int(seq)


def _refuse_seq(seq: int, stored: int) -> None:
    if seq <= stored:
        raise StudyError(
            f"Autosave {seq} refused: the store already holds autosave {stored}; "
            "the session continues elsewhere."
        )


def _check_file_name(file_name: str) -> str:
    if not file_name or Path(file_name).name != file_name or file_name in (".", ".."):
        raise StudyError(f"{file_name!r} is not a bare file name.")
    return file_name


def _autosave_seq(data: bytes) -> int:
    """The ``seq`` of an autosave document, 0 when it has none or cannot be read."""
    try:
        doc = loads_strict(data)
    except StudyError:
        return 0
    seq = doc.get("seq", 0) if isinstance(doc, dict) else 0
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
        return 0
    return seq


class FileSessionStore:
    """The autosave and the finished file in a folder, with the file names of the desktop app.

    The autosave is ``<stem>.autosave.json``, written atomically; the finished
    file is ``<stem>.json``, written once and never replaced. A put whose
    ``seq`` is not above the one in the autosave on disk (or the last one this
    store wrote) is refused, so a stale copy of a session cannot overwrite a
    newer autosave.

    Parameters
    ----------
    output_dir : str or pathlib.Path
        Folder of the session files; created at the first write.
    """

    def __init__(self, output_dir: str | os.PathLike[str]) -> None:
        self.output_dir = Path(output_dir)
        self._stem: str | None = None
        self._seq = 0

    def bind(self, stem: str) -> None:
        """Attach the store to the session ``stem``; a store serves one session."""
        if self._stem is not None and self._stem != stem:
            raise StudyError(f"This store belongs to session {self._stem}, not {stem}.")
        self._stem = stem

    @property
    def output_path(self) -> Path | None:
        """Path of the finished session file (``None`` before :meth:`bind`)."""
        return None if self._stem is None else self.output_dir / f"{self._stem}.json"

    @property
    def autosave_path(self) -> Path | None:
        """Path of the autosave (``None`` before :meth:`bind`)."""
        return None if self._stem is None else self.output_dir / f"{self._stem}.autosave.json"

    def _paths(self) -> tuple[Path, Path]:
        output, autosave = self.output_path, self.autosave_path
        if output is None or autosave is None:
            raise StudyError("The session store is not bound to a session.")
        return output, autosave

    def load_autosave(self) -> tuple[int, bytes] | None:
        """Read the autosave; its ``seq`` is 0 if it has none (an autosave of the desktop app)."""
        _, path = self._paths()
        if not path.exists():
            return None
        data = path.read_bytes()
        seq = _autosave_seq(data)
        self._seq = max(self._seq, seq)
        return seq, data

    def put_autosave(self, seq: int, data: bytes) -> None:
        """Write the autosave atomically.

        Raises
        ------
        StudyError
            If ``seq`` is not above the stored one.
        """
        seq = _check_seq(seq)
        _, path = self._paths()
        stored = _autosave_seq(path.read_bytes()) if path.exists() else 0
        _refuse_seq(seq, max(stored, self._seq))
        self.output_dir.mkdir(parents=True, exist_ok=True)
        write_bytes_atomic(bytes(data), path)
        self._seq = seq

    def finished_exists(self) -> bool:
        """Whether ``<stem>.json`` exists."""
        output, _ = self._paths()
        return output.exists()

    def put_finished(self, file_name: str, data: bytes) -> None:
        """Write ``file_name`` as a new file; an existing file is never replaced."""
        self._paths()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        write_bytes_exclusive(bytes(data), self.output_dir / _check_file_name(file_name))

    def discard_autosave(self) -> None:
        """Delete the autosave file."""
        _, path = self._paths()
        path.unlink(missing_ok=True)


class MemorySessionStore:
    """The autosave and the finished file in memory (the browser keeps them in IndexedDB).

    Parameters
    ----------
    autosave : bytes, optional
        A stored autosave to resume from.
    seq : int, optional
        Its sequence number (required with ``autosave``).
    """

    def __init__(self, autosave: bytes | None = None, seq: int | None = None) -> None:
        if autosave is not None and seq is None:
            raise StudyError("An autosave needs its seq.")
        self._seq = 0 if seq is None else _check_seq(seq)
        self._autosave = None if autosave is None else bytes(autosave)
        self._finished: tuple[str, bytes] | None = None
        self.stem: str | None = None

    def bind(self, stem: str) -> None:
        """Attach the store to the session ``stem``; a store serves one session."""
        if self.stem is not None and self.stem != stem:
            raise StudyError(f"This store belongs to session {self.stem}, not {stem}.")
        self.stem = stem

    @property
    def seq(self) -> int:
        """The sequence number of the last stored autosave (0 before any)."""
        return self._seq

    @property
    def autosave(self) -> bytes | None:
        """The bytes of the stored autosave, or ``None``."""
        return self._autosave

    @property
    def finished_file_name(self) -> str | None:
        """Name of the finished session file, once stored."""
        return None if self._finished is None else self._finished[0]

    @property
    def finished_bytes(self) -> bytes | None:
        """Bytes of the finished session file, once stored."""
        return None if self._finished is None else self._finished[1]

    def load_autosave(self) -> tuple[int, bytes] | None:
        """``(seq, bytes)`` of the stored autosave, or ``None``."""
        return None if self._autosave is None else (self._seq, self._autosave)

    def put_autosave(self, seq: int, data: bytes) -> None:
        """Keep ``data`` as the autosave ``seq``.

        Raises
        ------
        StudyError
            If ``seq`` is not above the stored one.
        """
        seq = _check_seq(seq)
        _refuse_seq(seq, self._seq)
        self._seq = seq
        self._autosave = bytes(data)

    def finished_exists(self) -> bool:
        """Whether a finished file is stored."""
        return self._finished is not None

    def put_finished(self, file_name: str, data: bytes) -> None:
        """Keep the finished file; a second one is refused."""
        _check_file_name(file_name)
        if self._finished is not None:
            raise StudyError(
                f"{file_name} already exists; a finished session file cannot be overwritten."
            )
        self._finished = (file_name, bytes(data))

    def discard_autosave(self) -> None:
        """Drop the autosave (its ``seq`` is kept, so it never goes back)."""
        self._autosave = None


@dataclass
class _Action:
    """One undoable step: a click, the M-line skip or the switch to myofibril 2."""

    kind: str
    x: float = 0.0
    y: float = 0.0
    seconds: float = 0.0
    undo_count: int = 0

    def to_json(self) -> dict[str, Any]:
        if self.kind != "click":
            return {"kind": self.kind}
        return {
            "kind": "click",
            "x": self.x,
            "y": self.y,
            "seconds": self.seconds,
            "undo_count": self.undo_count,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> _Action:
        kind = str(data["kind"])
        if kind == "click":
            return cls(
                kind,
                float(data["x"]),
                float(data["y"]),
                float(data["seconds"]),
                int(data["undo_count"]),
            )
        if kind not in ("m_skip", "next_myofibril"):
            raise ValueError(f"unknown action {kind!r}")
        return cls(kind)


@dataclass
class _ItemState:
    item: dict[str, Any]
    mapping: DisplayMapping
    started_utc: str
    active_before: float = 0.0
    run_start: float | None = None
    undo_count: int = 0
    actions: list[_Action] = field(default_factory=list)

    @property
    def kind(self) -> str:
        return str(self.item["type"])

    @property
    def clicks(self) -> list[_Action]:
        return [a for a in self.actions if a.kind == "click"]

    @property
    def m_skipped(self) -> bool:
        return any(a.kind == "m_skip" for a in self.actions)

    @property
    def split(self) -> int | None:
        """Number of clicks on myofibril 1 once a pair moved on to myofibril 2."""
        n = 0
        for a in self.actions:
            if a.kind == "next_myofibril":
                return n
            if a.kind == "click":
                n += 1
        return None

    @property
    def landmarks(self) -> list[int]:
        """Landmark numbers (1-6, A-F) a sarcomere expects, in click order."""
        return [1, 2, 5, 6] if self.m_skipped else [1, 2, 3, 4, 5, 6]


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _parse_utc(text: str) -> datetime:
    return datetime.strptime(text, _UTC_FORMAT).replace(tzinfo=timezone.utc)


def _source_digest() -> str:
    """SHA-256 of the running SarcoStat sources (:mod:`sarcostat.io.provenance`)."""
    return source_digest()


def _session_bytes(doc: dict[str, Any]) -> bytes:
    """Validate ``doc`` as format v1 and return the bytes of the session file."""
    return dumps_annotations(doc).encode("ascii")


def _check_item_bytes(kit: SessionKit, item: Mapping[str, Any]) -> None:
    """Read the kit image of ``item`` (SHA-256 checked) and check it as a plain 8-bit grey PNG."""
    check_item_image(item, kit.read_file(str(item["kit_file"])))


def _zline_problem(clicks: list[_Action], part: str) -> Notice | None:
    """Why ``clicks`` are not yet a set of at least two Z-lines (``None`` when they are)."""
    if len(clicks) % 2:
        return Notice("zline_unfinished", {"part": part})
    if len(clicks) < 4:
        return Notice("zlines_too_few", {"part": part})
    for k in range(0, len(clicks), 2):
        a, b = clicks[k], clicks[k + 1]
        if math.hypot(b.x - a.x, b.y - a.y) < _MIN_LINE_PX:
            return Notice("zline_too_short", {"part": part, "zline": k // 2 + 1})
    return None


_P = ParamSpec("_P")


def _action(
    method: Callable[Concatenate[StudyPresenter, _P], Notice | None],
) -> Callable[Concatenate[StudyPresenter, _P], str | None]:
    """Run an action: clear the last notice, keep a new one and return its sentence."""

    @functools.wraps(method)
    def run(self: StudyPresenter, /, *args: _P.args, **kwargs: _P.kwargs) -> str | None:
        self._notice = None
        notice = method(self, *args, **kwargs)
        if notice is None:
            return None
        self._notice = notice
        return notice.text

    return run


class StudyPresenter:
    """Run one blinded session from a session kit.

    Every action returns ``None`` on success or a short English message the
    view should show as a notice; :meth:`view_state` gives the same notice
    as a code.

    Parameters
    ----------
    kit : SessionKit
        The verified kit (:func:`sarcostat.io.study.load_session_kit` or
        :func:`~sarcostat.io.study.load_session_kit_from_bytes`).
    store_or_dir : SessionStore, str or pathlib.Path
        Where the autosave and the finished session file go; a folder means
        a :class:`FileSessionStore` on it.
    view : StudyView
        The window, or a test double.
    clock : Clock, optional
        Time source; defaults to :class:`~sarcostat.engine.clock.SystemClock`.

    Raises
    ------
    StudyError
        If the session is already finished, an item image fails its check,
        the kit cannot give a valid format v1 file, or the autosave belongs
        to another kit or cannot be read.
    """

    def __init__(
        self,
        kit: SessionKit,
        store_or_dir: SessionStore | str | os.PathLike[str],
        view: StudyView,
        clock: Clock | None = None,
    ) -> None:
        self.kit = kit
        self.view = view
        self.clock: Clock = clock or SystemClock()
        manifest = kit.manifest
        stem = f"{manifest['study_id']}_{manifest['annotator_id']}_{manifest['session_id']}"
        if isinstance(store_or_dir, (str, os.PathLike)):
            self.store: SessionStore = FileSessionStore(store_or_dir)
        else:
            self.store = store_or_dir
        self.store.bind(stem)
        self.file_name = f"{stem}.json"
        self.autosave_name = f"{stem}.autosave.json"
        if self.store.finished_exists():
            raise StudyError(
                f"Session {manifest['session_id']} of annotator {manifest['annotator_id']} is "
                f"finished ({self.file_name}); a finished session cannot be overwritten."
            )
        self._digest = _source_digest()
        for item in kit.manifest["items"]:
            _check_item_bytes(kit, item)
        self._records: list[dict[str, Any]] = []
        self._state: _ItemState | None = None
        self._identity_confirmed = False
        self._paused = False
        self._finished = False
        self._notice: Notice | None = None
        self._seq = 0
        self.resumed = False
        self.created_utc = ""
        last: datetime | None = None
        try:
            stored = self.store.load_autosave()
        except OSError as exc:
            raise StudyError(f"The autosave {self.autosave_name} cannot be read: {exc}") from exc
        if stored is not None:
            last = self._restore(*stored)
            self.resumed = True
        now = _as_utc(self.clock.utc_now())
        self._anchor_utc = now if last is None else max(now, last)
        self._anchor_mono = self.clock.monotonic()
        self._last = self._anchor_utc if last is None else last
        if not self.resumed:
            self.created_utc = self._stamp()
        try:
            validate_annotations(dict(self.document(), items=[]))
        except ValueError as exc:
            raise StudyError(f"This kit cannot give a valid session file: {exc}") from exc

    @property
    def output_dir(self) -> Path | None:
        """Folder of the session files, for a :class:`FileSessionStore`; otherwise ``None``."""
        return self.store.output_dir if isinstance(self.store, FileSessionStore) else None

    @property
    def output_path(self) -> Path | None:
        """Path of the finished session file, for a :class:`FileSessionStore`; otherwise ``None``."""
        return self.store.output_path if isinstance(self.store, FileSessionStore) else None

    @property
    def autosave_path(self) -> Path | None:
        """Path of the autosave, for a :class:`FileSessionStore`; otherwise ``None``."""
        return self.store.autosave_path if isinstance(self.store, FileSessionStore) else None

    @property
    def seq(self) -> int:
        """Sequence number of the last autosave this session stored or resumed from."""
        return self._seq

    @property
    def identity(self) -> tuple[str, str, str]:
        """``(study_id, annotator_id, session_id)`` from the manifest."""
        m = self.kit.manifest
        return str(m["study_id"]), str(m["annotator_id"]), str(m["session_id"])

    @property
    def finished(self) -> bool:
        """Whether the session file has been written."""
        return self._finished

    @property
    def records(self) -> list[dict[str, Any]]:
        """Copies of the finished item records (format v1 items), in session order."""
        return copy.deepcopy(self._records)

    @property
    def current_item(self) -> dict[str, Any] | None:
        """The manifest entry of the open item, or ``None``."""
        return None if self._state is None else dict(self._state.item)

    @property
    def current_clicks(self) -> list[tuple[float, float]]:
        """Display coordinates of the clicks kept on the open item."""
        return [] if self._state is None else [(a.x, a.y) for a in self._state.clicks]

    def identity_text(self) -> str:
        """Question asked before the session opens."""
        return self._identity_notice().text

    def _identity_notice(self) -> Notice:
        study_id, annotator_id, session_id = self.identity
        params: dict[str, ParamValue] = {
            "study_id": study_id,
            "annotator_id": annotator_id,
            "session_id": session_id,
        }
        if not self.resumed:
            return Notice("identity", params)
        n_items = len(self.kit.manifest["items"])
        params.update(item=min(len(self._records) + 1, n_items), total=n_items)
        return Notice("identity_resumed", params)

    def document(self) -> dict[str, Any]:
        """The session as an annotation document of format v1."""
        m = self.kit.manifest
        return {
            "format": FORMAT_NAME,
            "format_version": FORMAT_VERSION,
            "sarcostat_version": __version__,
            "source_digest": self._digest,
            "created_utc": self.created_utc,
            "study_id": m["study_id"],
            "annotator_id": m["annotator_id"],
            "session_id": m["session_id"],
            "images": copy.deepcopy(m["images"]),
            "items": copy.deepcopy(self._records),
        }

    def view_state(self) -> dict[str, Any]:
        """What the view shows now, as JSON-ready data (keys :data:`VIEW_STATE_KEYS`).

        Numbers are display coordinates (``roi``, ``marks``, ``strokes``),
        counts and indexes (``progress``, code parameters) only. While the
        session is paused, before the identity is confirmed and after the
        end, no item is shown.
        """
        st = self._state
        shown = (
            st is not None and self._identity_confirmed and not self._paused and not self._finished
        )
        item_file: str | None = None
        roi: list[float] | None = None
        title: str | None = None
        marks: list[Mark] = []
        strokes: list[Stroke] = []
        controls = _IDLE
        if st is not None and shown:
            item_file = str(st.item["kit_file"])
            roi = list(st.mapping.roi_to_display(st.item["roi"]))
            title = self._title()
            marks, strokes = self._marks(st)
            controls = self._controls(st)
        step = self._step()
        return {
            "item_file": item_file,
            "roi": roi,
            "title": title,
            "marks": [m.to_json() for m in marks],
            "strokes": [s.to_json() for s in strokes],
            "controls": controls.to_json(),
            "step": None if step is None else step.to_json(),
            "progress": {"done": len(self._records), "total": len(self.kit.manifest["items"])},
            "notice": None if self._notice is None else self._notice.to_json(),
            "paused": self._paused,
            "finished": {"file_name": self.file_name} if self._finished else None,
        }

    def _step(self) -> Notice | None:
        if self._finished:
            return Notice("session_complete", {"file_name": self.file_name})
        if not self._identity_confirmed:
            return self._identity_notice()
        if self._paused:
            return Notice("paused")
        if self._state is None:
            return None
        return self._prompt(self._state)

    @_action
    def confirm_identity(self) -> Notice | None:
        """The annotator confirmed the IDs: show the first open item."""
        if self._finished:
            return Notice("session_finished")
        if self._identity_confirmed:
            return Notice("session_already_open")
        self._identity_confirmed = True
        if self._state is None:
            if len(self._records) == len(self.kit.manifest["items"]):
                return self._finish()
            self._state = self._new_state(len(self._records))
        else:
            self._state.run_start = self.clock.monotonic()
        self._save()
        self._show_current()
        return None

    @_action
    def pause(self) -> Notice | None:
        """Stop the item clock and hide the item."""
        problem = self._guard()
        if problem:
            return problem
        st = self._require_state()
        st.active_before = self._active()
        st.run_start = None
        self._paused = True
        self._save()
        self.view.show_paused()
        self.view.show_controls(_IDLE)
        self.view.show_status(Notice("paused").text)
        return None

    @_action
    def resume(self) -> Notice | None:
        """Show the item again and restart its clock."""
        if not self._paused:
            return Notice("session_not_paused")
        self._paused = False
        self._require_state().run_start = self.clock.monotonic()
        self._show_current()
        return None

    def save(self) -> None:
        """Write the autosave now (for example when the window closes)."""
        if self._identity_confirmed and not self._finished:
            self._save()

    @_action
    def add_click(self, x: float, y: float) -> Notice | None:
        """Add a click at display coordinates ``(x, y)`` to the open item."""
        problem = self._guard()
        if problem:
            return problem
        st = self._require_state()
        if not (math.isfinite(x) and math.isfinite(y)):
            return Notice("click_outside_image")
        xq, yq = quantize_coordinate(x), quantize_coordinate(y)
        w, h = st.mapping.display_size
        if not (-0.5 <= xq <= w - 0.5 and -0.5 <= yq <= h - 0.5):
            return Notice("click_outside_image")
        if st.kind == "shape" and len(st.clicks) >= len(st.landmarks):
            return Notice("shape_points_placed")
        st.actions.append(_Action("click", xq, yq, round(self._active(), 3), st.undo_count))
        self._save()
        self._refresh()
        return None

    @_action
    def undo(self) -> Notice | None:
        """Undo the last step of the open item (confirmed items cannot be changed)."""
        problem = self._guard()
        if problem:
            return problem
        st = self._require_state()
        if not st.actions:
            self._notice = Notice("nothing_to_undo")
            self.view.show_status(self._notice.text)
            return None
        st.actions.pop()
        st.undo_count += 1
        self._save()
        self._refresh()
        return None

    @_action
    def skip_m_line(self) -> Notice | None:
        """Mark the M-line of the open sarcomere as not identifiable (both Z-lines stay)."""
        problem = self._guard()
        if problem:
            return problem
        st = self._require_state()
        if st.kind != "shape":
            return Notice("m_line_skip_not_shape")
        if st.m_skipped:
            return Notice("m_line_already_skipped")
        if len(st.clicks) > 2:
            return Notice("m_line_started")
        st.actions.append(_Action("m_skip"))
        self._save()
        self._refresh()
        return None

    @_action
    def next_myofibril(self) -> Notice | None:
        """Close myofibril 1 of a pair item and start tracing myofibril 2."""
        problem = self._guard()
        if problem:
            return problem
        st = self._require_state()
        if st.kind != "pair":
            return Notice("not_a_pair")
        if st.split is not None:
            return Notice("myofibril_2_open")
        problem = _zline_problem(st.clicks, "myofibril_1")
        if problem:
            return problem
        st.actions.append(_Action("next_myofibril"))
        self._save()
        self._refresh()
        return None

    @_action
    def confirm_item(self, branch_end: bool = False) -> Notice | None:
        """Save the open item and show the next one.

        Parameters
        ----------
        branch_end : bool
            For a chain item: the chain stops before a branch point.
        """
        problem = self._guard()
        if problem:
            return problem
        st = self._require_state()
        clicks = st.clicks
        if st.kind == "shape":
            n = len(st.landmarks)
            if len(clicks) != n:
                return Notice("shape_points_missing", {"expected": n, "placed": len(clicks)})
            for k in range(0, n, 2):
                a, b = clicks[k], clicks[k + 1]
                if math.hypot(b.x - a.x, b.y - a.y) < _MIN_LINE_PX:
                    return Notice(
                        "shape_line_too_short", {"line": _SHAPE_STROKES[st.landmarks[k + 1]]}
                    )
        elif st.kind == "chain":
            problem = _zline_problem(clicks, "chain")
        else:
            split = st.split
            if split is None:
                return Notice("pair_needs_next_myofibril")
            problem = _zline_problem(clicks[:split], "myofibril_1") or _zline_problem(
                clicks[split:], "myofibril_2"
            )
        if problem:
            return problem
        return self._advance(self._record(skipped=False, reason=None, branch_end=branch_end))

    @_action
    def skip_item(self, reason: str) -> Notice | None:
        """Skip the open item; ``reason`` is required."""
        problem = self._guard()
        if problem:
            return problem
        text = reason.strip() if isinstance(reason, str) else ""
        if not text:
            return Notice("skip_needs_reason")
        return self._advance(self._record(skipped=True, reason=text, branch_end=False))

    def _guard(self) -> Notice | None:
        if self._finished:
            return Notice("session_finished")
        if not self._identity_confirmed:
            return Notice("identity_not_confirmed")
        if self._paused:
            return Notice("session_paused")
        if self._state is None:
            return Notice("no_open_item")
        return None

    def _require_state(self) -> _ItemState:
        if self._state is None:
            raise RuntimeError("no open item")
        return self._state

    def _stamp(self) -> str:
        moment = self._anchor_utc + timedelta(seconds=self.clock.monotonic() - self._anchor_mono)
        if moment < self._last:
            moment = self._last
        self._last = moment
        return moment.strftime(_UTC_FORMAT)

    def _active(self) -> float:
        st = self._require_state()
        if st.run_start is None:
            return st.active_before
        return st.active_before + max(0.0, self.clock.monotonic() - st.run_start)

    def _new_state(self, index: int) -> _ItemState:
        item = self.kit.manifest["items"][index]
        return _ItemState(
            item=item,
            mapping=DisplayMapping.from_item(item),
            started_utc=self._stamp(),
            run_start=self.clock.monotonic(),
        )

    def _title(self) -> str:
        return Notice(
            "item_title",
            {"index": len(self._records) + 1, "total": len(self.kit.manifest["items"])},
        ).text

    def _show_current(self) -> None:
        st = self._require_state()
        _check_item_bytes(self.kit, st.item)
        roi = st.mapping.roi_to_display(st.item["roi"])
        self.view.show_item(str(st.item["kit_file"]), roi, self._title())
        self._refresh()

    def _refresh(self) -> None:
        st = self._require_state()
        marks, strokes = self._marks(st)
        self.view.show_marks(marks, strokes)
        self.view.show_controls(self._controls(st))
        self.view.show_status(self._prompt(st).text)

    @staticmethod
    def _marks(st: _ItemState) -> tuple[list[Mark], list[Stroke]]:
        clicks = st.clicks
        marks: list[Mark] = []
        strokes: list[Stroke] = []
        if st.kind == "shape":
            for a, landmark in zip(clicks, st.landmarks, strict=False):
                marks.append(Mark(a.x, a.y, _LANDMARK_LETTERS[landmark]))
            for k in range(1, len(marks), 2):
                strokes.append(
                    Stroke(
                        (marks[k - 1].x, marks[k - 1].y),
                        (marks[k].x, marks[k].y),
                        _SHAPE_STROKES[st.landmarks[k]],
                    )
                )
            return marks, strokes
        split = st.split if st.kind == "pair" else None
        for k, a in enumerate(clicks):
            if st.kind == "chain":
                role = "chain"
            else:
                role = "myofibril_1" if split is None or k < split else "myofibril_2"
            marks.append(Mark(a.x, a.y, role))
            if k % 2:
                strokes.append(Stroke((clicks[k - 1].x, clicks[k - 1].y), (a.x, a.y), role))
        return marks, strokes

    @staticmethod
    def _controls(st: _ItemState) -> StudyControls:
        clicks = st.clicks
        if st.kind == "shape":
            ready = len(clicks) == len(st.landmarks)
        elif st.kind == "chain":
            ready = len(clicks) >= 4 and len(clicks) % 2 == 0
        else:
            split = st.split
            ready = split is not None and len(clicks) - split >= 4 and len(clicks) % 2 == 0
        return StudyControls(
            can_confirm=ready,
            can_undo=bool(st.actions),
            can_skip_m_line=st.kind == "shape" and not st.m_skipped and len(clicks) <= 2,
            can_next_myofibril=st.kind == "pair" and st.split is None,
            shows_branch_end=st.kind == "chain",
            item_type=st.kind,
        )

    @staticmethod
    def _prompt(st: _ItemState) -> Notice:
        clicks = st.clicks
        if st.kind == "shape":
            seq = st.landmarks
            n = len(clicks)
            if n >= len(seq):
                return Notice("shape_complete")
            params: dict[str, ParamValue] = {
                "landmark": _LANDMARK_LETTERS[seq[n]],
                "index": n + 1,
                "total": len(seq),
            }
            if n <= 2 and not st.m_skipped:
                return Notice("shape_click_or_skip_m_line", params)
            return Notice("shape_click", params)
        if st.kind == "chain":
            done = len(clicks) // 2
            if len(clicks) % 2:
                return Notice("chain_second_end", {"zline": done + 1})
            return Notice("chain_first_end", {"zline": done + 1, "done": done})
        split = st.split
        fibril, own = (1, clicks) if split is None else (2, clicks[split:])
        done = len(own) // 2
        end = "second" if len(own) % 2 else "first"
        return Notice(f"pair_myofibril_{fibril}", {"end": end, "zline": done + 1, "done": done})

    def _record(self, *, skipped: bool, reason: str | None, branch_end: bool) -> dict[str, Any]:
        """The open item as a format v1 item (the one place that maps fields)."""
        st = self._require_state()
        item = st.item
        clicks = st.clicks
        finished_utc = self._stamp()
        record: dict[str, Any] = {
            "item_id": item["item_id"],
            "target_id": item["target_id"],
            "image_id": item["image_id"],
            "type": st.kind,
            "display_transform": item["display_transform"],
            "kit_crop_offset": list(item["kit_crop_offset"]),
            "kit_crop_size": list(item["kit_crop_size"]),
            "raw_clicks": [
                {
                    "x": a.x,
                    "y": a.y,
                    "order": k + 1,
                    "seconds": a.seconds,
                    "undo_count": a.undo_count,
                }
                for k, a in enumerate(clicks)
            ],
            "points": {},
            "started_utc": st.started_utc,
            "finished_utc": finished_utc,
            "active_seconds": round(self._active(), 3),
            "skipped": skipped,
            "skip_reason": reason,
            "skipped_parts": [],
        }
        if not skipped:
            original: list[list[float]] = st.mapping.to_original(
                [[a.x, a.y] for a in clicks]
            ).tolist()
            lines = [[original[k], original[k + 1]] for k in range(0, len(original), 2)]
            if st.kind == "shape":
                if st.m_skipped:
                    record["points"] = {"z1": lines[0], "z2": lines[1]}
                    record["skip_reason"] = M_LINE_SKIP_REASON
                    record["skipped_parts"] = ["m_line"]
                else:
                    record["points"] = {"z1": lines[0], "m": lines[1], "z2": lines[2]}
            elif st.kind == "chain":
                record["points"] = {"z_lines": lines}
            else:
                half = (st.split or 0) // 2
                record["points"] = {"myofibrils": [lines[:half], lines[half:]]}
        if st.kind == "chain":
            record["branch_end"] = bool(branch_end) and not skipped
        return record

    def _advance(self, record: dict[str, Any]) -> Notice | None:
        self._records.append(record)
        index = len(self._records)
        if index < len(self.kit.manifest["items"]):
            self._state = self._new_state(index)
            self._save()
            self._show_current()
            return None
        self._state = None
        self._save()
        return self._finish()

    def _finish(self) -> Notice | None:
        try:
            self.store.put_finished(self.file_name, _session_bytes(self.document()))
        except (StudyError, ValueError, OSError) as exc:
            return Notice("finish_failed", {"error": str(exc), "autosave": self.autosave_name})
        self._finished = True
        self.store.discard_autosave()
        self.view.show_controls(_IDLE)
        self.view.show_finished(self.file_name)
        self.view.show_status(Notice("session_complete", {"file_name": self.file_name}).text)
        return None

    def _save(self) -> None:
        st = self._state
        current = None
        if st is not None:
            current = {
                "item_id": st.item["item_id"],
                "started_utc": st.started_utc,
                "active_seconds": self._active(),
                "undo_count": st.undo_count,
                "actions": [a.to_json() for a in st.actions],
            }
        study_id, annotator_id, session_id = self.identity
        seq = self._seq + 1
        doc = {
            "format": _AUTOSAVE_FORMAT,
            "format_version": _AUTOSAVE_VERSION,
            "manifest_sha256": self.kit.manifest_sha256,
            "study_id": study_id,
            "annotator_id": annotator_id,
            "session_id": session_id,
            "created_utc": self.created_utc,
            "last_utc": self._last.strftime(_UTC_FORMAT),
            "records": self._records,
            "current": current,
            "seq": seq,
        }
        self.store.put_autosave(seq, dumps_canonical(doc))
        self._seq = seq

    def _restore(self, seq: int, data: bytes) -> datetime:
        """Load the autosave ``seq``; return the last timestamp it recorded."""
        name = self.autosave_name
        try:
            saved = loads_strict(data)
        except StudyError as exc:
            raise StudyError(f"The autosave {name} cannot be read: {exc}") from exc
        try:
            if saved["format"] != _AUTOSAVE_FORMAT:
                raise ValueError("not a study autosave")
            if saved["manifest_sha256"] != self.kit.manifest_sha256:
                raise StudyError(
                    f"The autosave {name} belongs to another kit (manifest SHA-256 differs); "
                    "tell the coordinator."
                )
            if (saved["study_id"], saved["annotator_id"], saved["session_id"]) != self.identity:
                raise ValueError("the IDs differ from the manifest")
            saved_seq = saved.get("seq", 0)
            if isinstance(saved_seq, bool) or saved_seq != seq:
                raise ValueError(f"its seq {saved_seq!r} differs from the stored seq {seq}")
            items = self.kit.manifest["items"]
            records = list(saved["records"])
            if [r["item_id"] for r in records] != [i["item_id"] for i in items[: len(records)]]:
                raise ValueError("the saved items do not follow the manifest")
            current = saved["current"]
            if current is not None:
                index = len(records)
                if index >= len(items) or current["item_id"] != items[index]["item_id"]:
                    raise ValueError("the open item does not follow the manifest")
                item = items[index]
                self._state = _ItemState(
                    item=item,
                    mapping=DisplayMapping.from_item(item),
                    started_utc=str(current["started_utc"]),
                    active_before=float(current["active_seconds"]),
                    undo_count=int(current["undo_count"]),
                    actions=[_Action.from_json(a) for a in current["actions"]],
                )
            self._records = records
            self.created_utc = str(saved["created_utc"])
            self._seq = seq
            return _parse_utc(str(saved["last_utc"]))
        except StudyError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise StudyError(f"The autosave {name} is not valid: {exc}") from exc


__all__ = [
    "M_LINE_SKIP_REASON",
    "NOTICES",
    "PARAM_WORDS",
    "VIEW_STATE_KEYS",
    "Clock",
    "ExplicitClock",
    "FileSessionStore",
    "Mark",
    "MemorySessionStore",
    "Notice",
    "SessionStore",
    "Stroke",
    "StudyControls",
    "StudyPresenter",
    "StudyView",
    "SystemClock",
    "notice_text",
]
