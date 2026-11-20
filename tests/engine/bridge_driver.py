"""Drive a study session through the bridge, as the web page does.

The commands and their order are those of
:func:`tests.engine.scripted_session.annotate_open_item`, so that the same
clock readings give the same session file as the scripted session run
directly on the presenter. Only the bridge, the study engine and
:mod:`sarcostat.io.study` are imported: no metric code.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from sarcostat.engine.bridge import Bridge
from sarcostat.engine.clock import ExplicitClock
from sarcostat.io.study import (
    MANIFEST_FILE,
    MANIFEST_SHA256_FILE,
    DisplayMapping,
    quantize_coordinate,
)

from .scripted_session import CHAIN, M_SKIP_ITEM, PAIR, SHAPE, SKIP_REASON, SKIPPED_ITEM

START_MONO_S = 100.0
START_WALL_MS = 1_795_085_291_000.0
STEP_S = 0.75


class Times:
    """The clock readings of successive input events: ``STEP_S`` seconds apart."""

    def __init__(self, mono_s: float = START_MONO_S, wall_ms: float = START_WALL_MS) -> None:
        self.mono_s = mono_s
        self.wall_ms = wall_ms
        self.clock = ExplicitClock(mono_s, wall_ms)

    def now(self) -> dict[str, float]:
        """The current reading as the ``t`` argument of a study command."""
        return {"mono_s": self.mono_s, "wall_ms": self.wall_ms}

    def tick(self) -> dict[str, float]:
        """Advance to the next input event; set :attr:`clock` to it and return it as ``t``."""
        self.mono_s += STEP_S
        self.wall_ms += STEP_S * 1000.0
        self.clock.set(self.mono_s, self.wall_ms)
        return self.now()


class Client:
    """JSON requests to one :class:`~sarcostat.engine.bridge.Bridge`, as the worker sends them."""

    def __init__(self, bridge: Bridge | None = None) -> None:
        self.bridge = bridge or Bridge()
        self.count = 0
        self.log: list[tuple[str, dict[str, Any], int]] = []

    def call(
        self, cmd: str, args: dict[str, Any], buffers: list[bytes] | None = None
    ) -> tuple[dict[str, Any], list[bytes]]:
        """Send one request; return the parsed response and the buffers it names."""
        self.count += 1
        request = json.dumps({"v": 1, "id": self.count, "cmd": cmd, "args": args})
        text, out = self.bridge.dispatch(request, buffers or [])
        response: dict[str, Any] = json.loads(text)
        self.log.append((cmd, response, len(out)))
        return response, out

    def ok(
        self, cmd: str, args: dict[str, Any], buffers: list[bytes] | None = None
    ) -> tuple[dict[str, Any], list[bytes]]:
        """Like :meth:`call`, raising unless the response is ``ok``; returns the result."""
        response, out = self.call(cmd, args, buffers)
        if not response["ok"]:
            raise RuntimeError(f"{cmd}: {response['error']}")
        return response["result"], out


def kit_files(kit_dir: str | Path) -> dict[str, bytes]:
    """Every file of a kit folder by kit-relative POSIX path, as the page reads them."""
    root = Path(kit_dir)
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def open_and_verify(client: Client, files: dict[str, bytes]) -> dict[str, Any]:
    """``study.open_kit`` then ``study.verify_items`` with the listed files; the kit summary."""
    summary, _ = client.ok(
        "study.open_kit",
        {"manifest": {"$buf": 0}, "manifest_sha256": {"$buf": 1}},
        [files[MANIFEST_FILE], files[MANIFEST_SHA256_FILE]],
    )
    listed = summary["files"]
    buffers = [files[path] for path in listed]
    checked, _ = client.ok(
        "study.verify_items",
        {"files": {path: {"$buf": k} for k, path in enumerate(listed)}},
        buffers,
    )
    if not checked["ok"]:
        raise RuntimeError(f"verify_items: {checked['problems']}")
    return summary


def _click(item: dict[str, Any], fx: float, fy: float) -> dict[str, float]:
    x_min, y_min, x_max, y_max = item["roi"]
    point = [
        quantize_coordinate(x_min + fx * (x_max - x_min)),
        quantize_coordinate(y_min + fy * (y_max - y_min)),
    ]
    x, y = DisplayMapping.from_item(item).to_display([point])[0]
    return {"x": float(x), "y": float(y)}


def item_commands(position: int, item: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """The commands (without ``t``) that annotate the ``position``-th item, as the scripted session does."""
    if position == SKIPPED_ITEM:
        yield "study.skip_item", {"reason": SKIP_REASON}
        return
    if item["type"] == "shape":
        landmarks = SHAPE
        if position == M_SKIP_ITEM:
            landmarks = SHAPE[:2] + SHAPE[4:]
        for k, (fx, fy) in enumerate(landmarks):
            if position == M_SKIP_ITEM and k == 2:
                yield "study.skip_m_line", {}
            if position == 1 and k == 1:
                yield "study.click", _click(item, 0.9, 0.9)
                yield "study.undo", {}
            yield "study.click", _click(item, fx, fy)
        yield "study.confirm_item", {"branch_end": False}
    elif item["type"] == "chain":
        for p1, p2 in CHAIN:
            yield "study.click", _click(item, *p1)
            yield "study.click", _click(item, *p2)
        yield "study.confirm_item", {"branch_end": False}
    else:
        for fibril, zlines in enumerate(PAIR):
            if fibril:
                yield "study.next_myofibril", {}
            for p1, p2 in zlines:
                yield "study.click", _click(item, *p1)
                yield "study.click", _click(item, *p2)
        yield "study.confirm_item", {"branch_end": False}


def run_study(
    client: Client,
    files: dict[str, bytes],
    times: Times,
    stop_after: int | None = None,
    autosave: bytes | None = None,
    seq: int | None = None,
) -> list[tuple[dict[str, Any], list[bytes]]]:
    """Open, verify, start (or resume) and annotate a kit through the bridge.

    Returns every study result with its buffers, ``study.start`` first. With
    ``stop_after`` the run stops once that many items are done, as after a
    crash; ``autosave`` and ``seq`` resume from a stored autosave.
    """
    manifest = json.loads(files[MANIFEST_FILE])
    open_and_verify(client, files)
    buffers = [] if autosave is None else [autosave]
    results = [
        client.ok(
            "study.start",
            {"autosave": None if autosave is None else {"$buf": 0}, "seq": seq, "t": times.now()},
            buffers,
        )
    ]
    results.append(client.ok("study.confirm_identity", {"t": times.tick()}))
    while results[-1][0]["finished"] is None:
        done = results[-1][0]["view"]["progress"]["done"]
        if stop_after is not None and done >= stop_after:
            break
        item = manifest["items"][done]
        for cmd, args in item_commands(done + 1, item):
            results.append(client.ok(cmd, {**args, "t": times.tick()}))
    return results


def finished_bytes(results: list[tuple[dict[str, Any], list[bytes]]]) -> tuple[str, bytes]:
    """The finished file of a run: its name and bytes (from the last result's buffer)."""
    result, out = results[-1]
    finished = result["finished"]
    if finished is None:
        raise RuntimeError("the session is not finished")
    return str(finished["file_name"]), out[finished["bytes"]["$buf"]]


__all__ = [
    "Client",
    "Times",
    "finished_bytes",
    "item_commands",
    "kit_files",
    "open_and_verify",
    "run_study",
]
