"""Tests for sarcostat.io.provenance: the source digest and provenance blocks."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import sarcostat
from sarcostat.io import provenance as prov
from sarcostat.io.provenance import provenance, source_digest

PACKAGE = Path(sarcostat.__file__).resolve().parent
ABC_SHA256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def _copy_package(dest: Path) -> Path:
    root = dest / "sarcostat"
    shutil.copytree(PACKAGE, root, ignore=shutil.ignore_patterns("__pycache__", ".omc"))
    return root


class TestSourceDigest:
    def test_format(self):
        assert re.fullmatch(r"[0-9a-f]{64}", source_digest())

    def test_selected_files(self):
        files = [rel for rel, _path in prov._source_files(PACKAGE)]
        assert files == sorted(files)
        for expected in (
            "__init__.py",
            "py.typed",
            "core/shape_uncertainty.py",
            "io/annotation-v1.schema.json",
            "gui/logo.png",
            "simulation/config.yaml",
            "simulation/sarcomere_config.yaml",
        ):
            assert expected in files
        for rel in files:
            parts = rel.split("/")
            assert "__pycache__" not in parts and ".omc" not in parts, rel
            assert parts[0] not in ("tests", "docs", "paper"), rel
            assert re.search(r"(\.py|\.yaml|^io/[^/]+\.json|^gui/[^/]+\.png|py\.typed)$", rel), rel

    @pytest.mark.needs_cpython(reason="runs sha256sum in a subprocess")
    def test_matches_sha256sum_recipe(self):
        """The digest is the SHA-256 of what ``sha256sum`` prints for the files, sorted by path."""
        sha256sum = shutil.which("sha256sum")
        if sha256sum is None:
            pytest.skip("sha256sum not available")
        files = [rel for rel, _path in prov._source_files(PACKAGE)]
        listing = subprocess.run(
            [sha256sum, "--", *files], cwd=PACKAGE, check=True, capture_output=True
        ).stdout
        assert hashlib.sha256(listing).hexdigest() == source_digest()

    @pytest.mark.needs_cpython(reason="runs bash, find, sort, xargs and sha256sum in a subprocess")
    def test_shell_recipe_in_the_docstring(self):
        """The find/sha256sum pipeline documented in the module gives the same digest."""
        if not all(shutil.which(t) for t in ("bash", "find", "sort", "xargs", "sha256sum")):
            pytest.skip("POSIX tools not available")
        block = prov.__doc__.split("::\n", 1)[1].strip("\n").split("\n\n", 1)[0]
        lines = [line.strip() for line in block.splitlines()]
        assert lines[0].startswith("cd ")
        command = " ".join(lines[1:]).replace("\\ ", " ")
        out = subprocess.run(
            ["bash", "-c", command], cwd=PACKAGE, check=True, capture_output=True, text=True
        ).stdout
        assert out.split()[0] == source_digest()

    def test_root_argument_gives_the_same_digest_on_a_copy(self, tmp_path):
        assert source_digest(_copy_package(tmp_path)) == source_digest()

    def test_ignores_caches_tool_folders_and_other_files(self, tmp_path):
        root = _copy_package(tmp_path)
        before = source_digest(root)
        junk = {
            ".omc/state/sessions/x.json": "{}",
            ".omc/notes.py": "x = 1\n",
            "core/.omc/plan.py": "x = 1\n",
            "__pycache__/x.cpython-310.pyc": "bytecode",
            "core/__pycache__/shape_uncertainty.cpython-313.pyc": "bytecode",
            "core/stray.pyc": "bytecode",
            "sarcostat-0.2.0.dist-info/RECORD.py": "x = 1\n",
            "README.txt": "text",
            "gui/settings.json": "{}",
            "io/sample.png": "png",
            "simulation/extra.json": "{}",
            ".hidden/module.py": "x = 1\n",
        }
        for rel, text in junk.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text)
        assert source_digest(root) == before

    @pytest.mark.parametrize(
        "rel",
        [
            "core/shape_uncertainty.py",
            "core/new_module.py",
            "simulation/config.yaml",
            "io/annotation-v1.schema.json",
            "io/other.json",
            "gui/logo.png",
            "gui/icon.png",
            "py.typed",
        ],
    )
    def test_changes_with_any_source_file(self, tmp_path, rel):
        root = _copy_package(tmp_path)
        before = source_digest(root)
        path = root / rel
        path.write_bytes((path.read_bytes() if path.exists() else b"") + b"\n")
        assert source_digest(root) != before

    def test_renaming_a_file_changes_the_digest(self, tmp_path):
        root = _copy_package(tmp_path)
        before = source_digest(root)
        (root / "core" / "register.py").rename(root / "core" / "register2.py")
        assert source_digest(root) != before

    def test_root_must_be_a_package(self, tmp_path):
        with pytest.raises(ValueError, match="__init__.py"):
            source_digest(tmp_path)

    @pytest.mark.needs_cpython(reason="runs a subprocess; Emscripten has no processes")
    def test_computing_imports_nothing_and_survives_importing_everything(self):
        code = (
            "import sys, pkgutil, importlib\n"
            "import sarcostat, sarcostat.io.provenance as p\n"
            "before = set(sys.modules)\n"
            "d1 = p.source_digest()\n"
            "assert set(sys.modules) == before, sorted(set(sys.modules) - before)\n"
            "for m in pkgutil.walk_packages(sarcostat.__path__, 'sarcostat.'):\n"
            "    try:\n"
            "        importlib.import_module(m.name)\n"
            "    except Exception as exc:\n"
            "        print('skipped', m.name, type(exc).__name__)\n"
            "print(d1 == p.source_digest())\n"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], check=True, capture_output=True, text=True
        ).stdout
        assert out.strip().splitlines()[-1] == "True"


class TestProvenance:
    def test_block(self, tmp_path):
        (tmp_path / "abc.tif").write_bytes(b"abc")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "empty.json").write_bytes(b"")
        block = provenance(
            [tmp_path / "abc.tif", str(tmp_path / "sub" / "empty.json")],
            parameters={"nm_per_px": 4.39, "tolerance": 0.05},
        )
        assert block == {
            "sarcostat_version": sarcostat.__version__,
            "source_digest": source_digest(),
            "files": [
                {"file_name": "abc.tif", "sha256": ABC_SHA256},
                {"file_name": "empty.json", "sha256": hashlib.sha256(b"").hexdigest()},
            ],
            "parameters": {"nm_per_px": 4.39, "tolerance": 0.05},
        }

    def test_defaults(self):
        block = provenance()
        assert block["files"] == [] and block["parameters"] == {}

    def test_has_no_absolute_paths(self, tmp_path):
        (tmp_path / "image.png").write_bytes(b"x")
        text = json.dumps(provenance([tmp_path / "image.png"], parameters={"image": "image.png"}))
        assert str(tmp_path) not in text and "image.png" in text

    @pytest.mark.parametrize(
        "parameters",
        [
            {"image": "/home/user/image.png"},
            {"nested": {"list": ["ok", "C:\\Users\\me\\image.png"]}},
            {"unc": "\\\\server\\share\\image.png"},
            {"path": Path("/tmp/image.png")},
        ],
    )
    def test_rejects_absolute_paths_in_parameters(self, parameters):
        with pytest.raises(ValueError, match="absolute path"):
            provenance(parameters=parameters)

    def test_parameters_are_copied(self):
        params = {"grid": [1, 2]}
        block = provenance(parameters=params)
        params["grid"].append(3)
        assert block["parameters"] == {"grid": [1, 2]}

    def test_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            provenance([tmp_path / "absent.png"])
