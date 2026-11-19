"""JSON serialization helpers for SarcoStat core data containers.

The dataclasses in :mod:`sarcostat.core.circular_stats` and
:mod:`sarcostat.core.sarcomere_morphometry` carry only ``to_dict`` methods.
File persistence is decoupled into the small helpers below so that data
representation and storage are independent concerns: callers construct the
dataclass, then hand it to :func:`dump_json` if they want a file, or
:func:`to_json_string` if they want a string.
"""

from __future__ import annotations

import json
import math
from os import PathLike
from pathlib import Path
from typing import Any, Protocol, Union, runtime_checkable

PathInput = Union[str, "PathLike[str]", Path]


def _strict_json_value(value: Any) -> Any:
    """Recursively replace NaN and +/-inf floats with ``None``.

    :func:`json.dumps` would otherwise emit the non-standard ``NaN`` and
    ``Infinity`` tokens (e.g. for an empty report, or the infinite
    circular std of uniform data), which strict JSON parsers reject.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _strict_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_strict_json_value(v) for v in value]
    return value


@runtime_checkable
class Serializable(Protocol):
    """Structural type for objects that expose a JSON-friendly dictionary view.

    Any container that implements ``to_dict()`` returning a ``dict`` whose
    leaves are JSON-encodable (numbers, strings, lists, dicts, booleans,
    or ``None``) satisfies this Protocol. Enums and numpy scalars are NOT
    automatically converted - the implementing ``to_dict`` is responsible
    for reducing them to plain Python types.
    """

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dictionary representation of the object."""
        ...


def to_json_string(obj: Serializable | dict[str, Any], *, indent: int = 2) -> str:
    """Serialize a dataclass-like container to a JSON string.

    Parameters
    ----------
    obj : Serializable or dict
        Either an object exposing ``to_dict()`` or an already-prepared
        dictionary.
    indent : int, optional
        Indentation level passed through to :func:`json.dumps` (default 2).

    Returns
    -------
    str
        Pretty-printed, standard-compliant JSON representation of the
        container. Non-finite floats (NaN, inf) are written as ``null``.
    """
    payload = obj if isinstance(obj, dict) else obj.to_dict()
    return json.dumps(_strict_json_value(payload), indent=indent, allow_nan=False)


def dump_json(
    obj: Serializable | dict[str, Any],
    path: PathInput,
    *,
    indent: int = 2,
) -> Path:
    """Write a dataclass-like container to a JSON file.

    Parameters
    ----------
    obj : Serializable or dict
        Either an object exposing ``to_dict()`` or an already-prepared
        dictionary.
    path : str or os.PathLike
        Destination file path. Parent directories must already exist;
        callers that need directory creation should use
        ``Path(path).parent.mkdir(parents=True, exist_ok=True)`` first.
    indent : int, optional
        Indentation level passed through to :func:`json.dumps` (default 2).

    Returns
    -------
    pathlib.Path
        The resolved path that was written, for convenient chaining.
    """
    target = Path(path)
    target.write_text(to_json_string(obj, indent=indent))
    return target
