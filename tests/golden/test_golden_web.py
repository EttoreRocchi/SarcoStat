"""The golden corpus of the web parity tests, recomputed by this CPython under the governed rule.

The expected files were computed with numpy 2.4; recomputing them here with another numpy (1.26
in the development environment) is the cross-version check, and it is blocking for every field,
the ``special`` ones (MVL belt, scipy special functions) included.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from sarcostat.analysis import analyze_document, estimate_click_error
from sarcostat.export import results_json
from sarcostat.io.annotations import dumps_annotations, read_annotations

from .conftest import EXAMPLES_IMAGES, GOLDEN, RULE

PINNED_SHA256 = "2fbbe20a4dee23af1a9bafdc756ce4988e7334d7e6af35a1f88d8dd94e01611d"
INDEX = json.loads((GOLDEN / "index.json").read_text(encoding="utf-8"))
NAMES = [entry["name"] for entry in INDEX["goldens"]]
CHECKED = {entry["name"]: entry["checks"] for entry in INDEX["goldens"]}
TOLERANCE_WORDS = {"tolerance", "rel", "rel_default", "rel_special", "special", "rtol", "atol"}


def _keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def _paths(value, path=""):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _paths(item, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for item in value:
            yield from _paths(item, f"{path}[*]")
    else:
        yield path


class TestCorpusFiles:
    def test_index_lists_every_file(self):
        assert INDEX["format"] == "sarcostat-golden-index" and len(NAMES) == len(set(NAMES))
        expected = {"index.json"} | {f"{n}{s}" for n in NAMES for s in (".json", ".expected.json")}
        present = {p.name for p in GOLDEN.iterdir()} - {RULE.name}
        assert present == expected

    @pytest.mark.parametrize("name", NAMES)
    def test_inputs_are_canonical_v1_documents_of_one_image(self, name):
        path = GOLDEN / f"{name}.json"
        doc = read_annotations(path)
        assert dumps_annotations(doc) == path.read_text(encoding="ascii")
        (image,) = doc["images"]
        local = EXAMPLES_IMAGES / image["file_name"]
        if image["file_name"].startswith("synthetic_"):
            assert not local.exists()
        else:
            assert hashlib.sha256(local.read_bytes()).hexdigest() == image["sha256"]

    @pytest.mark.parametrize("name", NAMES)
    def test_no_golden_file_carries_a_tolerance(self, name):
        for suffix in (".json", ".expected.json"):
            payload = json.loads((GOLDEN / f"{name}{suffix}").read_text(encoding="ascii"))
            assert not set(_keys(payload)) & TOLERANCE_WORDS

    @pytest.mark.parametrize("name", NAMES)
    def test_expected_is_canonical_results_json(self, name):
        text = (GOLDEN / f"{name}.expected.json").read_text(encoding="ascii")
        payload = json.loads(text)
        again = json.dumps(
            payload, sort_keys=True, indent=2, separators=(",", ": "), ensure_ascii=True
        )
        assert text == again + "\n"
        assert payload["format"] == "sarcostat-results" and len(payload["images"]) == 1


class TestRecomputed:
    @pytest.mark.parametrize("name", NAMES)
    def test_cpython_matches_the_expected_values(self, name, rule, make_golden):
        doc = read_annotations(GOLDEN / f"{name}.json")
        actual = json.loads(results_json(analyze_document(doc)))
        expected = json.loads((GOLDEN / f"{name}.expected.json").read_text(encoding="ascii"))
        comparison = make_golden.compare(expected, actual, rule)
        assert comparison.ok, comparison.problems[:20]

    @pytest.mark.parametrize("name", [n for n in NAMES if "estimate_click_error" in CHECKED[n]])
    def test_click_error_is_the_estimate_of_the_repeats(self, name, rule, make_golden):
        doc = read_annotations(GOLDEN / f"{name}.json")
        est = estimate_click_error(doc, doc["images"][0]["image_id"])
        actual = {
            "click_sigma_z": est.click_sigma_z,
            "click_sigma_m": est.click_sigma_m,
            "click_perp_ratio": est.click_perp_ratio,
        }
        expected = {key: doc["gui"][key] for key in actual}
        comparison = make_golden.compare(expected, actual, rule)
        assert comparison.ok, comparison.problems

    def test_the_builder_reproduces_the_corpus(self, rule, make_golden):
        comparison = make_golden.check(GOLDEN, rule, make_golden.build_cases())
        assert comparison.ok, comparison.problems[:20]

    def test_every_special_path_occurs_in_the_corpus(self, rule):
        seen = set()
        for name in NAMES:
            seen |= set(_paths(json.loads((GOLDEN / f"{name}.expected.json").read_text())))
        assert set(rule["special"]) <= seen


class TestRuleFile:
    def _text(self) -> str:
        if not RULE.is_file():
            pytest.skip(f"{RULE.name} is missing: it waits for the USER's approval (plan 3.13)")
        return RULE.read_text(encoding="ascii")

    def test_rule_file_is_pinned(self):
        assert hashlib.sha256(self._text().encode("ascii")).hexdigest() == PINNED_SHA256

    def test_rule_file_is_canonical_and_valid(self, make_golden):
        text = self._text()
        rule = make_golden.load_rule(RULE)
        assert make_golden.canonical_json(rule) == text
        assert (rule["rel_default"], rule["rel_special"]) == (1e-12, 1e-9)
        assert rule["special"] == sorted(rule["special"])

    def test_moving_a_field_into_or_out_of_special_changes_the_pin(self, make_golden):
        rule = json.loads(self._text())

        def pin(document) -> str:
            text = make_golden.canonical_json(document)
            return hashlib.sha256(text.encode("ascii")).hexdigest()

        assert pin(rule) == PINNED_SHA256
        assert rule["special"], "the special list is empty"
        out = dict(rule, special=rule["special"][1:])
        into = dict(
            rule, special=sorted([*rule["special"], "images[*].chains[*].metrics.rayleigh_p"])
        )
        assert pin(out) != PINNED_SHA256
        assert pin(into) != PINNED_SHA256


def test_paths_of_the_corpus_follow_the_rule_syntax(make_golden):
    for name in NAMES:
        payload = json.loads((GOLDEN / f"{name}.expected.json").read_text())
        for path in _paths(payload):
            assert make_golden.PATH_RE.fullmatch(path), path
