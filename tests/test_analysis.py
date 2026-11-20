"""Tests for sarcostat.analysis: the one analysis path from a format v1 document.

Expected values come from direct calls of :mod:`sarcostat.core` on the same points and settings,
so the tests do not depend on the Tk GUI.
"""

from __future__ import annotations

import ast
import copy
import json
import math
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

import sarcostat
from sarcostat import analysis
from sarcostat.analysis import (
    AnalysisError,
    analyze_document,
    estimate_click_error,
    sarcomere_ids,
    settings_for_image,
)
from sarcostat.core.circular_stats import analyze_segment_sequence
from sarcostat.core.register import z_line_register
from sarcostat.core.sarcomere_morphometry import (
    analyze_sarcomere_chain,
    calculate_sarcomere_geometry,
    calculate_z_staircase_alignment,
)
from sarcostat.core.shape_uncertainty import (
    class_stability,
    estimate_annotation_sigma,
    shape_uncertainty,
)
from sarcostat.export import results_json
from sarcostat.io.annotations import (
    FORMAT_NAME,
    FORMAT_VERSION,
    AnnotationFormatError,
    validate_annotations,
)

PACKAGE = Path(sarcostat.__file__).resolve().parent
SIZE = 4096
SQUARE = ((10.0, 10.0), (10.0, 30.0), (20.0, 10.0), (20.0, 30.0), (30.0, 10.0), (30.0, 30.0))
ZLINES_A = [((x, 50.0), (x, 70.0)) for x in (20.0, 60.0, 100.0, 140.0, 180.0)]
ZLINES_B = [((x, 110.0), (x, 130.0)) for x in (30.0, 70.0, 110.0, 150.0, 190.0)]
ZLINES_C = [((x, 110.0), (x, 130.0)) for x in (220.0, 250.0, 280.0)]
ZLINES_D = [((x, 150.0), (x, 170.0)) for x in (228.0, 258.0, 288.0)]
CHAIN = [
    ((x, 300.0 + dy), (x + 2.0, 330.0 + dy)) for x, dy in ((40, 0), (80, 3), (121, 1), (160, 6))
]


def shifted(points, dx=0.0, dy=0.0):
    """The six landmarks ``points`` moved by ``(dx, dy)``."""
    return tuple((x + dx, y + dy) for x, y in points)


def shape_points(points=SQUARE, m_line=True):
    """v1 ``points`` of a shape item from six landmarks A-F."""
    a, b, c, d, e, f = ([float(x), float(y)] for x, y in points)
    out = {"z1": [a, b], "z2": [e, f]}
    if m_line:
        out["m"] = [c, d]
    return out


def lines(zlines):
    return [[[float(p[0]), float(p[1])], [float(q[0]), float(q[1])]] for p, q in zlines]


def item(item_id, kind, points=None, *, image_id="img", skipped=False, m_skipped=False, **extra):
    """A free-session v1 item (identity transform, whole-image crop, no clicks)."""
    out = {
        "item_id": item_id,
        "target_id": None,
        "image_id": image_id,
        "type": kind,
        "display_transform": "identity",
        "kit_crop_offset": [0, 0],
        "kit_crop_size": [SIZE, SIZE],
        "raw_clicks": [],
        "started_utc": None,
        "finished_utc": None,
        "active_seconds": None,
        "skipped": skipped,
        "skip_reason": "not visible" if skipped or m_skipped else None,
        "skipped_parts": ["m_line"] if m_skipped else [],
        **extra,
    }
    if not skipped:
        out["points"] = points
    return out


def shape(item_id, points=SQUARE, **kwargs):
    m_skipped = kwargs.get("m_skipped", False)
    pts = None if kwargs.get("skipped") else shape_points(points, m_line=not m_skipped)
    return item(item_id, "shape", pts, **kwargs)


def chain(item_id, zlines=CHAIN, **kwargs):
    pts = None if kwargs.get("skipped") else {"z_lines": lines(zlines)}
    return item(item_id, "chain", pts, **kwargs)


def pair(item_id, zlines_a, zlines_b, **kwargs):
    pts = None if kwargs.get("skipped") else {"myofibrils": [lines(zlines_a), lines(zlines_b)]}
    return item(item_id, "pair", pts, **kwargs)


def image(image_id="img", file_name="synthetic.png", nm_per_px=None, sha256="a" * 64):
    return {
        "image_id": image_id,
        "file_name": file_name,
        "sha256": sha256,
        "width_px": SIZE,
        "height_px": SIZE,
        "scale": {"nm_per_px": nm_per_px, "source": None if nm_per_px is None else "user"},
        "study": {"animal": None, "group": None},
    }


def make_doc(items, *, gui=None, images=None):
    """A valid v1 document holding ``items``; ``gui`` overrides the defaults of a gui block."""
    doc = {
        "format": FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        "sarcostat_version": "0.2.0",
        "source_digest": "f" * 64,
        "created_utc": "2026-11-20T00:00:00Z",
        "annotator_id": None,
        "session_id": None,
        "study_id": None,
        "images": images or [image()],
        "items": list(items),
    }
    if gui is not None:
        doc["gui"] = {
            "pixel_size": 1.0,
            "unit": "px",
            "expected_direction_deg": None,
            "click_sigma_z": None,
            "click_sigma_m": None,
            "click_perp_ratio": 1.0,
            "mode": "Single Sarcomere",
            "open_myofibril": False,
            **gui,
        }
    validate_annotations(doc)
    return doc


def only(results):
    (img,) = results.images
    return img


class TestSettings:
    def test_gui_block_sets_everything(self):
        gui = {
            "pixel_size": 0.5,
            "unit": "um",
            "expected_direction_deg": 15,
            "click_sigma_z": 0.25,
            "click_sigma_m": 0.5,
            "click_perp_ratio": 0.75,
        }
        s = settings_for_image(make_doc([], gui=gui, images=[image(nm_per_px=3.0)]), "img")
        assert (s.pixel_size, s.unit, s.scale_source) == (0.5, "um", "gui")
        assert (s.reference_direction_deg, s.click_sigma_z, s.click_sigma_m) == (15.0, 0.25, 0.5)
        assert s.click_perp_ratio == 0.75 and s.has_click_error

    def test_image_scale_then_pixels(self):
        doc = make_doc([], images=[image("a", "a.png", 6.62), image("b", "b.png")])
        a, b = settings_for_image(doc, "a"), settings_for_image(doc, "b")
        assert (a.pixel_size, a.unit, a.scale_source) == (6.62, "nm", "image")
        assert (b.pixel_size, b.unit, b.scale_source) == (1.0, "px", "none")
        for s in (a, b):
            assert s.reference_direction_deg is None and not s.has_click_error

    def test_unknown_image(self):
        with pytest.raises(AnalysisError) as info:
            settings_for_image(make_doc([]), "nope")
        assert info.value.code == "unknown_image" and info.value.params == {"image_id": "nope"}
        with pytest.raises(AnalysisError, match="nope"):
            analyze_document(make_doc([]), "nope")


class TestShapes:
    def test_geometry_equals_core(self):
        doc = make_doc(
            [shape("s1", shifted(SQUARE, 3.5, 7.25))], gui={"pixel_size": 2.0, "unit": "nm"}
        )
        (s,) = only(analyze_document(doc)).shapes
        expected = calculate_sarcomere_geometry(
            *shifted(SQUARE, 3.5, 7.25), sarcomere_id=1, pixel_to_unit=2.0, unit="nm"
        )
        assert repr(s.geometry) == repr(expected)
        assert s.skip_reason is None and s.sarcomere_id == 1

    def test_unmeasured_items_keep_their_number(self):
        bad = ((10.0, 10.0), (10.0, 10.0), *SQUARE[2:])
        doc = make_doc(
            [
                shape("s1"),
                shape("s2", m_skipped=True),
                shape("s3", skipped=True),
                shape("s4", bad),
                shape("s5", shifted(SQUARE, 40)),
            ]
        )
        img = only(analyze_document(doc))
        got = [(s.item_id, s.sarcomere_id, s.skip_reason) for s in img.shapes]
        assert got == [
            ("s1", 1, None),
            ("s2", 2, "m_line_skipped"),
            ("s3", 3, "item_skipped"),
            ("s4", 4, "degenerate_geometry"),
            ("s5", 5, None),
        ]
        assert [g.sarcomere_id for g in img.geometries] == [1, 5]

    def test_no_uncertainty_without_click_error(self):
        img = only(analyze_document(make_doc([shape("s1")])))
        assert img.uncertainties is None

    def test_uncertainty_and_stability_equal_core_with_the_sarcomere_id_as_seed(self):
        gui = {"click_sigma_z": 0.6, "click_sigma_m": 0.9, "click_perp_ratio": 0.7}
        doc = make_doc(
            [shape("s1"), shape("s2", m_skipped=True), shape("s3", shifted(SQUARE, 40, 2))],
            gui=gui,
        )
        img = only(analyze_document(doc))
        assert [u.sarcomere_id for u in img.uncertainties] == [1, 3]
        for g, u in zip(img.geometries, img.uncertainties, strict=True):
            assert repr(u.uncertainty) == repr(shape_uncertainty(g, 0.6, 0.9, 0.7))
            assert repr(u.stability) == repr(class_stability(g, 0.6, 0.9, 0.7, seed=g.sarcomere_id))

    def test_shape_summary_with_two_sarcomeres(self):
        one = make_doc([shape("s1")], gui={"pixel_size": 3.0, "unit": "nm"})
        assert only(analyze_document(one)).shape_summary is None
        doc = make_doc(
            [shape("s1"), shape("s2", shifted(SQUARE, 25, 4))],
            gui={"pixel_size": 3.0, "unit": "nm"},
        )
        img = only(analyze_document(doc))
        summary = img.shape_summary
        expected_chain = analyze_sarcomere_chain(list(img.geometries))
        assert summary.chain.timestamp == ""
        for name in (
            "alpha_angles",
            "mean_aspect_ratio",
            "std_curviness",
            "dominant_shape",
            "unit",
        ):
            assert getattr(summary.chain, name) == getattr(expected_chain, name)
        expected_h = calculate_z_staircase_alignment(
            [g.z_line_1_points for g in img.geometries], pixel_to_unit=3.0, unit="nm"
        )
        assert repr(summary.alignment) == repr(expected_h)


class TestSarcomereIds:
    def test_rule_per_image_in_item_order(self):
        doc = make_doc(
            [
                shape("a1", image_id="a"),
                chain("a-chain", image_id="a"),
                shape("b1", image_id="b", skipped=True),
                shape("a2", image_id="a", m_skipped=True),
                pair("a-pair", ZLINES_A, ZLINES_B, image_id="a"),
                shape("b2", image_id="b"),
                shape("a3", image_id="a"),
            ],
            images=[image("a", "a.png"), image("b", "b.png")],
        )
        assert sarcomere_ids(doc) == {"a1": 1, "b1": 1, "a2": 2, "b2": 2, "a3": 3}

    def test_study_golden_counts_skipped_items(self):
        from sarcostat.io.annotations import read_annotations

        doc = read_annotations(Path(__file__).parent / "io" / "data" / "golden_v1_study.json")
        ids = sarcomere_ids(doc)
        assert ids == {"S0-001": 1, "S0-002": 2, "S0-003": 3, "S0-006": 1, "S0-007": 2, "S0-008": 4}

    def test_delete_then_add(self):
        """Deleting sarcomere 2 of 3 and adding one renumbers by position; seeds follow the ids."""
        p1, p2, p3, p4 = (shifted(SQUARE, 40 * k, k) for k in range(4))
        gui = {"click_sigma_z": 1.5}
        before = make_doc([shape("s1", p1), shape("s2", p2), shape("s3", p3)], gui=gui)
        after = copy.deepcopy(before)
        del after["items"][1]
        after["items"].append(shape("s4", p4))
        validate_annotations(after)
        assert sarcomere_ids(before) == {"s1": 1, "s2": 2, "s3": 3}
        assert sarcomere_ids(after) == {"s1": 1, "s3": 2, "s4": 3}
        img = only(analyze_document(after))
        assert [(s.item_id, s.geometry.sarcomere_id) for s in img.shapes] == [
            ("s1", 1),
            ("s3", 2),
            ("s4", 3),
        ]
        for g, u in zip(img.geometries, img.uncertainties, strict=True):
            assert u.stability == class_stability(g, 1.5, None, 1.0, seed=g.sarcomere_id)
        fresh = make_doc([shape("x", p1), shape("y", p3), shape("z", p4)], gui=gui)
        fresh_img = only(analyze_document(fresh))
        assert [u.stability for u in fresh_img.uncertainties] == [
            u.stability for u in img.uncertainties
        ]
        assert sarcomere_ids(fresh) == {"x": 1, "y": 2, "z": 3}

    def test_appended_item_gets_count_plus_one(self):
        doc = make_doc([shape("s1"), shape("s2", m_skipped=True), chain("c1")])
        n_shapes = sum(1 for it in doc["items"] if it["type"] == "shape")
        doc["items"].append(shape("new", shifted(SQUARE, 50)))
        assert sarcomere_ids(doc)["new"] == n_shapes + 1


class TestChains:
    def test_linearity_equals_core(self):
        gui = {"pixel_size": 0.5, "unit": "um", "expected_direction_deg": 20.0}
        doc = make_doc([chain("c1", branch_end=True)], gui=gui)
        (c,) = only(analyze_document(doc)).chains
        mids = np.array([((p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0) for p, q in CHAIN])
        expected = analyze_segment_sequence(
            mids, pixel_to_unit=0.5, unit_name="um", expected_direction=math.radians(20.0)
        )
        assert repr(c.metrics) == repr(expected)
        assert (c.chain, c.n_z_lines, c.branch_end, c.skip_reason) == (1, 4, True, None)
        assert c.metrics.timestamp is None and c.metrics.v_p_value is not None

    def test_no_reference_direction_skips_the_v_test(self):
        (c,) = only(analyze_document(make_doc([chain("c1")]))).chains
        assert c.metrics.v_p_value is None and c.branch_end is False

    def test_skipped_and_coincident_chains_keep_their_labels(self):
        twin = [CHAIN[0], CHAIN[0], CHAIN[1]]
        doc = make_doc([chain("c1", skipped=True), chain("c2", twin), chain("c3")])
        got = [(c.item_id, c.chain, c.skip_reason) for c in only(analyze_document(doc)).chains]
        assert got == [("c1", 1, "item_skipped"), ("c2", 2, "coincident_z_lines"), ("c3", 3, None)]


class TestRegister:
    def test_pair_equals_core(self):
        doc = make_doc([pair("p1", ZLINES_A, ZLINES_B)], gui={"pixel_size": 2.0, "unit": "nm"})
        (p,) = only(analyze_document(doc)).register
        expected = z_line_register(ZLINES_A, ZLINES_B, pixel_to_unit=2.0, unit="nm")
        assert repr(p.metrics) == repr(expected)
        assert (p.pair, p.myofibril_a, p.myofibril_b, p.n_z_lines_a, p.n_z_lines_b) == (
            1,
            1,
            2,
            5,
            5,
        )

    def test_k_m2_a_pair_without_overlap_never_shifts_the_labels(self):
        """Three pair items, the first without overlap: pairs 2 and 3 keep their labels."""
        doc = make_doc(
            [
                pair("p1", ZLINES_A, ZLINES_C),
                pair("p2", ZLINES_A, ZLINES_B),
                pair("p3", ZLINES_C, ZLINES_D),
            ]
        )
        first, second, third = only(analyze_document(doc)).register
        assert (first.pair, first.myofibril_a, first.myofibril_b) == (1, 1, 2)
        assert first.metrics is None and first.skip_reason == "no_overlap"
        assert (second.pair, second.myofibril_a, second.myofibril_b) == (2, 3, 4)
        assert (third.pair, third.myofibril_a, third.myofibril_b) == (3, 5, 6)
        assert second.skip_reason is None and third.skip_reason is None
        assert repr(second.metrics) == repr(z_line_register(ZLINES_A, ZLINES_B, unit="px"))
        assert repr(third.metrics) == repr(z_line_register(ZLINES_C, ZLINES_D, unit="px"))
        without = make_doc([pair("p2", ZLINES_A, ZLINES_B), pair("p3", ZLINES_C, ZLINES_D)])
        alone = only(analyze_document(without)).register
        assert [p.pair for p in alone] == [1, 2]
        assert [repr(p.metrics) for p in alone] == [repr(second.metrics), repr(third.metrics)]

    def test_no_overlap_reason_matches_the_core_message(self):
        with pytest.raises(ValueError, match=re.escape(analysis._NO_OVERLAP_MESSAGE)):
            z_line_register(ZLINES_A, ZLINES_C)

    def test_skipped_and_degenerate_pairs(self):
        same = [((5.0, 5.0), (5.0, 9.0))] * 2
        doc = make_doc([pair("p1", ZLINES_A, ZLINES_B, skipped=True), pair("p2", same, ZLINES_B)])
        first, second = only(analyze_document(doc)).register
        assert (first.pair, first.skip_reason, first.n_z_lines_a) == (1, "item_skipped", 0)
        assert (second.pair, second.myofibril_a, second.skip_reason) == (
            2,
            3,
            "degenerate_geometry",
        )

    def test_register_comes_from_pair_items_only(self):
        doc = make_doc(
            [chain("c1", ZLINES_A), chain("c2", ZLINES_B), pair("p1", ZLINES_C, ZLINES_D)]
        )
        img = only(analyze_document(doc))
        assert [p.item_id for p in img.register] == ["p1"]
        assert img.notices == ()
        assert [c.chain for c in img.chains] == [1, 2]

    def test_chain_only_file_gets_the_notice_and_no_register(self):
        doc = make_doc([chain("c1", ZLINES_A), shape("s1"), chain("c2", ZLINES_B)])
        img = only(analyze_document(doc))
        assert img.register == ()
        assert [(n.code, n.params) for n in img.notices] == [
            ("register_needs_pair_items", {"n_chain_items": 2})
        ]
        payload = json.loads(results_json(analyze_document(doc)))["images"][0]
        assert payload["register"] == []
        assert payload["notices"] == [
            {"code": "register_needs_pair_items", "params": {"n_chain_items": 2}}
        ]

    def test_one_chain_gets_no_notice(self):
        assert only(analyze_document(make_doc([chain("c1")]))).notices == ()


class TestDocument:
    def test_all_images_or_one(self):
        doc = make_doc(
            [shape("a1", image_id="a"), shape("b1", image_id="b")],
            images=[image("a", "a.png", 2.0), image("b", "b.png")],
        )
        both = analyze_document(doc)
        assert [im.image_id for im in both.images] == ["a", "b"]
        (b,) = analyze_document(doc, "b").images
        assert repr(b) == repr(both.images[1])
        assert b.settings.unit == "px" and both.images[0].settings.unit == "nm"

    def test_validates_first(self):
        doc = make_doc([shape("s1")])
        doc["items"][0]["points"]["z1"] = [[1.0, 2.0]]
        with pytest.raises(AnnotationFormatError):
            analyze_document(doc)

    def test_document_is_not_modified(self):
        doc = make_doc([shape("s1"), chain("c1"), pair("p1", ZLINES_A, ZLINES_B)], gui={})
        before = copy.deepcopy(doc)
        analyze_document(doc)
        assert doc == before

    def test_pure_function_of_the_document(self):
        doc = make_doc(
            [
                shape("s1"),
                shape("s2", shifted(SQUARE, 30)),
                chain("c1"),
                pair("p1", ZLINES_A, ZLINES_B),
            ],
            gui={"click_sigma_z": 0.5, "expected_direction_deg": 10.0},
        )
        text = results_json(analyze_document(doc))
        again = results_json(analyze_document(json.loads(json.dumps(doc))))
        assert text == again
        assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", text) is None
        assert '"timestamp": null' in text and '"timestamp": ""' not in text


class TestClickErrorEstimate:
    def test_equals_core_on_the_measured_sarcomeres(self):
        repeats = [
            SQUARE,
            ((10.4, 10.1), (9.8, 30.2), (20.3, 9.7), (20.1, 30.4), (29.6, 10.2), (30.3, 29.9)),
            ((9.7, 9.8), (10.2, 29.7), (19.8, 10.3), (19.6, 29.8), (30.4, 9.9), (29.9, 30.3)),
        ]
        doc = make_doc(
            [shape(f"r{k}", pts) for k, pts in enumerate(repeats)] + [shape("m", m_skipped=True)],
            gui={"pixel_size": 2.0, "unit": "nm"},
        )
        est = estimate_click_error(doc, "img")
        geoms = [
            calculate_sarcomere_geometry(*pts, sarcomere_id=k, pixel_to_unit=2.0, unit="nm")
            for k, pts in enumerate(repeats, start=1)
        ]
        expected = estimate_annotation_sigma(geoms)
        assert est.estimate == expected
        assert est.click_sigma_z == expected.sigma_z_along
        assert est.click_sigma_m == expected.sigma_m_along
        assert est.click_perp_ratio == expected.sigma_z_perp / expected.sigma_z_along

    def test_too_few_repeats(self):
        with pytest.raises(AnalysisError) as info:
            estimate_click_error(make_doc([shape("s1"), shape("s2", m_skipped=True)]), "img")
        assert info.value.code == "too_few_repeats" and info.value.params == {"n_repeats": 1}


def _module_imports(path: Path) -> set[str]:
    """Absolute names of every module imported anywhere in ``path`` (relative imports resolved)."""
    package = ".".join(path.relative_to(PACKAGE.parent).with_suffix("").parts[:-1])
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - node.level + 1]
                names.add(".".join([*base, node.module] if node.module else base))
            else:
                names.add(node.module or "")
    return names


def _hits(names: set[str], forbidden: tuple[str, ...]) -> set[str]:
    return {n for n in names for f in forbidden if n == f or n.startswith(f + ".")}


class TestLayering:
    FORBIDDEN = (
        "sarcostat.gui",
        "sarcostat.engine",
        "sarcostat.cli",
        "sarcostat.simulation",
        "pandas",
        "matplotlib",
        "PIL",
        "tkinter",
        "scipy",
        "typer",
        "rich",
    )

    @pytest.mark.parametrize("name", ["analysis.py", "export.py"])
    def test_analysis_and_export_import_core_and_io_only(self, name):
        names = _module_imports(PACKAGE / name)
        assert not _hits(names, self.FORBIDDEN)
        own = {n for n in names if n.startswith("sarcostat")}
        assert own <= {"sarcostat.analysis"} | {
            n for n in own if n.startswith(("sarcostat.core.", "sarcostat.io."))
        }

    def test_io_imports_nothing_from_analysis_export_or_engine(self):
        for path in sorted((PACKAGE / "io").rglob("*.py")):
            names = _module_imports(path)
            assert not _hits(
                names, ("sarcostat.analysis", "sarcostat.export", "sarcostat.engine")
            ), path

    def test_loader_is_gone(self):
        assert not (PACKAGE / "io" / "loader.py").exists()

    def test_no_summary_string_in_analysis(self):
        assert "summary_string" not in (PACKAGE / "analysis.py").read_text(encoding="utf-8")

    @pytest.mark.needs_cpython(reason="runs a subprocess; Emscripten has no processes")
    def test_importing_loads_no_scipy_and_no_ui(self):
        code = (
            "import json, sys\n"
            "import sarcostat.analysis, sarcostat.export\n"
            "print(json.dumps(sorted(sys.modules)))\n"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], check=True, capture_output=True, text=True
        ).stdout
        loaded = set(json.loads(out.strip().splitlines()[-1]))
        roots = {name.split(".")[0] for name in loaded}
        assert not roots & {"scipy", "pandas", "matplotlib", "PIL", "tkinter", "typer", "rich"}
        assert not _hits(loaded, ("sarcostat.gui", "sarcostat.engine", "sarcostat.cli"))
