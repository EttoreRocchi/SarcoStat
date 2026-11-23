import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import type { Download, Page } from "@playwright/test";

import { en } from "../../src/strings/en.ts";
import { fill } from "../../src/strings/format.ts";
import { readRef } from "../../src/view/results/model.ts";
import { CLICK_ERROR_KEYS, compareJson, parseGoldenIndex, parseRule, type FieldStats } from "./hooks/parity_rule.ts";
import { expect, test, type Recorder } from "./fixtures.ts";
import { boot, cpython, GOLDEN_DIR, goldenText, ITEMS_PANEL, jsonPayload, openGoldenImage, openPayload, RESULTS_PANEL, refTexts, type RefText } from "./workspace_view.ts";

const RULE_TEXT = fs.readFileSync(path.join(GOLDEN_DIR, "tolerance.json"), "utf8");
const RULE = parseRule(RULE_TEXT);
const SHAPE_GOLDEN = /^(shape_|large_)|^mixed_types$/;
const GOLDENS = parseGoldenIndex(fs.readFileSync(path.join(GOLDEN_DIR, "index.json"), "utf8"), fs.readdirSync(GOLDEN_DIR)).filter((entry) => SHAPE_GOLDEN.test(entry.name));
const LANDMARK_LINES: Readonly<Record<string, readonly [string, number]>> = { A: ["z1", 0], B: ["z1", 1], C: ["m", 0], D: ["m", 1], E: ["z2", 0], F: ["z2", 1] };

const VIEW = [
  "import json, sys",
  "from sarcostat.engine.annotate import AnnotationSession",
  "from sarcostat.engine.reports import results_view",
  "session = AnnotationSession()",
  "session.doc_load_v1(open(sys.argv[1], encoding='utf-8').read())",
  "print(json.dumps(results_view(session.analysis()), ensure_ascii=False))",
].join("\n");
const ANALYZE = ["import sys", "from sarcostat.cli.app import app", "sys.argv = ['sarcostat', 'analyze', sys.argv[1], sys.argv[2]]", "app()"].join("\n");

interface GoldenDoc {
  images: { file_name: string; sha256: string }[];
  gui?: Record<string, unknown>;
  items: { item_id: string; type: string; skipped: boolean; points: Record<string, number[][]> }[];
}

interface Boundary {
  ref: string;
  pyodide: string;
  cpython: string;
}

function oneUnitApart(a: string, b: string): boolean {
  const numbers = (text: string): string[] => text.match(/-?\d+\.\d+|-?\d+/g) ?? [];
  const na = numbers(a);
  const nb = numbers(b);
  if (na.length !== nb.length || a.replace(/-?\d+(\.\d+)?/g, "#") !== b.replace(/-?\d+(\.\d+)?/g, "#")) {
    return false;
  }
  let differing = 0;
  for (let k = 0; k < na.length; k += 1) {
    const x = na[k] as string;
    const y = nb[k] as string;
    if (x === y) {
      continue;
    }
    const decimals = (x.split(".")[1] ?? "").length;
    if (decimals !== (y.split(".")[1] ?? "").length || Math.abs(Number(x) - Number(y)) > 1.5 * 10 ** -decimals) {
      return false;
    }
    differing += 1;
  }
  return differing > 0;
}

async function lastAppResult<T>(recorder: Recorder, cmd: string): Promise<T> {
  const calls = (await recorder.calls("app")).filter((call) => call.cmd === cmd && call.ok === true);
  const last = calls[calls.length - 1];
  if (last === undefined) {
    throw new Error(`the app sent no successful ${cmd}`);
  }
  return last.result as T;
}

async function exportFiles(page: Page, downloads: Download[], id: string, count: number): Promise<string[]> {
  const before = downloads.length;
  await page.keyboard.press("e");
  const menu = page.getByRole("dialog", { name: en.exportMenu.label });
  await expect(menu).toBeVisible();
  await menu.locator(`[data-export='${id}']`).click();
  await expect.poll(() => downloads.length, { timeout: 20_000 }).toBe(before + count);
  return Promise.all(downloads.slice(before).map(async (download) => fs.readFileSync(await download.path(), "utf8")));
}

async function domStrings(page: Page, ids: readonly number[]): Promise<RefText[]> {
  const seen = new Map<string, RefText>();
  const add = (entries: RefText[]): void => {
    for (const entry of entries) {
      const previous = seen.get(entry.ref);
      if (previous !== undefined && previous.text !== entry.text) {
        throw new Error(`${entry.ref} shows ${JSON.stringify(previous.text)} and ${JSON.stringify(entry.text)}`);
      }
      seen.set(entry.ref, entry);
    }
  };
  await page.locator(".tabs__tab[data-tab='items']").click();
  add(await refTexts(page, ITEMS_PANEL));
  for (const id of ids) {
    await page.locator(".tabs__tab[data-tab='items']").click();
    await page.locator(`${ITEMS_PANEL} .items__row[data-sarcomere='${id}'] [data-action='show']`).click();
    await expect(page.locator(`${RESULTS_PANEL} [data-part='results-title'] b`)).toHaveText(fill(en.panel.item, { n: id }));
    add(await refTexts(page, RESULTS_PANEL));
  }
  return [...seen.values()];
}

test.describe("shape goldens through the UI (plan 3.13, M4-1 #1-#3)", () => {
  test.setTimeout(180_000);
  for (const entry of GOLDENS) {
    test(entry.name, async ({ page, recorder, engine, baseURL }, testInfo) => {
      const downloads: Download[] = [];
      page.on("download", (download) => downloads.push(download));
      const text = goldenText(entry.name);
      const expectedText = fs.readFileSync(path.join(GOLDEN_DIR, `${entry.name}.expected.json`), "utf8");
      const doc = JSON.parse(text) as GoldenDoc;
      const image = doc.images[0] as { file_name: string; sha256: string };
      const goldenFile = path.join(GOLDEN_DIR, `${entry.name}.json`);
      const problems: string[] = [];
      const fields = new Map<string, FieldStats>();

      await boot(page);
      await openGoldenImage(page, image.file_name);
      await openPayload(page, jsonPayload(`${entry.name}.json`, text));
      const shapes = doc.items.filter((item) => item.type === "shape");
      await expect(page.locator(`${ITEMS_PANEL} .items__row`)).toHaveCount(doc.items.length, { timeout: 60_000 });
      const loaded = await lastAppResult<{ items: { item_id: string; type: string; sarcomere_id: number | null; marks: { x: number; y: number; role: string }[] }[]; notice: { code: string; params: Record<string, unknown> } }>(recorder, "doc.load_v1");
      expect(loaded.notice.code).toBe("document_loaded");
      expect(loaded.notice.params.image_checked).toBe(true);

      for (const item of shapes) {
        const shown = loaded.items.find((candidate) => candidate.item_id === item.item_id);
        expect(shown, item.item_id).toBeDefined();
        const expectedMarks: { x: number | undefined; y: number | undefined; role: string }[] = item.skipped
          ? []
          : Object.entries(LANDMARK_LINES).flatMap(([role, [line, end]]) => {
              const point = item.points[line]?.[end];
              return point === undefined ? [] : [{ x: point[0], y: point[1], role }];
            });
        expect(shown?.marks as unknown, `${item.item_id}: landmarks`).toEqual(expectedMarks);
      }
      await expect(page.locator("[data-part='overlay'] .ov__item")).toHaveCount(loaded.items.filter((item) => item.marks.length > 0).length);

      if (entry.checks.includes("estimate_click_error")) {
        await page.locator(".tabs__tab[data-tab='results']").click();
        await page.locator(`${RESULTS_PANEL} [data-action='estimate-click-error']`).click();
        await expect(page.locator(".toast")).toContainText("Click error estimated from");
        const state = await lastAppResult<{ settings: Record<string, unknown> }>(recorder, "doc.estimate_click_error");
        const actual = Object.fromEntries(CLICK_ERROR_KEYS.map((key) => [key, state.settings[key]]));
        const expected = Object.fromEntries(CLICK_ERROR_KEYS.map((key) => [key, doc.gui?.[key]]));
        compareJson({ estimate_click_error: expected }, { estimate_click_error: actual }, RULE, fields, problems, entry.name);
        await expect.poll(async () => (await recorder.calls("app")).filter((call) => call.cmd === "results.get").length).toBeGreaterThan(1);
      }

      await expect(page.locator(`${RESULTS_PANEL} [data-part='results-title']`)).toBeVisible({ timeout: 60_000 });
      const cpythonView = JSON.parse(cpython(VIEW, [goldenFile])) as unknown;
      const ids = loaded.items.flatMap((item) => (item.type === "shape" && item.sarcomere_id !== null ? [item.sarcomere_id] : []));
      const strings = await domStrings(page, ids);
      const pyodideView = await lastAppResult<unknown>(recorder, "results.get");
      const boundary: Boundary[] = [];
      for (const { ref, text: shownText } of strings) {
        const fromPyodide = readRef(pyodideView, ref);
        const fromCpython = readRef(cpythonView, ref);
        if (shownText !== fromPyodide) {
          problems.push(`${entry.name}: ${ref}: the page shows ${JSON.stringify(shownText)}, results.get gave ${JSON.stringify(fromPyodide)}`);
        } else if (shownText !== fromCpython) {
          if (typeof fromCpython === "string" && oneUnitApart(shownText, fromCpython)) {
            boundary.push({ ref, pyodide: shownText, cpython: fromCpython });
          } else {
            problems.push(`${entry.name}: ${ref}: the page shows ${JSON.stringify(shownText)}, CPython formats ${JSON.stringify(fromCpython)}`);
          }
        }
      }
      const cells = strings.filter((entry) => entry.ref.startsWith("tables.sarcomeres.rows."));
      expect(cells.length, "table cells shown").toBeGreaterThan(0);

      const [resultsJson, sidecarJson] = await exportFiles(page, downloads, "results_json", 2);
      const engineJson = (await engine.call<{ text: string }>("doc.export", { what: "results_json" })).text;
      const analyzeJson = cpython(ANALYZE, ["--json", goldenFile]);
      const byteEqualPyodide = resultsJson === engineJson;
      if (!byteEqualPyodide) {
        problems.push(`${entry.name}: the downloaded results JSON differs from the Pyodide export`);
      }
      compareJson(JSON.parse(expectedText), JSON.parse(resultsJson as string), RULE, fields, problems, `${entry.name} expected`);
      compareJson(JSON.parse(analyzeJson), JSON.parse(resultsJson as string), RULE, fields, problems, `${entry.name} analyze --json`);
      const [resultsCsv, sidecarCsv] = await exportFiles(page, downloads, "results_csv", 2);
      const engineCsv = (await engine.call<{ text: string }>("doc.export", { what: "results_csv" })).text;
      if (resultsCsv !== engineCsv) {
        problems.push(`${entry.name}: the downloaded results CSV differs from the Pyodide export`);
      }
      const analyzeCsv = cpython(ANALYZE, ["--csv", goldenFile]);
      const csvRows = (value: string): string[][] => value.replace(/\r?\n$/, "").split(/\r?\n/).map((line) => line.split(","));
      const pyRows = csvRows(resultsCsv as string);
      const cpRows = csvRows(analyzeCsv);
      if (pyRows.length !== cpRows.length) {
        problems.push(`${entry.name}: CSV has ${pyRows.length} rows, CPython ${cpRows.length}`);
      } else {
        const asValues = (rows: string[][]): unknown => rows.map((row) => row.map((cell) => (cell !== "" && Number.isFinite(Number(cell)) ? Number(cell) : cell)));
        compareJson(asValues(cpRows), asValues(pyRows), RULE, fields, problems, `${entry.name} analyze --csv`);
      }
      const build = (await (await page.request.get(new URL("BUILD.json", baseURL).href)).json()) as { version: string; source_digest: string };
      for (const sidecar of [sidecarJson, sidecarCsv]) {
        const parsed = JSON.parse(sidecar as string) as { provenance: { files: { file_name: string; sha256: string }[]; sarcostat_version: string; source_digest: string; parameters: Record<string, unknown> } };
        expect(parsed.provenance.sarcostat_version).toBe(build.version);
        expect(parsed.provenance.source_digest).toBe(build.source_digest);
        expect(parsed.provenance.files).toContainEqual({ file_name: image.file_name, sha256: image.sha256 });
        expect(Object.keys(parsed.provenance.parameters).length).toBeGreaterThan(0);
        expect(sidecar).not.toMatch(/"(\/|[A-Za-z]:\\\\)/);
      }

      const [annotation] = await exportFiles(page, downloads, "v1", 1);
      expect(annotation, "the app writes the Python-written golden back unchanged").toBe(text);

      const worst = [...fields.values()].reduce((max, stats) => Math.max(max, stats.max_scaled), 0);
      const report = {
        golden: entry.name,
        rule: { sha256: createHash("sha256").update(RULE_TEXT).digest("hex"), approved: RULE.approved },
        dom_strings: strings.length,
        table_cells: cells.length,
        rounding_boundary_cases: boundary,
        numbers: [...fields.values()].reduce((n, stats) => n + stats.n, 0),
        max_scaled: worst,
        results_json_byte_equal_to_pyodide: byteEqualPyodide,
        results_json_byte_equal_to_cpython_expected: resultsJson === expectedText,
        results_json_byte_equal_to_cpython_analyze: resultsJson === analyzeJson,
        results_csv_byte_equal_to_cpython_analyze: resultsCsv === analyzeCsv,
        problems,
      };
      const reportText = `${JSON.stringify(report, null, 2)}\n`;
      fs.writeFileSync(testInfo.outputPath("parity.json"), reportText);
      await testInfo.attach("parity.json", { body: reportText, contentType: "application/json" });
      console.log(`${problems.length === 0 ? "ok  " : "FAIL"} ${entry.name}: ${strings.length} DOM strings (${cells.length} table cells), ${boundary.length} rounding-boundary case(s), ${report.numbers} numbers, max scaled ${worst.toExponential(3)}, JSON byte-equal to CPython ${report.results_json_byte_equal_to_cpython_analyze}`);
      expect(problems).toEqual([]);
    });
  }
});
