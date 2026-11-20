"""Contract of the bridge between the web worker and the engine (catalogue v1)."""

from __future__ import annotations

import json
import math
import subprocess
import sys
from typing import Any

import pytest

from sarcostat import __version__
from sarcostat.engine import bridge
from sarcostat.engine.bridge import (
    BRIDGE_VERSION,
    MAX_JSON_DEPTH,
    SCHEMA_KEYWORDS,
    SCHEMA_TYPES,
    Bridge,
    NeedsConfirmation,
    Refused,
    SchemaError,
    catalogue,
    dispatch,
    json_depth,
    validate,
)

T = {"mono_s": 0.0, "wall_ms": 0.0}

EXPECTED_ARGS = {
    "sys.identity": set(),
    "sys.load_scipy": set(),
    "doc.new": {"image"},
    "doc.load_v1": {"text", "image", "confirm"},
    "doc.export": {"what"},
    "doc.set_scale": {"pixel_size", "unit"},
    "doc.set_click_error": {"sigma"},
    "doc.estimate_click_error": set(),
    "doc.set_reference_direction": {"deg"},
    "shape.click": {"x", "y"},
    "shape.undo": set(),
    "shape.skip_m_line": set(),
    "shape.confirm": set(),
    "shape.delete": {"item_id"},
    "shape.clear": set(),
    "results.get": set(),
    "study.open_kit": {"manifest", "manifest_sha256"},
    "study.verify_items": {"files"},
    "study.start": {"autosave", "seq", "t"},
    "study.confirm_identity": {"t"},
    "study.click": {"x", "y", "t"},
    "study.undo": {"t"},
    "study.skip_m_line": {"t"},
    "study.next_myofibril": {"t"},
    "study.confirm_item": {"branch_end", "t"},
    "study.skip_item": {"reason", "t"},
    "study.pause": {"t"},
    "study.resume": {"t"},
}
MODE_STATE = {
    "mode",
    "image",
    "settings",
    "pending",
    "items",
    "controls",
    "step",
    "notice",
    "not_exported",
}
STUDY_RESULT = {"view", "autosave", "seq", "finished"}
STUDY_VIEW = {
    "item_file",
    "roi",
    "title",
    "marks",
    "strokes",
    "controls",
    "step",
    "progress",
    "notice",
    "paused",
    "finished",
}
EXPECTED_RESULTS = {
    "sys.identity": {"sarcostat_version", "source_digest", "bridge_version"},
    "sys.load_scipy": {"scipy_version"},
    "doc.export": {"file_name", "text"},
    "results.get": {"sentences", "tables", "plot_data"},
    "study.open_kit": {"study_id", "annotator_id", "session_id", "n_items", "files"},
    "study.verify_items": {"ok", "problems"},
}


def _commands() -> dict[str, Any]:
    return catalogue()["commands"]


def _types(schema: dict[str, Any]) -> list[str]:
    kind = schema["type"]
    return [kind] if isinstance(kind, str) else list(kind)


def minimal_instance(schema: dict[str, Any], buffers: list[bytes]) -> Any:
    """The smallest value matching ``schema`` (a stand-in for the generated mock worker)."""
    kind = _types(schema)[0]
    if "enum" in schema:
        return schema["enum"][0]
    if kind == "buffer":
        buffers.append(b"")
        return {"$buf": len(buffers) - 1}
    if kind == "object":
        properties = schema.get("properties")
        if properties is None:
            return {}
        return {key: minimal_instance(properties[key], buffers) for key in schema["required"]}
    if kind == "array":
        return []
    if kind in ("integer", "number"):
        return schema.get("minimum", 0)
    return {"string": "", "boolean": False, "null": None}[kind]


def schema_problems(schema: Any, path: str = "$") -> list[str]:
    """Why ``schema`` is not a well-formed schema of the catalogue's subset."""
    if not isinstance(schema, dict):
        return [f"{path}: not an object"]
    problems = [f"{path}: keyword {k!r}" for k in schema if k not in SCHEMA_KEYWORDS]
    if "type" not in schema:
        return [*problems, f"{path}: no type"]
    kinds = _types(schema)
    if not kinds or any(k not in SCHEMA_TYPES for k in kinds) or len(set(kinds)) != len(kinds):
        problems.append(f"{path}: bad type {schema['type']!r}")
    if "object" in kinds:
        extra = schema.get("additionalProperties")
        if "properties" in schema:
            if extra is not False:
                problems.append(
                    f"{path}: an object with properties needs additionalProperties false"
                )
            required = schema.get("required", [])
            if set(required) - set(schema["properties"]) or len(set(required)) != len(required):
                problems.append(f"{path}: required keys outside properties")
            for key, sub in schema["properties"].items():
                problems += schema_problems(sub, f"{path}.{key}")
        elif isinstance(extra, dict) and "required" not in schema:
            problems += schema_problems(extra, f"{path}[*]")
        else:
            problems.append(f"{path}: an object needs properties or a value schema")
    elif {"properties", "required", "additionalProperties"} & set(schema):
        problems.append(f"{path}: object keywords on a non-object")
    if "array" in kinds:
        if "items" not in schema:
            problems.append(f"{path}: an array needs items")
        else:
            problems += schema_problems(schema["items"], f"{path}[]")
    elif "items" in schema:
        problems.append(f"{path}: items on a non-array")
    if "enum" in schema:
        values = schema["enum"]
        if not isinstance(values, list) or not values:
            problems.append(f"{path}: enum must be a non-empty list")
        else:
            for value in values:
                try:
                    validate(value, {"type": schema["type"]})
                except SchemaError:
                    problems.append(f"{path}: enum value {value!r} outside the type")
    if "minimum" in schema and (
        not {"integer", "number"} & set(kinds)
        or isinstance(schema["minimum"], bool)
        or not isinstance(schema["minimum"], (int, float))
    ):
        problems.append(f"{path}: minimum on a non-number")
    return problems


def _keys(schema: dict[str, Any]) -> set[str]:
    return set(schema["properties"])


def call(cmd: str, args: Any, ident: Any = 7, buffers=(), on: Bridge | None = None) -> dict:
    request = json.dumps({"v": 1, "id": ident, "cmd": cmd, "args": args})
    text, _out = (on or Bridge()).dispatch(request, buffers)
    return json.loads(text)


def _error(response: dict) -> tuple[str, str]:
    assert response["ok"] is False
    assert set(response) == {"v", "id", "ok", "error"}
    assert set(response["error"]) == {"code", "message"}
    message = response["error"]["message"]
    assert "\n" not in message and message
    return response["error"]["code"], message


class TestCatalogue:
    def test_every_command_of_the_plan_is_listed(self) -> None:
        cat = catalogue()
        assert cat["bridge_version"] == BRIDGE_VERSION == 1
        assert set(cat["commands"]) == set(EXPECTED_ARGS)
        assert cat["absent_families"] == ["chain", "register", "planner"]

    def test_every_entry_has_family_args_and_result(self) -> None:
        for name, spec in _commands().items():
            assert set(spec) == {"family", "args", "result"}, name
            assert spec["family"] == name.split(".")[0], name
            assert spec["family"] not in catalogue()["absent_families"]
            assert spec["args"]["type"] == "object", name
            assert spec["result"]["type"] == "object", name

    @pytest.mark.parametrize("name", sorted(EXPECTED_ARGS))
    def test_argument_fields(self, name: str) -> None:
        args = _commands()[name]["args"]
        assert _keys(args) == EXPECTED_ARGS[name]
        assert set(args["required"]) == EXPECTED_ARGS[name]

    def test_result_fields(self) -> None:
        commands = _commands()
        for name, spec in commands.items():
            result = spec["result"]
            if name in EXPECTED_RESULTS:
                assert _keys(result) == EXPECTED_RESULTS[name], name
            elif name.startswith("study."):
                assert _keys(result) == STUDY_RESULT, name
            else:
                assert _keys(result) == MODE_STATE, name

    def test_study_result_and_view_are_exactly_the_contract(self) -> None:
        for name, spec in _commands().items():
            if not name.startswith("study.") or name in EXPECTED_RESULTS:
                continue
            result = spec["result"]
            assert result["additionalProperties"] is False
            assert set(result["required"]) == STUDY_RESULT
            view = result["properties"]["view"]
            assert view["type"] == "object" and view["additionalProperties"] is False
            assert _keys(view) == STUDY_VIEW and set(view["required"]) == STUDY_VIEW
            assert _keys(view["properties"]["controls"]) == {
                "can_confirm",
                "can_undo",
                "can_skip_m_line",
                "can_next_myofibril",
                "shows_branch_end",
                "item_type",
            }
            assert _keys(view["properties"]["progress"]) == {"done", "total"}
            assert _keys(view["properties"]["finished"]) == {"file_name"}
            finished = result["properties"]["finished"]
            assert _keys(finished) == {"file_name", "bytes", "sha256"}
            assert finished["properties"]["bytes"] == {"type": "buffer"}
            assert set(_types(result["properties"]["autosave"])) == {"buffer", "null"}

    def test_mode_state_controls_and_items(self) -> None:
        state = _commands()["shape.click"]["result"]
        props = state["properties"]
        assert _keys(props["controls"]) == {
            "can_confirm",
            "can_undo",
            "can_skip_m_line",
            "can_delete",
        }
        assert _keys(props["items"]["items"]) == {
            "item_id",
            "type",
            "sarcomere_id",
            "marks",
            "strokes",
        }
        assert _keys(props["pending"]) == {"marks", "strokes"}
        assert props["mode"]["enum"] == ["shape", "home"]

    def test_roles(self) -> None:
        mark = _commands()["shape.click"]["result"]["properties"]["pending"]["properties"]
        assert mark["marks"]["items"]["properties"]["role"]["enum"] == [
            "A",
            "B",
            "C",
            "D",
            "E",
            "F",
            "chain",
            "myofibril_1",
            "myofibril_2",
        ]
        assert mark["strokes"]["items"]["properties"]["role"]["enum"] == [
            "z1",
            "m",
            "z2",
            "chain",
            "myofibril_1",
            "myofibril_2",
        ]

    def test_every_schema_is_well_formed(self) -> None:
        problems = []
        for name, spec in _commands().items():
            problems += schema_problems(spec["args"], f"{name}.args")
            problems += schema_problems(spec["result"], f"{name}.result")
        assert problems == []

    def test_the_well_formedness_check_finds_defects(self) -> None:
        assert schema_problems({"type": "object", "properties": {}})
        assert schema_problems({"type": "string", "pattern": "x"})
        assert schema_problems({"type": "array"})
        assert schema_problems({"type": "texture"})
        assert schema_problems({"type": "string", "minimum": 1})
        assert schema_problems({"type": "string", "enum": [1]})
        assert not schema_problems({"type": "object", "additionalProperties": {"type": "buffer"}})

    def test_catalogue_is_a_fresh_copy(self) -> None:
        first = catalogue()
        first["commands"]["doc.new"]["args"]["properties"].clear()
        assert catalogue()["commands"]["doc.new"]["args"]["properties"]

    def test_catalogue_is_json(self) -> None:
        cat = catalogue()
        assert json.loads(json.dumps(cat, allow_nan=False)) == cat


class TestStudyEngineConformance:
    """The study engine's view state and notices fit the catalogue's study result."""

    def test_every_view_state_of_a_session_is_schema_valid(self, tmp_path) -> None:
        from sarcostat.engine.study import MemorySessionStore, StudyPresenter
        from sarcostat.io.study import load_session_kit

        from .scripted_session import QuietView, annotate_open_item
        from .study_kit import FakeClock, make_kit

        spec = _commands()["study.click"]["result"]
        view_schema = spec["properties"]["view"]
        kit = make_kit(
            tmp_path,
            [("shape", "identity"), ("chain", "rot90"), ("pair", "flip_h"), ("shape", "rot180")],
        )
        presenter = StudyPresenter(
            load_session_kit(kit), MemorySessionStore(), QuietView(), clock=FakeClock()
        )
        seen: list[dict[str, Any]] = []

        def check() -> None:
            view = presenter.view_state()
            validate(view, view_schema, 0, "view")
            seen.append(view)

        check()
        presenter.add_click(1, 1)
        check()
        presenter.confirm_identity()
        check()
        for action in (presenter.undo, presenter.pause, presenter.resume, presenter.skip_m_line):
            action()
            check()
        presenter.undo()
        while not presenter.finished:
            annotate_open_item(presenter, len(presenter.records) + 1)
            check()
        assert seen[-1]["finished"] is not None
        assert {v["step"]["code"] for v in seen if v["step"]} >= {"paused", "session_complete"}
        assert any(v["notice"] for v in seen)
        result = {
            "view": seen[-1],
            "autosave": None,
            "seq": presenter.seq,
            "finished": {"file_name": "x.json", "bytes": {"$buf": 0}, "sha256": "0" * 64},
        }
        validate(result, spec, 1, "result")


class TestMockWorker:
    """Every command answered with a minimal result generated from the catalogue."""

    @pytest.mark.parametrize("name", sorted(EXPECTED_ARGS))
    def test_minimal_result_is_schema_valid(self, name: str) -> None:
        spec = _commands()[name]
        buffers: list[bytes] = []
        result = minimal_instance(spec["result"], buffers)
        validate(result, spec["result"], len(buffers), "result")
        response = {"v": 1, "id": 1, "ok": True, "result": result}
        assert json.loads(json.dumps(response, allow_nan=False)) == response

    @pytest.mark.parametrize("name", sorted(EXPECTED_ARGS))
    def test_minimal_arguments_are_schema_valid(self, name: str) -> None:
        spec = _commands()[name]
        buffers: list[bytes] = []
        validate(minimal_instance(spec["args"], buffers), spec["args"], len(buffers), "args")

    def test_a_result_with_a_missing_key_is_not_valid(self) -> None:
        spec = _commands()["study.click"]
        buffers: list[bytes] = []
        result = minimal_instance(spec["result"], buffers)
        del result["view"]["paused"]
        with pytest.raises(SchemaError, match="paused"):
            validate(result, spec["result"], len(buffers), "result")

    def test_a_study_view_with_an_extra_key_is_not_valid(self) -> None:
        spec = _commands()["study.click"]
        buffers: list[bytes] = []
        result = minimal_instance(spec["result"], buffers)
        result["view"]["image"] = None
        with pytest.raises(SchemaError, match="unexpected key 'image'"):
            validate(result, spec["result"], len(buffers), "result")


class TestBadRequests:
    @pytest.mark.parametrize(
        ("cmd", "args", "fragment"),
        [
            ("shape.click", {"x": 1.0, "y": "2"}, "args.y: expected number, got string"),
            ("shape.click", {"x": 1.0}, "missing key 'y'"),
            ("shape.click", {"x": 1.0, "y": 2.0, "z": 3.0}, "unexpected key 'z'"),
            ("shape.click", {"x": True, "y": 2.0}, "args.x: expected number, got boolean"),
            ("shape.delete", {"item_id": None}, "args.item_id"),
            ("doc.export", {"what": "pdf"}, "must be one of v1, results_json"),
            ("doc.new", {"image": {"file_name": "a.png", "sha256": "0" * 64}}, "width_px"),
            (
                "doc.new",
                {"image": {"file_name": "a", "sha256": "", "width_px": 0, "height_px": 1}},
                "must be >= 1",
            ),
            (
                "doc.new",
                {"image": {"file_name": "a", "sha256": "", "width_px": 1.5, "height_px": 1}},
                "expected integer, got number",
            ),
            ("study.click", {"x": 1, "y": 2, "t": {"mono_s": -1, "wall_ms": 0}}, "mono_s"),
            ("study.click", {"x": 1, "y": 2, "t": {"mono_s": 0}}, "missing key 'wall_ms'"),
            ("study.start", {"autosave": None, "seq": "1", "t": T}, "args.seq"),
            ("doc.set_click_error", {"sigma": [1]}, "expected number or null, got array"),
            ("sys.identity", {"extra": 1}, "unexpected key 'extra'"),
        ],
    )
    def test_malformed_arguments(self, cmd: str, args: Any, fragment: str) -> None:
        response = call(cmd, args)
        code, message = _error(response)
        assert code == "bad_request"
        assert fragment in message
        assert response["id"] == 7

    @pytest.mark.parametrize("args", [[], "x", 1, None, True])
    def test_non_object_args(self, args: Any) -> None:
        code, message = _error(call("shape.undo", args))
        assert code == "bad_request" and "args must be a JSON object" in message

    def test_unknown_command(self) -> None:
        response = call("shape.paint", {}, ident="abc")
        assert _error(response)[0] == "bad_request"
        assert "unknown command" in response["error"]["message"]
        assert response["id"] == "abc"

    @pytest.mark.parametrize("cmd", [1, None, ["sys.identity"]])
    def test_command_not_a_string(self, cmd: Any) -> None:
        response = call(cmd, {})
        assert _error(response)[0] == "bad_request" and response["id"] == 7

    @pytest.mark.parametrize("version", [2, 0, "1", 1.0, True, None])
    def test_wrong_version(self, version: Any) -> None:
        request = json.dumps({"v": version, "id": 3, "cmd": "sys.identity", "args": {}})
        response = json.loads(dispatch(request)[0])
        code, message = _error(response)
        assert code == "bad_request" and "version" in message
        assert response["id"] == 3 and response["v"] == 1

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "{",
            "not json",
            '{"v": 1, "id": 1, "cmd": "sys.identity", "args": {}, "args": {}}',
            '{"v": 1, "id": 1, "cmd": "shape.click", "args": {"x": NaN, "y": 1}}',
            '{"v": 1, "id": 1, "cmd": "shape.click", "args": {"x": Infinity, "y": 1}}',
            '{"v": 1, "id": 1, "cmd": "shape.click", "args": {"x": 1e400, "y": 1}}',
            "[" * 100000,
        ],
    )
    def test_bad_json(self, text: str) -> None:
        response = json.loads(dispatch(text)[0])
        code, message = _error(response)
        assert code == "bad_request" and "not valid JSON" in message
        assert response["id"] is None

    @pytest.mark.parametrize(
        ("text", "depth"),
        [
            ("", 0),
            ("1", 0),
            ("{}", 1),
            ('[[1], {"a": [2]}]', 3),
            ('{"a": "[[[[{{", "b": ["}]\\"[{"]}', 2),
            ('"\\\\"', 0),
            ("[" * 70, 70),
        ],
    )
    def test_json_depth(self, text: str, depth: int) -> None:
        assert json_depth(text) == depth

    def test_deep_nesting_is_refused_before_parsing(self) -> None:
        def nested(levels: int) -> str:
            value = "[" * levels + "]" * levels
            return json.dumps({"v": 1, "id": 5, "cmd": "sys.identity", "args": {}})[:-1] + (
                f', "x": {value}}}'
            )

        assert json_depth(nested(MAX_JSON_DEPTH - 1)) == MAX_JSON_DEPTH
        allowed = json.loads(dispatch(nested(MAX_JSON_DEPTH - 1))[0])
        assert "unexpected keys x" in _error(allowed)[1] and allowed["id"] == 5
        refused = json.loads(dispatch(nested(MAX_JSON_DEPTH))[0])
        code, message = _error(refused)
        assert code == "bad_request" and f"more than {MAX_JSON_DEPTH}" in message
        assert refused["id"] is None

    def test_brackets_inside_strings_do_not_count(self) -> None:
        text = "[" * 10000
        request = json.dumps({"v": 1, "id": 6, "cmd": "sys.identity", "args": {}, "x": text})
        response = json.loads(dispatch(request)[0])
        assert "unexpected keys x" in _error(response)[1]

    @pytest.mark.parametrize("request_value", [b"{}", None, 12, ["x"]])
    def test_request_not_text(self, request_value: Any) -> None:
        response = json.loads(dispatch(request_value)[0])
        assert _error(response)[0] == "bad_request" and response["id"] is None

    @pytest.mark.parametrize(
        ("request_value", "echoed"),
        [
            ([1, 2], None),
            ({"v": 1, "id": 4, "cmd": "sys.identity"}, 4),
            ({"v": 1, "id": 4, "cmd": "sys.identity", "args": {}, "x": 1}, 4),
            ({"v": 1, "cmd": "sys.identity", "args": {}}, None),
            ({"v": 1, "id": 1.5, "cmd": "sys.identity", "args": {}}, None),
            ({"v": 1, "id": True, "cmd": "sys.identity", "args": {}}, None),
            ({"v": 1, "id": None, "cmd": "sys.identity", "args": {}}, None),
        ],
    )
    def test_envelope(self, request_value: Any, echoed: Any) -> None:
        response = json.loads(dispatch(json.dumps(request_value))[0])
        assert _error(response)[0] == "bad_request"
        assert response["id"] == echoed

    @pytest.mark.parametrize(
        ("ref", "n_buffers"),
        [({"$buf": 1}, 1), ({"$buf": -1}, 1), ({"$buf": "0"}, 1), ({"$buf": 0}, 0)],
    )
    def test_bad_buffer_index(self, ref: Any, n_buffers: int) -> None:
        args = {"manifest": ref, "manifest_sha256": {"$buf": 0}}
        response = call("study.open_kit", args, buffers=[b"x"] * n_buffers)
        code, message = _error(response)
        assert code == "bad_request" and "$buf" in message

    def test_buffer_where_none_is_expected(self) -> None:
        code, _message = _error(call("shape.delete", {"item_id": {"$buf": 0}}, buffers=[b"x"]))
        assert code == "bad_request"

    def test_buffers_must_be_bytes(self) -> None:
        args = {"manifest": {"$buf": 0}, "manifest_sha256": {"$buf": 1}}
        code, _message = _error(call("study.open_kit", args, buffers=[b"x", "text"]))
        assert code == "bad_request"


class TestRefusals:
    @pytest.mark.parametrize("cmd", ["chain.click", "register.next", "planner.compute"])
    def test_absent_families(self, cmd: str) -> None:
        code, message = _error(call(cmd, {}))
        assert code == "refused" and "not in this build" in message

    @pytest.mark.parametrize("name", sorted(n for n in EXPECTED_ARGS if not n.startswith("sys.")))
    def test_commands_without_a_handler_are_refused(self, name: str) -> None:
        spec = _commands()[name]
        buffers: list[bytes] = []
        args = minimal_instance(spec["args"], buffers)
        only_sys = Bridge(families={"sys": bridge.FAMILY_LOADERS["sys"]})
        response = call(name, args, buffers=buffers, on=only_sys)
        code, message = _error(response)
        assert code == "refused" and "not implemented yet" in message
        assert response["id"] == 7

    def test_every_family_has_a_loader(self) -> None:
        families = {spec["family"] for spec in _commands().values()}
        assert families == set(bridge.FAMILY_LOADERS)
        for family, loader in bridge.FAMILY_LOADERS.items():
            handled = set(loader())
            assert {n for n, s in _commands().items() if s["family"] == family} <= handled


class TestSys:
    def test_identity(self) -> None:
        from sarcostat.io.provenance import source_digest

        response = call("sys.identity", {}, ident="boot")
        assert response == {
            "v": 1,
            "id": "boot",
            "ok": True,
            "result": {
                "sarcostat_version": __version__,
                "source_digest": source_digest(),
                "bridge_version": BRIDGE_VERSION,
            },
        }

    def test_load_scipy(self) -> None:
        pytest.importorskip("scipy")
        response = call("sys.load_scipy", {})
        assert response["ok"] is True and response["result"]["scipy_version"]

    def test_response_text_is_compact_ascii(self) -> None:
        text, out = dispatch(json.dumps({"v": 1, "id": 1, "cmd": "sys.identity", "args": {}}))
        assert out == []
        assert text.isascii() and ", " not in text and ": " not in text
        assert text == json.dumps(json.loads(text), separators=(",", ":"), sort_keys=True)


def _probe_bridge(result: Any = None, error: BaseException | None = None) -> Bridge:
    commands = {
        "probe.run": {
            "family": "probe",
            "args": {
                "type": "object",
                "properties": {"data": {"type": ["buffer", "null"]}},
                "required": ["data"],
                "additionalProperties": False,
            },
            "result": {
                "type": "object",
                "properties": {
                    "value": {"type": ["number", "null"]},
                    "blob": {"type": ["buffer", "null"]},
                    "seen": {"type": "string"},
                },
                "required": ["value", "blob", "seen"],
                "additionalProperties": False,
            },
        }
    }

    def run(_bridge: Bridge, args: dict[str, Any]) -> dict[str, Any]:
        if error is not None:
            raise error
        data = args["data"]
        seen = "none" if data is None else f"{type(data).__name__}:{bytes(data).decode()}"
        out = {"value": 1.0, "blob": None, "seen": seen}
        if result is not None:
            out.update(result)
        return out

    return Bridge(families={"probe": lambda: {"probe.run": run}}, commands=commands)


class TestDispatchMechanics:
    def test_buffers_in_and_out(self) -> None:
        probe = _probe_bridge(result={"blob": bytearray(b"out")})
        request = json.dumps({"v": 1, "id": 9, "cmd": "probe.run", "args": {"data": {"$buf": 1}}})
        text, out = probe.dispatch(request, [b"zero", memoryview(b"one")])
        response = json.loads(text)
        assert response["result"] == {"value": 1.0, "blob": {"$buf": 0}, "seen": "bytes:one"}
        assert out == [b"out"] and isinstance(out[0], bytes)

    @pytest.mark.parametrize("bad", [math.nan, math.inf])
    def test_non_finite_result_is_internal(self, bad: float) -> None:
        response = call("probe.run", {"data": None}, on=_probe_bridge(result={"value": bad}))
        code, message = _error(response)
        assert code == "internal" and "finite" in message
        assert response["id"] == 7

    def test_result_off_schema_is_internal(self) -> None:
        response = call("probe.run", {"data": None}, on=_probe_bridge(result={"value": "1"}))
        code, message = _error(response)
        assert code == "internal" and "result.value" in message

    @pytest.mark.parametrize(
        ("error", "code"),
        [
            (Refused("Load an image first."), "refused"),
            (NeedsConfirmation("The image differs.\nLoad anyway?"), "needs_confirmation"),
            (RuntimeError("boom\nsecond line"), "internal"),
            (KeyError("k"), "internal"),
            (RecursionError("deep"), "internal"),
            (KeyboardInterrupt(), "internal"),
        ],
    )
    def test_handler_errors(self, error: BaseException, code: str) -> None:
        response = call("probe.run", {"data": None}, ident=11, on=_probe_bridge(error=error))
        assert _error(response)[0] == code
        assert response["id"] == 11

    def test_loader_runs_once_per_family(self) -> None:
        calls: list[int] = []
        commands = {
            "x.a": {
                "family": "x",
                "args": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
                "result": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            }
        }

        def loader():
            calls.append(1)
            return {"x.a": lambda _b, _a: {}}

        probe = Bridge(families={"x": loader}, commands=commands)
        for _ in range(3):
            assert call("x.a", {}, on=probe)["ok"] is True
        assert calls == [1]

    def test_state_lives_between_commands(self) -> None:
        commands = {
            "x.count": {
                "family": "x",
                "args": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
                "result": {
                    "type": "object",
                    "properties": {"n": {"type": "integer"}},
                    "required": ["n"],
                    "additionalProperties": False,
                },
            }
        }

        def count(b: Bridge, _args: dict[str, Any]) -> dict[str, Any]:
            b.state["n"] = b.state.get("n", 0) + 1
            return {"n": b.state["n"]}

        probe = Bridge(families={"x": lambda: {"x.count": count}}, commands=commands)
        assert [call("x.count", {}, on=probe)["result"]["n"] for _ in range(3)] == [1, 2, 3]

    @pytest.mark.parametrize(
        "junk",
        [
            "\x00",
            "{}",
            '{"v":1,"id":1,"cmd":"doc.load_v1","args":{"text":"x","image":null,"confirm":"no"}}',
            '{"v":1,"id":1,"cmd":"study.verify_items","args":{"files":{"a":{"$buf":0}}}}',
            '{"v":1,"id":1,"cmd":"study.verify_items","args":{"files":{"a":1}}}',
            '"\\ud800"',
            "1" * 5000,
        ],
    )
    def test_never_raises(self, junk: str) -> None:
        text, out = dispatch(junk)
        response = json.loads(text)
        assert response["ok"] is False and out == []
        assert response["error"]["code"] in bridge.ERROR_CODES


@pytest.mark.needs_cpython(reason="runs a subprocess; Emscripten has no processes")
class TestProcess:
    def test_import_and_sys_commands_load_no_metric_code(self) -> None:
        code = (
            "import json, sys\n"
            "import sarcostat.engine.bridge as b\n"
            "metric = ('sarcostat.core', 'sarcostat.analysis', 'sarcostat.export', 'scipy')\n"
            "before = sorted(m for m in sys.modules if m.startswith((*metric, 'numpy')))\n"
            "b.dispatch(json.dumps({'v': 1, 'id': 1, 'cmd': 'sys.identity', 'args': {}}))\n"
            "middle = sorted(m for m in sys.modules if m.startswith((*metric, 'numpy')))\n"
            "b.dispatch(json.dumps({'v': 1, 'id': 2, 'cmd': 'study.undo', 'args': {'t': {'mono_s': 0, 'wall_ms': 0}}}))\n"
            "after = sorted(m for m in sys.modules if m.startswith(metric))\n"
            "print(json.dumps([before, middle, after]))\n"
        )
        done = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True
        )
        assert json.loads(done.stdout) == [[], [], []]

    def test_catalogue_command_line(self) -> None:
        done = subprocess.run(
            [sys.executable, "-m", "sarcostat.engine.bridge", "--catalogue"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, done.stderr
        assert json.loads(done.stdout) == catalogue()
        assert done.stdout == bridge.catalogue_json()
        assert done.stdout == json.dumps(catalogue(), sort_keys=True, indent=2) + "\n"

    def test_command_line_without_an_option(self) -> None:
        done = subprocess.run(
            [sys.executable, "-m", "sarcostat.engine.bridge"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 2 and "usage" in done.stderr
