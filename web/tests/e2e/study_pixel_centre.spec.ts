import fs from "node:fs";
import path from "node:path";

import { expect, test } from "./fixtures.ts";
import {
  checkBrowser,
  confirmIdentity,
  download,
  enterStudy,
  installStudyPointerProbe,
  KITS_DIR,
  openKit,
  pixelCentre,
  probedDowns,
  quantize,
  readManifest,
  seq,
  STUDY_CANVAS,
  STUDY_STAGE,
  studyView,
  toOriginal,
  TRANSFORMS,
  wheelTo,
  type ManifestItem,
  type ProbedDown,
} from "./study_view.ts";
import { referenceMapping } from "./workspace_view.ts";

interface MarkedDescription {
  marked: { x: number; y: number };
  kits: Record<string, { display: [number, number]; display_size: [number, number] }>;
}

const MARKED_DIR = path.join(KITS_DIR, "marked");
const MARKED = JSON.parse(fs.readFileSync(path.join(MARKED_DIR, "marked.json"), "utf8")) as MarkedDescription;
const STEPS = [0, 1, 3] as const;
const OFFSETS: readonly (readonly [number, number])[] = [
  [8, 5],
  [8, -5],
  [-8, 5],
];
const ROLES = ["z1", "m", "z2"] as const;

interface FinishedItem {
  raw_clicks: { x: number; y: number }[];
  points: Record<(typeof ROLES)[number], [[number, number], [number, number]]>;
}

for (const dpr of [1, 2] as const) {
  test.describe(`study pixel-centre mapping at device pixel ratio ${dpr}`, () => {
    test.use({ deviceScaleFactor: dpr });

    for (const transform of TRANSFORMS) {
      test(`${transform}: a click at the visual centre of the marked pixel records the original pixel through DisplayMapping.to_original, at fit, fit x 1.25 and fit x 1.25^3 (DPR ${dpr})`, async ({ page, recorder }, testInfo) => {
        test.setTimeout(120_000);
        const kit = path.join(MARKED_DIR, transform);
        const item = readManifest(kit).items[0] as ManifestItem;
        const [mx, my] = MARKED.kits[transform]?.display as [number, number];
        expect(toOriginal(item, [mx, my])).toEqual([MARKED.marked.x, MARKED.marked.y]);
        await page.addInitScript(installStudyPointerProbe);
        await enterStudy(page);
        await checkBrowser(page);
        await openKit(page, kit);
        await confirmIdentity(page);
        const fit = await studyView(page);
        const cases: { step: number; down: ProbedDown; centre: { x: number; y: number } }[] = [];
        for (const [index, step] of STEPS.entries()) {
          const view = await wheelTo(page, step);
          expect(view.zoom).toBe(fit.zoom * 1.25 ** step);
          expect(view.dpr).toBe(dpr);
          expect(view.backingWidth).toBe(Math.round(view.width * dpr));
          const [dx, dy] = OFFSETS[index] as [number, number];
          for (const [x, y] of [
            [mx, my],
            [mx + dx, my + dy],
          ] as const) {
            const centre = pixelCentre(view, x, y);
            const hit = await page.evaluate(([px, py]) => (document.elementFromPoint(px, py) as HTMLElement | null)?.dataset.part ?? null, [centre.x, centre.y] as const);
            expect(hit, `the study canvas is under display pixel ${x} ${y}`).toBe("study-canvas");
            const probed = (await probedDowns(page)).length;
            const before = await seq(page);
            await page.mouse.click(centre.x, centre.y);
            await expect.poll(() => seq(page)).toBeGreaterThan(before);
            const down = (await probedDowns(page, probed)).find((entry) => entry.part === "study-canvas") as ProbedDown;
            expect(down.view).toEqual(view);
            if (x === mx && y === my) {
              cases.push({ step, down, centre });
            }
          }
        }
        let top = await zoomSteps(page);
        expect(top).toBeGreaterThan(3);
        const highest = await wheelTo(page, top - 1);
        expect(highest.zoom).toBeGreaterThan(6.4);
        expect(highest.zoom).toBeLessThanOrEqual(8);
        top = await zoomSteps(page);
        const sent = (await recorder.calls("app")).filter((call) => call.cmd === "study.click").map((call) => call.args as { x: number; y: number });
        expect(sent).toHaveLength(6);
        const file = await download(page, () => page.locator("[data-part='study-dock'] [data-action='next']").click());
        const record = (JSON.parse(file.bytes.toString("utf8")) as { items: FinishedItem[] }).items[0] as FinishedItem;
        const log: Record<string, unknown>[] = [];
        for (const [index, entry] of cases.entries()) {
          const view = entry.down.view as NonNullable<ProbedDown["view"]>;
          const reference = referenceMapping(entry.down.clientX, entry.down.clientY, view);
          const args = sent[index * 2] as { x: number; y: number };
          expect(Object.is(args.x, reference.x) && Object.is(args.y, reference.y), `sent ${JSON.stringify(args)} is the reference mapping ${JSON.stringify(reference)}`).toBe(true);
          const quantized: [number, number] = [quantize(reference.x), quantize(reference.y)];
          const raw = record.raw_clicks[index * 2] as { x: number; y: number };
          expect([raw.x, raw.y]).toEqual(quantized);
          const expected = toOriginal(item, quantized);
          const point = record.points[ROLES[index] as (typeof ROLES)[number]][0];
          expect(Object.is(point[0], expected[0]) && Object.is(point[1], expected[1]), `points ${JSON.stringify(point)} equal to_original ${JSON.stringify(expected)}`).toBe(true);
          const fractional = { x: !Number.isInteger(entry.down.clientX), y: !Number.isInteger(entry.down.clientY) };
          const error = { x: point[0] - MARKED.marked.x, y: point[1] - MARKED.marked.y };
          for (const axis of ["x", "y"] as const) {
            if (fractional[axis]) {
              expect(point[axis === "x" ? 0 : 1], `${axis} with fractional delivery`).toBe(MARKED.marked[axis]);
            } else {
              expect(Math.abs(error[axis]), `${axis} with integer delivery`).toBeLessThan(1 / view.zoom);
            }
          }
          log.push({ transform, dpr, step: entry.step, zoom: view.zoom, centre: entry.centre, delivered: { x: entry.down.clientX, y: entry.down.clientY }, fractional, reference, point, error });
        }
        const out = testInfo.outputPath(`study-pixel-centre-${transform}-dpr${dpr}.json`);
        fs.writeFileSync(out, `${JSON.stringify({ transform, dpr, browser: testInfo.project.name, steps: top, cases: log }, null, 2)}\n`);
        await testInfo.attach(path.basename(out), { path: out, contentType: "application/json" });
        await expect(page.locator(STUDY_CANVAS)).toHaveCount(0);
      });
    }
  });
}

async function zoomSteps(page: import("@playwright/test").Page): Promise<number> {
  return Number(await page.locator(STUDY_STAGE).getAttribute("data-zoom-steps"));
}
