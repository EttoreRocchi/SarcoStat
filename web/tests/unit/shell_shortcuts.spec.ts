import { describe, expect, it } from "vitest";

import { guardChoiceForKey, leaveChoiceForKey } from "../../src/view/shell/guard.ts";
import { SHEET, sheetKeys } from "../../src/view/shell/sheet.ts";
import { resolveKey, targetKind, type KeyContext, type KeyInput } from "../../src/view/shell/shortcuts.ts";
import { en } from "../../src/strings/en.ts";

function key(name: string, extra: Partial<KeyInput> = {}): KeyInput {
  return { key: name, ctrlKey: false, metaKey: false, altKey: false, repeat: false, isComposing: false, target: "other", ...extra };
}

const SHAPE: KeyContext = { route: "shape", imageOpen: true };
const HOME: KeyContext = { route: "home", imageOpen: false };

const DESIGN_KEYS = {
  global: ["0", "1", "2", "3", "4", "O", "E", "?"],
  view: ["+", "−", "F", "Space", "W", "L", "A"],
  annotate: ["Click", "Backspace", "Esc", "Enter", "N", "[", "]", "V"],
  study: ["Backspace", "M", "S", "P", "Enter"],
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
    for (const name of ["0", "1", "o", "e", "Backspace", "Escape", "Enter"]) {
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
