"""JSON command interface between the web worker and the Python engine.

The page's worker sends one request at a time as JSON text, with binary
values travelling beside it as a list of buffers::

    {"v": 1, "id": <int or str>, "cmd": <str>, "args": <object>}

and receives::

    {"v": 1, "id": <same>, "ok": true, "result": <object>}
    {"v": 1, "id": <same or null>, "ok": false, "error": {"code": <code>, "message": <one line>}}

where ``code`` is ``bad_request`` (the request does not follow the
catalogue), ``refused`` (the command cannot run in this state or build),
``needs_confirmation`` (the command needs the user's explicit consent,
given by sending it again with ``confirm``) or ``internal`` (a defect).
Inside ``args`` and ``result`` a binary value is the object
``{"$buf": k}``, an index into the buffer list of the message.

:func:`catalogue` describes every command with the schemas of its
arguments and of its result, in a small subset of JSON Schema (see
:data:`SCHEMA_KEYWORDS`); :func:`dispatch` checks each request against
it, runs the command and checks the result against it too, and never
raises. Command families are looked up lazily, on their first command,
so that a study session never imports the metric code: ``sys`` is answered
here, ``doc``, ``shape`` and ``results`` by
:mod:`sarcostat.engine.handlers_annotate`, ``study`` by
:mod:`sarcostat.engine.handlers_study`. The engine objects that live
between commands are kept in :attr:`Bridge.state`.

``python -m sarcostat.engine.bridge --catalogue`` prints the catalogue as
canonical JSON.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from itertools import accumulate
from typing import Any, NoReturn

BRIDGE_VERSION = 1
ABSENT_FAMILIES = ("chain", "register", "planner")
ERROR_CODES = ("bad_request", "refused", "needs_confirmation", "internal")
SCHEMA_TYPES = ("object", "array", "string", "number", "integer", "boolean", "null", "buffer")
SCHEMA_KEYWORDS = (
    "type",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "enum",
    "minimum",
)
MARK_ROLES = ("A", "B", "C", "D", "E", "F", "chain", "myofibril_1", "myofibril_2")
STROKE_ROLES = ("z1", "m", "z2", "chain", "myofibril_1", "myofibril_2")
EXPORT_KINDS = ("v1", "results_json", "results_csv", "provenance")
STUDY_VIEW_KEYS = (
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
)
STUDY_RESULT_KEYS = ("view", "autosave", "seq", "finished")
MODE_STATE_KEYS = (
    "mode",
    "image",
    "settings",
    "pending",
    "items",
    "controls",
    "step",
    "notice",
    "not_exported",
)

MAX_JSON_DEPTH = 64

_REQUEST_KEYS = frozenset({"v", "id", "cmd", "args"})
_JSON_STRING = re.compile(r'"(?:[^"\\]|\\.)*"', re.DOTALL)
_NOT_BRACKET = re.compile(r"[^\[\]{}]+")
_MAX_MESSAGE = 500
_FALLBACK = (
    '{"error":{"code":"internal","message":"the bridge could not write its response"},'
    '"id":null,"ok":false,"v":1}'
)


class BridgeError(Exception):
    """A command that ends with an error response instead of a result.

    The engine raises the subclasses :class:`Refused` and
    :class:`NeedsConfirmation`; :func:`dispatch` turns them into error
    responses with their :attr:`code` and a one-line :attr:`message`.
    """

    code = "internal"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = one_line(message)


class BadRequest(BridgeError):
    """The request does not follow the envelope or the command's schema."""

    code = "bad_request"


class SchemaError(BadRequest):
    """A value that does not match its schema (the message starts with its path)."""


class Refused(BridgeError):
    """The command cannot run now: wrong state, invalid input or not in this build."""

    code = "refused"


class NeedsConfirmation(BridgeError):
    """The command runs only when the user confirms it (``confirm: true``)."""

    code = "needs_confirmation"


Handler = Callable[["Bridge", dict[str, Any]], dict[str, Any]]
FamilyLoader = Callable[[], Mapping[str, Handler]]


def one_line(text: str) -> str:
    """``text`` on one line (whitespace runs collapsed), at most 500 characters."""
    flat = " ".join(str(text).split())
    if len(flat) > _MAX_MESSAGE:
        flat = flat[: _MAX_MESSAGE - 3] + "..."
    return flat


_STRING: dict[str, Any] = {"type": "string"}
_NUMBER: dict[str, Any] = {"type": "number"}
_BOOLEAN: dict[str, Any] = {"type": "boolean"}
_BUFFER: dict[str, Any] = {"type": "buffer"}


def _integer(minimum: int | None = None, nullable: bool = False) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": ["integer", "null"] if nullable else "integer"}
    if minimum is not None:
        schema["minimum"] = minimum
    return schema


def _nullable(kind: str) -> dict[str, Any]:
    return {"type": [kind, "null"]}


def _enum(values: Sequence[Any], nullable: bool = False) -> dict[str, Any]:
    return {
        "type": ["string", "null"] if nullable else "string",
        "enum": [*values, None] if nullable else list(values),
    }


def _object(properties: dict[str, Any], nullable: bool = False) -> dict[str, Any]:
    """An object whose keys are exactly ``properties`` (all required)."""
    return {
        "type": ["object", "null"] if nullable else "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _map(values: dict[str, Any]) -> dict[str, Any]:
    """An object with free string keys, every value matching ``values``."""
    return {"type": "object", "additionalProperties": values}


def _array(items: dict[str, Any], nullable: bool = False) -> dict[str, Any]:
    return {"type": ["array", "null"] if nullable else "array", "items": items}


def _code(nullable: bool = False) -> dict[str, Any]:
    """A structured notice or prompt: a code and its parameters."""
    return _object(
        {"code": _STRING, "params": _map({"type": ["string", "integer", "boolean"]})},
        nullable=nullable,
    )


def _point() -> dict[str, Any]:
    return _array(_NUMBER)


def _mark() -> dict[str, Any]:
    return _object({"x": _NUMBER, "y": _NUMBER, "role": _enum(MARK_ROLES)})


def _stroke() -> dict[str, Any]:
    return _object({"p1": _point(), "p2": _point(), "role": _enum(STROKE_ROLES)})


def _image(nullable: bool = False) -> dict[str, Any]:
    return _object(
        {
            "file_name": _STRING,
            "sha256": _STRING,
            "width_px": _integer(1),
            "height_px": _integer(1),
        },
        nullable=nullable,
    )


def _mode_state() -> dict[str, Any]:
    return _object(
        {
            "mode": _enum(("shape", "home")),
            "image": _image(nullable=True),
            "settings": _object(
                {
                    "pixel_size": _NUMBER,
                    "unit": _STRING,
                    "reference_direction_deg": _nullable("number"),
                    "click_sigma_z": _nullable("number"),
                    "click_sigma_m": _nullable("number"),
                    "click_perp_ratio": _NUMBER,
                }
            ),
            "pending": _object({"marks": _array(_mark()), "strokes": _array(_stroke())}),
            "items": _array(
                _object(
                    {
                        "item_id": _STRING,
                        "type": _enum(("shape", "chain", "pair")),
                        "sarcomere_id": _integer(1, nullable=True),
                        "marks": _array(_mark()),
                        "strokes": _array(_stroke()),
                    }
                )
            ),
            "controls": _object(
                {
                    "can_confirm": _BOOLEAN,
                    "can_undo": _BOOLEAN,
                    "can_skip_m_line": _BOOLEAN,
                    "can_delete": _BOOLEAN,
                }
            ),
            "step": _code(nullable=True),
            "notice": _code(nullable=True),
            "not_exported": _BOOLEAN,
        }
    )


def _time() -> dict[str, Any]:
    return _object({"mono_s": {"type": "number", "minimum": 0}, "wall_ms": _NUMBER})


def _study_view() -> dict[str, Any]:
    return _object(
        {
            "item_file": _nullable("string"),
            "roi": _array(_NUMBER, nullable=True),
            "title": _nullable("string"),
            "marks": _array(_mark()),
            "strokes": _array(_stroke()),
            "controls": _object(
                {
                    "can_confirm": _BOOLEAN,
                    "can_undo": _BOOLEAN,
                    "can_skip_m_line": _BOOLEAN,
                    "can_next_myofibril": _BOOLEAN,
                    "shows_branch_end": _BOOLEAN,
                    "item_type": _enum(("shape", "chain", "pair"), nullable=True),
                }
            ),
            "step": _code(nullable=True),
            "progress": _object({"done": _integer(0), "total": _integer(0)}),
            "notice": _code(nullable=True),
            "paused": _BOOLEAN,
            "finished": _object({"file_name": _STRING}, nullable=True),
        }
    )


def _study_result() -> dict[str, Any]:
    return _object(
        {
            "view": _study_view(),
            "autosave": _nullable("buffer"),
            "seq": _integer(0),
            "finished": _object(
                {"file_name": _STRING, "bytes": _BUFFER, "sha256": _STRING}, nullable=True
            ),
        }
    )


def _table() -> dict[str, Any]:
    return _object(
        {
            "title": _STRING,
            "columns": _array(_STRING),
            "rows": _array(_array(_STRING)),
            "notes": _array(_STRING),
        }
    )


def _plot_data() -> dict[str, Any]:
    numbers = _array(_nullable("number"))
    return _object(
        {
            "shape": _array(
                _object(
                    {
                        "sarcomere_id": _integer(1),
                        "shape_index": _nullable("number"),
                        "u_shape_index": _nullable("number"),
                        "shape_class": _STRING,
                    }
                )
            ),
            "chains": _array(
                _object(
                    {
                        "chain": _integer(1),
                        "headings_deg": numbers,
                        "turning_deg": copy.deepcopy(numbers),
                        "mean_heading_deg": _nullable("number"),
                        "mean_vector_length": _nullable("number"),
                        "reference_direction_deg": _nullable("number"),
                    }
                )
            ),
            "register": _array(
                _object(
                    {
                        "pair": _integer(1),
                        "phases_deg": copy.deepcopy(numbers),
                        "correlation_lags": copy.deepcopy(numbers),
                        "correlation_values": copy.deepcopy(numbers),
                    }
                )
            ),
        }
    )


def _build_commands() -> dict[str, dict[str, Any]]:
    mode_state = _mode_state
    study = _study_result
    timed = _object({"t": _time()})
    specs: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
        "sys.identity": (
            _object({}),
            _object(
                {
                    "sarcostat_version": _STRING,
                    "source_digest": _STRING,
                    "bridge_version": _integer(1),
                }
            ),
        ),
        "sys.load_scipy": (_object({}), _object({"scipy_version": _STRING})),
        "doc.new": (_object({"image": _image()}), mode_state()),
        "doc.load_v1": (
            _object({"text": _STRING, "image": _image(nullable=True), "confirm": _BOOLEAN}),
            mode_state(),
        ),
        "doc.export": (
            _object({"what": _enum(EXPORT_KINDS)}),
            _object({"file_name": _STRING, "text": _STRING}),
        ),
        "doc.set_scale": (_object({"pixel_size": _NUMBER, "unit": _STRING}), mode_state()),
        "doc.set_click_error": (_object({"sigma": _nullable("number")}), mode_state()),
        "doc.estimate_click_error": (_object({}), mode_state()),
        "doc.set_reference_direction": (_object({"deg": _nullable("number")}), mode_state()),
        "shape.click": (_object({"x": _NUMBER, "y": _NUMBER}), mode_state()),
        "shape.undo": (_object({}), mode_state()),
        "shape.skip_m_line": (_object({}), mode_state()),
        "shape.confirm": (_object({}), mode_state()),
        "shape.delete": (_object({"item_id": _STRING}), mode_state()),
        "shape.clear": (_object({}), mode_state()),
        "results.get": (
            _object({}),
            _object(
                {
                    "sentences": _array(_STRING),
                    "tables": _map(_table()),
                    "plot_data": _plot_data(),
                }
            ),
        ),
        "study.open_kit": (
            _object({"manifest": _BUFFER, "manifest_sha256": _BUFFER}),
            _object(
                {
                    "study_id": _STRING,
                    "annotator_id": _STRING,
                    "session_id": _STRING,
                    "n_items": _integer(0),
                    "files": _array(_STRING),
                }
            ),
        ),
        "study.verify_items": (
            _object({"files": _map(_BUFFER)}),
            _object({"ok": _BOOLEAN, "problems": _array(_code())}),
        ),
        "study.start": (
            _object(
                {"autosave": _nullable("buffer"), "seq": _integer(0, nullable=True), "t": _time()}
            ),
            study(),
        ),
        "study.confirm_identity": (copy.deepcopy(timed), study()),
        "study.click": (_object({"x": _NUMBER, "y": _NUMBER, "t": _time()}), study()),
        "study.undo": (copy.deepcopy(timed), study()),
        "study.skip_m_line": (copy.deepcopy(timed), study()),
        "study.next_myofibril": (copy.deepcopy(timed), study()),
        "study.confirm_item": (_object({"branch_end": _BOOLEAN, "t": _time()}), study()),
        "study.skip_item": (_object({"reason": _STRING, "t": _time()}), study()),
        "study.pause": (copy.deepcopy(timed), study()),
        "study.resume": (copy.deepcopy(timed), study()),
    }
    return {
        name: {"family": name.split(".", 1)[0], "args": args, "result": result}
        for name, (args, result) in specs.items()
    }


_COMMANDS = _build_commands()


def catalogue() -> dict[str, Any]:
    """Every command with its family and the schemas of its arguments and result.

    Returns
    -------
    dict
        ``{"bridge_version": 1, "commands": {name: {"family", "args",
        "result"}}, "absent_families": [...]}``, a fresh copy.
    """
    return {
        "bridge_version": BRIDGE_VERSION,
        "commands": copy.deepcopy(_COMMANDS),
        "absent_families": list(ABSENT_FAMILIES),
    }


def _fail(path: str, message: str) -> NoReturn:
    raise SchemaError(f"{path}: {message}")


def _types(schema: Mapping[str, Any]) -> tuple[str, ...]:
    kind = schema["type"]
    return (kind,) if isinstance(kind, str) else tuple(kind)


def _is_buffer_ref(value: Any) -> bool:
    return isinstance(value, dict) and set(value) == {"$buf"}


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def validate(value: Any, schema: Mapping[str, Any], n_buffers: int = 0, path: str = "") -> None:
    """Check a JSON value, in its wire form, against a catalogue schema.

    Parameters
    ----------
    value : Any
        The parsed JSON value; a binary value is ``{"$buf": k}``.
    schema : Mapping
        A schema of the catalogue's JSON Schema subset.
    n_buffers : int
        Number of buffers sent with the message (valid indexes are
        ``0 .. n_buffers - 1``).
    path : str
        Where ``value`` sits, for the error message.

    Raises
    ------
    SchemaError
        On the first mismatch, with the path of the offending value.
    """
    where = path or "value"
    allowed = _types(schema)
    if "buffer" in allowed and _is_buffer_ref(value):
        index = value["$buf"]
        if type(index) is not int or not 0 <= index < n_buffers:
            _fail(where, f"$buf index {index!r} does not name one of the {n_buffers} buffer(s)")
        return
    actual = _json_type(value)
    if actual == "integer" and "integer" not in allowed and "number" in allowed:
        actual = "number"
    if actual not in allowed:
        _fail(where, f"expected {' or '.join(allowed)}, got {actual}")
    if actual == "number" and not math.isfinite(value):
        _fail(where, "must be a finite number")
    if "enum" in schema and not any(
        type(value) is type(choice) and value == choice for choice in schema["enum"]
    ):
        choices = ", ".join("null" if c is None else str(c) for c in schema["enum"])
        _fail(where, f"must be one of {choices}; got {value!r}")
    if "minimum" in schema and actual in ("integer", "number") and value < schema["minimum"]:
        _fail(where, f"must be >= {schema['minimum']}, got {value!r}")
    if actual == "array":
        for k, element in enumerate(value):
            validate(element, schema["items"], n_buffers, f"{where}[{k}]")
    elif actual == "object":
        _validate_object(value, schema, n_buffers, where)


def _validate_object(
    value: dict[str, Any], schema: Mapping[str, Any], n_buffers: int, where: str
) -> None:
    properties = schema.get("properties")
    extra = schema.get("additionalProperties", False)
    if properties is None:
        for key, element in value.items():
            validate(element, extra, n_buffers, f"{where}[{key!r}]")
        return
    for key in schema.get("required", ()):
        if key not in value:
            _fail(where, f"missing key {key!r}")
    for key, element in value.items():
        if key in properties:
            validate(element, properties[key], n_buffers, f"{where}.{key}")
        elif extra is False:
            _fail(where, f"unexpected key {key!r}")
        else:
            validate(element, extra, n_buffers, f"{where}[{key!r}]")


def _decode_buffers(value: Any, schema: Mapping[str, Any], buffers: Sequence[bytes]) -> Any:
    """Replace each ``{"$buf": k}`` of a validated value by the bytes of buffer ``k``."""
    allowed = _types(schema)
    if "buffer" in allowed and _is_buffer_ref(value):
        return buffers[value["$buf"]]
    if isinstance(value, list):
        return [_decode_buffers(v, schema["items"], buffers) for v in value]
    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        extra = schema.get("additionalProperties", False)
        return {
            key: _decode_buffers(v, properties[key] if key in properties else extra, buffers)
            for key, v in value.items()
        }
    return value


def _encode_buffers(value: Any, out: list[bytes]) -> Any:
    """Replace each bytes-like value by ``{"$buf": k}``, appending its bytes to ``out``."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        out.append(bytes(value))
        return {"$buf": len(out) - 1}
    if isinstance(value, (list, tuple)):
        return [_encode_buffers(v, out) for v in value]
    if isinstance(value, dict):
        return {key: _encode_buffers(v, out) for key, v in value.items()}
    return value


def _reject_constant(token: str) -> NoReturn:
    raise ValueError(f"the constant {token} is not JSON")


def _finite_float(token: str) -> float:
    number = float(token)
    if not math.isfinite(number):
        raise ValueError(f"the number {token} is too large")
    return number


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate key {key!r}")
        out[key] = value
    return out


def json_depth(text: str) -> int:
    """How deeply arrays and objects nest in JSON ``text`` (brackets inside strings ignored).

    A cheap scan without parsing, so that deeply nested text is refused
    before :func:`json.loads` recurses into it: in the browser the
    parser's recursion would exhaust the WebAssembly stack and kill the
    worker. Malformed text may give a larger depth than a parser would
    reach; it is refused either way.
    """
    brackets = _NOT_BRACKET.sub("", _JSON_STRING.sub("", text))
    steps = (1 if c in "[{" else -1 for c in brackets)
    return max(accumulate(steps, initial=0))


def _parse(text: Any) -> Any:
    if not isinstance(text, str):
        raise BadRequest(f"the request must be JSON text, got {type(text).__name__}")
    depth = json_depth(text)
    if depth > MAX_JSON_DEPTH:
        raise BadRequest(
            f"the request is not valid JSON: it nests {depth} levels, more than {MAX_JSON_DEPTH}"
        )
    try:
        return json.loads(
            text,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
            object_pairs_hook=_unique_keys,
        )
    except (ValueError, RecursionError) as exc:
        raise BadRequest(f"the request is not valid JSON: {exc}") from None


def _readable_id(request: Any) -> int | str | None:
    if not isinstance(request, dict):
        return None
    ident = request.get("id")
    if isinstance(ident, str) or (type(ident) is int):
        return ident
    return None


def _buffers(buffers: Any) -> list[bytes]:
    try:
        return [b if isinstance(b, bytes) else bytes(memoryview(b)) for b in buffers]
    except TypeError as exc:
        raise BadRequest(f"the buffers must be bytes: {exc}") from None


def _response(ident: int | str | None, payload: dict[str, Any]) -> str:
    return json.dumps(
        {"v": BRIDGE_VERSION, "id": ident, **payload},
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _error(ident: int | str | None, code: str, message: str) -> tuple[str, list[bytes]]:
    payload = {"ok": False, "error": {"code": code, "message": one_line(message)}}
    return _response(ident, payload), []


def _sys_handlers() -> dict[str, Handler]:
    return {"sys.identity": _sys_identity, "sys.load_scipy": _sys_load_scipy}


def _sys_identity(bridge: Bridge, args: dict[str, Any]) -> dict[str, Any]:
    if "source_digest" not in bridge.state:
        from sarcostat.io.provenance import source_digest

        bridge.state["source_digest"] = source_digest()
    from sarcostat import __version__

    return {
        "sarcostat_version": __version__,
        "source_digest": bridge.state["source_digest"],
        "bridge_version": BRIDGE_VERSION,
    }


def _sys_load_scipy(bridge: Bridge, args: dict[str, Any]) -> dict[str, Any]:
    try:
        import scipy
        import scipy.special  # noqa: F401
    except ImportError as exc:
        raise Refused(f"scipy is not available: {exc}") from None
    return {"scipy_version": str(scipy.__version__)}


def _annotation_handlers() -> Mapping[str, Handler]:
    from sarcostat.engine.handlers_annotate import handlers

    return handlers()


def _study_handlers() -> Mapping[str, Handler]:
    from sarcostat.engine.handlers_study import handlers

    return handlers()


FAMILY_LOADERS: dict[str, FamilyLoader] = {
    "sys": _sys_handlers,
    "doc": _annotation_handlers,
    "shape": _annotation_handlers,
    "results": _annotation_handlers,
    "study": _study_handlers,
}


class Bridge:
    """One worker's command dispatcher and the engine state it holds.

    Parameters
    ----------
    families : Mapping, optional
        ``{family: loader}``, where ``loader()`` returns the handlers of
        that family as ``{command: handler}``; each loader runs once, on
        the first command of its family. Default: :data:`FAMILY_LOADERS`.
    commands : Mapping, optional
        The catalogue's commands; default: the shipped catalogue.

    Attributes
    ----------
    state : dict
        Engine objects that live between commands (the annotation session,
        the study presenter), owned by the handlers.
    """

    def __init__(
        self,
        families: Mapping[str, FamilyLoader] | None = None,
        commands: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        self._loaders = dict(FAMILY_LOADERS if families is None else families)
        self._handlers: dict[str, Mapping[str, Handler]] = {}
        self._commands = _COMMANDS if commands is None else commands
        self.state: dict[str, Any] = {}

    def dispatch(
        self,
        request_json: str,
        buffers: Sequence[bytes | bytearray | memoryview] = (),
    ) -> tuple[str, list[bytes]]:
        """Run one request; return the response JSON and the buffers it names.

        Never raises: every failure becomes an error response.
        """
        ident: int | str | None = None
        try:
            request = _parse(request_json)
            ident = _readable_id(request)
            return self._run(request, ident, buffers)
        except BridgeError as exc:
            return _error(ident, exc.code, exc.message)
        except BaseException as exc:
            try:
                return _error(ident, "internal", f"{type(exc).__name__}: {exc}")
            except BaseException:
                return _FALLBACK, []

    def _run(
        self, request: Any, ident: int | str | None, raw_buffers: Sequence[Any]
    ) -> tuple[str, list[bytes]]:
        if not isinstance(request, dict):
            raise BadRequest(f"the request must be a JSON object, got {_json_type(request)}")
        missing = sorted(_REQUEST_KEYS - set(request))
        if missing:
            raise BadRequest(f"the request misses {', '.join(missing)}")
        extra = sorted(set(request) - _REQUEST_KEYS)
        if extra:
            raise BadRequest(f"the request has unexpected keys {', '.join(extra)}")
        if ident is None:
            raise BadRequest("the request id must be an integer or a string")
        version = request["v"]
        if type(version) is not int or version != BRIDGE_VERSION:
            raise BadRequest(f"bridge version {version!r} is not {BRIDGE_VERSION}")
        cmd = request["cmd"]
        if not isinstance(cmd, str):
            raise BadRequest(f"cmd must be a string, got {_json_type(cmd)}")
        if cmd.split(".", 1)[0] in ABSENT_FAMILIES:
            raise Refused(f"{cmd}: not in this build")
        spec = self._commands.get(cmd)
        if spec is None:
            raise BadRequest(f"unknown command {cmd!r}")
        args = request["args"]
        if not isinstance(args, dict):
            raise BadRequest(f"{cmd}: args must be a JSON object, got {_json_type(args)}")
        buffers = _buffers(raw_buffers)
        try:
            validate(args, spec["args"], len(buffers), "args")
        except SchemaError as exc:
            raise BadRequest(f"{cmd}: {exc.message}") from None
        handler = self._handler(cmd, str(spec["family"]))
        if handler is None:
            raise Refused(f"{cmd}: not implemented yet")
        result = handler(self, _decode_buffers(args, spec["args"], buffers))
        out: list[bytes] = []
        wire = _encode_buffers(result, out)
        try:
            validate(wire, spec["result"], len(out), "result")
        except SchemaError as exc:
            raise BridgeError(
                f"{cmd}: the result does not match its schema: {exc.message}"
            ) from None
        try:
            text = _response(ident, {"ok": True, "result": wire})
        except ValueError as exc:
            raise BridgeError(f"{cmd}: the result cannot be written as JSON: {exc}") from None
        return text, out

    def _handler(self, cmd: str, family: str) -> Handler | None:
        if family not in self._handlers:
            loader = self._loaders.get(family)
            self._handlers[family] = {} if loader is None else loader()
        return self._handlers[family].get(cmd)


_DEFAULT = Bridge()


def dispatch(
    request_json: str, buffers: Sequence[bytes | bytearray | memoryview] = ()
) -> tuple[str, list[bytes]]:
    """Run one request on the worker's bridge (see :meth:`Bridge.dispatch`)."""
    return _DEFAULT.dispatch(request_json, buffers)


def catalogue_json() -> str:
    """The catalogue as canonical JSON: sorted keys, two-space indent, ASCII, final newline."""
    text = json.dumps(
        catalogue(), sort_keys=True, indent=2, separators=(",", ": "), ensure_ascii=True
    )
    return text + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    """Command line: ``--catalogue`` prints the catalogue and exits 0."""
    parser = argparse.ArgumentParser(
        prog="python -m sarcostat.engine.bridge",
        description="The JSON command interface of the SarcoStat web worker.",
    )
    parser.add_argument(
        "--catalogue", action="store_true", help="print the command catalogue as canonical JSON"
    )
    options = parser.parse_args(argv)
    if not options.catalogue:
        parser.print_usage(sys.stderr)
        return 2
    sys.stdout.write(catalogue_json())
    return 0


__all__ = [
    "ABSENT_FAMILIES",
    "BRIDGE_VERSION",
    "ERROR_CODES",
    "EXPORT_KINDS",
    "FAMILY_LOADERS",
    "MARK_ROLES",
    "MODE_STATE_KEYS",
    "SCHEMA_KEYWORDS",
    "SCHEMA_TYPES",
    "STROKE_ROLES",
    "STUDY_RESULT_KEYS",
    "STUDY_VIEW_KEYS",
    "BadRequest",
    "Bridge",
    "BridgeError",
    "FamilyLoader",
    "Handler",
    "MAX_JSON_DEPTH",
    "NeedsConfirmation",
    "Refused",
    "SchemaError",
    "catalogue",
    "catalogue_json",
    "dispatch",
    "json_depth",
    "main",
    "one_line",
    "validate",
]


if __name__ == "__main__":
    sys.exit(main())
