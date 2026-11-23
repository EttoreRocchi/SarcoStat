import { describe, expect, it } from "vitest";

import { siDomain } from "../../src/view/plots/shape_si.ts";
import { formatTick, niceTicks, scaleLinear } from "../../src/view/plots/svg.ts";

describe("axis ticks (formatted in TypeScript, plan 3.7)", () => {
  it("chooses round steps and as many decimals as the step needs", () => {
    expect(niceTicks(0.9, 1.2, 5)).toEqual({ step: 0.1, decimals: 1, values: [0.9, 1, 1.1, 1.2] });
    expect(niceTicks(0.9, 1.2, 6)).toEqual({ step: 0.05, decimals: 2, values: [0.9, 0.95, 1, 1.05, 1.1, 1.15, 1.2] });
    expect(niceTicks(0.83, 1.31, 4)).toMatchObject({ step: 0.2, decimals: 1, values: [1, 1.2] });
    expect(niceTicks(0, 100, 5)).toMatchObject({ step: 20, decimals: 0 });
    const ticks = niceTicks(0.6, 1.1, 5);
    expect(ticks.values.map((value) => formatTick(value, ticks.decimals))).toEqual(["0.6", "0.7", "0.8", "0.9", "1.0", "1.1"]);
    expect(niceTicks(1, 1.25, 10)).toMatchObject({ step: 0.025, decimals: 3 });
  });

  it("never writes a negative zero and refuses an empty range", () => {
    expect(formatTick(-0.0001, 2)).toBe("0.00");
    expect(formatTick(-0.5, 1)).toBe("-0.5");
    expect(() => niceTicks(1, 1)).toThrow(RangeError);
    expect(() => niceTicks(Number.NaN, 1)).toThrow(RangeError);
  });

  it("maps a domain linearly onto a range", () => {
    const x = scaleLinear([0.9, 1.1], [40, 340]);
    expect(x(0.9)).toBe(40);
    expect(x(1.1)).toBeCloseTo(340, 9);
    expect(x(1)).toBeCloseTo(190, 9);
  });
});

describe("shape index plot domain", () => {
  it("holds every point with ±2u and the rectangular band, padded", () => {
    const domain = siDomain(
      [
        { id: 1, si: 0.69, u: 0.012, shapeClass: "barrel" },
        { id: 2, si: 1.53, u: null, shapeClass: "hourglass" },
      ],
      [0.95, 1.05],
    );
    expect(domain.low).toBeLessThan(0.69 - 0.024);
    expect(domain.high).toBeGreaterThan(1.53);
    expect(siDomain([{ id: 1, si: 1, u: null, shapeClass: "rectangular" }], null).high).toBeGreaterThan(1);
  });
});
