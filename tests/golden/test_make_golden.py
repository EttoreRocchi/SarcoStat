"""Tests for tools/make_golden.py: refusals of ill-conditioned means, corpus coverage, rule and comparison."""

from __future__ import annotations

import json
import math
import struct
import zlib

import pytest

from sarcostat.analysis import analyze_document

from .conftest import GOLDEN


def _chain_case(make_golden, name, midpoints):
    lines = [[[x, y - 10.0], [x, y + 10.0]] for x, y in midpoints]
    doc = make_golden.document(
        make_golden.TEM1, [("chain", "chain", {"z_lines": lines})], nm_per_px=6.62
    )
    return make_golden.Case(name, "test", doc)


def _triangle(delta: float):
    """Midpoints of three 600 px steps at 0, 120 + delta and 240 degrees (heading R about delta / 3)."""
    points = [(300.0, 300.0)]
    for angle in (0.0, 120.0 + math.degrees(delta), 240.0):
        x, y = points[-1]
        a = math.radians(angle)
        points.append(
            (round(64 * (x + 600 * math.cos(a))) / 64, round(64 * (y + 600 * math.sin(a))) / 64)
        )
    return points


@pytest.mark.filterwarnings("ignore:Data appears uniform")
class TestRefusals:
    def test_refuses_a_set_with_r_5e_4(self, make_golden, tmp_path):
        case = _chain_case(make_golden, "low_r", _triangle(1.5e-3))
        (chain,) = analyze_document(case.doc).images[0].chains
        assert 4e-4 < chain.metrics.mean_vector_length < 6e-4
        out = tmp_path / "out"
        with pytest.raises(make_golden.RefusedCorpus, match=r"mean resultant length .* < 0\.001"):
            make_golden.write_corpus([case], out)
        assert not out.exists()

    def test_refuses_a_set_with_a_mean_within_1e_9_of_pi(self, make_golden, tmp_path):
        case = _chain_case(make_golden, "near_pi", [(900.0, 300.0), (600.0, 300.0), (300.0, 300.0)])
        (chain,) = analyze_document(case.doc).images[0].chains
        assert abs(abs(chain.metrics.circular_mean) - math.pi) < 1e-9
        with pytest.raises(make_golden.RefusedCorpus, match=r"within 1e-09 of \+-pi"):
            make_golden.write_corpus([case], tmp_path / "out")
        assert not (tmp_path / "out").exists()

    def test_refusal_stops_the_whole_set(self, make_golden, tmp_path):
        good = make_golden.build_cases()[0]
        bad = _chain_case(make_golden, "low_r", _triangle(1.5e-3))
        with pytest.raises(make_golden.RefusedCorpus):
            make_golden.write_corpus([good, bad], tmp_path / "out")
        assert not (tmp_path / "out").exists()

    def test_main_exits_1_on_refusal(self, make_golden, tmp_path, monkeypatch, capsys):
        bad = _chain_case(make_golden, "near_pi", [(900.0, 300.0), (600.0, 300.0), (300.0, 300.0)])
        monkeypatch.setattr(make_golden, "build_cases", lambda: [bad])
        assert make_golden.main(["--out", str(tmp_path / "out")]) == 1
        assert "refused golden corpus" in capsys.readouterr().err

    @pytest.mark.parametrize(
        ("metrics", "message"),
        [
            ({"mean_phase": math.pi - 5e-10, "register_index": 0.9}, "pi"),
            ({"mean_phase": -math.pi + 5e-10, "register_index": 0.9}, "pi"),
            ({"mean_phase": 0.3, "register_index": 9e-4}, "0.001"),
        ],
    )
    def test_register_phases_are_checked(self, make_golden, metrics, message):
        results = {"images": [{"chains": [], "register": [{"metrics": metrics}]}]}
        (problem,) = make_golden.ill_conditioned(results)
        assert problem.startswith("images[0].register[0].metrics.mean_phase") and message in problem

    def test_turning_means_are_checked_and_skipped_items_ignored(self, make_golden):
        metrics = {
            "circular_mean": 0.5,
            "mean_vector_length": 0.9,
            "turning_mean": 1.0,
            "turning_mvl": 2e-4,
        }
        results = {
            "images": [
                {
                    "chains": [{"metrics": None}, {"metrics": metrics}],
                    "register": [{"metrics": None}],
                }
            ]
        }
        (problem,) = make_golden.ill_conditioned(results)
        assert problem.startswith("images[0].chains[1].metrics.turning_mean")


class TestCorpus:
    def test_corpus_is_well_conditioned(self, make_golden):
        for name in json.loads((GOLDEN / "index.json").read_text())["goldens"]:
            payload = json.loads((GOLDEN / f"{name['name']}.expected.json").read_text())
            assert make_golden.ill_conditioned(payload) == []

    def test_corpus_covers_the_plan_list(self):
        """M2-5 #1: classes, M-skipped shape, scales, click errors, ids, magnitudes, chains, pairs."""
        index = json.loads((GOLDEN / "index.json").read_text())["goldens"]
        docs = {g["name"]: json.loads((GOLDEN / f"{g['name']}.json").read_text()) for g in index}
        results = {
            g["name"]: json.loads((GOLDEN / f"{g['name']}.expected.json").read_text())["images"][0]
            for g in index
        }
        sarcomeres = [s for image in results.values() for s in image["sarcomeres"]]
        classes = {s["geometry"]["shape_classification"] for s in sarcomeres if s["geometry"]}
        assert classes == {"square", "rectangular", "barrel", "hourglass"}
        assert any(s["skip_reason"] == "m_line_skipped" for s in sarcomeres)
        sources = {image["settings"]["scale_source"] for image in results.values()}
        assert sources == {"gui", "image", "none"}
        assert any(s["class_stability"] for s in sarcomeres)
        assert any("estimate_click_error" in g["checks"] for g in index)
        ids = [
            (s["item_id"], s["sarcomere_id"])
            for s in results["shape_deleted_then_added"]["sarcomeres"]
        ]
        assert ids == [("sarcomere-1", 1), ("sarcomere-3", 2), ("sarcomere-4", 3)]
        for name, unit in (("large_px", "px"), ("large_nm", "nm")):
            image = results[name]
            coords = [
                p[0]
                for item in docs[name]["items"]
                for value in item["points"].values()
                for p in _flat_points(value)
            ]
            assert max(coords) > 4096 and image["settings"]["unit"] == unit
            lengths = [
                s["geometry"]["sarcomere_length"] for s in image["sarcomeres"] if s["geometry"]
            ]
            lengths += [c["metrics"]["total_length"] for c in image["chains"]]
            assert max(lengths) > 4096
        chains = [c for image in results.values() for c in image["chains"]]
        with_reference = {
            c["n_z_lines"] for c in chains if c["metrics"]["expected_direction"] is not None
        }
        assert {5, 20} <= with_reference
        for c in chains:
            heading = math.degrees(c["metrics"]["circular_mean"]) % 90.0
            assert 1.0 < heading < 89.0
        pairs = [p for image in results.values() for p in image["register"]]
        assert any(p["skip_reason"] == "no_overlap" for p in pairs)
        assert any(p["metrics"] for p in pairs)
        assert results["chain_only_notice"]["notices"][0]["code"] == "register_needs_pair_items"

    def test_synthetic_png_is_a_valid_black_greyscale_image(self, make_golden):
        data = b"".join(make_golden.synthetic_png(300, 7))
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        chunks, offset = [], 8
        while offset < len(data):
            (size,) = struct.unpack(">I", data[offset : offset + 4])
            kind, body = data[offset + 4 : offset + 8], data[offset + 8 : offset + 8 + size]
            (crc,) = struct.unpack(">I", data[offset + 8 + size : offset + 12 + size])
            assert crc == zlib.crc32(kind + body)
            chunks.append((kind, body))
            offset += 12 + size
        assert [k for k, _ in chunks] == [b"IHDR", b"IDAT", b"IEND"]
        assert struct.unpack(">IIBBBBB", chunks[0][1]) == (300, 7, 8, 0, 0, 0, 0)
        assert zlib.decompress(chunks[1][1]) == bytes(7 * 301)

    def test_synthetic_image_digest_is_recorded(self, make_golden):
        doc = json.loads((GOLDEN / "large_px.json").read_text())
        assert doc["images"][0]["sha256"] == make_golden.WIDE.sha256()


def _flat_points(value):
    if isinstance(value, list) and len(value) == 2 and all(isinstance(v, float) for v in value):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _flat_points(item)


class TestRuleAndCompare:
    RULE = {
        "rule": "r",
        "rel_default": 1e-12,
        "rel_special": 1e-9,
        "special": ["a[*].p"],
        "approved": None,
    }

    def test_numbers_follow_the_rule(self, make_golden):
        expected = {"a": [{"p": 2.0, "q": 2.0}, {"p": 0.5, "q": 1e-3}]}
        ok = {"a": [{"p": 2.0 + 1.5e-9, "q": 2.0 + 1.5e-12}, {"p": 0.5 + 9e-10, "q": 1e-3 + 9e-13}]}
        comparison = make_golden.compare(expected, ok, self.RULE)
        assert comparison.ok, comparison.problems
        assert comparison.fields["a[*].p"].rel == 1e-9 and comparison.fields["a[*].q"].rel == 1e-12
        assert comparison.fields["a[*].q"].count == 2
        bad = {"a": [{"p": 2.0, "q": 2.0 + 3e-12}, {"p": 0.5 + 2e-9, "q": 1e-3}]}
        problems = make_golden.compare(expected, bad, self.RULE).problems
        assert [p.split(":")[0] for p in problems] == ["$.a[0].q", "$.a[1].p"]

    @pytest.mark.parametrize(
        ("expected", "actual"),
        [
            ({"x": 1}, {"y": 1}),
            ({"x": [1, 2]}, {"x": [1]}),
            ({"x": None}, {"x": 0.0}),
            ({"x": 1.0}, {"x": None}),
            ({"x": True}, {"x": 1}),
            ({"x": "a"}, {"x": "b"}),
        ],
    )
    def test_structure_must_match(self, make_golden, expected, actual):
        assert not make_golden.compare(expected, actual, self.RULE).ok

    @pytest.mark.parametrize(
        "change",
        [
            {"extra": 1},
            {"special": ["a..b"]},
            {"special": ["a[0].b"]},
            {"special": ["a", "a"]},
            {"rel_default": -1},
            {"rel_special": True},
            {"approved": 3},
        ],
    )
    def test_load_rule_rejects_bad_files(self, make_golden, tmp_path, change):
        path = tmp_path / "tolerance.json"
        path.write_text(json.dumps({**self.RULE, **change}))
        with pytest.raises(ValueError):
            make_golden.load_rule(path)

    def test_load_rule_accepts_a_good_file(self, make_golden, tmp_path):
        path = tmp_path / "tolerance.json"
        path.write_text(make_golden.canonical_json(self.RULE))
        assert make_golden.load_rule(path) == self.RULE
