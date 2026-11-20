"""The annotation session and its shape flow (engine of the web app's shape mode)."""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import math
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import sarcostat.engine
from sarcostat.analysis import analyze_file, sarcomere_ids
from sarcostat.engine.annotate import (
    LANDMARKS,
    M_SKIPPED_LANDMARKS,
    NOTICE_CODES,
    NOTICE_TEXT,
    STEP_CODES,
    STEP_TEXT,
    AnnotationSession,
    ModeState,
    ShapeFlow,
    code,
    describe,
)
from sarcostat.engine.bridge import BridgeError, Refused, catalogue, validate
from sarcostat.io.annotations import read_annotations

SHA_A = hashlib.sha256(b"micrograph A").hexdigest()
SHA_B = hashlib.sha256(b"micrograph B").hexdigest()
IMAGE = {"file_name": "micrograph.png", "sha256": SHA_A, "width_px": 300, "height_px": 200}
SQUARE_POINTS = [(10, 10), (10, 30), (20, 10), (20, 30), (30, 10), (30, 30)]
FIXED_UTC = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
MODE_STATE_SCHEMA = catalogue()["commands"]["shape.click"]["result"]
FORBIDDEN_IMPORTS = (
    "sarcostat.gui",
    "sarcostat.cli",
    "sarcostat.simulation",
    "matplotlib",
    "pandas",
    "PIL",
    "tifffile",
    "tkinter",
    "typer",
    "rich",
)


def checked(state: ModeState) -> ModeState:
    """``state`` after checking it against the bridge's ModeState schema."""
    validate(state, MODE_STATE_SCHEMA, 0, "state")
    assert json.loads(json.dumps(state, allow_nan=False)) == state
    return state


def new_session(image: dict[str, Any] | None = None) -> AnnotationSession:
    session = AnnotationSession(utc_now=lambda: FIXED_UTC)
    checked(session.doc_new(IMAGE if image is None else image))
    return session


def shifted(points: Sequence[tuple[float, float]], dx: float = 0.0, dy: float = 0.0) -> list:
    return [(x + dx, y + dy) for x, y in points]


def confirm_shape(
    session: AnnotationSession, points: Sequence[tuple[float, float]], m_skip: bool = False
) -> ModeState:
    """Click ``points`` (A-F, or A, B, E, F with ``m_skip``) and confirm."""
    for k, (x, y) in enumerate(points):
        if m_skip and k == 2:
            assert checked(session.shape_skip_m_line())["notice"] is None
        state = checked(session.shape_click(x, y))
        assert state["notice"] is None, state["notice"]
    state = checked(session.shape_confirm())
    assert state["notice"] is None, state["notice"]
    return state


def assert_identical(a: Any, b: Any, path: str = "results") -> None:
    """Equality bit for bit: same types, same float bits, arrays of the same dtype and bytes."""
    if dataclasses.is_dataclass(a) and not isinstance(a, type):
        assert type(a) is type(b), path
        for f in dataclasses.fields(a):
            assert_identical(getattr(a, f.name), getattr(b, f.name), f"{path}.{f.name}")
    elif isinstance(a, dict):
        assert isinstance(b, dict) and set(a) == set(b), path
        for key in a:
            assert_identical(a[key], b[key], f"{path}[{key!r}]")
    elif isinstance(a, (list, tuple)):
        assert type(a) is type(b) and len(a) == len(b), path
        for k, (x, y) in enumerate(zip(a, b, strict=True)):
            assert_identical(x, y, f"{path}[{k}]")
    elif isinstance(a, np.ndarray):
        assert isinstance(b, np.ndarray) and a.dtype == b.dtype and a.shape == b.shape, path
        assert a.tobytes() == b.tobytes(), path
    elif isinstance(a, (float, np.floating)):
        assert type(a) is type(b), path
        assert np.asarray(a).tobytes() == np.asarray(b).tobytes(), path
    else:
        assert type(a) is type(b) and a == b, path


class TestShapeFlow:
    def test_landmarks_in_order(self) -> None:
        flow = ShapeFlow()
        assert flow.landmarks == LANDMARKS
        for k, (x, y) in enumerate(SQUARE_POINTS):
            assert flow.step(1)["params"]["landmark"] == LANDMARKS[k]
            assert flow.click(x, y) is None
        assert flow.complete and flow.step(1) == code("shape_ready", sarcomere=1)
        assert flow.click(1, 1) == code("shape_complete")
        assert len(flow.clicks) == 6

    def test_m_line_skip_before_the_m_line(self) -> None:
        flow = ShapeFlow()
        flow.click(10, 10)
        flow.click(10, 30)
        assert flow.can_skip_m_line
        assert flow.skip_m_line() is None
        assert flow.landmarks == M_SKIPPED_LANDMARKS
        assert flow.skip_m_line() == code("m_line_already_skipped")
        flow.click(30, 10)
        flow.click(30, 30)
        assert flow.complete and flow.problem() is None
        assert flow.lines() == {
            "z1": [[10.0, 10.0], [10.0, 30.0]],
            "z2": [[30.0, 10.0], [30.0, 30.0]],
        }
        assert [m["role"] for m in flow.marks()] == ["A", "B", "E", "F"]
        assert [s["role"] for s in flow.strokes()] == ["z1", "z2"]

    def test_m_line_skip_refused_once_started(self) -> None:
        flow = ShapeFlow()
        for x, y in SQUARE_POINTS[:3]:
            flow.click(x, y)
        assert not flow.can_skip_m_line
        assert flow.skip_m_line() == code("m_line_started")
        assert not flow.m_skipped

    def test_undo_removes_one_step_at_a_time(self) -> None:
        flow = ShapeFlow()
        flow.click(10, 10)
        flow.skip_m_line()
        flow.click(20, 20)
        assert flow.undo() and flow.m_skipped and len(flow.clicks) == 1
        assert flow.undo() and not flow.m_skipped
        assert flow.undo() and flow.clicks == []
        assert flow.undo() is False

    def test_problems(self) -> None:
        flow = ShapeFlow()
        flow.click(10, 10)
        assert flow.problem() == code("shape_incomplete", placed=1, needed=6)
        flow.clear()
        for x, y in [(10, 10), (10, 30), (20, 10), (20.5, 10.5), (30, 10), (30, 30)]:
            flow.click(x, y)
        assert flow.problem() == code("line_too_short", line="m")
        flow.clear()
        for x, y in [(10, 10), (10, 30), (20, 10), (20, 30), (10, 30), (10, 10)]:
            flow.click(x, y)
        assert flow.problem() == code("shape_degenerate")

    def test_pending_strokes_follow_pairs(self) -> None:
        flow = ShapeFlow()
        for x, y in SQUARE_POINTS[:5]:
            flow.click(x, y)
        assert [s["role"] for s in flow.strokes()] == ["z1", "m"]
        assert [m["role"] for m in flow.marks()] == ["A", "B", "C", "D", "E"]


class TestModeState:
    def test_empty_session(self) -> None:
        state = checked(AnnotationSession().state())
        assert state["mode"] == "home" and state["image"] is None and state["items"] == []
        assert not any(state["controls"].values())
        assert state["step"] is None and state["not_exported"] is False

    def test_new_image_is_shape_mode(self) -> None:
        state = checked(AnnotationSession().doc_new(IMAGE))
        assert state["mode"] == "shape" and state["image"] == IMAGE
        assert state["step"] == code(
            "shape_click", sarcomere=1, landmark="A", number=1, of=6, can_skip_m_line=True
        )
        assert state["controls"] == {
            "can_confirm": False,
            "can_undo": False,
            "can_skip_m_line": True,
            "can_delete": False,
        }

    def test_confirmed_item(self) -> None:
        session = new_session()
        state = confirm_shape(session, SQUARE_POINTS)
        (item,) = state["items"]
        assert item["item_id"] == "sarcomere-1" and item["type"] == "shape"
        assert item["sarcomere_id"] == 1
        assert [(m["x"], m["y"], m["role"]) for m in item["marks"]] == [
            (10.0, 10.0, "A"),
            (10.0, 30.0, "B"),
            (20.0, 10.0, "C"),
            (20.0, 30.0, "D"),
            (30.0, 10.0, "E"),
            (30.0, 30.0, "F"),
        ]
        assert [s["role"] for s in item["strokes"]] == ["z1", "m", "z2"]
        assert state["pending"] == {"marks": [], "strokes": []}
        assert state["step"]["params"]["sarcomere"] == 2
        assert state["controls"]["can_delete"] and state["controls"]["can_undo"]
        assert state["not_exported"] is True

    def test_numbers_are_coordinates_counts_and_indexes(self) -> None:
        session = new_session()
        confirm_shape(session, SQUARE_POINTS)
        session.doc_set_scale(0.37, "furlong")
        session.doc_set_click_error(0.11)
        session.doc_set_reference_direction(12.5)
        state = checked(session.shape_click(5.25, 6.75))
        assert state["settings"] == {
            "pixel_size": 0.37,
            "unit": "furlong",
            "reference_direction_deg": 12.5,
            "click_sigma_z": 0.11,
            "click_sigma_m": 0.11,
            "click_perp_ratio": 1.0,
        }
        text = json.dumps({k: v for k, v in state.items() if k != "settings"})
        for value in ("0.37", "0.11", "12.5", "furlong"):
            assert value not in text
        assert state["pending"]["marks"] == [{"x": 5.25, "y": 6.75, "role": "A"}]

    def test_settings_are_null_when_off(self) -> None:
        state = checked(AnnotationSession().state())
        assert state["settings"] == {
            "pixel_size": 1.0,
            "unit": "px",
            "reference_direction_deg": None,
            "click_sigma_z": None,
            "click_sigma_m": None,
            "click_perp_ratio": 1.0,
        }

    def test_clicks_inside_the_pixel_centre_bounds(self) -> None:
        session = new_session()
        for x, y in [(-0.5, -0.5), (299.5, 199.5)]:
            assert checked(session.shape_click(x, y))["notice"] is None
        session.shape_undo()
        session.shape_undo()
        for x, y in [(-0.6, 0), (0, 199.6), (300, 10), (math.nan, 1), (1, math.inf), (True, 1)]:
            state = checked(session.shape_click(x, y))
            assert state["notice"] == code("click_outside_image")
            assert state["pending"]["marks"] == []

    def test_notice_lasts_one_command(self) -> None:
        session = new_session()
        assert session.shape_click(1000, 1)["notice"] == code("click_outside_image")
        assert session.shape_click(1, 1)["notice"] is None


class TestCommands:
    def test_commands_without_an_image_are_refused(self) -> None:
        session = AnnotationSession()
        for call in (
            lambda: session.shape_click(1, 1),
            session.shape_undo,
            session.shape_skip_m_line,
            session.shape_confirm,
            lambda: session.shape_delete("sarcomere-1"),
            session.shape_clear,
            session.doc_estimate_click_error,
            lambda: session.doc_export("v1"),
        ):
            with pytest.raises(Refused, match="Open an image first"):
                call()

    def test_settings_without_an_image(self) -> None:
        session = AnnotationSession()
        assert checked(session.doc_set_scale(2.0, "nm"))["notice"] is None
        assert checked(session.doc_set_click_error(1.0))["notice"] is None
        assert checked(session.doc_set_reference_direction(5.0))["notice"] is None
        assert session.settings.pixel_size == 2.0 and session.settings.click_sigma_z == 1.0

    def test_settings_survive_a_new_image(self) -> None:
        session = new_session()
        session.doc_set_scale(4.0, "nm")
        session.doc_set_click_error(2.0)
        session.doc_set_reference_direction(30.0)
        before = session.settings
        session.doc_new(dict(IMAGE, file_name="second.png", sha256=SHA_B))
        assert session.settings == before

    @pytest.mark.parametrize(
        "image",
        [
            dict(IMAGE, sha256="ABC"),
            dict(IMAGE, file_name="folder/a.png"),
            dict(IMAGE, file_name=""),
            dict(IMAGE, width_px=0),
            {"file_name": "a.png"},
        ],
    )
    def test_new_image_must_be_recordable(self, image: dict[str, Any]) -> None:
        session = new_session()
        before = session.state()
        with pytest.raises(Refused):
            session.doc_new(image)
        assert session.state() == before

    def test_undo_then_last_item(self) -> None:
        session = new_session()
        confirm_shape(session, SQUARE_POINTS)
        session.shape_click(50, 50)
        state = checked(session.shape_undo())
        assert state["pending"]["marks"] == [] and len(state["items"]) == 1
        state = checked(session.shape_undo())
        assert state["items"] == []
        assert checked(session.shape_undo())["notice"] == code("nothing_to_undo")

    def test_delete_by_item_id(self) -> None:
        session = new_session()
        for dx in (0, 40, 80):
            confirm_shape(session, shifted(SQUARE_POINTS, dx))
        session.shape_click(5, 5)
        state = checked(session.shape_delete("sarcomere-2"))
        assert [i["item_id"] for i in state["items"]] == ["sarcomere-1", "sarcomere-3"]
        assert [i["sarcomere_id"] for i in state["items"]] == [1, 2]
        assert len(state["pending"]["marks"]) == 1
        assert checked(session.shape_delete("sarcomere-2"))["notice"] == code(
            "item_not_found", item_id="sarcomere-2"
        )

    def test_new_ids_never_repeat(self) -> None:
        session = new_session()
        for dx in (0, 40):
            confirm_shape(session, shifted(SQUARE_POINTS, dx))
        session.shape_delete("sarcomere-1")
        state = confirm_shape(session, shifted(SQUARE_POINTS, 80))
        assert [i["item_id"] for i in state["items"]] == ["sarcomere-2", "sarcomere-3"]
        session.shape_clear()
        state = confirm_shape(session, SQUARE_POINTS)
        assert [i["item_id"] for i in state["items"]] == ["sarcomere-1"]

    def test_confirm_refusals_keep_the_clicks(self) -> None:
        session = new_session()
        session.shape_click(10, 10)
        state = checked(session.shape_confirm())
        assert state["notice"] == code("shape_incomplete", placed=1, needed=6)
        assert len(state["pending"]["marks"]) == 1 and state["items"] == []

    def test_m_line_skipped_item(self) -> None:
        session = new_session()
        state = confirm_shape(session, [(10, 10), (10, 30), (30, 10), (30, 30)], m_skip=True)
        (item,) = state["items"]
        assert [m["role"] for m in item["marks"]] == ["A", "B", "E", "F"]
        (stored,) = session.items
        assert stored["skipped_parts"] == ["m_line"]
        assert stored["skip_reason"] == "M-line not identifiable"
        assert "m" not in stored["points"]
        results = session.analysis()
        assert results is not None and results.shapes[0].skip_reason == "m_line_skipped"

    def test_skip_m_line_controls(self) -> None:
        session = new_session()
        for x, y in SQUARE_POINTS[:2]:
            assert session.shape_click(x, y)["controls"]["can_skip_m_line"]
        state = checked(session.shape_click(*SQUARE_POINTS[2]))
        assert not state["controls"]["can_skip_m_line"]
        assert checked(session.shape_skip_m_line())["notice"] == code("m_line_started")

    def test_click_error_estimate_needs_repeats(self) -> None:
        session = new_session()
        confirm_shape(session, SQUARE_POINTS)
        state = checked(session.doc_estimate_click_error())
        assert state["notice"] == code("click_error_too_few_repeats", n_repeats=1)
        assert session.settings.click_sigma_z is None

    @pytest.mark.parametrize("bad", [0.0, -1.0, math.nan, math.inf, True])
    def test_scale_validation(self, bad: Any) -> None:
        session = new_session()
        state = checked(session.doc_set_scale(bad, "nm"))
        assert state["notice"] == code("pixel_size_invalid")
        assert session.settings.pixel_size == 1.0

    def test_empty_unit_is_pixels(self) -> None:
        session = new_session()
        session.doc_set_scale(2.0, "  ")
        assert session.settings.unit == "px"

    @pytest.mark.parametrize("bad", [math.nan, -math.inf])
    def test_reference_direction_validation(self, bad: float) -> None:
        session = new_session()
        state = checked(session.doc_set_reference_direction(bad))
        assert state["notice"] == code("reference_direction_invalid")
        assert checked(session.doc_set_reference_direction(None))["notice"] is None
        assert session.settings.reference_direction_deg is None

    def test_results_exports_come_from_the_results(self) -> None:
        session = new_session()
        confirm_shape(session, SQUARE_POINTS)
        for what in ("results_json", "results_csv", "provenance"):
            with pytest.raises(Refused, match="sarcostat.export"):
                session.doc_export(what)

    def test_refusals_are_bridge_errors(self) -> None:
        with pytest.raises(BridgeError) as caught:
            AnnotationSession().shape_click(1, 1)
        assert caught.value.code == "refused"


class TestNoticeTexts:
    def test_codes(self) -> None:
        assert set(STEP_CODES) == set(STEP_TEXT) == {"shape_click", "shape_ready"}
        assert len(NOTICE_CODES) == len(set(NOTICE_CODES)) == len(NOTICE_TEXT)

    @pytest.mark.parametrize(
        "obj",
        [
            code("shape_click", sarcomere=2, landmark="C", number=3, of=6, can_skip_m_line=True),
            code("shape_click", sarcomere=1, landmark="E", number=3, of=4, can_skip_m_line=False),
            code("shape_ready", sarcomere=4),
            code("click_outside_image"),
            code("shape_complete"),
            code("shape_incomplete", placed=3, needed=6),
            code("line_too_short", line="z2"),
            code("shape_degenerate"),
            code("m_line_already_skipped"),
            code("m_line_started"),
            code("nothing_to_undo"),
            code("item_not_found", item_id="x"),
            code("item_not_shape", item_id="myofibril-1", type="chain"),
            code("pixel_size_invalid"),
            code("reference_direction_invalid"),
            code("click_error_invalid"),
            code("click_error_too_few_repeats", n_repeats=1),
            code("click_error_not_estimable"),
            code("click_error_estimated", n_repeats=4),
            code(
                "document_loaded",
                shape_items=2,
                chain_items=1,
                pair_items=1,
                incomplete_items=1,
                image_checked=False,
                missing_modes="straightness,register",
            ),
        ],
    )
    def test_every_code_has_one_english_line(self, obj: Any) -> None:
        text = describe(obj)
        assert text and "\n" not in text and "{" not in text and text.endswith((".", "?"))

    def test_every_listed_code_is_described(self) -> None:
        described = {
            "shape_click",
            "shape_ready",
            "click_outside_image",
            "shape_complete",
            "shape_incomplete",
            "line_too_short",
            "shape_degenerate",
            "m_line_already_skipped",
            "m_line_started",
            "nothing_to_undo",
            "item_not_found",
            "item_not_shape",
            "pixel_size_invalid",
            "reference_direction_invalid",
            "click_error_invalid",
            "click_error_too_few_repeats",
            "click_error_not_estimable",
            "click_error_estimated",
            "document_loaded",
        }
        assert set(STEP_CODES) | set(NOTICE_CODES) == described

    def test_step_texts(self) -> None:
        first = describe(
            code("shape_click", sarcomere=2, landmark="C", number=3, of=6, can_skip_m_line=True)
        )
        assert first == (
            "Sarcomere 2: click C (M-line start) (3 of 6). If the M-line is not identifiable, "
            "press Skip M-line."
        )
        loaded = describe(
            code(
                "document_loaded",
                shape_items=1,
                chain_items=0,
                pair_items=0,
                incomplete_items=0,
                image_checked=True,
                missing_modes="",
            )
        )
        assert loaded == "Loaded 1 sarcomere item(s), 0 chain item(s) and 0 pair item(s)."


class TestSarcomereNumbers:
    """Acceptance M2-2 #5: numbers and class-stability seeds as ``analysis.py`` gives them."""

    def test_delete_then_add_matches_the_exported_file(self, tmp_path: Path) -> None:
        session = new_session()
        shapes = [
            shifted([(10, 10), (11, 30), (19, 11), (21, 29), (30, 10), (29, 31)], 0),
            shifted([(10, 10), (10, 24), (22, 10), (21, 25), (40, 11), (40, 23)], 60),
            shifted([(10, 10), (12, 30), (20, 12), (19, 28), (27, 10), (28, 31)], 120),
            shifted([(10, 10), (10, 26), (18, 9), (19, 27), (26, 11), (25, 26)], 180),
        ]
        for points in shapes[:3]:
            confirm_shape(session, points)
        session.doc_set_click_error(2.5)
        session.shape_delete("sarcomere-2")
        state = confirm_shape(session, shapes[3])
        assert [(i["item_id"], i["sarcomere_id"]) for i in state["items"]] == [
            ("sarcomere-1", 1),
            ("sarcomere-3", 2),
            ("sarcomere-4", 3),
        ]
        path = tmp_path / "a.json"
        path.write_text(session.doc_export("v1")["text"], encoding="utf-8")
        assert sarcomere_ids(read_annotations(path)) == {
            i["item_id"]: i["sarcomere_id"] for i in state["items"]
        }
        from_file = analyze_file(path).images[0]
        from_session = session.analysis()
        assert from_session is not None and from_file.uncertainties is not None
        assert [u.sarcomere_id for u in from_file.uncertainties] == [1, 2, 3]
        assert_identical(from_session.uncertainties, from_file.uncertainties)
        assert_identical(from_session.shapes, from_file.shapes)
        assert any(u.stability.stability < 1.0 for u in from_file.uncertainties)

        fresh = new_session()
        for points in (shapes[0], shapes[2], shapes[3]):
            confirm_shape(fresh, points)
        fresh.doc_set_click_error(2.5)
        fresh_results = fresh.analysis()
        assert fresh_results is not None
        assert_identical(fresh_results.uncertainties, from_file.uncertainties)


class TestLayering:
    """Acceptance M2-2 #6: the engine imports no GUI, plotting, table or CLI library."""

    @staticmethod
    def _imports(path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names.add(node.module)
                names.update(f"{node.module}.{alias.name}" for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level > 0:
                names.add("." * node.level + (node.module or ""))
        return names

    def test_engine_imports(self) -> None:
        folder = Path(sarcostat.engine.__file__).resolve().parent
        files = sorted(folder.rglob("*.py"))
        assert {"bridge.py", "annotate.py", "document.py"} <= {p.name for p in files}
        problems = []
        for path in files:
            for name in sorted(self._imports(path)):
                if name.startswith(".") and name.lstrip(".").startswith("gui"):
                    problems.append(f"{path.name}: {name}")
                for banned in FORBIDDEN_IMPORTS:
                    if name == banned or name.startswith(banned + "."):
                        problems.append(f"{path.name}: {name}")
        assert problems == []

    def test_the_check_sees_an_import(self, tmp_path: Path) -> None:
        sample = tmp_path / "sample.py"
        sample.write_text("from sarcostat.gui import session\nimport matplotlib.pyplot\n")
        assert {"sarcostat.gui", "matplotlib.pyplot"} <= self._imports(sample)
