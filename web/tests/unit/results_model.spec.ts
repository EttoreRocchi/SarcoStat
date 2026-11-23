import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { WEB_DIR } from "../../scripts/lane.mjs";
import type { ResultsView } from "../../src/state/store.ts";
import { pickSarcomere, readRef, shapeResults, splitPlusMinus, SARCOMERE_COLUMNS } from "../../src/view/results/model.ts";

const GOLDEN_DIR = path.resolve(WEB_DIR, "..", "tests", "golden", "web");
const SHAPE_GOLDENS = ["shape_classes", "shape_click_set", "shape_click_estimated", "shape_deleted_then_added", "shape_scale_unset", "large_px", "large_nm", "mixed_types"];

const VIEW = [
  "import json, sys",
  "from sarcostat.engine.annotate import AnnotationSession",
  "from sarcostat.engine.reports import results_view",
  "out = {}",
  "for path in sys.argv[1:]:",
  "    session = AnnotationSession()",
  "    session.doc_load_v1(open(path, encoding='utf-8').read())",
  "    out[path] = results_view(session.analysis())",
  "print(json.dumps(out, ensure_ascii=False))",
].join("\n");

function python(): string {
  return process.env.SARCOSTAT_PYTHON && process.env.SARCOSTAT_PYTHON !== "" ? process.env.SARCOSTAT_PYTHON : "python3";
}

function cpythonViews(names: readonly string[]): Map<string, ResultsView> {
  const files = names.map((name) => path.join(GOLDEN_DIR, `${name}.json`));
  const text = execFileSync(python(), ["-c", VIEW, ...files], { encoding: "utf8", env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } });
  const parsed = JSON.parse(text) as Record<string, ResultsView>;
  return new Map(names.map((name, i) => [name, parsed[files[i] as string] as ResultsView]));
}

describe("results model on the CPython results.get of every shape golden", () => {
  const views = cpythonViews(SHAPE_GOLDENS);

  it("finds every column of the sarcomeres table and points each cell at its source", () => {
    for (const [name, view] of views) {
      expect(fs.existsSync(path.join(GOLDEN_DIR, `${name}.json`))).toBe(true);
      const model = shapeResults(view);
      expect(model, name).not.toBeNull();
      const table = view.tables.sarcomeres;
      expect(table, name).toBeDefined();
      expect(model?.sarcomeres.length, name).toBe(table?.rows.length);
      for (const sarcomere of model?.sarcomeres ?? []) {
        for (const key of Object.keys(SARCOMERE_COLUMNS) as (keyof typeof SARCOMERE_COLUMNS)[]) {
          const cell = sarcomere.cells[key];
          expect(readRef(view, cell.ref), `${name} ${cell.ref}`).toBe(cell.text);
        }
        expect(sarcomere.measured, name).toBe(sarcomere.cells.status.text === "measured");
      }
      for (const cell of [model?.scale, model?.click, model?.notes.si, model?.notes.asrCur, model?.notes.stability]) {
        if (cell != null) {
          expect(readRef(view, cell.ref), `${name} ${cell.ref}`).toBe(cell.text);
        }
      }
      expect(model?.scale?.text, name).toMatch(/^(Scale: 1 px = |No scale is set)/);
      expect(model?.click?.text, name).toMatch(/^(Click error: σ = |No click error is set)/);
      expect(model?.band, name).toEqual([0.95, 1.05]);
    }
  });

  it("carries the raw stability share exactly when the table shows a stability", () => {
    for (const [name, view] of views) {
      for (const sarcomere of shapeResults(view)?.sarcomeres ?? []) {
        expect(sarcomere.stability === null, `${name} sarcomere ${sarcomere.id}`).toBe(sarcomere.cells.stability.text === "");
      }
    }
    const withSigma = shapeResults(views.get("shape_click_set") as ResultsView);
    expect(withSigma?.notes.stability?.text).toMatch(/^Stability: /);
    expect(withSigma?.sarcomeres.every((entry) => entry.stability !== null && entry.stability >= 0 && entry.stability <= 1)).toBe(true);
    expect(shapeResults(views.get("shape_classes") as ResultsView)?.notes.stability).toBeNull();
  });

  it("plots only measured sarcomeres, with their raw SI and u(SI)", () => {
    for (const [name, view] of views) {
      const model = shapeResults(view);
      expect(model?.points.map((point) => point.id), name).toEqual(model?.sarcomeres.filter((entry) => entry.measured).map((entry) => entry.id));
    }
  });

  it("splits a value from its ± u without changing the text", () => {
    expect(splitPlusMinus("1.140 ± 0.031")).toEqual({ value: "1.140", pm: " ± 0.031" });
    expect(splitPlusMinus("0.690")).toEqual({ value: "0.690", pm: "" });
    const parts = splitPlusMinus("2648.07 ± 1.00 nm");
    expect(parts.value + parts.pm).toBe("2648.07 ± 1.00 nm");
  });

  it("keeps the selection on a sarcomere that exists, at the ends of the list", () => {
    expect(pickSarcomere([], 3, 1)).toBeNull();
    expect(pickSarcomere([1, 2, 3], null, 0)).toBe(3);
    expect(pickSarcomere([1, 2, 3], 2, 1)).toBe(3);
    expect(pickSarcomere([1, 2, 3], 3, 1)).toBe(3);
    expect(pickSarcomere([1, 2, 3], 1, -1)).toBe(1);
    expect(pickSarcomere([1, 2], 7, -1)).toBe(2);
  });
});
