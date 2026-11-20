"""Annotation format v1 to and from the annotation session (engine/document.py)."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from sarcostat.analysis import analyze_document
from sarcostat.engine.annotate import AnnotationSession, code, describe
from sarcostat.engine.bridge import NeedsConfirmation, Refused
from sarcostat.engine.document import (
    LINEARITY_GUI_MODE,
    SHAPE_GUI_MODE,
    ImageInfo,
    SessionSettings,
    build_document,
    export_file_name,
    gui_mode,
    image_id_for,
    nm_per_px,
    parse_document,
    read_document,
    shape_item,
)
from sarcostat.io.annotations import (
    GUI_MODES,
    AnnotationFormatError,
    dumps_annotations,
    read_annotations,
)

from .test_annotate import (
    FIXED_UTC,
    IMAGE,
    SHA_A,
    SHA_B,
    SQUARE_POINTS,
    assert_identical,
    checked,
    confirm_shape,
    new_session,
    shifted,
)

DATA = Path(__file__).resolve().parents[1] / "io" / "data"
EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
DIGEST = "0" * 64


def _base_item(item_id: str, kind: str, points: dict[str, Any], **extra: Any) -> dict[str, Any]:
    item = {
        "item_id": item_id,
        "target_id": None,
        "image_id": "mixed",
        "type": kind,
        "display_transform": "identity",
        "kit_crop_offset": [0, 0],
        "kit_crop_size": [300, 200],
        "raw_clicks": [],
        "points": points,
        "started_utc": None,
        "finished_utc": None,
        "active_seconds": None,
        "skipped": False,
        "skip_reason": None,
        "skipped_parts": [],
    }
    item.update(extra)
    return item


def mixed_document(gui: bool = True) -> dict[str, Any]:
    """A canonical-ready v1 document of one image with every item type and unknown fields."""
    z = [
        [[40.5, 60.0], [41.0, 90.0]],
        [[70.25, 61.0], [71.0, 91.5]],
        [[100.0, 60.0], [101.0, 92.0]],
    ]
    pair_a = [[[40.0, 120.0], [40.5, 140.0]], [[80.0, 120.0], [80.0, 141.0]]]
    pair_b = [[[50.0, 150.0], [50.5, 170.0]], [[90.0, 150.0], [90.0, 171.0]]]
    doc: dict[str, Any] = {
        "format": "sarcostat-annotations",
        "format_version": "1.0",
        "sarcostat_version": "0.1.0",
        "source_digest": DIGEST,
        "created_utc": "2026-05-18T10:00:00Z",
        "annotator_id": None,
        "session_id": None,
        "study_id": None,
        "x_tool_note": {"kept": True},
        "images": [
            {
                "image_id": "mixed",
                "file_name": "micrograph.png",
                "sha256": SHA_A,
                "width_px": 300,
                "height_px": 200,
                "scale": {"nm_per_px": 2.5, "source": "metadata"},
                "study": {"animal": "m1", "group": "control"},
                "x_image_note": "kept",
            }
        ],
        "items": [
            _base_item(
                "s-1",
                "shape",
                {
                    "z1": [[10.0, 10.0], [10.0, 30.0]],
                    "m": [[20.0, 10.0], [20.5, 30.0]],
                    "z2": [[30.0, 10.0], [30.0, 30.0]],
                },
                x_item_note=[1, 2],
            ),
            _base_item(
                "s-2",
                "shape",
                {"z1": [[50.0, 10.0], [50.0, 30.0]], "z2": [[70.0, 10.0], [70.0, 30.0]]},
                skip_reason="M-line not identifiable",
                skipped_parts=["m_line"],
            ),
            _base_item("s-3", "shape", {}, skipped=True, skip_reason="out of focus"),
            _base_item("c-1", "chain", {"z_lines": z}, branch_end=True),
            _base_item("p-1", "pair", {"myofibrils": [pair_a, pair_b]}),
            _base_item("c-2", "chain", {}, skipped=True, skip_reason="unclear", branch_end=False),
        ],
    }
    if gui:
        doc["gui"] = {
            "pixel_size": 2.5,
            "unit": "nm",
            "expected_direction_deg": 15.0,
            "click_sigma_z": 1.5,
            "click_sigma_m": None,
            "click_perp_ratio": 1.0,
            "mode": SHAPE_GUI_MODE,
            "open_myofibril": False,
        }
    return doc


def mixed_text(gui: bool = True) -> str:
    return dumps_annotations(mixed_document(gui))


def _session() -> AnnotationSession:
    return AnnotationSession(utc_now=lambda: FIXED_UTC)


def _write(tmp_path: Path, text: str, name: str = "a.json") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


class TestByteIdenticalRoundTrip:
    """Acceptance M2-2 #4: shape, chain and pair items through the engine unchanged."""

    @pytest.mark.parametrize("gui", [True, False])
    def test_mixed_file(self, gui: bool) -> None:
        text = mixed_text(gui)
        session = _session()
        checked(session.doc_load_v1(text, IMAGE, False))
        assert session.doc_export("v1")["text"] == text
        assert session.doc_export("v1")["text"] == text

    def test_golden_gui_file(self) -> None:
        golden = DATA / "golden_v1_gui.json"
        if not golden.is_file():
            pytest.skip("the format corpus tests/io/data is not in this run")
        text = golden.read_text(encoding="utf-8")
        session = _session()
        checked(session.doc_load_v1(text, None, False))
        assert session.doc_export("v1")["text"] == text

    def test_edit_and_undo_gives_the_same_bytes(self) -> None:
        text = mixed_text()
        session = _session()
        session.doc_load_v1(text, IMAGE, False)
        confirm_shape(session, shifted(SQUARE_POINTS, 150))
        session.shape_undo()
        session.doc_set_scale(5.0, "nm")
        session.doc_set_scale(2.5, "nm")
        assert session.doc_export("v1")["text"] == text

    def test_edited_file_keeps_chain_and_pair_items_and_unknown_fields(self) -> None:
        original = mixed_document()
        session = _session()
        session.doc_load_v1(dumps_annotations(original), IMAGE, False)
        confirm_shape(session, shifted(SQUARE_POINTS, 150))
        doc = json.loads(session.doc_export("v1")["text"])
        assert doc["items"][:-1] == original["items"]
        assert doc["items"][-1]["item_id"] == "sarcomere-1"
        assert doc["x_tool_note"] == {"kept": True}
        assert doc["images"][0]["x_image_note"] == "kept"
        assert doc["images"][0]["study"] == {"animal": "m1", "group": "control"}
        assert doc["images"][0]["scale"] == {"nm_per_px": 2.5, "source": "metadata"}
        assert doc["created_utc"] == "2026-11-20T12:00:00Z"
        assert doc["source_digest"] != DIGEST
        assert doc["gui"]["mode"] == SHAPE_GUI_MODE

    def test_a_new_scale_rewrites_the_image_scale(self) -> None:
        session = _session()
        session.doc_load_v1(mixed_text(), IMAGE, False)
        session.doc_set_scale(0.004, "um")
        doc = session.document()
        assert doc["images"][0]["scale"] == {"nm_per_px": 4.0, "source": "user"}
        assert doc["gui"]["pixel_size"] == 0.004 and doc["gui"]["unit"] == "um"


class TestSaveAndLoadBitForBit:
    """Acceptance M2-2 #2: export then open reproduces every annotation and metric."""

    def _annotated(self) -> AnnotationSession:
        session = _session()
        session.doc_load_v1(mixed_text(), IMAGE, False)
        session.doc_set_scale(0.5, "um")
        session.doc_set_reference_direction(10.0)
        for dx in (150.0, 190.0):
            confirm_shape(
                session,
                shifted(
                    [(10.25, 10.0), (10.5, 30.75), (20, 10), (20, 30), (30, 10), (30.5, 30)], dx
                ),
            )
        confirm_shape(session, [(250, 10), (250, 30), (270, 10), (270, 30)], m_skip=True)
        session.doc_set_click_error(0.25)
        return session

    def test_round_trip(self, tmp_path: Path) -> None:
        first = self._annotated()
        state_before = first.state()
        results_before = first.analysis()
        text = first.doc_export("v1")["text"]
        second = _session()
        state_after = checked(second.doc_load_v1(text, IMAGE, False))
        assert second.items == first.items
        assert second.settings == first.settings
        assert state_after["items"] == state_before["items"]
        assert_identical(second.analysis(), results_before)
        assert second.doc_export("v1")["text"] == text
        path = _write(tmp_path, text)
        assert dumps_annotations(read_annotations(path)) == text
        assert_identical(analyze_document(read_annotations(path)).images[0], results_before)

    def test_fresh_image_round_trip(self, tmp_path: Path) -> None:
        first = new_session()
        first.doc_set_scale(0.065, "um")
        for dx in (0.0, 40.0, 80.0):
            confirm_shape(first, shifted(SQUARE_POINTS, dx, dx / 4))
        first.doc_set_click_error(0.01)
        text = first.doc_export("v1")["text"]
        second = _session()
        second.doc_load_v1(text, IMAGE, False)
        assert_identical(second.analysis(), first.analysis())
        assert dumps_annotations(read_annotations(_write(tmp_path, text))) == text


class TestResultsExports:
    def test_session_results_equal_the_exported_file(self, tmp_path: Path) -> None:
        from sarcostat.analysis import analyze_file
        from sarcostat.export import results_csv, results_json

        session = _session()
        session.doc_load_v1(mixed_text(), IMAGE, False)
        confirm_shape(session, shifted(SQUARE_POINTS, 150))
        session.doc_set_click_error(0.75)
        path = _write(tmp_path, session.doc_export("v1")["text"])
        from_file = analyze_file(path)
        results = session.results()
        assert results is not None
        assert_identical(results, from_file)
        assert results_json(results) == results_json(from_file)
        assert results_csv(results) == results_csv(from_file)

    def test_no_results_without_an_image(self) -> None:
        assert _session().results() is None and _session().analysis() is None


class TestImageChecks:
    """Acceptance M2-2 #3: another image needs a confirmation; no image gives a warning."""

    def test_sha256_mismatch_needs_confirmation(self) -> None:
        text = mixed_text()
        session = new_session(dict(IMAGE, file_name="other.png", sha256=SHA_B))
        before = session.state()
        other = dict(IMAGE, sha256=SHA_B)
        with pytest.raises(NeedsConfirmation, match="SHA-256 differs") as caught:
            session.doc_load_v1(text, other, False)
        assert caught.value.code == "needs_confirmation"
        assert session.state() == before
        state = checked(session.doc_load_v1(text, other, True))
        assert state["image"] == other and len(state["items"]) == 6
        doc = json.loads(session.doc_export("v1")["text"])
        assert doc["images"][0]["sha256"] == SHA_B

    def test_image_absent_gives_results_and_a_warning(self) -> None:
        session = _session()
        state = checked(session.doc_load_v1(mixed_text(), None, False))
        assert state["image"] == IMAGE
        notice = state["notice"]
        assert notice is not None and notice["params"]["image_checked"] is False
        assert "not checked" in describe(notice)
        results = session.analysis()
        assert results is not None and len(results.geometries) == 1
        assert results.chains[0].metrics is not None and results.register[0].metrics is not None

    def test_matching_image_gives_no_warning(self) -> None:
        state = _session().doc_load_v1(mixed_text(), IMAGE, False)
        assert state["notice"] == code(
            "document_loaded",
            shape_items=3,
            chain_items=2,
            pair_items=1,
            incomplete_items=3,
            image_checked=True,
            missing_modes="straightness,register",
        )

    def test_size_mismatch_is_refused(self) -> None:
        session = _session()
        with pytest.raises(Refused, match="300 x 200 px"):
            session.doc_load_v1(mixed_text(), dict(IMAGE, width_px=301), True)
        assert session.image is None

    def test_golden_file_on_the_example_image(self) -> None:
        golden = DATA / "golden_v1_gui.json"
        if not golden.is_file():
            pytest.skip("the format corpus tests/io/data is not in this run")
        example = EXAMPLES / "human_skeletal_muscle_tem_3.jpg"
        image = {
            "file_name": example.name,
            "sha256": hashlib.sha256(example.read_bytes()).hexdigest(),
            "width_px": 2400,
            "height_px": 1745,
        }
        state = checked(_session().doc_load_v1(golden.read_text(encoding="utf-8"), image, False))
        assert state["notice"]["params"]["image_checked"] is True


class TestRefusedFiles:
    def test_several_images(self) -> None:
        doc = mixed_document()
        second = copy.deepcopy(doc["images"][0])
        second.update(image_id="second", file_name="second.png", sha256=SHA_B)
        doc["images"].append(second)
        session = _session()
        with pytest.raises(Refused, match="sarcostat analyze") as caught:
            session.doc_load_v1(dumps_annotations(doc), None, False)
        assert "2 images" in caught.value.message
        assert session.image is None

    def test_golden_study_file(self) -> None:
        study = DATA / "golden_v1_study.json"
        if not study.is_file():
            pytest.skip("the format corpus tests/io/data is not in this run")
        with pytest.raises(Refused, match="sarcostat analyze"):
            _session().doc_load_v1(study.read_text(encoding="utf-8"), None, False)

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "{}",
            "\ufeff" + mixed_text(),
            mixed_text().replace('"pixel_size": 2.5', '"pixel_size": NaN'),
            mixed_text().replace('"pixel_size": 2.5', '"pixel_size": 1e999'),
            mixed_text().replace(
                '"format": "sarcostat-annotations",',
                '"format": "x", "format": "sarcostat-annotations",',
            ),
            mixed_text().replace('"format_version": "1.0"', '"format_version": "2.0"'),
            "[" * 50000,
            '{"a": ' * 65 + "1" + "}" * 65,
        ],
    )
    def test_invalid_text(self, text: str) -> None:
        session = new_session()
        before = session.state()
        with pytest.raises(Refused, match="not an annotation file of format v1"):
            session.doc_load_v1(text, None, False)
        assert session.state() == before


class TestGuiBlock:
    def test_new_image_writes_the_shape_mode(self) -> None:
        session = new_session()
        session.doc_set_scale(0.5, "um")
        confirm_shape(session, SQUARE_POINTS)
        doc = json.loads(session.doc_export("v1")["text"])
        assert doc["gui"] == {
            "pixel_size": 0.5,
            "unit": "um",
            "expected_direction_deg": None,
            "click_sigma_z": None,
            "click_sigma_m": None,
            "click_perp_ratio": 1.0,
            "mode": SHAPE_GUI_MODE,
            "open_myofibril": False,
        }

    def test_chain_only_file_opens_home_and_keeps_the_linearity_mode(self) -> None:
        doc = mixed_document(gui=False)
        doc["items"] = [i for i in doc["items"] if i["type"] != "shape"]
        session = _session()
        state = checked(session.doc_load_v1(dumps_annotations(doc), IMAGE, False))
        assert state["mode"] == "home" and state["step"] is None
        assert not any(state["controls"].values())
        assert state["notice"]["params"]["missing_modes"] == "straightness,register"
        session.doc_set_reference_direction(30.0)
        written = session.document()
        assert written["gui"]["mode"] == LINEARITY_GUI_MODE
        assert written["gui"]["expected_direction_deg"] == 30.0
        assert written["items"] == doc["items"]

    def test_shape_items_open_the_shape_mode(self) -> None:
        doc = mixed_document()
        doc["gui"]["mode"] = LINEARITY_GUI_MODE
        session = _session()
        assert session.doc_load_v1(dumps_annotations(doc), IMAGE, False)["mode"] == "shape"
        session.doc_set_click_error(0.5)
        assert session.document()["gui"]["mode"] == SHAPE_GUI_MODE

    def test_settings_follow_the_analysis_rules(self) -> None:
        session = _session()
        session.doc_load_v1(mixed_text(gui=False), IMAGE, False)
        assert session.settings == SessionSettings(pixel_size=2.5, unit="nm")
        session.doc_load_v1(mixed_text(gui=True), IMAGE, False)
        assert session.settings == SessionSettings(
            pixel_size=2.5,
            unit="nm",
            reference_direction_deg=15.0,
            click_sigma_z=1.5,
            click_sigma_m=None,
        )

    def test_gui_modes(self) -> None:
        assert set(GUI_MODES) == {SHAPE_GUI_MODE, LINEARITY_GUI_MODE}
        assert gui_mode(True) == SHAPE_GUI_MODE and gui_mode(False) == LINEARITY_GUI_MODE


class TestHelpers:
    def test_image_id_for(self) -> None:
        assert image_id_for("micrograph.png") == "micrograph"
        assert image_id_for("my image (1).tif") == "my-image--1-"
        assert image_id_for("..png") == "image"

    def test_export_file_name(self) -> None:
        assert export_file_name("micrograph.png") == "micrograph_annotations.json"
        assert export_file_name("a.b.tif") == "a.b_annotations.json"

    def test_nm_per_px(self) -> None:
        assert nm_per_px(2.0, "nm") == 2.0
        assert nm_per_px(0.5, "um") == 500.0
        assert nm_per_px(0.5, "\u00b5m") == 500.0
        assert nm_per_px(1.0, "px") is None

    def test_shape_item_is_valid(self) -> None:
        image = ImageInfo.from_json(IMAGE)
        item = shape_item(
            "sarcomere-1",
            "micrograph",
            image,
            {"z1": [[1, 2], [1, 20]], "z2": [[9, 2], [9, 20]]},
        )
        assert item["points"] == {"z1": [[1.0, 2.0], [1.0, 20.0]], "z2": [[9.0, 2.0], [9.0, 20.0]]}
        assert item["skipped_parts"] == ["m_line"]
        doc = build_document(
            image=image,
            image_id="micrograph",
            settings=SessionSettings(),
            items=[item],
            last_mode_shape=True,
            open_myofibril=False,
            created_utc="2026-01-01T00:00:00Z",
        )
        assert parse_document(dumps_annotations(doc)) == doc

    def test_parse_document_is_strict(self) -> None:
        with pytest.raises(AnnotationFormatError, match="duplicate key"):
            parse_document('{"a": 1, "a": 2}')
        loaded = read_document(mixed_text())
        assert loaded.image == ImageInfo.from_json(IMAGE) and loaded.image_id == "mixed"
        assert [i["item_id"] for i in loaded.items] == ["s-1", "s-2", "s-3", "c-1", "p-1", "c-2"]
        assert loaded.last_mode_shape is True and loaded.open_myofibril is False
