"""Annotation files, format v1.

An annotation file holds the landmarks clicked on one or more images,
together with what is needed to check and reproduce them: the SHA-256 and
size of each image, the crop and display transform under which each item
was shown, the raw display clicks with their timing, and the landmark
points mapped back to original image pixels. The full specification is in
``docs/annotation_format.md``; the JSON Schema shipped with the package
(:func:`load_schema`) describes the same structure.

- :func:`validate_annotations` is the authoritative check. It enforces the
  structure of the schema plus the rules a schema cannot express
  (references between images and items, unique IDs, crops inside the
  image, points inside the crop, clicks inside the display, click order).
- :func:`dumps_annotations` writes canonical JSON: sorted keys, two-space
  indentation, ASCII only, a trailing newline, no NaN or infinity. Reading
  a canonical file and writing it again gives identical bytes.
- :func:`write_annotations` validates, then writes atomically.
- :func:`read_annotations` parses strictly (no NaN/Infinity tokens, no
  duplicate keys, UTF-8 without byte order mark) and validates; with
  ``image_dir`` it also checks each image's SHA-256.

Readers of 1.x accept files of any later 1.x version and keep the fields
they do not know; files of another major version are rejected.

This module uses only the standard library.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import tempfile
import warnings
from datetime import datetime
from importlib import resources
from os import PathLike
from pathlib import Path
from typing import Any, NoReturn, Union

PathInput = Union[str, "PathLike[str]"]

FORMAT_NAME = "sarcostat-annotations"
FORMAT_VERSION = "1.0"
SCHEMA_RESOURCE = "annotation-v1.schema.json"

#: Item types: one sarcomere (6 landmarks), a chain of Z-lines, a pair of myofibrils.
ITEM_TYPES = ("shape", "chain", "pair")
#: The eight dihedral display transforms (implemented in :mod:`sarcostat.io.study`).
DISPLAY_TRANSFORMS = (
    "identity",
    "rot90",
    "rot180",
    "rot270",
    "flip_h",
    "flip_v",
    "transpose",
    "anti_transpose",
)
#: Parts of an item that can be skipped while the rest is kept.
SKIPPABLE_PARTS = ("m_line",)
#: Analysis modes of the GUI window, stored in the optional ``gui`` block.
GUI_MODES = ("Myofibril Linearity", "Single Sarcomere")

_SWAPS_AXES = frozenset({"rot90", "rot270", "transpose", "anti_transpose"})
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_UTC_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z")
_VERSION_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
_HEADER_KEYS = ("format", "format_version")
_TOP_KEYS = (
    "sarcostat_version",
    "source_digest",
    "created_utc",
    "annotator_id",
    "session_id",
    "study_id",
    "images",
    "items",
)
_IMAGE_KEYS = ("image_id", "file_name", "sha256", "width_px", "height_px", "scale", "study")
_GUI_KEYS = (
    "pixel_size",
    "unit",
    "expected_direction_deg",
    "click_sigma_z",
    "click_sigma_m",
    "click_perp_ratio",
    "mode",
    "open_myofibril",
)
_ITEM_KEYS = (
    "item_id",
    "target_id",
    "image_id",
    "type",
    "display_transform",
    "kit_crop_offset",
    "kit_crop_size",
    "raw_clicks",
    "started_utc",
    "finished_utc",
    "active_seconds",
    "skipped",
    "skip_reason",
    "skipped_parts",
)
_CLICK_KEYS = ("x", "y", "order", "seconds", "undo_count")


class AnnotationFormatError(ValueError):
    """An annotation file or document that does not follow format v1."""


class ImageMismatchError(AnnotationFormatError):
    """An image whose SHA-256 differs from the one recorded in the annotation file."""


class MissingImageWarning(UserWarning):
    """An image of the annotation file was not found, so its SHA-256 was not checked."""


# Primitive checks ------------------------------------------------------------


def _fail(path: str, message: str) -> NoReturn:
    raise AnnotationFormatError(f"{path}: {message}")


def _object(value: Any, path: str, required: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(path, "must be a JSON object")
    for key in required:
        if key not in value:
            _fail(f"{path}.{key}" if path else key, "is required")
    return value


def _array(value: Any, path: str, min_items: int = 0, exact: bool = False) -> list[Any]:
    if not isinstance(value, list):
        _fail(path, "must be an array")
    if len(value) < min_items or (exact and len(value) != min_items):
        size = f"exactly {min_items}" if exact else f"at least {min_items}"
        _fail(path, f"must have {size} entries, got {len(value)}")
    return value


def _text(value: Any, path: str, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not re.search(r"\S", value):
        _fail(path, "must be a non-empty string" + (" or null" if nullable else ""))
    return value


def _match(
    value: Any, path: str, pattern: re.Pattern[str], what: str, nullable: bool = False
) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        _fail(path, f"must be {what}" + (" or null" if nullable else "") + f", got {value!r}")
    return value


def _ident(value: Any, path: str, nullable: bool = False) -> str | None:
    what = "an ID (letters, digits, '.', '_', '-'; at most 64 characters)"
    return _match(value, path, _ID_RE, what, nullable)


def _utc(value: Any, path: str, nullable: bool = False) -> str | None:
    text = _match(value, path, _UTC_RE, "a UTC timestamp YYYY-MM-DDTHH:MM:SS[.ffffff]Z", nullable)
    if text is not None:
        try:
            datetime.strptime(text.split(".")[0].rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            _fail(path, f"is not a valid date and time: {text!r}")
    return text


def _enum(value: Any, path: str, choices: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in choices:
        _fail(path, f"must be one of {', '.join(choices)}; got {value!r}")
    return value


def _bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        _fail(path, f"must be true or false, got {value!r}")
    return value


def _int(value: Any, path: str, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        _fail(path, f"must be an integer >= {minimum}, got {value!r}")
    return value


def _number(value: Any, path: str, minimum: float | None = None, nullable: bool = False):
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(path, f"must be a number, got {value!r}")
    if isinstance(value, float) and not math.isfinite(value):
        _fail(path, f"must be a finite number, got {value!r}")
    if minimum is not None and value < minimum:
        _fail(path, f"must be >= {minimum:g}, got {value!r}")
    return value


def _positive(value: Any, path: str, nullable: bool = False) -> Any:
    number = _number(value, path, nullable=nullable)
    if number is not None and number <= 0:
        _fail(path, f"must be positive{' or null' if nullable else ''}, got {number!r}")
    return number


def _int_pair(value: Any, path: str, minimum: int) -> tuple[int, int]:
    a, b = _array(value, path, 2, exact=True)
    return _int(a, f"{path}[0]", minimum), _int(b, f"{path}[1]", minimum)


# Document structure ----------------------------------------------------------


def _check_header(doc: Any) -> None:
    if not isinstance(doc, dict):
        _fail("document", "must be a JSON object")
    _object(doc, "", _HEADER_KEYS)
    if doc["format"] != FORMAT_NAME:
        _fail("format", f"must be {FORMAT_NAME!r}, got {doc['format']!r}")
    version = doc["format_version"]
    if not isinstance(version, str):
        _fail("format_version", f"must be a string such as {FORMAT_VERSION!r}, got {version!r}")
    match = _VERSION_RE.fullmatch(version)
    if match is None:
        _fail("format_version", f"must look like {FORMAT_VERSION!r}, got {version!r}")
    if match.group(1) != "1":
        _fail(
            "format_version",
            f"unsupported major version {match.group(1)} ({version!r}); this reader supports 1.x",
        )


def _check_gui(gui: Any) -> None:
    _object(gui, "gui", _GUI_KEYS)
    _positive(gui["pixel_size"], "gui.pixel_size")
    _text(gui["unit"], "gui.unit")
    _number(gui["expected_direction_deg"], "gui.expected_direction_deg", nullable=True)
    _number(gui["click_sigma_z"], "gui.click_sigma_z", minimum=0, nullable=True)
    _number(gui["click_sigma_m"], "gui.click_sigma_m", minimum=0, nullable=True)
    _positive(gui["click_perp_ratio"], "gui.click_perp_ratio")
    _enum(gui["mode"], "gui.mode", GUI_MODES)
    _bool(gui["open_myofibril"], "gui.open_myofibril")


def _check_image(image: Any, path: str) -> None:
    _object(image, path, _IMAGE_KEYS)
    _ident(image["image_id"], f"{path}.image_id")
    name = image["file_name"]
    if (
        not isinstance(name, str)
        or not 0 < len(name) <= 255
        or "/" in name
        or "\\" in name
        or name in (".", "..")
    ):
        _fail(f"{path}.file_name", f"must be a file name without folders, got {name!r}")
    _match(image["sha256"], f"{path}.sha256", _SHA256_RE, "64 lowercase hexadecimal digits")
    _int(image["width_px"], f"{path}.width_px", 1)
    _int(image["height_px"], f"{path}.height_px", 1)
    scale = _object(image["scale"], f"{path}.scale", ("nm_per_px", "source"))
    _positive(scale["nm_per_px"], f"{path}.scale.nm_per_px", nullable=True)
    _text(scale["source"], f"{path}.scale.source", nullable=True)
    study = _object(image["study"], f"{path}.study", ("animal", "group"))
    _text(study["animal"], f"{path}.study.animal", nullable=True)
    _text(study["group"], f"{path}.study.group", nullable=True)


def _z_lines(value: Any, path: str) -> list[tuple[float, float]]:
    """At least two lines; returns their points flat, in click order."""
    return [p for i, line in enumerate(_array(value, path, 2)) for p in _line(line, f"{path}[{i}]")]


def _line(value: Any, path: str) -> list[tuple[float, float]]:
    out = []
    for j, point in enumerate(_array(value, path, 2, exact=True)):
        x, y = _array(point, f"{path}[{j}]", 2, exact=True)
        out.append((_number(x, f"{path}[{j}][0]"), _number(y, f"{path}[{j}][1]")))
    return out


def _item_points(kind: str, points: Any, path: str, m_skipped: bool) -> list[tuple[float, float]]:
    """Check the points of an annotated item; return them flat, in click order."""
    if kind == "shape":
        _object(points, path, ("z1", "z2"))
        keys: tuple[str, ...]
        if m_skipped:
            if "m" in points:
                _fail(f"{path}.m", "must be absent when the M-line is skipped")
            keys = ("z1", "z2")
        else:
            if "m" not in points:
                _fail(f"{path}.m", "is required unless skipped_parts lists 'm_line'")
            keys = ("z1", "m", "z2")
        return [p for key in keys for p in _line(points[key], f"{path}.{key}")]
    if kind == "chain":
        _object(points, path, ("z_lines",))
        return _z_lines(points["z_lines"], f"{path}.z_lines")
    _object(points, path, ("myofibrils",))
    fibrils = _array(points["myofibrils"], f"{path}.myofibrils", 2, exact=True)
    return [p for k, fib in enumerate(fibrils) for p in _z_lines(fib, f"{path}.myofibrils[{k}]")]


def _check_item(item: Any, path: str, images: dict[str, dict[str, Any]]) -> None:
    _object(item, path, _ITEM_KEYS)
    _ident(item["item_id"], f"{path}.item_id")
    _ident(item["target_id"], f"{path}.target_id", nullable=True)
    image_id = _ident(item["image_id"], f"{path}.image_id")
    kind = _enum(item["type"], f"{path}.type", ITEM_TYPES)
    transform = _enum(item["display_transform"], f"{path}.display_transform", DISPLAY_TRANSFORMS)
    x0, y0 = _int_pair(item["kit_crop_offset"], f"{path}.kit_crop_offset", 0)
    w, h = _int_pair(item["kit_crop_size"], f"{path}.kit_crop_size", 1)
    _utc(item["started_utc"], f"{path}.started_utc", nullable=True)
    _utc(item["finished_utc"], f"{path}.finished_utc", nullable=True)
    _number(item["active_seconds"], f"{path}.active_seconds", minimum=0, nullable=True)
    skipped = _bool(item["skipped"], f"{path}.skipped")
    parts = _array(item["skipped_parts"], f"{path}.skipped_parts")
    for k, part in enumerate(parts):
        _enum(part, f"{path}.skipped_parts[{k}]", SKIPPABLE_PARTS)
    if len(set(parts)) != len(parts):
        _fail(f"{path}.skipped_parts", "must not repeat a part")
    reason = _text(item["skip_reason"], f"{path}.skip_reason", nullable=True)
    if "branch_end" in item:
        if kind != "chain":
            _fail(f"{path}.branch_end", "is allowed only on chain items")
        _bool(item["branch_end"], f"{path}.branch_end")

    if image_id not in images:
        _fail(f"{path}.image_id", f"unknown image_id {image_id!r}")
    image = images[image_id]
    if x0 + w > image["width_px"] or y0 + h > image["height_px"]:
        _fail(
            path,
            f"kit crop {[x0, y0]} + {[w, h]} lies outside image {image_id!r} "
            f"({image['width_px']} x {image['height_px']} px)",
        )

    flat: list[tuple[float, float]] = []
    if skipped:
        if reason is None:
            _fail(f"{path}.skip_reason", "is required when the item is skipped")
        if parts:
            _fail(f"{path}.skipped_parts", "must be empty when the whole item is skipped")
        if "points" in item and item["points"] != {}:
            _fail(f"{path}.points", "must be absent or {} when the item is skipped")
    else:
        if parts and kind != "shape":
            _fail(
                f"{path}.skipped_parts", f"'m_line' can be skipped only on shape items, not {kind}"
            )
        if bool(parts) != (reason is not None):
            _fail(
                f"{path}.skip_reason",
                "must be given exactly when the item or a part of it is skipped",
            )
        if "points" not in item:
            _fail(f"{path}.points", "is required unless the item is skipped")
        flat = _item_points(kind, item["points"], f"{path}.points", "m_line" in parts)
        for x, y in flat:
            if not (x0 - 0.5 <= x <= x0 + w - 0.5 and y0 - 0.5 <= y <= y0 + h - 0.5):
                _fail(f"{path}.points", f"point {[x, y]} lies outside the crop")

    _check_clicks(
        item["raw_clicks"], f"{path}.raw_clicks", (h, w) if transform in _SWAPS_AXES else (w, h)
    )
    n_clicks = len(item["raw_clicks"])
    if not skipped and n_clicks and n_clicks != len(flat):
        _fail(path, f"{len(flat)} points but {n_clicks} raw_clicks")


def _check_clicks(clicks: Any, path: str, display: tuple[int, int]) -> None:
    dw, dh = display
    last_seconds: float = 0.0
    last_undo = 0
    for k, click in enumerate(_array(clicks, path)):
        cpath = f"{path}[{k}]"
        _object(click, cpath, _CLICK_KEYS)
        x = _number(click["x"], f"{cpath}.x")
        y = _number(click["y"], f"{cpath}.y")
        order = _int(click["order"], f"{cpath}.order", 1)
        seconds = _number(click["seconds"], f"{cpath}.seconds", minimum=0)
        undo = _int(click["undo_count"], f"{cpath}.undo_count", 0)
        if order != k + 1:
            _fail(f"{cpath}.order", f"must be {k + 1} (clicks are listed in order), got {order}")
        if seconds < last_seconds:
            _fail(f"{cpath}.seconds", "must not decrease from one click to the next")
        if undo < last_undo:
            _fail(f"{cpath}.undo_count", "must not decrease from one click to the next")
        if not (-0.5 <= x <= dw - 0.5 and -0.5 <= y <= dh - 0.5):
            _fail(cpath, f"click {[x, y]} lies outside the display ({dw} x {dh} px)")
        last_seconds, last_undo = seconds, undo


def validate_annotations(doc: dict[str, Any]) -> None:
    """Check that ``doc`` is a valid annotation document of format 1.x.

    Parameters
    ----------
    doc : dict
        The parsed document. It is not modified.

    Raises
    ------
    AnnotationFormatError
        On the first violation, with the JSON path of the offending value
        (for example ``items[3].raw_clicks[0].order``).
    """
    _check_header(doc)
    _object(doc, "", _TOP_KEYS)
    _text(doc["sarcostat_version"], "sarcostat_version")
    _match(doc["source_digest"], "source_digest", _SHA256_RE, "64 lowercase hexadecimal digits")
    _utc(doc["created_utc"], "created_utc")
    for key in ("annotator_id", "session_id", "study_id"):
        _ident(doc[key], key, nullable=True)
    if "gui" in doc:
        _check_gui(doc["gui"])
        for key in ("annotator_id", "session_id", "study_id"):
            if doc[key] is not None:
                _fail(key, "must be null in a file with a gui block (free GUI saves only)")

    images: dict[str, dict[str, Any]] = {}
    names: set[str] = set()
    for i, image in enumerate(_array(doc["images"], "images", min_items=1)):
        _check_image(image, f"images[{i}]")
        if image["image_id"] in images:
            _fail(f"images[{i}].image_id", f"duplicate image_id {image['image_id']!r}")
        if image["file_name"] in names:
            _fail(f"images[{i}].file_name", f"duplicate file_name {image['file_name']!r}")
        images[image["image_id"]] = image
        names.add(image["file_name"])

    item_ids: set[str] = set()
    for i, item in enumerate(_array(doc["items"], "items")):
        _check_item(item, f"items[{i}]", images)
        if item["item_id"] in item_ids:
            _fail(f"items[{i}].item_id", f"duplicate item_id {item['item_id']!r}")
        item_ids.add(item["item_id"])


# Reading and writing ---------------------------------------------------------


def dumps_annotations(doc: dict[str, Any]) -> str:
    """Validate ``doc`` and return it as canonical JSON text.

    Keys are sorted, the indentation is two spaces with ``", "``/``": "``
    separators, non-ASCII characters are escaped and the text ends with
    one newline, so equal documents give identical text on every platform.
    """
    validate_annotations(doc)
    text = json.dumps(
        doc,
        sort_keys=True,
        indent=2,
        separators=(",", ": "),
        ensure_ascii=True,
        allow_nan=False,
    )
    return text + "\n"


def write_annotations(doc: dict[str, Any], path: PathInput) -> None:
    """Validate ``doc`` and write it to ``path`` atomically.

    The text is written to a temporary file in the same folder, flushed to
    disk and moved over ``path`` with :func:`os.replace`, so ``path``
    holds either the old content or the complete new file. Line endings
    are ``\\n`` on every platform.
    """
    target = Path(path)
    data = dumps_annotations(doc).encode("ascii")
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, target.stat().st_mode & 0o777 if target.exists() else 0o644)
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _reject_constant(token: str) -> NoReturn:
    raise AnnotationFormatError(f"not valid JSON: the constant {token} is not allowed")


def _finite_float(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        raise AnnotationFormatError(f"not valid JSON: non-finite number {token}")
    return value


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise AnnotationFormatError(f"not valid JSON: duplicate key {key!r}")
        out[key] = value
    return out


def read_annotations(path: PathInput, image_dir: PathInput | None = None) -> dict[str, Any]:
    """Read and validate an annotation file.

    Parameters
    ----------
    path : str or os.PathLike
        The annotation file.
    image_dir : str or os.PathLike, optional
        Folder holding the images, looked up by ``file_name``. When given,
        each image found there must have the recorded SHA-256
        (:class:`ImageMismatchError` otherwise); an image that is not
        there only raises a :class:`MissingImageWarning`.

    Returns
    -------
    dict
        The document, including any fields unknown to this version.

    Raises
    ------
    AnnotationFormatError
        If the file is not strict UTF-8 JSON or not a valid document. The
        message starts with the file name, without its folder.
    """
    source = Path(path)
    try:
        raw = source.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            raise AnnotationFormatError("not valid JSON: the file starts with a byte order mark")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AnnotationFormatError(f"not valid UTF-8 ({exc.reason})") from None
        try:
            doc: dict[str, Any] = json.loads(
                text,
                parse_constant=_reject_constant,
                parse_float=_finite_float,
                object_pairs_hook=_unique_keys,
            )
        except json.JSONDecodeError as exc:
            raise AnnotationFormatError(f"not valid JSON ({exc})") from None
        validate_annotations(doc)
    except AnnotationFormatError as exc:
        raise AnnotationFormatError(f"{source.name}: {exc}") from None
    if image_dir is not None:
        _check_images(doc, Path(image_dir))
    return doc


def _check_images(doc: dict[str, Any], image_dir: Path) -> None:
    for i, image in enumerate(doc["images"]):
        name = image["file_name"]
        candidate = image_dir / name
        if not candidate.is_file():
            warnings.warn(
                f"Image {name} ({image['image_id']}) was not found; its SHA-256 was not checked.",
                MissingImageWarning,
                stacklevel=3,
            )
            continue
        digest = sha256_file(candidate)
        if digest != image["sha256"]:
            raise ImageMismatchError(
                f"images[{i}] ({image['image_id']}): {name} has SHA-256 {digest}, "
                f"but the annotation file records {image['sha256']}"
            )


def sha256_file(path: PathInput) -> str:
    """Return the SHA-256 of a file as 64 lowercase hexadecimal digits."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_schema() -> dict[str, Any]:
    """Return the JSON Schema of format v1 shipped with the package.

    The schema documents the structure; :func:`validate_annotations` is the
    authoritative check and also enforces the cross-references and bounds
    that a schema cannot express.
    """
    text = resources.files("sarcostat.io").joinpath(SCHEMA_RESOURCE).read_text(encoding="utf-8")
    schema: dict[str, Any] = json.loads(text)
    return schema


__all__ = [
    "DISPLAY_TRANSFORMS",
    "FORMAT_NAME",
    "FORMAT_VERSION",
    "GUI_MODES",
    "ITEM_TYPES",
    "SCHEMA_RESOURCE",
    "SKIPPABLE_PARTS",
    "AnnotationFormatError",
    "ImageMismatchError",
    "MissingImageWarning",
    "dumps_annotations",
    "load_schema",
    "read_annotations",
    "sha256_file",
    "validate_annotations",
    "write_annotations",
]
