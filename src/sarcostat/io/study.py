"""Session manifest v1 and the dihedral display transforms of blinded study sessions.

A *session kit* is one folder per annotator and session::

    kit/
        manifest.json      session manifest v1 (this module)
        manifest.sha256    SHA-256 of manifest.json, ``sha256sum`` format
        items/...          one 8-bit greyscale PNG per item, listed in the manifest

Each item image is a lossless crop of an original image, cropped first and
then shown under one of the eight dihedral transforms (rotations by multiples
of 90 degrees and reflections), so a repeated target does not look the same
twice. Coordinates flow original -> crop -> transform -> display; the inverse
runs the other way. Pixel centres sit at integer coordinates, so a horizontal
flip of a crop ``W`` pixels wide maps ``x`` to ``W - 1 - x``.

This module is the only implementation of these transforms: the kit maker and
the study engine both use it. Image transforms are numpy rotations and flips
(no interpolation), so they are lossless. Point transforms are exact integer
maps; display clicks are rounded to :data:`CLICK_RESOLUTION` (an exact binary
fraction of a pixel), on which grid every forward and inverse map is exact in
floating point.

A study session reads only the files listed in the manifest
(:meth:`SessionKit.read_file`), after checking ``manifest.sha256``. A kit is
read from a folder (:func:`load_session_kit`) or from the bytes of its files
(:func:`load_session_kit_from_bytes`, for a browser) with the same checks.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import struct
import tempfile
import zlib
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import numpy as np
from numpy.typing import ArrayLike

MANIFEST_FORMAT = "sarcostat-session-manifest"
MANIFEST_VERSION = "1.0"
MANIFEST_FILE = "manifest.json"
MANIFEST_SHA256_FILE = "manifest.sha256"

ITEM_TYPES = ("shape", "chain", "pair")

TRANSFORMS = (
    "identity",
    "rot90",
    "rot180",
    "rot270",
    "flip_h",
    "flip_v",
    "transpose",
    "anti_transpose",
)

INVERSE_TRANSFORM = {
    "identity": "identity",
    "rot90": "rot270",
    "rot180": "rot180",
    "rot270": "rot90",
    "flip_h": "flip_h",
    "flip_v": "flip_v",
    "transpose": "transpose",
    "anti_transpose": "anti_transpose",
}

CLICK_RESOLUTION = 1.0 / 1024.0

_SWAPS_AXES = frozenset({"rot90", "rot270", "transpose", "anti_transpose"})
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_VERSION_RE = re.compile(r"^1\.(0|[1-9][0-9]*)$")


class StudyError(ValueError):
    """An invalid session kit, manifest or transform, or a refused read."""


_FORWARD: dict[str, Callable[[np.ndarray, np.ndarray, int, int], tuple[Any, Any]]] = {
    "identity": lambda x, y, w, h: (x, y),
    "rot90": lambda x, y, w, h: (y, (w - 1) - x),
    "rot180": lambda x, y, w, h: ((w - 1) - x, (h - 1) - y),
    "rot270": lambda x, y, w, h: ((h - 1) - y, x),
    "flip_h": lambda x, y, w, h: ((w - 1) - x, y),
    "flip_v": lambda x, y, w, h: (x, (h - 1) - y),
    "transpose": lambda x, y, w, h: (y, x),
    "anti_transpose": lambda x, y, w, h: ((h - 1) - y, (w - 1) - x),
}

_IMAGE_OPS: dict[str, Callable[[np.ndarray], np.ndarray]] = {
    "identity": lambda a: a,
    "rot90": lambda a: np.rot90(a, 1),
    "rot180": lambda a: np.rot90(a, 2),
    "rot270": lambda a: np.rot90(a, 3),
    "flip_h": lambda a: a[:, ::-1],
    "flip_v": lambda a: a[::-1, :],
    "transpose": lambda a: a.swapaxes(0, 1),
    "anti_transpose": lambda a: np.rot90(a, 2).swapaxes(0, 1),
}


def _check_transform(name: str) -> None:
    if name not in _FORWARD:
        raise StudyError(f"Unknown display_transform {name!r}; expected one of {TRANSFORMS}.")


def _size(size: ArrayLike) -> tuple[int, int]:
    w, h = (int(v) for v in np.asarray(size).tolist())
    if w <= 0 or h <= 0:
        raise StudyError(f"Crop size must be positive, got {(w, h)}.")
    return w, h


def transformed_size(name: str, size: ArrayLike) -> tuple[int, int]:
    """Return the display size ``(w', h')`` of a ``(w, h)`` crop under transform ``name``."""
    _check_transform(name)
    w, h = _size(size)
    return (h, w) if name in _SWAPS_AXES else (w, h)


def _as_points(points: ArrayLike) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float64)
    if pts.ndim == 0 or pts.shape[-1] != 2:
        raise StudyError(f"Points must have shape (..., 2), got {pts.shape}.")
    return pts


def transform_points(points: ArrayLike, name: str, size: ArrayLike) -> np.ndarray:
    """Map points from the crop frame to the display frame.

    Parameters
    ----------
    points : array_like, shape (..., 2)
        ``(x, y)`` coordinates in the crop, pixel centres at integers.
    name : str
        One of :data:`TRANSFORMS`.
    size : array_like of int
        Crop size ``(w, h)``.

    Returns
    -------
    numpy.ndarray
        Float64 ``(x', y')`` coordinates in the display frame.
    """
    _check_transform(name)
    w, h = _size(size)
    pts = _as_points(points)
    x, y = _FORWARD[name](pts[..., 0], pts[..., 1], w, h)
    out: np.ndarray = np.stack([x, y], axis=-1).astype(np.float64)
    return out


def inverse_transform_points(points: ArrayLike, name: str, size: ArrayLike) -> np.ndarray:
    """Map points from the display frame back to the crop frame (``size`` is the crop size)."""
    return transform_points(points, INVERSE_TRANSFORM[_known(name)], transformed_size(name, size))


def _known(name: str) -> str:
    _check_transform(name)
    return name


def transform_image(image: np.ndarray, name: str) -> np.ndarray:
    """Apply transform ``name`` to an image array (rows are y); returns a new array."""
    _check_transform(name)
    result: np.ndarray = np.ascontiguousarray(_IMAGE_OPS[name](np.asarray(image))).copy()
    return result


def inverse_transform_image(image: np.ndarray, name: str) -> np.ndarray:
    """Undo :func:`transform_image`."""
    return transform_image(image, INVERSE_TRANSFORM[_known(name)])


def crop_image(image: np.ndarray, offset: ArrayLike, size: ArrayLike) -> np.ndarray:
    """Return the lossless crop ``image[y0:y0+h, x0:x0+w]``; it must lie inside the image."""
    x0, y0 = (int(v) for v in np.asarray(offset).tolist())
    w, h = _size(size)
    img = np.asarray(image)
    if x0 < 0 or y0 < 0 or x0 + w > img.shape[1] or y0 + h > img.shape[0]:
        raise StudyError(
            f"Crop at {(x0, y0)} of size {(w, h)} falls outside the "
            f"{img.shape[1]} x {img.shape[0]} image."
        )
    crop: np.ndarray = img[y0 : y0 + h, x0 : x0 + w].copy()
    return crop


def crop_and_transform(
    image: np.ndarray, name: str, offset: ArrayLike, size: ArrayLike
) -> np.ndarray:
    """Crop first, then transform: the image counterpart of :meth:`DisplayMapping.to_display`."""
    return transform_image(crop_image(image, offset, size), name)


def quantize_coordinate(value: float) -> float:
    """Round a display coordinate to the :data:`CLICK_RESOLUTION` grid."""
    v = float(value)
    if not math.isfinite(v):
        raise StudyError(f"Coordinate must be finite, got {value!r}.")
    return round(v / CLICK_RESOLUTION) * CLICK_RESOLUTION


@dataclass(frozen=True)
class DisplayMapping:
    """How one item is shown: crop ``crop_size`` at ``crop_offset``, then ``transform``.

    Attributes
    ----------
    transform : str
        One of :data:`TRANSFORMS`.
    crop_offset : tuple of int
        ``(x0, y0)`` of the crop in the original image (``kit_crop_offset``).
    crop_size : tuple of int
        ``(w, h)`` of the crop (``kit_crop_size``).
    """

    transform: str
    crop_offset: tuple[int, int]
    crop_size: tuple[int, int]

    def __post_init__(self) -> None:
        _check_transform(self.transform)
        _size(self.crop_size)

    @classmethod
    def from_item(cls, item: Mapping[str, Any]) -> DisplayMapping:
        """Build the mapping of a manifest (or annotation v1) item."""
        x0, y0 = item["kit_crop_offset"]
        w, h = item["kit_crop_size"]
        return cls(str(item["display_transform"]), (int(x0), int(y0)), (int(w), int(h)))

    @property
    def display_size(self) -> tuple[int, int]:
        """Size ``(w', h')`` of the displayed item image."""
        return transformed_size(self.transform, self.crop_size)

    def to_display(self, points: ArrayLike) -> np.ndarray:
        """Map original-image pixel coordinates to display coordinates."""
        pts: np.ndarray = _as_points(points) - np.asarray(self.crop_offset, dtype=np.float64)
        return transform_points(pts, self.transform, self.crop_size)

    def to_original(self, points: ArrayLike) -> np.ndarray:
        """Map display coordinates to original-image pixel coordinates."""
        crop = inverse_transform_points(points, self.transform, self.crop_size)
        return crop + np.asarray(self.crop_offset, dtype=np.float64)

    def roi_to_display(self, roi: ArrayLike) -> tuple[float, float, float, float]:
        """Map an ROI box ``[x_min, y_min, x_max, y_max]`` to the display frame."""
        x_min, y_min, x_max, y_max = (float(v) for v in np.asarray(roi).tolist())
        corners = self.to_display([[x_min, y_min], [x_max, y_max]])
        lo, hi = corners.min(axis=0), corners.max(axis=0)
        return float(lo[0]), float(lo[1]), float(hi[0]), float(hi[1])


def dumps_canonical(doc: Any) -> bytes:
    """Serialise ``doc`` as canonical JSON bytes (sorted keys, 2-space indent, ASCII, trailing newline).

    Raises
    ------
    StudyError
        If ``doc`` contains NaN or infinity.
    """
    try:
        text = json.dumps(
            doc,
            sort_keys=True,
            indent=2,
            separators=(",", ": "),
            ensure_ascii=True,
            allow_nan=False,
        )
    except ValueError as exc:
        raise StudyError(f"Cannot write non-finite numbers: {exc}") from exc
    return (text + "\n").encode("ascii")


def _reject_constant(name: str) -> Any:
    raise StudyError(f"Non-finite number {name} is not allowed.")


def loads_strict(data: bytes) -> Any:
    """Parse JSON bytes, refusing NaN and infinity."""
    try:
        return json.loads(data.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StudyError(f"Not valid JSON: {exc}") from exc


def write_bytes_atomic(data: bytes, path: Path) -> None:
    """Write ``data`` to ``path`` atomically (temporary file in the same folder, then rename)."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_bytes_exclusive(data: bytes, path: Path) -> None:
    """Write ``data`` to the new file ``path`` atomically, never replacing an existing file.

    The data go to a temporary file in the same folder, which is then
    hard-linked to ``path`` (the link fails if ``path`` exists). On file
    systems without hard links, and where :func:`os.link` does not exist
    (Pyodide), the temporary file is renamed after a check: the content
    still appears at once and complete, and an existing file is refused.

    Raises
    ------
    StudyError
        If ``path`` already exists.
    """
    path = Path(path)
    refusal = f"{path.name} already exists; a finished session file cannot be overwritten."
    if path.exists():
        raise StudyError(refusal)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError as exc:
            raise StudyError(refusal) from exc
        except (OSError, AttributeError):
            if path.exists():
                raise StudyError(refusal) from None
            os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def sha256_bytes(data: bytes) -> str:
    """Lower-case hex SHA-256 of ``data``."""
    return hashlib.sha256(data).hexdigest()


_TOP_KEYS = frozenset(
    {"format", "format_version", "study_id", "annotator_id", "session_id", "images", "items"}
)
_IMAGE_KEYS = frozenset(
    {"image_id", "file_name", "sha256", "width_px", "height_px", "scale", "study"}
)
_ITEM_KEYS = frozenset(
    {
        "item_id",
        "target_id",
        "image_id",
        "type",
        "kit_file",
        "kit_sha256",
        "display_transform",
        "kit_crop_offset",
        "kit_crop_size",
        "roi",
    }
)


def _keys(obj: Any, expected: frozenset[str], where: str) -> None:
    if not isinstance(obj, dict):
        raise StudyError(f"{where} must be an object.")
    missing = sorted(expected - obj.keys())
    unknown = sorted(obj.keys() - expected)
    if missing:
        raise StudyError(f"{where} is missing {missing}.")
    if unknown:
        raise StudyError(f"{where} has unknown keys {unknown}.")


def _identifier(value: Any, where: str) -> str:
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise StudyError(
            f"{where} must match [A-Za-z0-9][A-Za-z0-9._-]* (at most 64 characters), got {value!r}."
        )
    return value


def _int(value: Any, where: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise StudyError(f"{where} must be an integer >= {minimum}, got {value!r}.")
    return int(value)


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise StudyError(f"{where} must be a finite number, got {value!r}.")
    return float(value)


def _optional_str(value: Any, where: str) -> None:
    if value is not None and not isinstance(value, str):
        raise StudyError(f"{where} must be a string or null.")


def _int_pair(value: Any, where: str, minimum: int) -> tuple[int, int]:
    if not isinstance(value, list) or len(value) != 2:
        raise StudyError(f"{where} must be a list of two integers.")
    return _int(value[0], where, minimum), _int(value[1], where, minimum)


def _sha256(value: Any, where: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.match(value):
        raise StudyError(f"{where} must be 64 lower-case hex characters.")
    return value


def _kit_file(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise StudyError(f"{where} must be a relative POSIX path.")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts or str(path) != value:
        raise StudyError(
            f"{where} must be a normalised relative path inside the kit, got {value!r}."
        )
    if value in (MANIFEST_FILE, MANIFEST_SHA256_FILE):
        raise StudyError(f"{where} cannot be the manifest itself.")
    return value


def _validate_image(image: Any, where: str) -> tuple[str, int, int]:
    _keys(image, _IMAGE_KEYS, where)
    image_id = _identifier(image["image_id"], f"{where}.image_id")
    name = image["file_name"]
    if not isinstance(name, str) or not name or "/" in name or "\\" in name or name in (".", ".."):
        raise StudyError(f"{where}.file_name must be a bare file name.")
    _sha256(image["sha256"], f"{where}.sha256")
    width = _int(image["width_px"], f"{where}.width_px", 1)
    height = _int(image["height_px"], f"{where}.height_px", 1)
    scale = image["scale"]
    _keys(scale, frozenset({"nm_per_px", "source"}), f"{where}.scale")
    if scale["nm_per_px"] is not None and _number(scale["nm_per_px"], f"{where}.scale") <= 0:
        raise StudyError(f"{where}.scale.nm_per_px must be positive or null.")
    _optional_str(scale["source"], f"{where}.scale.source")
    info = image["study"]
    _keys(info, frozenset({"animal", "group"}), f"{where}.study")
    _optional_str(info["animal"], f"{where}.study.animal")
    _optional_str(info["group"], f"{where}.study.group")
    return image_id, width, height


def _validate_item(item: Any, where: str, images: Mapping[str, tuple[int, int]]) -> None:
    _keys(item, _ITEM_KEYS, where)
    _identifier(item["item_id"], f"{where}.item_id")
    _identifier(item["target_id"], f"{where}.target_id")
    image_id = item["image_id"]
    if image_id not in images:
        raise StudyError(f"{where}.image_id {image_id!r} is not in images.")
    if item["type"] not in ITEM_TYPES:
        raise StudyError(f"{where}.type must be one of {ITEM_TYPES}, got {item['type']!r}.")
    _kit_file(item["kit_file"], f"{where}.kit_file")
    _sha256(item["kit_sha256"], f"{where}.kit_sha256")
    if item["display_transform"] not in _FORWARD:
        raise StudyError(f"{where}.display_transform must be one of {TRANSFORMS}.")
    x0, y0 = _int_pair(item["kit_crop_offset"], f"{where}.kit_crop_offset", 0)
    w, h = _int_pair(item["kit_crop_size"], f"{where}.kit_crop_size", 1)
    width, height = images[image_id]
    if x0 + w > width or y0 + h > height:
        raise StudyError(f"{where}: the crop falls outside the {width} x {height} image.")
    roi = item["roi"]
    if not isinstance(roi, list) or len(roi) != 4:
        raise StudyError(f"{where}.roi must be [x_min, y_min, x_max, y_max].")
    x_min, y_min, x_max, y_max = (_number(v, f"{where}.roi") for v in roi)
    if not (x_min < x_max and y_min < y_max):
        raise StudyError(f"{where}.roi must have x_min < x_max and y_min < y_max.")
    if x_min < x0 or y_min < y0 or x_max > x0 + w - 1 or y_max > y0 + h - 1:
        raise StudyError(f"{where}.roi must lie inside the crop.")


def validate_manifest(doc: Any) -> None:
    """Check a session manifest v1; raise :class:`StudyError` on the first problem.

    The manifest is strict: unknown keys are refused, so nothing that was not
    vetted can reach the annotator's screen.
    """
    _keys(doc, _TOP_KEYS, "manifest")
    if doc["format"] != MANIFEST_FORMAT:
        raise StudyError(f"manifest.format must be {MANIFEST_FORMAT!r}.")
    version = doc["format_version"]
    if not isinstance(version, str) or not _VERSION_RE.match(version):
        raise StudyError(f"Unsupported manifest format_version {version!r}; this reader needs 1.x.")
    for key in ("study_id", "annotator_id", "session_id"):
        _identifier(doc[key], f"manifest.{key}")

    images = doc["images"]
    if not isinstance(images, list) or not images:
        raise StudyError("manifest.images must be a non-empty list.")
    sizes: dict[str, tuple[int, int]] = {}
    names: set[str] = set()
    for k, image in enumerate(images):
        image_id, width, height = _validate_image(image, f"images[{k}]")
        if image_id in sizes:
            raise StudyError(f"Duplicate image_id {image_id!r}.")
        if image["file_name"] in names:
            raise StudyError(f"Duplicate file_name {image['file_name']!r}.")
        sizes[image_id] = (width, height)
        names.add(image["file_name"])

    items = doc["items"]
    if not isinstance(items, list) or not items:
        raise StudyError("manifest.items must be a non-empty list.")
    item_ids: set[str] = set()
    kit_sha: dict[str, str] = {}
    for k, item in enumerate(items):
        _validate_item(item, f"items[{k}]", sizes)
        if item["item_id"] in item_ids:
            raise StudyError(f"Duplicate item_id {item['item_id']!r}.")
        item_ids.add(item["item_id"])
        if kit_sha.setdefault(item["kit_file"], item["kit_sha256"]) != item["kit_sha256"]:
            raise StudyError(
                f"{item['kit_file']!r} is listed with two different kit_sha256 values."
            )


def write_manifest(doc: Mapping[str, Any], kit_dir: str | Path) -> Path:
    """Validate ``doc`` and write ``manifest.json`` (canonical) and ``manifest.sha256`` into ``kit_dir``.

    Returns
    -------
    pathlib.Path
        Path of the written ``manifest.json``.
    """
    validate_manifest(doc)
    data = dumps_canonical(doc)
    kit_dir = Path(kit_dir)
    path = kit_dir / MANIFEST_FILE
    write_bytes_atomic(data, path)
    sidecar = f"{sha256_bytes(data)}  {MANIFEST_FILE}\n".encode("ascii")
    write_bytes_atomic(sidecar, kit_dir / MANIFEST_SHA256_FILE)
    return path


def _sidecar_digest(text: str) -> str:
    fields = text.split()
    if len(fields) == 2 and fields[1].lstrip("*") == MANIFEST_FILE:
        fields = fields[:1]
    if len(fields) != 1 or not _SHA256_RE.match(fields[0].lower()):
        raise StudyError(f"{MANIFEST_SHA256_FILE} must hold the SHA-256 of {MANIFEST_FILE}.")
    return fields[0].lower()


class KitSource(ABC):
    """Read access to the files of one session kit, by kit-relative POSIX path.

    :class:`FolderKitSource` reads a folder on disk; :class:`MappingKitSource`
    holds the bytes of the files, for example those a browser read from a
    chosen folder. The loaders and :meth:`SessionKit.read_file` check both
    in the same way.
    """

    @property
    @abstractmethod
    def label(self) -> str:
        """Name of the kit in messages."""

    @abstractmethod
    def read_bytes(self, relpath: str) -> bytes:
        """Return the bytes of ``relpath``; raise :class:`FileNotFoundError` if it is missing."""


class FolderKitSource(KitSource):
    """The files of a kit folder; a path that resolves outside the folder is refused."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    @property
    def label(self) -> str:
        """Name of the kit folder."""
        return self.directory.name

    def read_bytes(self, relpath: str) -> bytes:
        """Read ``relpath`` inside the folder.

        Raises
        ------
        StudyError
            If the path resolves outside the kit folder.
        """
        root = self.directory.resolve()
        path = (self.directory / relpath).resolve()
        if root not in path.parents:
            raise StudyError(f"{relpath!r} resolves outside the kit folder; refusing to read it.")
        return path.read_bytes()


class MappingKitSource(KitSource):
    """Kit files held in memory, as a mapping from kit-relative POSIX path to bytes.

    The bytes are copied when the source is built, so later changes to the
    caller's buffers cannot reach a verified kit.
    """

    def __init__(
        self, files: Mapping[str, bytes | bytearray | memoryview], label: str = "the kit"
    ) -> None:
        self._files = {str(name): bytes(data) for name, data in files.items()}
        self._label = str(label)

    @property
    def label(self) -> str:
        """Name of the kit given by the caller."""
        return self._label

    def read_bytes(self, relpath: str) -> bytes:
        """Return the bytes held for ``relpath``."""
        try:
            return self._files[relpath]
        except KeyError:
            raise FileNotFoundError(relpath) from None


@dataclass(frozen=True)
class SessionKit:
    """A loaded, verified session kit.

    Use :func:`load_session_kit` or :func:`load_session_kit_from_bytes` to
    build one. Every later read goes through :meth:`read_file`, which
    refuses any file the manifest does not list.

    Attributes
    ----------
    directory : pathlib.Path or None
        Kit folder, or None for a kit loaded from bytes.
    manifest : dict
        The validated manifest.
    manifest_sha256 : str
        SHA-256 of ``manifest.json``, as checked against ``manifest.sha256``.
    source : KitSource or None
        Where the item files are read from; None reads ``directory``.
    """

    directory: Path | None
    manifest: dict[str, Any]
    manifest_sha256: str
    source: KitSource | None = field(default=None, repr=False, compare=False)

    def _reader(self) -> KitSource:
        if self.source is not None:
            return self.source
        if self.directory is None:
            raise StudyError("This kit has neither a folder nor file bytes to read from.")
        return FolderKitSource(self.directory)

    @property
    def items(self) -> list[dict[str, Any]]:
        """Manifest items, in session order."""
        return list(self.manifest["items"])

    @property
    def whitelist(self) -> frozenset[str]:
        """Kit-relative paths this session may read."""
        return frozenset(item["kit_file"] for item in self.manifest["items"])

    def read_file(self, relpath: str) -> bytes:
        """Read a file listed in the manifest and check its SHA-256.

        Raises
        ------
        StudyError
            If the path is not listed, resolves outside the kit, or its
            content does not match ``kit_sha256``.
        """
        if relpath not in self.whitelist:
            raise StudyError(f"{relpath!r} is not listed in the manifest; refusing to read it.")
        data = self._reader().read_bytes(relpath)
        expected = next(i["kit_sha256"] for i in self.manifest["items"] if i["kit_file"] == relpath)
        if sha256_bytes(data) != expected:
            raise StudyError(f"SHA-256 of {relpath!r} does not match the manifest.")
        return data


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_ALLOWED_CHUNKS = frozenset(
    {b"IHDR", b"IDAT", b"IEND", b"pHYs", b"tIME", b"tEXt", b"zTXt", b"iTXt"}
)


def _png_chunks(data: bytes, name: str) -> list[tuple[bytes, bytes]]:
    if not data.startswith(_PNG_SIGNATURE):
        raise StudyError(f"{name} is not a PNG file.")
    chunks: list[tuple[bytes, bytes]] = []
    pos = len(_PNG_SIGNATURE)
    while pos < len(data):
        if pos + 12 > len(data):
            raise StudyError(f"{name} is truncated.")
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        kind = data[pos + 4 : pos + 8]
        end = pos + 12 + length
        if end > len(data):
            raise StudyError(f"{name} is truncated.")
        body = data[pos + 8 : end - 4]
        (crc,) = struct.unpack(">I", data[end - 4 : end])
        if zlib.crc32(kind + body) != crc:
            raise StudyError(f"{name} has a damaged {kind.decode('latin-1')!r} chunk.")
        chunks.append((kind, body))
        pos = end
        if kind == b"IEND":
            break
    if pos != len(data) or not chunks or chunks[-1][0] != b"IEND":
        raise StudyError(f"{name} must end with its IEND chunk.")
    return chunks


def check_item_image(item: Mapping[str, Any], data: bytes) -> None:
    """Check that ``data`` is a plain 8-bit greyscale PNG of the display size of ``item``.

    The browser draws the kit image from these bytes, so they must decode to the
    same grey values everywhere: bit depth 8, colour type 0, no interlacing,
    and no colour, gamma or transparency chunk (``gAMA``, ``iCCP``, ``sRGB``,
    ``cHRM`` and any other chunk outside IHDR, IDAT, IEND, pHYs, tIME and
    the text chunks are refused). Only the standard library is used.

    Raises
    ------
    StudyError
        On the first problem, with a one-line message.
    """
    name = repr(str(item["kit_file"]))
    chunks = _png_chunks(bytes(data), name)
    kind, header = chunks[0]
    if kind != b"IHDR" or len(header) != 13:
        raise StudyError(f"{name} must start with a 13-byte IHDR chunk.")
    width, height, depth, colour, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", header
    )
    if depth != 8 or colour != 0:
        raise StudyError(
            f"{name} must be an 8-bit greyscale PNG, got bit depth {depth} and colour type {colour}."
        )
    if interlace != 0:
        raise StudyError(f"{name} must not be interlaced.")
    if compression != 0 or filtering != 0:
        raise StudyError(f"{name} uses an unknown PNG compression or filter method.")
    expected = DisplayMapping.from_item(item).display_size
    if (width, height) != expected:
        raise StudyError(
            f"{name} has size {width} x {height}; the manifest implies {expected[0]} x {expected[1]}."
        )
    kinds = [k for k, _ in chunks]
    for kind in kinds:
        if kind not in _PNG_ALLOWED_CHUNKS:
            raise StudyError(
                f"{name} has a {kind.decode('latin-1')!r} chunk; a kit image may hold only "
                "IHDR, IDAT, IEND, pHYs, tIME and text chunks (no colour, gamma or transparency data)."
            )
    if kinds.count(b"IHDR") != 1:
        raise StudyError(f"{name} must hold exactly one IHDR chunk.")
    runs = kinds.count(b"IDAT")
    first = kinds.index(b"IDAT") if runs else 0
    if not runs or kinds[first : first + runs] != [b"IDAT"] * runs:
        raise StudyError(f"{name} must hold its image data in consecutive IDAT chunks.")
    stride = width + 1
    size = height * stride
    inflater = zlib.decompressobj()
    try:
        raw = inflater.decompress(b"".join(body for k, body in chunks if k == b"IDAT"), size + 1)
    except zlib.error as exc:
        raise StudyError(f"{name} has damaged image data.") from exc
    if len(raw) != size or not inflater.eof or max(raw[::stride]) > 4:
        raise StudyError(f"{name} has image data that do not match its {width} x {height} size.")


def _load_from_source(source: KitSource, directory: Path | None) -> SessionKit:
    try:
        expected = _sidecar_digest(source.read_bytes(MANIFEST_SHA256_FILE).decode("ascii"))
    except FileNotFoundError as exc:
        raise StudyError(f"{MANIFEST_SHA256_FILE} is missing from {source.label}.") from exc
    except UnicodeDecodeError as exc:
        raise StudyError(f"{MANIFEST_SHA256_FILE} is not a text file.") from exc
    try:
        data = source.read_bytes(MANIFEST_FILE)
    except FileNotFoundError as exc:
        raise StudyError(f"{MANIFEST_FILE} is missing from {source.label}.") from exc
    digest = sha256_bytes(data)
    if digest != expected:
        raise StudyError(f"SHA-256 of {MANIFEST_FILE} does not match {MANIFEST_SHA256_FILE}.")
    doc = loads_strict(data)
    validate_manifest(doc)
    return SessionKit(directory=directory, manifest=doc, manifest_sha256=digest, source=source)


def load_session_kit(kit_dir: str | Path) -> SessionKit:
    """Load a session kit: check ``manifest.sha256``, then validate the manifest.

    Raises
    ------
    StudyError
        If the sidecar is missing or does not match, or the manifest is invalid.
    """
    kit_dir = Path(kit_dir)
    return _load_from_source(FolderKitSource(kit_dir), kit_dir)


def load_session_kit_from_bytes(
    files: Mapping[str, bytes | bytearray | memoryview], label: str = "the kit"
) -> SessionKit:
    """Load a session kit from the bytes of its files, with the checks of :func:`load_session_kit`.

    Parameters
    ----------
    files : mapping of str to bytes
        Kit-relative POSIX paths (``manifest.json``, ``manifest.sha256``,
        ``items/...``) to file contents. Item files are checked against the
        manifest when :meth:`SessionKit.read_file` reads them.
    label : str
        Name of the kit in messages, for example the folder name.

    Raises
    ------
    StudyError
        If the sidecar is missing or does not match, or the manifest is invalid.
    """
    return _load_from_source(MappingKitSource(files, label), None)


__all__ = [
    "CLICK_RESOLUTION",
    "INVERSE_TRANSFORM",
    "ITEM_TYPES",
    "MANIFEST_FILE",
    "MANIFEST_FORMAT",
    "MANIFEST_SHA256_FILE",
    "MANIFEST_VERSION",
    "TRANSFORMS",
    "DisplayMapping",
    "FolderKitSource",
    "KitSource",
    "MappingKitSource",
    "SessionKit",
    "StudyError",
    "check_item_image",
    "crop_and_transform",
    "crop_image",
    "dumps_canonical",
    "inverse_transform_image",
    "inverse_transform_points",
    "load_session_kit",
    "load_session_kit_from_bytes",
    "loads_strict",
    "quantize_coordinate",
    "sha256_bytes",
    "transform_image",
    "transform_points",
    "transformed_size",
    "validate_manifest",
    "write_bytes_atomic",
    "write_bytes_exclusive",
    "write_manifest",
]
