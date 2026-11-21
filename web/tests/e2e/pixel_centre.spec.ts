import fs from "node:fs";

import { en } from "../../src/strings/en.ts";
import { fill } from "../../src/strings/format.ts";
import { expect, test } from "./fixtures.ts";
import { boot, canvasView, manifest, openImage, pendingDots, pixelCentre, referenceMapping, zoomTo } from "./workspace_view.ts";

const MARKED = "marked_61x47.png";
const ZOOMS = [1, 2, 4] as const;

interface Delivered {
  type: string;
  clientX: number;
  clientY: number;
  part: string | null;
}

type Behaviour = "fractional" | "exact integer" | "round" | "truncate" | "other";

function installPointerProbe(): void {
  const log: { type: string; clientX: number; clientY: number; part: string | null }[] = [];
  Object.defineProperty(globalThis, "__sarcostatTestPointer", { value: log, configurable: false, writable: false });
  const record = (event: MouseEvent): void => {
    const target = event.target as HTMLElement | null;
    log.push({ type: event.type, clientX: event.clientX, clientY: event.clientY, part: target?.dataset?.part ?? null });
  };
  window.addEventListener("pointerdown", record, { capture: true });
  window.addEventListener("mousedown", record, { capture: true });
}

function behaviour(delivered: number, centre: number): Behaviour {
  if (!Number.isInteger(delivered)) {
    return delivered === centre ? "fractional" : "other";
  }
  if (delivered === centre) {
    return "exact integer";
  }
  if (delivered === Math.round(centre)) {
    return "round";
  }
  return delivered === Math.trunc(centre) ? "truncate" : "other";
}

for (const dpr of [1, 2] as const) {
  test.describe(`pixel-centre mapping at device pixel ratio ${dpr}`, () => {
    test.use({ deviceScaleFactor: dpr });

    test(`a click at the visual centre of the marked pixel sends the reference mapping of the delivered pointerdown coordinates, at zoom 1, 2 and 4 (DPR ${dpr})`, async ({ page, recorder }, testInfo) => {
      const marked = manifest().images[MARKED]?.marked;
      if (marked === undefined) {
        throw new Error(`${MARKED} has no marked pixel in the manifest`);
      }
      await page.addInitScript(installPointerProbe);
      await boot(page);
      await openImage(page, MARKED);
      const hud = page.locator("[data-part='hud'] .hud__count");
      const log: Record<string, unknown>[] = [];
      for (const zoom of ZOOMS) {
        const view = await zoomTo(page, zoom);
        expect(view.dpr).toBe(dpr);
        expect(view.backingWidth).toBe(Math.round(view.width * dpr));
        const centre = pixelCentre(view, marked.x, marked.y);
        const before = (await recorder.calls("app")).filter((call) => call.cmd === "shape.click").length;
        const probed = await page.evaluate(() => (globalThis as unknown as { __sarcostatTestPointer: unknown[] }).__sarcostatTestPointer.length);
        await page.mouse.click(centre.x, centre.y);
        await expect.poll(async () => (await recorder.calls("app")).filter((call) => call.cmd === "shape.click" && call.received !== null).length).toBe(before + 1);
        const call = (await recorder.calls("app")).filter((entry) => entry.cmd === "shape.click")[before];
        const sent = call?.args as { x: number; y: number };
        const delivered = await page.evaluate(
          (at) => (globalThis as unknown as { __sarcostatTestPointer: Delivered[] }).__sarcostatTestPointer.slice(at),
          probed,
        );
        const down = delivered.find((entry) => entry.type === "pointerdown") as Delivered;
        const mouse = delivered.find((entry) => entry.type === "mousedown") ?? null;
        expect(delivered.filter((entry) => entry.type === "pointerdown")).toHaveLength(1);
        expect(down.part).toBe("image-canvas");
        const after = await canvasView(page);
        expect(after).toEqual(view);
        const reference = referenceMapping(down.clientX, down.clientY, view);
        const kinds = { x: behaviour(down.clientX, centre.x), y: behaviour(down.clientY, centre.y) };
        const error = { x: sent.x - marked.x, y: sent.y - marked.y };
        const mouseMapped = mouse === null ? null : referenceMapping(mouse.clientX, mouse.clientY, view);
        const mouseError = mouseMapped === null ? null : { x: mouseMapped.x - marked.x, y: mouseMapped.y - marked.y };
        log.push({
          browser: testInfo.project.name,
          zoom,
          dpr,
          marked: { x: marked.x, y: marked.y },
          canvas: { left: view.left, top: view.top, offsetX: view.offsetX, offsetY: view.offsetY },
          centre,
          delivered: { clientX: down.clientX, clientY: down.clientY },
          fractional: { x: !Number.isInteger(down.clientX), y: !Number.isInteger(down.clientY) },
          behaviour: kinds,
          sent,
          reference,
          error,
          bound: 1 / zoom,
          mouseEvent:
            mouse === null
              ? null
              : {
                  clientX: mouse.clientX,
                  clientY: mouse.clientY,
                  behaviour: { x: behaviour(mouse.clientX, centre.x), y: behaviour(mouse.clientY, centre.y) },
                  mapped: mouseMapped,
                  error: mouseError,
                },
        });
        expect(Object.is(sent.x, reference.x) && Object.is(sent.y, reference.y), `sent ${JSON.stringify(sent)} is the reference mapping ${JSON.stringify(reference)}`).toBe(true);
        for (const axis of ["x", "y"] as const) {
          const coordinate = axis === "x" ? down.clientX : down.clientY;
          expect(kinds[axis], `${axis}: delivered ${coordinate}, centre ${centre[axis]}`).not.toBe("other");
          if (!Number.isInteger(coordinate)) {
            expect(sent[axis], `${axis} with fractional delivery`).toBe(marked[axis]);
          } else {
            expect(Math.abs(error[axis]), `${axis} with integer delivery`).toBeLessThan(1 / zoom);
          }
        }
        if (mouseError !== null) {
          expect(Math.abs(mouseError.x), "x from the integer MouseEvent coordinates").toBeLessThan(1 / zoom);
          expect(Math.abs(mouseError.y), "y from the integer MouseEvent coordinates").toBeLessThan(1 / zoom);
        }
        await expect(pendingDots(page)).toHaveCount(1);
        await page.keyboard.press("Backspace");
        await expect(pendingDots(page)).toHaveCount(0);
        await expect(hud).toHaveText(fill(en.hud.count, { sarcomere: 1, number: 1, of: 6 }));
      }
      const summary = {
        dpr,
        browser: testInfo.project.name,
        cases: log,
        maxError: Math.max(...log.map((entry) => Math.max(Math.abs((entry.error as { x: number }).x), Math.abs((entry.error as { y: number }).y)))),
      };
      const file = testInfo.outputPath(`delivered-coordinates-dpr${dpr}.json`);
      fs.writeFileSync(file, `${JSON.stringify(summary, null, 2)}\n`);
      await testInfo.attach(`delivered-coordinates-dpr${dpr}.json`, { path: file, contentType: "application/json" });
    });
  });
}
