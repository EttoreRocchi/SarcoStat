import { describe, expect, it } from "vitest";

import type { StudyViewState } from "../../src/state/study_session.ts";
import { en } from "../../src/strings/en.ts";
import { fill, placeholders } from "../../src/strings/format.ts";
import { chips, noticeText, prompt, skippedItems } from "../../src/view/study/words.ts";
import { runPython } from "./worker_mock.ts";

const NOTICE_CODES = JSON.parse(runPython(["-c", "import json; from sarcostat.engine.study import NOTICES; print(json.dumps(sorted(NOTICES)))"])) as string[];
const PROBLEM_CODES = JSON.parse(runPython(["-c", "import json; from sarcostat.engine.handlers_study import PROBLEMS; print(json.dumps(sorted(PROBLEMS)))"])) as string[];

function strings(value: unknown, at = "study"): [string, string][] {
  if (typeof value === "string") {
    return [[at, value]];
  }
  if (Array.isArray(value)) {
    return value.flatMap((element, k) => strings(element, `${at}[${k}]`));
  }
  if (value !== null && typeof value === "object") {
    return Object.entries(value).flatMap(([key, element]) => strings(element, `${at}.${key}`));
  }
  return [];
}

function view(overrides: Partial<StudyViewState>): StudyViewState {
  return {
    item_file: "items/001.png",
    roi: [0, 0, 10, 10],
    title: "Item 1 of 2",
    marks: [],
    strokes: [],
    controls: { can_confirm: false, can_undo: false, can_skip_m_line: true, can_next_myofibril: false, shows_branch_end: false, item_type: "shape" },
    step: null,
    progress: { done: 0, total: 2 },
    notice: null,
    paused: false,
    finished: null,
    ...overrides,
  };
}

describe("study strings", () => {
  it("hold no digit and fit on one line", () => {
    for (const [where, text] of strings(en.study)) {
      expect(text, where).not.toMatch(/\d/);
      expect(text, where).not.toMatch(/\n/);
    }
  });

  it("name every engine notice and prompt code of engine/study.py and every kit problem of the bridge", () => {
    const notices = Object.keys(en.study.notices).filter((key) => key !== "parts" && key !== "lines" && key !== "unknown");
    expect(notices.sort()).toEqual(NOTICE_CODES);
    expect(Object.keys(en.study.refused.problems).sort()).toEqual(PROBLEM_CODES);
  });

  it("render engine notices without any digit, whatever the parameters", () => {
    const params = { part: "myofibril_2", line: "m", zline: 7, expected: 6, placed: 3, index: 2, total: 40, error: "disk 9 full", autosave: "x_1.autosave.json", file_name: "s_1.json", study_id: "S1", annotator_id: "A1", session_id: "S1", item: 3, landmark: "C", end: "first", done: 4 };
    for (const code of NOTICE_CODES) {
      const text = noticeText({ code, params });
      expect(text, code).not.toMatch(/\d/);
      expect(text, code).not.toMatch(/[{}]/);
    }
    expect(noticeText({ code: "zline_unfinished", params: { part: "chain" } })).toBe("The chain: finish the Z-line you started, or undo it.");
    expect(noticeText({ code: "shape_line_too_short", params: { line: "z2" } })).toBe("The two ends of the second Z-line are less than one pixel apart; undo and click them again.");
    expect(noticeText({ code: "not_a_code", params: {} })).toBe(en.study.notices.unknown);
    expect(noticeText(null)).toBeNull();
  });

  it("allow digits only in the click counter, the item counter and Z-line ordinals", () => {
    const counters = [en.study.dock.click, en.study.header.item, en.study.identity.resumed];
    expect(counters.map((template) => placeholders(template))).toEqual([["number", "total"], ["index", "total"], ["item", "total"]]);
    expect(placeholders(en.study.dock.chainEnd)).toEqual(["zline", "end"]);
    expect(placeholders(en.study.dock.pairEnd)).toEqual(["fibril", "zline", "end"]);
  });
});

describe("study prompts and chips", () => {
  it("give the shape click counter, the landmark name and the chips", () => {
    const state = view({ step: { code: "shape_click_or_skip_m_line", params: { landmark: "C", index: 3, total: 6 } }, marks: [{ x: 1, y: 1, role: "A" }, { x: 2, y: 2, role: "B" }] });
    expect(prompt(state)).toEqual({ counter: { number: 3, total: 6 }, what: en.study.dock.landmarks.C, hint: null, landmark: "C", kind: "m" });
    expect(chips(state).map((chip) => `${chip.letter}:${chip.state}`)).toEqual(["A:done", "B:done", "C:now", "D:todo", "E:todo", "F:todo"]);
  });

  it("mark C and D as not identifiable after an M-line skip", () => {
    const state = view({ step: { code: "shape_click", params: { landmark: "E", index: 3, total: 4 } }, marks: [{ x: 1, y: 1, role: "A" }, { x: 2, y: 2, role: "B" }] });
    expect(fill(en.study.dock.click, prompt(state)?.counter ?? {})).toBe("Click 3 of 4");
    expect(chips(state).map((chip) => chip.state)).toEqual(["done", "done", "skipped", "skipped", "now", "todo"]);
    const complete = view({ step: { code: "shape_complete", params: {} }, marks: (["A", "B", "E", "F"] as const).map((role) => ({ x: 1, y: 1, role })) });
    expect(chips(complete).map((chip) => chip.state)).toEqual(["done", "done", "skipped", "skipped", "done", "done"]);
    expect(prompt(complete)?.counter).toBeNull();
  });

  it("word chain and pair prompts with Z-line ordinals and no other number", () => {
    const chain = view({ controls: { ...view({}).controls, item_type: "chain", shows_branch_end: true }, step: { code: "chain_first_end", params: { zline: 3, done: 2 } } });
    expect(prompt(chain)?.what).toBe("Z-line 3, first end");
    expect(chips(chain)).toEqual([]);
    const pair = view({ controls: { ...view({}).controls, item_type: "pair" }, step: { code: "pair_myofibril_2", params: { end: "second", zline: 2, done: 1 } } });
    expect(prompt(pair)).toMatchObject({ what: "Second myofibril, Z-line 2, second end", hint: en.study.dock.pairHint2, kind: "f2" });
  });

  it("has no prompt when no item is shown", () => {
    expect(prompt(view({ item_file: null, step: { code: "paused", params: {} } }))).toBeNull();
  });

  it("reads which finished items were skipped from an autosave document", () => {
    const doc = { records: [{ skipped: false }, { skipped: true }, { skipped: false }] };
    expect(skippedItems(new TextEncoder().encode(JSON.stringify(doc)))).toEqual([false, true, false]);
    expect(skippedItems(new TextEncoder().encode("not json"))).toBeNull();
    expect(skippedItems(null)).toBeNull();
  });
});
