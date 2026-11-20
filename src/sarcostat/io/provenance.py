"""Provenance of SarcoStat results: which source produced them, from which files.

:func:`source_digest` identifies the SarcoStat source that is installed,
independently of the version label: two installations give the same
digest exactly when their source files are identical, whether the package
comes from a wheel, an sdist or a source checkout. :func:`provenance`
builds the block that exports carry: the version, the digest, and the
names and SHA-256 of the input files, without folders, sizes or times.
:func:`provenance_from_digests` builds the same block from ``(file name,
SHA-256)`` pairs, for callers that hold the input bytes but no path (the
web app).

The digest is the SHA-256 of the text that ``sha256sum`` prints for the
source files, one line ``<sha256>  <path>`` per file with paths relative
to the package folder and sorted, so it can be checked without Python::

    cd .../site-packages/sarcostat
    find . \\( -name __pycache__ -o -name '.?*' -o -name '*.dist-info' \\
           -o -name '*.egg-info' \\) -prune -o -type f \\( -name '*.py' \\
           -o -name '*.yaml' -o -name py.typed -o -path './io/*.json' \\) \\
           -print | sed 's|^\\./||' | LC_ALL=C sort | xargs sha256sum | sha256sum

(``io/*.json`` means the files directly in that folder.)
Computing the digest imports nothing.
"""

from __future__ import annotations

import copy
import hashlib
import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from sarcostat import __version__

from .annotations import PathInput, sha256_file

_SKIPPED_DIR_SUFFIXES = (".dist-info", ".egg-info")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


def _package_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _selected(rel: PurePosixPath) -> bool:
    folder = rel.parent.as_posix()
    return (
        rel.suffix in (".py", ".yaml")
        or rel.name == "py.typed"
        or (folder == "io" and rel.suffix == ".json")
    )


def _source_files(root: Path) -> list[tuple[str, Path]]:
    """``(relative POSIX path, path)`` of the digested files, sorted by relative path.

    Folders named ``__pycache__``, hidden folders (``.omc`` and the like)
    and ``*.dist-info``/``*.egg-info`` folders are skipped.
    """
    found = []
    for folder, subfolders, names in os.walk(root):
        subfolders[:] = [
            d
            for d in subfolders
            if d != "__pycache__"
            and not d.startswith(".")
            and not d.endswith(_SKIPPED_DIR_SUFFIXES)
        ]
        for name in names:
            path = Path(folder, name)
            rel = PurePosixPath(path.relative_to(root).as_posix())
            if _selected(rel):
                found.append((rel.as_posix(), path))
    return sorted(found)


def source_digest(root: PathInput | None = None) -> str:
    """SHA-256 identifying the SarcoStat source files.

    Parameters
    ----------
    root : str or os.PathLike, optional
        Package folder to digest (the folder holding ``__init__.py``), for
        example of an unpacked wheel. Default: the installed package.

    Returns
    -------
    str
        64 lowercase hexadecimal digits.

    Notes
    -----
    The digested files are ``*.py``, ``*.yaml``, ``py.typed`` and
    ``io/*.json``; ``__pycache__``, hidden folders such as ``.omc`` and
    ``*.dist-info`` folders are skipped. See the module docstring for the
    exact construction.
    """
    base = _package_root() if root is None else Path(root)
    if not (base / "__init__.py").is_file():
        raise ValueError(f"{base.name or base} is not a package folder (no __init__.py)")
    listing = "".join(f"{sha256_file(path)}  {rel}\n" for rel, path in _source_files(base))
    return hashlib.sha256(listing.encode("utf-8")).hexdigest()


def _check_relative(value: Any, where: str) -> None:
    if isinstance(value, (str, os.PathLike)):
        text = os.fspath(value)
        if PurePosixPath(text).is_absolute() or PureWindowsPath(text).is_absolute():
            raise ValueError(f"{where} is an absolute path; record file names only")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _check_relative(item, f"{where}.{key}")
    elif isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            _check_relative(item, f"{where}[{i}]")


def _parameters(parameters: Mapping[str, Any] | None) -> dict[str, Any]:
    params = copy.deepcopy(dict(parameters or {}))
    _check_relative(params, "parameters")
    return params


def _block(files: list[dict[str, str]], params: dict[str, Any]) -> dict[str, Any]:
    return {
        "sarcostat_version": __version__,
        "source_digest": source_digest(),
        "files": files,
        "parameters": params,
    }


def provenance(
    files: Iterable[PathInput] = (), parameters: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Provenance block for an export.

    Parameters
    ----------
    files : iterable of str or os.PathLike
        Input files of the result (images, annotation files), in the order
        to report them. Only their names and SHA-256 are recorded.
    parameters : mapping, optional
        Settings of the analysis (scale, tolerances, seeds), copied as
        given. They must not contain absolute paths.

    Returns
    -------
    dict
        ``sarcostat_version``, ``source_digest``, ``files`` (a list of
        ``{"file_name", "sha256"}``) and ``parameters``. No folders, file
        sizes or times are recorded.

    Raises
    ------
    ValueError
        If a value in ``parameters`` is an absolute path.
    FileNotFoundError
        If one of ``files`` does not exist.
    """
    params = _parameters(parameters)
    entries = [{"file_name": Path(f).name, "sha256": sha256_file(f)} for f in files]
    return _block(entries, params)


def _file_name(value: Any, where: str) -> str:
    if (
        not isinstance(value, str)
        or not 0 < len(value) <= 255
        or "/" in value
        or "\\" in value
        or value in (".", "..")
    ):
        raise ValueError(f"{where} must be a file name without folders, got {value!r}")
    return value


def provenance_from_digests(
    files: Iterable[tuple[str, str]] = (), parameters: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Provenance block for an export whose input files are given by name and SHA-256.

    The variant of :func:`provenance` for callers that hold the bytes of
    the input files but no path to them, such as the web app; the block is
    the same.

    Parameters
    ----------
    files : iterable of (str, str)
        ``(file name, SHA-256)`` of each input file, in the order to report
        them. A file name has no folder; a SHA-256 is 64 lowercase
        hexadecimal digits.
    parameters : mapping, optional
        Settings of the analysis, copied as given. They must not contain
        absolute paths.

    Returns
    -------
    dict
        ``sarcostat_version``, ``source_digest``, ``files`` (a list of
        ``{"file_name", "sha256"}``) and ``parameters``.

    Raises
    ------
    ValueError
        If a file name has a folder, a SHA-256 is malformed, or a value in
        ``parameters`` is an absolute path.
    """
    params = _parameters(parameters)
    entries = []
    for k, pair in enumerate(files):
        name, digest = pair
        if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
            raise ValueError(
                f"files[{k}] SHA-256 must be 64 lowercase hexadecimal digits, got {digest!r}"
            )
        entries.append({"file_name": _file_name(name, f"files[{k}]"), "sha256": digest})
    return _block(entries, params)


__all__ = ["provenance", "provenance_from_digests", "source_digest"]
