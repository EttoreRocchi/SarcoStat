import { describe, expect, it } from "vitest";

import { en } from "../../src/strings/en.ts";
import { BOOT_STAGES, FATAL_CODES } from "../../src/worker/protocol.ts";

function leaves(value: unknown, where = "en"): [string, string][] {
  if (typeof value === "string") {
    return [[where, value]];
  }
  return Object.entries(value as Record<string, unknown>).flatMap(([key, child]) => leaves(child, `${where}.${key}`));
}

describe("strings of the worker shell", () => {
  it("are one line each, without surrounding spaces", () => {
    for (const [where, text] of leaves(en)) {
      expect(text, where).not.toMatch(/[\r\n]/);
      expect(text.trim(), where).toBe(text);
      expect(text, where).not.toBe("");
    }
  });

  it("name every boot stage and every fatal code", () => {
    expect(Object.keys(en.status.boot).sort()).toEqual([...BOOT_STAGES].sort());
    expect(Object.keys(en.fatal.title).sort()).toEqual([...FATAL_CODES].sort());
    expect(Object.keys(en.fatal.body).sort()).toEqual([...FATAL_CODES].sort());
  });
});
