import { describe, expect, it } from "vitest";

import { guardChoiceForKey, leaveChoiceForKey } from "../../src/view/shell/guard.ts";
import { SHEET, sheetKeys } from "../../src/view/shell/sheet.ts";
import { resolveKey, resolveKeyUp, targetKind, type KeyContext, type KeyInput, type ShortcutAction } from "../../src/view/shell/shortcuts.ts";
import { en } from "../../src/strings/en.ts";

function key(name: string, extra: Partial<KeyInput> = {}): KeyInput {
  return { key: name, ctrlKey: false, metaKey: false, altKey: false, repeat: false, isComposing: false, target: "other", ...extra };
}

const SHAPE: KeyContext = { route: "shape", imageOpen: true };
const HOME: KeyContext = { route: "home", imageOpen: false };
const STUDY: KeyContext = { route: "study", imageOpen: false };

const DESIGN_KEYS = {
  global: ["0", "1", "2", "3", "4", "O", "E", "?"],
  view: ["+", "−", "F", "Space", "W", "L", "A"],
  annotate: ["Click", "Backspace", "Esc", "Enter", "N", "[", "]", "V"],
  study: ["Backspace", "M", "S", "P", "Enter", "N", "+", "−", "F", "Space"],
};

describe("global shortcuts", () => {
  it("switch modes with 0 to 4 from Home and from an annotation mode", () => {
    for (const context of [HOME, SHAPE]) {
      expect(["0", "1", "2", "3", "4"].map((name) => resolveKey(key(name), context))).toEqual([
        { kind: "mode", route: "home" },
        { kind: "mode", route: "shape" },
        { kind: "mode", route: "straightness" },
        { kind: "mode", route: "register" },
        { kind: "mode", route: "planner" },
      ]);
    }
  });

  it("open the file picker with O, the export menu with E and the sheet with ?", () => {
    expect(resolveKey(key("o"), HOME)).toEqual({ kind: "open" });
    expect(resolveKey(key("O"), SHAPE)).toEqual({ kind: "open" });
    expect(resolveKey(key("e"), SHAPE)).toEqual({ kind: "export" });
    expect(resolveKey(key("E"), SHAPE)).toEqual({ kind: "export" });
    expect(resolveKey(key("e"), HOME)).toBeNull();
    expect(resolveKey(key("?"), HOME)).toEqual({ kind: "sheet" });
    expect(resolveKey(key("?"), { route: "study", imageOpen: false })).toEqual({ kind: "sheet" });
  });

  it("map Backspace, Esc and Enter to the annotation of the open image", () => {
    expect(resolveKey(key("Backspace"), SHAPE)).toEqual({ kind: "undo" });
    expect(resolveKey(key("Escape"), SHAPE)).toEqual({ kind: "discard" });
    expect(resolveKey(key("Enter"), SHAPE)).toEqual({ kind: "confirm" });
    expect(resolveKey(key("Enter", { target: "control" }), SHAPE)).toBeNull();
    expect(resolveKey(key("Backspace"), { route: "shape", imageOpen: false })).toBeNull();
    expect(resolveKey(key("Backspace"), HOME)).toBeNull();
  });

  it("never fire while typing, with a modifier, on auto-repeat, during composition or in Study", () => {
    for (const name of ["1", "o", "e", "?", "Backspace", "Escape", "Enter"]) {
      expect(resolveKey(key(name, { target: "text" }), SHAPE), name).toBeNull();
      expect(resolveKey(key(name, { ctrlKey: true }), SHAPE), name).toBeNull();
      expect(resolveKey(key(name, { metaKey: true }), SHAPE), name).toBeNull();
      expect(resolveKey(key(name, { altKey: true }), SHAPE), name).toBeNull();
      expect(resolveKey(key(name, { repeat: true }), SHAPE), name).toBeNull();
      expect(resolveKey(key(name, { isComposing: true }), SHAPE), name).toBeNull();
    }
    for (const name of ["0", "1", "4", "o", "e", "Escape", "l", "a", "[", "]", "v", "w"]) {
      expect(resolveKey(key(name), { route: "study", imageOpen: true }), name).toBeNull();
    }
    expect(resolveKey(key("1"), { route: null, imageOpen: false })).toBeNull();
  });

  it("classify focus targets: text fields, controls, the rest", () => {
    const element = (tagName: string, attrs: Record<string, string> = {}, inside: string | null = null): EventTarget =>
      ({
        tagName,
        getAttribute: (name: string) => attrs[name] ?? null,
        closest: (selector: string) => {
          if (inside !== null && selector.includes(inside)) {
            return {};
          }
          if (selector.split(",").some((part) => part.trim().toUpperCase() === tagName)) {
            return {};
          }
          return null;
        },
      }) as unknown as EventTarget;
    expect(targetKind(element("INPUT"))).toBe("text");
    expect(targetKind(element("INPUT", { type: "number" }))).toBe("text");
    expect(targetKind(element("INPUT", { type: "checkbox" }))).toBe("control");
    expect(targetKind(element("TEXTAREA"))).toBe("text");
    expect(targetKind(element("SPAN", {}, "[contenteditable='true']"))).toBe("text");
    expect(targetKind(element("BUTTON"))).toBe("control");
    expect(targetKind(element("DIV", { role: "tab" }))).toBe("control");
    expect(targetKind(element("DIV"))).toBe("other");
    expect(targetKind(null)).toBe("other");
  });
});

describe("guard and leave dialog keys", () => {
  it("Enter keeps the clicks and switches, D discards, Esc stays", () => {
    expect(guardChoiceForKey("Enter")).toBe("keep");
    expect(guardChoiceForKey("d")).toBe("discard");
    expect(guardChoiceForKey("D")).toBe("discard");
    expect(guardChoiceForKey("Escape")).toBe("stay");
    expect(guardChoiceForKey("1")).toBeNull();
  });

  it("Enter exports first when there is something to export, D leaves, Esc cancels", () => {
    expect(leaveChoiceForKey("Enter", true)).toBe("export");
    expect(leaveChoiceForKey("Enter", false)).toBe("leave");
    expect(leaveChoiceForKey("d", true)).toBe("leave");
    expect(leaveChoiceForKey("Escape", true)).toBe("cancel");
    expect(leaveChoiceForKey("x", true)).toBeNull();
  });
});

describe("shortcut sheet", () => {
  it("lists every key of DESIGN.md's shortcut table, group by group", () => {
    const byGroup = Object.fromEntries(SHEET.map((group) => [group.id, group.rows.flatMap((row) => row.keys)]));
    expect(byGroup).toEqual(DESIGN_KEYS);
    expect(new Set(sheetKeys())).toEqual(new Set(Object.values(DESIGN_KEYS).flat()));
    expect(SHEET.map((group) => group.title)).toEqual(Object.values(en.sheet.groups));
  });

  it("describes each key in one line", () => {
    for (const group of SHEET) {
      for (const row of group.rows) {
        expect(row.text.length, row.keys.join(" ")).toBeGreaterThan(0);
        expect(row.text, row.keys.join(" ")).not.toMatch(/[\r\n]/);
      }
    }
  });
});

const VIEW_KEYS: [string, ShortcutAction][] = [
  ["+", { kind: "view", action: "zoom-in" }],
  ["=", { kind: "view", action: "zoom-in" }],
  ["-", { kind: "view", action: "zoom-out" }],
  ["_", { kind: "view", action: "zoom-out" }],
  ["\u2212", { kind: "view", action: "zoom-out" }],
  ["f", { kind: "view", action: "fit" }],
  ["F", { kind: "view", action: "fit" }],
  ["l", { kind: "view", action: "layers" }],
  ["L", { kind: "view", action: "layers" }],
  ["a", { kind: "view", action: "annotate" }],
  ["A", { kind: "view", action: "annotate" }],
  [" ", { kind: "view", action: "pan-start" }],
];

const STUDY_KEYS: [string, ShortcutAction][] = [
  ["Backspace", { kind: "study", action: "undo" }],
  ["m", { kind: "study", action: "m-line" }],
  ["M", { kind: "study", action: "m-line" }],
  ["s", { kind: "study", action: "skip" }],
  ["S", { kind: "study", action: "skip" }],
  ["p", { kind: "study", action: "pause" }],
  ["P", { kind: "study", action: "pause" }],
  ["Enter", { kind: "study", action: "next" }],
  ["n", { kind: "study", action: "next-myofibril" }],
  ["N", { kind: "study", action: "next-myofibril" }],
  ["+", { kind: "study", action: "zoom-in" }],
  ["=", { kind: "study", action: "zoom-in" }],
  ["-", { kind: "study", action: "zoom-out" }],
  ["_", { kind: "study", action: "zoom-out" }],
  ["\u2212", { kind: "study", action: "zoom-out" }],
  ["f", { kind: "study", action: "fit" }],
  ["F", { kind: "study", action: "fit" }],
  [" ", { kind: "study", action: "pan-start" }],
];

describe("one key table (contract 3.1)", () => {
  it("maps the view keys of an open image to view actions, and Space up to the end of panning", () => {
    for (const [name, action] of VIEW_KEYS) {
      expect(resolveKey(key(name), SHAPE), JSON.stringify(name)).toEqual(action);
      expect(resolveKey(key(name), { route: "shape", imageOpen: false }), JSON.stringify(name)).toBeNull();
      expect(resolveKey(key(name), HOME), JSON.stringify(name)).toBeNull();
    }
    expect(resolveKeyUp(key(" "), SHAPE)).toEqual({ kind: "view", action: "pan-end" });
    expect(resolveKeyUp(key("f"), SHAPE)).toBeNull();
    expect(resolveKeyUp(key(" "), HOME)).toBeNull();
  });

  it("repeats zoom and Space while held, never the one-shot keys", () => {
    for (const name of ["+", "-", " "]) {
      expect(resolveKey(key(name, { repeat: true }), SHAPE), name).not.toBeNull();
      expect(resolveKey(key(name, { repeat: true }), STUDY), name).not.toBeNull();
    }
    for (const name of ["f", "l", "a", "Backspace", "Escape", "Enter", "[", "1", "o", "e", "?"]) {
      expect(resolveKey(key(name, { repeat: true }), SHAPE), name).toBeNull();
    }
    for (const name of ["Backspace", "m", "s", "p", "Enter", "n", "f"]) {
      expect(resolveKey(key(name, { repeat: true }), STUDY), name).toBeNull();
    }
  });

  it("leaves Space and Enter to a focused control", () => {
    expect(resolveKey(key(" ", { target: "control" }), SHAPE)).toBeNull();
    expect(resolveKey(key(" ", { target: "control" }), STUDY)).toBeNull();
    expect(resolveKey(key("Enter", { target: "control" }), STUDY)).toBeNull();
  });

  it("has the study rows: Backspace, M, S, P, Enter, N, + and −, F, Space, and nothing else but ?", () => {
    for (const [name, action] of STUDY_KEYS) {
      expect(resolveKey(key(name), STUDY), JSON.stringify(name)).toEqual(action);
    }
    expect(resolveKeyUp(key(" "), STUDY)).toEqual({ kind: "study", action: "pan-end" });
    expect(resolveKey(key("?"), STUDY)).toEqual({ kind: "sheet" });
    const known = new Set([...STUDY_KEYS.map(([name]) => name), "?"]);
    for (const name of candidateKeys()) {
      if (!known.has(name)) {
        expect(resolveKey(key(name), STUDY), JSON.stringify(name)).toBeNull();
      }
    }
  });

  it("steps through the sarcomeres with [ and ]", () => {
    expect(resolveKey(key("["), SHAPE)).toEqual({ kind: "item", step: -1 });
    expect(resolveKey(key("]"), SHAPE)).toEqual({ kind: "item", step: 1 });
    expect(resolveKey(key("["), { route: "shape", imageOpen: false })).toBeNull();
  });
});

function candidateKeys(): string[] {
  const printable = Array.from({ length: 95 }, (_, i) => String.fromCharCode(32 + i));
  return [...printable, "\u2212", "Backspace", "Enter", "Escape", "Tab", "Delete", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown", "F1", "F5"];
}

const SHEET_LABEL: Readonly<Record<string, string>> = { " ": "Space", "=": "+", "-": "−", _: "−", "\u2212": "−", Escape: "Esc", "[": "[", "]": "]" };

function sheetLabel(name: string): string {
  return SHEET_LABEL[name] ?? (name.length === 1 ? name.toUpperCase() : name);
}

function groupOf(action: ShortcutAction): string {
  switch (action.kind) {
    case "mode":
    case "open":
    case "export":
    case "sheet":
      return "global";
    case "view":
      return "view";
    case "study":
      return "study";
    default:
      return "annotate";
  }
}

describe("shortcut sheet covers the key table", () => {
  it("lists, in the right group, every key that resolveKey can return in any route", () => {
    const contexts: KeyContext[] = [HOME, SHAPE, STUDY, { route: "shape", imageOpen: false }, { route: "planner", imageOpen: false }];
    const byGroup = new Map(SHEET.map((group) => [group.id as string, new Set(group.rows.flatMap((row) => row.keys))]));
    const seen: string[] = [];
    for (const context of contexts) {
      for (const name of candidateKeys()) {
        const action = resolveKey(key(name), context);
        if (action === null) {
          continue;
        }
        const group = groupOf(action);
        const label = sheetLabel(name);
        expect(byGroup.get(group)?.has(label), `${JSON.stringify(name)} (${action.kind}) in ${context.route}: ${label} in the ${group} group`).toBe(true);
        seen.push(`${context.route}:${name}`);
      }
    }
    expect(seen.length).toBeGreaterThan(40);
  });
});
