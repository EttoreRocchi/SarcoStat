import fs from "node:fs";
import path from "node:path";

import type { Page } from "@playwright/test";

import { WEB_DIR } from "../../scripts/lane.mjs";
import { en } from "../../src/strings/en.ts";
import { fill } from "../../src/strings/format.ts";
import { expect, test } from "./fixtures.ts";
import { boot, clickPixel, pendingDots, WORKSPACE } from "./workspace_view.ts";

const IMAGE_A = path.join(WEB_DIR, "tests", "fixtures", "images", "grey_64x48.png");
const IMAGE_B = path.join(WEB_DIR, "tests", "fixtures", "images", "grey_32x40.png");
const SARCOMERE: readonly [number, number][] = [
  [10, 5],
  [10, 40],
  [30, 7],
  [30, 38],
  [50, 5],
  [50, 40],
];

async function pickWithKey(page: Page, file: string): Promise<void> {
  const chooser = page.waitForEvent("filechooser");
  await page.keyboard.press("o");
  await (await chooser).setFiles(file);
}

async function mouseClicks(page: Page, points: readonly (readonly [number, number])[]): Promise<void> {
  await expect(page.locator(WORKSPACE)).toHaveAttribute("data-state", "drawn");
  for (const [x, y] of points) {
    const before = await pendingDots(page).count();
    await clickPixel(page, x, y);
    await expect(pendingDots(page), `click at pixel ${x} ${y}`).toHaveCount(before + 1);
  }
}

function beforeUnloadArmed(page: Page): Promise<boolean> {
  return page.evaluate(() => {
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    return event.defaultPrevented;
  });
}

function hashOf(page: Page): string {
  return new URL(page.url()).hash;
}

test.describe("unsaved-work guard", () => {
  test("a mode switch with an unfinished sarcomere asks first: Esc stays, Enter keeps the clicks for the return, D discards them", async ({ page }) => {
    await boot(page);
    await pickWithKey(page, IMAGE_A);
    const hud = page.locator("[data-part='hud']");
    await expect(hud.locator(".hud__count")).toHaveText(fill(en.hud.count, { sarcomere: 1, number: 1, of: 6 }));
    await mouseClicks(page, SARCOMERE.slice(0, 3));
    await page.keyboard.press("Backspace");
    await expect(hud.locator(".hud__count")).toHaveText(fill(en.hud.count, { sarcomere: 1, number: 3, of: 6 }));
    await expect(hud.locator(".lm-chip--done")).toHaveText(["A", "B"]);
    await expect(page.locator(".statusline")).toContainText(fill(en.status.sarcomeresPending, { n: 0 }));

    await page.keyboard.press("0");
    const guard = page.getByRole("alertdialog");
    await expect(guard).toBeVisible();
    await expect(guard).toHaveAttribute("aria-modal", "true");
    await expect(guard).toHaveAccessibleName(fill(en.guard.shape.title, { n: 1 }));
    await expect(guard.locator(".dialog__text")).toHaveText(fill(en.guard.shape.text, { mode: en.modes.home.title, n: 1, placed: 2, of: 6 }));
    await expect(guard.locator(".dialog__text b")).toHaveText(en.modes.home.title);
    await expect(guard.locator(".lm-chip")).toHaveText(["A", "B", "C", "D", "E", "F"]);
    await expect(guard.locator(".lm-chip--done")).toHaveText(["A", "B"]);
    await expect(guard.locator(".lm-chip--todo")).toHaveText(["C", "D", "E", "F"]);
    await expect(guard.locator(".dialog__state")).toContainText(fill(en.guard.state, { placed: 2, of: 6 }));
    await expect(guard.locator(".dialog__foot")).toBeHidden();
    const keep = guard.locator("[data-choice='keep']");
    const discard = guard.locator("[data-choice='discard']");
    const stay = guard.locator("[data-choice='stay']");
    await expect(keep).toHaveText(`${en.guard.keep}${en.keys.enter}`);
    await expect(discard).toHaveText(`${fill(en.guard.shape.discard, { n: 1 })}D`);
    await expect(stay).toHaveText(`${en.guard.stay}${en.keys.esc}`);
    await expect(keep).toBeFocused();
    await expect(page.locator(".shell")).toHaveJSProperty("inert", true);
    for (const next of [discard, stay, keep]) {
      await page.keyboard.press("Tab");
      await expect(next).toBeFocused();
    }
    await page.keyboard.press("1");
    await expect(guard).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(guard).toBeHidden();
    expect(hashOf(page)).toBe("#/shape");
    await expect(page.locator(".shell")).toHaveJSProperty("inert", false);
    await expect(hud.locator(".hud__count")).toHaveText(fill(en.hud.count, { sarcomere: 1, number: 3, of: 6 }));

    await page.keyboard.press("0");
    await expect(guard).toBeVisible();
    await page.keyboard.press("Enter");
    await expect(guard).toBeHidden();
    await expect.poll(() => hashOf(page)).toBe("#/");
    await expect(page.getByRole("tab", { name: en.modes.home.title })).toHaveAttribute("aria-selected", "true");
    await page.keyboard.press("1");
    await expect.poll(() => hashOf(page)).toBe("#/shape");
    await expect(hud.locator(".hud__count")).toHaveText(fill(en.hud.count, { sarcomere: 1, number: 3, of: 6 }));
    await expect(hud.locator(".lm-chip--done")).toHaveText(["A", "B"]);

    await page.goBack();
    await expect(guard).toBeVisible();
    expect(hashOf(page)).toBe("#/shape");
    await stay.click();
    await expect(guard).toBeHidden();
    expect(hashOf(page)).toBe("#/shape");

    await page.getByRole("tab", { name: en.modes.home.title }).click();
    await expect(guard).toBeVisible();
    await page.keyboard.press("d");
    await expect(guard).toBeHidden();
    await expect.poll(() => hashOf(page)).toBe("#/");
    await page.keyboard.press("1");
    await expect(hud.locator(".hud__count")).toHaveText(fill(en.hud.count, { sarcomere: 1, number: 1, of: 6 }));
    await expect(hud.locator(".lm-chip--done")).toHaveCount(0);
    await page.keyboard.press("0");
    await expect.poll(() => hashOf(page)).toBe("#/");
    await expect(guard).toBeHidden();
  });

  test("work not exported: amber indicator, beforeunload only while not exported, the second question before another image, and export", async ({ page }) => {
    await boot(page);
    await pickWithKey(page, IMAGE_A);
    await expect(page.locator(".topbar__name")).toHaveText("grey_64x48.png");
    expect(await beforeUnloadArmed(page)).toBe(false);

    await mouseClicks(page, SARCOMERE);
    await page.keyboard.press("Enter");
    const unsaved = page.locator("[data-part='unsaved']");
    await expect(unsaved).toHaveText(fill(en.topbar.notExported.shapeOne, { n: 1 }));
    await expect(unsaved).not.toHaveClass(/unsaved--clear/);
    await expect(unsaved.locator(".unsaved__dot")).toBeVisible();
    await expect(page.locator("#result-tab-items .count")).toHaveText("1");
    expect(await beforeUnloadArmed(page)).toBe(true);

    await pickWithKey(page, IMAGE_B);
    const leave = page.getByRole("alertdialog");
    await expect(leave).toBeVisible();
    await expect(leave).toHaveAttribute("aria-modal", "true");
    await expect(leave).toHaveAccessibleName(fill(en.leave.title, { next: "grey_32x40.png" }));
    await expect(leave.locator(".dialog__text")).toHaveText(
      `${fill(en.leave.notExportedOne, { n: 1, current: "grey_64x48.png" })} ${fill(en.leave.consequence, { next: "grey_32x40.png" })}`,
    );
    await expect(leave.locator("[data-choice='export']")).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(leave).toBeHidden();
    await expect(page.locator(".topbar__name")).toHaveText("grey_64x48.png");
    expect(await beforeUnloadArmed(page)).toBe(true);

    await pickWithKey(page, IMAGE_B);
    await expect(leave).toBeVisible();
    const download = page.waitForEvent("download");
    await page.keyboard.press("Enter");
    const file = await download;
    const saved = fs.readFileSync(await file.path(), "utf8");
    expect(file.suggestedFilename()).toMatch(/\.json$/);
    expect(saved).toContain("grey_64x48.png");
    expect(JSON.parse(saved)).toBeTruthy();
    await expect(page.locator(".topbar__name")).toHaveText("grey_32x40.png");
    await expect(page.locator(".topbar__file-meta")).toHaveText(fill(en.topbar.size, { width: 32, height: 40 }));
    await expect(unsaved).toBeHidden();
    expect(await beforeUnloadArmed(page)).toBe(false);

    await mouseClicks(page, SARCOMERE.map(([x, y]) => [x / 2, 10 + Math.floor(y / 2)] as const));
    await page.keyboard.press("Enter");
    await expect(unsaved).toHaveText(fill(en.topbar.notExported.shapeOne, { n: 1 }));
    expect(await beforeUnloadArmed(page)).toBe(true);
    await page.keyboard.press("e");
    const menuDownload = page.waitForEvent("download");
    await page.keyboard.press("e");
    expect((await menuDownload).suggestedFilename()).toMatch(/\.json$/);
    await expect(unsaved).toHaveText(en.topbar.allExported);
    await expect(unsaved).toHaveClass(/unsaved--clear/);
    expect(await beforeUnloadArmed(page)).toBe(false);
    await expect(page.locator(".toast")).toHaveText(fill(en.exportMenu.saved, { file: (await menuDownload).suggestedFilename() }));

    await mouseClicks(page, SARCOMERE.map(([x, y]) => [x / 2 + 1, 9 + Math.floor(y / 2)] as const));
    await page.keyboard.press("Enter");
    await expect(unsaved).toHaveText(fill(en.topbar.notExported.shapeOther, { n: 2 }));
    const prompt = new Promise<string>((resolve) => {
      page.once("dialog", (dialog) => {
        resolve(dialog.type());
        void dialog.dismiss();
      });
    });
    await page.close({ runBeforeUnload: true });
    expect(await prompt).toBe("beforeunload");
    await expect(page.locator(".topbar__name")).toHaveText("grey_32x40.png");
  });
});
