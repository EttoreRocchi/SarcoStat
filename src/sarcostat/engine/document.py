"""Annotation files of format v1 to and from the annotation session of the web app.

The web app annotates one image at a time, so an annotation session maps
to a format v1 document with exactly one image:

- :func:`read_document` parses an annotation file strictly, validates it
  and returns its image, its settings and all of its items (shape, chain
  and pair, in document order); a file of several images is refused with
  a pointer to ``sarcostat analyze``;
- :func:`build_document` writes the session back as a free-session
  document: identity transform, the whole image as crop, null IDs and no
  timing for new items, the ``gui`` block with the settings the metrics
  depend on, and every item the session holds, chain and pair items
  unchanged. Fields of the document, of its image and of its items that
  the session does not know are kept.

The settings of a file follow :func:`sarcostat.analysis.settings_for_image`,
so the session measures a file as ``sarcostat analyze`` does. ``gui.mode``
is ``"Single Sarcomere"`` when the last active mode was the shape mode,
otherwise ``"Myofibril Linearity"``.
"""

from __future__ import annotations

import copy
import functools
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, NoReturn

from sarcostat import __version__
from sarcostat.analysis import settings_for_image
from sarcostat.engine.bridge import MAX_JSON_DEPTH, Refused, json_depth
from sarcostat.io.annotations import (
    FORMAT_NAME,
    FORMAT_VERSION,
    AnnotationFormatError,
    validate_annotations,
)

DEFAULT_UNIT = "px"
SHAPE_GUI_MODE = "Single Sarcomere"
LINEARITY_GUI_MODE = "Myofibril Linearity"
M_LINE_SKIP_REASON = "M-line not identifiable"
NM_PER_UNIT = {"nm": 1.0, "um": 1e3, "\u00b5m": 1e3, "\u03bcm": 1e3, "mm": 1e6}

_NOT_ID = re.compile(r"[^A-Za-z0-9._-]")


@functools.lru_cache(maxsize=1)
def _source_digest() -> str:
    from sarcostat.io.provenance import source_digest

    return source_digest()


@dataclass(frozen=True)
class ImageInfo:
    """The image a session annotates, as the page reports it.

    Attributes
    ----------
    file_name : str
        File name without folders.
    sha256 : str
        SHA-256 of the file, 64 lowercase hexadecimal digits.
    width_px, height_px : int
        Size of the image in pixels.
    """

    file_name: str
    sha256: str
    width_px: int
    height_px: int

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> ImageInfo:
        """Read ``{file_name, sha256, width_px, height_px}``."""
        return cls(
            str(value["file_name"]),
            str(value["sha256"]),
            int(value["width_px"]),
            int(value["height_px"]),
        )

    def to_json(self) -> dict[str, Any]:
        """``{file_name, sha256, width_px, height_px}``."""
        return {
            "file_name": self.file_name,
            "sha256": self.sha256,
            "width_px": self.width_px,
            "height_px": self.height_px,
        }


@dataclass(frozen=True)
class SessionSettings:
    """Settings of a session that the metrics depend on.

    Attributes
    ----------
    pixel_size : float
        Length of one pixel, in ``unit``.
    unit : str
        Unit of every length; ``"px"`` until the scale is set.
    reference_direction_deg : float or None
        Reference direction of the V-test of chain headings (degrees,
        image frame: 0 = right, 90 = down), or ``None``.
    click_sigma_z, click_sigma_m : float or None
        Click error along the Z-lines and along the M-line, in ``unit``;
        ``click_sigma_z`` ``None`` switches the uncertainty report off.
    click_perp_ratio : float
        Ratio of the click error across a line to the error along it.
    """

    pixel_size: float = 1.0
    unit: str = DEFAULT_UNIT
    reference_direction_deg: float | None = None
    click_sigma_z: float | None = None
    click_sigma_m: float | None = None
    click_perp_ratio: float = 1.0


@dataclass(frozen=True)
class LoadedDocument:
    """An annotation file of one image, read for the session.

    Attributes
    ----------
    doc : dict
        The validated document, as parsed.
    text : str
        The document as canonical text (equal to the file when the file
        was canonical).
    image : ImageInfo
        The image as recorded in the file.
    image_id : str
        Its ``image_id``.
    settings : SessionSettings
        The settings the file's metrics are computed with.
    items : list of dict
        Every item of the file, in document order.
    last_mode_shape : bool
        Whether ``gui.mode`` names the shape mode.
    open_myofibril : bool
        ``gui.open_myofibril`` (``False`` without a ``gui`` block).
    """

    doc: dict[str, Any]
    text: str
    image: ImageInfo
    image_id: str
    settings: SessionSettings
    items: list[dict[str, Any]]
    last_mode_shape: bool
    open_myofibril: bool


def image_id_for(file_name: str) -> str:
    """A format v1 ``image_id`` derived from an image file name."""
    stem = file_name.rsplit(".", 1)[0]
    ident = _NOT_ID.sub("-", stem).lstrip("._-")[:64]
    return ident or "image"


def export_file_name(file_name: str) -> str:
    """Default name of the annotation file of an image: ``<stem>_annotations.json``."""
    return f"{PurePosixPath(file_name).stem}_annotations.json"


def nm_per_px(pixel_size: float, unit: str) -> float | None:
    """The scale in nm per pixel, or ``None`` when ``unit`` is not a length unit."""
    factor = NM_PER_UNIT.get(unit.strip())
    return None if factor is None else float(pixel_size) * factor


def gui_mode(last_mode_shape: bool) -> str:
    """``gui.mode`` of a free save: the shape mode, or the linearity mode otherwise."""
    return SHAPE_GUI_MODE if last_mode_shape else LINEARITY_GUI_MODE


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


def parse_document(text: str) -> dict[str, Any]:
    """Parse annotation text strictly and validate it as format 1.x.

    The rules are those of :func:`sarcostat.io.annotations.read_annotations`:
    no byte order mark, no NaN or infinity, no duplicate keys; and no
    nesting deeper than :data:`~sarcostat.engine.bridge.MAX_JSON_DEPTH`
    levels, which would exhaust the browser's stack.

    Raises
    ------
    AnnotationFormatError
        If the text is not strict JSON or not a valid document.
    """
    if text.startswith("\ufeff"):
        raise AnnotationFormatError("not valid JSON: the text starts with a byte order mark")
    depth = json_depth(text)
    if depth > MAX_JSON_DEPTH:
        raise AnnotationFormatError(
            f"not valid JSON: it nests {depth} levels, more than {MAX_JSON_DEPTH}"
        )
    try:
        doc = json.loads(
            text,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
            object_pairs_hook=_unique_keys,
        )
    except json.JSONDecodeError as exc:
        raise AnnotationFormatError(f"not valid JSON ({exc})") from None
    except RecursionError:
        raise AnnotationFormatError("not valid JSON (nested too deeply)") from None
    validate_annotations(doc)
    result: dict[str, Any] = doc
    return result


def dumps_document(doc: dict[str, Any]) -> str:
    """Validate ``doc`` and return its canonical text (as :func:`~sarcostat.io.annotations.dumps_annotations`)."""
    from sarcostat.io.annotations import dumps_annotations

    return dumps_annotations(doc)


def _settings(doc: dict[str, Any], image_id: str) -> SessionSettings:
    s = settings_for_image(doc, image_id)
    return SessionSettings(
        pixel_size=s.pixel_size,
        unit=s.unit,
        reference_direction_deg=s.reference_direction_deg,
        click_sigma_z=s.click_sigma_z,
        click_sigma_m=s.click_sigma_m,
        click_perp_ratio=s.click_perp_ratio,
    )


def read_document(text: str) -> LoadedDocument:
    """Read an annotation file of one image for the session.

    Raises
    ------
    Refused
        If the text is not a valid annotation document, or if it covers
        several images (the web app opens one image per file).
    """
    try:
        doc = parse_document(text)
        canonical = dumps_document(doc)
    except AnnotationFormatError as exc:
        raise Refused(f"This is not an annotation file of format v1: {exc}") from None
    images = doc["images"]
    if len(images) != 1:
        raise Refused(
            f"This file annotates {len(images)} images, and the web app opens one image per "
            "file: analyse it with the command sarcostat analyze."
        )
    entry = images[0]
    image_id = entry["image_id"]
    gui = doc.get("gui")
    return LoadedDocument(
        doc=doc,
        text=canonical,
        image=ImageInfo.from_json(entry),
        image_id=image_id,
        settings=_settings(doc, image_id),
        items=[copy.deepcopy(item) for item in doc["items"] if item["image_id"] == image_id],
        last_mode_shape=gui is not None and gui["mode"] == SHAPE_GUI_MODE,
        open_myofibril=bool(gui["open_myofibril"]) if gui is not None else False,
    )


def scale_block(settings: SessionSettings) -> dict[str, Any]:
    """The image's ``scale`` block for the session's scale."""
    value = nm_per_px(settings.pixel_size, settings.unit)
    return {"nm_per_px": value, "source": None if value is None else "user"}


def gui_block(
    settings: SessionSettings, last_mode_shape: bool, open_myofibril: bool
) -> dict[str, Any]:
    """The ``gui`` block of a free save."""
    direction = settings.reference_direction_deg
    sigma_z, sigma_m = settings.click_sigma_z, settings.click_sigma_m
    return {
        "pixel_size": float(settings.pixel_size),
        "unit": settings.unit,
        "expected_direction_deg": None if direction is None else float(direction),
        "click_sigma_z": None if sigma_z is None else float(sigma_z),
        "click_sigma_m": None if sigma_m is None else float(sigma_m),
        "click_perp_ratio": float(settings.click_perp_ratio),
        "mode": gui_mode(last_mode_shape),
        "open_myofibril": bool(open_myofibril),
    }


def shape_item(
    item_id: str,
    image_id: str,
    image: ImageInfo,
    lines: Mapping[str, Sequence[Sequence[float]]],
) -> dict[str, Any]:
    """A confirmed sarcomere as a format v1 ``shape`` item with free-session values.

    Parameters
    ----------
    item_id, image_id : str
        IDs of the item and of its image.
    image : ImageInfo
        The image (its size is the crop).
    lines : Mapping
        ``z1``, ``z2`` and, unless the M-line was skipped, ``m``: each a
        line ``[[x, y], [x, y]]`` in image pixels.
    """
    points = {
        key: [[float(p[0]), float(p[1])] for p in line]
        for key, line in lines.items()
        if key in ("z1", "m", "z2")
    }
    m_skipped = "m" not in points
    return {
        "item_id": item_id,
        "target_id": None,
        "image_id": image_id,
        "type": "shape",
        "display_transform": "identity",
        "kit_crop_offset": [0, 0],
        "kit_crop_size": [image.width_px, image.height_px],
        "raw_clicks": [],
        "points": points,
        "started_utc": None,
        "finished_utc": None,
        "active_seconds": None,
        "skipped": False,
        "skip_reason": M_LINE_SKIP_REASON if m_skipped else None,
        "skipped_parts": ["m_line"] if m_skipped else [],
    }


def build_document(
    *,
    image: ImageInfo,
    image_id: str,
    settings: SessionSettings,
    items: Sequence[Mapping[str, Any]],
    last_mode_shape: bool,
    open_myofibril: bool,
    created_utc: str,
    base: Mapping[str, Any] | None = None,
    base_scale: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The session as a free-session format v1 document (not yet validated).

    Parameters
    ----------
    image, image_id : ImageInfo, str
        The annotated image and its ``image_id``.
    settings : SessionSettings
        Written to the ``gui`` block (and to the image's scale).
    items : sequence of dict
        Every item of the image, in order; copied unchanged.
    last_mode_shape, open_myofibril : bool
        ``gui.mode`` and ``gui.open_myofibril``.
    created_utc : str
        ``YYYY-MM-DDTHH:MM:SSZ``.
    base : Mapping, optional
        The document the session was read from: its fields unknown to the
        session, and those of its image, are kept.
    base_scale : Mapping, optional
        The image's ``scale`` block to keep (the scale was not changed);
        by default it follows ``settings``.
    """
    doc: dict[str, Any] = copy.deepcopy(dict(base)) if base is not None else {}
    entry: dict[str, Any] = {"study": {"animal": None, "group": None}}
    if base is not None:
        for candidate in base["images"]:
            if candidate["image_id"] == image_id:
                entry = copy.deepcopy(candidate)
    entry.update(
        image_id=image_id,
        file_name=image.file_name,
        sha256=image.sha256,
        width_px=image.width_px,
        height_px=image.height_px,
        scale=copy.deepcopy(dict(base_scale)) if base_scale is not None else scale_block(settings),
    )
    doc.update(
        format=FORMAT_NAME,
        format_version=FORMAT_VERSION,
        sarcostat_version=__version__,
        source_digest=_source_digest(),
        created_utc=created_utc,
        annotator_id=None,
        session_id=None,
        study_id=None,
        images=[entry],
        items=[copy.deepcopy(dict(item)) for item in items],
        gui=gui_block(settings, last_mode_shape, open_myofibril),
    )
    return doc


def check_image(image: ImageInfo) -> None:
    """Refuse an image that a format v1 file cannot record (name, SHA-256 or size).

    Raises
    ------
    Refused
        With the first problem found.
    """
    doc = build_document(
        image=image,
        image_id=image_id_for(image.file_name),
        settings=SessionSettings(),
        items=(),
        last_mode_shape=True,
        open_myofibril=False,
        created_utc="2000-01-01T00:00:00Z",
    )
    try:
        validate_annotations(doc)
    except AnnotationFormatError as exc:
        raise Refused(f"The image cannot be recorded in an annotation file: {exc}") from None


__all__ = [
    "DEFAULT_UNIT",
    "LINEARITY_GUI_MODE",
    "M_LINE_SKIP_REASON",
    "NM_PER_UNIT",
    "SHAPE_GUI_MODE",
    "ImageInfo",
    "LoadedDocument",
    "SessionSettings",
    "build_document",
    "check_image",
    "dumps_document",
    "export_file_name",
    "gui_block",
    "gui_mode",
    "image_id_for",
    "nm_per_px",
    "parse_document",
    "read_document",
    "scale_block",
    "shape_item",
]
