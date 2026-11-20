"""Layering of the package, read from the source (AST): who may import whom.

- ``engine/**`` and ``io/**`` import nothing from the command line, the
  simulation suite, the desktop GUI or the UI and plotting stacks;
- ``io/**`` imports nothing from ``analysis``, ``export`` or ``engine``;
- ``engine/study.py`` and ``engine/clock.py`` import no metric code;
- ``analysis.py`` and ``export.py`` import ``core`` and ``io`` only (``export``
  also ``analysis``), and no pandas, matplotlib or Pillow;
- scipy is imported only inside functions, and ``engine/reports.py`` imports
  ``core``, ``analysis`` and ``export`` only inside functions;
- the never-released ``io/loader.py`` is gone, and nothing in ``engine`` or
  ``analysis.py`` calls the ``summary_string`` reports of ``core``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "sarcostat"
UI_AND_TOOLS = (
    "sarcostat.cli",
    "sarcostat.simulation",
    "sarcostat.gui",
    "matplotlib",
    "pandas",
    "PIL",
    "tifffile",
    "tkinter",
    "typer",
    "rich",
)
METRICS = ("sarcostat.core", "sarcostat.analysis", "sarcostat.export")


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(PACKAGE.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imports(path: Path) -> list[tuple[str, bool]]:
    """``(absolute module name, at module level)`` of every import in ``path``."""
    name = _module_name(path)
    package = name if path.name == "__init__.py" else name.rpartition(".")[0]
    found: list[tuple[str, bool]] = []

    def visit(node: ast.AST, top: bool) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            top = False
        if isinstance(node, ast.Import):
            found.extend((alias.name, top) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module or ""
            else:
                anchor = package.split(".")
                anchor = anchor[: len(anchor) - (node.level - 1)]
                base = ".".join([*anchor, *([node.module] if node.module else [])])
            if node.module or node.level == 0:
                found.append((base, top))
            else:
                found.extend((f"{base}.{alias.name}", top) for alias in node.names)
        for child in ast.iter_child_nodes(node):
            visit(child, top)

    visit(ast.parse(path.read_text(encoding="utf-8")), True)
    return found


def _within(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(name == p or name.startswith(p + ".") for p in prefixes)


def _sources(folder: str) -> list[Path]:
    return sorted((PACKAGE / folder).rglob("*.py"))


ENGINE_AND_IO = [*_sources("engine"), *_sources("io")]


def test_the_layers_exist() -> None:
    names = {p.relative_to(PACKAGE).as_posix() for p in ENGINE_AND_IO}
    assert {"engine/study.py", "engine/clock.py", "engine/reports.py", "io/study.py"} <= names
    assert (PACKAGE / "analysis.py").is_file() and (PACKAGE / "export.py").is_file()


@pytest.mark.parametrize("path", ENGINE_AND_IO, ids=lambda p: p.relative_to(PACKAGE).as_posix())
def test_engine_and_io_import_no_ui_or_tools(path: Path) -> None:
    for name, _top in _imports(path):
        assert not _within(name, UI_AND_TOOLS), name


@pytest.mark.parametrize("path", _sources("io"), ids=lambda p: p.relative_to(PACKAGE).as_posix())
def test_io_imports_no_analysis_export_or_engine(path: Path) -> None:
    for name, _top in _imports(path):
        assert not _within(name, ("sarcostat.analysis", "sarcostat.export", "sarcostat.engine"))


@pytest.mark.parametrize("module", ["study.py", "clock.py"])
def test_study_engine_imports_no_metric_code(module: str) -> None:
    for name, _top in _imports(PACKAGE / "engine" / module):
        assert not _within(name, METRICS), name


def test_reports_load_metric_code_only_inside_functions() -> None:
    for name, top in _imports(PACKAGE / "engine" / "reports.py"):
        if top:
            assert not _within(name, METRICS), name


@pytest.mark.parametrize(
    "module, allowed",
    [
        ("analysis.py", ("sarcostat.core", "sarcostat.io")),
        ("export.py", ("sarcostat.core", "sarcostat.io", "sarcostat.analysis")),
    ],
)
def test_analysis_and_export_import_core_and_io_only(module: str, allowed) -> None:
    for name, _top in _imports(PACKAGE / module):
        assert not _within(name, ("pandas", "matplotlib", "PIL", "typer", "rich")), name
        if name.split(".")[0] == "sarcostat" and name != "sarcostat":
            assert _within(name, allowed), name


@pytest.mark.parametrize(
    "path",
    [*ENGINE_AND_IO, PACKAGE / "analysis.py", PACKAGE / "export.py"],
    ids=lambda p: p.relative_to(PACKAGE).as_posix(),
)
def test_scipy_is_imported_lazily(path: Path) -> None:
    for name, top in _imports(path):
        if _within(name, ("scipy",)):
            assert not top, name


def test_the_unreleased_loader_is_gone() -> None:
    assert not (PACKAGE / "io" / "loader.py").exists()


def test_no_summary_string_in_engine_or_analysis() -> None:
    paths = [*_sources("engine"), PACKAGE / "analysis.py"]
    offenders = [
        p.relative_to(PACKAGE).as_posix()
        for p in paths
        if "summary_string" in p.read_text(encoding="utf-8")
    ]
    assert offenders == []
