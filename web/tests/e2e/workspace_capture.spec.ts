import path from "node:path";

import type { Page } from "@playwright/test";

import { WEB_DIR } from "../../scripts/lane.mjs";
import { expect, test } from "./fixtures.ts";
import { boot, canvasView, clickPixel, pendingDots, pixelCentre, WORKSPACE, zoomTo } from "./workspace_view.ts";

const OUT = process.env.WORKSPACE_CAPTURE_DIR ?? "";
const MICROGRAPH = path.join(WEB_DIR, "..", "examples", "human_skeletal_muscle_tem_1.jpg");

async function settled(page: Page): Promise<void> {
  await page.evaluate(() => Promise.all(document.getAnimations().map((animation) => animation.finished)));
}

test.describe("workspace review captures", () => {
  test.skip(OUT === "", "set WORKSPACE_CAPTURE_DIR to write review screenshots of the image workspace");

  test("the example micrograph at fit, then at 400% with three landmarks, the scale bar and the loupe, at 1440 x 900", async ({ page, engine }) => {
    await boot(page);
    const chooser = page.waitForEvent("filechooser");
    await page.keyboard.press("o");
    await (await chooser).setFiles(MICROGRAPH);
    await expect(page.locator(WORKSPACE)).toHaveAttribute("data-state", "drawn");
    await engine.call("doc.set_scale", { pixel_size: 6.62, unit: "nm" });
    await page.mouse.move(0, 0);
    await settled(page);
    await page.screenshot({ path: path.join(OUT, "w5_shape_fit.png") });
    await zoomTo(page, 4);
    for (const [x, y] of [
      [1052, 862],
      [1098, 746],
      [1131, 880],
    ] as const) {
      const before = await pendingDots(page).count();
      await clickPixel(page, x, y);
      await expect(pendingDots(page)).toHaveCount(before + 1);
    }
    const view = await canvasView(page);
    const hover = pixelCentre(view, 1150, 770);
    await page.mouse.move(hover.x, hover.y);
    await expect(page.locator("[data-part='loupe']")).toBeVisible();
    await expect(page.locator("[data-part='scalebar']")).toBeVisible();
    await settled(page);
    await page.screenshot({ path: path.join(OUT, "w5_shape_400.png") });
  });
});
