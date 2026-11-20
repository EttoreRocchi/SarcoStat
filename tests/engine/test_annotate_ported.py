"""Shape and session tests of the desktop window, ported to the engine.

Each test names the desktop test it comes from (``tests/gui/...``); the
desktop originals stay until the Tk code is removed. The chain and
register tests are ported with the chain and register flows (M5-12,
M5-13).
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from sarcostat.engine.annotate import AnnotationSession, code, describe
from sarcostat.engine.bridge import NeedsConfirmation, Refused, SchemaError, catalogue, validate
from sarcostat.engine.document import LINEARITY_GUI_MODE, SHAPE_GUI_MODE
from sarcostat.io.annotations import GUI_MODES, dumps_annotations, read_annotations

from .test_annotate import (
    FIXED_UTC,
    IMAGE,
    SHA_B,
    SQUARE_POINTS,
    assert_identical,
    checked,
    confirm_shape,
    new_session,
    shifted,
)
from .test_document import DATA, EXAMPLES, mixed_document

SINGLE_IMAGE = {
    "file_name": "micrograph.png",
    "sha256": IMAGE["sha256"],
    "width_px": 300,
    "height_px": 200,
}
CHAIN_1 = [[[x, 40.0], [x + 1.5, 70.0]] for x in (20.25, 50.5, 80.0, 110.75)]
CHAIN_2 = [[[x, 80.0], [x + 0.5, 110.0]] for x in (35.0, 66.5, 95.25)]


def _session() -> AnnotationSession:
    return AnnotationSession(utc_now=lambda: FIXED_UTC)


def _chain_file() -> str:
    """Two chain items and a pair item on the test image (the desktop's two myofibrils)."""
    doc = mixed_document(gui=False)
    template = doc["items"][3]
    chains = []
    for k, z_lines in enumerate((CHAIN_1, CHAIN_2), start=1):
        item = copy.deepcopy(template)
        item.update(item_id=f"myofibril-{k}", points={"z_lines": z_lines}, branch_end=False)
        item.pop("x_item_note", None)
        chains.append(item)
    pair = copy.deepcopy(doc["items"][4])
    pair["points"] = {"myofibrils": [CHAIN_1, CHAIN_2]}
    doc["items"] = [*chains, pair]
    doc["images"][0]["scale"] = {"nm_per_px": None, "source": None}
    return dumps_annotations(doc)


def _annotate_everything(session: AnnotationSession) -> None:
    """The desktop's ``_annotate_everything``: myofibrils (here opened from a file), two sarcomeres."""
    checked(session.doc_load_v1(_chain_file(), SINGLE_IMAGE, False))
    assert checked(session.doc_set_scale(0.5, "um"))["notice"] is None
    assert checked(session.doc_set_reference_direction(10.0))["notice"] is None
    for dx in (0.0, 60.0):
        confirm_shape(session, shifted(SQUARE_POINTS, dx + 100.0, 120.0))
    assert checked(session.doc_set_click_error(0.25))["notice"] is None


def _two_sarcomeres(session: AnnotationSession) -> None:
    for dx in (0.0, 60.0):
        confirm_shape(session, shifted(SQUARE_POINTS, dx + 100.0, 120.0))


class TestPresenterLinearityWorkflow:
    def test_out_of_bounds_click_is_rejected(self) -> None:
        """From test_presenter.py::TestLinearityWorkflow::test_out_of_bounds_click_is_rejected."""
        session = new_session()
        state = checked(session.shape_click(500, 10))
        assert state["pending"]["marks"] == []
        assert state["notice"] == code("click_outside_image")
        assert "inside the image" in describe(state["notice"])


class TestPresenterSecondImage:
    def test_loading_a_second_image_after_annotating_works(self) -> None:
        """From test_presenter.py::TestSecondImage (the desktop artist check is not portable)."""
        session = new_session()
        confirm_shape(session, SQUARE_POINTS)
        session.shape_click(150, 150)
        state = checked(session.doc_new(dict(IMAGE, file_name="second.png", sha256=SHA_B)))
        assert state["items"] == [] and state["pending"]["marks"] == []
        session.shape_clear()
        state = confirm_shape(session, shifted(SQUARE_POINTS, 5, 5))
        assert len(state["items"]) == 1


class TestPresenterSarcomereWorkflow:
    def test_confirm_and_chain_summary(self) -> None:
        """From test_presenter.py::TestSarcomereWorkflow::test_confirm_and_chain_summary."""
        session = new_session()
        confirm_shape(session, SQUARE_POINTS)
        results = session.analysis()
        assert results is not None and len(results.geometries) == 1
        assert results.shape_summary is None
        confirm_shape(session, shifted(SQUARE_POINTS, 0, 30))
        results = session.analysis()
        assert results is not None and len(results.geometries) == 2
        summary = results.shape_summary
        assert summary is not None
        assert summary.chain.alpha_mean == pytest.approx(0.0)
        assert summary.alignment.h_mean == pytest.approx(0.0)
        confirm_shape(session, shifted(SQUARE_POINTS, 20, 60))
        results = session.analysis()
        assert results is not None and results.shape_summary is not None
        assert results.shape_summary.alignment.h_values == [
            pytest.approx(0.0),
            pytest.approx(20.0),
        ]

    def test_confirm_enabled_only_with_six_points(self) -> None:
        """From test_presenter.py::TestSarcomereWorkflow::test_confirm_enabled_only_with_six_points."""
        session = new_session()
        for x, y in SQUARE_POINTS[:5]:
            state = checked(session.shape_click(x, y))
        assert state["controls"]["can_confirm"] is False
        state = checked(session.shape_click(*SQUARE_POINTS[5]))
        assert state["controls"]["can_confirm"] is True

    def test_scale_change_recomputes_sarcomeres(self) -> None:
        """From test_presenter.py::TestSarcomereWorkflow::test_scale_change_recomputes_sarcomeres."""
        session = new_session()
        confirm_shape(session, SQUARE_POINTS)
        session.doc_set_scale(0.1, "um")
        results = session.analysis()
        assert results is not None
        (geom,) = results.geometries
        assert geom.unit == "um"
        assert geom.z_line_1_length == pytest.approx(2.0)

    def test_undo_removes_last_sarcomere_overlay(self) -> None:
        """From test_presenter.py::TestSarcomereWorkflow (the desktop artist count is not portable)."""
        session = new_session()
        confirm_shape(session, SQUARE_POINTS)
        confirm_shape(session, shifted(SQUARE_POINTS, 20))
        state = checked(session.shape_undo())
        assert [i["item_id"] for i in state["items"]] == ["sarcomere-1"]
        results = session.analysis()
        assert results is not None and len(results.geometries) == 1


class TestPresenterSettings:
    def test_default_unit_is_pixels(self) -> None:
        """From test_presenter.py::TestSettings::test_default_unit_is_pixels."""
        assert new_session().settings.unit == "px"

    @pytest.mark.parametrize("px", [0.0, -1.0, float("nan")])
    def test_invalid_pixel_size(self, px: float) -> None:
        """From test_presenter.py::TestSettings::test_invalid_pixel_size."""
        session = new_session()
        assert checked(session.doc_set_scale(px, "um"))["notice"] == code("pixel_size_invalid")

    def test_start_without_image(self) -> None:
        """From test_presenter.py::TestSettings::test_start_without_image."""
        with pytest.raises(Refused, match="Open an image first."):
            AnnotationSession().shape_click(10, 10)


def _snapshot(session: AnnotationSession) -> dict[str, Any]:
    results = session.analysis()
    assert results is not None
    return {
        "items": session.items,
        "state_items": session.state()["items"],
        "settings": session.settings,
        "results": results,
    }


class TestPresenterSaveAndLoad:
    def test_round_trip_reproduces_annotations_and_metrics(self) -> None:
        """From test_presenter.py::TestSaveAndLoad::test_round_trip_reproduces_annotations_and_metrics."""
        first = _session()
        _annotate_everything(first)
        before = _snapshot(first)
        assert before["results"].register[0].metrics is not None
        assert before["results"].chains[1].metrics is not None
        text = first.doc_export("v1")["text"]
        second = _session()
        state = checked(second.doc_load_v1(text, SINGLE_IMAGE, False))
        assert state["image"] == SINGLE_IMAGE
        assert state["mode"] == "shape"
        assert_identical(before, _snapshot(second))
        assert state["not_exported"] is False

    def test_saved_file_is_format_v1_with_free_session_values(self, tmp_path: Path) -> None:
        """From test_presenter.py::TestSaveAndLoad::test_saved_file_is_format_v1_with_free_session_values."""
        session = new_session()
        session.doc_set_scale(0.5, "um")
        session.doc_set_reference_direction(10.0)
        _two_sarcomeres(session)
        session.doc_set_click_error(0.25)
        exported = session.doc_export("v1")
        assert exported["file_name"] == "micrograph_annotations.json"
        path = tmp_path / exported["file_name"]
        path.write_text(exported["text"], encoding="utf-8")
        doc = read_annotations(path)
        assert doc["annotator_id"] is None and doc["session_id"] is None and doc["study_id"] is None
        (img,) = doc["images"]
        assert img["file_name"] == "micrograph.png"
        assert img["sha256"] == IMAGE["sha256"]
        assert (img["width_px"], img["height_px"]) == (300, 200)
        assert img["scale"] == {"nm_per_px": 500.0, "source": "user"}
        assert [i["type"] for i in doc["items"]] == ["shape", "shape"]
        for item in doc["items"]:
            assert item["display_transform"] == "identity"
            assert item["kit_crop_offset"] == [0, 0] and item["kit_crop_size"] == [300, 200]
            assert item["target_id"] is None and item["raw_clicks"] == []
            assert item["started_utc"] is None and item["skipped"] is False
        assert doc["gui"] == {
            "pixel_size": 0.5,
            "unit": "um",
            "expected_direction_deg": 10.0,
            "click_sigma_z": 0.25,
            "click_sigma_m": 0.25,
            "click_perp_ratio": 1.0,
            "mode": SHAPE_GUI_MODE,
            "open_myofibril": False,
        }
        assert str(tmp_path) not in exported["text"] and "/" not in img["file_name"]

    def test_uncalibrated_scale_is_null(self) -> None:
        """From test_presenter.py::TestSaveAndLoad::test_uncalibrated_scale_is_null."""
        session = new_session()
        confirm_shape(session, SQUARE_POINTS)
        doc = json.loads(session.doc_export("v1")["text"])
        assert doc["images"][0]["scale"] == {"nm_per_px": None, "source": None}

    def test_refusals(self) -> None:
        """From test_presenter.py::TestSaveAndLoad::test_refusals (the single-Z-line part is chain work)."""
        with pytest.raises(Refused, match="image"):
            AnnotationSession().doc_export("v1")
        session = new_session()
        with pytest.raises(Refused, match="Nothing to export"):
            session.doc_export("v1")
        session.shape_click(10, 10)
        with pytest.raises(Refused, match="Nothing to export"):
            session.doc_export("v1")

    def test_load_onto_the_current_image(self) -> None:
        """From test_presenter.py::TestSaveAndLoad::test_load_onto_the_current_image."""
        first = _session()
        _annotate_everything(first)
        text = first.doc_export("v1")["text"]
        other = new_session(SINGLE_IMAGE)
        state = checked(other.doc_load_v1(text, SINGLE_IMAGE, False))
        assert [i["type"] for i in state["items"]].count("shape") == 2
        assert state["notice"]["params"]["image_checked"] is True

    def test_sha256_mismatch_asks_for_confirmation(self) -> None:
        """From test_presenter.py::TestSaveAndLoad::test_sha256_mismatch_asks_for_confirmation."""
        first = _session()
        _annotate_everything(first)
        text = first.doc_export("v1")["text"]
        changed = dict(SINGLE_IMAGE, sha256=SHA_B)

        refused = new_session(changed)
        with pytest.raises(NeedsConfirmation, match="SHA-256"):
            refused.doc_load_v1(text, changed, False)
        assert refused.items == [] and refused.image is not None
        assert refused.image.sha256 == SHA_B

        accepted = new_session(changed)
        state = checked(accepted.doc_load_v1(text, changed, True))
        assert [i["type"] for i in state["items"]].count("shape") == 2

    def test_unreadable_or_unmatched_files(self) -> None:
        """From test_presenter.py::TestSaveAndLoad (a file without its image opens with a warning)."""
        with pytest.raises(Refused, match="not an annotation file") as caught:
            _session().doc_load_v1("{}", None, False)
        assert "format" in caught.value.message
        first = _session()
        _annotate_everything(first)
        text = first.doc_export("v1")["text"]
        state = checked(_session().doc_load_v1(text, None, False))
        assert state["image"] == SINGLE_IMAGE
        assert "micrograph.png" in json.dumps(state["image"])
        assert state["notice"]["params"]["image_checked"] is False

    def test_items_without_complete_landmarks_are_reported(self) -> None:
        """From test_presenter.py::TestSaveAndLoad (incomplete items are kept, without indices)."""
        session = new_session()
        _two_sarcomeres(session)
        doc = json.loads(session.doc_export("v1")["text"])
        shape = doc["items"][0]
        del shape["points"]["m"]
        shape["skipped_parts"] = ["m_line"]
        shape["skip_reason"] = "M-line not identifiable"
        doc["items"][1].update(skipped=True, skip_reason="test", points={})
        other = _session()
        state = checked(other.doc_load_v1(dumps_annotations(doc), SINGLE_IMAGE, False))
        assert state["notice"]["params"]["incomplete_items"] == 2
        assert "2 item(s)" in describe(state["notice"])
        assert len(state["items"]) == 2
        results = other.analysis()
        assert results is not None and results.geometries == ()

    def test_malformed_gui_block_is_refused(self) -> None:
        """From test_presenter.py::TestSaveAndLoad::test_malformed_gui_block_is_refused."""
        session = new_session()
        _two_sarcomeres(session)
        doc = json.loads(session.doc_export("v1")["text"])
        doc["gui"]["pixel_size"] = -1.0
        with pytest.raises(Refused, match="gui"):
            _session().doc_load_v1(json.dumps(doc), None, False)


class TestPresenterUnsavedWork:
    def test_saving_and_loading_clear_the_unsaved_flag(self) -> None:
        """From test_presenter.py::TestUnsavedWorkAndConfirmation::test_saving_and_loading_clear_the_unsaved_flag."""
        session = new_session()
        assert not session.not_exported
        _two_sarcomeres(session)
        assert session.not_exported
        session.doc_export("v1")
        assert not session.not_exported
        assert checked(session.shape_undo())["not_exported"] is True


class TestPresenterFormatV1Agreement:
    def test_gui_modes_match_the_format(self) -> None:
        """From test_presenter.py::TestFormatV1Agreement::test_gui_modes_match_the_format."""
        assert set(GUI_MODES) == {LINEARITY_GUI_MODE, SHAPE_GUI_MODE}

    def test_golden_gui_file_loads_onto_the_example_image(self) -> None:
        """From test_presenter.py::TestFormatV1Agreement (all items kept; it opens in shape mode)."""
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
        session = _session()
        state = checked(session.doc_load_v1(golden.read_text(encoding="utf-8"), image, False))
        results = session.analysis()
        assert results is not None and len(results.geometries) == 1
        assert [i["type"] for i in state["items"]] == ["shape", "shape", "chain", "pair"]
        s = session.settings
        assert (s.pixel_size, s.unit, s.click_sigma_z) == (4.39, "nm", 2.5)
        assert state["mode"] == "shape"
        assert state["notice"]["params"]["incomplete_items"] == 1


def _confirm_repeats(session: AnnotationSession, repeats: list[list[tuple[float, float]]]) -> None:
    for points in repeats:
        confirm_shape(session, points)


class TestClickErrorUncertainty:
    def test_no_uncertainty_by_default(self) -> None:
        """From test_myofibrils_and_uncertainty.py::TestClickErrorUncertainty::test_no_uncertainty_by_default."""
        session = new_session()
        confirm_shape(session, SQUARE_POINTS)
        results = session.analysis()
        assert results is not None and results.uncertainties is None

    def test_set_click_error_reports_uncertainty(self) -> None:
        """From test_myofibrils_and_uncertainty.py (the report text comes with engine/reports.py)."""
        session = new_session()
        confirm_shape(session, SQUARE_POINTS)
        assert checked(session.doc_set_click_error(0.5))["notice"] is None
        results = session.analysis()
        assert results is not None and results.uncertainties is not None
        (unc,) = results.uncertainties
        assert unc.uncertainty.u_shape_index > 0
        assert 0.0 <= unc.stability.stability <= 1.0

    @pytest.mark.parametrize("bad", [-1.0, float("nan")])
    def test_invalid_click_error(self, bad: float) -> None:
        """From test_myofibrils_and_uncertainty.py::TestClickErrorUncertainty::test_invalid_click_error."""
        session = new_session()
        assert checked(session.doc_set_click_error(bad))["notice"] == code("click_error_invalid")

    def test_click_error_follows_the_calibration(self) -> None:
        """From test_myofibrils_and_uncertainty.py::TestClickErrorUncertainty::test_click_error_follows_the_calibration."""
        session = new_session()
        session.doc_set_click_error(2.0)
        session.doc_set_scale(5.0, "nm")
        assert session.settings.click_sigma_z == pytest.approx(10.0)
        assert session.settings.click_sigma_m == pytest.approx(10.0)

    def test_estimate_from_repeats(self) -> None:
        """From test_myofibrils_and_uncertainty.py::TestClickErrorUncertainty::test_estimate_from_repeats."""
        session = new_session()
        state = checked(session.doc_estimate_click_error())
        assert state["notice"] == code("click_error_too_few_repeats", n_repeats=0)
        rng = np.random.default_rng(0)
        _confirm_repeats(
            session,
            [
                [(x + rng.normal(0, 0.3), y + rng.normal(0, 0.3)) for x, y in SQUARE_POINTS]
                for _ in range(4)
            ],
        )
        state = checked(session.doc_estimate_click_error())
        assert state["notice"] == code("click_error_estimated", n_repeats=4)
        assert "from 4 repeats" in describe(state["notice"])
        sigma = session.settings.click_sigma_z
        assert sigma is not None and 0.0 < sigma < 2.0
        results = session.analysis()
        assert results is not None and results.uncertainties is not None

    def test_estimate_with_a_repeat_from_the_other_z_line(self) -> None:
        """From test_myofibrils_and_uncertainty.py (K-M3: a reversed repeat is matched)."""
        session = new_session()
        rng = np.random.default_rng(0)
        repeats = [
            [(x + rng.normal(0, 0.05), y + rng.normal(0, 0.05)) for x, y in SQUARE_POINTS]
            for _ in range(3)
        ]
        repeats[2] = [repeats[2][i] for i in (4, 5, 3, 2, 0, 1)]
        _confirm_repeats(session, repeats)
        assert checked(session.doc_estimate_click_error())["notice"]["code"] == (
            "click_error_estimated"
        )
        s = session.settings
        assert s.click_sigma_z is not None and s.click_sigma_z < 0.1
        assert s.click_perp_ratio <= 3.0


class TestParseClickError:
    """From test_myofibrils_and_uncertainty.py::TestParseClickError (the page parses the text)."""

    def test_empty_disables(self) -> None:
        session = new_session()
        session.doc_set_click_error(1.0)
        assert checked(session.doc_set_click_error(None))["notice"] is None
        assert session.settings.click_sigma_z is None

    def test_number(self) -> None:
        session = new_session()
        session.doc_set_click_error(12.5)
        assert session.settings.click_sigma_z == 12.5

    @pytest.mark.parametrize("bad", [-1.0, float("nan")])
    def test_invalid(self, bad: float) -> None:
        session = new_session()
        assert checked(session.doc_set_click_error(bad))["notice"] == code("click_error_invalid")
        assert session.settings.click_sigma_z is None

    def test_invalid_text_never_reaches_the_engine(self) -> None:
        schema = catalogue()["commands"]["doc.set_click_error"]["args"]
        with pytest.raises(SchemaError, match="expected number or null, got string"):
            validate({"sigma": "abc"}, schema, 0, "args")


class TestSessionState:
    def test_default_state(self) -> None:
        """From test_session.py::TestAnalysisSessionState::test_default_state."""
        session = AnnotationSession()
        assert session.mode == "home" and session.image is None
        assert session.items == [] and session.flow.clicks == []
        assert session.settings.unit == "px"

    def test_has_image_with_path(self) -> None:
        """From test_session.py::TestAnalysisSessionState::test_has_image_with_path."""
        session = AnnotationSession()
        session.doc_new(IMAGE)
        assert session.image is not None and session.image.file_name == "micrograph.png"

    def test_clear_sarcomere_annotations(self) -> None:
        """From test_session.py::TestAnalysisSessionState::test_clear_sarcomere_annotations."""
        session = _session()
        session.doc_load_v1(_chain_file(), SINGLE_IMAGE, False)
        _two_sarcomeres(session)
        session.shape_click(5, 5)
        state = checked(session.shape_clear())
        assert state["pending"]["marks"] == []
        assert [i["type"] for i in state["items"]] == ["chain", "chain", "pair"]


class TestUnsavedWork:
    def test_empty_session_has_nothing_to_lose(self) -> None:
        """From test_session.py::TestUnsavedWork::test_empty_session_has_nothing_to_lose."""
        assert not AnnotationSession().not_exported
        assert not new_session().not_exported

    def test_new_annotation_is_unsaved_until_marked_saved(self) -> None:
        """From test_session.py::TestUnsavedWork::test_new_annotation_is_unsaved_until_marked_saved."""
        session = new_session()
        confirm_shape(session, SQUARE_POINTS)
        assert session.not_exported
        session.doc_export("v1")
        assert not session.not_exported
        confirm_shape(session, shifted(SQUARE_POINTS, 40))
        assert session.not_exported
        session.shape_undo()
        assert not session.not_exported

    def test_pending_points_alone_are_not_work(self) -> None:
        """From test_session.py::TestUnsavedWork::test_pending_points_alone_are_not_work."""
        session = new_session()
        session.shape_click(1.0, 1.0)
        session.shape_click(2.0, 2.0)
        assert not checked(session.state())["not_exported"]

    def test_clearing_everything_leaves_nothing_to_lose(self) -> None:
        """From test_session.py::TestUnsavedWork::test_clearing_everything_leaves_nothing_to_lose."""
        session = new_session()
        confirm_shape(session, SQUARE_POINTS)
        session.shape_clear()
        assert not session.not_exported


class TestTimestamps:
    def test_export_has_an_iso_timestamp(self) -> None:
        """From test_analysis_timestamp.py::test_gui_analysis_has_an_iso_timestamp.

        The engine's one wall-clock stamp is the export's ``created_utc``,
        taken from the session clock; the results themselves hold no time.
        """
        moment = datetime(2026, 11, 20, 9, 30, 15, tzinfo=timezone.utc)
        session = AnnotationSession(utc_now=lambda: moment)
        session.doc_new(IMAGE)
        confirm_shape(session, SQUARE_POINTS)
        stamp = json.loads(session.doc_export("v1")["text"])["created_utc"]
        assert stamp == "2026-11-20T09:30:15Z"
        assert datetime.fromisoformat(stamp.replace("Z", "+00:00")) == moment

    def test_results_hold_no_wall_clock_time(self) -> None:
        session = _session()
        session.doc_load_v1(_chain_file(), SINGLE_IMAGE, False)
        _two_sarcomeres(session)
        results = session.analysis()
        assert results is not None and results.shape_summary is not None
        assert results.shape_summary.chain.timestamp == ""
        assert all(c.metrics is not None and c.metrics.timestamp is None for c in results.chains)
        assert not math.isnan(results.chains[0].metrics.linearity_ratio)
