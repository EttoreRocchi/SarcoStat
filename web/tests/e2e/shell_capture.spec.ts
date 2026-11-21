import path from "node:path";

import type { Page } from "@playwright/test";

import { WEB_DIR } from "../../scripts/lane.mjs";
import { expect, test } from "./fixtures.ts";

const OUT = process.env.SHELL_CAPTURE_DIR ?? "";
const READY = '.statusline[data-state="ready"]';
const IMAGE = path.join(WEB_DIR, "tests", "fixtures", "images", "grey_64x48.png");

async function settled(page: Page): Promise<void> {
  await page.evaluate(() => Promise.all(document.getAnimations().map((animation) => animation.finished)));
}

test.describe("shell review captures", () => {
  test.skip(OUT === "", "set SHELL_CAPTURE_DIR to write review screenshots of Home, Shape, the keys sheet and Study");

  test("Home, Shape with an image, the keys sheet and the Study placeholder at 1440 x 900", async ({ page }) => {
    await page.goto("./");
    await expect(page.locator(READY)).toBeVisible({ timeout: 30_000 });
    await page.mouse.move(0, 0);
    await settled(page);
    await page.screenshot({ path: path.join(OUT, "w3_home.png") });
    await page.keyboard.press("1");
    const chooser = page.waitForEvent("filechooser");
    await page.keyboard.press("o");
    await (await chooser).setFiles(IMAGE);
    await expect(page.locator("[data-part='hud']")).toBeVisible();
    await settled(page);
    await page.screenshot({ path: path.join(OUT, "w3_shape.png") });
    await page.keyboard.press("?");
    await expect(page.getByRole("dialog")).toBeVisible();
    await settled(page);
    await page.screenshot({ path: path.join(OUT, "w3_keys.png") });
    await page.keyboard.press("Escape");
    await page.keyboard.press("e");
    await expect(page.getByRole("dialog")).toBeVisible();
    await settled(page);
    await page.screenshot({ path: path.join(OUT, "w3_export.png") });
    await page.keyboard.press("Escape");
    await page.keyboard.press("0");
    await page.locator(".mode-card--study").click();
    await expect(page.locator(".study__card")).toBeVisible();
    await page.mouse.move(0, 0);
    await settled(page);
    await page.screenshot({ path: path.join(OUT, "w3_study.png") });
  });
});
