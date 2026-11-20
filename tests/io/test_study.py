"""Tests for :mod:`sarcostat.io.study`: display transforms, session manifest v1, kit sources and PNG checks."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import struct
import subprocess
import sys
import zlib
from itertools import combinations
from pathlib import Path

import numpy as np
import pytest

from sarcostat.io import study
from sarcostat.io.study import (
    CLICK_RESOLUTION,
    INVERSE_TRANSFORM,
    MANIFEST_FILE,
    MANIFEST_SHA256_FILE,
    TRANSFORMS,
    DisplayMapping,
    FolderKitSource,
    KitSource,
    MappingKitSource,
    StudyError,
    check_item_image,
    crop_and_transform,
    crop_image,
    inverse_transform_image,
    inverse_transform_points,
    load_session_kit,
    load_session_kit_from_bytes,
    quantize_coordinate,
    transform_image,
    transform_points,
    transformed_size,
    validate_manifest,
    write_manifest,
)

from .kit_factory import kit_files, make_kit, png_bytes, png_chunk

W, H = 7, 5

EXPECTED_FORWARD = {
    "identity": lambda x, y: (x, y),
    "rot90": lambda x, y: (y, W - 1 - x),
    "rot180": lambda x, y: (W - 1 - x, H - 1 - y),
    "rot270": lambda x, y: (H - 1 - y, x),
    "flip_h": lambda x, y: (W - 1 - x, y),
    "flip_v": lambda x, y: (x, H - 1 - y),
    "transpose": lambda x, y: (y, x),
    "anti_transpose": lambda x, y: (H - 1 - y, W - 1 - x),
}


def _grid_points(rng: np.random.Generator, n: int, w: float, h: float) -> np.ndarray:
    """Random coordinates on the 1/1024 px click grid inside a ``w`` x ``h`` frame."""
    pts = rng.uniform([0.0, 0.0], [w - 1.0, h - 1.0], size=(n, 2))
    return np.vectorize(quantize_coordinate)(pts)


class TestTransforms:
    def test_eight_distinct_names_with_inverses(self) -> None:
        assert len(TRANSFORMS) == 8
        assert set(TRANSFORMS) == set(EXPECTED_FORWARD)
        assert set(INVERSE_TRANSFORM) == set(TRANSFORMS)
        assert INVERSE_TRANSFORM["rot90"] == "rot270"
        assert INVERSE_TRANSFORM["rot270"] == "rot90"
        for name in ("identity", "rot180", "flip_h", "flip_v", "transpose", "anti_transpose"):
            assert INVERSE_TRANSFORM[name] == name

    @pytest.mark.parametrize("name", TRANSFORMS)
    def test_point_map_matches_written_out_formula(self, name: str) -> None:
        xs, ys = np.meshgrid(np.arange(W, dtype=float), np.arange(H, dtype=float))
        pts = np.column_stack([xs.ravel(), ys.ravel()])
        got = transform_points(pts, name, (W, H))
        want = np.array([EXPECTED_FORWARD[name](x, y) for x, y in pts])
        np.testing.assert_array_equal(got, want)

    @pytest.mark.parametrize("name", TRANSFORMS)
    def test_image_and_point_maps_agree_pixel_by_pixel(self, name: str) -> None:
        a = np.arange(W * H, dtype=np.uint8).reshape(H, W)
        out = transform_image(a, name)
        tw, th = transformed_size(name, (W, H))
        assert out.shape == (th, tw)
        for y in range(H):
            for x in range(W):
                xd, yd = transform_points([[x, y]], name, (W, H))[0]
                assert out[int(yd), int(xd)] == a[y, x]

    @pytest.mark.parametrize("name", TRANSFORMS)
    def test_image_round_trip_is_lossless(self, name: str) -> None:
        rng = np.random.default_rng(1)
        a = rng.integers(0, 256, size=(H, W), dtype=np.uint8)
        out = transform_image(a, name)
        assert out.dtype == a.dtype
        np.testing.assert_array_equal(inverse_transform_image(out, name), a)
        rgb = rng.integers(0, 256, size=(H, W, 3), dtype=np.uint8)
        np.testing.assert_array_equal(
            inverse_transform_image(transform_image(rgb, name), name), rgb
        )

    def test_transformed_image_is_a_copy(self) -> None:
        a = np.zeros((H, W), dtype=np.uint8)
        out = transform_image(a, "identity")
        out[0, 0] = 9
        assert a[0, 0] == 0

    def test_eight_images_are_pairwise_distinct(self) -> None:
        a = np.arange(W * W, dtype=np.int32).reshape(W, W)
        images = [transform_image(a, name) for name in TRANSFORMS]
        for i, j in combinations(range(8), 2):
            assert not np.array_equal(images[i], images[j]), (TRANSFORMS[i], TRANSFORMS[j])

    def test_horizontal_flip_maps_x_to_w_minus_1_minus_x(self) -> None:
        np.testing.assert_array_equal(
            transform_points([[0.0, 2.0], [6.0, 2.0], [2.25, 1.0]], "flip_h", (W, H)),
            [[6.0, 2.0], [0.0, 2.0], [3.75, 1.0]],
        )

    def test_rot90_follows_numpy(self) -> None:
        a = np.arange(W * H).reshape(H, W)
        np.testing.assert_array_equal(transform_image(a, "rot90"), np.rot90(a, 1))

    @pytest.mark.parametrize("name", TRANSFORMS)
    def test_float_round_trip_is_exact_on_the_click_grid(self, name: str) -> None:
        rng = np.random.default_rng(2)
        size = (1531, 977)
        pts = _grid_points(rng, 500, *size)
        disp = transform_points(pts, name, size)
        back = inverse_transform_points(disp, name, size)
        assert np.array_equal(back, pts)
        tw, th = transformed_size(name, size)
        disp_pts = _grid_points(rng, 500, tw, th)
        assert np.array_equal(
            transform_points(inverse_transform_points(disp_pts, name, size), name, size), disp_pts
        )

    @pytest.mark.parametrize("name", TRANSFORMS)
    def test_arbitrary_floats_round_trip_to_rounding_error(self, name: str) -> None:
        pts = np.random.default_rng(3).uniform(0, 976, size=(200, 2))
        back = inverse_transform_points(transform_points(pts, name, (1531, 977)), name, (1531, 977))
        np.testing.assert_allclose(back, pts, rtol=0, atol=1e-9)

    def test_unknown_transform_is_rejected(self) -> None:
        with pytest.raises(StudyError, match="display_transform"):
            transform_points([[0, 0]], "rot45", (W, H))
        with pytest.raises(StudyError, match="display_transform"):
            transform_image(np.zeros((2, 2)), "mirror")

    def test_points_must_be_pairs(self) -> None:
        with pytest.raises(StudyError):
            transform_points([[0.0, 1.0, 2.0]], "identity", (W, H))


class TestQuantization:
    def test_values_land_on_the_grid_and_are_idempotent(self) -> None:
        assert CLICK_RESOLUTION == 1.0 / 1024.0
        for v in (0.0, 0.1, 12.3456789, 1023.99999, 4095.5004):
            q = quantize_coordinate(v)
            assert q * 1024 == int(q * 1024)
            assert abs(q - v) <= CLICK_RESOLUTION / 2
            assert quantize_coordinate(q) == q

    def test_non_finite_values_are_rejected(self) -> None:
        for v in (float("nan"), float("inf")):
            with pytest.raises(StudyError):
                quantize_coordinate(v)


class TestCropThenTransform:
    def test_pixel_lands_where_the_point_map_says(self) -> None:
        rng = np.random.default_rng(4)
        original = rng.integers(0, 256, size=(40, 60), dtype=np.uint8)
        offset, size = (11, 7), (23, 17)
        for name in TRANSFORMS:
            mapping = DisplayMapping(name, offset, size)
            shown = crop_and_transform(original, name, offset, size)
            assert (shown.shape[1], shown.shape[0]) == mapping.display_size
            for X, Y in [(11, 7), (33, 23), (20, 15), (11, 23)]:
                xd, yd = mapping.to_display([[X, Y]])[0]
                assert shown[int(yd), int(xd)] == original[Y, X], name

    def test_mapping_round_trip_is_exact(self) -> None:
        rng = np.random.default_rng(5)
        for name in TRANSFORMS:
            mapping = DisplayMapping(name, (1200, 803), (412, 288))
            tw, th = mapping.display_size
            disp = _grid_points(rng, 300, tw, th)
            orig = mapping.to_original(disp)
            assert orig[:, 0].min() >= 1200 and orig[:, 0].max() <= 1200 + 411
            assert orig[:, 1].min() >= 803 and orig[:, 1].max() <= 803 + 287
            assert np.array_equal(mapping.to_display(orig), disp)

    def test_crop_is_lossless_and_bounded(self) -> None:
        original = np.arange(100, dtype=np.uint8).reshape(10, 10)
        np.testing.assert_array_equal(crop_image(original, (2, 3), (4, 5)), original[3:8, 2:6])
        with pytest.raises(StudyError, match="outside"):
            crop_image(original, (8, 0), (4, 4))
        with pytest.raises(StudyError):
            crop_image(original, (-1, 0), (4, 4))

    def test_mapping_from_manifest_item(self) -> None:
        item = {"display_transform": "rot90", "kit_crop_offset": [3, 4], "kit_crop_size": [10, 6]}
        mapping = DisplayMapping.from_item(item)
        assert mapping == DisplayMapping("rot90", (3, 4), (10, 6))
        assert mapping.display_size == (6, 10)


def _make_kit(tmp_path: Path, n_items: int = 2) -> tuple[Path, dict]:
    """A tiny valid kit: one synthetic original, ``n_items`` crops under different transforms."""
    rng = np.random.default_rng(6)
    original = rng.integers(0, 256, size=(80, 120), dtype=np.uint8)
    kit = tmp_path / "kit"
    (kit / "items").mkdir(parents=True)
    items = []
    for k in range(n_items):
        name = TRANSFORMS[(2 * k + 1) % 8]
        offset, size = [10 + k, 5], [40, 30]
        data = png_bytes(crop_and_transform(original, name, tuple(offset), tuple(size)))
        rel = f"items/{k + 1:03d}.png"
        (kit / rel).write_bytes(data)
        items.append(
            {
                "item_id": f"{k + 1:03d}",
                "target_id": f"T{k + 1:02d}",
                "image_id": "img1",
                "type": "shape",
                "kit_file": rel,
                "kit_sha256": hashlib.sha256(data).hexdigest(),
                "display_transform": name,
                "kit_crop_offset": offset,
                "kit_crop_size": size,
                "roi": [offset[0] + 5, offset[1] + 5, offset[0] + 30, offset[1] + 20],
            }
        )
    manifest = {
        "format": study.MANIFEST_FORMAT,
        "format_version": study.MANIFEST_VERSION,
        "study_id": "demo",
        "annotator_id": "ANN1",
        "session_id": "S1",
        "images": [
            {
                "image_id": "img1",
                "file_name": "original.tif",
                "sha256": "a" * 64,
                "width_px": 120,
                "height_px": 80,
                "scale": {"nm_per_px": None, "source": None},
                "study": {"animal": None, "group": None},
            }
        ],
        "items": items,
    }
    write_manifest(manifest, kit)
    return kit, manifest


class TestManifestFiles:
    def test_write_then_load(self, tmp_path: Path) -> None:
        kit_dir, manifest = _make_kit(tmp_path)
        kit = load_session_kit(kit_dir)
        assert kit.manifest == manifest
        raw = (kit_dir / MANIFEST_FILE).read_bytes()
        assert kit.manifest_sha256 == hashlib.sha256(raw).hexdigest()
        sidecar = (kit_dir / MANIFEST_SHA256_FILE).read_text(encoding="utf-8")
        assert sidecar == f"{kit.manifest_sha256}  {MANIFEST_FILE}\n"

    def test_writing_is_canonical_and_deterministic(self, tmp_path: Path) -> None:
        kit_dir, manifest = _make_kit(tmp_path)
        first = (kit_dir / MANIFEST_FILE).read_bytes()
        shuffled = dict(reversed(list(copy.deepcopy(manifest).items())))
        write_manifest(shuffled, kit_dir)
        assert (kit_dir / MANIFEST_FILE).read_bytes() == first
        assert first.endswith(b"\n") and b"\r" not in first
        assert not [p for p in kit_dir.iterdir() if p.name.startswith(".")]

    def test_bare_hex_sidecar_is_accepted(self, tmp_path: Path) -> None:
        kit_dir, _ = _make_kit(tmp_path)
        digest = hashlib.sha256((kit_dir / MANIFEST_FILE).read_bytes()).hexdigest()
        (kit_dir / MANIFEST_SHA256_FILE).write_text(digest.upper() + "\n", encoding="utf-8")
        assert load_session_kit(kit_dir).manifest_sha256 == digest

    def test_tampered_manifest_is_refused(self, tmp_path: Path) -> None:
        kit_dir, _ = _make_kit(tmp_path)
        path = kit_dir / MANIFEST_FILE
        path.write_bytes(path.read_bytes().replace(b"ANN1", b"ANN2"))
        with pytest.raises(StudyError, match="SHA-256"):
            load_session_kit(kit_dir)

    def test_missing_sidecar_is_refused(self, tmp_path: Path) -> None:
        kit_dir, _ = _make_kit(tmp_path)
        (kit_dir / MANIFEST_SHA256_FILE).unlink()
        with pytest.raises(StudyError, match=MANIFEST_SHA256_FILE):
            load_session_kit(kit_dir)

    def test_sidecar_naming_another_file_is_refused(self, tmp_path: Path) -> None:
        kit_dir, _ = _make_kit(tmp_path)
        digest = hashlib.sha256((kit_dir / MANIFEST_FILE).read_bytes()).hexdigest()
        (kit_dir / MANIFEST_SHA256_FILE).write_text(f"{digest}  other.json\n", encoding="utf-8")
        with pytest.raises(StudyError):
            load_session_kit(kit_dir)

    def test_nan_in_the_file_is_refused(self, tmp_path: Path) -> None:
        kit_dir, manifest = _make_kit(tmp_path)
        text = json.dumps(manifest).replace('"roi": [15', '"roi": [NaN')
        data = text.encode("utf-8")
        (kit_dir / MANIFEST_FILE).write_bytes(data)
        (kit_dir / MANIFEST_SHA256_FILE).write_text(
            hashlib.sha256(data).hexdigest() + "\n", encoding="utf-8"
        )
        with pytest.raises(StudyError):
            load_session_kit(kit_dir)

    def test_invalid_manifest_is_not_written(self, tmp_path: Path) -> None:
        kit_dir, manifest = _make_kit(tmp_path)
        before = (kit_dir / MANIFEST_FILE).read_bytes()
        bad = copy.deepcopy(manifest)
        bad["items"][0]["display_transform"] = "rot45"
        with pytest.raises(StudyError):
            write_manifest(bad, kit_dir)
        assert (kit_dir / MANIFEST_FILE).read_bytes() == before


def _mutations():
    """(description, mutate(manifest)) pairs that each make a valid manifest invalid."""

    def setter(path, value):
        def mutate(m):
            target = m
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value

        return mutate

    def deleter(path):
        def mutate(m):
            target = m
            for key in path[:-1]:
                target = target[key]
            del target[path[-1]]

        return mutate

    def duplicate_item(m):
        m["items"][1]["item_id"] = m["items"][0]["item_id"]

    def duplicate_file_name(m):
        twin = copy.deepcopy(m["images"][0])
        twin["image_id"] = "img2"
        m["images"].append(twin)

    return [
        ("wrong format", setter(["format"], "something-else")),
        ("major version 2", setter(["format_version"], "2.0")),
        ("version not a string", setter(["format_version"], 1.0)),
        ("unknown top-level key", setter(["hint"], "AsR=1.2")),
        ("unknown item key", setter(["items", 0, "expected_class"], "Normal")),
        ("unknown image key", setter(["images", 0, "path"], "/abs/original.tif")),
        ("missing items", deleter(["items"])),
        ("empty items", setter(["items"], [])),
        ("missing roi", deleter(["items", 0, "roi"])),
        ("annotator id with a slash", setter(["annotator_id"], "ANN/1")),
        ("empty session id", setter(["session_id"], "")),
        ("duplicate item id", duplicate_item),
        ("unknown image id", setter(["items", 0, "image_id"], "nope")),
        ("unknown item type", setter(["items", 0, "type"], "triangle")),
        ("unknown transform", setter(["items", 0, "display_transform"], "rot45")),
        ("kit file escaping the kit", setter(["items", 0, "kit_file"], "../secret.png")),
        ("absolute kit file", setter(["items", 0, "kit_file"], "/tmp/x.png")),
        ("backslash kit file", setter(["items", 0, "kit_file"], "items\\001.png")),
        ("manifest as kit file", setter(["items", 0, "kit_file"], MANIFEST_FILE)),
        ("short sha", setter(["items", 0, "kit_sha256"], "abc")),
        ("float crop offset", setter(["items", 0, "kit_crop_offset"], [10.5, 5])),
        ("bool crop size", setter(["items", 0, "kit_crop_size"], [True, 30])),
        ("zero crop size", setter(["items", 0, "kit_crop_size"], [0, 30])),
        ("crop outside image", setter(["items", 0, "kit_crop_offset"], [100, 5])),
        ("roi outside crop", setter(["items", 0, "roi"], [0, 10, 20, 20])),
        ("roi inverted", setter(["items", 0, "roi"], [30, 10, 20, 20])),
        ("roi with inf", setter(["items", 0, "roi"], [15, 10, float("inf"), 20])),
        ("image file name with directory", setter(["images", 0, "file_name"], "a/b.tif")),
        ("image width not int", setter(["images", 0, "width_px"], 120.0)),
        ("duplicate image id", lambda m: m["images"].append(copy.deepcopy(m["images"][0]))),
        ("study not an object", setter(["images", 0, "study"], "group A")),
        ("scale missing source", deleter(["images", 0, "scale", "source"])),
        ("negative scale", setter(["images", 0, "scale", "nm_per_px"], -2.0)),
        ("scale as a bare number", setter(["images", 0, "scale"], 2.0)),
        ("animal not a string", setter(["images", 0, "study", "animal"], 3)),
        ("id longer than 64", setter(["items", 0, "item_id"], "x" * 65)),
        ("id starting with a dot", setter(["items", 0, "target_id"], ".T01")),
        ("null target id", setter(["items", 0, "target_id"], None)),
        ("duplicate image file name", duplicate_file_name),
    ]


class TestManifestValidation:
    def test_valid_manifest_passes(self, tmp_path: Path) -> None:
        _, manifest = _make_kit(tmp_path)
        validate_manifest(manifest)

    @pytest.mark.parametrize(("label", "mutate"), _mutations(), ids=[m[0] for m in _mutations()])
    def test_invalid_manifest_is_rejected(self, tmp_path: Path, label: str, mutate) -> None:
        _, manifest = _make_kit(tmp_path)
        bad = copy.deepcopy(manifest)
        mutate(bad)
        with pytest.raises(StudyError):
            validate_manifest(bad)

    def test_same_kit_file_needs_same_checksum(self, tmp_path: Path) -> None:
        _, manifest = _make_kit(tmp_path)
        bad = copy.deepcopy(manifest)
        bad["items"][1]["kit_file"] = bad["items"][0]["kit_file"]
        with pytest.raises(StudyError, match="kit_sha256"):
            validate_manifest(bad)

    def test_minor_version_is_accepted(self, tmp_path: Path) -> None:
        _, manifest = _make_kit(tmp_path)
        manifest["format_version"] = "1.3"
        validate_manifest(manifest)


class TestReadWhitelist:
    def test_listed_kit_image_is_read_and_checked(self, tmp_path: Path) -> None:
        pytest.importorskip("PIL")
        kit_dir, manifest = _make_kit(tmp_path)
        kit = load_session_kit(kit_dir)
        item = kit.items[0]
        image = kit.read_item_image(item)
        assert image.dtype == np.uint8
        mapping = DisplayMapping.from_item(item)
        assert (image.shape[1], image.shape[0]) == mapping.display_size

    def test_unlisted_file_is_refused(self, tmp_path: Path) -> None:
        kit_dir, _ = _make_kit(tmp_path)
        (kit_dir / "targets.json").write_text("{}", encoding="utf-8")
        kit = load_session_kit(kit_dir)
        for rel in ("targets.json", "items/../targets.json", "../kit/targets.json", "/etc/passwd"):
            with pytest.raises(StudyError, match="not listed"):
                kit.read_file(rel)

    def test_modified_kit_image_is_refused(self, tmp_path: Path) -> None:
        pytest.importorskip("PIL")
        kit_dir, _ = _make_kit(tmp_path)
        kit = load_session_kit(kit_dir)
        path = kit_dir / kit.items[0]["kit_file"]
        path.write_bytes(path.read_bytes() + b"\0")
        with pytest.raises(StudyError, match="SHA-256"):
            kit.read_item_image(kit.items[0])

    @pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
    def test_symlink_out_of_the_kit_is_refused(self, tmp_path: Path) -> None:
        kit_dir, manifest = _make_kit(tmp_path)
        outside = tmp_path / "outside.png"
        target = kit_dir / manifest["items"][0]["kit_file"]
        outside.write_bytes(target.read_bytes())
        target.unlink()
        try:
            target.symlink_to(outside)
        except OSError:
            pytest.skip("cannot create symlinks here")
        kit = load_session_kit(kit_dir)
        with pytest.raises(StudyError, match="outside"):
            kit.read_file(manifest["items"][0]["kit_file"])

    def test_kit_image_must_be_8bit_grey_of_the_right_size(self, tmp_path: Path) -> None:
        pytest.importorskip("PIL")
        kit_dir, manifest = _make_kit(tmp_path)
        for k, array in enumerate(
            [np.zeros((30, 40, 3), dtype=np.uint8), np.zeros((31, 40), dtype=np.uint8)]
        ):
            data = png_bytes(array)
            item = manifest["items"][0]
            (kit_dir / item["kit_file"]).write_bytes(data)
            item["kit_sha256"] = hashlib.sha256(data).hexdigest()
            write_manifest(manifest, kit_dir)
            kit = load_session_kit(kit_dir)
            with pytest.raises(StudyError, match="8-bit" if k == 0 else "size"):
                kit.read_item_image(kit.items[0])


class TestExclusiveWrite:
    def test_new_file_is_written_and_existing_file_is_kept(self, tmp_path: Path) -> None:
        target = tmp_path / "session.json"
        study.write_bytes_exclusive(b"first\n", target)
        assert target.read_bytes() == b"first\n"
        with pytest.raises(StudyError, match="cannot be overwritten"):
            study.write_bytes_exclusive(b"second\n", target)
        assert target.read_bytes() == b"first\n"
        assert sorted(p.name for p in tmp_path.iterdir()) == ["session.json"]

    def test_without_hard_links_the_file_is_still_never_replaced(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        def no_links(*_args):
            raise OSError("hard links not supported")

        monkeypatch.setattr(study.os, "link", no_links, raising=False)
        target = tmp_path / "session.json"
        study.write_bytes_exclusive(b"first\n", target)
        with pytest.raises(StudyError):
            study.write_bytes_exclusive(b"second\n", target)
        assert target.read_bytes() == b"first\n"
        assert sorted(p.name for p in tmp_path.iterdir()) == ["session.json"]


class TestExclusiveWriteWithoutOsLink:
    def test_file_is_written_once_and_a_second_write_is_refused(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.delattr(os, "link", raising=False)
        assert not hasattr(study.os, "link")
        target = tmp_path / "session.json"
        study.write_bytes_exclusive(b"first\n", target)
        assert target.read_bytes() == b"first\n"
        with pytest.raises(StudyError, match="cannot be overwritten"):
            study.write_bytes_exclusive(b"second\n", target)
        assert target.read_bytes() == b"first\n"
        assert sorted(p.name for p in tmp_path.iterdir()) == ["session.json"]

    def test_a_file_that_appears_during_the_write_is_kept(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.delattr(os, "link", raising=False)
        target = tmp_path / "session.json"
        real_mkstemp = study.tempfile.mkstemp

        def mkstemp_then_other_writer(*args, **kwargs):
            result = real_mkstemp(*args, **kwargs)
            target.write_bytes(b"other\n")
            return result

        monkeypatch.setattr(study.tempfile, "mkstemp", mkstemp_then_other_writer)
        with pytest.raises(StudyError, match="cannot be overwritten"):
            study.write_bytes_exclusive(b"mine\n", target)
        assert target.read_bytes() == b"other\n"
        assert sorted(p.name for p in tmp_path.iterdir()) == ["session.json"]


@pytest.mark.skipif(sys.platform == "emscripten", reason="no subprocesses in Pyodide")
@pytest.mark.needs_cpython(reason="runs a subprocess; Emscripten has no processes")
def test_importing_the_study_module_loads_no_pil() -> None:
    code = (
        "import sys, sarcostat.io.study; "
        "print(sorted(m for m in sys.modules if m == 'PIL' or m.startswith('PIL.')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[]"


GREY = np.random.default_rng(7).integers(0, 256, size=(40, 30), dtype=np.uint8)
RAW_ROWS = b"".join(b"\x00" + row.tobytes() for row in GREY)
IHDR_GREY = png_chunk(b"IHDR", struct.pack(">IIBBBBB", 30, 40, 8, 0, 0, 0, 0))
IEND = png_chunk(b"IEND", b"")


def _item() -> dict:
    """A rot90 item with a 40 x 30 crop, shown as 30 x 40 (the shape of ``GREY``)."""
    return {
        "kit_file": "items/001.png",
        "display_transform": "rot90",
        "kit_crop_offset": [5, 7],
        "kit_crop_size": [40, 30],
    }


def _assemble(*chunks: bytes) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"".join(chunks)


def _png_like_the_kit_maker(pixels: np.ndarray) -> bytes:
    """Pillow, called as ``png_bytes`` of the study kit maker calls it (mode L, level 6)."""
    image_module = pytest.importorskip("PIL.Image")
    buf = io.BytesIO()
    image_module.fromarray(np.ascontiguousarray(pixels, dtype=np.uint8)).save(
        buf, format="PNG", optimize=False, compress_level=6
    )
    return buf.getvalue()


def _refused_pngs():
    def extra(kind: bytes, body: bytes):
        return lambda: png_bytes(GREY, extra_chunks=[(kind, body)])

    chrm = struct.pack(">8I", 31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000)
    return [
        ("rgb", lambda: png_bytes(np.zeros((40, 30, 3), dtype=np.uint8)), "8-bit greyscale"),
        ("16-bit", lambda: png_bytes(GREY.astype(np.uint16) * 257), "8-bit greyscale"),
        ("palette", lambda: png_bytes(GREY, palette=bytes(range(256)) * 3), "8-bit greyscale"),
        ("interlaced", lambda: png_bytes(GREY, interlace=True), "interlaced"),
        ("wrong size", lambda: png_bytes(GREY.T), "size 40 x 30"),
        ("gAMA", extra(b"gAMA", struct.pack(">I", 45455)), "gAMA"),
        ("iCCP", extra(b"iCCP", b"grey\x00\x00" + zlib.compress(b"profile")), "iCCP"),
        ("sRGB", extra(b"sRGB", b"\x00"), "sRGB"),
        ("cHRM", extra(b"cHRM", chrm), "cHRM"),
        ("tRNS", extra(b"tRNS", b"\x00\x00"), "tRNS"),
        ("cICP", extra(b"cICP", bytes([1, 13, 0, 1])), "cICP"),
        ("eXIf", extra(b"eXIf", b"MM\x00*\x00\x00\x00\x08\x00\x00"), "eXIf"),
        ("not a PNG", lambda: b"GIF89a" + bytes(40), "not a PNG"),
        ("truncated", lambda: png_bytes(GREY)[:-5], "truncated"),
        ("bytes after IEND", lambda: png_bytes(GREY) + b"\x00", "IEND"),
        ("no IEND", lambda: png_bytes(GREY)[:-12], "IEND"),
        ("no IDAT", lambda: _assemble(IHDR_GREY, IEND), "IDAT"),
        (
            "split IDAT",
            lambda: _assemble(
                IHDR_GREY,
                png_chunk(b"IDAT", zlib.compress(RAW_ROWS)[:20]),
                png_chunk(b"tEXt", b"Comment\x00x"),
                png_chunk(b"IDAT", zlib.compress(RAW_ROWS)[20:]),
                IEND,
            ),
            "consecutive",
        ),
        (
            "one row short",
            lambda: _assemble(IHDR_GREY, png_chunk(b"IDAT", zlib.compress(RAW_ROWS[:-31])), IEND),
            "do not match",
        ),
        (
            "unknown row filter",
            lambda: _assemble(
                IHDR_GREY, png_chunk(b"IDAT", zlib.compress(b"\x05" + RAW_ROWS[1:])), IEND
            ),
            "do not match",
        ),
        (
            "damaged image data",
            lambda: _assemble(IHDR_GREY, png_chunk(b"IDAT", b"not zlib data"), IEND),
            "damaged image data",
        ),
    ]


class TestCheckItemImage:
    def test_png_written_like_the_kit_maker_is_accepted(self) -> None:
        check_item_image(_item(), _png_like_the_kit_maker(GREY))

    def test_stdlib_png_is_accepted(self) -> None:
        check_item_image(_item(), png_bytes(GREY))
        check_item_image(_item(), bytearray(png_bytes(GREY)))

    def test_text_time_and_pixel_size_chunks_are_accepted(self) -> None:
        extra = [
            (b"tEXt", b"Comment\x00kit item"),
            (b"pHYs", struct.pack(">IIB", 3780, 3780, 1)),
            (b"tIME", struct.pack(">HBBBBB", 2026, 11, 20, 9, 0, 0)),
        ]
        check_item_image(_item(), png_bytes(GREY, extra_chunks=extra))

    def test_image_data_in_several_consecutive_idat_chunks_is_accepted(self) -> None:
        packed = zlib.compress(RAW_ROWS)
        data = _assemble(
            IHDR_GREY, png_chunk(b"IDAT", packed[:20]), png_chunk(b"IDAT", packed[20:]), IEND
        )
        check_item_image(_item(), data)

    def test_every_item_of_a_kit_is_accepted(self, tmp_path: Path) -> None:
        kit = load_session_kit(make_kit(tmp_path, [("shape", name) for name in TRANSFORMS]))
        for item in kit.items:
            check_item_image(item, kit.read_file(item["kit_file"]))

    def test_the_test_writer_decodes_like_pillow(self) -> None:
        image_module = pytest.importorskip("PIL.Image")
        for interlace in (False, True):
            with image_module.open(io.BytesIO(png_bytes(GREY, interlace=interlace))) as img:
                assert img.mode == "L"
                np.testing.assert_array_equal(np.asarray(img), GREY)

    @pytest.mark.parametrize(
        ("label", "make", "match"), _refused_pngs(), ids=[r[0] for r in _refused_pngs()]
    )
    def test_refused(self, label: str, make, match: str) -> None:
        with pytest.raises(StudyError, match=match) as info:
            check_item_image(_item(), make())
        assert "\n" not in str(info.value)

    def test_a_damaged_chunk_is_refused(self) -> None:
        data = bytearray(png_bytes(GREY))
        data[-20] ^= 0xFF
        with pytest.raises(StudyError, match="damaged 'IDAT' chunk"):
            check_item_image(_item(), bytes(data))


SPECS = [("shape", "rot90"), ("chain", "identity"), ("pair", "anti_transpose")]


class TestKitFromBytes:
    def test_bytes_kit_equals_the_folder_kit(self, tmp_path: Path) -> None:
        kit_dir = make_kit(tmp_path, SPECS)
        folder = load_session_kit(kit_dir)
        memory = load_session_kit_from_bytes(kit_files(kit_dir), label=kit_dir.name)
        assert folder.directory == kit_dir and memory.directory is None
        assert isinstance(folder.source, FolderKitSource)
        assert isinstance(memory.source, MappingKitSource)
        assert memory.manifest == folder.manifest
        assert memory.manifest_sha256 == folder.manifest_sha256
        assert memory.items == folder.items and memory.whitelist == folder.whitelist
        for item in folder.items:
            data = folder.read_file(item["kit_file"])
            assert memory.read_file(item["kit_file"]) == data
            check_item_image(item, data)

    def test_mapping_source_reads_what_the_folder_source_reads(self, tmp_path: Path) -> None:
        kit_dir = make_kit(tmp_path, SPECS)
        files = kit_files(kit_dir)
        folder, memory = FolderKitSource(kit_dir), MappingKitSource(files, label="A1_S1")
        assert folder.label == "kit" and memory.label == "A1_S1"
        for name in files:
            assert memory.read_bytes(name) == folder.read_bytes(name)
        for source in (folder, memory):
            with pytest.raises(FileNotFoundError):
                source.read_bytes("items/999.png")
        with pytest.raises(TypeError):
            KitSource()

    def test_unlisted_files_in_the_mapping_are_refused(self, tmp_path: Path) -> None:
        files = kit_files(make_kit(tmp_path, SPECS))
        assert {"targets.json", "items/unlisted.png"} <= files.keys()
        kit = load_session_kit_from_bytes(files)
        for rel in ("targets.json", "items/unlisted.png", MANIFEST_FILE, "../kit/targets.json"):
            with pytest.raises(StudyError, match="not listed"):
                kit.read_file(rel)

    @pytest.mark.parametrize("name", [MANIFEST_FILE, MANIFEST_SHA256_FILE])
    def test_a_tampered_manifest_byte_is_refused(self, tmp_path: Path, name: str) -> None:
        files = kit_files(make_kit(tmp_path, SPECS))
        data = bytearray(files[name])
        data[5] ^= 0x01
        files[name] = bytes(data)
        with pytest.raises(StudyError, match="SHA-256"):
            load_session_kit_from_bytes(files)

    def test_a_tampered_item_byte_is_refused(self, tmp_path: Path) -> None:
        files = kit_files(make_kit(tmp_path, SPECS))
        first, second = (item["kit_file"] for item in load_session_kit_from_bytes(files).items[:2])
        data = bytearray(files[first])
        data[len(data) // 2] ^= 0x01
        files[first] = bytes(data)
        kit = load_session_kit_from_bytes(files)
        with pytest.raises(StudyError, match="SHA-256"):
            kit.read_file(first)
        assert kit.read_file(second) == files[second]

    @pytest.mark.parametrize("name", [MANIFEST_FILE, MANIFEST_SHA256_FILE])
    def test_a_missing_manifest_file_names_the_kit(self, tmp_path: Path, name: str) -> None:
        files = kit_files(make_kit(tmp_path, SPECS))
        del files[name]
        with pytest.raises(StudyError, match=f"{name} is missing from A1_S1"):
            load_session_kit_from_bytes(files, label="A1_S1")

    def test_a_missing_item_file_is_not_found(self, tmp_path: Path) -> None:
        files = kit_files(make_kit(tmp_path, SPECS))
        kit = load_session_kit_from_bytes(files)
        rel = kit.items[0]["kit_file"]
        del files[rel]
        assert kit.read_file(rel)
        with pytest.raises(FileNotFoundError):
            load_session_kit_from_bytes(files).read_file(rel)

    def test_later_changes_to_the_buffers_do_not_reach_the_kit(self, tmp_path: Path) -> None:
        files = {
            name: bytearray(data) for name, data in kit_files(make_kit(tmp_path, SPECS)).items()
        }
        kit = load_session_kit_from_bytes(files)
        rel = kit.items[0]["kit_file"]
        original = bytes(files[rel])
        files[rel][100] ^= 0xFF
        assert kit.read_file(rel) == original

    def test_folder_kit_built_without_a_source_reads_its_folder(self, tmp_path: Path) -> None:
        kit = load_session_kit(make_kit(tmp_path, SPECS))
        rel = kit.items[0]["kit_file"]
        plain = type(kit)(kit.directory, kit.manifest, kit.manifest_sha256)
        assert plain == kit and plain.read_file(rel) == kit.read_file(rel)
        detached = type(kit)(None, kit.manifest, kit.manifest_sha256)
        with pytest.raises(StudyError, match="neither a folder"):
            detached.read_file(rel)
