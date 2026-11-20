"""Sentences, tables and numbers of :mod:`sarcostat.engine.reports`.

Ported from the desktop tests of the register wording, the window texts and
the text reports (``tests/gui/test_register_wording.py``,
``tests/gui/test_dialog_texts.py`` and ``TestReports`` of
``tests/gui/test_export.py``), plus the rules of the one number formatter.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from sarcostat.analysis import AnalysisResults, analyze_document, analyze_file
from sarcostat.engine import reports
from sarcostat.engine.reports import (
    format_number,
    report_lines,
    results_document,
    results_report,
    results_view,
)
from sarcostat.export import results_document as export_document
from sarcostat.export import results_json
from sarcostat.io.annotations import read_annotations

DATA = Path(__file__).resolve().parents[1] / "io" / "data"
GUI = DATA / "golden_v1_gui.json"
STUDY = DATA / "golden_v1_study.json"
FORBIDDEN = ("no lateral coupling", "no drift detected", "stationary register)")
HYGIENE_PHRASES = (
    "no lateral coupling",
    "no drift detected",
    "holds for any σ",
    "holds for any sigma",
)
DRIFT_SENTENCE = (
    "with few Z-line pairs the test has little power, so this does not show a stationary register"
)


def _texts(report: dict[str, Any]) -> list[str]:
    """Every string of a report (sentences, titles, columns, cells, notes)."""
    out: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk({k: v for k, v in report.items() if k != "plot_data"})
    return out


def _gui_document() -> dict[str, Any]:
    return results_document(analyze_file(GUI))


def _with_drift_p(results: AnalysisResults, p: float | None) -> AnalysisResults:
    images = []
    for image in results.images:
        register = tuple(
            dataclasses.replace(
                pair, metrics=dataclasses.replace(pair.metrics, serial_dependence_p=p)
            )
            if pair.metrics is not None
            else pair
            for pair in image.register
        )
        images.append(dataclasses.replace(image, register=register))
    return AnalysisResults(images=tuple(images))


@pytest.mark.parametrize("p", [0.01, 0.5])
def test_interpretation_avoids_unsupported_claims(p) -> None:
    report = results_report(_with_drift_p(analyze_file(GUI), p))["images"][0]
    text = "\n".join(_texts(report))
    for phrase in FORBIDDEN:
        assert phrase not in text
    line = next(s for s in report["sentences"] if s.startswith("Pair 1:") and "drift" in s)
    if p < 0.05:
        assert "not stationary over this tract" in line
    else:
        assert "little power" in line and "does not show a stationary register" in line


def test_static_texts_have_no_hard_wrapped_sentence() -> None:
    names = [n for n in dir(reports) if n.isupper() and not n.startswith("_")]
    found = 0
    for name in names:
        value = getattr(reports, name)
        strings = (
            [value]
            if isinstance(value, str)
            else list(value.values())
            if isinstance(value, dict)
            else list(value)
            if isinstance(value, tuple)
            else []
        )
        for text in strings:
            if isinstance(text, str):
                found += 1
                assert "\n" not in text, (name, text)
    assert found > 30
    for path in (GUI, STUDY):
        for image in results_report(analyze_file(path))["images"]:
            for text in _texts(image):
                assert "\n" not in text, text


def test_shape_placeholder_and_no_chain_section_for_one_sarcomere() -> None:
    assert "6-point annotation" in reports.SHAPE_HINT
    report = results_view(_gui_document()["images"][0])
    assert any(s.startswith("Sarcomere 1: hourglass") for s in report["sentences"])
    assert "shape_summary" not in report["tables"]
    assert not any("Alpha" in s for s in report["sentences"])


def test_straightness_text_mentions_heading_basis() -> None:
    report = results_view(_gui_document()["images"][0])
    text = "\n".join(_texts(report))
    assert "headings" in text and "0° = right, 90° = down" in text


class TestFormatNumber:
    @pytest.mark.parametrize(
        "value, kind, unit, text",
        [
            (412.345, "length", "nm", "412.35 nm"),
            (3.0, "length", "px", "3.00 px"),
            (1.05381, "ratio", None, "1.054"),
            (0.99996, "index", None, "1.0000"),
            (5.0004, "statistic", None, "5.000"),
            (88.04, "angle", None, "88.0°"),
            (-0.04, "angle", None, "0.0°"),
            (-4.4e-16, "statistic", None, "0.000"),
            (0.669, "percent", None, "67%"),
            (6.0, "count", None, "6"),
            (0.03355, "p", None, "p = 0.0336"),
            (2e-7, "p", None, "p < 0.0001"),
            (None, "length", "nm", "n/a"),
            (float("nan"), "ratio", None, "n/a"),
            (float("inf"), "length", "nm", "n/a"),
            (True, "count", None, "n/a"),
        ],
    )
    def test_kinds(self, value, kind, unit, text) -> None:
        assert format_number(value, kind, unit) == text

    def test_an_unknown_kind_is_refused(self) -> None:
        with pytest.raises(ValueError, match="kind"):
            format_number(1.0, "speed")

    def test_lengths_carry_their_unit_and_angles_are_in_degrees(self) -> None:
        doc = _gui_document()
        image = copy.deepcopy(doc["images"][0])
        image["settings"]["unit"] = "µm"
        tables = results_view(image)["tables"]
        lengths = {
            "sarcomeres": ("Sarcomere length", "Z-line 1", "Z-line 2", "M-line"),
            "chains": ("Path length",),
            "register": ("Spread", "Mean |offset| H", "H out of register"),
        }
        angles = {"chains": ("Mean heading",), "register": ("Phase",)}
        checked = 0
        for table, columns in lengths.items():
            for column in columns:
                k = tables[table]["columns"].index(column)
                for row in tables[table]["rows"]:
                    if row[k]:
                        assert row[k].endswith(" µm"), (table, column, row[k])
                        checked += 1
        for table, columns in angles.items():
            for column in columns:
                k = tables[table]["columns"].index(column)
                for row in tables[table]["rows"]:
                    assert row[k].endswith("°"), (table, column, row[k])
                    checked += 1
        assert checked >= 8


class TestWording:
    def test_hygiene_phrases_are_absent_and_the_drift_sentence_is_present(self) -> None:
        source = Path(reports.__file__).read_text(encoding="utf-8")
        texts = [source]
        for path in (GUI, STUDY):
            for p in (0.01, 0.5, None):
                for image in results_report(_with_drift_p(analyze_file(path), p))["images"]:
                    texts += _texts(image)
        for text in texts:
            for phrase in HYGIENE_PHRASES:
                assert phrase not in text.lower(), phrase
        assert DRIFT_SENTENCE in " ".join(source.split()).replace('" "', "")
        sentences = results_report(_with_drift_p(analyze_file(GUI), 0.5))["images"][0]["sentences"]
        assert any(DRIFT_SENTENCE in s for s in sentences)

    def test_pairs_keep_their_labels_when_the_first_has_no_overlap(self) -> None:
        doc = _gui_document()
        image = doc["images"][0]
        computed = image["register"][0]
        skipped = dict(computed, skipped=True, skip_reason="no_overlap", metrics=None)
        image["register"] = [
            dict(skipped, pair=1, myofibril_a=1, myofibril_b=2, item_id="p1"),
            dict(computed, pair=2, myofibril_a=3, myofibril_b=4, item_id="p2"),
            dict(computed, pair=3, myofibril_a=5, myofibril_b=6, item_id="p3"),
        ]
        report = results_view(image)
        sentences = report["sentences"]
        assert "Pair 1: no overlapping Z-lines (skipped)" in sentences
        assert any(s.startswith("Pair 2 (myofibrils 3 and 4):") for s in sentences)
        assert any(s.startswith("Pair 3 (myofibrils 5 and 6):") for s in sentences)
        rows = report["tables"]["register"]["rows"]
        assert [row[0] for row in rows] == ["1", "2", "3"]
        assert [row[1] for row in rows] == ["1 and 2", "3 and 4", "5 and 6"]
        assert rows[0][-1] == "Pair 1: no overlapping Z-lines (skipped)"

    def test_the_chain_only_notice_is_one_sentence(self) -> None:
        doc = read_annotations(GUI)
        items = [i for i in doc["items"] if i["type"] != "pair"]
        second = copy.deepcopy(next(i for i in items if i["type"] == "chain"))
        second["item_id"] = "gui-5"
        doc["items"] = [*items, second]
        report = results_report(analyze_document(doc))["images"][0]
        notices = [s for s in report["sentences"] if "pair item" in s]
        assert notices == [
            "The Z-line register needs pair items: this image has 2 chain items and no pair "
            "item, so no register is computed."
        ]
        assert "register" not in report["tables"]

    def test_skip_reasons_are_words(self) -> None:
        doc = results_document(analyze_file(STUDY))
        sentences = [s for image in results_report(doc)["images"] for s in image["sentences"]]
        assert "Sarcomere 2: M-line not identifiable (skipped)" in sentences
        assert "Sarcomere 3: not annotated (skipped)" in sentences
        image = copy.deepcopy(_gui_document()["images"][0])
        for code in reports.REASON_WORDS:
            image["chains"][0].update(metrics=None, skipped=True, skip_reason=code)
            assert (
                f"Chain 1: {reports.REASON_WORDS[code]} (skipped)"
                in results_view(image)["sentences"]
            )

    def test_tests_and_persistence_have_their_cases(self) -> None:
        image = copy.deepcopy(_gui_document()["images"][0])
        m = image["chains"][0]["metrics"]
        sentences = results_view(image)["sentences"]
        assert "Chain 1: headings cluster around the reference direction of 0.0°" in " ".join(
            sentences
        )
        assert any("headings are not uniform (Rayleigh" in s for s in sentences)
        m.update(v_p_value=0.4, rayleigh_p=0.3, persistence_length=10.0, total_length=1756.0)
        sentences = results_view(image)["sentences"]
        assert any(s.startswith("Chain 1: no evidence that headings cluster") for s in sentences)
        assert any("does not reject uniform headings" in s for s in sentences)
        assert "Chain 1: persistence length 10.00 nm." in sentences
        m.update(persistence_length=1e6)
        assert any("not resolved, above 10 times" in s for s in results_view(image)["sentences"])
        m.update(persistence_length=None, persistence_amplitude=None, v_p_value=None)
        image["settings"]["reference_direction_deg"] = None
        sentences = results_view(image)["sentences"]
        assert "Chain 1: persistence length not estimable." in sentences
        assert "Chain 1: V-test not run, no reference direction is set." in sentences
        image["register"][0]["metrics"]["serial_dependence_p"] = None
        assert (
            "Pair 1: drift test not run (fewer than 3 Z-line pairs)."
            in results_view(image)["sentences"]
        )

    def test_no_scale_and_no_click_error(self) -> None:
        image = copy.deepcopy(results_document(analyze_file(STUDY))["images"][0])
        image["settings"].update(scale_source="none", unit="px", pixel_size=1.0)
        sentences = results_view(image)["sentences"]
        assert sentences[0] == "No scale is set: lengths are in pixels."
        assert sentences[1].startswith("No click error is set")
        assert "Sarcomere 1: hourglass, SI 1.054; AsR 1.356, Cur 1.429" in sentences[3]
        assert sentences[3].endswith("px.")

    def test_a_fresh_web_session_has_no_scale(self) -> None:
        from sarcostat.engine.annotate import AnnotationSession

        session = AnnotationSession()
        session.doc_new(
            {"file_name": "cells.png", "sha256": "0" * 64, "width_px": 300, "height_px": 200}
        )
        for x, y in [(40, 50), (40, 90), (60, 52), (60, 88), (80, 50), (80, 90)]:
            session.shape_click(x, y)
        session.shape_confirm()
        assert session.document()["gui"]["unit"] == "px"
        sentences = results_view(session.analysis())["sentences"]
        assert sentences[0] == "No scale is set: lengths are in pixels."
        assert "sarcomere length 40.00 px" in sentences[2]
        assert not any("1 px = 1.00 px" in s for s in sentences)
        session.doc_set_scale(4.39, "nm")
        sentences = results_view(session.analysis())["sentences"]
        assert sentences[0] == "Scale: 1 px = 4.39 nm, as saved in the annotation file."
        assert "sarcomere length 175.60 nm" in sentences[2]
        session.doc_set_scale(1.0, "px")
        assert results_view(session.analysis())["sentences"][0] == (
            "No scale is set: lengths are in pixels."
        )

    def test_click_error_and_class_stability(self) -> None:
        sentences = results_view(_gui_document()["images"][0])["sentences"]
        assert sentences[1] == (
            "Click error: σ = 2.50 nm along the lines; across them it is 1.000 times as large."
        )
        assert (
            "Sarcomere 1: kept as hourglass in 67% of simulated re-annotations with your click "
            "error, σ = 2.50 nm." in sentences
        )
        assert "SI 1.054 ± 0.009" in sentences[3]

    def test_an_image_without_items(self) -> None:
        image = copy.deepcopy(_gui_document()["images"][0])
        image.update(sarcomeres=[], chains=[], register=[], shape_summary=None)
        report = results_view(image)
        assert report["sentences"][-1] == reports.EMPTY_IMAGE
        assert report["tables"] == {}


class TestInputsAndOutputs:
    def test_results_documents_and_json_give_the_same_report(self) -> None:
        results = analyze_file(STUDY)
        expected = results_report(results)
        assert results_report(export_document(results)) == expected
        assert results_report(json.loads(results_json(results))) == expected

    def test_reports_are_json_safe_and_hold_no_timestamp(self) -> None:
        for path in (GUI, STUDY):
            report = results_report(export_document(analyze_file(path)))
            text = json.dumps(report, allow_nan=False)
            assert "timestamp" not in text.lower()
            assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", text)
            for image in report["images"]:
                assert set(image) == {"image_id", "file_name", "sentences", "tables", "plot_data"}
                for table in image["tables"].values():
                    assert set(table) == {"title", "columns", "rows", "notes"}
                    assert all(len(row) == len(table["columns"]) for row in table["rows"])
                    assert all(isinstance(cell, str) for row in table["rows"] for cell in row)

    def test_results_view_takes_image_results_or_their_document(self) -> None:
        results = analyze_file(STUDY)
        documents = export_document(results)["images"]
        for image, entry in zip(results.images, documents, strict=True):
            view = results_view(image)
            assert set(view) == {"sentences", "tables", "plot_data"}
            assert set(view["plot_data"]) == {"shape", "chains", "register"}
            assert view == results_view(entry)
            json.dumps(view, allow_nan=False)

    def test_plot_data_holds_raw_numbers(self) -> None:
        plot = results_report(analyze_file(GUI))["images"][0]["plot_data"]
        assert plot["shape"][0]["shape_index"] == pytest.approx(1.0538166876780954)
        assert plot["chains"][0]["headings_deg"] == [0.0] * 5
        assert plot["register"][0]["correlation_lags"] == [1, 2]
        assert plot["register"][0]["phases_deg"][0] == pytest.approx(88.0)

    def test_report_lines_head_each_image(self) -> None:
        lines = report_lines(analyze_file(STUDY))
        assert lines[0] == "human_skeletal_muscle_tem_1.jpg (image tem1)"
        assert "" in lines
        assert lines[lines.index("") + 1] == "human_skeletal_muscle_tem_3.jpg (image tem3)"

    @pytest.mark.needs_cpython(reason="runs a subprocess; Emscripten has no processes")
    def test_importing_reports_loads_no_metric_code(self) -> None:
        code = (
            "import json, sys\nimport sarcostat.engine.reports\n"
            "print(json.dumps(sorted(m for m in sys.modules if m.startswith(('sarcostat.core', "
            "'sarcostat.analysis', 'sarcostat.export', 'scipy')))))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True
        )
        assert json.loads(result.stdout.strip().splitlines()[-1]) == []
