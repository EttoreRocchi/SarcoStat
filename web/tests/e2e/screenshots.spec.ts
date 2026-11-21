import fs from "node:fs";
import path from "node:path";

import type { Locator, Page } from "@playwright/test";

import { WEB_DIR } from "../../scripts/lane.mjs";
import { BRIDGE_VERSION } from "../../src/worker/protocol.ts";
import { comparePngBuffers } from "../screenshots/png.mjs";
import { shotsSettings } from "../screenshots/shots.mjs";
import { expect, test, type Engine, type Recorder } from "./fixtures.ts";
import { clickPixel, openImage, pendingDots, READY } from "./workspace_view.ts";

const SETTINGS = shotsSettings();
const VIEWPORT = { width: 1440, height: 900 };
const MAX_DIFF_PIXELS = 0;
const MASK_COLOR = "#FF00FF";
const BUILD_IDENTITY = ['.statusline [data-item="version"]', ".statusline .statusline__hash"];
const REACH_TIMEOUT_MS = 5_000;
const BOOT_TIMEOUT_MS = 30_000;
const IMAGE = "grey_64x48.png";
const GUARD_CLICKS: readonly (readonly [number, number])[] = [
  [10, 5],
  [10, 40],
];

interface Reach {
  page: Page;
  engine: Engine;
  recorder: Recorder;
}

interface ShotState {
  name: string;
  title: string;
  story: string;
  reach(context: Reach): Promise<string | null>;
}

async function shown(locator: Locator, timeout = REACH_TIMEOUT_MS): Promise<boolean> {
  try {
    await locator.first().waitFor({ state: "visible", timeout });
    return true;
  } catch {
    return false;
  }
}

async function attempt(what: string, step: () => Promise<unknown>): Promise<string | null> {
  try {
    await step();
    return null;
  } catch (error) {
    return `${what}: ${(error instanceof Error ? error.message : String(error)).split("\n")[0]}`;
  }
}

async function openApp(context: Reach, hash: string): Promise<string | null> {
  await context.page.goto(`./${hash}`);
  await context.engine.ready();
  return (await shown(context.page.locator(READY), BOOT_TIMEOUT_MS)) ? null : "the status line never reached data-state=ready";
}

async function inShape(context: Reach, withImage: boolean): Promise<string | null> {
  const missing = await openApp(context, "#/shape");
  if (missing !== null) {
    return missing;
  }
  if (!(await shown(context.page.locator('.shell[data-mode="shape"] [data-part="empty"]')))) {
    return "#/shape shows no shape-mode shell with its empty stage ([data-part=empty])";
  }
  return withImage ? attempt(`opening ${IMAGE} with O and the file chooser`, () => openImage(context.page, IMAGE)) : null;
}

const STATES: ShotState[] = [
  {
    name: "home",
    title: "Home",
    story: "M3-4",
    reach: async (context) => {
      const missing = await openApp(context, "#/");
      if (missing !== null) {
        return missing;
      }
      const page = context.page;
      if (!(await shown(page.locator('.rail [role="tab"][data-mode="home"][aria-selected="true"]')))) {
        return "the Home tab of the rail is not selected after opening #/";
      }
      if (!(await shown(page.locator('.shell[data-mode="home"] .home__grid .mode-card')))) {
        return "Home shows no mode cards (.home__grid .mode-card)";
      }
      return (await shown(page.locator('.shell[data-mode="home"] .home .mode-card--study'))) ? null : "Home shows no separate Study card (.mode-card--study)";
    },
  },
  {
    name: "shape-empty",
    title: "Shape mode without an image",
    story: "M3-4",
    reach: async (context) => inShape(context, false),
  },
  {
    name: "shape-image",
    title: "Shape mode with an image opened",
    story: "M3-5",
    reach: async (context) => inShape(context, true),
  },
  {
    name: "keys-sheet",
    title: "Keyboard shortcut sheet over Shape mode with an image",
    story: "M3-4",
    reach: async (context) => {
      const missing = await inShape(context, true);
      if (missing !== null) {
        return missing;
      }
      await context.page.keyboard.press("?");
      return (await shown(context.page.locator('[role="dialog"][aria-modal="true"] .keys-grid'))) ? null : "? opens no modal shortcut sheet (.keys-grid)";
    },
  },
  {
    name: "export-menu",
    title: "Export menu in Shape mode with an image",
    story: "M3-4",
    reach: async (context) => {
      const missing = await inShape(context, true);
      if (missing !== null) {
        return missing;
      }
      await context.page.keyboard.press("e");
      return (await shown(context.page.locator('.topbar [role="menu"]'))) ? null : "E opens no export menu (role=menu) in the top bar";
    },
  },
  {
    name: "mode-guard",
    title: "Unsaved-work guard after two clicks of a sarcomere, switching to Home",
    story: "M3-4, M3-5",
    reach: async (context) => {
      const missing = await inShape(context, true);
      if (missing !== null) {
        return missing;
      }
      const page = context.page;
      const clicked = await attempt("real mouse clicks on the image canvas", async () => {
        for (const [x, y] of GUARD_CLICKS) {
          const before = await pendingDots(page).count();
          await clickPixel(page, x, y);
          await expect(pendingDots(page), `click at pixel ${x} ${y}`).toHaveCount(before + 1);
        }
      });
      if (clicked !== null) {
        return clicked;
      }
      await page.keyboard.press("0");
      return attempt("the guard dialog with Keep focused", () => expect(page.getByRole("alertdialog").locator("[data-choice='keep']")).toBeFocused());
    },
  },
  {
    name: "fatal",
    title: "Fatal screen (bridge version mismatch)",
    story: "M3-2",
    reach: async (context) => {
      await context.recorder.configure({ alterIdentity: { bridge_version: BRIDGE_VERSION + 1 } });
      await context.page.goto("./");
      const dialog = context.page.locator('[role="alertdialog"][data-code="bridge_mismatch"]');
      if (!(await shown(dialog, BOOT_TIMEOUT_MS))) {
        return "the bridge-mismatch fatal screen did not appear";
      }
      return attempt("Reload focused on the fatal screen", () => expect(dialog.locator(".fatal__action")).toBeFocused());
    },
  },
  {
    name: "study-placeholder",
    title: "Study placeholder, entered from Home's Study card",
    story: "M3-4",
    reach: async (context) => {
      const missing = await openApp(context, "#/");
      if (missing !== null) {
        return missing;
      }
      const page = context.page;
      const card = page.locator(".home .mode-card--study");
      if (!(await shown(card))) {
        return "Home shows no Study card";
      }
      await card.focus();
      await page.keyboard.press("Enter");
      if (!(await shown(page.locator(".shell.shell--study .study__name")))) {
        return "Enter on the Study card does not open the study shell (.shell--study)";
      }
      return new URL(page.url()).hash === "#/study" ? null : `the Study card leads to ${new URL(page.url()).hash}, not #/study`;
    },
  },
];

async function settle(page: Page): Promise<{ fonts: string[]; reducedMotion: boolean; dpr: number; width: number; height: number }> {
  return page.evaluate(async () => {
    await document.fonts.ready;
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const fonts = [...document.fonts].filter((face) => face.status === "loaded").map((face) => `${face.family} ${face.weight}`);
    return {
      fonts: [...new Set(fonts)].sort(),
      reducedMotion: matchMedia("(prefers-reduced-motion: reduce)").matches,
      dpr: devicePixelRatio,
      width: innerWidth,
      height: innerHeight,
    };
  });
}

test.use({ contextOptions: { reducedMotion: "reduce" }, locale: "en-US", timezoneId: "UTC", viewport: VIEWPORT, deviceScaleFactor: 1 });

test.describe(`screenshots (set ${SETTINGS.set})`, () => {
  for (const state of STATES) {
    test(`${state.name}: ${state.title}`, async ({ page, engine, recorder }) => {
      test.setTimeout(90_000);
      const missing = await state.reach({ page, engine, recorder });
      if (missing !== null) {
        if (SETTINGS.require) {
          throw new Error(`${state.name} (${state.story}) is not reachable: ${missing}`);
        }
        test.skip(true, `${state.story}: ${missing}`);
        return;
      }
      const view = await settle(page);
      expect(view).toMatchObject({ reducedMotion: true, dpr: 1, width: VIEWPORT.width, height: VIEWPORT.height });
      const png = await page.screenshot({
        animations: "disabled",
        caret: "hide",
        scale: "css",
        mask: BUILD_IDENTITY.map((selector) => page.locator(selector)),
        maskColor: MASK_COLOR,
      });
      const file = `${state.name}.png`;
      const info = test.info();
      info.annotations.push({ type: "fonts", description: view.fonts.join(", ") || "none loaded" });
      fs.writeFileSync(info.outputPath(file), png);
      await info.attach(file, { body: png, contentType: "image/png" });
      if (SETTINGS.candidate !== null) {
        fs.mkdirSync(SETTINGS.candidate, { recursive: true });
        fs.writeFileSync(path.join(SETTINGS.candidate, file), png);
        info.annotations.push({ type: "candidate", description: path.join(SETTINGS.candidate, file) });
        return;
      }
      const reference = path.join(SETTINGS.setDir, file);
      const shownReference = path.relative(WEB_DIR, reference);
      if (!SETTINGS.compare) {
        info.annotations.push({ type: "not compared", description: `CI is set: the capture is kept, ${shownReference} is not compared` });
        return;
      }
      if (!fs.existsSync(reference)) {
        info.annotations.push({ type: "no reference", description: `${shownReference} does not exist: the capture is kept for review` });
        return;
      }
      const result = comparePngBuffers(fs.readFileSync(reference), png);
      if (result.diffPng !== null) {
        fs.writeFileSync(info.outputPath(`${state.name}.diff.png`), result.diffPng);
        await info.attach(`${state.name}.diff.png`, { body: result.diffPng, contentType: "image/png" });
      }
      expect(result.sameSize, `${shownReference} is ${result.referenceWidth} x ${result.referenceHeight}, the capture ${result.width} x ${result.height}`).toBe(true);
      expect(result.differing, `pixels differing from ${shownReference} (maxDiffPixels ${MAX_DIFF_PIXELS})`).toBeLessThanOrEqual(MAX_DIFF_PIXELS);
    });
  }
});
