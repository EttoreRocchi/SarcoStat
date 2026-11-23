import path from "node:path";

import type { Page } from "@playwright/test";

import { WEB_DIR } from "../../scripts/lane.mjs";
import { en } from "../../src/strings/en.ts";
import { fill } from "../../src/strings/format.ts";
import { expect, test } from "./fixtures.ts";

const READY = '.statusline[data-state="ready"]';
const BOOT_TIMEOUT_MS = 30_000;
const IMAGE = path.join(WEB_DIR, "tests", "fixtures", "images", "grey_64x48.png");
const ABSENT = ["straightness", "register", "planner"] as const;
const DESIGN_KEYS = ["0", "1", "2", "3", "4", "O", "E", "?", "+", "−", "F", "Space", "W", "L", "A", "Click", "Backspace", "Esc", "Enter", "N", "[", "]", "V", "M", "S", "P"];

async function boot(page: Page, hash = ""): Promise<void> {
  await page.goto(`./${hash}`);
  await expect(page.locator(READY)).toBeVisible({ timeout: BOOT_TIMEOUT_MS });
}

async function openWithKey(page: Page, file: string): Promise<void> {
  const chooser = page.waitForEvent("filechooser");
  await page.keyboard.press("o");
  await (await chooser).setFiles(file);
}

function hashOf(page: Page): string {
  return new URL(page.url()).hash;
}

test.describe("app shell", () => {
  test("Home shows the identity, the privacy line, four mode cards and the separate Study card; absent modes are disabled with one sentence", async ({ page }) => {
    await boot(page);
    expect(hashOf(page)).toBe("");
    const rail = page.getByRole("tablist", { name: en.rail.label });
    await expect(rail.getByRole("tab")).toHaveCount(5);
    await expect(rail.locator(".rail__key")).toHaveText(["0", "1", "2", "3", "4"]);
    await expect(page.getByRole("tab", { name: en.modes.home.title })).toHaveAttribute("aria-selected", "true");
    await expect(page.locator(".home__wordmark")).toHaveText("SarcoStat");
    await expect(page.locator(".home__tagline")).toHaveText(en.home.tagline);
    await expect(page.locator("[data-part='privacy']")).toHaveText(en.home.privacy);
    await expect(page.locator(".home__grid .mode-card")).toHaveCount(4);
    const shape = page.locator(".home__grid .mode-card[data-mode='shape']");
    await expect(shape).not.toHaveAttribute("aria-disabled", /.*/);
    await expect(shape.locator("kbd")).toHaveText("1");
    await expect(shape.locator(".mode-card__absent")).toHaveCount(0);
    for (const route of ABSENT) {
      const card = page.locator(`.home__grid .mode-card[data-mode='${route}']`);
      await expect(card, route).toHaveAttribute("aria-disabled", "true");
      await expect(card.locator(".mode-card__absent"), route).toHaveText(en.absent.card);
      await expect(page.getByRole("tab", { name: new RegExp(`^${en.modes[route].title}`) }), route).toHaveAttribute("aria-disabled", "true");
    }
    await page.locator(".home__grid .mode-card[data-mode='register']").press("Enter");
    await expect(page.locator(".toast")).toHaveText(fill(en.absent.notice, { mode: en.modes.register.title }));
    expect(hashOf(page)).toBe("");
    const study = page.locator(".mode-card--study");
    await expect(study).toHaveCount(1);
    await expect(page.locator(".home__grid .mode-card--study")).toHaveCount(0);
    await expect(study.locator("kbd")).toHaveCount(0);
    await expect(page.locator("#home-observer")).toHaveText(en.home.observer);
    await expect(page.locator(".statusline [data-item='local']")).toHaveText(en.status.local);
  });

  test("keys 0 to 4 switch modes, ? lists every key, O opens an image and E opens Export; buttons and chips show their keys", async ({ page }) => {
    await boot(page);
    await page.keyboard.press("1");
    await expect.poll(() => hashOf(page)).toBe("#/shape");
    await expect(page.getByRole("tab", { name: en.modes.shape.title })).toHaveAttribute("aria-selected", "true");
    await expect(page.locator(".topbar__title")).toHaveText(en.modes.shape.title);
    await expect(page.locator(".topbar__desc")).toHaveText(en.modes.shape.desc);
    await expect(page.locator(".topbar [data-action='open'] kbd")).toHaveText("O");
    await expect(page.locator(".topbar [data-action='export'] kbd")).toHaveText("E");
    await expect(page.locator(".rail [data-action='keys'] kbd")).toHaveText("?");
    for (const [key, route] of [["2", "straightness"], ["3", "register"], ["4", "planner"]] as const) {
      await page.keyboard.press(key);
      await expect(page.locator(".toast"), key).toHaveText(fill(en.absent.notice, { mode: en.modes[route].title }));
      expect(hashOf(page), key).toBe("#/shape");
    }
    await page.keyboard.press("0");
    await expect.poll(() => hashOf(page)).toBe("#/");
    await expect(page.getByRole("tab", { name: en.modes.home.title })).toHaveAttribute("aria-selected", "true");
    await page.keyboard.press("1");
    await expect.poll(() => hashOf(page)).toBe("#/shape");

    await page.keyboard.press("?");
    const sheet = page.getByRole("dialog", { name: en.sheet.title });
    await expect(sheet).toBeVisible();
    await expect(sheet).toHaveAttribute("aria-modal", "true");
    const labels = await sheet.locator(".keys-grid kbd").allTextContents();
    for (const key of DESIGN_KEYS) {
      expect(labels, key).toContain(key);
    }
    await expect(sheet.locator(".keys-grid__group h3")).toHaveText(Object.values(en.sheet.groups));
    await page.keyboard.press("0");
    expect(hashOf(page)).toBe("#/shape");
    await page.keyboard.press("Escape");
    await expect(sheet).toBeHidden();

    await expect(page.locator("[data-part='empty']")).toBeVisible();
    await openWithKey(page, IMAGE);
    await expect(page.locator(".topbar__name")).toHaveText("grey_64x48.png");
    await expect(page.locator(".topbar__file-meta")).toHaveText(fill(en.topbar.size, { width: 64, height: 48 }));
    await expect(page.locator(".topbar__scale")).toHaveText(en.topbar.scaleUnset);
    await expect(page.locator("[data-part='empty']")).toBeHidden();
    const hud = page.locator("[data-part='hud']");
    await expect(hud).toBeVisible();
    await expect(hud.locator(".hud__count")).toHaveText(fill(en.hud.count, { sarcomere: 1, number: 1, of: 6 }));
    await expect(hud.locator(".hud__what")).toHaveText(en.landmarks.A);
    await expect(hud.locator(".lm-chip")).toHaveText(["A", "B", "C", "D", "E", "F"]);
    await expect(hud.locator(".lm-chip").first()).toHaveAttribute("data-state", "now");
    await expect(hud.locator(".lm-chip--todo")).toHaveCount(5);
    await expect(hud.locator("[data-part='hud-keys'] kbd")).toHaveText([en.keys.backspace, en.keys.esc, en.keys.enter]);
    await expect(page.locator("[data-part='unsaved']")).toBeHidden();

    await page.keyboard.press("e");
    const menu = page.getByRole("dialog", { name: en.exportMenu.label });
    await expect(menu).toBeVisible();
    await expect(menu).toHaveAttribute("aria-modal", "true");
    await expect(page.locator(".topbar [data-action='export']")).toHaveAttribute("aria-expanded", "true");
    const items = menu.getByRole("menuitem");
    await expect(items).toHaveCount(5);
    await expect(items.first()).toBeFocused();
    await expect(items.first().locator("kbd")).toHaveText("E");
    for (const index of [0, 1, 2]) {
      await expect(items.nth(index)).toHaveAttribute("aria-disabled", "true");
      await expect(items.nth(index).locator(".menu__reason")).toHaveText(en.exportMenu.needItems);
    }
    for (const index of [3, 4]) {
      await expect(items.nth(index)).toHaveAttribute("aria-disabled", "true");
      await expect(items.nth(index).locator(".menu__reason")).toHaveText(en.exportMenu.plotsAbsent);
    }
    await page.keyboard.press("ArrowDown");
    await expect(items.nth(1)).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(menu).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(menu).toBeHidden();
    await expect(page.locator(".topbar [data-action='export']")).toHaveAttribute("aria-expanded", "false");
    await page.locator(".topbar [data-action='export']").click();
    await expect(menu).toBeVisible();
    await page.locator(".topbar__title").click();
    await expect(menu).toBeHidden();
  });

  test("keys never fire while focus is in a text field or while a dialog holds them", async ({ page }) => {
    await boot(page);
    await page.evaluate(() => {
      const input = document.createElement("input");
      input.id = "typing-probe";
      document.body.append(input);
    });
    await page.locator("#typing-probe").focus();
    await page.keyboard.type("1o?e40");
    await expect(page.locator("#typing-probe")).toHaveValue("1o?e40");
    expect(hashOf(page)).toBe("");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.locator("#typing-probe").blur();
    await page.keyboard.press("?");
    const sheet = page.getByRole("dialog", { name: en.sheet.title });
    await expect(sheet).toBeVisible();
    await expect(page.locator(".shell")).toHaveJSProperty("inert", true);
    for (const key of ["1", "o", "e"]) {
      await page.keyboard.press(key);
    }
    expect(hashOf(page)).toBe("");
    for (let i = 0; i < 4; i += 1) {
      await page.keyboard.press("Tab");
      expect(await page.evaluate(() => document.activeElement?.closest("[role='dialog']") !== null)).toBe(true);
    }
    await page.keyboard.press("?");
    await expect(sheet).toBeHidden();
    await expect(page.locator(".shell")).toHaveJSProperty("inert", false);
  });

  test("Study is entered only from Home's card and has no rail; a direct #/study load shows the card first", async ({ page }) => {
    await boot(page, "#/study");
    await expect.poll(() => hashOf(page)).toBe("#/");
    const card = page.locator(".mode-card--study");
    await expect(card).toBeFocused();
    await expect(card).toHaveAttribute("data-arrival", "study");
    await expect(page.locator(".rail__list [data-mode='study']")).toHaveCount(0);
    await expect(page.locator(".rail [data-mode='study'] kbd")).toHaveCount(0);

    await page.keyboard.press("1");
    await expect.poll(() => hashOf(page)).toBe("#/shape");
    await page.locator(".rail__foot [data-mode='study']").click();
    await expect.poll(() => hashOf(page)).toBe("#/");
    await expect(card).toBeFocused();

    await card.click();
    await expect.poll(() => hashOf(page)).toBe("#/study");
    await expect(page.locator(".rail")).toHaveCount(0);
    await expect(page.locator(".shell")).toHaveClass(/shell--study/);
    await expect(page.locator(".study__name")).toHaveText(en.modes.study.title);
    const open = page.getByRole("button", { name: en.study.open });
    await expect(open).toHaveAttribute("aria-disabled", "true");
    await expect(open).toHaveAccessibleDescription(en.study.openDisabled);
    await expect(page.locator(".study__dock .statusline[data-state='ready'] [data-item='version']")).toBeVisible();
    await page.keyboard.press("1");
    await page.keyboard.press("o");
    expect(hashOf(page)).toBe("#/study");

    await page.getByRole("button", { name: en.study.back }).click();
    await expect.poll(() => hashOf(page)).toBe("#/");
    await expect(page.locator(".rail")).toHaveCount(1);
    await page.goBack();
    await expect.poll(() => hashOf(page)).toBe("#/");
    await expect(page.locator(".rail")).toHaveCount(1);
    await expect(card).toBeFocused();
  });

  test("direct loads of an absent mode or an unknown route land on Home; an absent mode is named in one line", async ({ page }) => {
    await boot(page, "#/register");
    await expect.poll(() => hashOf(page)).toBe("#/");
    await expect(page.locator(".toast")).toHaveText(fill(en.absent.notice, { mode: en.modes.register.title }));
    await expect(page.getByRole("tab", { name: en.modes.home.title })).toHaveAttribute("aria-selected", "true");
    await page.goto("./#/nowhere");
    await expect.poll(() => hashOf(page)).toBe("#/");
    await expect(page.getByRole("tab", { name: en.modes.home.title })).toHaveAttribute("aria-selected", "true");
  });

  test("accessibility: mode tabs with aria-selected, modal dialogs, live step guidance", async ({ page }) => {
    await boot(page);
    const rail = page.getByRole("tablist", { name: en.rail.label });
    await expect(rail).toHaveAttribute("aria-orientation", "vertical");
    await expect(rail.locator("[role='tab'][aria-selected='true']")).toHaveCount(1);
    await expect(rail.locator("[role='tab'][aria-selected='false']")).toHaveCount(4);
    await expect(page.getByRole("tabpanel", { name: en.modes.home.title })).toBeVisible();
    await expect(page.locator(".fatal")).toHaveAttribute("role", "alertdialog");
    await expect(page.locator(".fatal")).toHaveAttribute("aria-modal", "true");

    await page.getByRole("tab", { name: en.modes.home.title }).focus();
    await page.keyboard.press("ArrowDown");
    await expect(page.getByRole("tab", { name: en.modes.shape.title })).toBeFocused();
    await page.keyboard.press("Enter");
    await expect.poll(() => hashOf(page)).toBe("#/shape");
    await expect(page.getByRole("tab", { name: en.modes.shape.title })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("tabpanel", { name: en.modes.shape.title })).toBeVisible();

    const results = page.getByRole("tablist", { name: en.panel.tabsLabel });
    await expect(results.getByRole("tab")).toHaveCount(3);
    await expect(results.getByRole("tab", { name: en.panel.results })).toHaveAttribute("aria-selected", "true");
    await results.getByRole("tab", { name: en.panel.results }).focus();
    await page.keyboard.press("ArrowRight");
    await expect(results.getByRole("tab", { name: en.panel.plots })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("tabpanel", { name: en.panel.plots })).toBeVisible();

    await openWithKey(page, IMAGE);
    const hud = page.getByRole("status", { name: en.hud.label });
    await expect(hud).toBeVisible();
    await expect(hud).toHaveAttribute("aria-live", "polite");
    await expect(hud.locator("[aria-current='step']")).toHaveText("A");

    await page.keyboard.press("?");
    await expect(page.getByRole("dialog", { name: en.sheet.title })).toHaveAttribute("aria-modal", "true");
    await page.keyboard.press("Escape");
    await page.keyboard.press("e");
    const menu = page.getByRole("dialog", { name: en.exportMenu.label });
    await expect(menu).toHaveAttribute("aria-modal", "true");
    await expect(menu.getByRole("menu")).toHaveCount(1);
    await page.keyboard.press("Escape");
  });

  test("with prefers-reduced-motion every computed transition and animation duration is 0", async ({ page }) => {
    const sample = (): Promise<string[]> =>
      page.evaluate(() =>
        [...document.querySelectorAll(".rail__item, .btn, .mode-card, .statusline__hash, .tabs__tab, .lm-chip, .dialog, .scrim, .menu, .toast")].flatMap((element) => {
          const style = getComputedStyle(element);
          return [...style.transitionDuration.split(","), ...style.transitionDelay.split(","), ...style.animationDuration.split(",")].map((value) => value.trim());
        }),
      );
    await page.emulateMedia({ reducedMotion: "reduce" });
    await boot(page);
    await page.keyboard.press("?");
    const reduced = await sample();
    expect(reduced.length).toBeGreaterThan(20);
    expect(new Set(reduced)).toEqual(new Set(["0s"]));
    await page.keyboard.press("Escape");
    await page.emulateMedia({ reducedMotion: "no-preference" });
    const normal = await sample();
    expect(normal.some((value) => value !== "0s")).toBe(true);
  });
});
