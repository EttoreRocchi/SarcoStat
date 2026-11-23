"""Build a demo session kit from the two example micrographs of this repository.

The kit has six items: three sarcomeres (one shown twice under different
transforms), one chain and one pair of myofibrils, each cropped first and
then shown under one of the eight dihedral transforms. The boxes are fixed
demonstration boxes, not curated targets. Open the folder in the study mode
of the web app to try a blinded session.

With ``--fixtures`` the script writes the kits of the web app's tests instead
(:func:`make_fixture_kits`): a smaller demo kit with one item of each type,
one kit per transform with a marked pixel on a synthetic image, and four
kits whose item image is a PNG the study refuses. Only the repository's
example micrographs and synthetic images go into them.

Usage::

    python web/scripts/make_demo_kit.py OUTPUT_FOLDER
    python web/scripts/make_demo_kit.py --fixtures web/tests/fixtures/kits
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import struct
import zlib
from pathlib import Path

import numpy as np
from PIL import Image

from sarcostat.io.study import (
    MANIFEST_FORMAT,
    MANIFEST_VERSION,
    TRANSFORMS,
    DisplayMapping,
    crop_and_transform,
    write_manifest,
)

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
IMAGES = {
    "tem_1": ("human_skeletal_muscle_tem_1.jpg", 6.62),
    "tem_3": ("human_skeletal_muscle_tem_3.jpg", 4.39),
}
MARGIN = 40
"""Pixels of context around each box."""

ITEMS = [
    ("T01", "tem_3", "shape", [760, 560, 1520, 1040], "rot90"),
    ("T04", "tem_1", "chain", [1200, 80, 2080, 480], "flip_v"),
    ("T02", "tem_3", "shape", [40, 200, 760, 600], "identity"),
    ("T05", "tem_1", "pair", [1520, 600, 2160, 1080], "transpose"),
    ("T01", "tem_3", "shape", [760, 560, 1520, 1040], "anti_transpose"),
    ("T03", "tem_3", "shape", [1600, 1000, 2080, 1400], "rot180"),
]
"""``(target_id, image_id, type, ROI [x_min, y_min, x_max, y_max] in original pixels, transform)``, in session order."""


def _grey(path: Path) -> np.ndarray:
    """The micrograph as 8-bit grey (these JPEGs are 8-bit, so the window is 0-255)."""
    with Image.open(path) as img:
        return np.asarray(img.convert("L"), dtype=np.uint8)


def make_demo_kit(kit_dir: str | Path, annotator_id: str = "DEMO", session_id: str = "S1") -> Path:
    """Write the demo kit into ``kit_dir`` and return its path."""
    kit_dir = Path(kit_dir)
    (kit_dir / "items").mkdir(parents=True, exist_ok=True)
    pixels = {}
    images = []
    for image_id, (file_name, nm_per_px) in IMAGES.items():
        path = EXAMPLES / file_name
        pixels[image_id] = _grey(path)
        height, width = pixels[image_id].shape
        images.append(
            {
                "image_id": image_id,
                "file_name": file_name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "width_px": int(width),
                "height_px": int(height),
                "scale": {"nm_per_px": nm_per_px, "source": "scale bar"},
                "study": {"animal": None, "group": None},
            }
        )
    items = []
    for k, (target_id, image_id, kind, roi, transform) in enumerate(ITEMS, start=1):
        original = pixels[image_id]
        height, width = original.shape
        x0, y0 = max(0, roi[0] - MARGIN), max(0, roi[1] - MARGIN)
        x1, y1 = min(width - 1, roi[2] + MARGIN), min(height - 1, roi[3] + MARGIN)
        offset, size = [x0, y0], [x1 - x0 + 1, y1 - y0 + 1]
        buf = io.BytesIO()
        Image.fromarray(crop_and_transform(original, transform, offset, size)).save(buf, "PNG")
        data = buf.getvalue()
        rel = f"items/{k:03d}.png"
        (kit_dir / rel).write_bytes(data)
        items.append(
            {
                "item_id": f"{session_id}-{k:03d}",
                "target_id": target_id,
                "image_id": image_id,
                "type": kind,
                "kit_file": rel,
                "kit_sha256": hashlib.sha256(data).hexdigest(),
                "display_transform": transform,
                "kit_crop_offset": offset,
                "kit_crop_size": size,
                "roi": roi,
            }
        )
    manifest = {
        "format": MANIFEST_FORMAT,
        "format_version": MANIFEST_VERSION,
        "study_id": "demo",
        "annotator_id": annotator_id,
        "session_id": session_id,
        "images": images,
        "items": items,
    }
    write_manifest(manifest, kit_dir)
    return kit_dir


FIXTURE_ITEMS = [
    ("T01", "tem_3", "shape", [900, 640, 1260, 960], "rot90"),
    ("T04", "tem_1", "chain", [1300, 120, 1700, 480], "flip_v"),
    ("T05", "tem_1", "pair", [1600, 640, 1960, 1000], "transpose"),
    ("T01", "tem_3", "shape", [900, 640, 1260, 960], "anti_transpose"),
]
"""Items of the test demo kit: crops of about 440 x 400 pixels, so that fit x 1.25^3 stays far below 8."""

MARKED_SIZE = (640, 480)
MARKED_OFFSET = (37, 29)
MARKED_CROP = (403, 331)
MARKED_PIXEL = (227, 179)
MARKED_VALUES = {"background": 118, "ring": 0, "pixel": 255}
REFUSED = ("rgb", "grey16", "interlaced", "gama")
ADAM7 = (
    (0, 0, 8, 8),
    (4, 0, 8, 8),
    (0, 4, 4, 8),
    (2, 0, 4, 4),
    (0, 2, 2, 4),
    (1, 0, 2, 2),
    (0, 1, 1, 2),
)


def _png_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


def _chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def _grey_png(pixels: np.ndarray, interlace: bool = False, extra: bytes = b"") -> bytes:
    """An 8-bit greyscale PNG written by hand (filter 0), optionally Adam7-interlaced."""
    height, width = pixels.shape
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 1 if interlace else 0)
    passes = ADAM7 if interlace else ((0, 0, 1, 1),)
    raw = bytearray()
    for x0, y0, dx, dy in passes:
        sub = pixels[y0::dy, x0::dx]
        if sub.size == 0:
            continue
        for row in sub:
            raw += b"\x00" + bytes(row.tolist())
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + extra
        + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _chunk(b"IEND", b"")
    )


def _write_kit(
    kit_dir: Path,
    images: list[dict[str, object]],
    items: list[tuple[dict[str, object], bytes]],
    annotator_id: str,
    session_id: str,
    study_id: str,
) -> None:
    if kit_dir.exists():
        shutil.rmtree(kit_dir)
    (kit_dir / "items").mkdir(parents=True)
    entries = []
    for entry, data in items:
        rel = str(entry["kit_file"])
        (kit_dir / rel).write_bytes(data)
        entries.append({**entry, "kit_sha256": hashlib.sha256(data).hexdigest()})
    manifest = {
        "format": MANIFEST_FORMAT,
        "format_version": MANIFEST_VERSION,
        "study_id": study_id,
        "annotator_id": annotator_id,
        "session_id": session_id,
        "images": images,
        "items": entries,
    }
    write_manifest(manifest, kit_dir)


def _example_images() -> tuple[dict[str, np.ndarray], list[dict[str, object]]]:
    pixels = {}
    images: list[dict[str, object]] = []
    for image_id, (file_name, nm_per_px) in IMAGES.items():
        path = EXAMPLES / file_name
        pixels[image_id] = _grey(path)
        height, width = pixels[image_id].shape
        images.append(
            {
                "image_id": image_id,
                "file_name": file_name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "width_px": int(width),
                "height_px": int(height),
                "scale": {"nm_per_px": nm_per_px, "source": "scale bar"},
                "study": {"animal": None, "group": None},
            }
        )
    return pixels, images


def _crop_item(
    k: int,
    session_id: str,
    target_id: str,
    image_id: str,
    kind: str,
    roi: list[int],
    transform: str,
    original: np.ndarray,
    margin: int,
) -> tuple[dict[str, object], np.ndarray]:
    height, width = original.shape
    x0, y0 = max(0, roi[0] - margin), max(0, roi[1] - margin)
    x1, y1 = min(width - 1, roi[2] + margin), min(height - 1, roi[3] + margin)
    offset, size = [x0, y0], [x1 - x0 + 1, y1 - y0 + 1]
    entry: dict[str, object] = {
        "item_id": f"{session_id}-{k:03d}",
        "target_id": target_id,
        "image_id": image_id,
        "type": kind,
        "kit_file": f"items/{k:03d}.png",
        "display_transform": transform,
        "kit_crop_offset": offset,
        "kit_crop_size": size,
        "roi": roi,
    }
    return entry, crop_and_transform(original, transform, offset, size)


def _marked_original() -> np.ndarray:
    width, height = MARKED_SIZE
    yy, xx = np.mgrid[0:height, 0:width]
    pixels = (MARKED_VALUES["background"] + (xx // 64 + yy // 64) % 2 * 24).astype(np.uint8)
    x, y = MARKED_PIXEL
    pixels[y - 1 : y + 2, x - 1 : x + 2] = MARKED_VALUES["ring"]
    pixels[y, x] = MARKED_VALUES["pixel"]
    return pixels


def make_fixture_kits(root: str | Path) -> Path:
    """Write the kits of the web app's tests into ``root`` and return it."""
    root = Path(root)
    pixels, images = _example_images()
    demo = []
    for k, (target_id, image_id, kind, roi, transform) in enumerate(FIXTURE_ITEMS, start=1):
        entry, data = _crop_item(
            k, "S1", target_id, image_id, kind, roi, transform, pixels[image_id], 40
        )
        demo.append((entry, _png_bytes(Image.fromarray(data))))
    _write_kit(root / "demo", images, demo, "A1", "S1", "fixture")

    original = _marked_original()
    original_png = _png_bytes(Image.fromarray(original))
    marked_image = {
        "image_id": "marked",
        "file_name": "marked_original.png",
        "sha256": hashlib.sha256(original_png).hexdigest(),
        "width_px": MARKED_SIZE[0],
        "height_px": MARKED_SIZE[1],
        "scale": {"nm_per_px": None, "source": None},
        "study": {"animal": None, "group": None},
    }
    x, y = MARKED_PIXEL
    marked_kits = {}
    for transform in TRANSFORMS:
        entry = {
            "item_id": f"M-{transform.replace('_', '-')}",
            "target_id": "marked",
            "image_id": "marked",
            "type": "shape",
            "kit_file": "items/marked.png",
            "display_transform": transform,
            "kit_crop_offset": list(MARKED_OFFSET),
            "kit_crop_size": list(MARKED_CROP),
            "roi": [x - 40, y - 30, x + 40, y + 30],
        }
        data = crop_and_transform(original, transform, MARKED_OFFSET, MARKED_CROP)
        _write_kit(
            root / "marked" / transform,
            [marked_image],
            [(entry, _png_bytes(Image.fromarray(data)))],
            "A1",
            f"M{TRANSFORMS.index(transform) + 1}",
            "marked",
        )
        shown = DisplayMapping.from_item(entry).to_display([[x, y]]).tolist()[0]
        marked_kits[transform] = {
            "display": shown,
            "display_size": list(DisplayMapping.from_item(entry).display_size),
        }
    description = {
        "original_size": list(MARKED_SIZE),
        "original_png_sha256": hashlib.sha256(original_png).hexdigest(),
        "marked": {"x": x, "y": y},
        "values": MARKED_VALUES,
        "crop_offset": list(MARKED_OFFSET),
        "crop_size": list(MARKED_CROP),
        "kits": marked_kits,
    }
    (root / "marked" / "marked.json").write_text(
        json.dumps(description, indent=2, sort_keys=True) + "\n"
    )

    small = pixels["tem_3"][200:248, 200:264]
    variants = {
        "rgb": _png_bytes(Image.fromarray(small).convert("RGB")),
        "grey16": _png_bytes(Image.fromarray(small.astype(np.uint16) * 257)),
        "interlaced": _grey_png(small, interlace=True),
        "gama": _grey_png(small, extra=_chunk(b"gAMA", struct.pack(">I", 45455))),
    }
    for name in REFUSED:
        entry = {
            "item_id": f"R-{name}",
            "target_id": "R",
            "image_id": "tem_3",
            "type": "shape",
            "kit_file": "items/001.png",
            "display_transform": "identity",
            "kit_crop_offset": [200, 200],
            "kit_crop_size": [64, 48],
            "roi": [210, 210, 250, 240],
        }
        _write_kit(
            root / "refused" / name, images, [(entry, variants[name])], "A1", "S1", "refused"
        )
    return root


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "kit_dir", type=Path, help="folder to write the kit (or the test kits) into"
    )
    parser.add_argument("--fixtures", action="store_true", help="write the kits of the web tests")
    args = parser.parse_args()
    if args.fixtures:
        print(f"Test kits written to {make_fixture_kits(args.kit_dir)}")
        return
    print(f"Demo kit written to {make_demo_kit(args.kit_dir)}")


if __name__ == "__main__":
    main()
