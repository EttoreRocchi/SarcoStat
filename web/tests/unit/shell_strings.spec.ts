import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { WEB_DIR } from "../../scripts/lane.mjs";
import { ROUTES } from "../../src/router.ts";
import { LANDMARKS } from "../../src/state/store.ts";
import { en } from "../../src/strings/en.ts";
import { fill, parts, placeholders, plural } from "../../src/strings/format.ts";
import { MARK_ROLES, STROKE_ROLES } from "../../src/worker/catalogue.ts";

const ANNOTATE_PY = path.join(WEB_DIR, "..", "src", "sarcostat", "engine", "annotate.py");

function leaves(value: unknown, where = "en"): [string, string][] {
  if (typeof value === "string") {
    return [[where, value]];
  }
  return Object.entries(value as Record<string, unknown>).flatMap(([key, child]) => leaves(child, `${where}.${key}`));
}

function pythonTable(source: string, name: string): Map<string, string> {
  const start = source.indexOf(`${name} = {`);
  expect(start, `${name} in annotate.py`).toBeGreaterThanOrEqual(0);
  const end = source.indexOf("\n}\n", start);
  const body = source.slice(start, end);
  const out = new Map<string, string>();
  const entry = /^ {4}"([a-z_]+)":\s*\(?\s*((?:"[^"]*"\s*)+)\)?,?$/gm;
  for (const match of body.matchAll(entry)) {
    out.set(match[1] ?? "", [...(match[2] ?? "").matchAll(/"([^"]*)"/g)].map((m) => m[1]).join(""));
  }
  return out;
}

describe("shell strings", () => {
  it("are single lines without surrounding spaces or hard-coded line breaks", () => {
    for (const [where, text] of leaves(en)) {
      expect(text, where).not.toMatch(/[\r\n\t]/);
      expect(text.trim(), where).toBe(text);
      expect(text, where).not.toMatch(/ {2}/);
    }
  });

  it("name every route, landmark and line role", () => {
    for (const route of ROUTES) {
      expect(en.modes[route].title, route).not.toBe("");
      expect(en.modes[route].short, route).not.toBe("");
      expect(en.modes[route].desc, route).not.toBe("");
    }
    expect(Object.keys(en.landmarks)).toEqual([...LANDMARKS]);
    expect(MARK_ROLES.slice(0, 6)).toEqual([...LANDMARKS]);
    expect(Object.keys(en.lines)).toEqual(STROKE_ROLES.slice(0, 3));
  });

  it("use orientation-free landmark names", () => {
    for (const name of Object.values(en.landmarks)) {
      expect(name).toMatch(/, (first|second) end$/);
      expect(name).not.toMatch(/start|top|bottom|left|right/i);
    }
  });

  it("cover every notice code of the annotation engine, with the engine's parameter names", () => {
    const source = fs.readFileSync(ANNOTATE_PY, "utf8");
    const notices = pythonTable(source, "NOTICE_TEXT");
    expect(notices.size).toBeGreaterThanOrEqual(15);
    const ours = en.notices as Readonly<Record<string, string>>;
    for (const [code, english] of notices) {
      expect(ours[code], code).toBeDefined();
      const engine = new Set(placeholders(english).map((name) => (name === "line_name" ? "line" : name)));
      for (const name of placeholders(ours[code] ?? "")) {
        expect(engine.has(name) || name === "line", `${code}: {${name}}`).toBe(true);
      }
    }
    const steps = pythonTable(source, "STEP_TEXT");
    expect([...steps.keys()].sort()).toEqual(["shape_click", "shape_ready"]);
  });

  it("fill templates and keep a parameter apart for bold text", () => {
    expect(fill(en.hud.count, { sarcomere: 4, number: 3, of: 6 })).toBe("Sarcomere 4 · click 3 of 6");
    expect(parts(en.hud.count, { sarcomere: 4, number: 3, of: 6 }).filter((part) => part.param === "number")).toEqual([{ text: "3", param: "number" }]);
    expect(fill("{a} {b}", { a: 1 })).toBe("1 {b}");
    expect(plural(1, en.topbar.notExported.shapeOne, en.topbar.notExported.shapeOther)).toBe("1 sarcomere not exported");
    expect(plural(3, en.topbar.notExported.shapeOne, en.topbar.notExported.shapeOther)).toBe("3 sarcomeres not exported");
  });

  it("say the privacy line and the absence of a mode in one sentence each", () => {
    expect(en.home.privacy).toBe("Everything is computed in this browser; nothing is uploaded.");
    for (const text of [en.absent.card, fill(en.absent.notice, { mode: en.modes.register.title }), en.study.openDisabled]) {
      expect(text.match(/[.!?](\s|$)/g), text).toHaveLength(1);
    }
  });
});
