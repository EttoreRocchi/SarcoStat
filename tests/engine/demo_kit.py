"""The study-mode demo kit, built from the two example micrographs of this repository.

A copy of ``web/scripts/make_demo_kit.py``, so that the engine tests import
nothing from outside ``tests``. The kit has six items: three
sarcomeres (one shown twice under different transforms), one chain and one
pair of myofibrils, each cropped first and then shown under one of the
eight dihedral transforms. The boxes are fixed demonstration boxes, not
curated targets.

The session file of a scripted session on this kit depends only on the
manifest geometry, the SHA-256 of the two JPEG files and the clicks, not on
the bytes of the item PNGs; the PNGs are written by Pillow and are the same
from one build to the next on one installation.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import numpy as np
from PIL import Image

from sarcostat.io.study import (
    MANIFEST_FORMAT,
    MANIFEST_VERSION,
    crop_and_transform,
    write_manifest,
)

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
IMAGES = {
    "tem_1": ("human_skeletal_muscle_tem_1.jpg", 6.62),
    "tem_3": ("human_skeletal_muscle_tem_3.jpg", 4.39),
}
MARGIN = 40

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
