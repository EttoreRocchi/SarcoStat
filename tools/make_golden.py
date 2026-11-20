"""Build and check the golden corpus of the web parity tests (``tests/golden/web``).

For every case of the corpus the folder holds ``<name>.json``, an annotation document of format
v1 with one image (canonical JSON; synthetic landmarks on a repository micrograph of
``examples/`` or on a synthetic image), and ``<name>.expected.json``, its results JSON: the text
of :func:`sarcostat.export.results_json` of :func:`sarcostat.analysis.analyze_document`, computed
by the CPython that runs this script. ``index.json`` lists the cases with a description and the
extra checks they carry. The documents record fixed producer fields (``sarcostat_version``,
``source_digest``, ``created_utc``), so they do not change with the code.

No golden file carries a tolerance. The comparison rule is the USER-governed file
``tolerance.json`` of the same folder: a number ``a`` matches the expected number ``b`` when
``|a - b| <= rel * max(1, |b|)``, with ``rel = rel_special`` for a number whose path is listed in
``special`` and ``rel = rel_default`` otherwise. A path names a value from the root of the
results JSON: object keys joined by ``.``, every list index written ``[*]``, for example
``images[*].chains[*].metrics.mvl_ci[*]``. Keys, list lengths, strings, booleans and nulls must be
equal.

The script refuses a corpus whose results hold an ill-conditioned circular mean: a mean
resultant length below 1e-3, or a mean within 1e-9 of +-pi, for the chain headings
(``circular_mean`` with ``mean_vector_length``), the turning angles (``turning_mean`` with
``turning_mvl``) and the register phases (``mean_phase`` with ``register_index``). Such a mean can
move by far more than the rule, or by 2 pi, between two platforms whose sine and cosine differ by
one unit in the last place.

Usage::

    python tools/make_golden.py [--out DIR] [--images DIR]
    python tools/make_golden.py --check [--out DIR] [--rule FILE]

The first form writes the corpus into ``DIR`` (default ``tests/golden/web``); ``--images`` also
writes the synthetic images there. The second form rebuilds the corpus in memory, compares it
with ``DIR`` under the rule (default ``DIR/tolerance.json``), prints the largest scaled difference
``|a - b| / max(1, |b|)`` of every numeric field and exits 1 when a value breaks the rule.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import struct
import sys
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
try:
    import sarcostat  # noqa: F401
except ModuleNotFoundError:
    sys.path.insert(0, str(ROOT / "src"))

from sarcostat.analysis import analyze_document, estimate_click_error  # noqa: E402
from sarcostat.export import results_json  # noqa: E402
from sarcostat.io.annotations import (  # noqa: E402
    FORMAT_NAME,
    FORMAT_VERSION,
    dumps_annotations,
    validate_annotations,
)

GOLDEN_DIR = ROOT / "tests" / "golden" / "web"
EXAMPLES = ROOT / "examples"
INDEX_NAME = "index.json"
RULE_NAME = "tolerance.json"
INDEX_FORMAT = "sarcostat-golden-index"
PRODUCER_VERSION = "0.2.0"
PRODUCER_DIGEST = hashlib.sha256(b"sarcostat golden corpus, format v1").hexdigest()
CREATED_UTC = "2026-11-20T00:00:00Z"
MIN_RESULTANT = 1e-3
MIN_DISTANCE_FROM_PI = 1e-9
CHECKS = ("estimate_click_error",)
RULE_KEYS = ("rule", "rel_default", "rel_special", "special", "approved")
PATH_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\[\*\])*(\.[A-Za-z_][A-Za-z0-9_]*(\[\*\])*)*")
GRID = 64.0

Point = list[float]


def canonical_json(value: Any) -> str:
    """Canonical JSON text: sorted keys, two-space indentation, ASCII, one trailing newline."""
    text = json.dumps(
        value,
        sort_keys=True,
        indent=2,
        separators=(",", ": "),
        ensure_ascii=True,
        allow_nan=False,
    )
    return text + "\n"


def _snap(value: float) -> float:
    return round(value * GRID) / GRID


def _point(x: float, y: float) -> Point:
    return [_snap(x), _snap(y)]


@dataclass(frozen=True)
class Picture:
    """An image of the corpus: a repository micrograph or a synthetic black PNG."""

    image_id: str
    file_name: str
    width: int
    height: int
    nm_per_px: float | None

    def sha256(self) -> str:
        """SHA-256 of the image file (the synthetic PNG is generated, not read)."""
        if self.file_name.startswith("synthetic_"):
            digest = hashlib.sha256()
            for block in synthetic_png(self.width, self.height):
                digest.update(block)
            return digest.hexdigest()
        return hashlib.sha256((EXAMPLES / self.file_name).read_bytes()).hexdigest()


TEM1 = Picture("tem1", "human_skeletal_muscle_tem_1.jpg", 2200, 1611, 6.62)
TEM3 = Picture("tem3", "human_skeletal_muscle_tem_3.jpg", 2400, 1745, 4.39)
WIDE = Picture("wide", "synthetic_5000x800.png", 5000, 800, None)


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def synthetic_png(width: int, height: int) -> Iterator[bytes]:
    """Bytes of a black 8-bit greyscale PNG, in blocks; stored deflate blocks, so any zlib agrees."""
    raw_size = height * (width + 1)
    block = 65535
    n_blocks = max(1, -(-raw_size // block))
    idat_size = 2 + 5 * n_blocks + raw_size + 4
    yield b"\x89PNG\r\n\x1a\n"
    yield _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
    yield struct.pack(">I", idat_size)
    crc = zlib.crc32(b"IDAT")
    adler = 1
    head = b"IDAT\x78\x01"
    crc = zlib.crc32(b"\x78\x01", crc)
    yield head
    left = raw_size
    while left > 0:
        size = min(block, left)
        left -= size
        header = struct.pack("<BHH", 1 if left == 0 else 0, size, size ^ 0xFFFF)
        data = bytes(size)
        adler = zlib.adler32(data, adler)
        crc = zlib.crc32(data, zlib.crc32(header, crc))
        yield header + data
    tail = struct.pack(">I", adler)
    crc = zlib.crc32(tail, crc)
    yield tail + struct.pack(">I", crc)
    yield _chunk(b"IEND", b"")


def _image_entry(picture: Picture, nm_per_px: float | None, source: str | None) -> dict[str, Any]:
    return {
        "image_id": picture.image_id,
        "file_name": picture.file_name,
        "sha256": picture.sha256(),
        "width_px": picture.width,
        "height_px": picture.height,
        "scale": {"nm_per_px": nm_per_px, "source": None if nm_per_px is None else source},
        "study": {"animal": None, "group": None},
    }


def _item(picture: Picture, item_id: str, kind: str, points: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "item_id": item_id,
        "target_id": None,
        "image_id": picture.image_id,
        "type": kind,
        "display_transform": "identity",
        "kit_crop_offset": [0, 0],
        "kit_crop_size": [picture.width, picture.height],
        "raw_clicks": [],
        "points": points,
        "started_utc": None,
        "finished_utc": None,
        "active_seconds": None,
        "skipped": False,
        "skip_reason": None,
        "skipped_parts": [],
    }
    if kind == "shape" and "m" not in points:
        out["skipped_parts"] = ["m_line"]
        out["skip_reason"] = "M-line not identifiable"
    return out


def sarcomere(
    cx: float,
    cy: float,
    angle_deg: float,
    length: float,
    z_line: float,
    m_line: float,
    *,
    z_line_2: float | None = None,
    m_skipped: bool = False,
    from_z2: bool = False,
) -> dict[str, list[Point]]:
    """Points of one sarcomere: Z-line midpoints ``length`` apart along ``angle_deg``."""
    a = math.radians(angle_deg)
    ux, uy, nx, ny = math.cos(a), math.sin(a), -math.sin(a), math.cos(a)

    def at(s: float, t: float) -> Point:
        return _point(cx + s * ux + t * nx, cy + s * uy + t * ny)

    z2 = z_line if z_line_2 is None else z_line_2
    first = [at(-length / 2, -z_line / 2), at(-length / 2, z_line / 2)]
    second = [at(length / 2, -z2 / 2), at(length / 2, z2 / 2)]
    if from_z2:
        first, second = second[::-1], first[::-1]
    points = {"z1": first, "z2": second}
    if not m_skipped:
        middle = [at(0.0, -m_line / 2), at(0.0, m_line / 2)]
        points["m"] = middle[::-1] if from_z2 else middle
    return points


def z_lines(
    x0: float,
    y0: float,
    heading_deg: float,
    n: int,
    spacing: float,
    length: float,
    *,
    bend_deg: float = 0.0,
    jitter: float = 0.0,
    seed: int = 0,
) -> list[list[Point]]:
    """``n`` Z-lines along a path of ``spacing`` steps, each across the local heading."""
    rng = random.Random(seed)
    x, y, heading = x0, y0, heading_deg
    out = []
    for _ in range(n):
        cx = x + jitter * (2.0 * rng.random() - 1.0)
        cy = y + jitter * (2.0 * rng.random() - 1.0)
        tilt = math.radians(heading + 90.0 + 4.0 * (2.0 * rng.random() - 1.0))
        dx, dy = 0.5 * length * math.cos(tilt), 0.5 * length * math.sin(tilt)
        out.append([_point(cx - dx, cy - dy), _point(cx + dx, cy + dy)])
        heading += bend_deg
        x += spacing * math.cos(math.radians(heading))
        y += spacing * math.sin(math.radians(heading))
    return out


def myofibril_pair(
    x0: float,
    y0: float,
    heading_deg: float,
    n_a: int,
    n_b: int,
    spacing: float,
    length: float,
    lateral: float,
    stagger: float,
    *,
    jitter: float = 0.0,
    seed: int = 0,
) -> dict[str, list[list[list[Point]]]]:
    """Points of a pair item: B runs beside A, ``lateral`` across and ``stagger`` along the axis."""
    a = math.radians(heading_deg)
    bx = x0 + stagger * math.cos(a) - lateral * math.sin(a)
    by = y0 + stagger * math.sin(a) + lateral * math.cos(a)
    first = z_lines(x0, y0, heading_deg, n_a, spacing, length, jitter=jitter, seed=seed)
    second = z_lines(bx, by, heading_deg, n_b, spacing, length, jitter=jitter, seed=seed + 1)
    return {"myofibrils": [first, second]}


def _gui(
    pixel_size: float,
    unit: str,
    *,
    mode: str = "Single Sarcomere",
    direction: float | None = None,
    sigma_z: float | None = None,
    sigma_m: float | None = None,
    perp: float = 1.0,
) -> dict[str, Any]:
    return {
        "pixel_size": pixel_size,
        "unit": unit,
        "expected_direction_deg": direction,
        "click_sigma_z": sigma_z,
        "click_sigma_m": sigma_m,
        "click_perp_ratio": perp,
        "mode": mode,
        "open_myofibril": False,
    }


def document(
    picture: Picture,
    items: list[tuple[str, str, dict[str, Any]]],
    *,
    gui: dict[str, Any] | None = None,
    nm_per_px: float | None = None,
    source: str | None = "scale_bar",
) -> dict[str, Any]:
    """A valid v1 document of one image; with ``gui``, a free save whose scale is the gui scale."""
    if gui is not None:
        nm_per_px = gui["pixel_size"] if gui["unit"] == "nm" else None
        source = "user"
    doc: dict[str, Any] = {
        "format": FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        "sarcostat_version": PRODUCER_VERSION,
        "source_digest": PRODUCER_DIGEST,
        "created_utc": CREATED_UTC,
        "annotator_id": None,
        "session_id": None,
        "study_id": None,
        "images": [_image_entry(picture, nm_per_px, source)],
        "items": [_item(picture, item_id, kind, points) for item_id, kind, points in items],
    }
    if gui is not None:
        doc["gui"] = gui
    validate_annotations(doc)
    return doc


@dataclass(frozen=True)
class Case:
    """One golden: its name, what it covers, its document and its extra checks."""

    name: str
    description: str
    doc: dict[str, Any]
    checks: tuple[str, ...] = field(default=())


def _shape_classes() -> Case:
    items: list[tuple[str, str, dict[str, Any]]] = [
        ("square", "shape", sarcomere(500, 400, 12.0, 300, 300, 300)),
        ("rectangular", "shape", sarcomere(1100, 420, -27.0, 420, 210, 210)),
        ("barrel", "shape", sarcomere(1700, 450, 33.0, 420, 210, 300)),
        ("hourglass", "shape", sarcomere(600, 1100, -8.0, 420, 240, 150)),
        ("m-skipped", "shape", sarcomere(1400, 1200, 71.0, 400, 220, 0, m_skipped=True)),
    ]
    return Case(
        "shape_classes",
        "Four sarcomeres, one per shape class (square, rectangular, barrel, hourglass), and one whose M-line was skipped; scale from the gui block (nm); no click error.",
        document(TEM3, items, gui=_gui(4.39, "nm")),
    )


def _shape_click_set() -> Case:
    items: list[tuple[str, str, dict[str, Any]]] = [
        ("s1", "shape", sarcomere(500, 400, 17.0, 400, 200, 290)),
        ("s2", "shape", sarcomere(1200, 520, -35.0, 380, 230, 150)),
        ("s3", "shape", sarcomere(700, 1100, 52.0, 420, 210, 214)),
        ("s4", "shape", sarcomere(1600, 1100, 5.0, 300, 296, 302)),
    ]
    gui = _gui(6.62, "nm", sigma_z=13.24, sigma_m=19.86, perp=0.75)
    return Case(
        "shape_click_set",
        "Four sarcomeres with a click error set (Z-lines 13.24 nm, M-line 19.86 nm, across/along 0.75): uncertainties and class stability seeded by sarcomere_id.",
        document(TEM1, items, gui=gui),
    )


def _shape_click_estimated() -> Case:
    rng = random.Random(3)
    items: list[tuple[str, str, dict[str, Any]]] = []
    for k in range(5):
        dx, dy = 2.0 * rng.random() - 1.0, 2.0 * rng.random() - 1.0
        da = 0.8 * (2.0 * rng.random() - 1.0)
        dl = 3.0 * (2.0 * rng.random() - 1.0)
        points = sarcomere(
            1200 + dx, 800 + dy, 21.0 + da, 410 + dl, 220 - dl, 300 + dl, from_z2=k % 2 == 1
        )
        items.append((f"repeat-{k + 1}", "shape", points))
    gui = _gui(4.39, "nm")
    doc = document(TEM3, items, gui=gui)
    estimate = estimate_click_error(doc, TEM3.image_id)
    gui.update(
        click_sigma_z=estimate.click_sigma_z,
        click_sigma_m=estimate.click_sigma_m,
        click_perp_ratio=estimate.click_perp_ratio,
    )
    return Case(
        "shape_click_estimated",
        "Five repeats of one sarcomere, two of them started from the other Z-line; the gui block holds the click error estimated from the repeats (estimate_click_error), which the check recomputes.",
        document(TEM3, items, gui=gui),
        ("estimate_click_error",),
    )


def _shape_deleted_then_added() -> Case:
    items: list[tuple[str, str, dict[str, Any]]] = [
        ("sarcomere-1", "shape", sarcomere(400, 400, 10.0, 330, 320, 316)),
        ("sarcomere-3", "shape", sarcomere(1000, 600, -20.0, 400, 240, 160)),
        ("sarcomere-4", "shape", sarcomere(1600, 900, 40.0, 420, 200, 280)),
    ]
    gui = _gui(6.62, "nm", sigma_z=9.93)
    return Case(
        "shape_deleted_then_added",
        "Three sarcomeres annotated, the second deleted, then a fourth added: sarcomere_id 1, 2, 3 and class-stability seeds 1, 2, 3 by position; click error 9.93 nm.",
        document(TEM1, items, gui=gui),
    )


def _shape_scale_unset() -> Case:
    items: list[tuple[str, str, dict[str, Any]]] = [
        ("s1", "shape", sarcomere(600, 500, -14.0, 420, 210, 212)),
        ("s2", "shape", sarcomere(1300, 700, 28.0, 400, 220, 310)),
        ("s3", "shape", sarcomere(1900, 1200, 63.0, 380, 250, 140)),
    ]
    return Case(
        "shape_scale_unset",
        "Three sarcomeres in a study-style file without a gui block and without a scale: lengths in px.",
        document(TEM3, items, nm_per_px=None, source=None),
    )


def _large_px() -> Case:
    items: list[tuple[str, str, dict[str, Any]]] = [
        ("long-sarcomere", "shape", sarcomere(2550, 400, 1.3, 4500, 500, 610)),
        (
            "chain",
            "chain",
            {"z_lines": z_lines(400, 400, 3.5, 5, 1075, 300, bend_deg=-0.6, jitter=4.0, seed=12)},
        ),
        (
            "pair",
            "pair",
            myofibril_pair(4130, 230, 1.7, 5, 4, 180, 220, 300, 47, jitter=3.0, seed=11),
        ),
    ]
    return Case(
        "large_px",
        "Synthetic 5000 x 800 image without a scale: coordinates and lengths above 4096 px (a 4500 px sarcomere, a chain of 5 Z-lines over 4300 px, a pair at x > 4096).",
        document(WIDE, items, nm_per_px=None, source=None),
    )


def _large_nm() -> Case:
    items: list[tuple[str, str, dict[str, Any]]] = [
        ("barrel", "shape", sarcomere(4400, 400, 3.0, 700, 450, 600)),
        ("hourglass", "shape", sarcomere(4600, 420, -4.0, 690, 520, 330)),
        (
            "chain",
            "chain",
            {"z_lines": z_lines(4150, 380, 2.4, 5, 180, 300, bend_deg=0.9, jitter=2.0, seed=4)},
        ),
    ]
    gui = _gui(6.62, "nm", mode="Myofibril Linearity", direction=2.0, sigma_z=13.24)
    return Case(
        "large_nm",
        "Synthetic 5000 x 800 image at 6.62 nm per px: coordinates above 4096 px and lengths above 4096 nm (sarcomeres of about 4600 nm, a chain of 5 Z-lines), click error and reference direction set.",
        document(WIDE, items, gui=gui),
    )


def _chain_5_reference() -> Case:
    lines = z_lines(400, 500, 23.7, 5, 330, 260, bend_deg=1.5, jitter=4.0, seed=5)
    gui = _gui(4.39, "nm", mode="Myofibril Linearity", direction=20.0)
    return Case(
        "chain_5_reference",
        "One chain of 5 Z-lines heading about 24 degrees (off the image axes), V-test against the reference direction 20 degrees.",
        document(TEM3, [("chain-5", "chain", {"z_lines": lines})], gui=gui),
    )


def _chain_20_reference() -> Case:
    lines = z_lines(300, 1450, -61.3, 20, 70, 110, bend_deg=-0.8, jitter=3.0, seed=7)
    gui = _gui(6.62, "nm", mode="Myofibril Linearity", direction=-60.0)
    return Case(
        "chain_20_reference",
        "One curved chain of 20 Z-lines heading about -61 degrees (off the image axes), V-test against -60 degrees; persistence length and MVL belt with 19 segments.",
        document(TEM1, [("chain-20", "chain", {"z_lines": lines})], gui=gui),
    )


def _register_pairs() -> Case:
    beyond = 6 * 150 + 300
    items: list[tuple[str, str, dict[str, Any]]] = [
        ("pair-1", "pair", myofibril_pair(300, 300, 17.0, 6, 4, 150, 120, 150, beyond)),
        (
            "pair-2",
            "pair",
            myofibril_pair(300, 800, 17.0, 6, 6, 150, 120, 140, 27, jitter=3.0, seed=21),
        ),
        (
            "pair-3",
            "pair",
            myofibril_pair(1200, 900, 17.0, 5, 3, 150, 120, 130, 40, jitter=2.0, seed=31),
        ),
    ]
    return Case(
        "register_pairs",
        "Three pair items in a study-style file with the image scale (6.62 nm per px): pair 1 without overlap (skipped, no_overlap), pairs 2 and 3 computed and labelled 2 and 3 (K-M2); pair 3 has 5 and 3 Z-lines.",
        document(TEM1, items, nm_per_px=6.62),
    )


def _chain_only_notice() -> Case:
    items: list[tuple[str, str, dict[str, Any]]] = [
        (
            "myofibril-1",
            "chain",
            {"z_lines": z_lines(300, 300, 33.0, 6, 160, 120, jitter=2.0, seed=41)},
        ),
        (
            "myofibril-2",
            "chain",
            {"z_lines": z_lines(240, 420, 36.0, 6, 160, 120, jitter=2.0, seed=42)},
        ),
    ]
    return Case(
        "chain_only_notice",
        "Two chain items and no pair item in a study-style file with the image scale: linearity per chain, the notice register_needs_pair_items and no register output.",
        document(TEM3, items, nm_per_px=4.39),
    )


def _mixed_types() -> Case:
    items: list[tuple[str, str, dict[str, Any]]] = [
        ("sarcomere-1", "shape", sarcomere(500, 350, 14.0, 410, 215, 300)),
        ("sarcomere-2", "shape", sarcomere(1000, 350, 14.0, 400, 220, 0, m_skipped=True)),
        (
            "myofibril-1",
            "chain",
            {"z_lines": z_lines(300, 800, 14.0, 6, 230, 150, jitter=3.0, seed=51)},
        ),
        (
            "pair-1",
            "pair",
            myofibril_pair(300, 1050, 14.0, 5, 5, 230, 150, 190, 60, jitter=3.0, seed=52),
        ),
    ]
    gui = _gui(6.62, "nm", mode="Myofibril Linearity", direction=15.0, sigma_z=13.24)
    return Case(
        "mixed_types",
        "Shape (one with the M-line skipped), chain and pair items in one free save with click error and reference direction: every section of the results.",
        document(TEM1, items, gui=gui),
    )


CASES: tuple[Callable[[], Case], ...] = (
    _shape_classes,
    _shape_click_set,
    _shape_click_estimated,
    _shape_deleted_then_added,
    _shape_scale_unset,
    _large_px,
    _large_nm,
    _chain_5_reference,
    _chain_20_reference,
    _register_pairs,
    _chain_only_notice,
    _mixed_types,
)


def build_cases() -> list[Case]:
    """The cases of the corpus, in index order."""
    return [make() for make in CASES]


def ill_conditioned(results: dict[str, Any]) -> list[str]:
    """Circular means of a results document that no cross-platform comparison may rest on."""
    problems = []
    for i, image in enumerate(results["images"]):
        sources = [
            (f"images[{i}].chains[{k}].metrics", chain["metrics"], pairs)
            for k, chain in enumerate(image["chains"])
            for pairs in (("circular_mean", "mean_vector_length"), ("turning_mean", "turning_mvl"))
        ]
        sources += [
            (
                f"images[{i}].register[{k}].metrics",
                pair["metrics"],
                ("mean_phase", "register_index"),
            )
            for k, pair in enumerate(image["register"])
        ]
        for where, metrics, (mean_key, r_key) in sources:
            if metrics is None or metrics.get(mean_key) is None:
                continue
            mean, r = metrics[mean_key], metrics[r_key]
            if r is None or r < MIN_RESULTANT:
                problems.append(
                    f"{where}.{mean_key}: mean resultant length {r} < {MIN_RESULTANT:g}"
                )
            if abs(abs(mean) - math.pi) < MIN_DISTANCE_FROM_PI:
                problems.append(
                    f"{where}.{mean_key} = {mean!r} lies within {MIN_DISTANCE_FROM_PI:g} of +-pi"
                )
    return problems


class RefusedCorpus(ValueError):
    """A corpus with ill-conditioned circular means, or otherwise unfit to be a golden."""


def render(cases: list[Case]) -> dict[str, str]:
    """File name to text of the whole corpus; raises :class:`RefusedCorpus` on an unfit case."""
    files: dict[str, str] = {}
    index = []
    problems = []
    for case in cases:
        if not re.fullmatch(r"[a-z0-9_]+", case.name) or f"{case.name}.json" in files:
            problems.append(f"{case.name}: bad or repeated name")
            continue
        unknown = sorted(set(case.checks) - set(CHECKS))
        if unknown or len(case.doc["images"]) != 1:
            problems.append(f"{case.name}: unknown checks {unknown} or not exactly one image")
            continue
        expected = results_json(analyze_document(case.doc))
        problems += [f"{case.name}: {p}" for p in ill_conditioned(json.loads(expected))]
        files[f"{case.name}.json"] = dumps_annotations(case.doc)
        files[f"{case.name}.expected.json"] = expected
        index.append(
            {
                "name": case.name,
                "description": case.description,
                "image": case.doc["images"][0]["file_name"],
                "checks": list(case.checks),
            }
        )
    if problems:
        raise RefusedCorpus("refused golden corpus:\n" + "\n".join(problems))
    files[INDEX_NAME] = canonical_json({"format": INDEX_FORMAT, "goldens": index})
    return files


def write_corpus(cases: list[Case], out: Path) -> list[str]:
    """Write the corpus into ``out`` and return the names written; nothing is written on refusal."""
    files = render(cases)
    out.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (out / name).write_bytes(text.encode("ascii"))
    return sorted(files)


def load_rule(path: Path) -> dict[str, Any]:
    """Read and check a tolerance file."""
    rule = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rule, dict) or sorted(rule) != sorted(RULE_KEYS):
        raise ValueError(f"{path.name}: keys must be exactly {', '.join(RULE_KEYS)}")
    for key in ("rel_default", "rel_special"):
        value = rule[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not value >= 0:
            raise ValueError(f"{path.name}: {key} must be a non-negative number")
    special = rule["special"]
    if not isinstance(special, list) or not all(
        isinstance(p, str) and PATH_RE.fullmatch(p) for p in special
    ):
        raise ValueError(f"{path.name}: special must be a list of paths such as a.b[*].c")
    if len(set(special)) != len(special):
        raise ValueError(f"{path.name}: special repeats a path")
    if not (rule["approved"] is None or isinstance(rule["approved"], str)):
        raise ValueError(f"{path.name}: approved must be a DECISIONS id or null")
    if not isinstance(rule["rule"], str):
        raise ValueError(f"{path.name}: rule must be a string")
    return rule


@dataclass
class FieldStats:
    """Largest differences of one numeric field over a comparison."""

    rel: float
    count: int = 0
    max_abs: float = 0.0
    max_scaled: float = 0.0


@dataclass
class Comparison:
    """Outcome of comparing actual results with expected ones under the rule."""

    fields: dict[str, FieldStats] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether every value matched."""
        return not self.problems

    def merge(self, other: Comparison, label: str) -> None:
        """Add ``other`` (one golden) to this comparison."""
        for path, stats in other.fields.items():
            mine = self.fields.setdefault(path, FieldStats(stats.rel))
            mine.count += stats.count
            mine.max_abs = max(mine.max_abs, stats.max_abs)
            mine.max_scaled = max(mine.max_scaled, stats.max_scaled)
        self.problems += [f"{label}: {p}" for p in other.problems]


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def compare(expected: Any, actual: Any, rule: dict[str, Any]) -> Comparison:
    """Compare ``actual`` with ``expected`` under ``rule`` (see the module docstring)."""
    special = set(rule["special"])
    result = Comparison()

    def walk(e: Any, a: Any, path: str, where: str) -> None:
        if isinstance(e, dict):
            if not isinstance(a, dict) or sorted(a) != sorted(e):
                result.problems.append(f"{where}: keys differ")
                return
            for key in sorted(e):
                walk(e[key], a[key], f"{path}.{key}" if path else key, f"{where}.{key}")
        elif isinstance(e, list):
            if not isinstance(a, list) or len(a) != len(e):
                result.problems.append(f"{where}: list lengths differ")
                return
            for k, (ev, av) in enumerate(zip(e, a, strict=True)):
                walk(ev, av, f"{path}[*]", f"{where}[{k}]")
        elif _is_number(e):
            if not _is_number(a):
                result.problems.append(f"{where}: expected the number {e!r}, got {a!r}")
                return
            rel = rule["rel_special"] if path in special else rule["rel_default"]
            stats = result.fields.setdefault(path, FieldStats(rel))
            diff = abs(float(a) - float(e))
            scaled = diff / max(1.0, abs(float(e)))
            stats.count += 1
            stats.max_abs = max(stats.max_abs, diff)
            stats.max_scaled = max(stats.max_scaled, scaled)
            if scaled > rel:
                result.problems.append(
                    f"{where}: {a!r} vs expected {e!r} (scaled {scaled:.3g} > {rel:g})"
                )
        elif e != a or type(e) is not type(a):
            result.problems.append(f"{where}: {a!r} vs expected {e!r}")

    walk(expected, actual, "", "$")
    return result


def check(out: Path, rule: dict[str, Any], cases: list[Case]) -> Comparison:
    """Rebuild the corpus and compare it with the files in ``out`` under ``rule``."""
    files = render(cases)
    total = Comparison()
    present = {p.name for p in out.glob("*.json")} - {RULE_NAME}
    if present != set(files):
        total.problems.append(
            f"files differ: missing {sorted(set(files) - present)}, extra {sorted(present - set(files))}"
        )
    for name in sorted(set(files) & present):
        expected = json.loads((out / name).read_text(encoding="utf-8"))
        total.merge(compare(expected, json.loads(files[name]), rule), name)
    return total


def report(comparison: Comparison) -> str:
    """Text table of the largest scaled difference per field, worst first."""
    rows = sorted(comparison.fields.items(), key=lambda kv: (-kv[1].max_scaled, kv[0]))
    lines = [f"{'max |a-b|/max(1,|b|)':>22}  {'allowed':>8}  {'n':>5}  path"]
    lines += [f"{s.max_scaled:22.3e}  {s.rel:8.0e}  {s.count:5d}  {path}" for path, s in rows]
    lines.append(f"{len(comparison.problems)} problem(s)")
    lines += comparison.problems[:50]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or check the web golden corpus.")
    parser.add_argument("--out", type=Path, default=GOLDEN_DIR)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--rule", type=Path)
    parser.add_argument("--images", type=Path)
    args = parser.parse_args(argv)
    import numpy
    import scipy

    print(f"python {sys.version.split()[0]}, numpy {numpy.__version__}, scipy {scipy.__version__}")
    try:
        cases = build_cases()
        if args.check:
            rule = load_rule(args.rule or args.out / RULE_NAME)
            comparison = check(args.out, rule, cases)
            print(report(comparison))
            return 0 if comparison.ok else 1
        written = write_corpus(cases, args.out)
    except RefusedCorpus as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"wrote {len(written)} files into {args.out}")
    if args.images is not None:
        args.images.mkdir(parents=True, exist_ok=True)
        with (args.images / WIDE.file_name).open("wb") as f:
            for block in synthetic_png(WIDE.width, WIDE.height):
                f.write(block)
        print(f"wrote {WIDE.file_name} into {args.images}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
