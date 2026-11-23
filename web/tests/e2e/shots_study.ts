import path from "node:path";

import { attempt, BOOT_TIMEOUT_MS, openApp, shown, type Reach, type ShotState } from "./shots_common.ts";
import {
  checkBrowser,
  chooseKit,
  clickDisplay,
  completeItem,
  confirmIdentity,
  DEMO_KIT,
  displaySize,
  KITS_DIR,
  openKit,
  pressAction,
  readManifest,
  shapeClicks,
  chainClicks,
  type ManifestItem,
} from "./study_view.ts";

const ITEMS = readManifest(DEMO_KIT).items;
const STORY = "M4-2";

function item(index: number): ManifestItem {
  return ITEMS[index] as ManifestItem;
}

async function inStudy(context: Reach): Promise<string | null> {
  const missing = await openApp(context, "#/");
  if (missing !== null) {
    return missing;
  }
  const page = context.page;
  await page.locator(".home .mode-card--study").click();
  return (await shown(page.locator("[data-part='study-start']"))) ? null : "the Study card opens no study start screen ([data-part=study-start])";
}

async function checked(context: Reach): Promise<string | null> {
  const missing = await inStudy(context);
  return missing ?? attempt("the readiness check with a fresh study engine", () => checkBrowser(context.page));
}

async function atIdentity(context: Reach): Promise<string | null> {
  const missing = await checked(context);
  return missing ?? attempt("opening the demo kit through the folder picker", () => openKit(context.page, DEMO_KIT));
}

async function inSession(context: Reach, done: number): Promise<string | null> {
  const missing = await atIdentity(context);
  if (missing !== null) {
    return missing;
  }
  return attempt(`confirming the identity and completing ${done} item(s)`, async () => {
    await confirmIdentity(context.page);
    for (const entry of ITEMS.slice(0, done)) {
      await completeItem(context.page, entry);
    }
  });
}

async function settle(context: Reach): Promise<void> {
  const page = context.page;
  await page.mouse.move(4, 4);
  await page.waitForTimeout(100);
}

export const STUDY_STATES: ShotState[] = [
  {
    name: "study-readiness",
    title: "Study start screen after the readiness check",
    story: STORY,
    reach: async (context) => checked(context),
  },
  {
    name: "study-kit-refused",
    title: "Study kit refused: an item replaced by an RGB PNG",
    story: STORY,
    reach: async (context) => {
      const missing = await checked(context);
      if (missing !== null) {
        return missing;
      }
      await chooseKit(context.page, path.join(KITS_DIR, "refused", "rgb"));
      return (await shown(context.page.locator("[data-part='refusals']"), BOOT_TIMEOUT_MS)) ? null : "the refused kit shows no refusal lines";
    },
  },
  {
    name: "study-identity",
    title: "Study identity confirmation",
    story: STORY,
    reach: async (context) => atIdentity(context),
  },
  {
    name: "study-shape-item",
    title: "Study shape item after two clicks",
    story: STORY,
    reach: async (context) => {
      const missing = await inSession(context, 0);
      return (
        missing ??
        attempt("two clicks on the shape item", async () => {
          for (const [x, y] of shapeClicks(item(0)).slice(0, 2)) {
            await clickDisplay(context.page, x, y);
          }
          await settle(context);
        })
      );
    },
  },
  {
    name: "study-chain-item",
    title: "Study chain item after three clicks",
    story: STORY,
    reach: async (context) => {
      const missing = await inSession(context, 1);
      return (
        missing ??
        attempt("three clicks on the chain item", async () => {
          for (const [x, y] of chainClicks(item(1), 2).slice(0, 3)) {
            await clickDisplay(context.page, x, y);
          }
          await settle(context);
        })
      );
    },
  },
  {
    name: "study-pair-item",
    title: "Study pair item: first myofibril done, second started",
    story: STORY,
    reach: async (context) => {
      const missing = await inSession(context, 2);
      return (
        missing ??
        attempt("tracing the first myofibril and starting the second", async () => {
          const pair = item(2);
          for (const [x, y] of chainClicks(pair, 2)) {
            await clickDisplay(context.page, x, y);
          }
          await pressAction(context.page, "next-myofibril");
          const [w] = displaySize(pair);
          await clickDisplay(context.page, Math.round(w * 0.3), 300);
          await settle(context);
        })
      );
    },
  },
  {
    name: "study-skip-dialog",
    title: "Study skip dialog with no reason chosen",
    story: STORY,
    reach: async (context) => {
      const missing = await inSession(context, 0);
      if (missing !== null) {
        return missing;
      }
      await context.page.locator("[data-part='study-dock'] [data-action='skip']").click();
      return (await shown(context.page.locator("[data-part='skip-dialog']"))) ? null : "Skip item… opens no skip dialog";
    },
  },
  {
    name: "study-paused",
    title: "Study session paused",
    story: STORY,
    reach: async (context) => {
      const missing = await inSession(context, 0);
      if (missing !== null) {
        return missing;
      }
      await context.page.locator(".study__pause").click();
      return (await shown(context.page.locator("[data-part='paused']"))) ? null : "Pause shows no paused screen";
    },
  },
  {
    name: "study-finished",
    title: "Study session finished, session file downloaded",
    story: STORY,
    reach: async (context) => {
      const missing = await inSession(context, ITEMS.length);
      if (missing !== null) {
        return missing;
      }
      return (await shown(context.page.locator("[data-part='finished']"))) ? null : "the last Next item shows no finished screen";
    },
  },
  {
    name: "study-autosave-failure",
    title: "Study autosave failure: blocking banner, input disabled",
    story: "M4-2, M4-3",
    reach: async (context) => {
      await context.page.addInitScript(() => {
        const proto = IDBObjectStore.prototype;
        const put = proto.put;
        proto.put = function failing(this: IDBObjectStore, ...args: Parameters<IDBObjectStore["put"]>): IDBRequest<IDBValidKey> {
          if ((globalThis as unknown as { __sarcostatTestFailPut?: boolean }).__sarcostatTestFailPut === true) {
            throw new DOMException("injected failure", "QuotaExceededError");
          }
          return put.apply(this, args);
        };
      });
      const missing = await inSession(context, 0);
      if (missing !== null) {
        return missing;
      }
      const page = context.page;
      await page.evaluate(() => Object.defineProperty(globalThis, "__sarcostatTestFailPut", { value: true, configurable: true }));
      const [x, y] = shapeClicks(item(0))[0] as [number, number];
      const view = await page.locator("[data-part='study-canvas']").evaluate((canvas: HTMLCanvasElement) => {
        const rect = canvas.getBoundingClientRect();
        return { left: rect.left, top: rect.top, zoom: Number(canvas.dataset.zoom), offsetX: Number(canvas.dataset.offsetX), offsetY: Number(canvas.dataset.offsetY) };
      });
      await page.mouse.click(view.left + view.offsetX + view.zoom * (x + 0.5), view.top + view.offsetY + view.zoom * (y + 0.5));
      if (!(await shown(page.locator("[data-part='study-banner']")))) {
        return "a failing autosave shows no banner";
      }
      await settle(context);
      return null;
    },
  },
  {
    name: "study-fatal",
    title: "Study fatal screen after the engine stopped answering",
    story: STORY,
    reach: async (context) => {
      const missing = await inSession(context, 0);
      if (missing !== null) {
        return missing;
      }
      const page = context.page;
      await page.evaluate(() => (globalThis as unknown as { __sarcostatTestRecorder: { terminate(): void } }).__sarcostatTestRecorder.terminate());
      const [x, y] = shapeClicks(item(0))[0] as [number, number];
      await clickDisplay(page, x, y).catch(() => undefined);
      const fatal = page.locator(".fatal[role='alertdialog']");
      return (await shown(fatal, 20_000)) ? null : "the stopped study engine shows no fatal screen";
    },
  },
];
