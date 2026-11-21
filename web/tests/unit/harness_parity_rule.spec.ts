import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";

import { describe, expect, it } from "vitest";

import { WEB_DIR } from "../../scripts/lane.mjs";
import {
  compareJson,
  mergeFields,
  parseGoldenIndex,
  parseRule,
  rankFields,
  type FieldStats,
  type ToleranceRule,
} from "../e2e/hooks/parity_rule.ts";

const GOLDEN_DIR = path.resolve(WEB_DIR, "..", "tests", "golden", "web");
const RULE_FILE = path.join(GOLDEN_DIR, "tolerance.json");
const PARITY_MJS = path.join(WEB_DIR, "tests", "pyodide", "parity.mjs");

const RULE: ToleranceRule = {
  approved: "D-000",
  rel_default: 1e-12,
  rel_special: 1e-9,
  rule: "test",
  special: ["images[*].chains[*].metrics.mvl_ci[*]"],
  specialSet: new Set(["images[*].chains[*].metrics.mvl_ci[*]"]),
};

type Compare = (expected: unknown, actual: unknown, rule: ToleranceRule, fields: Map<string, FieldStats>, problems: string[], label: string) => void;

function run(compare: Compare, expected: unknown, actual: unknown, rule: ToleranceRule = RULE): { problems: string[]; fields: Record<string, FieldStats> } {
  const fields = new Map<string, FieldStats>();
  const problems: string[] = [];
  compare(expected, actual, rule, fields, problems, "g");
  return { problems, fields: Object.fromEntries(fields) };
}

function parityMjsCompare(): Compare {
  const source = fs.readFileSync(PARITY_MJS, "utf8");
  const start = source.indexOf("function kind(value)");
  const end = source.indexOf("function readIndex(");
  if (start < 0 || end < start || !source.slice(start, end).includes("function compare(")) {
    throw new Error("parity.mjs no longer defines kind() and compare() before readIndex(); update this cross-check");
  }
  return vm.runInContext(`${source.slice(start, end)}\ncompare`, vm.createContext({})) as Compare;
}

function perturb(value: unknown, rel: number, counter: { n: number }): unknown {
  if (typeof value === "number") {
    counter.n += 1;
    const factor = [0, 0.5, 2, -0.9, 1500][counter.n % 5] as number;
    return value + factor * rel * Math.max(1, Math.abs(value));
  }
  if (Array.isArray(value)) {
    const out = value.map((item) => perturb(item, rel, counter));
    counter.n += 1;
    return counter.n % 37 === 0 ? out.slice(1) : out;
  }
  if (value !== null && typeof value === "object") {
    counter.n += 1;
    const entries = Object.entries(value).map(([key, item]) => [key, perturb(item, rel, counter)] as const);
    if (counter.n % 41 === 0 && entries.length > 0) {
      const [key, item] = entries[0] as readonly [string, unknown];
      entries[0] = [`${key}_renamed`, item];
    }
    return Object.fromEntries(entries);
  }
  if (typeof value === "string") {
    counter.n += 1;
    return counter.n % 23 === 0 ? `${value}!` : value;
  }
  return value;
}

describe("parity rule of the Playwright parity spec", () => {
  it("reads tests/golden/web/tolerance.json with the checks of parity.mjs", () => {
    const rule = parseRule(fs.readFileSync(RULE_FILE, "utf8"));
    expect(rule.special.length).toBeGreaterThan(0);
    expect([...rule.specialSet].sort()).toEqual([...rule.special].sort());
    expect(rule.rel_default).toBeLessThanOrEqual(rule.rel_special);
    const good = JSON.stringify({ approved: null, rel_default: 1e-12, rel_special: 1e-9, rule: "r", special: ["a.b[*].c"] });
    expect(parseRule(good).approved).toBeNull();
    const bad = (patch: Record<string, unknown>): string => JSON.stringify({ ...JSON.parse(good), ...patch });
    expect(() => parseRule(bad({ extra: 1 }))).toThrow(/keys must be exactly/);
    expect(() => parseRule(bad({ rel_default: -1 }))).toThrow(/rel_default/);
    expect(() => parseRule(bad({ special: ["a..b"] }))).toThrow(/special must be a list of paths/);
    expect(() => parseRule(bad({ special: ["a", "a"] }))).toThrow(/repeats/);
    expect(() => parseRule(bad({ approved: 48 }))).toThrow(/approved/);
  });

  it("reads the golden index and refuses a folder with unlisted or missing files", () => {
    const files = fs.readdirSync(GOLDEN_DIR);
    const goldens = parseGoldenIndex(fs.readFileSync(path.join(GOLDEN_DIR, "index.json"), "utf8"), files);
    expect(goldens.length).toBe(files.filter((name) => name.endsWith(".expected.json")).length);
    expect(goldens.length).toBeGreaterThanOrEqual(12);
    expect(goldens.some((entry) => entry.checks.includes("estimate_click_error"))).toBe(true);
    const index = fs.readFileSync(path.join(GOLDEN_DIR, "index.json"), "utf8");
    expect(() => parseGoldenIndex(index, [...files, "stray.json"])).toThrow(/not listed in index.json: stray.json/);
    expect(() => parseGoldenIndex(index, files.filter((name) => name !== "large_px.expected.json"))).toThrow(/missing: large_px.expected.json/);
  });

  it("applies |a - b| <= rel * max(1, |b|), rel_special on the listed paths, and equality elsewhere", () => {
    const expected = { images: [{ chains: [{ metrics: { mvl_ci: [0.5, 2000], n: 3, ok: true, label: "x", none: null } }] }] };
    const within = { images: [{ chains: [{ metrics: { mvl_ci: [0.5 + 5e-10, 2000 * (1 + 5e-10)], n: 3 + 9e-13, ok: true, label: "x", none: null } }] }] };
    const result = run(compareJson, expected, within);
    expect(result.problems).toEqual([]);
    expect(Object.keys(result.fields).sort()).toEqual(["images[*].chains[*].metrics.mvl_ci[*]", "images[*].chains[*].metrics.n"]);
    expect(result.fields["images[*].chains[*].metrics.mvl_ci[*]"]).toMatchObject({ rel: 1e-9, n: 2 });
    expect(result.fields["images[*].chains[*].metrics.n"]?.rel).toBe(1e-12);
    const beyond = structuredClone(within);
    (beyond.images[0] as { chains: { metrics: { n: number } }[] }).chains[0]!.metrics.n = 3 + 6e-12;
    expect(run(compareJson, expected, beyond).problems).toEqual([expect.stringMatching(/^g: \$\.images\[0\]\.chains\[0\]\.metrics\.n: .* \(scaled \d\.\d{3}e-12 > 1e-12\)$/)]);
    expect(run(compareJson, { a: 1 }, { a: 1, b: 2 }).problems).toEqual(["g: $: keys differ"]);
    expect(run(compareJson, { a: [1, 2] }, { a: [1] }).problems).toEqual(["g: $.a: list lengths differ"]);
    expect(run(compareJson, { a: "x" }, { a: "y" }).problems).toEqual(['g: $.a: "y" vs expected "x"']);
    expect(run(compareJson, { a: null }, { a: 0 }).problems).toEqual(["g: $.a: 0 vs expected null"]);
    expect(run(compareJson, { a: 1 }, { a: "1" }).problems).toEqual(['g: $.a: expected the number 1, got "1"']);
    expect(run(compareJson, { a: true }, { a: false }).problems).toEqual(["g: $.a: false vs expected true"]);
  });

  it("gives the same problems and field statistics as parity.mjs on every golden, perturbed", () => {
    const reference = parityMjsCompare();
    const rule = parseRule(fs.readFileSync(RULE_FILE, "utf8"));
    const goldens = parseGoldenIndex(fs.readFileSync(path.join(GOLDEN_DIR, "index.json"), "utf8"), fs.readdirSync(GOLDEN_DIR));
    let problems = 0;
    for (const entry of goldens) {
      const expected: unknown = JSON.parse(fs.readFileSync(path.join(GOLDEN_DIR, `${entry.name}.expected.json`), "utf8"));
      for (const seed of [0, 1, 2]) {
        const actual = perturb(expected, rule.rel_default, { n: seed });
        const ours = run(compareJson, expected, actual, rule);
        const theirs = run(reference, expected, actual, rule);
        expect(ours, `${entry.name} seed ${seed}`).toEqual(theirs);
        problems += ours.problems.length;
      }
      expect(run(compareJson, expected, structuredClone(expected), rule).problems).toEqual([]);
    }
    expect(problems).toBeGreaterThan(0);
  });

  it("merges per-golden statistics and ranks fields by their largest scaled difference", () => {
    const total = new Map<string, FieldStats>();
    const one = new Map<string, FieldStats>([["a", { rel: 1e-12, n: 2, max_abs: 1e-14, max_scaled: 1e-14, worst: "g1: $.a" }]]);
    const two = new Map<string, FieldStats>([
      ["a", { rel: 1e-12, n: 1, max_abs: 3e-14, max_scaled: 3e-14, worst: "g2: $.a" }],
      ["b", { rel: 1e-9, n: 4, max_abs: 0, max_scaled: 0, worst: null }],
    ]);
    expect(mergeFields(total, one)).toBe(1e-14);
    expect(mergeFields(total, two)).toBe(3e-14);
    expect(total.get("a")).toEqual({ rel: 1e-12, n: 3, max_abs: 3e-14, max_scaled: 3e-14, worst: "g2: $.a" });
    expect(rankFields(total).map(([key]) => key)).toEqual(["a", "b"]);
  });
});
