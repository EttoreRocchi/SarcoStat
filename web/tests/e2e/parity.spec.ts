import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import { WEB_DIR } from "../../scripts/lane.mjs";
import { EngineCallError, expect, test } from "./fixtures.ts";
import {
  CLICK_ERROR_KEYS,
  IMAGE_KEYS,
  compareJson,
  mergeFields,
  parseGoldenIndex,
  parseRule,
  rankFields,
  type FieldStats,
} from "./hooks/parity_rule.ts";

const GOLDEN_DIR = path.resolve(WEB_DIR, "..", "tests", "golden", "web");
const RULE_FILE = path.join(GOLDEN_DIR, "tolerance.json");
const INDEX_FILE = path.join(GOLDEN_DIR, "index.json");

interface ModeState {
  notice: { code: string } | null;
  settings: Record<string, unknown>;
}

interface GoldenOutcome {
  name: string;
  ok: boolean;
  numbers: number;
  max_scaled: number;
  byte_equal_to_expected: boolean;
  calls: { cmd: string; ms: number }[];
  seconds: number;
}

function sha256(bytes: Buffer): string {
  return createHash("sha256").update(bytes).digest("hex");
}

test("the app's engine worker reproduces every golden under tests/golden/web/tolerance.json (AC-W1)", async ({ page, engine, recorder, baseURL }) => {
  test.setTimeout(240_000);
  const ruleBytes = fs.readFileSync(RULE_FILE);
  const rule = parseRule(ruleBytes.toString("utf8"));
  const goldens = parseGoldenIndex(fs.readFileSync(INDEX_FILE, "utf8"), fs.readdirSync(GOLDEN_DIR));
  expect(goldens.length).toBeGreaterThan(0);

  const started = Date.now();
  await page.goto("./");
  await engine.ready();
  const identity = await engine.call<Record<string, unknown>>("sys.identity", {});
  const buildResponse = await page.request.get(new URL("BUILD.json", baseURL).href);
  expect(buildResponse.ok()).toBe(true);
  const build = (await buildResponse.json()) as { version: string; wheel: { file: string; sha256: string }; source_digest: string; pyodide: { version: string } };
  expect(identity).toMatchObject({ source_digest: build.source_digest, bridge_version: 1 });
  const scipy = await engine.call<{ scipy_version: string }>("sys.load_scipy", {}, 120_000);

  const fields = new Map<string, FieldStats>();
  const problems: string[] = [];
  const outcomes: GoldenOutcome[] = [];
  for (const entry of goldens) {
    await test.step(entry.name, async () => {
      const text = fs.readFileSync(path.join(GOLDEN_DIR, `${entry.name}.json`), "utf8");
      const expectedText = fs.readFileSync(path.join(GOLDEN_DIR, `${entry.name}.expected.json`), "utf8");
      const doc = JSON.parse(text) as { images: Record<string, unknown>[]; gui?: Record<string, unknown> };
      const before = problems.length;
      const goldenFields = new Map<string, FieldStats>();
      const t0 = Date.now();
      await recorder.clear();
      let exportedText = "";
      try {
        if (!Array.isArray(doc.images) || doc.images.length !== 1) {
          throw new Error("the bridge opens annotation files of one image only");
        }
        const first = doc.images[0] as Record<string, unknown>;
        const image = Object.fromEntries(IMAGE_KEYS.map((key) => [key, first[key]]));
        const loaded = await engine.call<ModeState>("doc.load_v1", { text, image, confirm: false });
        if (loaded.notice?.code !== "document_loaded") {
          problems.push(`${entry.name}: doc.load_v1: notice ${JSON.stringify(loaded.notice)}, expected document_loaded`);
        }
        const exported = await engine.call<{ file_name: string; text: string }>("doc.export", { what: "results_json" });
        exportedText = exported.text;
        compareJson(JSON.parse(expectedText), JSON.parse(exportedText), rule, goldenFields, problems, entry.name);
        if (entry.checks.includes("estimate_click_error")) {
          const state = await engine.call<ModeState>("doc.estimate_click_error", {});
          if (state.notice?.code !== "click_error_estimated") {
            problems.push(`${entry.name}: doc.estimate_click_error: notice ${JSON.stringify(state.notice)}, expected click_error_estimated`);
          }
          const actual = Object.fromEntries(CLICK_ERROR_KEYS.map((key) => [key, state.settings[key]]));
          const expected = Object.fromEntries(CLICK_ERROR_KEYS.map((key) => [key, doc.gui?.[key]]));
          compareJson({ estimate_click_error: expected }, { estimate_click_error: actual }, rule, goldenFields, problems, entry.name);
        }
      } catch (error) {
        problems.push(`${entry.name}: ${error instanceof EngineCallError || error instanceof Error ? error.message : String(error)}`);
      }
      const worst = mergeFields(fields, goldenFields);
      const numbers = [...goldenFields.values()].reduce((sum, stats) => sum + stats.n, 0);
      const calls = (await recorder.calls("test")).map((call) => ({ cmd: call.cmd, ms: Number((call.ms ?? -1).toFixed(1)) }));
      const ok = problems.length === before;
      outcomes.push({
        name: entry.name,
        ok,
        numbers,
        max_scaled: worst,
        byte_equal_to_expected: exportedText === expectedText,
        calls,
        seconds: Number(((Date.now() - t0) / 1000).toFixed(3)),
      });
      console.log(`${ok ? "ok  " : "FAIL"} ${entry.name}: ${numbers} numbers, max scaled difference ${worst.toExponential(3)}, ${calls.map((call) => `${call.cmd} ${call.ms} ms`).join(", ")}`);
    });
  }

  const ranked = rankFields(fields);
  const passed = outcomes.filter((outcome) => outcome.ok).length;
  const report = {
    tool: "web/tests/e2e/parity.spec.ts",
    via: "the app's engine worker in the served page (injected recorder)",
    result: problems.length === 0 && passed === goldens.length ? "PASS" : "FAIL",
    app_url: baseURL ?? null,
    build: { version: build.version, wheel: build.wheel, source_digest: build.source_digest, pyodide: build.pyodide.version },
    bridge_identity: identity,
    scipy: scipy.scipy_version,
    rule: {
      file: path.relative(path.resolve(WEB_DIR, ".."), RULE_FILE),
      sha256: sha256(ruleBytes),
      rel_default: rule.rel_default,
      rel_special: rule.rel_special,
      special: rule.special,
      approved: rule.approved,
    },
    goldens: outcomes,
    fields: Object.fromEntries(ranked),
    rounding_boundary_cases: [],
    problems,
    seconds: Number(((Date.now() - started) / 1000).toFixed(1)),
  };
  const text = `${JSON.stringify(report, null, 2)}\n`;
  const file = path.join(test.info().project.outputDir, "parity.json");
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, text);
  await test.info().attach("parity.json", { body: text, contentType: "application/json" });

  const lines = ["", "=== SarcoStat parity in the browser ==="];
  lines.push(`app ${baseURL}; sarcostat ${String(identity.sarcostat_version)}; Pyodide ${build.pyodide.version}; scipy ${scipy.scipy_version}; bridge ${String(identity.bridge_version)}`);
  lines.push(`wheel sha256: ${build.wheel.sha256}  ${build.wheel.file}`);
  lines.push(`rule sha256: ${report.rule.sha256}  ${report.rule.file} (approved ${String(rule.approved)})`);
  lines.push(`goldens: ${passed}/${goldens.length} ok; byte-equal to the CPython expected text: ${outcomes.filter((outcome) => outcome.byte_equal_to_expected).length}/${goldens.length}`);
  lines.push("largest scaled differences |a-b|/max(1,|b|) (field path, allowed, n):");
  ranked.slice(0, 15).forEach(([key, stats]) => lines.push(`  ${stats.max_scaled.toExponential(3)}  ${String(stats.rel).padEnd(6)} ${String(stats.n).padStart(5)}  ${key}`));
  problems.slice(0, 40).forEach((line) => lines.push(`  ${line}`));
  lines.push(`parity.json: ${file}`);
  lines.push(`RESULT: ${report.result}`);
  console.log(lines.join("\n"));

  expect(problems).toEqual([]);
  expect(passed).toBe(goldens.length);
});
