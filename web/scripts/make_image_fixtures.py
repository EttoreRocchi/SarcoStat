"""Write the small images that the web app's tests open, and their manifest.

Run with ``~/.venvs/sarcostat/bin/python web/scripts/make_image_fixtures.py
[OUTPUT_FOLDER]``; the files are written into ``web/tests/fixtures/images/``
by default and are byte-for-byte reproducible with the same Pillow and zlib.

The two ``grey_*`` PNGs are written with the standard library only. The other
fixtures are written with Pillow: 8-bit greyscale and RGB PNGs, greyscale JPEGs
with the EXIF orientation 1 and 6, a 16-bit greyscale PNG and a PNG with one
marked pixel. ``manifest.json`` records, for every file, the stored-grid pixel
array that Pillow decodes without ``exif_transpose`` (base64 of
``Image.tobytes()``), the size, the bit depth, the EXIF orientation and the
SHA-256 from :func:`sarcostat.io.annotations.sha256_file`.
"""

from __future__ import annotations

import base64
import io
import json
import struct
import sys
import zlib
from collections.abc import Callable
from pathlib import Path

import PIL
from PIL import Image

from sarcostat.io.annotations import sha256_file

OUT_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "images"
MANIFEST = "manifest.json"
ORIENTATION_TAG = 0x0112
MARKED = {"x": 37, "y": 22, "value": 255, "background": 48}


def _chunk(kind: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(kind + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)


def grey_png(width: int, height: int, value_at: Callable[[int, int], int]) -> bytes:
    """An 8-bit greyscale PNG whose pixel (x, y) is ``value_at(x, y)``."""
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        rows.extend(value_at(x, y) & 0xFF for x in range(width))
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + _chunk(b"IEND", b"")
    )


STDLIB_FIXTURES = {
    "grey_64x48.png": (64, 48, lambda x, y: 40 + (x * 3 + y * 2) % 160),
    "grey_32x40.png": (32, 40, lambda x, y: 200 - (x + y) % 120),
}


def _image(
    mode: str, width: int, height: int, value_at: Callable[[int, int], object]
) -> Image.Image:
    image = Image.new(mode, (width, height))
    image.putdata([value_at(x, y) for y in range(height) for x in range(width)])
    return image


def _png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, format="PNG", compress_level=9, optimize=False)
    return out.getvalue()


def _jpeg(image: Image.Image, orientation: int) -> bytes:
    exif = Image.Exif()
    exif[ORIENTATION_TAG] = orientation
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=100, exif=exif.tobytes())
    return out.getvalue()


def _block_grey(seed: int) -> Callable[[int, int], int]:
    """Uniform 8 x 8 blocks, so every JPEG decoder gives the same values."""

    def value_at(x: int, y: int) -> int:
        return 16 + (seed + 37 * (x // 8) + 71 * (y // 8)) % 211

    return value_at


def pillow_fixtures() -> dict[str, bytes]:
    """The fixtures written with Pillow, by file name."""
    marked = MARKED

    def marked_at(x: int, y: int) -> int:
        return marked["value"] if (x, y) == (marked["x"], marked["y"]) else marked["background"]

    return {
        "png_grey8_37x29.png": _png(
            _image("L", 37, 29, lambda x, y: (x * 7 + y * 29 + (x * y) % 11) % 256)
        ),
        "png_rgb8_31x22.png": _png(
            _image(
                "RGB",
                31,
                22,
                lambda x, y: ((x * 8 + y) % 256, (y * 11 + x * 3) % 256, (x * y * 5 + 40) % 256),
            )
        ),
        "jpeg_grey_o1_40x24.jpg": _jpeg(_image("L", 40, 24, _block_grey(0)), 1),
        "jpeg_grey_o6_40x24.jpg": _jpeg(_image("L", 40, 24, _block_grey(101)), 6),
        "png_grey16_33x21.png": _png(
            _image("I;16", 33, 21, lambda x, y: (x * 1999 + y * 3001 + 257) % 65536)
        ),
        "marked_61x47.png": _png(_image("L", 61, 47, marked_at)),
    }


def _bit_depth(data: bytes) -> int:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return data[24]
    return 8


def describe(path: Path) -> dict[str, object]:
    """The manifest entry of one fixture, read back with Pillow."""
    data = path.read_bytes()
    with Image.open(path) as image:
        image.load()
        orientation = image.getexif().get(ORIENTATION_TAG)
        raw = image.tobytes()
        mode = image.mode
        width, height = image.size
        kind = "jpeg" if image.format == "JPEG" else "png"
    channels = {"L": 1, "RGB": 3, "I;16": 1}[mode]
    entry: dict[str, object] = {
        "kind": kind,
        "mode": mode,
        "width": width,
        "height": height,
        "bit_depth": _bit_depth(data),
        "channels": channels,
        "orientation": orientation,
        "dtype": "u16le" if mode == "I;16" else "u8",
        "sha256": sha256_file(path),
        "pixels": base64.b64encode(raw).decode("ascii"),
    }
    if path.name.startswith("marked_"):
        entry["marked"] = dict(MARKED)
    return entry


def write_all(out: Path) -> dict[str, object]:
    """Write every fixture and the manifest into ``out``; return the manifest."""
    out.mkdir(parents=True, exist_ok=True)
    files: dict[str, bytes] = {
        name: grey_png(width, height, value_at)
        for name, (width, height, value_at) in STDLIB_FIXTURES.items()
    }
    files.update(pillow_fixtures())
    for name, data in files.items():
        (out / name).write_bytes(data)
    manifest = {
        "format": "sarcostat-image-fixtures",
        "format_version": 1,
        "pillow": PIL.__version__,
        "images": {name: describe(out / name) for name in sorted(files)},
    }
    (out / MANIFEST).write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: list[str]) -> int:
    out = Path(argv[1]) if len(argv) > 1 else OUT_DIR
    manifest = write_all(out)
    for name, entry in manifest["images"].items():
        print(
            f"{out / name} {entry['width']} x {entry['height']} {entry['mode']} {entry['sha256']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
