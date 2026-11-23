import { en } from "../../src/strings/en.ts";
import { fill } from "../../src/strings/format.ts";
import { attempt, expect, openApp, shown, type Reach, type ShotState } from "./shots_common.ts";
import { clickPixel, goldenText, ITEMS_PANEL, jsonPayload, openGoldenImage, openPayload, pendingDots, RESULTS_PANEL, selectTab, waitToastGone } from "./workspace_view.ts";

const TEM1 = "human_skeletal_muscle_tem_1.jpg";
const NEW_SARCOMERE: readonly (readonly [number, number])[] = [
  [1180, 1150],
  [1100, 1420],
];

const SETTLE_MS = 600;

async function rest(context: Reach): Promise<void> {
  await context.page.mouse.move(1000, 892);
}

async function frames(context: Reach): Promise<void> {
  await context.page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

async function settled(context: Reach): Promise<void> {
  const page = context.page;
  await page.waitForTimeout(SETTLE_MS);
  const size = page.viewportSize();
  if (size !== null) {
    await page.setViewportSize({ width: size.width, height: size.height + 1 });
    await frames(context);
    await page.setViewportSize(size);
  }
  await frames(context);
}

async function withGolden(context: Reach, golden: string, image: string | null = TEM1): Promise<string | null> {
  const missing = await openApp(context, "#/shape");
  if (missing !== null) {
    return missing;
  }
  const page = context.page;
  const opened = await attempt(`opening ${image ?? "no image"} and ${golden}.json with O`, async () => {
    if (image !== null) {
      await openGoldenImage(page, image);
    }
    await openPayload(page, jsonPayload(`${golden}.json`, goldenText(golden)));
    await expect(page.locator(`${ITEMS_PANEL} .items__row`).first()).toBeAttached({ timeout: 30_000 });
    await waitToastGone(page);
  });
  if (opened !== null) {
    return opened;
  }
  await rest(context);
  return null;
}

async function withResults(context: Reach): Promise<string | null> {
  const missing = await withGolden(context, "shape_click_set");
  if (missing !== null) {
    return missing;
  }
  if (!(await shown(context.page.locator(`${RESULTS_PANEL} [data-part='stability-ring']`), 20_000))) {
    return "the Results tab shows no stability ring after shape_click_set";
  }
  await settled(context);
  return null;
}

export const SHAPE_STATES: ShotState[] = [
  {
    name: "shape-hud-midflow",
    title: "Shape mode mid-flow: Click 3 of 6, M-line first end, chips A-F and the hint, with the loupe",
    story: "M4-1",
    reach: async (context) => {
      const missing = await withResults(context);
      if (missing !== null) {
        return missing;
      }
      const page = context.page;
      return attempt("two real clicks of a fifth sarcomere", async () => {
        for (const [x, y] of NEW_SARCOMERE) {
          const before = await pendingDots(page).count();
          await clickPixel(page, x, y);
          await expect(pendingDots(page)).toHaveCount(before + 1);
        }
        await expect(page.locator("[data-part='hud'] .hud__count")).toHaveText(fill(en.hud.count, { sarcomere: 5, number: 3, of: 6 }));
        await expect(page.locator("[data-part='hud-hint']")).toHaveText(en.hud.hint.C);
      });
    },
  },
  {
    name: "shape-results",
    title: "Results tab: SI, class with the stability ring, AsR, Cur, lengths, scale and click error",
    story: "M4-1",
    reach: async (context) => withResults(context),
  },
  {
    name: "shape-plots",
    title: "Plots tab: shape index of each sarcomere with ±u and ±2u over the class bands, and its caption",
    story: "M4-1",
    reach: async (context) => {
      const missing = await withResults(context);
      if (missing !== null) {
        return missing;
      }
      await selectTab(context.page, "plots");
      await rest(context);
      return (await shown(context.page.locator("[data-part='si-plot']"))) ? null : "the Plots tab shows no SI plot";
    },
  },
  {
    name: "shape-items",
    title: "Sarcomeres tab: the confirmed sarcomeres with class, SI and delete",
    story: "M4-1",
    reach: async (context) => {
      const missing = await withResults(context);
      if (missing !== null) {
        return missing;
      }
      await selectTab(context.page, "items");
      await rest(context);
      return null;
    },
  },
  {
    name: "shape-delete-confirm",
    title: "Delete confirmation of a sarcomere from the Sarcomeres tab",
    story: "M4-1",
    reach: async (context) => {
      const missing = await withResults(context);
      if (missing !== null) {
        return missing;
      }
      const page = context.page;
      await selectTab(page, "items");
      await page.locator(`${ITEMS_PANEL} .items__row[data-sarcomere='2'] [data-action='delete']`).click();
      return (await shown(page.getByRole("alertdialog", { name: fill(en.dialogs.remove.title, { n: 2 }) }))) ? null : "no delete confirmation";
    },
  },
  {
    name: "shape-click-error",
    title: "Click error dialog (Set… in the Results tab)",
    story: "M4-1",
    reach: async (context) => {
      const missing = await withResults(context);
      if (missing !== null) {
        return missing;
      }
      await context.page.locator(`${RESULTS_PANEL} [data-action='set-click-error']`).click();
      return (await shown(context.page.getByRole("dialog", { name: en.dialogs.clickError.title }))) ? null : "Set… opens no click error dialog";
    },
  },
  {
    name: "shape-scale",
    title: "Scale dialog from the scale in the top bar",
    story: "M4-1",
    reach: async (context) => {
      const missing = await withResults(context);
      if (missing !== null) {
        return missing;
      }
      await context.page.locator(".topbar [data-action='scale']").click();
      return (await shown(context.page.getByRole("dialog", { name: en.dialogs.scale.title }))) ? null : "the scale button opens no scale dialog";
    },
  },
  {
    name: "shape-export-results",
    title: "Export menu with sarcomeres: annotation file and results enabled, plots not in this build",
    story: "M4-1",
    reach: async (context) => {
      const missing = await withResults(context);
      if (missing !== null) {
        return missing;
      }
      await context.page.keyboard.press("e");
      return (await shown(context.page.locator(".topbar [role='menu']"))) ? null : "E opens no export menu";
    },
  },
  {
    name: "shape-v1-confirm",
    title: "Opening an annotation file made on another image asks first (SHA-256 differs)",
    story: "M4-1",
    reach: async (context) => {
      const missing = await openApp(context, "#/shape");
      if (missing !== null) {
        return missing;
      }
      const page = context.page;
      const other = JSON.parse(goldenText("shape_click_set")) as { images: { sha256: string }[] };
      (other.images[0] as { sha256: string }).sha256 = "0".repeat(64);
      const opened = await attempt("opening the image and a file with another SHA-256", async () => {
        await openGoldenImage(page, TEM1);
        await rest(context);
        await openPayload(page, jsonPayload("other_image.json", `${JSON.stringify(other, null, 2)}\n`));
      });
      if (opened !== null) {
        return opened;
      }
      return (await shown(page.getByRole("alertdialog", { name: en.dialogs.load.title }))) ? null : "no confirmation for a SHA-256 mismatch";
    },
  },
  {
    name: "shape-absent-modes",
    title: "A file with shape, chain and pair items in Shape: one line names Straightness and Register",
    story: "M4-1",
    reach: async (context) => {
      const missing = await withGolden(context, "mixed_types");
      if (missing !== null) {
        return missing;
      }
      return (await shown(context.page.locator(".panel [data-part='absent-modes']"))) ? null : "no absent-mode line in the panel";
    },
  },
  {
    name: "home-absent-modes",
    title: "A chain-only file opens Home with the absent-mode line",
    story: "M4-1",
    reach: async (context) => {
      const missing = await withGolden(context, "chain_only_notice", null);
      if (missing !== null) {
        return missing;
      }
      return (await shown(context.page.locator(".home [data-part='absent-modes']"))) ? null : "no absent-mode line on Home";
    },
  },
  {
    name: "shape-pixels-absent",
    title: "An annotation file opened without its image: the stage asks for the image",
    story: "M4-1",
    reach: async (context) => {
      const missing = await withGolden(context, "shape_click_set", null);
      if (missing !== null) {
        return missing;
      }
      return (await shown(context.page.locator("[data-part='empty'] .stage__title"))) ? null : "no pixels-absent stage";
    },
  },
  {
    name: "shape-backspace-notice",
    title: "Backspace with no pending click: the one-line notice, nothing deleted",
    story: "M4-1",
    reach: async (context) => {
      const missing = await withResults(context);
      if (missing !== null) {
        return missing;
      }
      await context.page.keyboard.press("Backspace");
      return (await shown(context.page.locator(".toast", { hasText: en.hud.noPendingClick }))) ? null : "Backspace shows no notice";
    },
  },
];
