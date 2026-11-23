import fs from "node:fs";
import path from "node:path";

import type { Page } from "@playwright/test";

import { en } from "../../src/strings/en.ts";
import { expect, test } from "./fixtures.ts";
import {
  checkBrowser,
  clickDisplay,
  confirmIdentity,
  DEMO_KIT,
  enterStudy,
  installStorageLog,
  openKit,
  pixelCentre,
  readManifest,
  readStudyDb,
  reloadApp,
  sha256,
  startSession,
  storageLog,
  studyView,
  STUDY_OVERLAY,
} from "./study_view.ts";

const t = en.study;
const DEMO = readManifest(DEMO_KIT);

function stubPersistence(value: boolean): void {
  const answer = async (): Promise<boolean> => value;
  const storage = { persist: answer, persisted: answer };
  Object.defineProperty(navigator, "storage", { configurable: true, get: () => storage });
}

function stubbedPersisted(page: Page): Promise<boolean> {
  return page.evaluate(() => navigator.storage.persisted());
}

test.describe("study storage in this browser", () => {
  test("every acknowledged action is in IndexedDB and survives a reload", async ({ page }) => {
    test.setTimeout(120_000);
    await page.addInitScript(installStorageLog);
    await startSession(page, DEMO_KIT);
    await clickDisplay(page, 150, 150);
    await clickDisplay(page, 210, 150);
    const log = await storageLog(page);
    const db = await readStudyDb(page);
    expect(db?.version).toBe(1);
    expect(db?.stores).toEqual(["sessions"]);
    const manifest = sha256(fs.readFileSync(path.join(DEMO_KIT, "manifest.json")));
    expect(db?.records.map((record) => [record.study_id, record.annotator_id, record.session_id, record.manifest_sha256, record.status])).toEqual([[DEMO.study_id, DEMO.annotator_id, DEMO.session_id, manifest, "open"]]);
    expect(log.filter((entry) => entry.startsWith("open ")).length).toBeGreaterThan(0);
    expect(log.filter((entry) => entry.startsWith("open "))).toEqual(log.filter((entry) => entry.startsWith("open ")).map(() => "open sarcostat-study-v1 1"));
    expect(log.filter((entry) => entry.startsWith("delete "))).toEqual([]);
    expect(log.filter((entry) => entry.startsWith("lock "))).toEqual([`lock sarcostat-study-v1:${DEMO.study_id}/${DEMO.annotator_id}/${DEMO.session_id}/${manifest} {"ifAvailable":true}`]);
    await reloadApp(page);
    await enterStudy(page);
    await checkBrowser(page);
    await openKit(page, DEMO_KIT);
    await confirmIdentity(page);
    await expect(page.locator(`${STUDY_OVERLAY} .ov__dot`)).toHaveCount(2);
  });

  test("persisted() false shows the eviction risk and the one-sitting rule as a warning that does not block", async ({ page }) => {
    test.setTimeout(120_000);
    await page.addInitScript(stubPersistence, false);
    await enterStudy(page);
    expect(await stubbedPersisted(page), "stubbed navigator.storage.persisted()").toBe(false);
    await checkBrowser(page);
    const row = page.locator("[data-part='readiness'] [data-check='persisted']");
    await expect(row).toHaveAttribute("data-state", "warn");
    await expect(row.locator(".study-ready__text")).toHaveText(t.readiness.items.persisted.fail);
    await expect(row.locator(".study-ready__state")).toHaveText(t.readiness.warn);
  });

  test("persisted() true passes the persistent-storage check", async ({ page }) => {
    test.setTimeout(120_000);
    await page.addInitScript(stubPersistence, true);
    await enterStudy(page);
    expect(await stubbedPersisted(page), "stubbed navigator.storage.persisted()").toBe(true);
    await checkBrowser(page);
    const row = page.locator("[data-part='readiness'] [data-check='persisted']");
    await expect(row).toHaveAttribute("data-state", "pass");
    await expect(row.locator(".study-ready__text")).toHaveText(t.readiness.items.persisted.pass);
  });

  test("a failing IndexedDB write shows a blocking banner and disables every input", async ({ page, recorder }) => {
    test.setTimeout(120_000);
    await page.addInitScript(() => {
      const put = IDBObjectStore.prototype.put;
      IDBObjectStore.prototype.put = function failing(this: IDBObjectStore, ...args: Parameters<IDBObjectStore["put"]>): IDBRequest<IDBValidKey> {
        if ((globalThis as unknown as { __sarcostatTestFailPut?: boolean }).__sarcostatTestFailPut === true) {
          throw new DOMException("injected failure", "QuotaExceededError");
        }
        return put.apply(this, args);
      };
    });
    await startSession(page, DEMO_KIT);
    await clickDisplay(page, 150, 150);
    await page.evaluate(() => Object.defineProperty(globalThis, "__sarcostatTestFailPut", { value: true }));
    const view = await studyView(page);
    const at = pixelCentre(view, 200, 150);
    await page.mouse.click(at.x, at.y);
    const banner = page.locator("[data-part='study-banner']");
    await expect(banner).toBeVisible();
    await expect(banner).toHaveText(t.banner.autosave);
    await expect(page.locator("[data-part='autosave']")).toHaveAttribute("data-state", "failed");
    for (const action of ["undo", "m-line", "skip", "next"]) {
      await expect(page.locator(`[data-part='study-dock'] [data-action='${action}']`)).toHaveAttribute("aria-disabled", "true");
    }
    await expect(page.locator(".study__pause")).toHaveAttribute("aria-disabled", "true");
    const clicks = (await recorder.calls("app")).filter((call) => call.cmd.startsWith("study.")).length;
    await page.mouse.click(at.x + 30, at.y + 30);
    await page.keyboard.press("Backspace");
    await page.keyboard.press("p");
    await page.keyboard.press("s");
    await page.waitForTimeout(300);
    expect((await recorder.calls("app")).filter((call) => call.cmd.startsWith("study.")).length).toBe(clicks);
    await expect(page.locator("[data-part='skip-dialog']")).toBeHidden();
    expect((await readStudyDb(page))?.records[0]?.seq).toBe(2);
  });
});
