"""Tests for sarcostat.export: results JSON, results CSV and the provenance sidecar.

Ported from the non-Tk tests of ``tests/gui/test_export.py`` (exports and provenance), driven by
:mod:`sarcostat.analysis` results instead of a Tk session.
"""

from __future__ import annotations

import csv
import dataclasses
import io
import json
import os
import re
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from sarcostat.analysis import analyze_document, analyze_file
from sarcostat.export import (
    CHAIN_COLUMNS,
    COMMON_COLUMNS,
    LANDMARK_COLUMNS,
    REGISTER_COLUMNS,
    SHAPE_COLUMNS,
    UNCERTAINTY_COLUMNS,
    export_file_names,
    provenance_sidecar,
    results_csv,
    results_document,
    results_json,
    write_exports,
)
from sarcostat.io.annotations import read_annotations, sha256_file, write_annotations
from sarcostat.io.provenance import source_digest

from .test_analysis import (
    SQUARE,
    ZLINES_A,
    ZLINES_B,
    ZLINES_C,
    ZLINES_D,
    chain,
    image,
    make_doc,
    pair,
    shape,
    shifted,
)

DATA = Path(__file__).resolve().parent / "io" / "data"


def _rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _no_absolute_paths(folder: Path, names, root: Path) -> None:
    for name in names:
        assert "/" not in name and "\\" not in name, name
        text = (folder / name).read_text(encoding="utf-8")
        assert str(root) not in text, name
        assert str(Path.home()) not in text, name
        values = (
            list(_strings(json.loads(text)))
            if name.endswith(".json")
            else [cell for row in csv.reader(io.StringIO(text)) for cell in row]
        )
        for value in values:
            assert not PurePosixPath(value).is_absolute(), (name, value)
            assert not PureWindowsPath(value).is_absolute(), (name, value)


def _on_disk(tmp_path: Path, items, gui=None):
    """Write an image file and its annotation file; return (annotation path, image path)."""
    folder = tmp_path / "images"
    folder.mkdir()
    picture = folder / "micrograph.png"
    picture.write_bytes(bytes(range(256)) * 7)
    doc = make_doc(items, gui=gui, images=[image(file_name="micrograph.png")])
    doc["images"][0]["sha256"] = sha256_file(picture)
    path = folder / "micrograph_annotations.json"
    write_annotations(doc, path)
    return path, picture


class TestResultsExports:
    def test_files_and_units_come_from_the_results(self, tmp_path) -> None:
        doc = make_doc([chain("c1")], gui={"pixel_size": 0.5, "unit": "um"})
        results = analyze_document(doc)
        written = write_exports(results, tmp_path / "out", "T")
        assert written == ["T_results.json", "T_results.csv", "T_provenance.json"]
        assert sorted(p.name for p in (tmp_path / "out").iterdir()) == sorted(written)
        (row,) = _rows((tmp_path / "out" / "T_results.csv").read_text())
        assert row["unit"] == "um" and row["n_segments"] == "3" and row["v_p_value"] == ""
        payload = json.loads((tmp_path / "out" / "T_results.json").read_text())
        metrics = payload["images"][0]["chains"][0]["metrics"]
        assert metrics["v_p_value"] is None and metrics["metadata"]["unit"] == "um"

    def test_rows_keep_their_own_units(self) -> None:
        doc = make_doc(
            [chain("a1", image_id="a"), chain("b1", image_id="b")],
            images=[image("a", "a.png", 2.5), image("b", "b.png")],
        )
        rows = _rows(results_csv(analyze_document(doc)))
        assert [(r["image_id"], r["unit"]) for r in rows] == [("a", "nm"), ("b", "px")]
        assert all(r["v_p_value"] == "" and r["rayleigh_p"] for r in rows)
        with_direction = make_doc([chain("c1")], gui={"expected_direction_deg": 0.0})
        (row,) = _rows(results_csv(analyze_document(with_direction)))
        assert float(row["v_p_value"]) < 1.0 and float(row["v_test_direction_degrees"]) == 0.0

    def test_sarcomeres_csv_and_json(self) -> None:
        doc = make_doc([shape("s1"), shape("s2", shifted(SQUARE, 20))])
        results = analyze_document(doc)
        rows = _rows(results_csv(results))
        assert [r["shape_classification"] for r in rows] == ["square", "square"]
        assert [r["label"] for r in rows] == ["1", "2"]
        payload = json.loads(results_json(results))["images"][0]
        summary = payload["shape_summary"]
        assert summary["chain"]["n_sarcomeres"] == 2 and "alpha_mean" in summary["chain"]
        assert "timestamp" not in summary["chain"] and "sarcomeres" not in summary["chain"]
        assert summary["z_line_alignment"]["n_measurements"] == 1
        assert [s["geometry"]["sarcomere_id"] for s in payload["sarcomeres"]] == [1, 2]

    def test_uncertainty_columns_only_with_a_click_error(self) -> None:
        plain = results_csv(analyze_document(make_doc([shape("s1")])))
        header = plain.splitlines()[0].split(",")
        assert header == [*COMMON_COLUMNS, *SHAPE_COLUMNS, *LANDMARK_COLUMNS]
        doc = make_doc([shape("s1"), shape("s2", m_skipped=True)], gui={"click_sigma_z": 0.5})
        rows = _rows(results_csv(analyze_document(doc)))
        assert list(rows[0]) == [
            *COMMON_COLUMNS,
            *SHAPE_COLUMNS,
            *UNCERTAINTY_COLUMNS,
            *LANDMARK_COLUMNS,
        ]
        assert rows[0]["click_sigma_z"] == "0.5" and 0 <= float(rows[0]["class_stability"]) <= 1
        assert rows[1]["skipped"] == "true" and rows[1]["skip_reason"] == "m_line_skipped"
        assert rows[1]["aspect_ratio"] == "" and rows[1]["class_stability"] == ""
        payload = json.loads(results_json(analyze_document(doc)))["images"][0]["sarcomeres"]
        assert payload[0]["uncertainty"]["sigma_z_along"] == 0.5
        assert payload[1]["uncertainty"] is None and payload[1]["geometry"] is None

    def test_columns_follow_the_item_types_present(self) -> None:
        chains = results_csv(analyze_document(make_doc([chain("c1")])))
        assert chains.splitlines()[0].split(",") == [*COMMON_COLUMNS, *CHAIN_COLUMNS]
        pairs = results_csv(analyze_document(make_doc([pair("p1", ZLINES_A, ZLINES_B)])))
        assert pairs.splitlines()[0].split(",") == [*COMMON_COLUMNS, *REGISTER_COLUMNS]
        mixed = make_doc([pair("p1", ZLINES_A, ZLINES_B), chain("c1"), shape("s1")])
        rows = _rows(results_csv(analyze_document(mixed)))
        assert [(r["item_type"], r["item_id"]) for r in rows] == [
            ("shape", "s1"),
            ("chain", "c1"),
            ("pair", "p1"),
        ]
        assert rows[0]["register_index"] == "" and rows[2]["aspect_ratio"] == ""
        empty = results_csv(analyze_document(make_doc([])))
        assert empty == ",".join(COMMON_COLUMNS) + "\n"

    def test_k_m2_rows_keep_their_labels(self) -> None:
        doc = make_doc(
            [
                pair("p1", ZLINES_A, ZLINES_C),
                pair("p2", ZLINES_A, ZLINES_B),
                pair("p3", ZLINES_C, ZLINES_D),
            ]
        )
        results = analyze_document(doc)
        rows = _rows(results_csv(results))
        assert [(r["label"], r["myofibril_a"], r["myofibril_b"]) for r in rows] == [
            ("1", "1", "2"),
            ("2", "3", "4"),
            ("3", "5", "6"),
        ]
        assert [(r["skipped"], r["skip_reason"]) for r in rows] == [
            ("true", "no_overlap"),
            ("false", ""),
            ("false", ""),
        ]
        assert rows[0]["register_index"] == "" and rows[1]["register_index"] != ""
        entries = json.loads(results_json(results))["images"][0]["register"]
        assert [(e["pair"], e["myofibril_a"], e["myofibril_b"], e["skipped"]) for e in entries] == [
            (1, 1, 2, True),
            (2, 3, 4, False),
            (3, 5, 6, False),
        ]
        assert entries[0]["metrics"] is None and entries[0]["skip_reason"] == "no_overlap"

    def test_results_json_is_canonical(self) -> None:
        doc = make_doc(
            [shape("s1"), chain("c1"), pair("p1", ZLINES_A, ZLINES_B)],
            gui={"click_sigma_z": 0.5, "expected_direction_deg": 5.0},
        )
        text = results_json(analyze_document(doc))
        assert text.endswith("}\n") and text.isascii()
        payload = json.loads(text, parse_constant=lambda token: pytest.fail(token))
        again = json.dumps(
            payload, sort_keys=True, indent=2, separators=(",", ": "), ensure_ascii=True
        )
        assert text == again + "\n"
        assert payload == json.loads(json.dumps(results_document(analyze_document(doc))))
        assert payload["format"] == "sarcostat-results" and payload["format_version"] == "1.0"

    def test_non_finite_values_are_null_and_empty(self) -> None:
        results = analyze_document(make_doc([pair("p1", ZLINES_A, ZLINES_B)]))
        img = results.images[0]
        (p,) = img.register
        metrics = dataclasses.replace(p.metrics, register_spread=float("inf"), h_mean=float("nan"))
        broken = dataclasses.replace(
            results,
            images=(dataclasses.replace(img, register=(dataclasses.replace(p, metrics=metrics),)),),
        )
        text = results_json(broken)
        entry = json.loads(text)["images"][0]["register"][0]["metrics"]
        assert entry["register_spread"] is None and entry["h_mean"] is None
        (row,) = _rows(results_csv(broken))
        assert row["register_spread"] == "" and row["h_mean"] == ""

    def test_same_text_from_a_file_and_from_its_document(self, tmp_path) -> None:
        path, _picture = _on_disk(tmp_path, [shape("s1"), chain("c1")], gui={})
        from_file = analyze_file(path, image_dir=path.parent)
        from_doc = analyze_document(read_annotations(path))
        assert results_json(from_file) == results_json(from_doc)
        assert results_csv(from_file) == results_csv(from_doc)

    def test_golden_files_export(self) -> None:
        for path in sorted(DATA.glob("golden_v1_*.json")):
            results = analyze_file(path)
            assert json.loads(results_json(results))["images"]
            assert len(_rows(results_csv(results))) == len(read_annotations(path)["items"])


class TestProvenance:
    def test_export_has_a_provenance_sidecar(self, tmp_path) -> None:
        gui = {"pixel_size": 0.5, "unit": "um", "expected_direction_deg": 15.0}
        path, picture = _on_disk(tmp_path, [chain("c1"), shape("s1")], gui=gui)
        results = analyze_file(path, image_dir=path.parent)
        inputs = [(path.name, sha256_file(path))]
        written = write_exports(results, tmp_path / "out", path.stem, inputs=inputs)
        sidecar = tmp_path / "out" / written[-1]
        assert written[-1] == export_file_names(path.stem)["provenance"]
        payload = json.loads(sidecar.read_text())
        block = payload["provenance"]
        assert block["source_digest"] == source_digest()
        assert block["files"] == [
            {"file_name": path.name, "sha256": sha256_file(path)},
            {"file_name": "micrograph.png", "sha256": sha256_file(picture)},
        ]
        (settings,) = block["parameters"]["images"]
        assert settings["pixel_size"] == 0.5 and settings["unit"] == "um"
        assert settings["reference_direction_deg"] == 15.0
        assert block["parameters"]["class_stability_seed"] == "sarcomere_id"
        assert payload["outputs"] == written[:-1]
        _no_absolute_paths(tmp_path / "out", written, tmp_path)

    def test_results_record_the_image_name_only(self, tmp_path) -> None:
        path, _picture = _on_disk(tmp_path, [chain("c1")])
        results = analyze_file(path)
        assert _rows(results_csv(results))[0]["file_name"] == "micrograph.png"
        assert results_document(results)["images"][0]["file_name"] == "micrograph.png"
        written = write_exports(results, tmp_path / "w", "h", kinds=["results_csv"])
        assert written == ["h_results.csv", "h_provenance.json"]
        sidecar = json.loads((tmp_path / "w" / "h_provenance.json").read_text())
        assert sidecar["outputs"] == ["h_results.csv"]
        _no_absolute_paths(tmp_path / "w", written, tmp_path)

    def test_sarcomere_export_has_a_provenance_sidecar(self, tmp_path) -> None:
        path, picture = _on_disk(tmp_path, [shape("s1")], gui={"click_sigma_z": 0.5})
        results = analyze_file(path, image_dir=path.parent)
        written = write_exports(results, tmp_path / "out", "T")
        payload = json.loads((tmp_path / "out" / "T_provenance.json").read_text())
        assert payload["provenance"]["files"][0]["sha256"] == sha256_file(picture)
        assert payload["provenance"]["parameters"]["images"][0]["click_sigma_z"] == 0.5
        _no_absolute_paths(tmp_path / "out", written, tmp_path)

    def test_every_export_carries_a_sidecar(self, tmp_path) -> None:
        results = analyze_document(make_doc([shape("s1")]))
        for kinds in (["results_json"], ["results_csv"], ["results_json", "results_csv"]):
            out = tmp_path / "-".join(kinds)
            written = write_exports(results, out, "T", kinds=kinds)
            assert written[-1] == "T_provenance.json" and len(written) == len(kinds) + 1
            block = json.loads((out / "T_provenance.json").read_text())["provenance"]
            assert re.fullmatch(r"[0-9a-f]{64}", block["source_digest"])
            assert block["files"] == [{"file_name": "synthetic.png", "sha256": "a" * 64}]

    def test_sidecar_text_is_deterministic_and_has_no_time(self) -> None:
        results = analyze_document(make_doc([shape("s1")]))
        text = provenance_sidecar(results, ["x_results.json"])
        assert text == provenance_sidecar(results, ["x_results.json"])
        assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", text) is None

    @pytest.mark.parametrize("name", ["", "a/b.json", "a\\b.json", "..", os.sep + "abs.json"])
    def test_names_must_be_plain_file_names(self, tmp_path, name) -> None:
        results = analyze_document(make_doc([]))
        with pytest.raises(ValueError, match="without folders"):
            provenance_sidecar(results, [name])
        with pytest.raises(ValueError, match="without folders"):
            write_exports(results, tmp_path, name)

    def test_unknown_kind(self, tmp_path) -> None:
        with pytest.raises(ValueError, match="unknown export kinds"):
            write_exports(analyze_document(make_doc([])), tmp_path, "T", kinds=["plots"])
        assert list(tmp_path.iterdir()) == []
