"""Strict YAML parsing helpers shared by the simulation config loaders.

The four loaders (chain, sarc, coverage, MDE) used to read keys with
``cfg.get(...)`` and coerce them with ``bool()``/``int()``/``float()``.
That silently ignored misspelled keys (``n_simulation:``) and turned a
quoted ``"false"`` into ``True``. The helpers below make every loader
reject unknown keys with a did-you-mean hint and coerce values strictly,
while letting a loader accept retired keys with a :class:`FutureWarning`
so that older configuration files still load.
"""

from __future__ import annotations

import difflib
import warnings
from collections.abc import Callable, Collection, Mapping
from pathlib import Path
from typing import Any, TypeVar

import yaml

T = TypeVar("T")


def read_yaml_mapping(path: str | Path) -> dict[str, Any]:
    """Load a YAML file whose top level must be a mapping (or empty)."""
    with open(path) as f:
        data = yaml.safe_load(f)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a mapping, got {type(data).__name__}")
    return data


def section(raw: Mapping[str, Any], key: str, where: str = "") -> dict[str, Any]:
    """Return the nested mapping ``raw[key]`` (``{}`` when absent or null)."""
    value = raw.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{_qualified(where, key)} must be a mapping, got {value!r}")
    return value


def check_keys(
    raw: Mapping[str, Any],
    allowed: Collection[str],
    *,
    where: str = "",
    retired: Mapping[str, str] | None = None,
) -> None:
    """Reject keys not in ``allowed``; warn on keys listed in ``retired``.

    Parameters
    ----------
    raw : mapping
        Parsed YAML section.
    allowed : collection of str
        Keys the loader understands.
    where : str, optional
        Dotted section name used in messages (empty for the top level).
    retired : mapping, optional
        ``{key: reason}`` for keys that are accepted but ignored; each
        one present emits a :class:`FutureWarning` with ``reason``.

    Raises
    ------
    ValueError
        Listing every unknown key, with the closest allowed key when one
        is similar enough.
    """
    retired = retired or {}
    unknown = [k for k in raw if k not in allowed and k not in retired]
    if unknown:
        hints = []
        for key in unknown:
            match = difflib.get_close_matches(str(key), list(allowed), n=1)
            hint = f" (did you mean {match[0]!r}?)" if match else ""
            hints.append(f"{key!r}{hint}")
        place = f"section {where!r}" if where else "the configuration"
        raise ValueError(
            f"Unknown key(s) in {place}: {', '.join(hints)}. "
            f"Valid keys: {', '.join(sorted(allowed))}"
        )
    for key, reason in retired.items():
        if key in raw:
            warnings.warn(
                f"{_qualified(where, key)} is ignored: {reason}",
                FutureWarning,
                stacklevel=3,
            )


def as_bool(value: Any, name: str) -> bool:
    """Return ``value`` if it is a YAML boolean; reject strings and numbers."""
    if isinstance(value, bool):
        return value
    raise ValueError(f"{name} must be true or false, got {value!r}")


def as_int(value: Any, name: str) -> int:
    """Return ``value`` as an int; reject booleans and non-integral floats."""
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer, got {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            pass
    raise ValueError(f"{name} must be an integer, got {value!r}")


def as_float(value: Any, name: str) -> float:
    """Return ``value`` as a float; reject booleans and non-numeric strings.

    Numeric strings are accepted because YAML 1.1 parses exponent
    notation without a decimal point (``1e-3``) as a string.
    """
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number, got {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            pass
    raise ValueError(f"{name} must be a number, got {value!r}")


def as_number(value: Any, name: str) -> float:
    """Like :func:`as_float`, but keep YAML integers as ``int``.

    Grid axes such as ``kappa_values`` are used as dictionary keys and in
    labels, where ``0`` and ``0.0`` print differently.
    """
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return as_float(value, name)


def as_str(value: Any, name: str) -> str:
    """Return ``value`` if it is a string."""
    if isinstance(value, str):
        return value
    raise ValueError(f"{name} must be a string, got {value!r}")


def as_list(value: Any, name: str, item: Callable[[Any, str], T]) -> list[T]:
    """Return ``value`` as a list, coercing each element with ``item``."""
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be a list, got {value!r}")
    return [item(v, f"{name}[{i}]") for i, v in enumerate(value)]


def as_pair(value: Any, name: str) -> tuple[float, float]:
    """Return a two-element numeric list as a ``(float, float)`` tuple."""
    values = as_list(value, name, as_float)
    if len(values) != 2:
        raise ValueError(f"{name} must have exactly 2 elements, got {value!r}")
    return values[0], values[1]


def require_unique(values: list[Any], name: str) -> None:
    """Reject duplicated grid values, which would silently collapse cells."""
    seen: set[Any] = set()
    dupes: set[Any] = set()
    for value in values:
        if value in seen:
            dupes.add(value)
        seen.add(value)
    if dupes:
        raise ValueError(f"{name} contains duplicate values: {sorted(dupes)}")


def load_flat_dataclass(
    path: str | Path,
    cls: Callable[..., T],
    converters: Mapping[str, Callable[[Any, str], Any]],
) -> T:
    """Load a flat YAML mapping into ``cls`` with one converter per key.

    Keys missing from the file (or set to null) take the dataclass
    defaults; unknown keys raise ``ValueError``.
    """
    raw = read_yaml_mapping(path)
    check_keys(raw, converters.keys())
    kwargs = {key: converters[key](value, key) for key, value in raw.items() if value is not None}
    return cls(**kwargs)


def _qualified(where: str, key: str) -> str:
    return f"{where}.{key}" if where else key


__all__ = [
    "read_yaml_mapping",
    "section",
    "check_keys",
    "as_bool",
    "as_int",
    "as_float",
    "as_number",
    "as_str",
    "as_list",
    "as_pair",
    "require_unique",
    "load_flat_dataclass",
]
