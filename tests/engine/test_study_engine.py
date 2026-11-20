"""Tests of the study engine beyond the desktop rules.

Layering (no metric, UI or image code), the view-state whitelist, roles
instead of colours, prompt and notice codes, the explicit clock, the
session stores with their ``seq``, resuming from memory, and the call
surface used by downstream projects that drive a session headless.
"""

from __future__ import annotations

import ast
import json
import math
import string
import subprocess
import sys
import textwrap
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from sarcostat.engine import study as study_mode
from sarcostat.engine.clock import ExplicitClock, SystemClock
from sarcostat.engine.study import (
    NOTICES,
    PARAM_WORDS,
    VIEW_STATE_KEYS,
    FileSessionStore,
    Mark,
    MemorySessionStore,
    Notice,
    Stroke,
    StudyControls,
    StudyPresenter,
    notice_text,
)
from sarcostat.io.annotations import read_annotations, validate_annotations
from sarcostat.io.study import (
    DisplayMapping,
    StudyError,
    dumps_canonical,
    load_session_kit,
    quantize_coordinate,
)

from . import scripted_session
from .study_kit import (
    CHAIN_ZLINES,
    PAIR_MYOFIBRILS,
    SHAPE_POINTS,
    FakeClock,
    FixedClock,
    QuietView,
    RecordingView,
    annotate_shape,
    annotate_zlines,
    click_original,
    make_kit,
    original_image,
    png_bytes,
)

SPECS6 = [
    ("shape", "rot90"),
    ("chain", "transpose"),
    ("shape", "flip_h"),
    ("pair", "rot270"),
    ("shape", "anti_transpose"),
    ("chain", "identity"),
]
SPECS = SPECS6[:4]
UTC_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
WALL_MS = 1_795_085_291_000.0
STEP_S = 1.5
STEM = "demo_ANN1_S1"
FORBIDDEN_IMPORTS = (
    "sarcostat.core",
    "sarcostat.gui",
    "sarcostat.analysis",
    "sarcostat.export",
    "sarcostat.cli",
    "sarcostat.simulation",
    "matplotlib",
    "PIL",
    "tkinter",
    "pandas",
    "tifffile",
    "typer",
    "rich",
    "scipy",
    "numpy",
)
ALLOWED_SARCOSTAT_IMPORTS = {
    "study.py": {
        "sarcostat",
        "sarcostat.io.annotations",
        "sarcostat.io.provenance",
        "sarcostat.io.study",
        "sarcostat.engine.clock",
    },
    "clock.py": set(),
}
MARK_ROLES = {"A", "B", "C", "D", "E", "F", "chain", "myofibril_1", "myofibril_2"}
STROKE_ROLES = {"z1", "m", "z2", "chain", "myofibril_1", "myofibril_2"}
TK_AUTOSAVE_KEYS = {
    "format",
    "format_version",
    "manifest_sha256",
    "study_id",
    "annotator_id",
    "session_id",
    "created_utc",
    "last_utc",
    "records",
    "current",
}


def _parse(stamp: str) -> datetime:
    return datetime.strptime(stamp, UTC_FORMAT).replace(tzinfo=timezone.utc)


class Ticker:
    """Sets an :class:`ExplicitClock` before every action, as the browser does at each input event."""

    def __init__(self, mono_s: float, wall_ms: float, jumps: dict[int, float] | None = None):
        self.mono = mono_s
        self.wall = wall_ms
        self.jumps = jumps or {}
        self.count = 0
        self.clock = ExplicitClock(mono_s, wall_ms)

    def __call__(self) -> None:
        self.count += 1
        self.mono += STEP_S
        self.wall += STEP_S * 1000.0 + self.jumps.get(self.count, 0.0)
        self.clock.set(self.mono, self.wall)


def _scripted(kit, store, ticker: Ticker, stop_after: int | None = None) -> StudyPresenter:
    return scripted_session.run_scripted_session(
        kit, store, clock=ticker.clock, stop_after=stop_after, tick=ticker
    )


def _stamps(doc: dict[str, Any]) -> list[datetime]:
    out = [_parse(doc["created_utc"])]
    for item in doc["items"]:
        out += [_parse(item["started_utc"]), _parse(item["finished_utc"])]
    return out


class TestLayering:
    @staticmethod
    def _imports(module: str) -> list[str]:
        tree = ast.parse(Path(study_mode.__file__).with_name(module).read_text(encoding="utf-8"))
        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0:
                    names.append(node.module or "")
                else:
                    assert node.level == 1, node.module
                    names.append(f"sarcostat.engine.{node.module}" if node.module else "")
        return names

    @pytest.mark.parametrize("module", ["study.py", "clock.py"])
    def test_study_engine_imports_no_metric_ui_or_image_code(self, module) -> None:
        names = self._imports(module)
        assert names
        for name in names:
            assert name, "bare relative import"
            for forbidden in FORBIDDEN_IMPORTS:
                assert name != forbidden and not name.startswith(forbidden + "."), name
            if name.split(".")[0] == "sarcostat":
                assert name in ALLOWED_SARCOSTAT_IMPORTS[module], name

    @pytest.mark.needs_cpython(reason="runs a subprocess; Emscripten has no processes")
    def test_a_full_session_loads_no_metric_code(self, tmp_path) -> None:
        kit_dir = make_kit(tmp_path, SPECS6)
        root = Path(__file__).resolve().parents[2]
        watched = list(FORBIDDEN_IMPORTS[:-1]) + [
            "sarcostat.engine.annotate",
            "sarcostat.engine.document",
            "sarcostat.engine.reports",
        ]
        code = textwrap.dedent(
            f"""
            import json
            import sys

            sys.path.insert(0, {str(root)!r})
            import sarcostat.engine.study
            from tests.engine.scripted_session import run_scripted_session

            presenter = run_scripted_session({str(kit_dir)!r}, {str(tmp_path / "out")!r})
            watched = {watched!r}
            loaded = sorted(
                m for m in sys.modules if any(m == w or m.startswith(w + ".") for w in watched)
            )
            print(json.dumps({{"finished": presenter.finished, "loaded": loaded}}))
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout.strip().splitlines()[-1])
        assert report == {"finished": True, "loaded": []}


class TestViewState:
    @staticmethod
    def _check_state(presenter: StudyPresenter, state: dict[str, Any]) -> None:
        assert set(state) == VIEW_STATE_KEYS
        json.dumps(state, allow_nan=False)
        assert set(state["controls"]) == {f.name for f in fields(StudyControls)}
        assert set(state["progress"]) == {"done", "total"}
        for key in ("step", "notice"):
            code = state[key]
            if code is not None:
                assert set(code) == {"code", "params"} and code["code"] in NOTICES
                for value in code["params"].values():
                    assert isinstance(value, (str, int, bool)) and not isinstance(value, float)
        assert state["finished"] is None or set(state["finished"]) == {"file_name"}
        numbers: list[tuple[str, Any]] = []

        def walk(path: str, value: Any) -> None:
            if isinstance(value, bool) or value is None or isinstance(value, str):
                return
            if isinstance(value, (int, float)):
                numbers.append((path, value))
            elif isinstance(value, dict):
                for k, v in value.items():
                    walk(f"{path}.{k}", v)
            else:
                for v in value:
                    walk(f"{path}[]", v)

        walk("", state)
        coordinates = {".roi[]", ".marks[].x", ".marks[].y", ".strokes[].p1[]", ".strokes[].p2[]"}
        for path, value in numbers:
            if path in coordinates:
                assert isinstance(value, float) and math.isfinite(value), path
            else:
                assert path in {".progress.done", ".progress.total"} or path.startswith(
                    (".step.params.", ".notice.params.")
                ), path
                assert isinstance(value, int) and 0 <= value <= 1000, path
        if state["item_file"] is None:
            assert state["roi"] is None and state["marks"] == [] and state["strokes"] == []
            return
        item = presenter.current_item
        assert item is not None and state["item_file"] == item["kit_file"]
        mapping = DisplayMapping.from_item(item)
        assert tuple(state["roi"]) == mapping.roi_to_display(item["roi"])
        assert [(m["x"], m["y"]) for m in state["marks"]] == presenter.current_clicks
        w, h = mapping.display_size
        for mark in state["marks"]:
            assert set(mark) == {"x", "y", "role"} and mark["role"] in MARK_ROLES
            assert -0.5 <= mark["x"] <= w - 0.5 and -0.5 <= mark["y"] <= h - 0.5
        for stroke in state["strokes"]:
            assert set(stroke) == {"p1", "p2", "role"} and stroke["role"] in STROKE_ROLES

    def test_every_state_of_a_session_has_only_whitelisted_keys_and_numbers(self, tmp_path) -> None:
        kit = load_session_kit(make_kit(tmp_path, SPECS6))
        presenter = StudyPresenter(kit, MemorySessionStore(), RecordingView(), clock=FakeClock())
        states = [presenter.view_state()]

        def tick() -> None:
            states.append(presenter.view_state())
            self._check_state(presenter, states[-1])

        assert presenter.add_click(1.0, 1.0) is not None
        tick()
        assert presenter.confirm_identity() is None
        assert presenter.add_click(float("nan"), 2.0) is not None
        tick()
        assert presenter.pause() is None
        tick()
        assert presenter.resume() is None
        while not presenter.finished:
            scripted_session.annotate_open_item(presenter, len(presenter.records) + 1, tick)
        tick()
        assert len(states) > 40
        assert states[0]["step"]["code"] == "identity" and states[0]["item_file"] is None
        assert states[2]["notice"] == {"code": "click_outside_image", "params": {}}
        assert states[3]["paused"] is True and states[3]["item_file"] is None
        assert states[3]["step"] == {"code": "paused", "params": {}}
        assert states[-1]["finished"] == {"file_name": f"{STEM}.json"}
        assert states[-1]["step"]["code"] == "session_complete"
        assert states[-1]["progress"] == {"done": 6, "total": 6}

    def test_marks_and_strokes_carry_roles_not_colours(self, tmp_path) -> None:
        assert [f.name for f in fields(Mark)] == ["x", "y", "role"]
        assert [f.name for f in fields(Stroke)] == ["p1", "p2", "role"]
        kit = load_session_kit(make_kit(tmp_path, SPECS))
        presenter = StudyPresenter(kit, MemorySessionStore(), RecordingView(), clock=FakeClock())
        presenter.confirm_identity()

        def roles() -> tuple[list[str], list[str]]:
            state = presenter.view_state()
            return [m["role"] for m in state["marks"]], [s["role"] for s in state["strokes"]]

        annotate_shape(presenter)
        assert roles() == (["A", "B", "C", "D", "E", "F"], ["z1", "m", "z2"])
        presenter.confirm_item()
        annotate_zlines(presenter, CHAIN_ZLINES[:2])
        click_original(presenter, *CHAIN_ZLINES[2][0])
        assert roles() == (["chain"] * 5, ["chain"] * 2)
        click_original(presenter, *CHAIN_ZLINES[2][1])
        presenter.confirm_item()
        annotate_shape(presenter, points=SHAPE_POINTS[:2])
        presenter.skip_m_line()
        annotate_shape(presenter, points=SHAPE_POINTS[4:])
        assert roles() == (["A", "B", "E", "F"], ["z1", "z2"])
        presenter.confirm_item()
        annotate_zlines(presenter, PAIR_MYOFIBRILS[0])
        assert roles() == (["myofibril_1"] * 4, ["myofibril_1"] * 2)
        presenter.next_myofibril()
        annotate_zlines(presenter, PAIR_MYOFIBRILS[1][:1])
        assert roles() == (
            ["myofibril_1"] * 4 + ["myofibril_2"] * 2,
            ["myofibril_1"] * 2 + ["myofibril_2"],
        )
        text = json.dumps(presenter.view_state()).lower()
        assert "colo" not in text and "#" not in text and "dashed" not in text


class TestCodes:
    def test_every_code_has_one_sentence_with_its_parameters(self) -> None:
        for code, template in NOTICES.items():
            names = {name for _, name, _, _ in string.Formatter().parse(template) if name}
            params: dict[str, str | int] = {
                name: next(iter(PARAM_WORDS[name])) if name in PARAM_WORDS else 7 for name in names
            }
            text = Notice(code, params).text
            assert "{" not in text and "}" not in text, code
            assert text == notice_text(code, params)
            assert "\n" not in text or code.startswith("identity"), code
        with pytest.raises(KeyError):
            Notice("no_such_code")

    def test_refusals_return_the_sentence_of_the_notice_code(self, tmp_path) -> None:
        kit = load_session_kit(make_kit(tmp_path, SPECS))
        view = RecordingView()
        presenter = StudyPresenter(kit, MemorySessionStore(), view, clock=FakeClock())

        def notice() -> dict[str, Any] | None:
            result: dict[str, Any] | None = presenter.view_state()["notice"]
            return result

        assert presenter.add_click(1.0, 1.0) == "Confirm your identity first."
        assert notice() == {"code": "identity_not_confirmed", "params": {}}
        presenter.confirm_identity()
        assert notice() is None
        assert presenter.undo() is None
        assert notice() == {"code": "nothing_to_undo", "params": {}}
        assert view.statuses[-1] == NOTICES["nothing_to_undo"]
        annotate_shape(presenter, points=SHAPE_POINTS[:5])
        assert notice() is None
        text = presenter.confirm_item()
        assert text == "Place all 6 points of the sarcomere first (5 placed)."
        assert notice() == {"code": "shape_points_missing", "params": {"expected": 6, "placed": 5}}
        assert presenter.next_myofibril() == "Only a pair item has a second myofibril."
        for _ in range(4):
            assert presenter.undo() is None
        click_original(presenter, SHAPE_POINTS[0][0] + 0.25, SHAPE_POINTS[0][1] + 0.25)
        assert view.statuses[-1].startswith("Sarcomere: click C (M-line start) (3 of 6).")
        state = presenter.view_state()
        assert state["step"] == {
            "code": "shape_click_or_skip_m_line",
            "params": {"landmark": "C", "index": 3, "total": 6},
        }
        assert view.statuses[-1] == notice_text(state["step"]["code"], state["step"]["params"])
        annotate_shape(presenter, points=SHAPE_POINTS[2:])
        text = presenter.confirm_item()
        assert (
            text
            == "The ends of Z-line 1 (A-B) are less than 1 pixel apart; undo and click them again."
        )
        assert notice() == {"code": "shape_line_too_short", "params": {"line": "z1"}}
        assert presenter.skip_item(" ") == "A skipped item needs a reason."
        assert presenter.skip_item("blurred") is None
        annotate_zlines(presenter, CHAIN_ZLINES[:1])
        text = presenter.confirm_item()
        assert text == "The chain needs at least 2 Z-lines."
        assert notice() == {"code": "zlines_too_few", "params": {"part": "chain"}}


class TestExplicitClock:
    def test_a_smaller_monotonic_value_is_clamped_and_counted(self) -> None:
        clock = ExplicitClock(10.0, WALL_MS)
        clock.set(12.5, WALL_MS + 2500)
        assert clock.monotonic() == 12.5 and clock.clamped == 0
        clock.set(11.0, WALL_MS + 4000)
        assert clock.monotonic() == 12.5 and clock.clamped == 1
        assert clock.utc_now() == datetime.fromtimestamp((WALL_MS + 4000) / 1000, timezone.utc)
        clock.set(13.0, WALL_MS - 3_600_000)
        assert clock.monotonic() == 13.0 and clock.clamped == 1

    def test_the_wall_clock_is_aware_utc_to_the_microsecond(self) -> None:
        clock = ExplicitClock(0.0, 1_795_085_291_123.5)
        moment = clock.utc_now()
        assert moment.tzinfo is not None and moment.utcoffset() == timedelta(0)
        assert moment == datetime(2026, 11, 19, 10, 48, 11, 123500, tzinfo=timezone.utc)
        assert isinstance(SystemClock().utc_now().tzinfo, timezone)

    @pytest.mark.parametrize(
        "mono, wall",
        [
            (float("nan"), WALL_MS),
            (float("inf"), WALL_MS),
            (-0.5, WALL_MS),
            (True, WALL_MS),
            ("1", WALL_MS),
            (1.0, float("nan")),
            (1.0, 1e20),
            (1.0, None),
        ],
    )
    def test_bad_values_are_refused_and_change_nothing(self, mono, wall) -> None:
        with pytest.raises(ValueError):
            ExplicitClock(mono, wall)
        clock = ExplicitClock(5.0, WALL_MS)
        with pytest.raises(ValueError):
            clock.set(mono, wall)
        assert clock.monotonic() == 5.0 and clock.utc_now() == ExplicitClock(5.0, WALL_MS).utc_now()

    def test_wall_clock_jumps_keep_timestamps_ordered(self, tmp_path) -> None:
        kit = load_session_kit(make_kit(tmp_path, SPECS6))
        first = Ticker(10.0, WALL_MS, jumps={12: -3_600_000.0, 25: 86_400_000.0})
        store = MemorySessionStore()
        _scripted(kit, store, first, stop_after=3)
        loaded = store.load_autosave()
        assert loaded is not None
        second = Ticker(0.25, first.wall - 3_600_000.0, jumps={6: 86_400_000.0, 9: -3_600_000.0})
        resumed = MemorySessionStore(loaded[1], loaded[0])
        presenter = _scripted(kit, resumed, second)
        assert presenter.finished and resumed.finished_bytes is not None
        doc = json.loads(resumed.finished_bytes)
        stamps = _stamps(doc)
        assert stamps == sorted(stamps)
        assert stamps[6] - stamps[0] <= timedelta(seconds=first.mono - 10.0)
        for item in doc["items"]:
            seconds = [c["seconds"] for c in item["raw_clicks"]]
            assert seconds == sorted(seconds) and all(s <= item["active_seconds"] for s in seconds)

    def test_a_monotonic_clock_that_goes_back_does_not_reorder_anything(self, tmp_path) -> None:
        kit = load_session_kit(make_kit(tmp_path, SPECS6))
        ticker = Ticker(50.0, WALL_MS)
        original = ticker.__call__

        def tick() -> None:
            original()
            if ticker.count == 20:
                ticker.mono -= 30.0
                ticker.clock.set(ticker.mono, ticker.wall)

        store = MemorySessionStore()
        presenter = scripted_session.run_scripted_session(kit, store, clock=ticker.clock, tick=tick)
        assert presenter.finished and ticker.clock.clamped >= 1
        doc = json.loads(store.finished_bytes or b"")
        stamps = _stamps(doc)
        assert stamps == sorted(stamps)


class TestStores:
    @pytest.fixture(params=["memory", "file"])
    def store(self, request, tmp_path):
        made = MemorySessionStore() if request.param == "memory" else FileSessionStore(tmp_path)
        made.bind(STEM)
        return made

    @staticmethod
    def _doc(seq: int) -> bytes:
        return dumps_canonical({"format": "sarcostat-study-autosave", "seq": seq})

    def test_a_lower_or_equal_seq_is_refused(self, store) -> None:
        assert store.load_autosave() is None
        store.put_autosave(3, self._doc(3))
        for seq in (3, 2, 0):
            with pytest.raises(StudyError, match="refused"):
                store.put_autosave(seq, self._doc(seq))
        assert store.load_autosave() == (3, self._doc(3))
        store.put_autosave(4, self._doc(4))
        assert store.load_autosave() == (4, self._doc(4))
        for bad in (-1, True, 5.0, "6"):
            with pytest.raises(StudyError):
                store.put_autosave(bad, self._doc(7))

    def test_the_finished_file_is_stored_once(self, store) -> None:
        store.put_autosave(1, self._doc(1))
        assert not store.finished_exists()
        store.put_finished(f"{STEM}.json", b"first")
        assert store.finished_exists()
        with pytest.raises(StudyError, match="cannot be overwritten"):
            store.put_finished(f"{STEM}.json", b"second")
        store.discard_autosave()
        assert store.load_autosave() is None
        with pytest.raises(StudyError, match="refused"):
            store.put_autosave(1, self._doc(1))
        with pytest.raises(StudyError, match="bare file name"):
            store.put_finished("../x.json", b"x")

    def test_a_store_serves_one_session(self, store) -> None:
        store.bind(STEM)
        with pytest.raises(StudyError, match="belongs to session"):
            store.bind("demo_ANN2_S1")

    def test_a_second_file_store_on_the_folder_refuses_an_older_seq(self, tmp_path) -> None:
        a, b = FileSessionStore(tmp_path), FileSessionStore(tmp_path)
        a.bind(STEM)
        b.bind(STEM)
        a.put_autosave(5, self._doc(5))
        with pytest.raises(StudyError, match="refused"):
            b.put_autosave(3, self._doc(3))
        b.put_autosave(6, self._doc(6))
        assert a.load_autosave() == (6, self._doc(6))
        assert a.autosave_path == tmp_path / f"{STEM}.autosave.json"
        assert a.output_path == tmp_path / f"{STEM}.json"

    def test_a_memory_autosave_needs_its_seq(self) -> None:
        with pytest.raises(StudyError, match="seq"):
            MemorySessionStore(b"{}")
        with pytest.raises(StudyError, match="seq"):
            MemorySessionStore(b"{}", -2)

    def test_a_stale_presenter_cannot_overwrite_a_newer_autosave(self, tmp_path) -> None:
        kit = load_session_kit(make_kit(tmp_path, SPECS))
        out = tmp_path / "out"
        stale = StudyPresenter(kit, out, RecordingView(), clock=FakeClock())
        stale.confirm_identity()
        click_original(stale, *SHAPE_POINTS[0])
        newer = StudyPresenter(kit, out, RecordingView(), clock=FakeClock())
        assert newer.resumed and newer.seq == stale.seq == 2
        newer.confirm_identity()
        with pytest.raises(StudyError, match="refused"):
            click_original(stale, *SHAPE_POINTS[1])
        saved = json.loads(newer.autosave_path.read_text(encoding="utf-8"))
        assert saved["seq"] == newer.seq == 3

    def test_the_autosave_keeps_the_desktop_fields_and_gains_seq(self, tmp_path) -> None:
        kit = load_session_kit(make_kit(tmp_path, SPECS))
        store = MemorySessionStore()
        presenter = StudyPresenter(kit, store, RecordingView(), clock=FakeClock())
        assert store.seq == presenter.seq == 0 and store.autosave is None
        seqs = []

        def tick() -> None:
            if store.autosave is not None:
                doc = json.loads(store.autosave)
                assert set(doc) == TK_AUTOSAVE_KEYS | {"seq"}
                assert doc["seq"] == store.seq == presenter.seq
                seqs.append(doc["seq"])

        presenter.confirm_identity()
        while not presenter.finished:
            scripted_session.annotate_open_item(presenter, len(presenter.records) + 1, tick)
        assert seqs == list(range(1, len(seqs) + 1))
        assert store.autosave is None and store.seq == presenter.seq == seqs[-1] + 1

    def test_an_autosave_whose_seq_differs_from_the_store_is_refused(self, tmp_path) -> None:
        kit = load_session_kit(make_kit(tmp_path, SPECS))
        store = MemorySessionStore()
        presenter = StudyPresenter(kit, store, RecordingView(), clock=FakeClock())
        presenter.confirm_identity()
        click_original(presenter, *SHAPE_POINTS[0])
        assert store.autosave is not None
        with pytest.raises(StudyError, match="not valid"):
            StudyPresenter(kit, MemorySessionStore(store.autosave, store.seq + 1), RecordingView())
        resumed = StudyPresenter(
            kit, MemorySessionStore(store.autosave, store.seq), RecordingView()
        )
        assert resumed.resumed and resumed.current_clicks == presenter.current_clicks

    def test_resume_from_memory_reproduces_the_uninterrupted_file(self, tmp_path) -> None:
        kit = load_session_kit(make_kit(tmp_path, SPECS6))
        straight = MemorySessionStore()
        _scripted(kit, straight, Ticker(100.0, WALL_MS))
        crashed = MemorySessionStore()
        before = Ticker(100.0, WALL_MS)
        _scripted(kit, crashed, before, stop_after=3)
        assert crashed.finished_bytes is None and crashed.autosave is not None
        resumed = MemorySessionStore(crashed.autosave, crashed.seq)
        _scripted(kit, resumed, Ticker(2.0, before.wall + 600_000.0))
        assert straight.finished_bytes is not None and resumed.finished_bytes is not None
        a, b = json.loads(straight.finished_bytes), json.loads(resumed.finished_bytes)
        assert b["items"][:3] == a["items"][:3]
        assert b["items"][3]["started_utc"] == a["items"][3]["started_utc"]
        for doc in (a, b):
            for k, item in enumerate(doc["items"]):
                del item["active_seconds"]
                del item["finished_utc"]
                if k > 3:
                    del item["started_utc"]
        assert b == a
        assert _stamps(json.loads(resumed.finished_bytes)) == sorted(
            _stamps(json.loads(resumed.finished_bytes))
        )


class TestItemImages:
    def test_item_images_are_checked_from_their_bytes_at_open(self, tmp_path) -> None:
        cases = [
            ("bit depth 16", lambda a: png_bytes(a, depth16=True)),
            ("'gAMA' chunk", lambda a: png_bytes(a, extra_chunks=[(b"gAMA", b"\0\0\xb1\x8f")])),
            ("size", lambda a: png_bytes(a[:-1])),
        ]
        for k, (message, encode) in enumerate(cases):
            kit = load_session_kit(make_kit(tmp_path / str(k), SPECS, encode=encode))
            with pytest.raises(StudyError, match=message):
                StudyPresenter(kit, MemorySessionStore(), RecordingView(), clock=FakeClock())

    def test_an_item_image_changed_after_the_open_is_refused_when_shown(self, tmp_path) -> None:
        kit_dir = make_kit(tmp_path, SPECS)
        presenter = StudyPresenter(
            load_session_kit(kit_dir), MemorySessionStore(), RecordingView(), clock=FakeClock()
        )
        presenter.confirm_identity()
        (kit_dir / "items" / "002.png").write_bytes(png_bytes(original_image()[:90, :120]))
        annotate_shape(presenter)
        with pytest.raises(StudyError, match="SHA-256"):
            presenter.confirm_item()


class TestPaperCallSurface:
    def test_a_session_driven_like_the_synthetic_study(self, tmp_path) -> None:
        assert StudyPresenter.__module__ == "sarcostat.engine.study"
        kit = load_session_kit(make_kit(tmp_path, SPECS6))
        raw_dir = tmp_path / "raw_sessions"
        clock = FixedClock(datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc))
        presenter = StudyPresenter(kit, raw_dir, QuietView(), clock=clock)
        assert presenter.confirm_identity() is None
        assert presenter.current_item["item_id"] == "S1-001"
        assert presenter.output_path == raw_dir / f"{STEM}.json"
        assert presenter.autosave_path == raw_dir / f"{STEM}.autosave.json"
        assert presenter.autosave_path.exists()
        for position, item in enumerate(kit.items, start=1):
            assert presenter.current_item["item_id"] == item["item_id"]
            if position == 6:
                assert presenter.skip_item("synthetic skip") is None
                continue
            mapping = DisplayMapping.from_item(item)
            if item["type"] == "shape":
                lines = [SHAPE_POINTS]
            elif item["type"] == "chain":
                lines = [[p for line in CHAIN_ZLINES for p in line]]
            else:
                lines = [[p for line in fibril for p in line] for fibril in PAIR_MYOFIBRILS]
            if position == 1:
                assert presenter.pause() is None
                assert presenter.resume() is None
                assert presenter.add_click(5.0, 5.0) is None
                assert presenter.undo() is None
            for fibril, points in enumerate(lines):
                if fibril:
                    assert presenter.next_myofibril() is None
                for x, y in mapping.to_display(points):
                    assert (
                        presenter.add_click(quantize_coordinate(x), quantize_coordinate(y)) is None
                    )
            assert presenter.confirm_item() is None
        assert presenter.finished
        doc = read_annotations(presenter.output_path)
        validate_annotations(doc)
        assert presenter.output_path.name == f"{STEM}.json"
        assert not presenter.autosave_path.exists()
        assert [i["item_id"] for i in doc["items"]] == [i["item_id"] for i in kit.items]

    def test_a_session_skipped_item_by_item_with_the_system_clock(self, tmp_path) -> None:
        class QuietView:
            def __getattr__(self, name):
                return lambda *a, **k: None

        presenter = StudyPresenter(
            load_session_kit(make_kit(tmp_path, SPECS)), tmp_path / "out", QuietView()
        )
        assert presenter.confirm_identity() is None
        while not presenter.finished:
            assert presenter.skip_item("test") is None
        session = read_annotations(presenter.output_path)
        assert [i["skipped"] for i in session["items"]] == [True] * len(SPECS)

    def test_with_a_memory_store_there_are_no_paths(self, tmp_path) -> None:
        kit = load_session_kit(make_kit(tmp_path, SPECS))
        presenter = StudyPresenter(kit, MemorySessionStore(), QuietView())
        assert presenter.output_path is None and presenter.autosave_path is None
        assert presenter.output_dir is None
        assert presenter.file_name == f"{STEM}.json"
        assert presenter.autosave_name == f"{STEM}.autosave.json"
