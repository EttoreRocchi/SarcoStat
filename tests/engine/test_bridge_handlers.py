"""The bridge's handlers (M2-4): every command through ``dispatch``, JSON in and out."""

from __future__ import annotations

import copy
import hashlib
import json
import random
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from sarcostat.analysis import analyze_document
from sarcostat.engine import bridge
from sarcostat.engine.annotate import AnnotationSession
from sarcostat.engine.bridge import (
    ERROR_CODES,
    Bridge,
    NeedsConfirmation,
    Refused,
    catalogue,
    validate,
)
from sarcostat.engine.document import parse_document
from sarcostat.engine.reports import results_view
from sarcostat.export import provenance_sidecar, results_csv, results_json

from . import demo_kit, scripted_session
from .bridge_driver import Client, Times, finished_bytes, kit_files, open_and_verify, run_study
from .test_annotate import FIXED_UTC, IMAGE, SHA_B, SQUARE_POINTS, shifted
from .test_document import mixed_document, mixed_text

COMMANDS = catalogue()["commands"]
ROOT = Path(__file__).resolve().parents[2]
REFERENCE = Path(__file__).resolve().parent / "data" / "demo_DEMO_S1.tk.json"
TIMING = ("started_utc", "finished_utc", "active_seconds")


def check_log(log: list[tuple[str, dict[str, Any], int]]) -> None:
    """Every logged response has a valid envelope; every result matches its schema."""
    for cmd, response, n_out in log:
        assert response["v"] == 1
        if response["ok"]:
            assert set(response) == {"v", "id", "ok", "result"}
            validate(response["result"], COMMANDS[cmd]["result"], n_out, f"{cmd}.result")
        else:
            assert set(response) == {"v", "id", "ok", "error"}
            assert response["error"]["code"] in ERROR_CODES
            message = response["error"]["message"]
            assert message and "\n" not in message


def annotation_client() -> Client:
    client = Client()
    client.bridge.state["annotation"] = AnnotationSession(utc_now=lambda: FIXED_UTC)
    return client


@pytest.fixture(scope="module")
def demo_files(tmp_path_factory) -> dict[str, bytes]:
    return kit_files(demo_kit.make_demo_kit(tmp_path_factory.mktemp("demo") / "kit"))


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory) -> Path:
    return demo_kit.make_demo_kit(tmp_path_factory.mktemp("demo_dir") / "kit")


def _shape_clicks(client: Client, points: list[tuple[float, float]]) -> None:
    for x, y in points:
        client.ok("shape.click", {"x": x, "y": y})


class TestEveryCommand:
    """Acceptance M2-4 #1: each catalogue command answers through ``dispatch``."""

    def test_every_command_answers(self, demo_files: dict[str, bytes]) -> None:
        pytest.importorskip("scipy")
        client = annotation_client()
        client.ok("sys.identity", {})
        client.ok("sys.load_scipy", {})
        client.ok("doc.new", {"image": IMAGE})
        _shape_clicks(client, SQUARE_POINTS[:2])
        client.ok("shape.undo", {})
        client.ok("shape.skip_m_line", {})
        _shape_clicks(client, [(10, 30), (30, 10), (30, 30)])
        client.ok("shape.confirm", {})
        for dx in (40.0, 80.0, 120.0):
            _shape_clicks(client, shifted(SQUARE_POINTS, dx))
            client.ok("shape.confirm", {})
        client.ok("doc.set_scale", {"pixel_size": 0.5, "unit": "um"})
        client.ok("doc.set_click_error", {"sigma": 0.25})
        client.ok("doc.estimate_click_error", {})
        client.ok("doc.set_reference_direction", {"deg": 12.0})
        client.ok("results.get", {})
        for what in ("v1", "results_json", "results_csv", "provenance"):
            client.ok("doc.export", {"what": what})
        client.ok("shape.delete", {"item_id": "sarcomere-2"})
        client.ok("shape.clear", {})
        client.ok("doc.load_v1", {"text": mixed_text(), "image": None, "confirm": False})

        run_study(client, demo_files, Times())
        second = Client()
        open_and_verify(second, demo_files)
        times = Times()
        second.ok("study.start", {"autosave": None, "seq": None, "t": times.now()})
        second.ok("study.confirm_identity", {"t": times.tick()})
        second.ok("study.pause", {"t": times.tick()})
        paused, _ = second.ok("study.resume", {"t": times.tick()})
        assert paused["view"]["paused"] is False

        log = client.log + second.log
        check_log(log)
        answered = {cmd for cmd, response, _n in log if response["ok"]}
        assert answered == set(COMMANDS)


SHAPE_SCRIPT: list[tuple[str, dict[str, Any]]] = [
    ("shape.click", {"x": 1.0, "y": 1.0}),
    ("doc.new", {"image": IMAGE}),
    ("doc.set_scale", {"pixel_size": 0.065, "unit": "um"}),
    *[("shape.click", {"x": x, "y": y}) for x, y in SQUARE_POINTS],
    ("shape.click", {"x": 1.0, "y": 1.0}),
    ("shape.confirm", {}),
    *[("shape.click", {"x": x, "y": y}) for x, y in shifted(SQUARE_POINTS, 40, 2)[:2]],
    ("shape.skip_m_line", {}),
    *[("shape.click", {"x": x, "y": y}) for x, y in shifted(SQUARE_POINTS, 40, 2)[4:]],
    ("shape.confirm", {}),
    ("shape.click", {"x": 400.0, "y": 1.0}),
    *[("shape.click", {"x": x, "y": y}) for x, y in shifted(SQUARE_POINTS, 80.5, 3.25)],
    ("shape.confirm", {}),
    ("shape.undo", {}),
    *[("shape.click", {"x": x, "y": y}) for x, y in shifted(SQUARE_POINTS, 81, 3)],
    ("shape.confirm", {}),
    ("doc.set_click_error", {"sigma": -1.0}),
    ("doc.set_click_error", {"sigma": 0.02}),
    ("doc.estimate_click_error", {}),
    ("doc.set_reference_direction", {"deg": 30.0}),
    ("shape.delete", {"item_id": "sarcomere-1"}),
    ("shape.delete", {"item_id": "nope"}),
    ("results.get", {}),
    ("doc.export", {"what": "v1"}),
    ("doc.export", {"what": "results_json"}),
    ("doc.load_v1", {"text": mixed_text(), "image": dict(IMAGE, sha256=SHA_B), "confirm": False}),
    ("doc.load_v1", {"text": mixed_text(), "image": dict(IMAGE, sha256=SHA_B), "confirm": True}),
    ("shape.clear", {}),
    ("doc.export", {"what": "v1"}),
]


def _direct(session: AnnotationSession, cmd: str, args: dict[str, Any]) -> Any:
    if cmd == "results.get":
        image = session.analysis()
        if image is None:
            raise Refused("Open an image first.")
        return results_view(image)
    if cmd == "doc.export" and args["what"] != "v1":
        results = session.results()
        assert results is not None
        return {"text": results_json(results) if args["what"] == "results_json" else None}
    method = getattr(session, cmd.replace(".", "_"))
    return method(*args.values())


class TestScriptedShapeSession:
    """Acceptance M2-4 #2: the bridge gives the direct engine run's states and files."""

    def test_bridge_equals_direct_run(self) -> None:
        client = annotation_client()
        direct = AnnotationSession(utc_now=lambda: FIXED_UTC)
        for cmd, args in SHAPE_SCRIPT:
            response, _out = client.call(cmd, args)
            try:
                expected = _direct(direct, cmd, copy.deepcopy(args))
            except (Refused, NeedsConfirmation) as exc:
                assert not response["ok"], cmd
                assert response["error"] == {"code": exc.code, "message": exc.message}
                continue
            assert response["ok"], (cmd, response)
            result = response["result"]
            if cmd == "doc.export":
                assert result["text"] == expected["text"]
            else:
                assert result == json.loads(json.dumps(expected)), cmd
        check_log(client.log)
        codes = [r["error"]["code"] for _c, r, _n in client.log if not r["ok"]]
        assert codes == ["refused", "needs_confirmation"]

    def test_results_get_on_every_item_type(self) -> None:
        client = annotation_client()
        client.ok("doc.load_v1", {"text": mixed_text(), "image": IMAGE, "confirm": False})
        report, _ = client.ok("results.get", {})
        session = client.bridge.state["annotation"]
        assert report == json.loads(json.dumps(results_view(session.analysis())))
        assert report["plot_data"]["chains"] and report["plot_data"]["register"]
        assert report["plot_data"]["shape"] and set(report["tables"]) >= {"sarcomeres", "chains"}
        check_log(client.log)

    def test_settings_in_the_state(self) -> None:
        client = annotation_client()
        state, _ = client.ok(
            "doc.load_v1", {"text": mixed_text(), "image": IMAGE, "confirm": False}
        )
        assert state["settings"] == {
            "pixel_size": 2.5,
            "unit": "nm",
            "reference_direction_deg": 15.0,
            "click_sigma_z": 1.5,
            "click_sigma_m": None,
            "click_perp_ratio": 1.0,
        }

    def test_exports_and_their_names(self) -> None:
        client = annotation_client()
        client.ok("doc.load_v1", {"text": mixed_text(), "image": IMAGE, "confirm": False})
        session = client.bridge.state["annotation"]
        results = session.results()
        exported = {
            what: client.ok("doc.export", {"what": what})[0]
            for what in ("v1", "results_json", "results_csv", "provenance")
        }
        assert {w: e["file_name"] for w, e in exported.items()} == {
            "v1": "micrograph_annotations.json",
            "results_json": "micrograph_results.json",
            "results_csv": "micrograph_results.csv",
            "provenance": "micrograph_provenance.json",
        }
        assert exported["results_json"]["text"] == results_json(results)
        assert exported["results_csv"]["text"] == results_csv(results)
        sidecar = json.loads(exported["provenance"]["text"])
        assert sidecar["outputs"] == ["micrograph_results.json", "micrograph_results.csv"]
        assert sidecar["provenance"]["files"] == [
            {"file_name": IMAGE["file_name"], "sha256": IMAGE["sha256"]}
        ]
        assert exported["provenance"]["text"] == provenance_sidecar(results, sidecar["outputs"], [])

    def test_annotation_refusals(self) -> None:
        client = annotation_client()
        for cmd, args in [
            ("shape.click", {"x": 1.0, "y": 1.0}),
            ("results.get", {}),
            ("doc.export", {"what": "results_json"}),
            ("doc.export", {"what": "provenance"}),
        ]:
            response, _ = client.call(cmd, args)
            assert response["error"] == {"code": "refused", "message": "Open an image first."}
        doc = mixed_document()
        doc["images"].append(dict(doc["images"][0], image_id="b", file_name="b.png"))
        response, _ = client.call(
            "doc.load_v1", {"text": json.dumps(doc), "image": None, "confirm": False}
        )
        assert response["error"]["code"] == "refused"
        assert "sarcostat analyze" in response["error"]["message"]
        check_log(client.log)


def _without_timing(doc: dict[str, Any]) -> dict[str, Any]:
    doc = copy.deepcopy(doc)
    for key in ("source_digest", "sarcostat_version", "created_utc"):
        doc.pop(key)
    for item in doc["items"]:
        for key in TIMING:
            item.pop(key)
        for click in item["raw_clicks"]:
            click.pop("seconds")
    return doc


class TestStudyThroughTheBridge:
    """Acceptance M2-4 #3: the bridge session gives the file-store session's bytes."""

    def test_memory_store_gives_the_file_store_bytes(self, demo_dir: Path, tmp_path: Path) -> None:
        results = run_study(Client(), kit_files(demo_dir), Times())
        name, data = finished_bytes(results)
        times = Times()
        presenter = scripted_session.run_scripted_session(
            demo_dir, tmp_path / "out", clock=times.clock, tick=times.tick
        )
        assert presenter.output_path is not None
        assert name == presenter.output_path.name == "demo_DEMO_S1.json"
        assert data == presenter.output_path.read_bytes()
        finished = results[-1][0]["finished"]
        assert finished["sha256"] == hashlib.sha256(data).hexdigest()
        assert results[-1][0]["view"]["finished"] == {"file_name": name}

    def test_same_annotations_as_the_desktop_reference(self, demo_files: dict[str, bytes]) -> None:
        """The reference was written with a clock that ticks on every reading, so only timing differs."""
        _name, data = finished_bytes(run_study(Client(), demo_files, Times()))
        produced = json.loads(data)
        reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
        assert _without_timing(produced) == _without_timing(reference)

    def test_seq_grows_by_one_per_saved_action(self, demo_files: dict[str, bytes]) -> None:
        results = run_study(Client(), demo_files, Times())
        assert results[0][0]["seq"] == 0 and results[0][0]["autosave"] is None
        for (before, _), (after, out) in zip(results, results[1:], strict=False):
            step = after["seq"] - before["seq"]
            assert step == 1
            if after["finished"] is None:
                saved = json.loads(out[after["autosave"]["$buf"]])
                assert saved["seq"] == after["seq"]
            else:
                assert after["autosave"] is None
        assert results[-1][0]["seq"] == len(results) - 1

    def test_refused_actions_are_ordinary_results(self, demo_files: dict[str, bytes]) -> None:
        client = Client()
        open_and_verify(client, demo_files)
        times = Times()
        client.ok("study.start", {"autosave": None, "seq": None, "t": times.now()})
        result, out = client.ok("study.click", {"x": 1.0, "y": 1.0, "t": times.tick()})
        assert result["view"]["notice"]["code"] == "identity_not_confirmed"
        assert result["autosave"] is None and result["seq"] == 0 and out == []
        client.ok("study.confirm_identity", {"t": times.tick()})
        result, _ = client.ok("study.skip_item", {"reason": "  ", "t": times.tick()})
        assert result["view"]["notice"]["code"] == "skip_needs_reason"
        assert result["autosave"] is None and result["seq"] == 1
        check_log(client.log)

    def test_resume_from_the_bridge_autosave(self, demo_files: dict[str, bytes]) -> None:
        times = Times()
        partial = run_study(Client(), demo_files, times, stop_after=3)
        last, out = next((r, o) for r, o in reversed(partial) if r["autosave"] is not None)
        autosave = out[last["autosave"]["$buf"]]
        resumed = run_study(Client(), demo_files, times, autosave=autosave, seq=last["seq"])
        assert resumed[0][0]["seq"] == last["seq"]
        assert resumed[0][0]["view"]["step"]["code"] == "identity_resumed"
        _name, data = finished_bytes(resumed)
        _name, straight = finished_bytes(run_study(Client(), demo_files, Times()))
        assert _without_timing(json.loads(data)) == _without_timing(json.loads(straight))

    def test_kit_problems_and_refusals(self, demo_files: dict[str, bytes]) -> None:
        client = Client()
        t = {"mono_s": 1.0, "wall_ms": 1_795_085_291_000.0}
        response, _ = client.call("study.start", {"autosave": None, "seq": None, "t": t})
        assert response["error"] == {"code": "refused", "message": "Open a session kit first."}
        response, _ = client.call(
            "study.open_kit",
            {"manifest": {"$buf": 0}, "manifest_sha256": {"$buf": 1}},
            [demo_files["manifest.json"], b"0" * 64],
        )
        assert response["error"]["code"] == "refused"
        summary = open_and_verify(client, demo_files)
        assert summary["n_items"] == 6 and summary["annotator_id"] == "DEMO"
        listed = summary["files"]
        sent = {path: demo_files[path] for path in listed}
        sent[listed[0]] = b"not the image"
        del sent[listed[1]]
        sent["items/extra.png"] = b"x"
        buffers = list(sent.values())
        files = {path: {"$buf": k} for k, path in enumerate(sent)}
        checked, _ = client.ok("study.verify_items", {"files": files}, buffers)
        assert checked["ok"] is False
        assert {(p["code"], p["params"]["file"]) for p in checked["problems"]} == {
            ("file_not_listed", "items/extra.png"),
            ("item_sha256_mismatch", listed[0]),
            ("item_missing", listed[1]),
        }
        response, _ = client.call("study.start", {"autosave": None, "seq": None, "t": t})
        assert response["error"]["code"] == "refused" and "Verify" in response["error"]["message"]
        response, _ = client.call("study.undo", {"t": t})
        assert response["error"] == {"code": "refused", "message": "Start the session first."}
        open_and_verify(client, demo_files)
        bad_t = {"mono_s": 1.0, "wall_ms": 1e30}
        response, _ = client.call("study.start", {"autosave": None, "seq": None, "t": bad_t})
        assert response["error"]["code"] == "bad_request"
        response, _ = client.call(
            "study.start", {"autosave": {"$buf": 0}, "seq": None, "t": t}, [b"{}"]
        )
        assert response["error"]["code"] == "refused" and "seq" in response["error"]["message"]
        client.ok("study.start", {"autosave": None, "seq": None, "t": t})
        response, _ = client.call("study.start", {"autosave": None, "seq": None, "t": t})
        assert response["error"]["code"] == "refused"
        check_log(client.log)

    def test_an_invalid_item_image_is_a_problem(self, tmp_path: Path) -> None:
        from .study_kit import make_kit, png_bytes

        def rgb(image: Any) -> bytes:
            data = bytearray(png_bytes(image))
            data[25] = 2
            return bytes(data)

        kit = make_kit(tmp_path, [("shape", "identity")], decoys=False, encode=rgb)
        files = kit_files(kit)
        client = Client()
        summary, _ = client.ok(
            "study.open_kit",
            {"manifest": {"$buf": 0}, "manifest_sha256": {"$buf": 1}},
            [files["manifest.json"], files["manifest.sha256"]],
        )
        path = summary["files"][0]
        checked, _ = client.ok("study.verify_items", {"files": {path: {"$buf": 0}}}, [files[path]])
        (problem,) = checked["problems"]
        assert problem["code"] == "item_image_invalid" and problem["params"]["file"] == path
        assert problem["params"]["detail"]


def _random_value(
    schema: dict[str, Any], rng: random.Random, n_buffers: int, depth: int = 0
) -> Any:
    kinds = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
    kind = rng.choice(kinds)
    if "enum" in schema and rng.random() < 0.8:
        return rng.choice(schema["enum"])
    if kind == "null":
        return None
    if kind == "buffer":
        return {"$buf": rng.randrange(max(n_buffers, 1) + 1)}
    if kind == "boolean":
        return rng.random() < 0.5
    if kind == "integer":
        return rng.choice([0, 1, 2, 3, 7, 42, -1, 10**6])
    if kind == "number":
        return rng.choice([0.0, 0.5, 1.0, 10.0, 25.0, 150.5, 299.5, -0.5, -3.0, 1e6, 1e-9])
    if kind == "string":
        return rng.choice(["", "x", "sarcomere-1", "v1", "um", "items/001.png", mixed_text()[:200]])
    if kind == "array":
        return [
            _random_value(schema["items"], rng, n_buffers, depth + 1)
            for _ in range(rng.randrange(3) if depth < 4 else 0)
        ]
    properties = schema.get("properties")
    if properties is None:
        return {
            f"k{k}": _random_value(schema["additionalProperties"], rng, n_buffers, depth + 1)
            for k in range(rng.randrange(3))
        }
    return {key: _random_value(sub, rng, n_buffers, depth + 1) for key, sub in properties.items()}


def _mutate(args: dict[str, Any], rng: random.Random) -> Any:
    roll = rng.random()
    if roll < 0.15 and args:
        args.pop(rng.choice(sorted(args)))
    elif roll < 0.3:
        args[rng.choice(["extra", "x", "t"])] = rng.choice([1, "a", None, [], {}])
    elif roll < 0.45 and args:
        args[rng.choice(sorted(args))] = rng.choice([True, "s", 3, [1], {"$buf": 99}, None, 1.5])
    elif roll < 0.5:
        return rng.choice([[], "args", 7, None])
    return args


class TestFuzz:
    """Acceptance M2-4 #4: 1000 seeded random commands, each answered with a valid envelope."""

    def test_random_commands(self, demo_files: dict[str, bytes]) -> None:
        rng = random.Random(20261120)
        client = annotation_client()
        client.ok("doc.load_v1", {"text": mixed_text(), "image": IMAGE, "confirm": False})
        open_and_verify(client, demo_files)
        times = Times()
        client.ok("study.start", {"autosave": None, "seq": None, "t": times.now()})
        names = sorted(COMMANDS)
        real_buffers = [demo_files["manifest.json"], demo_files["manifest.sha256"]]
        internal = []
        for k in range(1000):
            cmd = rng.choice(names) if rng.random() < 0.95 else rng.choice(["chain.click", "nope"])
            buffers = [
                rng.choice([b"", rng.randbytes(rng.randrange(1, 40)), *real_buffers])
                for _ in range(rng.randrange(4))
            ]
            schema = (
                COMMANDS[cmd]["args"] if cmd in COMMANDS else {"type": "object", "properties": {}}
            )
            args = _random_value(schema, rng, len(buffers))
            if isinstance(args, dict) and "t" in args and rng.random() < 0.7:
                args["t"] = times.tick()
            if rng.random() < 0.4:
                args = _mutate(args, rng)
            request = json.dumps({"v": 1, "id": k, "cmd": cmd, "args": args})
            if rng.random() < 0.03:
                request = request[: rng.randrange(len(request))]
            text, out = client.bridge.dispatch(request, buffers)
            response = json.loads(text)
            assert response["v"] == 1
            assert response["id"] in (k, None)
            if response["ok"]:
                validate(response["result"], COMMANDS[cmd]["result"], len(out), "result")
            else:
                error = response["error"]
                assert error["code"] in ERROR_CODES and "\n" not in error["message"]
                if error["code"] == "internal":
                    internal.append((cmd, args, error["message"]))
        assert internal == []


@pytest.mark.needs_cpython(reason="runs a subprocess; Emscripten has no processes")
class TestStudyLoadsNoMetricCode:
    """Acceptance M2-4 #5: a whole study session through the bridge imports no metric code."""

    def test_full_session_in_a_fresh_process(self, demo_dir: Path) -> None:
        code = (
            "import json, sys\n"
            f"sys.path.insert(0, {str(ROOT)!r})\n"
            "from tests.engine.bridge_driver import Client, Times, finished_bytes, kit_files, run_study\n"
            f"results = run_study(Client(), kit_files({str(demo_dir)!r}), Times())\n"
            "name, data = finished_bytes(results)\n"
            "banned = ('sarcostat.core', 'sarcostat.analysis', 'sarcostat.export', 'sarcostat.engine.reports', 'sarcostat.engine.annotate')\n"
            "loaded = sorted(m for m in sys.modules if m.split('.')[0] == 'scipy' or m.startswith(banned))\n"
            "print(json.dumps({'loaded': loaded, 'file': name, 'n': len(results)}))\n"
        )
        done = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=ROOT
        )
        report = json.loads(done.stdout)
        assert report["loaded"] == []
        assert report["file"] == "demo_DEMO_S1.json" and report["n"] > 40


def _analysis_inputs() -> list[Path]:
    """One-image v1 files of the repository's format corpus and golden inputs."""
    folders = [ROOT / "tests" / "io" / "data", ROOT / "tests" / "golden" / "web"]
    found = []
    for folder in folders:
        for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            if path.name.endswith(".expected.json") or path.name in (
                "index.json",
                "tolerance.json",
            ):
                continue
            try:
                doc = parse_document(path.read_text(encoding="utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if len(doc["images"]) == 1:
                found.append(path)
    return found


class TestResultsJsonEqualsAnalyze:
    """M2-3 #1, bridge side: ``doc.export results_json`` is what ``sarcostat analyze --json`` prints."""

    @pytest.mark.parametrize("path", _analysis_inputs(), ids=lambda p: f"{p.parent.name}/{p.name}")
    def test_same_text(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        client = Client()
        client.ok("doc.load_v1", {"text": text, "image": None, "confirm": False})
        exported, _ = client.ok("doc.export", {"what": "results_json"})
        assert exported["text"] == results_json(analyze_document(parse_document(text)))
        check_log(client.log)

    def test_the_corpus_is_found(self) -> None:
        names = {p.name for p in _analysis_inputs()}
        if (ROOT / "tests" / "io" / "data" / "golden_v1_gui.json").is_file():
            assert "golden_v1_gui.json" in names
            assert "golden_v1_study.json" not in names


def test_bridge_state_is_per_instance() -> None:
    first, second = annotation_client(), Client()
    first.ok("doc.new", {"image": IMAGE})
    response, _ = second.call("shape.undo", {})
    assert response["error"]["code"] == "refused"
    assert isinstance(first.bridge.state["annotation"], AnnotationSession)
    assert bridge.FAMILY_LOADERS["doc"] is bridge.FAMILY_LOADERS["shape"]
    assert isinstance(Bridge().state, dict)
