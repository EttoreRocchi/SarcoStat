"""Bridge handlers of the study commands: ``study.*``.

The page reads a session kit and sends its files as buffers:

- ``study.open_kit`` checks ``manifest.json`` against ``manifest.sha256``
  and validates it (:func:`~sarcostat.io.study.load_session_kit_from_bytes`);
- ``study.verify_items`` checks the bytes of every item file listed in the
  manifest (SHA-256 and :func:`~sarcostat.io.study.check_item_image`) and
  keeps them for the session; problems are code objects (:data:`PROBLEMS`);
- ``study.start`` opens the :class:`~sarcostat.engine.study.StudyPresenter`
  on a :class:`~sarcostat.engine.study.MemorySessionStore` (with the stored
  autosave and its ``seq`` when resuming) and an
  :class:`~sarcostat.engine.clock.ExplicitClock`;
- each action sets the clock to the times the page captured at the input
  event, then calls the presenter.

Every study command answers ``{view, autosave, seq, finished}``: the
presenter's :meth:`~sarcostat.engine.study.StudyPresenter.view_state`, the
autosave bytes when the command stored a new autosave (otherwise null), the
store's ``seq``, and, once the session is finished, the finished file with
its SHA-256. A refused action is an ordinary answer whose ``view.notice``
holds the code.

This module imports nothing from :mod:`sarcostat.core`,
:mod:`sarcostat.analysis` or :mod:`sarcostat.export`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sarcostat.engine.bridge import BadRequest, Refused
from sarcostat.engine.clock import ExplicitClock
from sarcostat.engine.study import MemorySessionStore, StudyPresenter
from sarcostat.io.study import (
    MANIFEST_FILE,
    MANIFEST_SHA256_FILE,
    SessionKit,
    StudyError,
    check_item_image,
    load_session_kit_from_bytes,
    sha256_bytes,
)

if TYPE_CHECKING:
    from sarcostat.engine.bridge import Bridge, Handler

STATE_KEY = "study"
PROBLEMS = {
    "item_missing": "{file} is listed in the manifest but was not read.",
    "item_sha256_mismatch": "{file} does not match its SHA-256 in the manifest.",
    "item_image_invalid": "{file} is not a valid kit image: {detail}",
    "file_not_listed": "{file} is not listed in the manifest.",
}
"""English sentence of each problem code of ``study.verify_items``."""


class _SilentView:
    """The presenter's view: the page draws from ``view_state()``, so nothing is pushed."""

    def show_status(self, message: str) -> None:
        """Ignore."""

    def show_item(self, kit_file: str, roi: tuple[float, float, float, float], title: str) -> None:
        """Ignore."""

    def show_marks(self, marks: Any, strokes: Any) -> None:
        """Ignore."""

    def show_controls(self, controls: Any) -> None:
        """Ignore."""

    def show_paused(self) -> None:
        """Ignore."""

    def show_finished(self, file_name: str) -> None:
        """Ignore."""


@dataclass
class StudyRun:
    """The study state between commands: the kit, its verified files and the session."""

    kit: SessionKit
    files: dict[str, bytes]
    verified: bool = False
    presenter: StudyPresenter | None = None
    store: MemorySessionStore | None = None
    clock: ExplicitClock | None = None
    problems: list[dict[str, Any]] = field(default_factory=list)


def _run(bridge: Bridge) -> StudyRun:
    run = bridge.state.get(STATE_KEY)
    if not isinstance(run, StudyRun):
        raise Refused("Open a session kit first.")
    return run


def _problem(code: str, **params: str) -> dict[str, Any]:
    return {"code": code, "params": dict(params)}


def _clock_times(args: Mapping[str, Any]) -> tuple[float, float]:
    t = args["t"]
    return float(t["mono_s"]), float(t["wall_ms"])


def _result(run: StudyRun, seq_before: int) -> dict[str, Any]:
    presenter, store = run.presenter, run.store
    assert presenter is not None and store is not None
    autosave = store.autosave if store.seq != seq_before else None
    finished = None
    data = store.finished_bytes
    if data is not None:
        finished = {
            "file_name": store.finished_file_name,
            "bytes": data,
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    return {
        "view": presenter.view_state(),
        "autosave": autosave,
        "seq": store.seq,
        "finished": finished,
    }


def open_kit(bridge: Bridge, args: dict[str, Any]) -> dict[str, Any]:
    """``study.open_kit``: check and read the manifest; forget any earlier kit and session."""
    files = {
        MANIFEST_FILE: bytes(args["manifest"]),
        MANIFEST_SHA256_FILE: bytes(args["manifest_sha256"]),
    }
    bridge.state.pop(STATE_KEY, None)
    try:
        kit = load_session_kit_from_bytes(files)
    except StudyError as exc:
        raise Refused(str(exc)) from None
    bridge.state[STATE_KEY] = StudyRun(kit=kit, files=files)
    manifest = kit.manifest
    return {
        "study_id": str(manifest["study_id"]),
        "annotator_id": str(manifest["annotator_id"]),
        "session_id": str(manifest["session_id"]),
        "n_items": len(manifest["items"]),
        "files": [str(item["kit_file"]) for item in manifest["items"]],
    }


def verify_items(bridge: Bridge, args: dict[str, Any]) -> dict[str, Any]:
    """``study.verify_items``: check every listed item file; keep them when all pass."""
    run = _run(bridge)
    if run.presenter is not None:
        raise Refused("The session is already open; open the kit again to start over.")
    sent = {str(path): bytes(data) for path, data in args["files"].items()}
    listed = {str(item["kit_file"]) for item in run.kit.manifest["items"]}
    problems = [_problem("file_not_listed", file=path) for path in sorted(set(sent) - listed)]
    checked: set[str] = set()
    for item in run.kit.manifest["items"]:
        path = str(item["kit_file"])
        if path in checked:
            continue
        checked.add(path)
        data = sent.get(path)
        if data is None:
            problems.append(_problem("item_missing", file=path))
        elif sha256_bytes(data) != item["kit_sha256"]:
            problems.append(_problem("item_sha256_mismatch", file=path))
        else:
            try:
                check_item_image(item, data)
            except StudyError as exc:
                problems.append(_problem("item_image_invalid", file=path, detail=str(exc)))
    run.problems = problems
    run.verified = not problems
    if run.verified:
        items = {path: sent[path] for path in listed}
        run.kit = load_session_kit_from_bytes({**run.files, **items})
    return {"ok": run.verified, "problems": problems}


def start(bridge: Bridge, args: dict[str, Any]) -> dict[str, Any]:
    """``study.start``: open the session, new or resumed from the page's autosave."""
    run = _run(bridge)
    if not run.verified:
        raise Refused("Verify the item files of the kit first.")
    if run.presenter is not None:
        raise Refused("The session is already open.")
    autosave, seq = args["autosave"], args["seq"]
    try:
        clock = ExplicitClock(*_clock_times(args))
    except ValueError as exc:
        raise BadRequest(f"study.start: args.t: {exc}") from None
    try:
        store = MemorySessionStore(None if autosave is None else bytes(autosave), seq)
        presenter = StudyPresenter(run.kit, store, _SilentView(), clock=clock)
    except StudyError as exc:
        raise Refused(str(exc)) from None
    run.store, run.clock, run.presenter = store, clock, presenter
    return _result(run, 0 if seq is None else int(seq))


def _action(call: Callable[[StudyPresenter, dict[str, Any]], Any]) -> Handler:
    def handle(bridge: Bridge, args: dict[str, Any]) -> dict[str, Any]:
        run = _run(bridge)
        if run.presenter is None or run.store is None or run.clock is None:
            raise Refused("Start the session first.")
        try:
            run.clock.set(*_clock_times(args))
        except ValueError as exc:
            raise BadRequest(f"args.t: {exc}") from None
        seq_before = run.store.seq
        try:
            call(run.presenter, args)
        except StudyError as exc:
            raise Refused(str(exc)) from None
        return _result(run, seq_before)

    return handle


def handlers() -> dict[str, Handler]:
    """The handlers of the ``study`` family, by command."""
    return {
        "study.open_kit": open_kit,
        "study.verify_items": verify_items,
        "study.start": start,
        "study.confirm_identity": _action(lambda p, a: p.confirm_identity()),
        "study.click": _action(lambda p, a: p.add_click(a["x"], a["y"])),
        "study.undo": _action(lambda p, a: p.undo()),
        "study.skip_m_line": _action(lambda p, a: p.skip_m_line()),
        "study.next_myofibril": _action(lambda p, a: p.next_myofibril()),
        "study.confirm_item": _action(lambda p, a: p.confirm_item(branch_end=a["branch_end"])),
        "study.skip_item": _action(lambda p, a: p.skip_item(a["reason"])),
        "study.pause": _action(lambda p, a: p.pause()),
        "study.resume": _action(lambda p, a: p.resume()),
    }


__all__ = ["PROBLEMS", "STATE_KEY", "StudyRun", "handlers"]
