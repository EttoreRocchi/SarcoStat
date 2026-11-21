import { describe, expect, it } from "vitest";

import { placement, visibleSpan } from "../../src/view/workspace/canvas.ts";
import { loupePosition, loupeView, LOUPE_SIZE } from "../../src/view/workspace/loupe.ts";
import {
  centred,
  clampView,
  clientToDisplay,
  displayToLocal,
  FIT_MARGIN,
  fitView,
  fitZoom,
  insideImage,
  KEEP_VISIBLE,
  localToImage,
  MAX_ZOOM,
  MIN_ZOOM,
  panBy,
  stepZoom,
  ZOOM_STEPS,
  zoomAround,
  type View,
} from "../../src/view/workspace/viewport.ts";
import { deviceExtent } from "../../src/view/workspace/workspace.ts";

const ORIGIN = { left: 80, top: 52 };

function climb(from: number, direction: 1 | -1, target: number): number[] {
  const out: number[] = [];
  let zoom = from;
  for (let step = 0; step < ZOOM_STEPS.length + 2 && zoom !== target; step += 1) {
    zoom = stepZoom(zoom, direction);
    out.push(zoom);
  }
  return out;
}

describe("the pixel-centre mapping (plan §3.6)", () => {
  it("is (clientX − rect.left − off) / z − 0.5 per axis, bit for bit", () => {
    const views: View[] = [
      { zoom: 1, offsetX: 457, offsetY: 387 },
      { zoom: 2, offsetX: 427.5, offsetY: 363.5 },
      { zoom: 4, offsetX: -1234.5, offsetY: 17 },
      { zoom: 14.5, offsetX: 24, offsetY: 61.5 },
      { zoom: 2 / 3, offsetX: 301.25, offsetY: 0.75 },
    ];
    let seed = 7;
    const next = (): number => {
      seed = (seed * 1103515245 + 12345) % 2147483648;
      return seed / 2147483648;
    };
    for (const view of views) {
      for (let i = 0; i < 200; i += 1) {
        const clientX = 80 + next() * 976;
        const clientY = 52 + next() * 820;
        const got = clientToDisplay(clientX, clientY, ORIGIN, view);
        expect(Object.is(got.x, (clientX - ORIGIN.left - view.offsetX) / view.zoom - 0.5)).toBe(true);
        expect(Object.is(got.y, (clientY - ORIGIN.top - view.offsetY) / view.zoom - 0.5)).toBe(true);
      }
    }
  });

  it("maps the visual centre of pixel i to i exactly and its edges to i ± 0.5 at the integer zooms and offsets on the device grid", () => {
    for (const zoom of [1, 2, 4, 8, 32]) {
      for (const offsetX of [0, 0.5, 457, 427.5, -96]) {
        const view = { zoom, offsetX, offsetY: offsetX };
        for (const i of [0, 1, 37, 1023]) {
          const centre = ORIGIN.left + offsetX + zoom * (i + 0.5);
          expect(clientToDisplay(centre, centre - ORIGIN.left + ORIGIN.top, ORIGIN, view)).toEqual({ x: i, y: i });
          expect(clientToDisplay(ORIGIN.left + offsetX + zoom * i, ORIGIN.top, ORIGIN, view).x).toBe(i - 0.5);
          expect(displayToLocal({ x: i, y: i }, view)).toEqual({ x: centre - ORIGIN.left, y: centre - ORIGIN.left });
        }
      }
    }
  });

  it("treats pixel i as [i − 0.5, i + 0.5) when it decides whether a point is on the image", () => {
    const image = { width: 61, height: 47 };
    expect(insideImage({ x: -0.5, y: -0.5 }, image)).toBe(true);
    expect(insideImage({ x: 60.49, y: 46.49 }, image)).toBe(true);
    expect(insideImage({ x: 60.5, y: 0 }, image)).toBe(false);
    expect(insideImage({ x: 0, y: -0.51 }, image)).toBe(false);
  });
});

describe("zoom steps", () => {
  it("reach exactly 1, 2 and 4 CSS px per image px with + and − from any fit", () => {
    for (const fit of [0.0417, 0.443, 1.1, 3.7, 14.5, 25.08]) {
      for (const target of [1, 2, 4]) {
        const path = climb(fit, target > fit ? 1 : -1, target);
        expect(path.at(-1), `${fit} → ${target}`).toBe(target);
      }
    }
  });

  it("go one ladder step at a time and stop at the ends", () => {
    expect(stepZoom(1, 1)).toBe(1.5);
    expect(stepZoom(1, -1)).toBe(2 / 3);
    expect(stepZoom(1.2, -1)).toBe(1);
    expect(stepZoom(MAX_ZOOM, 1)).toBe(MAX_ZOOM);
    expect(stepZoom(MIN_ZOOM, -1)).toBe(MIN_ZOOM);
    expect(stepZoom(100, -1)).toBe(MAX_ZOOM);
    expect([...ZOOM_STEPS].sort((a, b) => a - b)).toEqual(ZOOM_STEPS);
  });
});

describe("fit, zoom around a point, pan", () => {
  it("fits with a 24 px margin, centred and snapped to device pixels", () => {
    const stage = { width: 976, height: 820 };
    const image = { width: 37, height: 29 };
    const zoom = Math.min((976 - 2 * FIT_MARGIN) / 37, (820 - 2 * FIT_MARGIN) / 29);
    expect(fitZoom(image, stage)).toBe(zoom);
    for (const dpr of [1, 1.5, 2]) {
      const view = fitView(image, stage, dpr);
      expect(view.zoom).toBe(zoom);
      expect(Math.abs(view.offsetX * dpr - Math.round(view.offsetX * dpr))).toBeLessThan(1e-9);
      expect(Math.abs(view.offsetX - (976 - zoom * 37) / 2)).toBeLessThanOrEqual(0.5 / dpr);
    }
    expect(fitZoom({ width: 2, height: 2 }, stage)).toBe(MAX_ZOOM);
    expect(fitZoom({ width: 100000, height: 100000 }, stage)).toBe(MIN_ZOOM);
  });

  it("keeps the image point under the anchor within half a device pixel", () => {
    const view = centred(2, { width: 61, height: 47 }, { width: 976, height: 820 }, 2);
    const anchor = { x: 523.25, y: 401.75 };
    const before = localToImage(anchor, view);
    for (const zoom of [3, 4, 1.5, 1 / 3]) {
      const after = localToImage(anchor, zoomAround(view, zoom, anchor, 2));
      expect(Math.abs(after.x - before.x) * zoom * 2).toBeLessThanOrEqual(0.5 + 1e-9);
      expect(Math.abs(after.y - before.y) * zoom * 2).toBeLessThanOrEqual(0.5 + 1e-9);
    }
  });

  it("pans by the pointer delta on the device grid and keeps part of the image in view", () => {
    const view = { zoom: 2, offsetX: 100, offsetY: 50 };
    expect(panBy(view, 40, -30, 1)).toEqual({ zoom: 2, offsetX: 140, offsetY: 20 });
    expect(panBy(view, 0.3, 0.3, 2)).toEqual({ zoom: 2, offsetX: 100.5, offsetY: 50.5 });
    const image = { width: 37, height: 29 };
    const stage = { width: 976, height: 820 };
    expect(clampView({ zoom: 2, offsetX: 5000, offsetY: -5000 }, image, stage, 1)).toEqual({ zoom: 2, offsetX: 976 - KEEP_VISIBLE, offsetY: KEEP_VISIBLE - 58 });
    expect(clampView(view, image, stage, 1)).toBe(view);
  });
});

describe("drawing geometry", () => {
  it("draws only the visible source pixels, on whole device pixels at integer zooms", () => {
    expect(visibleSpan(-30, 4, 100, 64)).toEqual({ first: 7, last: 33 });
    expect(visibleSpan(10, 1, 100, 64)).toEqual({ first: 0, last: 64 });
    expect(visibleSpan(200, 1, 100, 64)).toEqual({ first: 0, last: 0 });
    const at = placement({ width: 64, height: 48 }, { zoom: 4, offsetX: -7.5, offsetY: 3 }, { width: 200, height: 100, dpr: 2 });
    expect(at).toEqual({ sx: 1, sy: 0, sw: 26, sh: 12, dx: -7, dy: 6, dw: 208, dh: 96 });
    expect(placement({ width: 4, height: 4 }, { zoom: 1, offsetX: 500, offsetY: 0 }, { width: 100, height: 100, dpr: 1 })).toBeNull();
  });

  it("takes the device size from device-pixel-content-box only when it agrees with the CSS size", () => {
    expect(deviceExtent(1952, 976, 2)).toBe(1952);
    expect(deviceExtent(976, 976, 2)).toBe(1952);
    expect(deviceExtent(undefined, 976.4, 1.25)).toBe(1221);
  });
});

describe("the loupe", () => {
  it("magnifies three times around the image point under the cursor and sits up and to the right of it, inside the stage", () => {
    const view = { zoom: 2, offsetX: 427, offsetY: 365 };
    const point = { x: 37.5, y: 22.5 };
    const loupe = loupeView(point, view, 1);
    expect(loupe.zoom).toBe(6);
    expect(loupe.offsetX + loupe.zoom * point.x).toBe(LOUPE_SIZE / 2);
    const stage = { width: 976, height: 820 };
    expect(loupePosition({ x: 500, y: 400 }, stage)).toEqual({ x: 500 + 94 - 58, y: 400 - 76 - 58 });
    expect(loupePosition({ x: 950, y: 400 }, stage).x).toBe(950 - 94 - 58);
    expect(loupePosition({ x: 500, y: 20 }, stage).y).toBe(20 + 76 - 58);
  });
});
