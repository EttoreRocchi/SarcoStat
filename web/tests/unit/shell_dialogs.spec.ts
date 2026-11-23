import { describe, expect, it } from "vitest";

import { parseNumber, SCALE_UNITS } from "../../src/view/shell/dialogs.ts";

describe("dialog input parsing (the engine validates the value)", () => {
  it("reads a number, a decimal comma, or an empty field", () => {
    expect(parseNumber("6.62")).toBe(6.62);
    expect(parseNumber(" 22 ")).toBe(22);
    expect(parseNumber("6,62")).toBe(6.62);
    expect(parseNumber("-1")).toBe(-1);
    expect(parseNumber("")).toBeNull();
    expect(parseNumber("   ")).toBeNull();
  });

  it("marks text that is not a number as NaN, so the dialog asks again", () => {
    expect(parseNumber("abc")).toBeNaN();
    expect(parseNumber("1e400")).toBeNaN();
    expect(parseNumber("Infinity")).toBeNaN();
  });

  it("offers the units the engine turns into a scale, and px for none", () => {
    expect([...SCALE_UNITS]).toEqual(["nm", "µm", "px"]);
  });
});
