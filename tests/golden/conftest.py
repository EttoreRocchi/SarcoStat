"""Fixtures of the golden tests: the corpus builder ``tools/make_golden.py`` and the rule file."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "make_golden.py"
EXAMPLES_IMAGES = ROOT / "examples"
GOLDEN = Path(__file__).resolve().parent / "web"
RULE = GOLDEN / "tolerance.json"
RULE_OVERRIDE = "SARCOSTAT_GOLDEN_RULE"


@pytest.fixture(scope="session")
def make_golden():
    """The module ``tools/make_golden.py``."""
    if not TOOL.is_file():
        pytest.skip("tools/make_golden.py is not part of this tree")
    module = sys.modules.get("make_golden")
    if module is None:
        spec = importlib.util.spec_from_file_location("make_golden", TOOL)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["make_golden"] = module
        spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def rule(make_golden):
    """The comparison rule: ``tests/golden/web/tolerance.json``, or the file named by
    ``SARCOSTAT_GOLDEN_RULE`` (to try a proposal before the USER approves it)."""
    override = os.environ.get(RULE_OVERRIDE)
    path = Path(override) if override else RULE
    if not path.is_file():
        pytest.skip(
            f"{path.name} is missing: the tolerance file waits for the USER's approval (plan 3.13)"
        )
    return make_golden.load_rule(path)
