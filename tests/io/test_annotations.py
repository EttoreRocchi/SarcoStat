"""Tests for the annotation file format v1 (sarcostat.io.annotations).

The corpus in ``tests/io/data`` is built by ``build_corpus.py`` from
synthetic annotations on the repository micrographs; the hand-written
validator is checked against the JSON Schema shipped with the package.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

from sarcostat.io import annotations as ann
from sarcostat.io.annotations import (
    DISPLAY_TRANSFORMS,
    FORMAT_NAME,
    FORMAT_VERSION,
    GUI_MODES,
    ITEM_TYPES,
    SKIPPABLE_PARTS,
    AnnotationFormatError,
    ImageMismatchError,
    MissingImageWarning,
    dumps_annotations,
    load_schema,
    read_annotations,
    sha256_file,
    validate_annotations,
    write_annotations,
)

DATA = Path(__file__).resolve().parent / "data"
REPO = DATA.parents[2]
EXAMPLES = REPO / "examples"
FORMAT_DOC = REPO / "docs" / "annotation_format.md"
needs_format_doc = pytest.mark.skipif(
    not FORMAT_DOC.exists(), reason="docs/annotation_format.md is not in this tree"
)
GOLDEN = sorted(DATA.glob("golden_v1_*.json"))
INVALID_DIR = DATA / "invalid"
MANIFEST = json.loads((DATA / "invalid_manifest.json").read_text())
TEXT_CASES = {
    "duplicate_key",
    "infinity_token",
    "nan_token",
    "overflow_float",
    "truncated",
    "utf8_bom",
}


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_corpus", DATA / "build_corpus.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def schema_validator():
    jsonschema = pytest.importorskip("jsonschema")
    schema = load_schema()
    cls = jsonschema.validators.validator_for(schema)
    cls.check_schema(schema)
    return cls(schema)


@pytest.fixture
def study_doc():
    return json.loads((DATA / "golden_v1_study.json").read_text())


def _rejects(doc, match: str | None = None) -> None:
    with pytest.raises(AnnotationFormatError, match=match):
        validate_annotations(doc)


class TestGoldenFiles:
    def test_corpus_exists(self):
        assert {p.name for p in GOLDEN} == {"golden_v1_gui.json", "golden_v1_study.json"}

    @pytest.mark.parametrize("path", GOLDEN, ids=lambda p: p.name)
    def test_round_trip_is_byte_identical(self, path, tmp_path):
        doc = read_annotations(path)
        assert dumps_annotations(doc).encode("ascii") == path.read_bytes()
        out = tmp_path / path.name
        write_annotations(doc, out)
        assert out.read_bytes() == path.read_bytes()
        assert read_annotations(out) == doc

    @pytest.mark.parametrize("path", GOLDEN, ids=lambda p: p.name)
    def test_golden_passes_validator_and_schema(self, path, schema_validator):
        doc = json.loads(path.read_text())
        validate_annotations(doc)
        schema_validator.validate(doc)

    def test_corpus_is_up_to_date(self):
        builder = _load_builder()
        assert (
            dumps_annotations(builder.golden_study()) == (DATA / "golden_v1_study.json").read_text()
        )
        assert dumps_annotations(builder.golden_gui()) == (DATA / "golden_v1_gui.json").read_text()
        for name, (build, _error, _schema) in builder.INVALID.items():
            assert build().encode("utf-8") == (INVALID_DIR / f"{name}.json").read_bytes(), name

    def test_study_file_covers_every_type_and_transform(self, study_doc):
        assert {item["type"] for item in study_doc["items"]} == set(ITEM_TYPES)
        assert {item["display_transform"] for item in study_doc["items"]} == set(DISPLAY_TRANSFORMS)

    def test_free_gui_session_values(self):
        doc = read_annotations(DATA / "golden_v1_gui.json")
        assert doc["annotator_id"] is doc["session_id"] is doc["study_id"] is None
        assert set(doc["gui"]) == {
            "pixel_size",
            "unit",
            "expected_direction_deg",
            "click_sigma_z",
            "click_sigma_m",
            "click_perp_ratio",
            "mode",
            "open_myofibril",
        }
        assert doc["gui"]["mode"] in GUI_MODES
        (image,) = doc["images"]
        for item in doc["items"]:
            assert item["target_id"] is None
            assert item["display_transform"] == "identity"
            assert item["kit_crop_offset"] == [0, 0]
            assert item["kit_crop_size"] == [image["width_px"], image["height_px"]]

    def test_clicks_map_onto_points(self, study_doc):
        """The golden clicks follow the transforms of sarcostat.io.study exactly."""
        study = pytest.importorskip("sarcostat.io.study")
        np = pytest.importorskip("numpy")
        for item in study_doc["items"]:
            if item["skipped"]:
                continue
            clicks = np.array([[c["x"], c["y"]] for c in item["raw_clicks"]])
            crop = study.inverse_transform_points(
                clicks, item["display_transform"], item["kit_crop_size"]
            )
            original = crop + np.array(item["kit_crop_offset"], dtype=float)
            assert original.tolist() == list(_numbers_as_points(item["points"]))

    def test_coordinates_are_dyadic(self, study_doc):
        """Golden coordinates are multiples of 1/1024 px, so transforms are exact."""

        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key in ("x", "y", "points"):
                        yield from _numbers(value)
                    else:
                        yield from walk(value)
            elif isinstance(node, list):
                for value in node:
                    yield from walk(value)

        values = list(walk(study_doc["items"]))
        assert values and all((v * 1024).is_integer() for v in values)


def _numbers_as_points(points):
    flat = list(_numbers_in_order(points))
    return [flat[i : i + 2] for i in range(0, len(flat), 2)]


def _numbers_in_order(points):
    if isinstance(points, dict):
        keys = ("z1", "m", "z2") if "z1" in points else sorted(points)
        for key in keys:
            if key in points:
                yield from _numbers(points[key])
    else:
        yield from _numbers(points)


def _numbers(node):
    if isinstance(node, (int, float)) and not isinstance(node, bool):
        yield float(node)
    elif isinstance(node, list):
        for value in node:
            yield from _numbers(value)
    elif isinstance(node, dict):
        for value in node.values():
            yield from _numbers(value)


class TestInvalidCorpus:
    def test_manifest_matches_folder(self):
        assert {p.stem for p in INVALID_DIR.glob("*.json")} == set(MANIFEST)

    @pytest.mark.parametrize("name", sorted(MANIFEST))
    def test_reader_rejects(self, name):
        with pytest.raises(AnnotationFormatError) as info:
            read_annotations(INVALID_DIR / f"{name}.json")
        assert MANIFEST[name]["error"] in str(info.value)

    @pytest.mark.parametrize("name", sorted(n for n in MANIFEST if MANIFEST[n]["schema_rejects"]))
    def test_schema_also_rejects(self, name, schema_validator):
        doc = json.loads((INVALID_DIR / f"{name}.json").read_text())
        assert not schema_validator.is_valid(doc)

    @pytest.mark.parametrize(
        "name",
        sorted(n for n in MANIFEST if not MANIFEST[n]["schema_rejects"] and n not in TEXT_CASES),
    )
    def test_semantic_cases_are_validator_only(self, name, schema_validator):
        """Cases flagged as beyond the schema really are beyond it."""
        doc = json.loads((INVALID_DIR / f"{name}.json").read_text())
        assert schema_validator.is_valid(doc)


class TestVersionPolicy:
    def test_constants(self):
        assert FORMAT_NAME == "sarcostat-annotations"
        assert FORMAT_VERSION == "1.0"

    def test_major_2_is_rejected(self, study_doc):
        study_doc["format_version"] = "2.1"
        _rejects(study_doc, "unsupported major version 2")

    def test_later_minor_with_unknown_fields_is_read_and_kept(self, study_doc, tmp_path):
        study_doc["format_version"] = "1.7"
        study_doc["future_top"] = {"anything": [1, 2]}
        study_doc["images"][0]["future_image"] = "x"
        study_doc["images"][0]["scale"]["future_scale"] = None
        study_doc["items"][0]["future_item"] = 3
        study_doc["items"][0]["points"]["future_points"] = []
        study_doc["items"][0]["raw_clicks"][0]["future_click"] = True
        path = tmp_path / "future.json"
        path.write_text(json.dumps(study_doc))
        doc = read_annotations(path)
        assert doc == study_doc

    def test_gui_block_is_optional_and_keeps_unknown_keys(self, tmp_path):
        doc = json.loads((DATA / "golden_v1_gui.json").read_text())
        doc["gui"]["future_setting"] = [1]
        path = tmp_path / "future_gui.json"
        path.write_text(json.dumps(doc))
        assert read_annotations(path) == doc
        del doc["gui"]
        validate_annotations(doc)

    def test_header_checked_before_body(self, study_doc):
        study_doc["format_version"] = "2.0"
        del study_doc["images"]
        _rejects(study_doc, "unsupported major version")


class TestValidator:
    def test_not_a_dict(self):
        _rejects([], "must be a JSON object")

    def test_nan_and_inf_in_memory(self, study_doc):
        for bad in (float("nan"), float("inf"), -float("inf")):
            doc = copy.deepcopy(study_doc)
            doc["items"][0]["points"]["z1"][0][0] = bad
            _rejects(doc, r"items\[0\]\.points\.z1\[0\]\[0\]")
            with pytest.raises(AnnotationFormatError):
                dumps_annotations(doc)

    def test_integers_reject_bools_and_floats(self, study_doc):
        study_doc["items"][0]["kit_crop_offset"] = [1000.0, 600]
        _rejects(study_doc, r"items\[0\]\.kit_crop_offset\[0\]")

    def test_full_skip_with_empty_points(self, study_doc):
        study_doc["items"][2]["points"] = {}
        validate_annotations(study_doc)

    def test_full_skip_with_null_points_is_rejected(self, study_doc):
        study_doc["items"][2]["points"] = None
        _rejects(study_doc, r"items\[2\]\.points")

    def test_pair_with_unequal_z_line_counts(self, study_doc):
        fibrils = study_doc["items"][4]["points"]["myofibrils"]
        assert len(fibrils[0]) != len(fibrils[1])
        validate_annotations(study_doc)

    def test_empty_items_is_valid(self, study_doc):
        study_doc["items"] = []
        validate_annotations(study_doc)

    def test_skipped_item_clicks_are_not_counted(self, study_doc):
        assert study_doc["items"][2]["skipped"] and study_doc["items"][2]["raw_clicks"]
        study_doc["items"][2]["raw_clicks"] = []
        validate_annotations(study_doc)

    def test_points_on_crop_edge_are_inside(self, study_doc):
        item = study_doc["items"][0]
        x0, y0 = item["kit_crop_offset"]
        w, h = item["kit_crop_size"]
        item["raw_clicks"] = []
        item["points"]["z1"] = [[x0 - 0.5, y0 - 0.5], [x0 + w - 0.5, y0 + h - 0.5]]
        validate_annotations(study_doc)
        item["points"]["z1"][1][1] += 2**-10
        _rejects(study_doc, "outside the crop")

    def test_repeated_skipped_part(self, study_doc):
        study_doc["items"][1]["skipped_parts"] = ["m_line", "m_line"]
        _rejects(study_doc, r"items\[1\]\.skipped_parts: must not repeat")

    def test_invalid_utf8(self, tmp_path):
        path = tmp_path / "latin1.json"
        path.write_bytes((DATA / "golden_v1_gui.json").read_bytes().replace(b'"user"', b'"\xe9"'))
        with pytest.raises(AnnotationFormatError, match="not valid UTF-8"):
            read_annotations(path)

    def test_does_not_mutate(self, study_doc):
        before = copy.deepcopy(study_doc)
        validate_annotations(study_doc)
        dumps_annotations(study_doc)
        assert study_doc == before

    def test_error_is_value_error(self):
        assert issubclass(AnnotationFormatError, ValueError)
        assert issubclass(ImageMismatchError, AnnotationFormatError)
        assert issubclass(MissingImageWarning, UserWarning)


class TestCanonicalJson:
    def test_key_order_does_not_matter(self, study_doc):
        reordered = json.loads(json.dumps(study_doc), object_pairs_hook=lambda p: dict(p[::-1]))
        assert list(reordered) != list(study_doc)
        assert dumps_annotations(reordered) == dumps_annotations(study_doc)

    def test_layout(self, study_doc):
        study_doc["items"][0]["skip_reason"] = None
        study_doc["images"][0]["study"]["group"] = "gruppo è"
        text = dumps_annotations(study_doc)
        assert text.endswith("}\n") and not text.endswith("\n\n")
        assert "\r" not in text
        assert text.isascii() and "\\u00e8" in text
        assert '"format": "sarcostat-annotations",\n' in text

    def test_write_uses_lf_bytes(self, study_doc, tmp_path):
        out = tmp_path / "a.json"
        write_annotations(study_doc, out)
        assert b"\r\n" not in out.read_bytes()
        assert out.read_bytes() == dumps_annotations(study_doc).encode("ascii")


class TestAtomicWrite:
    def test_invalid_doc_writes_nothing(self, study_doc, tmp_path):
        study_doc["format_version"] = "2.0"
        out = tmp_path / "a.json"
        with pytest.raises(AnnotationFormatError):
            write_annotations(study_doc, out)
        assert list(tmp_path.iterdir()) == []

    def test_replaces_existing_file(self, study_doc, tmp_path):
        out = tmp_path / "a.json"
        out.write_text("old")
        write_annotations(study_doc, out)
        assert read_annotations(out) == study_doc
        assert [p.name for p in tmp_path.iterdir()] == ["a.json"]

    def test_failure_keeps_old_file_and_cleans_up(self, study_doc, tmp_path, monkeypatch):
        out = tmp_path / "a.json"
        out.write_text("old")

        def boom(src, dst):
            raise OSError("disk full")

        monkeypatch.setattr(ann.os, "replace", boom)
        with pytest.raises(OSError, match="disk full"):
            write_annotations(study_doc, out)
        assert out.read_text() == "old"
        assert [p.name for p in tmp_path.iterdir()] == ["a.json"]

    def test_temp_file_is_in_the_target_folder(self, study_doc, tmp_path, monkeypatch):
        seen = []
        real = os.replace

        def spy(src, dst):
            seen.append(Path(src).parent)
            real(src, dst)

        monkeypatch.setattr(ann.os, "replace", spy)
        write_annotations(study_doc, tmp_path / "a.json")
        assert seen == [tmp_path]

    def test_accepts_str_path(self, study_doc, tmp_path):
        write_annotations(study_doc, str(tmp_path / "a.json"))
        assert read_annotations(str(tmp_path / "a.json")) == study_doc


class TestImageCheck:
    def test_repository_images_match(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            for path in GOLDEN:
                read_annotations(path, image_dir=EXAMPLES)

    def test_mismatch_is_an_error(self, tmp_path):
        (tmp_path / "human_skeletal_muscle_tem_3.jpg").write_bytes(b"not the image")
        with pytest.raises(ImageMismatchError, match="tem3"):
            read_annotations(DATA / "golden_v1_gui.json", image_dir=tmp_path)

    def test_absent_image_warns(self, tmp_path):
        (tmp_path / "human_skeletal_muscle_tem_1.jpg").write_bytes(
            (EXAMPLES / "human_skeletal_muscle_tem_1.jpg").read_bytes()
        )
        with pytest.warns(MissingImageWarning, match="human_skeletal_muscle_tem_3.jpg") as rec:
            doc = read_annotations(DATA / "golden_v1_study.json", image_dir=tmp_path)
        assert len(rec) == 1
        assert doc["images"][1]["image_id"] == "tem3"

    def test_sha256_file_reference_value(self, tmp_path):
        (tmp_path / "abc").write_bytes(b"abc")
        assert (
            sha256_file(tmp_path / "abc")
            == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        )


def _schema_nodes(node, path=""):
    if isinstance(node, dict):
        yield path, node
        for key, value in node.items():
            yield from _schema_nodes(value, f"{path}/{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _schema_nodes(value, f"{path}/{i}")


class TestSchemaCrossCheck:
    def test_schema_is_packaged(self):
        schema = load_schema()
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["properties"]["format"]["const"] == FORMAT_NAME

    def test_enums_match_the_validator(self):
        schema = load_schema()
        defs = schema["$defs"]
        assert tuple(defs["item"]["properties"]["type"]["enum"]) == ITEM_TYPES
        assert tuple(defs["item"]["properties"]["display_transform"]["enum"]) == DISPLAY_TRANSFORMS
        assert (
            tuple(defs["item"]["properties"]["skipped_parts"]["items"]["enum"]) == SKIPPABLE_PARTS
        )
        assert tuple(schema["properties"]["gui"]["properties"]["mode"]["enum"]) == GUI_MODES

    @needs_format_doc
    def test_every_property_is_documented(self):
        doc = FORMAT_DOC.read_text()
        names = set()
        for _path, node in _schema_nodes(load_schema()):
            names.update(node.get("properties", {}))
        missing = sorted(n for n in names if f"`{n}`" not in doc)
        assert names and not missing

    @needs_format_doc
    def test_documented_example_is_valid(self, schema_validator):
        text = FORMAT_DOC.read_text()
        example = json.loads(re.search(r"```json\n(.*?)\n```", text, re.S).group(1))
        validate_annotations(example)
        schema_validator.validate(example)

    def test_every_property_has_a_description(self):
        for path, node in _schema_nodes(load_schema()):
            if "/allOf/" in path:
                continue
            for name, sub in node.get("properties", {}).items():
                assert "description" in sub or "$ref" in sub, f"{path}/{name}"

    def test_validator_is_never_laxer_than_the_schema(self, schema_validator):
        """Every single-node mutation of the golden files that the validator
        accepts is also accepted by the schema (the validator is authoritative)."""
        replacements = [None, True, False, -1, 0, 1, 0.5, 2200.0, "", "x", "2.0", [], {}, [0, 0]]
        n_checked = n_schema_rejects = 0
        for path in GOLDEN:
            doc = json.loads(path.read_text())
            for parent, key in _slots(doc):
                original = parent[key]
                trials = [_DELETE, *replacements] if isinstance(parent, dict) else replacements
                for value in trials:
                    if value is _DELETE:
                        del parent[key]
                    else:
                        parent[key] = value
                    try:
                        validate_annotations(doc)
                        accepted = True
                    except AnnotationFormatError:
                        accepted = False
                    schema_ok = schema_validator.is_valid(doc)
                    n_checked += 1
                    n_schema_rejects += not schema_ok
                    if accepted:
                        assert schema_ok, f"{path.name}: {key!r} -> {value!r}"
                    parent[key] = original
        assert n_checked > 1000 and n_schema_rejects > n_checked // 2


_DELETE = object()


def _slots(node):
    """Every (container, key) slot, depth first; arrays beyond two entries are sampled."""
    if isinstance(node, dict):
        for key in list(node):
            yield node, key
            yield from _slots(node[key])
    elif isinstance(node, list):
        for i in range(min(len(node), 2)):
            yield node, i
            yield from _slots(node[i])


class TestIsolation:
    @pytest.mark.needs_cpython(reason="runs a subprocess; Emscripten has no processes")
    def test_runtime_does_not_import_jsonschema(self):
        code = (
            "import sys, sarcostat.io.annotations as a; "
            "a.load_schema(); "
            "assert 'jsonschema' not in sys.modules; "
            "assert 'numpy' not in sys.modules"
        )
        subprocess.run([sys.executable, "-c", code], check=True)

    @pytest.mark.needs_cpython(reason="runs a subprocess; Emscripten has no processes")
    def test_package_import_is_light(self):
        code = (
            "import sys, sarcostat.io; "
            "assert 'sarcostat.io.annotations' not in sys.modules; "
            "assert 'sarcostat.io.study' not in sys.modules"
        )
        subprocess.run([sys.executable, "-c", code], check=True)

    def test_messages_have_no_absolute_paths(self, tmp_path, study_doc):
        bad = copy.deepcopy(study_doc)
        bad["items"][0]["type"] = "nope"
        path = tmp_path / "bad.json"
        path.write_text(json.dumps(bad))
        with pytest.raises(AnnotationFormatError) as info:
            read_annotations(path)
        assert str(tmp_path) not in str(info.value)
        assert re.search(r"bad\.json", str(info.value))
