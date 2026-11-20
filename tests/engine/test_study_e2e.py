"""End to end: demo kit from the repository images -> scripted headless session -> format v1 file.

Ported from the end-to-end test of the desktop study mode, plus the check
that the engine, on the scripted demo with a fixed clock, writes the session
file the desktop presenter wrote (``data/demo_DEMO_S1.tk.json``, recorded
from the desktop study presenter before the engine existed).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sarcostat.engine.study import MemorySessionStore
from sarcostat.io.annotations import dumps_annotations, read_annotations, validate_annotations
from sarcostat.io.study import (
    TRANSFORMS,
    DisplayMapping,
    StudyError,
    check_item_image,
    load_session_kit,
)

from . import demo_kit, scripted_session
from .study_kit import FixedClock

DATA = Path(__file__).resolve().parent / "data"
REFERENCE = DATA / "demo_DEMO_S1.tk.json"
REFERENCE_START = datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)
REFERENCE_STEP = 0.5
BUILD_FIELDS = ("source_digest", "sarcostat_version")


@pytest.fixture(scope="module")
def kit_dir(tmp_path_factory) -> Path:
    return demo_kit.make_demo_kit(tmp_path_factory.mktemp("demo") / "kit")


def _flat_points(points: dict) -> list[list[float]]:
    if "z_lines" in points:
        lines = points["z_lines"]
    elif "myofibrils" in points:
        lines = [line for fibril in points["myofibrils"] for line in fibril]
    else:
        lines = [points[key] for key in ("z1", "m", "z2") if key in points]
    return [p for line in lines for p in line]


def _without_timing(doc: dict) -> list[dict]:
    keys = ("started_utc", "finished_utc", "active_seconds")
    out = []
    for item in doc["items"]:
        item = {k: v for k, v in item.items() if k not in keys}
        item["raw_clicks"] = [
            {k: v for k, v in c.items() if k != "seconds"} for c in item["raw_clicks"]
        ]
        out.append(item)
    return out


def test_demo_kit_is_valid_and_uses_several_transforms(kit_dir) -> None:
    kit = load_session_kit(kit_dir)
    assert len(kit.items) == 6
    assert {i["type"] for i in kit.items} == {"shape", "chain", "pair"}
    assert len({i["display_transform"] for i in kit.items}) == 6
    assert {i["display_transform"] for i in kit.items} <= set(TRANSFORMS)
    repeated = [i for i in kit.items if i["target_id"] == "T01"]
    assert (
        len(repeated) == 2 and repeated[0]["display_transform"] != repeated[1]["display_transform"]
    )
    for item in kit.items:
        check_item_image(item, kit.read_file(item["kit_file"]))


def test_scripted_demo_session_validates(kit_dir, tmp_path) -> None:
    path = scripted_session.run_scripted_session(kit_dir, tmp_path / "out").output_path
    assert path.name == "demo_DEMO_S1.json"
    doc = read_annotations(path, image_dir=demo_kit.EXAMPLES)
    validate_annotations(doc)
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]

    items = doc["items"]
    assert [i["item_id"] for i in items] == [f"S1-{k:03d}" for k in range(1, 7)]
    assert items[0]["raw_clicks"][-1]["undo_count"] == 1
    assert items[2]["skipped_parts"] == ["m_line"] and "m" not in items[2]["points"]
    assert set(items[2]["points"]) == {"z1", "z2"}
    assert items[5]["skipped"] is True and items[5]["skip_reason"] == "Demo: skipped on purpose"
    assert len(items[3]["points"]["myofibrils"]) == 2

    for item in items:
        if item["skipped"]:
            continue
        mapping = DisplayMapping.from_item(item)
        display = [[c["x"], c["y"]] for c in item["raw_clicks"]]
        assert mapping.to_original(display).tolist() == _flat_points(item["points"])
        assert mapping.to_display(_flat_points(item["points"])).tolist() == display

    with pytest.raises(StudyError, match="cannot be overwritten"):
        scripted_session.run_scripted_session(kit_dir, tmp_path / "out")


def test_crash_and_resume_give_the_same_annotations(kit_dir, tmp_path) -> None:
    straight = read_annotations(
        scripted_session.run_scripted_session(kit_dir, tmp_path / "a").output_path
    )
    partial = scripted_session.run_scripted_session(kit_dir, tmp_path / "b", stop_after=3)
    assert not partial.output_path.exists()
    assert (tmp_path / "b" / "demo_DEMO_S1.autosave.json").exists()
    resumed = read_annotations(
        scripted_session.run_scripted_session(kit_dir, tmp_path / "b").output_path
    )
    assert _without_timing(resumed) == _without_timing(straight)


def test_file_store_writes_the_desktop_reference_file(kit_dir, tmp_path) -> None:
    clock = FixedClock(REFERENCE_START, REFERENCE_STEP)
    presenter = scripted_session.run_scripted_session(kit_dir, tmp_path / "out", clock=clock)
    produced = presenter.output_path.read_bytes()
    reference = REFERENCE.read_bytes()
    doc, expected = json.loads(produced), json.loads(reference)
    for key in BUILD_FIELDS:
        doc[key] = expected[key]
    assert doc == expected
    assert dumps_annotations(doc).encode("ascii") == reference


def test_memory_store_gives_the_file_store_bytes(kit_dir, tmp_path) -> None:
    on_disk = scripted_session.run_scripted_session(
        kit_dir, tmp_path / "out", clock=FixedClock(REFERENCE_START, REFERENCE_STEP)
    )
    store = MemorySessionStore()
    in_memory = scripted_session.run_scripted_session(
        kit_dir, store, clock=FixedClock(REFERENCE_START, REFERENCE_STEP)
    )
    assert in_memory.finished and in_memory.output_path is None
    assert store.finished_file_name == on_disk.output_path.name
    assert store.finished_bytes == on_disk.output_path.read_bytes()
    assert store.autosave is None
