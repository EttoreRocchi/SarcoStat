import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { WEB_DIR } from "../../scripts/lane.mjs";
import { DISPLAY, studyDisplay, zoomLevels } from "../../src/view/study/stage.ts";

const CONFIG = path.join(WEB_DIR, "src", "config", "study_display.json");

describe("study display settings (compiled into the build)", () => {
  it("are the protocol's settings: zoom from fit by 1.25 up to 8 CSS px per image px, a x3 loupe, no smoothing, no display window, no zoom percentage", () => {
    expect(JSON.parse(fs.readFileSync(CONFIG, "utf8"))).toEqual({
      format: "sarcostat-study-display",
      format_version: 1,
      zoom: { from: "fit", factor: 1.25, max_css_px_per_image_px: 8 },
      loupe: { factor: 3 },
      smoothing: false,
      display_window: false,
      zoom_percentage: false,
    });
    expect(DISPLAY).toEqual({ factor: 1.25, maxZoom: 8, loupeFactor: 3 });
  });

  it("refuse a description that turns smoothing, the display window or the zoom percentage on", () => {
    const base = JSON.parse(fs.readFileSync(CONFIG, "utf8")) as Record<string, unknown>;
    for (const key of ["smoothing", "display_window", "zoom_percentage"]) {
      expect(() => studyDisplay({ ...base, [key]: true }), key).toThrow();
    }
    expect(() => studyDisplay({ ...base, format_version: 2 })).toThrow();
  });

  it("step zoom as fit x 1.25^k up to the last step not above 8", () => {
    const fit = 1.7097;
    const levels = zoomLevels(fit);
    expect(levels[0]).toBe(fit);
    expect(levels.every((zoom, k) => zoom === fit * 1.25 ** k)).toBe(true);
    expect(levels[levels.length - 1]).toBeLessThanOrEqual(8);
    expect((levels[levels.length - 1] as number) * 1.25).toBeGreaterThan(8);
    expect(levels).toHaveLength(7);
    expect(zoomLevels(8)).toEqual([8]);
    expect(zoomLevels(12)).toEqual([8]);
    expect(zoomLevels(8 / 1.25 ** 3)).toHaveLength(4);
  });
});
