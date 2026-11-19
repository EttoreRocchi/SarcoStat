"""Build the annotation-format v1 test corpus in this folder.

Run from the repository root::

    python tests/io/data/build_corpus.py

The golden files hold synthetic annotations placed on the two repository
micrographs (``examples/human_skeletal_muscle_tem_{1,3}.jpg``); no study
or dry-run data is used. All coordinates are dyadic, so the display
transforms map them exactly in float64. ``invalid/`` holds one mutation
of the golden study file per case; ``invalid_manifest.json`` records, for
each case, a substring of the expected error and whether the JSON Schema
also rejects it (the remaining cases are semantic checks that only the
validator makes).
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image

from sarcostat.io.annotations import FORMAT_NAME, FORMAT_VERSION, dumps_annotations

DATA_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = DATA_DIR.parents[2] / "examples"

TEM_1 = "human_skeletal_muscle_tem_1.jpg"
TEM_3 = "human_skeletal_muscle_tem_3.jpg"

_FORWARD: dict[str, Callable[[float, float, int, int], tuple[float, float]]] = {
    "identity": lambda x, y, w, h: (x, y),
    "rot90": lambda x, y, w, h: (y, w - 1 - x),
    "rot180": lambda x, y, w, h: (w - 1 - x, h - 1 - y),
    "rot270": lambda x, y, w, h: (h - 1 - y, x),
    "flip_h": lambda x, y, w, h: (w - 1 - x, y),
    "flip_v": lambda x, y, w, h: (x, h - 1 - y),
    "transpose": lambda x, y, w, h: (y, x),
    "anti_transpose": lambda x, y, w, h: (h - 1 - y, w - 1 - x),
}


def _image(image_id: str, file_name: str, nm_per_px: float | None, source: str | None, study):
    path = EXAMPLES_DIR / file_name
    with Image.open(path) as im:
        width, height = im.size
    return {
        "image_id": image_id,
        "file_name": file_name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "width_px": width,
        "height_px": height,
        "scale": {"nm_per_px": nm_per_px, "source": source},
        "study": study,
    }


def _flatten(kind: str, points: dict[str, Any]) -> list[list[float]]:
    if kind == "shape":
        return [p for key in ("z1", "m", "z2") if key in points for p in points[key]]
    if kind == "chain":
        return [p for line in points["z_lines"] for p in line]
    return [p for fibril in points["myofibrils"] for line in fibril for p in line]


def _shift(kind: str, points: dict[str, Any], dx: float, dy: float) -> dict[str, Any]:
    """Crop coordinates -> original pixels."""

    def line(seg):
        return [[x + dx, y + dy] for x, y in seg]

    if kind == "shape":
        return {key: line(seg) for key, seg in points.items()}
    if kind == "chain":
        return {"z_lines": [line(seg) for seg in points["z_lines"]]}
    return {"myofibrils": [[line(seg) for seg in fib] for fib in points["myofibrils"]]}


def _item(
    item_id: str,
    target_id: str | None,
    image_id: str,
    kind: str,
    transform: str,
    offset: list[int],
    size: list[int],
    crop_points: dict[str, Any] | None,
    start: str,
    finish: str,
    active: float,
    *,
    skip_reason: str | None = None,
    skipped_parts: tuple[str, ...] = (),
    n_skip_clicks: int = 0,
    undo_at: int | None = None,
    branch_end: bool | None = None,
) -> dict[str, Any]:
    w, h = size
    if crop_points is None:
        display = [_FORWARD[transform](10.5 * (k + 1), 12.25, w, h) for k in range(n_skip_clicks)]
    else:
        display = [_FORWARD[transform](x, y, w, h) for x, y in _flatten(kind, crop_points)]
    clicks = [
        {
            "order": k + 1,
            "seconds": 2.5 + 3.25 * k,
            "undo_count": 0 if undo_at is None or k < undo_at else 1,
            "x": float(x),
            "y": float(y),
        }
        for k, (x, y) in enumerate(display)
    ]
    item: dict[str, Any] = {
        "item_id": item_id,
        "target_id": target_id,
        "image_id": image_id,
        "type": kind,
        "display_transform": transform,
        "kit_crop_offset": offset,
        "kit_crop_size": size,
        "raw_clicks": clicks,
        "started_utc": start,
        "finished_utc": finish,
        "active_seconds": active,
        "skipped": crop_points is None,
        "skip_reason": skip_reason,
        "skipped_parts": list(skipped_parts),
    }
    if crop_points is not None:
        item["points"] = _shift(kind, crop_points, *offset)
    if branch_end is not None:
        item["branch_end"] = branch_end
    return item


_SHAPE = {
    "z1": [[40.25, 30.5], [40.75, 150.25]],
    "m": [[120.5, 35.0], [121.0, 146.5]],
    "z2": [[200.25, 32.75], [199.5, 148.0]],
}
_SHAPE_NO_M = {"z1": _SHAPE["z1"], "z2": _SHAPE["z2"]}
_CHAIN = {"z_lines": [[[20.5 + 80 * k, 40.25], [22.0 + 80 * k, 210.75]] for k in range(6)]}
_PAIR = {
    "myofibrils": [
        [[[30.25 + 90 * k, 20.5], [31.5 + 90 * k, 110.0]] for k in range(3)],
        [[[52.75 + 90 * k, 130.25], [53.0 + 90 * k, 230.5]] for k in range(4)],
    ]
}


def golden_study() -> dict[str, Any]:
    """A short study session that uses every item type and every transform."""
    images = [
        _image("tem1", TEM_1, 6.62, "scale_bar", {"animal": "H1", "group": "demo"}),
        _image("tem3", TEM_3, 4.39, "scale_bar", {"animal": None, "group": None}),
    ]
    day = "2026-11-02T09:"
    items = [
        _item(
            "S0-001",
            "T01",
            "tem3",
            "shape",
            "identity",
            [1000, 600],
            [256, 192],
            _SHAPE,
            day + "01:00Z",
            day + "01:31.5Z",
            30.25,
            undo_at=2,
        ),
        _item(
            "S0-002",
            "T02",
            "tem3",
            "shape",
            "rot90",
            [1200, 800],
            [240, 180],
            _SHAPE_NO_M,
            day + "01:40Z",
            day + "02:05.25Z",
            24.5,
            skip_reason="M-line not identifiable",
            skipped_parts=("m_line",),
        ),
        _item(
            "S0-003",
            "T03",
            "tem3",
            "shape",
            "rot270",
            [400, 300],
            [256, 192],
            None,
            day + "02:10Z",
            day + "02:20.125Z",
            9.75,
            skip_reason="Z-line not visible",
            n_skip_clicks=2,
        ),
        _item(
            "S0-004",
            "C01",
            "tem1",
            "chain",
            "transpose",
            [300, 400],
            [512, 256],
            _CHAIN,
            day + "02:30Z",
            day + "03:41Z",
            70.5,
            branch_end=True,
        ),
        _item(
            "S0-005",
            "P01",
            "tem1",
            "pair",
            "anti_transpose",
            [900, 700],
            [384, 256],
            _PAIR,
            day + "03:50Z",
            day + "05:12.75Z",
            81.0,
        ),
        _item(
            "S0-006",
            "T04",
            "tem1",
            "shape",
            "rot180",
            [1500, 1100],
            [256, 192],
            _SHAPE,
            day + "05:20Z",
            day + "05:49Z",
            28.5,
        ),
        _item(
            "S0-007",
            "T05",
            "tem1",
            "shape",
            "flip_v",
            [100, 1300],
            [256, 192],
            _SHAPE,
            day + "06:00Z",
            day + "06:33.5Z",
            32.0,
        ),
        _item(
            "S0-008",
            "T01",
            "tem3",
            "shape",
            "flip_h",
            [1000, 600],
            [256, 192],
            _SHAPE,
            day + "06:40Z",
            day + "07:10Z",
            29.75,
        ),
    ]
    return {
        "format": FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        "sarcostat_version": "0.2.0rc1.dev0",
        "source_digest": hashlib.sha256(b"sarcostat golden v1").hexdigest(),
        "created_utc": "2026-11-02T09:00:00Z",
        "annotator_id": "demo-annotator",
        "session_id": "demo-session-1",
        "study_id": "demo-study",
        "images": images,
        "items": items,
    }


def golden_gui() -> dict[str, Any]:
    """A free GUI session: identity transform, whole image, no IDs or timing."""
    image = _image("tem3", TEM_3, 4.39, "user", {"animal": None, "group": None})
    size = [image["width_px"], image["height_px"]]
    items = []
    for k, (kind, pts) in enumerate(
        [("shape", _SHAPE), ("shape", _SHAPE_NO_M), ("chain", _CHAIN), ("pair", _PAIR)]
    ):
        dx = 300.0 * k
        item = _item(
            f"gui-{k + 1}",
            None,
            "tem3",
            kind,
            "identity",
            [0, 0],
            size,
            _shift(kind, pts, dx, 500.0),
            "2026-11-02T10:00:00Z",
            "2026-11-02T10:00:00Z",
            0.0,
        )
        item.update(started_utc=None, finished_utc=None, active_seconds=None)
        if kind == "shape" and "m" not in pts:
            item.update(skip_reason="M-line not identifiable", skipped_parts=["m_line"])
        if k > 0:
            item["raw_clicks"] = []
        items.append(item)
    return {
        "format": FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        "sarcostat_version": "0.2.0rc1.dev0",
        "source_digest": hashlib.sha256(b"sarcostat golden v1").hexdigest(),
        "created_utc": "2026-11-02T10:05:00Z",
        "annotator_id": None,
        "session_id": None,
        "study_id": None,
        "images": [image],
        "items": items,
        "gui": dict(GUI_BLOCK),
    }


GUI_BLOCK: dict[str, Any] = {
    "pixel_size": 4.39,
    "unit": "nm",
    "expected_direction_deg": 0.0,
    "click_sigma_z": 2.5,
    "click_sigma_m": None,
    "click_perp_ratio": 1.0,
    "mode": "Single Sarcomere",
    "open_myofibril": False,
}


def _raw(doc: Any) -> str:
    """Canonical layout without validation, for the invalid cases."""
    return json.dumps(doc, sort_keys=True, indent=2, separators=(",", ": ")) + "\n"


def _mutate(
    fn: Callable[[dict[str, Any]], Any], base: Callable[[], dict[str, Any]] = golden_study
) -> Callable[[], str]:
    def build() -> str:
        doc = copy.deepcopy(base())
        fn(doc)
        return _raw(doc)

    return build


def _set(path: str, value: Any, base: Callable[[], dict[str, Any]] = golden_study):
    def fn(doc):
        *parents, last = _parse(path)
        target = doc
        for key in parents:
            target = target[key]
        target[last] = value

    return _mutate(fn, base)


def _drop(path: str, base: Callable[[], dict[str, Any]] = golden_study):
    def fn(doc):
        *parents, last = _parse(path)
        target = doc
        for key in parents:
            target = target[key]
        del target[last]

    return _mutate(fn, base)


def _parse(path: str) -> list[Any]:
    return [int(p) if p.isdigit() else p for p in path.split("/")]


def _text(fn: Callable[[str], str]) -> Callable[[], str]:
    return lambda: fn(_raw(golden_study()))


def _two(*fns):
    def fn(doc):
        for f in fns:
            f(doc)

    return _mutate(fn)


INVALID: dict[str, tuple[Callable[[], str], str, bool]] = {
    "top_level_array": (lambda: "[]\n", "must be a JSON object", True),
    "missing_format": (_drop("format"), "format", True),
    "wrong_format_name": (_set("format", "other-format"), "format", True),
    "version_2": (_set("format_version", "2.0"), "unsupported major version 2", True),
    "version_0": (_set("format_version", "0.9"), "unsupported major version 0", True),
    "version_three_parts": (_set("format_version", "1.0.0"), "format_version", True),
    "version_leading_zero": (_set("format_version", "1.01"), "format_version", True),
    "version_number": (_set("format_version", 1.0), "format_version", True),
    "missing_source_digest": (_drop("source_digest"), "source_digest", True),
    "source_digest_upper": (
        _set("source_digest", "A" * 64),
        "source_digest",
        True,
    ),
    "source_digest_short": (_set("source_digest", "a" * 63), "source_digest", True),
    "sarcostat_version_blank": (_set("sarcostat_version", " "), "sarcostat_version", True),
    "created_utc_space": (_set("created_utc", "2026-11-02 09:00:00Z"), "created_utc", True),
    "created_utc_offset": (_set("created_utc", "2026-11-02T09:00:00+01:00"), "created_utc", True),
    "created_utc_bad_date": (_set("created_utc", "2026-13-02T09:00:00Z"), "created_utc", False),
    "missing_annotator_id": (_drop("annotator_id"), "annotator_id", True),
    "annotator_id_space": (_set("annotator_id", "A 1"), "annotator_id", True),
    "study_id_empty": (_set("study_id", ""), "study_id", True),
    "missing_images": (_drop("images"), "images", True),
    "images_empty": (_set("images", []), "images", True),
    "items_object": (_set("items", {}), "items", True),
    "image_sha256_bad": (_set("images/0/sha256", "z" * 64), "images[0].sha256", True),
    "image_file_name_path": (
        _set("images/0/file_name", "examples/" + TEM_1),
        "images[0].file_name",
        True,
    ),
    "image_file_name_backslash": (
        _set("images/0/file_name", "examples\\" + TEM_1),
        "images[0].file_name",
        True,
    ),
    "image_file_name_dotdot": (_set("images/0/file_name", ".."), "images[0].file_name", True),
    "image_width_zero": (_set("images/0/width_px", 0), "images[0].width_px", True),
    "image_width_fraction": (_set("images/0/width_px", 2200.5), "images[0].width_px", True),
    "image_width_float_integral": (_set("images/0/width_px", 2200.0), "images[0].width_px", False),
    "image_height_bool": (_set("images/1/height_px", True), "images[1].height_px", True),
    "image_scale_zero": (_set("images/0/scale/nm_per_px", 0), "images[0].scale.nm_per_px", True),
    "image_scale_missing_source": (
        _drop("images/0/scale/source"),
        "images[0].scale.source",
        True,
    ),
    "image_missing_study": (_drop("images/1/study"), "images[1].study", True),
    "image_study_group_number": (
        _set("images/0/study/group", 3),
        "images[0].study.group",
        True,
    ),
    "image_duplicate_id": (_set("images/1/image_id", "tem1"), "duplicate image_id", False),
    "image_duplicate_file_name": (
        _set("images/1/file_name", TEM_1),
        "duplicate file_name",
        False,
    ),
    "item_missing_raw_clicks": (_drop("items/0/raw_clicks"), "items[0].raw_clicks", True),
    "item_id_space": (_set("items/0/item_id", "S0 001"), "items[0].item_id", True),
    "item_duplicate_id": (_set("items/1/item_id", "S0-001"), "duplicate item_id", False),
    "item_unknown_image": (_set("items/0/image_id", "tem9"), "unknown image_id", False),
    "item_type_unknown": (_set("items/0/type", "sarcomere"), "items[0].type", True),
    "item_transform_unknown": (
        _set("items/0/display_transform", "rot45"),
        "items[0].display_transform",
        True,
    ),
    "item_crop_offset_negative": (
        _set("items/0/kit_crop_offset", [-1, 600]),
        "items[0].kit_crop_offset",
        True,
    ),
    "item_crop_size_zero": (
        _set("items/0/kit_crop_size", [0, 192]),
        "items[0].kit_crop_size",
        True,
    ),
    "item_crop_size_three": (
        _set("items/0/kit_crop_size", [256, 192, 1]),
        "items[0].kit_crop_size",
        True,
    ),
    "item_crop_outside_image": (
        _set("items/0/kit_crop_offset", [2300, 600]),
        "outside image",
        False,
    ),
    "item_started_bad": (_set("items/0/started_utc", "yesterday"), "items[0].started_utc", True),
    "item_active_negative": (_set("items/0/active_seconds", -1.0), "items[0].active_seconds", True),
    "click_order_zero": (_set("items/0/raw_clicks/0/order", 0), "raw_clicks[0].order", True),
    "click_order_gap": (_set("items/0/raw_clicks/2/order", 4), "raw_clicks[2].order", False),
    "click_seconds_negative": (
        _set("items/0/raw_clicks/0/seconds", -0.5),
        "raw_clicks[0].seconds",
        True,
    ),
    "click_seconds_decreasing": (
        _set("items/0/raw_clicks/3/seconds", 1.0),
        "raw_clicks[3].seconds",
        False,
    ),
    "click_undo_bool": (_set("items/0/raw_clicks/0/undo_count", True), "undo_count", True),
    "click_undo_decreasing": (
        _set("items/0/raw_clicks/5/undo_count", 0),
        "raw_clicks[5].undo_count",
        False,
    ),
    "click_missing_x": (_drop("items/0/raw_clicks/0/x"), "raw_clicks[0].x", True),
    "click_outside_display": (
        _set("items/1/raw_clicks/0/x", 200.0),
        "outside the display",
        False,
    ),
    "click_count_mismatch": (
        _mutate(lambda d: d["items"][0]["raw_clicks"].pop()),
        "6 points but 5 raw_clicks",
        False,
    ),
    "points_outside_crop": (
        _set("items/0/points/z1/0/0", 999.0),
        "outside the crop",
        False,
    ),
    "points_missing_for_annotated": (_drop("items/0/points"), "items[0].points", True),
    "shape_missing_z2": (_drop("items/0/points/z2"), "items[0].points.z2", True),
    "shape_missing_m": (_drop("items/0/points/m"), "items[0].points.m", True),
    "shape_line_three_points": (
        _mutate(lambda d: d["items"][0]["points"]["z1"].append([1100.0, 700.0])),
        "items[0].points.z1",
        True,
    ),
    "point_string": (_set("items/0/points/m/0/1", "35"), "items[0].points.m[0][1]", True),
    "chain_one_z_line": (
        _mutate(
            lambda d: d["items"][3]["points"].update(z_lines=d["items"][3]["points"]["z_lines"][:1])
        ),
        "items[3].points.z_lines",
        True,
    ),
    "pair_three_myofibrils": (
        _mutate(
            lambda d: d["items"][4]["points"]["myofibrils"].append(
                d["items"][4]["points"]["myofibrils"][0]
            )
        ),
        "items[4].points.myofibrils",
        True,
    ),
    "pair_myofibril_one_z_line": (
        _mutate(lambda d: d["items"][4]["points"]["myofibrils"][0].__delitem__(slice(1, None))),
        "items[4].points.myofibrils[0]",
        True,
    ),
    "skipped_without_reason": (_set("items/2/skip_reason", None), "items[2].skip_reason", True),
    "skipped_with_points": (
        _set("items/2/points", {"z1": [[401.0, 301.0], [402.0, 350.0]]}),
        "items[2].points",
        True,
    ),
    "skipped_with_parts": (
        _set("items/2/skipped_parts", ["m_line"]),
        "items[2].skipped_parts",
        True,
    ),
    "skipped_not_bool": (_set("items/2/skipped", "yes"), "items[2].skipped", True),
    "partial_skip_without_reason": (
        _set("items/1/skip_reason", None),
        "items[1].skip_reason",
        True,
    ),
    "partial_skip_keeps_m": (
        _set("items/1/points/m", [[1300.0, 850.0], [1301.0, 900.0]]),
        "items[1].points.m",
        True,
    ),
    "partial_skip_unknown_part": (
        _set("items/1/skipped_parts", ["z1"]),
        "items[1].skipped_parts",
        True,
    ),
    "partial_skip_on_chain": (
        _two(
            lambda d: d["items"][3].update(skipped_parts=["m_line"]),
            lambda d: d["items"][3].update(skip_reason="M-line not identifiable"),
        ),
        "items[3].skipped_parts",
        True,
    ),
    "reason_without_skip": (_set("items/0/skip_reason", "tired"), "items[0].skip_reason", True),
    "branch_end_on_shape": (_set("items/0/branch_end", False), "items[0].branch_end", True),
    "branch_end_not_bool": (_set("items/3/branch_end", 1), "items[3].branch_end", True),
    "gui_in_study_file": (_set("gui", dict(GUI_BLOCK)), "annotator_id: must be null", True),
    "gui_with_session_id": (_set("session_id", "S1", golden_gui), "session_id", True),
    "gui_not_object": (_set("gui", [], golden_gui), "gui: must be a JSON object", True),
    "gui_missing_mode": (_drop("gui/mode", golden_gui), "gui.mode", True),
    "gui_mode_unknown": (_set("gui/mode", "Linearity", golden_gui), "gui.mode", True),
    "gui_pixel_size_zero": (_set("gui/pixel_size", 0, golden_gui), "gui.pixel_size", True),
    "gui_pixel_size_null": (_set("gui/pixel_size", None, golden_gui), "gui.pixel_size", True),
    "gui_unit_blank": (_set("gui/unit", " ", golden_gui), "gui.unit", True),
    "gui_unit_null": (_set("gui/unit", None, golden_gui), "gui.unit", True),
    "gui_direction_string": (
        _set("gui/expected_direction_deg", "90", golden_gui),
        "gui.expected_direction_deg",
        True,
    ),
    "gui_sigma_negative": (_set("gui/click_sigma_z", -0.5, golden_gui), "gui.click_sigma_z", True),
    "gui_sigma_m_bool": (_set("gui/click_sigma_m", False, golden_gui), "gui.click_sigma_m", True),
    "gui_perp_ratio_zero": (
        _set("gui/click_perp_ratio", 0.0, golden_gui),
        "gui.click_perp_ratio",
        True,
    ),
    "gui_perp_ratio_null": (
        _set("gui/click_perp_ratio", None, golden_gui),
        "gui.click_perp_ratio",
        True,
    ),
    "gui_open_myofibril_int": (
        _set("gui/open_myofibril", 1, golden_gui),
        "gui.open_myofibril",
        True,
    ),
    "nan_token": (
        _text(lambda t: t.replace('"active_seconds": 30.25', '"active_seconds": NaN')),
        "NaN",
        False,
    ),
    "infinity_token": (
        _text(lambda t: t.replace('"active_seconds": 30.25', '"active_seconds": -Infinity')),
        "Infinity",
        False,
    ),
    "overflow_float": (
        _text(lambda t: t.replace('"active_seconds": 30.25', '"active_seconds": 1e999')),
        "non-finite",
        False,
    ),
    "duplicate_key": (
        _text(lambda t: t.replace('"format": ', '"format": "x",\n  "format": ', 1)),
        "duplicate key",
        False,
    ),
    "utf8_bom": (_text(lambda t: "﻿" + t), "byte order mark", False),
    "truncated": (_text(lambda t: t[: len(t) // 2]), "not valid JSON", False),
}


def build() -> None:
    (DATA_DIR / "golden_v1_study.json").write_bytes(dumps_annotations(golden_study()).encode())
    (DATA_DIR / "golden_v1_gui.json").write_bytes(dumps_annotations(golden_gui()).encode())
    invalid_dir = DATA_DIR / "invalid"
    invalid_dir.mkdir(exist_ok=True)
    for old in invalid_dir.glob("*.json"):
        old.unlink()
    manifest = {}
    for name, (builder, error, schema) in INVALID.items():
        (invalid_dir / f"{name}.json").write_bytes(builder().encode("utf-8"))
        manifest[name] = {"error": error, "schema_rejects": schema}
    (DATA_DIR / "invalid_manifest.json").write_bytes(_raw(manifest).encode())


if __name__ == "__main__":
    build()
