"""Synthetic session kits, a stdlib PNG writer, clocks and views for the study-engine tests.

The kit builder, the PNG writer, the fake clock and the click helpers are
copies of ``tests/io/kit_factory.py`` (so that ``tests/engine`` runs on its
own, for example in Pyodide); the recording view follows the engine's view
protocol, whose ``show_item`` takes the kit file instead of an image array.
:class:`FixedClock` and :class:`QuietView` are copies of the clock and the
view with which a downstream project drives the study presenter headless, so
the tests keep that call surface working.
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from sarcostat.io.study import (
    MANIFEST_FORMAT,
    MANIFEST_VERSION,
    DisplayMapping,
    crop_and_transform,
    write_manifest,
)

__all__ = [
    "CHAIN_ZLINES",
    "CROP_SIZE",
    "ORIGINAL_SHAPE",
    "PAIR_MYOFIBRILS",
    "SHAPE_POINTS",
    "FakeClock",
    "FixedClock",
    "QuietView",
    "RecordingView",
    "annotate_shape",
    "annotate_zlines",
    "click_original",
    "kit_files",
    "make_kit",
    "original_image",
    "png_bytes",
    "png_chunk",
]

ORIGINAL_SHAPE = (240, 360)
CROP_SIZE = (120, 90)

SHAPE_POINTS = [
    (40.0, 50.0),
    (40.0, 90.0),
    (60.5, 52.25),
    (60.5, 88.0),
    (80.0, 50.0),
    (80.75, 90.0),
]
CHAIN_ZLINES = [
    ((30.0, 45.0), (31.0, 95.0)),
    ((50.0, 46.0), (50.5, 96.0)),
    ((70.0, 47.0), (71.0, 97.0)),
]
PAIR_MYOFIBRILS = [
    [((30.0, 40.0), (30.0, 60.0)), ((50.0, 40.0), (50.0, 60.0))],
    [((32.0, 70.0), (32.0, 95.0)), ((52.0, 70.0), (52.0, 95.0)), ((72.0, 70.0), (72.0, 95.0))],
]


def original_image() -> np.ndarray:
    """Deterministic 8-bit test image with structure in both axes."""
    rng = np.random.default_rng(42)
    return rng.integers(0, 256, size=ORIGINAL_SHAPE, dtype=np.uint8)


def png_chunk(kind: bytes, body: bytes) -> bytes:
    """One PNG chunk: length, type, data and CRC."""
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def png_bytes(
    pixels: np.ndarray, *, depth16: bool = False, extra_chunks: Iterable[tuple[bytes, bytes]] = ()
) -> bytes:
    """Encode a 2-D array as a greyscale PNG with the standard library (filter 0, zlib level 6).

    ``depth16`` writes 16-bit samples; ``extra_chunks`` go between ``IHDR`` and the image data.
    """
    array = np.asarray(pixels)
    samples = array.astype(">u2" if depth16 else np.uint8)
    height, width = array.shape[:2]
    raw = b"".join(b"\x00" + row.tobytes() for row in samples)
    header = struct.pack(">IIBBBBB", width, height, 16 if depth16 else 8, 0, 0, 0, 0)
    chunks = [png_chunk(b"IHDR", header)]
    chunks += [png_chunk(kind, body) for kind, body in extra_chunks]
    chunks.append(png_chunk(b"IDAT", zlib.compress(raw, 6)))
    chunks.append(png_chunk(b"IEND", b""))
    return b"\x89PNG\r\n\x1a\n" + b"".join(chunks)


def kit_files(kit: Path) -> dict[str, bytes]:
    """Every file under ``kit`` as kit-relative POSIX path to bytes, as a browser would read them."""
    return {p.relative_to(kit).as_posix(): p.read_bytes() for p in kit.rglob("*") if p.is_file()}


def make_kit(
    root: Path,
    specs: list[tuple[str, str]],
    *,
    annotator_id: str = "ANN1",
    session_id: str = "S1",
    decoys: bool = True,
    encode: Any = None,
) -> Path:
    """Write a session kit with one item per ``(type, display_transform)`` in ``specs``.

    With ``decoys``, files the session must never read are placed next to the kit.
    ``encode`` replaces :func:`png_bytes` for the item images (to build a bad kit).
    """
    encoder = encode or png_bytes
    original = original_image()
    kit = root / "kit"
    (kit / "items").mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    for k, (item_type, name) in enumerate(specs):
        offset = [10 + 3 * k, 20 + 2 * k]
        data = encoder(crop_and_transform(original, name, tuple(offset), CROP_SIZE))
        rel = f"items/{k + 1:03d}.png"
        (kit / rel).write_bytes(data)
        items.append(
            {
                "item_id": f"{session_id}-{k + 1:03d}",
                "target_id": f"T{k % 3 + 1:02d}",
                "image_id": "img1",
                "type": item_type,
                "kit_file": rel,
                "kit_sha256": hashlib.sha256(data).hexdigest(),
                "display_transform": name,
                "kit_crop_offset": offset,
                "kit_crop_size": list(CROP_SIZE),
                "roi": [offset[0] + 15, offset[1] + 10, offset[0] + 100, offset[1] + 80],
            }
        )
    manifest = {
        "format": MANIFEST_FORMAT,
        "format_version": MANIFEST_VERSION,
        "study_id": "demo",
        "annotator_id": annotator_id,
        "session_id": session_id,
        "images": [
            {
                "image_id": "img1",
                "file_name": "synthetic.png",
                "sha256": hashlib.sha256(original.tobytes()).hexdigest(),
                "width_px": ORIGINAL_SHAPE[1],
                "height_px": ORIGINAL_SHAPE[0],
                "scale": {"nm_per_px": None, "source": None},
                "study": {"animal": None, "group": None},
            }
        ],
        "items": items,
    }
    write_manifest(manifest, kit)
    if decoys:
        (kit / "targets.json").write_text('{"secret": true}', encoding="utf-8")
        (kit / "items" / "unlisted.png").write_bytes(png_bytes(original[:10, :10]))
    return kit


class FakeClock:
    """Monotonic and wall clocks under test control (the wall clock may jump)."""

    def __init__(self) -> None:
        self.mono = 5000.0
        self.utc = datetime(2026, 11, 16, 9, 0, 0, tzinfo=timezone.utc)

    def monotonic(self) -> float:
        return self.mono

    def utc_now(self) -> datetime:
        return self.utc

    def advance(self, seconds: float) -> None:
        self.mono += seconds
        self.utc += timedelta(seconds=seconds)


class FixedClock:
    """Deterministic clock: every call advances the time by ``step`` seconds."""

    def __init__(self, start: datetime, step: float = 0.5) -> None:
        self._t = 0.0
        self._start = start
        self._step = step

    def monotonic(self) -> float:
        self._t += self._step
        return self._t

    def utc_now(self) -> datetime:
        return self._start + timedelta(seconds=self._t)


class QuietView:
    """The study view, silenced (the session runs headless)."""

    def __getattr__(self, name: str) -> Any:
        return lambda *args, **kwargs: None


class RecordingView:
    """Records every call the study presenter makes on its view."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def _record(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))

    def show_status(self, message: str) -> None:
        self._record("show_status", message)

    def show_item(self, kit_file: str, roi, title: str) -> None:
        self._record("show_item", kit_file, roi, title)

    def show_marks(self, marks, strokes) -> None:
        self._record("show_marks", list(marks), list(strokes))

    def show_controls(self, controls) -> None:
        self._record("show_controls", controls)

    def show_paused(self) -> None:
        self._record("show_paused")

    def show_finished(self, file_name: str) -> None:
        self._record("show_finished", file_name)

    def last(self, name: str) -> tuple[Any, ...]:
        for call, args in reversed(self.calls):
            if call == name:
                return args
        raise AssertionError(f"{name} was never called")

    def count(self, name: str) -> int:
        return sum(1 for call, _ in self.calls if call == name)

    @property
    def statuses(self) -> list[str]:
        return [args[0] for call, args in self.calls if call == "show_status"]


def click_original(presenter, x: float, y: float) -> str | None:
    """Click the display position of original pixel ``(x, y)`` on the current item."""
    mapping = DisplayMapping.from_item(presenter.current_item)
    dx, dy = mapping.to_display([[x, y]])[0]
    return presenter.add_click(float(dx), float(dy))


def annotate_shape(presenter, clock: FakeClock | None = None, points=SHAPE_POINTS) -> None:
    for x, y in points:
        assert click_original(presenter, x, y) is None
        if clock is not None:
            clock.advance(1.5)


def annotate_zlines(presenter, zlines, clock: FakeClock | None = None) -> None:
    for p1, p2 in zlines:
        assert click_original(presenter, *p1) is None
        assert click_original(presenter, *p2) is None
        if clock is not None:
            clock.advance(2.0)
