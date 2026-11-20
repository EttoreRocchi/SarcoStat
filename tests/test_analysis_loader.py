"""Tests for the headless shape measurements of sarcostat.analysis (ported from the loader tests).

The measurements of an annotation file must equal what the core gives for the same original-pixel
landmarks and the same scale, the computation the Tk GUI and the web app make on confirm.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from sarcostat.analysis import ShapeMeasurement, load_shape_geometries, shape_geometries
from sarcostat.core.sarcomere_morphometry import calculate_sarcomere_geometry
from sarcostat.io.annotations import read_annotations

DATA = Path(__file__).resolve().parent / "io" / "data"
EXAMPLES = DATA.parents[2] / "examples"
GOLDEN = sorted(DATA.glob("golden_v1_*.json"))


def _scale(doc, image):
    if "gui" in doc:
        return doc["gui"]["pixel_size"], doc["gui"]["unit"]
    nm = image["scale"]["nm_per_px"]
    return (1.0, "px") if nm is None else (nm, "nm")


class TestHeadlessLoader:
    @pytest.mark.parametrize("path", GOLDEN, ids=lambda p: p.name)
    def test_matches_the_core_on_the_same_landmarks(self, path):
        doc = read_annotations(path)
        measured = load_shape_geometries(path, image_dir=EXAMPLES)
        assert measured
        images = {img["image_id"]: img for img in doc["images"]}
        items = {item["item_id"]: item for item in doc["items"]}
        for m in measured:
            item, image = items[m.item_id], images[m.image_id]
            pts = item["points"]
            points = [tuple(p) for key in ("z1", "m", "z2") for p in pts[key]]
            pixel_size, unit = _scale(doc, image)
            core = calculate_sarcomere_geometry(
                *points,
                sarcomere_id=m.geometry.sarcomere_id,
                pixel_to_unit=pixel_size,
                unit=unit,
            )
            for name in ("aspect_ratio", "curviness", "shape_index"):
                assert getattr(m.geometry, name) == getattr(core, name), (path.name, m.item_id)
            assert m.geometry.unit == core.unit
            assert m.geometry.shape_classification == core.shape_classification

    def test_selects_measurable_shape_items(self):
        doc = read_annotations(DATA / "golden_v1_study.json")
        measured = shape_geometries(doc)
        expected = [
            item["item_id"]
            for item in doc["items"]
            if item["type"] == "shape" and not item["skipped"] and "m" in item["points"]
        ]
        assert [m.item_id for m in measured] == expected
        assert all(isinstance(m, ShapeMeasurement) for m in measured)
        by_id = {m.item_id: m for m in measured}
        assert by_id["S0-001"].target_id == "T01" and by_id["S0-001"].image_id == "tem3"
        assert by_id["S0-001"].geometry.unit == "nm"

    def test_sarcomere_ids_count_every_shape_item_of_the_image(self):
        by_id = {
            m.item_id: m for m in shape_geometries(read_annotations(DATA / "golden_v1_study.json"))
        }
        assert by_id["S0-001"].geometry.sarcomere_id == 1
        assert by_id["S0-008"].geometry.sarcomere_id == 4
        assert by_id["S0-006"].geometry.sarcomere_id == 1
        assert by_id["S0-007"].geometry.sarcomere_id == 2

    def test_same_target_under_two_transforms_gives_the_same_shape(self):
        """Items S0-001 (identity) and S0-008 (flip_h) hold the same sarcomere, clicked mirrored."""
        by_id = {
            m.item_id: m for m in shape_geometries(read_annotations(DATA / "golden_v1_study.json"))
        }
        a, b = by_id["S0-001"].geometry, by_id["S0-008"].geometry
        assert a.shape_index == pytest.approx(b.shape_index, rel=1e-12)
        assert a.aspect_ratio == pytest.approx(b.aspect_ratio, rel=1e-12)

    def test_unknown_scale_gives_pixels(self):
        doc = json.loads((DATA / "golden_v1_study.json").read_text())
        for image in doc["images"]:
            image["scale"]["nm_per_px"] = None
        measured = shape_geometries(doc)
        assert {m.geometry.unit for m in measured} == {"px"}

    def test_gui_block_sets_the_scale(self):
        doc = read_annotations(DATA / "golden_v1_gui.json")
        (first, *_rest) = shape_geometries(doc)
        assert first.geometry.unit == doc["gui"]["unit"]

    @pytest.mark.needs_cpython(reason="runs a subprocess; Emscripten has no processes")
    def test_runs_without_the_gui(self):
        code = (
            "import sys; from sarcostat.analysis import load_shape_geometries as f; "
            f"assert f({str(DATA / 'golden_v1_study.json')!r}); "
            "bad = [m for m in ('tkinter', '_tkinter', 'sarcostat.gui', 'matplotlib', 'PIL') "
            "if m in sys.modules]; "
            "assert not bad, bad"
        )
        subprocess.run([sys.executable, "-c", code], check=True)
