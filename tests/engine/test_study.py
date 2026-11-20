"""Headless tests of the study engine (:mod:`sarcostat.engine.study`) driven through a recording view.

Ported from the tests of the desktop study mode; the tests of the Tk canvas
and of the ``gui --study`` command line are not ported, since neither exists
in the engine.
"""

from __future__ import annotations

import ast
import json
import struct
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from sarcostat.engine import study as study_mode
from sarcostat.engine.study import (
    M_LINE_SKIP_REASON,
    StudyControls,
    StudyPresenter,
    StudyView,
)
from sarcostat.io.study import (
    TRANSFORMS,
    DisplayMapping,
    StudyError,
    check_item_image,
    dumps_canonical,
    load_session_kit,
)

from .study_kit import (
    CHAIN_ZLINES,
    PAIR_MYOFIBRILS,
    SHAPE_POINTS,
    FakeClock,
    RecordingView,
    annotate_shape,
    annotate_zlines,
    click_original,
    make_kit,
)

SPECS = [("shape", "rot90"), ("chain", "transpose"), ("shape", "flip_h"), ("pair", "rot270")]
UTC_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


@pytest.fixture
def setup(tmp_path):
    kit_dir = make_kit(tmp_path, SPECS)
    clock = FakeClock()
    view = RecordingView()
    out = tmp_path / "out"
    presenter = StudyPresenter(load_session_kit(kit_dir), out, view, clock=clock)
    return presenter, view, clock, kit_dir, out


@pytest.fixture
def started(setup):
    presenter = setup[0]
    assert presenter.confirm_identity() is None
    return setup


def _reopen(kit_dir, out, clock=None, view=None):
    return StudyPresenter(
        load_session_kit(kit_dir), out, view or RecordingView(), clock=clock or FakeClock()
    )


def _finish_all(presenter, clock) -> None:
    """Annotate every remaining item of the SPECS kit."""
    while not presenter.finished:
        kind = presenter.current_item["type"]
        if kind == "shape":
            annotate_shape(presenter, clock)
            assert presenter.confirm_item() is None
        elif kind == "chain":
            annotate_zlines(presenter, CHAIN_ZLINES, clock)
            assert presenter.confirm_item(branch_end=True) is None
        else:
            annotate_zlines(presenter, PAIR_MYOFIBRILS[0], clock)
            assert presenter.next_myofibril() is None
            annotate_zlines(presenter, PAIR_MYOFIBRILS[1], clock)
            assert presenter.confirm_item() is None
        clock.advance(3.0)


def _parse(stamp: str) -> datetime:
    return datetime.strptime(stamp, UTC_FORMAT)


class TestIdentityAndDisplay:
    def test_nothing_is_shown_before_the_identity_is_confirmed(self, setup) -> None:
        presenter, view, _clock, _kit, _out = setup
        assert presenter.identity == ("demo", "ANN1", "S1")
        text = presenter.identity_text()
        assert "ANN1" in text and "S1" in text and "demo" in text
        assert view.count("show_item") == 0
        assert presenter.add_click(10.0, 10.0) is not None
        assert presenter.skip_item("x") is not None
        assert presenter.records == []

    def test_confirmation_shows_the_first_item_and_its_roi(self, started) -> None:
        presenter, view, _clock, kit_dir, _out = started
        kit_file, roi, title = view.last("show_item")
        kit = load_session_kit(kit_dir)
        item = kit.items[0]
        mapping = DisplayMapping.from_item(item)
        assert kit_file == item["kit_file"]
        data = kit.read_file(kit_file)
        check_item_image(item, data)
        assert struct.unpack(">II", data[16:24]) == mapping.display_size
        assert roi == mapping.roi_to_display(item["roi"])
        assert "1 of 4" in title
        assert "A (Z-line 1 start)" in view.statuses[-1]
        assert presenter.confirm_identity() is not None

    def test_finished_session_cannot_be_reopened(self, tmp_path) -> None:
        kit_dir = make_kit(tmp_path, SPECS)
        out = tmp_path / "out"
        out.mkdir()
        (out / "demo_ANN1_S1.json").write_text("{}", encoding="utf-8")
        with pytest.raises(StudyError, match="cannot be overwritten"):
            _reopen(kit_dir, out)


class TestKitChecksAtOpen:
    def test_a_tampered_item_image_stops_the_session_before_it_starts(self, tmp_path) -> None:
        kit_dir = make_kit(tmp_path, SPECS)
        last = kit_dir / "items" / f"{len(SPECS):03d}.png"
        last.write_bytes(last.read_bytes() + b"\0")
        with pytest.raises(StudyError, match="SHA-256"):
            _reopen(kit_dir, tmp_path / "out")

    def test_images_that_format_v1_rejects_stop_the_session(self, tmp_path) -> None:
        kit = load_session_kit(make_kit(tmp_path, SPECS))
        bad = dict(kit.manifest)
        bad["images"] = [dict(kit.manifest["images"][0], file_name="x" * 300)]
        broken = type(kit)(kit.directory, bad, kit.manifest_sha256)
        with pytest.raises(StudyError, match="file_name"):
            StudyPresenter(broken, tmp_path / "out", RecordingView(), clock=FakeClock())


class TestShapeItems:
    def test_six_clicks_give_exact_original_points(self, started) -> None:
        presenter, view, clock, _kit, _out = started
        clock.advance(2.0)
        annotate_shape(presenter, clock)
        assert presenter.confirm_item() is None
        record = presenter.records[0]
        assert record["points"] == {
            "z1": [list(SHAPE_POINTS[0]), list(SHAPE_POINTS[1])],
            "m": [list(SHAPE_POINTS[2]), list(SHAPE_POINTS[3])],
            "z2": [list(SHAPE_POINTS[4]), list(SHAPE_POINTS[5])],
        }
        clicks = record["raw_clicks"]
        assert [c["order"] for c in clicks] == [1, 2, 3, 4, 5, 6]
        assert [c["seconds"] for c in clicks] == [2.0, 3.5, 5.0, 6.5, 8.0, 9.5]
        assert all(c["undo_count"] == 0 for c in clicks)
        assert record["active_seconds"] == 11.0
        assert record["skipped"] is False
        assert record["skip_reason"] is None
        assert record["skipped_parts"] == []
        assert "branch_end" not in record
        assert "2 of 4" in view.last("show_item")[2]

    @pytest.mark.parametrize("name", TRANSFORMS)
    def test_exact_coordinates_under_every_transform(self, tmp_path, name) -> None:
        kit_dir = make_kit(tmp_path, [("shape", name), ("shape", "identity")])
        presenter = _reopen(kit_dir, tmp_path / "out")
        presenter.confirm_identity()
        mapping = DisplayMapping.from_item(presenter.current_item)
        display = mapping.to_display(SHAPE_POINTS)
        annotate_shape(presenter)
        assert presenter.confirm_item() is None
        record = presenter.records[0]
        flat = [p for key in ("z1", "m", "z2") for p in record["points"][key]]
        assert flat == [list(p) for p in SHAPE_POINTS]
        assert [[c["x"], c["y"]] for c in record["raw_clicks"]] == display.tolist()

    def test_clicks_outside_the_displayed_crop_are_refused(self, started) -> None:
        presenter, _view, _clock, _kit, _out = started
        w, h = DisplayMapping.from_item(presenter.current_item).display_size
        for x, y in [(-0.6, 5.0), (5.0, h - 0.4), (w - 0.4, 3.0), (float("nan"), 1.0)]:
            assert presenter.add_click(x, y) is not None
        assert presenter.add_click(-0.5, h - 0.5) is None
        assert len(presenter.current_clicks) == 1

    def test_clicks_are_quantised_to_the_grid(self, started) -> None:
        presenter, _view, _clock, _kit, _out = started
        presenter.add_click(10.123456789, 20.987654321)
        x, y = presenter.current_clicks[0]
        assert x * 1024 == int(x * 1024) and y * 1024 == int(y * 1024)
        assert abs(x - 10.123456789) < 1e-3

    def test_incomplete_or_extra_clicks_are_refused(self, started) -> None:
        presenter, _view, _clock, _kit, _out = started
        annotate_shape(presenter, points=SHAPE_POINTS[:5])
        assert presenter.confirm_item() is not None
        assert click_original(presenter, *SHAPE_POINTS[5]) is None
        assert click_original(presenter, 70.0, 70.0) is not None
        assert len(presenter.current_clicks) == 6

    def test_degenerate_line_is_refused(self, started) -> None:
        presenter, _view, _clock, _kit, _out = started
        points = list(SHAPE_POINTS)
        points[1] = (40.25, 50.25)
        annotate_shape(presenter, points=points)
        assert "Z-line 1" in presenter.confirm_item()
        assert presenter.records == []


class TestUndo:
    def test_undo_is_limited_to_the_current_item_and_counted(self, started) -> None:
        presenter, view, clock, _kit, _out = started
        annotate_shape(presenter, clock, points=SHAPE_POINTS[:2])
        assert presenter.undo() is None
        assert presenter.undo() is None
        clock.advance(1.0)
        annotate_shape(presenter, clock)
        assert presenter.confirm_item() is None
        record = presenter.records[0]
        assert [c["undo_count"] for c in record["raw_clicks"]] == [2] * 6
        assert len(record["raw_clicks"]) == 6

        assert presenter.undo() is None
        assert "Nothing to undo" in view.statuses[-1]
        assert presenter.records[0] == record
        assert presenter._state.undo_count == 0

    def test_there_is_no_way_back(self) -> None:
        names = {n for n in dir(StudyPresenter) if not n.startswith("_")}
        assert not {n for n in names if "prev" in n or "back" in n or "goto" in n}

    def test_undo_redraws_the_marks(self, started) -> None:
        presenter, view, _clock, _kit, _out = started
        annotate_shape(presenter, points=SHAPE_POINTS[:3])
        assert len(view.last("show_marks")[0]) == 3
        presenter.undo()
        marks, strokes = view.last("show_marks")
        assert [m.role for m in marks] == ["A", "B"]
        assert len(strokes) == 1


class TestSkips:
    def test_partial_m_skip_keeps_z(self, started) -> None:
        presenter, _view, _clock, _kit, _out = started
        annotate_shape(presenter, points=SHAPE_POINTS[:2])
        assert presenter.skip_m_line() is None
        annotate_shape(presenter, points=SHAPE_POINTS[4:])
        assert presenter.confirm_item() is None
        record = presenter.records[0]
        assert record["skipped"] is False
        assert record["skipped_parts"] == ["m_line"]
        assert record["skip_reason"] == M_LINE_SKIP_REASON == "M-line not identifiable"
        assert record["points"] == {
            "z1": [list(SHAPE_POINTS[0]), list(SHAPE_POINTS[1])],
            "z2": [list(SHAPE_POINTS[4]), list(SHAPE_POINTS[5])],
        }
        assert [c["order"] for c in record["raw_clicks"]] == [1, 2, 3, 4]

    def test_m_skip_labels_the_next_clicks_e_and_f(self, started) -> None:
        presenter, view, _clock, _kit, _out = started
        presenter.skip_m_line()
        annotate_shape(presenter, points=SHAPE_POINTS[:3])
        assert [m.role for m in view.last("show_marks")[0]] == ["A", "B", "E"]
        assert "F (Z-line 2 end)" in view.statuses[-1]

    def test_m_skip_is_refused_once_the_m_line_is_started(self, started) -> None:
        presenter, _view, _clock, _kit, _out = started
        annotate_shape(presenter, points=SHAPE_POINTS[:3])
        assert presenter.skip_m_line() is not None
        assert presenter.skip_m_line() is not None

    def test_undo_reverts_the_m_skip(self, started) -> None:
        presenter, _view, _clock, _kit, _out = started
        annotate_shape(presenter, points=SHAPE_POINTS[:2])
        presenter.skip_m_line()
        presenter.undo()
        annotate_shape(presenter, points=SHAPE_POINTS[2:])
        assert presenter.confirm_item() is None
        record = presenter.records[0]
        assert record["skipped_parts"] == [] and "m" in record["points"]
        assert record["raw_clicks"][2]["undo_count"] == 1

    def test_m_skip_only_for_shape_items(self, started) -> None:
        presenter, _view, clock, _kit, _out = started
        annotate_shape(presenter, clock)
        presenter.confirm_item()
        assert presenter.current_item["type"] == "chain"
        assert presenter.skip_m_line() is not None

    def test_a_skip_needs_a_reason(self, started) -> None:
        presenter, _view, _clock, _kit, _out = started
        annotate_shape(presenter, points=SHAPE_POINTS[:2])
        assert presenter.skip_item("") is not None
        assert presenter.skip_item("   ") is not None
        assert presenter.records == []
        assert presenter.skip_item("  Target not visible  ") is None
        record = presenter.records[0]
        assert record["skipped"] is True
        assert record["skip_reason"] == "Target not visible"
        assert record["skipped_parts"] == []
        assert record["points"] == {}
        assert len(record["raw_clicks"]) == 2
        assert presenter.current_item["type"] == "chain"


class TestChainAndPair:
    def _to_chain(self, presenter, clock):
        annotate_shape(presenter, clock)
        presenter.confirm_item()

    def test_chain_with_branch_end(self, started) -> None:
        presenter, view, clock, _kit, _out = started
        self._to_chain(presenter, clock)
        controls = view.last("show_controls")[0]
        assert controls.item_type == "chain" and controls.shows_branch_end
        annotate_zlines(presenter, CHAIN_ZLINES[:1])
        assert presenter.confirm_item() is not None
        click_original(presenter, 90.0, 50.0)
        assert presenter.confirm_item() is not None
        presenter.undo()
        annotate_zlines(presenter, CHAIN_ZLINES[1:])
        assert presenter.confirm_item(branch_end=True) is None
        record = presenter.records[1]
        assert record["points"] == {"z_lines": [[list(a), list(b)] for a, b in CHAIN_ZLINES]}
        assert record["branch_end"] is True
        assert len(record["raw_clicks"]) == 6

    def test_pair_needs_two_myofibrils_of_two_zlines(self, started, monkeypatch) -> None:
        presenter, _view, clock, _kit, _out = started
        monkeypatch.setattr(study_mode, "_session_bytes", dumps_canonical)
        self._to_chain(presenter, clock)
        annotate_zlines(presenter, CHAIN_ZLINES)
        presenter.confirm_item()
        annotate_shape(presenter, clock)
        presenter.confirm_item()
        assert presenter.current_item["type"] == "pair"
        assert presenter.next_myofibril() is not None
        annotate_zlines(presenter, PAIR_MYOFIBRILS[0][:1])
        assert presenter.next_myofibril() is not None
        annotate_zlines(presenter, PAIR_MYOFIBRILS[0][1:])
        assert presenter.confirm_item() is not None
        assert presenter.next_myofibril() is None
        assert presenter.next_myofibril() is not None
        presenter.undo()
        assert presenter.next_myofibril() is None
        annotate_zlines(presenter, PAIR_MYOFIBRILS[1][:1])
        assert presenter.confirm_item() is not None
        annotate_zlines(presenter, PAIR_MYOFIBRILS[1][1:])
        assert presenter.confirm_item(branch_end=True) is None
        record = presenter.records[3]
        assert record["points"] == {
            "myofibrils": [[[list(a), list(b)] for a, b in fib] for fib in PAIR_MYOFIBRILS]
        }
        assert "branch_end" not in record
        assert record["raw_clicks"][-1]["undo_count"] == 1


class TestAutosaveAndResume:
    def test_every_action_is_saved_atomically(self, started) -> None:
        presenter, _view, _clock, _kit, out = started
        click_original(presenter, *SHAPE_POINTS[0])
        saved = json.loads(presenter.autosave_path.read_text(encoding="utf-8"))
        assert len(saved["current"]["actions"]) == 1
        presenter.undo()
        saved = json.loads(presenter.autosave_path.read_text(encoding="utf-8"))
        assert saved["current"]["actions"] == [] and saved["current"]["undo_count"] == 1
        assert sorted(p.name for p in out.iterdir()) == ["demo_ANN1_S1.autosave.json"]

    def test_resume_after_a_crash(self, started) -> None:
        presenter, _view, clock, kit_dir, out = started
        annotate_shape(presenter, clock)
        presenter.confirm_item()
        annotate_zlines(presenter, CHAIN_ZLINES[:2], clock)
        presenter.undo()
        records_before = presenter.records
        del presenter

        clock.advance(600.0)
        view = RecordingView()
        resumed = _reopen(kit_dir, out, clock=clock, view=view)
        assert resumed.resumed
        assert view.count("show_item") == 0
        resumed.confirm_identity()
        assert resumed.records == records_before
        assert resumed.current_item["item_id"] == "S1-002"
        marks, _strokes = view.last("show_marks")
        assert len(marks) == 3
        click_original(resumed, *CHAIN_ZLINES[1][1])
        annotate_zlines(resumed, CHAIN_ZLINES[2:], clock)
        assert resumed.confirm_item() is None
        record = resumed.records[1]
        assert record["points"] == {"z_lines": [[list(a), list(b)] for a, b in CHAIN_ZLINES]}
        assert [c["undo_count"] for c in record["raw_clicks"]] == [0, 0, 0, 1, 1, 1]
        assert record["active_seconds"] < 600.0

    def test_autosave_of_another_kit_is_refused(self, started, tmp_path) -> None:
        presenter, _view, _clock, _kit, out = started
        click_original(presenter, *SHAPE_POINTS[0])
        other = make_kit(tmp_path / "other", SPECS[:3])
        with pytest.raises(StudyError, match="autosave"):
            _reopen(other, out)

    def test_corrupt_autosave_is_refused(self, started) -> None:
        presenter, _view, _clock, kit_dir, out = started
        click_original(presenter, *SHAPE_POINTS[0])
        presenter.autosave_path.write_text("{not json", encoding="utf-8")
        with pytest.raises(StudyError, match="autosave"):
            _reopen(kit_dir, out)


class TestTiming:
    def test_pause_stops_the_item_clock_and_hides_the_image(self, started) -> None:
        presenter, view, clock, _kit, _out = started
        clock.advance(4.0)
        assert presenter.pause() is None
        assert view.calls[-1][0] in ("show_paused", "show_status", "show_controls")
        assert view.count("show_paused") == 1
        assert presenter.add_click(5.0, 5.0) is not None
        clock.advance(100.0)
        assert presenter.resume() is None
        assert view.last("show_item")[0] is not None
        clock.advance(1.0)
        annotate_shape(presenter)
        presenter.confirm_item()
        assert presenter.records[0]["active_seconds"] == 5.0
        assert presenter.records[0]["raw_clicks"][0]["seconds"] == 5.0

    def test_timestamps_are_monotonic_when_the_wall_clock_jumps_back(
        self, started, monkeypatch
    ) -> None:
        presenter, _view, clock, kit_dir, out = started
        monkeypatch.setattr(study_mode, "_session_bytes", dumps_canonical)
        annotate_shape(presenter, clock)
        presenter.confirm_item()
        clock.utc -= timedelta(hours=2)
        clock.advance(1.0)
        annotate_zlines(presenter, CHAIN_ZLINES, clock)
        presenter.confirm_item()
        annotate_shape(presenter, clock)
        del presenter
        clock.utc -= timedelta(hours=1)
        resumed = _reopen(kit_dir, out, clock=clock)
        resumed.confirm_identity()
        resumed.confirm_item()
        _finish_all(resumed, clock)
        stamps = []
        for record in resumed.records:
            assert _parse(record["started_utc"]) <= _parse(record["finished_utc"])
            stamps += [_parse(record["started_utc"]), _parse(record["finished_utc"])]
            seconds = [c["seconds"] for c in record["raw_clicks"]]
            assert seconds == sorted(seconds)
            assert all(s <= record["active_seconds"] for s in seconds)
        assert stamps == sorted(stamps)
        assert _parse(resumed.created_utc) <= stamps[0]


class TestFinishing:
    def test_complete_session_writes_a_valid_v1_file(self, started) -> None:
        from sarcostat.io.annotations import read_annotations, validate_annotations

        presenter, view, clock, kit_dir, out = started
        _finish_all(presenter, clock)
        assert presenter.finished
        path = out / "demo_ANN1_S1.json"
        assert view.last("show_finished") == (path.name,)
        assert not presenter.autosave_path.exists()
        doc = read_annotations(path)
        validate_annotations(doc)
        assert [i["item_id"] for i in doc["items"]] == [
            i["item_id"] for i in load_session_kit(kit_dir).items
        ]
        assert doc["annotator_id"] == "ANN1" and doc["session_id"] == "S1"
        assert doc["study_id"] == "demo"
        assert presenter.add_click(5.0, 5.0) is not None
        with pytest.raises(StudyError, match="cannot be overwritten"):
            _reopen(kit_dir, out)

    def test_existing_output_is_never_replaced(self, started) -> None:
        presenter, _view, clock, _kit, out = started
        for _ in range(3):
            if presenter.current_item["type"] == "shape":
                annotate_shape(presenter, clock)
            else:
                annotate_zlines(presenter, CHAIN_ZLINES, clock)
            assert presenter.confirm_item() is None
        annotate_zlines(presenter, PAIR_MYOFIBRILS[0], clock)
        presenter.next_myofibril()
        annotate_zlines(presenter, PAIR_MYOFIBRILS[1], clock)
        (out / "demo_ANN1_S1.json").write_text("precious", encoding="utf-8")
        assert "cannot be overwritten" in presenter.confirm_item()
        assert not presenter.finished
        assert (out / "demo_ANN1_S1.json").read_text(encoding="utf-8") == "precious"
        assert presenter.autosave_path.exists()


class TestNoMetricFeedback:
    METRIC_MODULES = {"core", "analysis", "export", "presenter", "reports", "annotate", "document"}

    @pytest.mark.parametrize("module", ["study.py", "clock.py"])
    def test_study_modules_do_not_import_metric_code(self, module) -> None:
        path = Path(study_mode.__file__).with_name(module)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                parts = (node.module or "").split(".")
                if node.level == 0:
                    assert parts[:2] != ["sarcostat", "core"], node.module
                    assert parts[:2] != ["sarcostat", "gui"], node.module
                    if parts[:1] == ["sarcostat"]:
                        assert not set(parts[1:]) & self.METRIC_MODULES, node.module
                else:
                    assert not set(parts) & self.METRIC_MODULES, node.module
                    if not node.module:
                        assert not {a.name for a in node.names} & self.METRIC_MODULES
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("sarcostat.core"), alias.name
                    assert not set(alias.name.split(".")) & self.METRIC_MODULES, alias.name

    def test_the_view_protocol_has_no_metric_channel(self) -> None:
        methods = {n for n in vars(StudyView) if not n.startswith("_")}
        assert methods == {
            "show_status",
            "show_item",
            "show_marks",
            "show_controls",
            "show_paused",
            "show_finished",
        }

    def test_no_metric_reaches_the_view_in_a_full_session(self, started, monkeypatch) -> None:
        for name in ("sarcostat.core", "sarcostat.analysis", "sarcostat.export"):
            monkeypatch.setitem(sys.modules, name, None)
        presenter, view, clock, _kit, _out = started
        _finish_all(presenter, clock)
        allowed = {
            "show_status",
            "show_item",
            "show_marks",
            "show_controls",
            "show_paused",
            "show_finished",
        }
        assert {name for name, _ in view.calls} <= allowed
        words = ("asr", "cur=", "curviness", "aspect", "length", "angle", "classification")
        words += ("barrel", "hourglass", "rectangular", "square", "sigma", "register")
        words += ("linearity", "mvl", " nm", "µm")
        for _name, args in view.calls:
            for arg in args:
                if isinstance(arg, str):
                    assert not any(w in arg.lower() for w in words), arg
                elif isinstance(arg, StudyControls):
                    assert set(vars(arg)) == {
                        "can_confirm",
                        "can_undo",
                        "can_skip_m_line",
                        "can_next_myofibril",
                        "shows_branch_end",
                        "item_type",
                    }


class TestReadWhitelist:
    def test_session_reads_only_listed_files(self, tmp_path, monkeypatch) -> None:
        """Record every file the process opens or lists during a full session."""
        kit_dir = make_kit(tmp_path, SPECS)
        out = tmp_path / "out"
        opened: list[str] = []
        listed: list[str] = []
        recording = {"on": False}

        def hook(event, args):
            if not recording["on"]:
                return
            if event == "open" and isinstance(args[0], (str, bytes, Path)):
                opened.append(
                    str(
                        Path(
                            args[0] if not isinstance(args[0], bytes) else args[0].decode()
                        ).resolve()
                    )
                )
            elif event in ("os.listdir", "os.scandir", "glob.glob"):
                listed.append(str(args[0]))

        sys.addaudithook(hook)
        monkeypatch.setattr(study_mode, "_session_bytes", dumps_canonical)
        clock = FakeClock()
        recording["on"] = True
        try:
            presenter = StudyPresenter(load_session_kit(kit_dir), out, RecordingView(), clock=clock)
            presenter.confirm_identity()
            _finish_all(presenter, clock)
        finally:
            recording["on"] = False
        kit_root = kit_dir.resolve()
        allowed = {kit_root / "manifest.json", kit_root / "manifest.sha256"}
        allowed |= {kit_root / f"items/{k:03d}.png" for k in range(1, len(SPECS) + 1)}
        in_tmp = [Path(p) for p in opened if Path(p).is_relative_to(tmp_path.resolve())]
        assert in_tmp, "the audit hook saw no reads"
        for path in in_tmp:
            assert path in allowed or path.is_relative_to(out.resolve()), path
        assert not [p for p in listed if Path(p).resolve().is_relative_to(kit_root)]
        assert (kit_root / "targets.json") not in in_tmp
        assert (kit_root / "items/unlisted.png") not in in_tmp
