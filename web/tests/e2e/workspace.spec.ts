import fs from "node:fs";

import type { Page } from "@playwright/test";

import { en } from "../../src/strings/en.ts";
import { fill } from "../../src/strings/format.ts";
import { expect, test, type Recorder } from "./fixtures.ts";
import {
  canvasView,
  CANVAS,
  boot,
  clickPixel,
  fixturePath,
  LOUPE_CANVAS,
  manifest,
  openImage,
  OVERLAY,
  pendingDots,
  pixelCentre,
  readback,
  rgba8,
  samples,
  WORKSPACE,
  zoomTo,
  type CanvasView,
  type FixtureEntry,
} from "./workspace_view.ts";

const GREY = "png_grey8_37x29.png";
const STAGE_MARGIN = 24;
const LADDER = [1 / 32, 1 / 16, 1 / 8, 1 / 6, 1 / 4, 1 / 3, 1 / 2, 2 / 3, 1, 1.5, 2, 3, 4, 6, 8, 12, 16, 24, 32];
const SARCOMERE: readonly (readonly [number, number])[] = [
  [10, 5],
  [10, 40],
  [30, 7],
  [30, 38],
  [50, 5],
  [50, 40],
];

function ladder(from: number, direction: 1 | -1, count: number): number[] {
  const steps = direction > 0 ? LADDER.filter((zoom) => zoom > from) : LADDER.filter((zoom) => zoom < from).reverse();
  const end = direction > 0 ? (LADDER.at(-1) as number) : (LADDER[0] as number);
  return Array.from({ length: count }, (_, index) => steps[index] ?? end);
}

function entry(name: string): FixtureEntry {
  const found = manifest().images[name];
  if (found === undefined) {
    throw new Error(`${name} is not in the fixture manifest`);
  }
  return found;
}

async function clicks(recorder: Recorder): Promise<{ x: number; y: number }[]> {
  return (await recorder.calls("app")).filter((call) => call.cmd === "shape.click").map((call) => call.args as { x: number; y: number });
}

async function placed(page: Page, recorder: Recorder, points: readonly (readonly [number, number])[]): Promise<void> {
  for (const [x, y] of points) {
    const before = await pendingDots(page).count();
    await clickPixel(page, x, y);
    await expect(pendingDots(page)).toHaveCount(before + 1);
  }
  const sent = (await clicks(recorder)).slice(-points.length);
  expect(sent).toEqual(points.map(([x, y]) => ({ x, y })));
}

function loupeAt(page: Page): Promise<{ left: number; top: number; width: number; zoom: number }> {
  return page.locator(LOUPE_CANVAS).evaluate((canvas: HTMLCanvasElement) => {
    const rect = canvas.getBoundingClientRect();
    return { left: rect.left, top: rect.top, width: rect.width, zoom: Number(canvas.dataset.zoom) };
  });
}

function smoothing(page: Page, selector: string): Promise<boolean | null> {
  return page.locator(selector).evaluate((canvas: HTMLCanvasElement) => canvas.getContext("2d")?.imageSmoothingEnabled ?? null);
}

test.describe("image decoding", () => {
  test("every fixture decodes to the stored grid that Pillow reads, with its size and SHA-256, and only the 16-bit PNG shows the notice", async ({ page, recorder }, testInfo) => {
    await boot(page);
    const rotated = await page.evaluate(async (bytes) => {
      const bitmap = await createImageBitmap(new Blob([new Uint8Array(bytes)], { type: "image/jpeg" }), { colorSpaceConversion: "none", premultiplyAlpha: "none" });
      return [bitmap.width, bitmap.height];
    }, [...fs.readFileSync(fixturePath("jpeg_grey_o6_40x24.jpg"))]);
    expect(rotated, "the browser alone applies EXIF orientation 6, so the app must patch it").toEqual([24, 40]);
    const images = manifest().images;
    const report: Record<string, unknown> = { browserAloneOrientation6: rotated };
    for (const [name, fixture] of Object.entries(images)) {
      await openImage(page, name);
      const opened = (await recorder.calls("app")).filter((call) => call.cmd === "doc.new").at(-1);
      expect(opened?.args, name).toEqual({ image: { file_name: name, sha256: fixture.sha256, width_px: fixture.width, height_px: fixture.height } });
      await expect(page.locator(".topbar__file-meta")).toHaveText(fill(en.topbar.size, { width: fixture.width, height: fixture.height }));
      const notice = page.locator("[data-part='image-notice']");
      if (fixture.bit_depth === 16) {
        await expect(notice).toBeVisible();
        await expect(notice).toHaveText(en.workspace.sixteenBit);
      } else {
        await expect(notice).toBeHidden();
      }
      await expect(page.locator("[data-part='image-caption']")).toBeHidden();
      await zoomTo(page, 1);
      if (fixture.dtype === "u16le") {
        const high = samples(fixture).flatMap((v) => [v >> 8, v >> 8, v >> 8, 255]);
        const scaled = samples(fixture).flatMap((v) => [Math.round((v * 255) / 65535), Math.round((v * 255) / 65535), Math.round((v * 255) / 65535), 255]);
        const near = await readback(page, CANVAS, fixture.width, fixture.height, high, 1);
        expect(near.mismatches, `${name} within 1 of the high byte`).toBe(0);
        const byHigh = await readback(page, CANVAS, fixture.width, fixture.height, high);
        const byScale = await readback(page, CANVAS, fixture.width, fixture.height, scaled);
        report[name] = { highByteMismatches: byHigh.mismatches, scaledMismatches: byScale.mismatches, inside: near.inside };
      } else {
        const result = await readback(page, CANVAS, fixture.width, fixture.height, rgba8(fixture));
        expect(result.inside, name).toBe(fixture.width * fixture.height);
        expect(result.first, name).toBeNull();
        expect(result.mismatches, name).toBe(0);
        report[name] = { mismatches: result.mismatches, inside: result.inside, orientation: fixture.orientation };
      }
    }
    const file = testInfo.outputPath("decoded-fixtures.json");
    fs.writeFileSync(file, `${JSON.stringify(report, null, 2)}\n`);
    await testInfo.attach("decoded-fixtures.json", { path: file, contentType: "application/json" });
  });
});

for (const dpr of [1, 2] as const) {
  test.describe(`canvas readback at device pixel ratio ${dpr}`, () => {
    test.use({ deviceScaleFactor: dpr });

    test(`100%, 200% and 400% and the ×3 loupe show uniform blocks of the source value, smoothing off (DPR ${dpr})`, async ({ page }) => {
      await boot(page);
      await openImage(page, GREY);
      const fixture = entry(GREY);
      const expected = rgba8(fixture);
      expect(await smoothing(page, CANVAS)).toBe(false);
      for (const zoom of [1, 2, 4]) {
        const view = await zoomTo(page, zoom);
        expect(view.backingWidth).toBe(view.width * dpr);
        const image = await readback(page, CANVAS, fixture.width, fixture.height, expected);
        expect(image.inside).toBe(fixture.width * fixture.height * (zoom * dpr) ** 2);
        expect(image.first).toBeNull();
        const target = { x: 11, y: 17 };
        const at = pixelCentre(view, target.x, target.y);
        await page.mouse.move(at.x, at.y);
        const loupe = page.locator("[data-part='loupe']");
        await expect(loupe).toBeVisible();
        expect(await smoothing(page, LOUPE_CANVAS)).toBe(false);
        const geometry = await loupeAt(page);
        expect(geometry.zoom).toBe(zoom * 3);
        const magnified = await readback(page, LOUPE_CANVAS, fixture.width, fixture.height, expected);
        expect(magnified.first).toBeNull();
        expect(magnified.inside).toBeGreaterThan(0);
        const middle = await page.locator(LOUPE_CANVAS).evaluate((canvas: HTMLCanvasElement) => {
          const data = canvas.getContext("2d")?.getImageData(Math.floor(canvas.width / 2), Math.floor(canvas.height / 2), 1, 1).data;
          return data === undefined ? [] : [...data];
        });
        const value = samples(fixture)[target.y * fixture.width + target.x];
        expect(middle).toEqual([value, value, value, 255]);
      }
    });
  });
}

test.describe("view tools", () => {
  test("the loupe follows the cursor with a crosshair and its ×3 label, and hides off the image, while panning and when the pointer leaves", async ({ page }) => {
    await boot(page);
    await openImage(page, GREY);
    const loupe = page.locator("[data-part='loupe']");
    await expect(loupe).toBeHidden();
    const view = await zoomTo(page, 4);
    const first = pixelCentre(view, 5, 5);
    await page.mouse.move(first.x, first.y);
    await expect(loupe).toBeVisible();
    await expect(loupe.locator(".loupe__label")).toHaveText(fill(en.workspace.loupe, { factor: 3 }));
    await expect(loupe.locator(".loupe__cross")).toHaveCount(2);
    const one = await loupeAt(page);
    expect(one.width).toBe(116);
    const second = pixelCentre(view, 20, 12);
    await page.mouse.move(second.x, second.y);
    const two = await loupeAt(page);
    expect(two.left - one.left).toBeCloseTo(second.x - first.x, 5);
    expect(two.top - one.top).toBeCloseTo(second.y - first.y, 5);
    await page.mouse.move(view.left + 4, view.top + 4);
    await expect(loupe).toBeHidden();
    await page.mouse.move(second.x, second.y);
    await expect(loupe).toBeVisible();
    await page.keyboard.down("Space");
    await expect(loupe).toBeHidden();
    await page.keyboard.up("Space");
    await expect(loupe).toBeVisible();
    await page.mouse.move(view.left + view.width + 100, view.top + 10);
    await expect(loupe).toBeHidden();
  });

  test("the scale bar is hidden without a scale and shows 1 µm, or a round length when 1 µm does not fit, once a scale is set", async ({ page, recorder, engine }) => {
    await boot(page);
    await openImage(page, GREY);
    const bar = page.locator("[data-part='scalebar']");
    await expect(bar).toBeHidden();
    await zoomTo(page, 1);
    await engine.call("doc.set_scale", { pixel_size: 6.62, unit: "nm" });
    await placed(page, recorder, [[3, 3]]);
    await page.keyboard.press("Backspace");
    await expect(pendingDots(page)).toHaveCount(0);
    await expect(bar).toBeVisible();
    await expect(bar.locator(".scalebar__label")).toHaveText(fill(en.workspace.scaleBar, { length: 1, unit: "µm" }));
    await expect(bar).toHaveAttribute("aria-label", fill(en.workspace.scaleBarLabel, { label: "1 µm" }));
    const width = async (): Promise<number> => (await bar.locator(".scalebar__bar").boundingBox())?.width ?? 0;
    expect(await width()).toBeCloseTo(1000 / 6.62, 1);
    await zoomTo(page, 4);
    await expect(bar.locator(".scalebar__label")).toHaveText(fill(en.workspace.scaleBar, { length: 200, unit: "nm" }));
    expect(await width()).toBeCloseTo((4 * 200) / 6.62, 1);
    await engine.call("doc.set_scale", { pixel_size: 1, unit: "px" });
    await placed(page, recorder, [[3, 3]]);
    await page.keyboard.press("Backspace");
    await expect(bar).toBeHidden();
  });

  test("+ and − step through the zoom ladder, F fits, and the wheel zooms around the cursor at once, without animating the image", async ({ page }) => {
    await boot(page);
    await openImage(page, "grey_64x48.png");
    const fixture = entry("grey_64x48.png");
    const fitted = await canvasView(page);
    const fit = Math.min((fitted.width - 2 * STAGE_MARGIN) / fixture.width, (fitted.height - 2 * STAGE_MARGIN) / fixture.height);
    expect(fitted.zoom).toBe(fit);
    expect(fitted.offsetX).toBe(Math.round(((fitted.width - fit * fixture.width) / 2) * fitted.dpr) / fitted.dpr);
    const zoomBox = page.locator("[data-part='zoom']");
    await expect(zoomBox).toHaveText(fill(en.workspace.zoom, { percent: Math.round(fit * 100) }));
    const down: number[] = [];
    for (let step = 0; step < 12; step += 1) {
      await page.keyboard.press("-");
      down.push((await canvasView(page)).zoom);
    }
    expect(down).toEqual(ladder(fit, -1, 12));
    await page.keyboard.press("f");
    expect((await canvasView(page)).zoom).toBe(fit);
    const up: number[] = [];
    for (let step = 0; step < 4; step += 1) {
      await page.keyboard.press("+");
      up.push((await canvasView(page)).zoom);
    }
    expect(up).toEqual(ladder(fit, 1, 4));
    await expect(zoomBox).toHaveText(fill(en.workspace.zoom, { percent: Math.round((up.at(-1) ?? 0) * 100) }));
    await page.locator("[data-action='fit']").click();
    expect((await canvasView(page)).zoom).toBe(fit);
    await page.locator("[data-action='zoom-out']").click();
    expect((await canvasView(page)).zoom).toBe(LADDER.filter((zoom) => zoom < fit).at(-1));
    await page.locator("[data-action='zoom-in']").click();
    expect((await canvasView(page)).zoom).toBe(LADDER.filter((zoom) => zoom > fit)[0]);

    const two = await zoomTo(page, 2);
    const hover = pixelCentre(two, 6, 21);
    await page.mouse.move(hover.x, hover.y);
    const under = (view: CanvasView): { x: number; y: number } => ({ x: (hover.x - view.left - view.offsetX) / view.zoom, y: (hover.y - view.top - view.offsetY) / view.zoom });
    const before = under(two);
    await page.mouse.wheel(0, -100);
    const three = await canvasView(page);
    expect(three.zoom).toBe(3);
    const after = under(three);
    const snapBound = 0.5 / (three.zoom * three.dpr) + 1e-9;
    expect(Math.abs(after.x - before.x)).toBeLessThanOrEqual(snapBound);
    expect(Math.abs(after.y - before.y)).toBeLessThanOrEqual(snapBound);
    const instant = await readback(page, CANVAS, fixture.width, fixture.height, rgba8(fixture));
    expect(instant.first).toBeNull();
    const motion = await page.locator(CANVAS).evaluate((canvas: HTMLCanvasElement) => {
      const style = getComputedStyle(canvas);
      return { animations: canvas.getAnimations().length, transition: style.transitionDuration, animation: style.animationName };
    });
    expect(motion).toEqual({ animations: 0, transition: "0s", animation: "none" });
    await page.mouse.wheel(0, 100);
    expect((await canvasView(page)).zoom).toBe(2);
  });

  test("held Space pans by dragging, the pan tool pans, and a drag with the annotate tool pans without placing a landmark", async ({ page, recorder }) => {
    await boot(page);
    await openImage(page, GREY);
    const fixture = entry(GREY);
    const canvas = page.locator(CANVAS);
    const panButton = page.locator("[data-action='pan']");
    const annotateButton = page.locator("[data-action='annotate']");
    const start = await zoomTo(page, 2);
    await expect(annotateButton).toHaveAttribute("aria-pressed", "true");
    const from = pixelCentre(start, 18, 14);
    await page.mouse.move(from.x, from.y);
    await page.keyboard.down("Space");
    await expect(canvas).toHaveClass(/workspace__canvas--pan/);
    await expect(panButton).toHaveAttribute("aria-pressed", "true");
    await page.mouse.down();
    await page.mouse.move(from.x + 40, from.y - 30, { steps: 4 });
    await expect(canvas).toHaveClass(/workspace__canvas--panning/);
    await page.mouse.up();
    await page.keyboard.up("Space");
    await expect(canvas).not.toHaveClass(/workspace__canvas--pan/);
    const spaced = await canvasView(page);
    expect([spaced.offsetX - start.offsetX, spaced.offsetY - start.offsetY]).toEqual([40, -30]);
    expect((await readback(page, CANVAS, fixture.width, fixture.height, rgba8(fixture))).first).toBeNull();

    const here = pixelCentre(spaced, 18, 14);
    await page.mouse.move(here.x, here.y);
    await page.mouse.down();
    await page.mouse.move(here.x - 25, here.y + 15, { steps: 5 });
    await page.mouse.up();
    const dragged = await canvasView(page);
    expect([dragged.offsetX - spaced.offsetX, dragged.offsetY - spaced.offsetY]).toEqual([-25, 15]);

    await panButton.click();
    await expect(panButton).toHaveAttribute("aria-pressed", "true");
    await expect(annotateButton).toHaveAttribute("aria-pressed", "false");
    const there = pixelCentre(dragged, 18, 14);
    await page.mouse.move(there.x, there.y);
    await page.mouse.down();
    await page.mouse.move(there.x + 2, there.y + 1);
    await page.mouse.up();
    const nudged = await canvasView(page);
    expect([nudged.offsetX - dragged.offsetX, nudged.offsetY - dragged.offsetY]).toEqual([2, 1]);
    await page.keyboard.press("a");
    await expect(annotateButton).toHaveAttribute("aria-pressed", "true");
    expect(await clicks(recorder)).toEqual([]);
    await expect(pendingDots(page)).toHaveCount(0);
  });
});

test.describe("landmark overlays", () => {
  test("marks and strokes are drawn at display + 0.5 from the engine's state with the landmark palette, letter badges, a dashed M-line and a halo under every stroke; L toggles them", async ({ page, recorder }) => {
    await boot(page);
    await openImage(page, "grey_64x48.png");
    const view = await zoomTo(page, 8);
    await placed(page, recorder, SARCOMERE);
    const overlay = page.locator(OVERLAY);
    const pending = overlay.locator(".ov__item--pending");
    const dots = await pending.locator(".ov__dot").evaluateAll((nodes) =>
      nodes.map((node) => ({ role: node.getAttribute("data-role"), cx: Number(node.getAttribute("cx")), cy: Number(node.getAttribute("cy")), fill: getComputedStyle(node).fill })),
    );
    const svgBox = await overlay.evaluate((svg) => {
      const rect = svg.getBoundingClientRect();
      return { left: rect.left, top: rect.top };
    });
    expect(svgBox).toEqual({ left: view.left, top: view.top });
    const z = "rgb(86, 180, 233)";
    const m = "rgb(230, 159, 0)";
    expect(dots).toEqual(
      SARCOMERE.map(([x, y], index) => ({
        role: "ABCDEF"[index],
        cx: view.offsetX + view.zoom * (x + 0.5),
        cy: view.offsetY + view.zoom * (y + 0.5),
        fill: index === 2 || index === 3 ? m : z,
      })),
    );
    await expect(pending.locator(".ov__badge")).toHaveText(["A", "B", "C", "D", "E", "F"]);
    const badgeFills = await pending.locator(".ov__badge-disc").evaluateAll((nodes) => nodes.map((node) => getComputedStyle(node).fill));
    expect(badgeFills).toEqual([z, z, m, m, z, z]);
    const strokes = await pending.locator("line").evaluateAll((nodes) =>
      nodes.map((node) => {
        const style = getComputedStyle(node);
        return {
          kind: node.getAttribute("class"),
          role: node.getAttribute("data-role"),
          ends: ["x1", "y1", "x2", "y2"].map((name) => Number(node.getAttribute(name))),
          stroke: style.stroke,
          width: style.strokeWidth,
          dash: style.strokeDasharray,
        };
      }),
    );
    expect(strokes).toHaveLength(6);
    for (let index = 0; index < strokes.length; index += 2) {
      const halo = strokes[index];
      const line = strokes[index + 1];
      expect(halo?.kind).toBe("ov__halo");
      expect(halo?.stroke).toBe("rgba(0, 0, 0, 0.62)");
      expect(halo?.width).toBe("5.5px");
      expect(line?.ends).toEqual(halo?.ends);
    }
    const lines = strokes.filter((stroke) => stroke.kind !== "ov__halo");
    expect(lines.map((line) => [line.role, line.stroke, line.dash === "none" ? "solid" : "dashed"])).toEqual([
      ["z1", z, "solid"],
      ["m", m, "dashed"],
      ["z2", z, "solid"],
    ]);
    const local = (x: number, y: number): number[] => [view.offsetX + view.zoom * (x + 0.5), view.offsetY + view.zoom * (y + 0.5)];
    expect(lines[0]?.ends).toEqual([...local(10, 5), ...local(10, 40)]);

    const layersButton = page.locator("[data-action='layers']");
    await expect(layersButton).toHaveAttribute("aria-pressed", "true");
    await page.keyboard.press("l");
    await expect(overlay).toBeHidden();
    await expect(layersButton).toHaveAttribute("aria-pressed", "false");
    await page.keyboard.press("l");
    await expect(overlay).toBeVisible();

    await page.keyboard.press("Enter");
    await expect(overlay.locator(".ov__item--pending")).toHaveCount(0);
    const item = overlay.locator(".ov__item");
    await expect(item).toHaveCount(1);
    await expect(item.locator(".ov__badge")).toHaveCount(0);
    await expect(item.locator(".ov__side")).toHaveCount(2);
    await expect(item.locator(".ov__tag-text")).toHaveText("1");
    await expect(item.locator(".ov__dot")).toHaveCount(6);
    const zoomed = await zoomTo(page, 4);
    const moved = await item.locator(".ov__dot").first().evaluate((node) => [Number(node.getAttribute("cx")), Number(node.getAttribute("cy"))]);
    expect(moved).toEqual([zoomed.offsetX + 4 * 10.5, zoomed.offsetY + 4 * 5.5]);
  });

  test("click to mark drawn: 30 clicks, latency recorded", async ({ page, recorder }, testInfo) => {
    await page.addInitScript(() => {
      const downs: number[] = [];
      const marks: number[] = [];
      Object.defineProperty(globalThis, "__sarcostatTestLatency", { value: { downs, marks }, configurable: false, writable: false });
      window.addEventListener("pointerdown", () => downs.push(performance.now()), { capture: true });
      let last = 0;
      new MutationObserver(() => {
        const count = document.querySelectorAll("[data-part='overlay'] .ov__dot").length;
        if (count > last) {
          requestAnimationFrame(() => marks.push(performance.now()));
        }
        last = count;
      }).observe(document, { subtree: true, childList: true });
    });
    await boot(page);
    await openImage(page, "grey_64x48.png");
    const dots = page.locator(`${OVERLAY} .ov__dot`);
    for (let sarcomere = 0; sarcomere < 5; sarcomere += 1) {
      for (const [x, y] of SARCOMERE) {
        const before = await dots.count();
        await clickPixel(page, x + sarcomere, y);
        await expect(dots).toHaveCount(before + 1);
      }
      await page.keyboard.press("Enter");
      await expect(pendingDots(page)).toHaveCount(0);
    }
    expect(await clicks(recorder)).toHaveLength(30);
    const timing = await page.evaluate(() => (globalThis as unknown as { __sarcostatTestLatency: { downs: number[]; marks: number[] } }).__sarcostatTestLatency);
    expect(timing.downs).toHaveLength(30);
    await expect.poll(async () => (await page.evaluate(() => (globalThis as unknown as { __sarcostatTestLatency: { marks: number[] } }).__sarcostatTestLatency.marks.length))).toBe(30);
    const marks = await page.evaluate(() => (globalThis as unknown as { __sarcostatTestLatency: { marks: number[] } }).__sarcostatTestLatency.marks);
    const latencies = timing.downs.map((down, index) => (marks[index] ?? Number.NaN) - down);
    const sorted = [...latencies].sort((a, b) => a - b);
    const p95 = sorted[Math.ceil(0.95 * sorted.length) - 1] ?? Number.NaN;
    const file = testInfo.outputPath("click-latency.json");
    fs.writeFileSync(file, `${JSON.stringify({ clicks: latencies.length, p95_ms: p95, median_ms: sorted[Math.floor(sorted.length / 2)], max_ms: sorted.at(-1), latencies_ms: latencies }, null, 2)}\n`);
    await testInfo.attach("click-latency.json", { path: file, contentType: "application/json" });
    expect(latencies.every((value) => Number.isFinite(value) && value > 0)).toBe(true);
    expect(p95).toBeLessThan(1000);
    await expect(page.locator(WORKSPACE)).toHaveAttribute("data-state", "drawn");
  });
});
