import path from "node:path";

import type { Page } from "@playwright/test";

import { startServer } from "../../scripts/serve.mjs";
import { en } from "../../src/strings/en.ts";
import { fill } from "../../src/strings/format.ts";
import { makeBuilds } from "./builds.mjs";
import { expect, test } from "./fixtures.ts";
import {
  BOOT_TIMEOUT_MS,
  checkBrowser,
  chooseKit,
  clickDisplay,
  confirmIdentity,
  DEMO_KIT,
  download,
  finishSession,
  installStorageLog,
  KITS_DIR,
  readStudyDb,
  sha256,
  storageLog,
  STUDY_OVERLAY,
  type StoredDatabase,
} from "./study_view.ts";

interface Site {
  key: string;
  version: string;
  path: string;
  source_digest: string;
  resume_digests: string[];
  build_json_sha256: string;
}

type Sites = Record<"b1" | "rc1" | "hotfix" | "next", Site>;

const t = en.study;
const OTHER_KIT = path.join(KITS_DIR, "marked", "identity");
let origin = "";
let sites: Sites;
let stop: () => Promise<void> = async () => {};

test.describe.configure({ mode: "serial" });

test.beforeAll(async () => {
  test.setTimeout(300_000);
  const built = (await makeBuilds({ log: () => {} })) as { root: string; sites: Sites };
  sites = built.sites;
  const served = (await startServer({ root: built.root, port: 0 })) as { url: string; server: { close(callback: () => void): void } };
  origin = served.url;
  stop = () => new Promise((resolve) => served.server.close(() => resolve()));
});

test.afterAll(async () => {
  await stop();
});

async function enterStudyAt(page: Page, site: Site): Promise<void> {
  await page.goto(new URL(`${site.path}#/`, origin).href);
  await expect(page.locator('.statusline[data-state="ready"] [data-item="version"]')).toContainText(site.version, { timeout: BOOT_TIMEOUT_MS });
  await page.locator(".mode-card--study").click();
  await expect(page.locator("[data-part='study-start']")).toBeVisible();
  await checkBrowser(page);
}

async function refusalOf(page: Page, kit: string): Promise<string[]> {
  await chooseKit(page, kit);
  await expect(page.locator("[data-part='refusals']")).toBeVisible({ timeout: BOOT_TIMEOUT_MS });
  await expect(page.locator("[data-part='identity']")).toHaveCount(0);
  return page.locator("[data-part='refusals'] .study-refused__line").allTextContents();
}

async function startAt(page: Page, site: Site, kit: string, clicks: number): Promise<void> {
  await enterStudyAt(page, site);
  await chooseKit(page, kit);
  await expect(page.locator("[data-part='identity']")).toBeVisible({ timeout: BOOT_TIMEOUT_MS });
  await confirmIdentity(page);
  for (let k = 0; k < clicks; k += 1) {
    await clickDisplay(page, 120 + 20 * k, 140);
  }
}

async function resumeAt(page: Page, site: Site, kit: string, dots: number): Promise<void> {
  await enterStudyAt(page, site);
  await chooseKit(page, kit);
  await expect(page.locator("[data-part='resumed']")).toBeVisible({ timeout: BOOT_TIMEOUT_MS });
  await confirmIdentity(page);
  await expect(page.locator(`${STUDY_OVERLAY} .ov__dot`)).toHaveCount(dots);
}

function recordOf(db: StoredDatabase | null, session: string): StoredDatabase["records"][number] | undefined {
  return db?.records.find((record) => record.session_id === session);
}

function assertNeverReopened(log: string[]): void {
  expect(log.filter((entry) => entry.startsWith("delete "))).toEqual([]);
  expect(log.filter((entry) => /^open sarcostat-study-v1 (?!1$|-$)/.test(entry))).toEqual([]);
}

test.describe("study builds under one origin (local multi-build site)", () => {
  test("(a) rc1 refuses the session b1 opened, naming /v<b1>/; a session started in rc1 resumes in rc1 and in its hotfix, which lists rc1's digest", async ({ page }) => {
    test.setTimeout(240_000);
    expect(sites.hotfix.resume_digests).toEqual([sites.rc1.source_digest]);
    expect(new Set(Object.values(sites).map((site) => site.source_digest)).size).toBe(4);
    await page.addInitScript(installStorageLog);
    await startAt(page, sites.b1, DEMO_KIT, 1);
    const created = recordOf(await readStudyDb(page), "S1");
    expect(created).toMatchObject({ build_version: sites.b1.version, source_digest: sites.b1.source_digest, build_json_sha256: sites.b1.build_json_sha256 });

    await enterStudyAt(page, sites.rc1);
    const lines = await refusalOf(page, DEMO_KIT);
    expect(lines).toEqual([fill(t.refused.otherBuild, { version: sites.b1.version })]);
    expect(lines[0]).toContain(`/v${sites.b1.version}/`);
    await startAtSamePage(page, OTHER_KIT, 1);
    await resumeAt(page, sites.rc1, OTHER_KIT, 1);
    await clickDisplay(page, 180, 180);
    await resumeAt(page, sites.hotfix, OTHER_KIT, 2);
    await clickDisplay(page, 200, 200);
    const record = recordOf(await readStudyDb(page), "M1");
    expect(record?.build_version).toBe(sites.rc1.version);
    expect(record?.builds_seen.map((seen) => seen.version)).toEqual([sites.rc1.version, sites.rc1.version, sites.hotfix.version]);
    expect(record?.builds_seen[2]).toEqual({ version: sites.hotfix.version, build_json_sha256: sites.hotfix.build_json_sha256, source_digest: sites.hotfix.source_digest });
    expect(recordOf(await readStudyDb(page), "S1")).toEqual(created);
    assertNeverReopened(await storageLog(page));
  });

  test("(b) the next build refuses rc1's open session naming /v<rc1>/, never opens the database at another version, and rc1 still resumes it", async ({ page }) => {
    test.setTimeout(240_000);
    await page.addInitScript(installStorageLog);
    await startAt(page, sites.rc1, DEMO_KIT, 2);
    const before = await readStudyDb(page);
    await enterStudyAt(page, sites.next);
    expect(await refusalOf(page, DEMO_KIT)).toEqual([fill(t.refused.otherBuild, { version: sites.rc1.version })]);
    assertNeverReopened(await storageLog(page));
    expect(await readStudyDb(page)).toEqual(before);
    await resumeAt(page, sites.rc1, DEMO_KIT, 2);
    assertNeverReopened(await storageLog(page));
  });

  test("(c) reverse order: the next build opens a fresh profile first, then rc1 creates and resumes a session", async ({ page }) => {
    test.setTimeout(240_000);
    await page.addInitScript(installStorageLog);
    await enterStudyAt(page, sites.next);
    const fresh = await readStudyDb(page);
    expect(fresh).toEqual({ version: 1, stores: ["sessions"], records: [] });
    await startAt(page, sites.rc1, DEMO_KIT, 1);
    await resumeAt(page, sites.rc1, DEMO_KIT, 1);
    expect(recordOf(await readStudyDb(page), "S1")?.builds_seen.map((seen) => seen.version)).toEqual([sites.rc1.version, sites.rc1.version]);
    assertNeverReopened(await storageLog(page));
  });

  test("(d) a v2 store (test-only opener) uses a new database name and reads v1 read-only; v1 is never reopened or deleted and its records stay byte-identical", async ({ page }) => {
    test.setTimeout(240_000);
    await page.addInitScript(installStorageLog);
    await startAt(page, sites.rc1, DEMO_KIT, 2);
    const before = await readStudyDb(page);
    await page.addInitScript((nextPath: string) => {
      if (!location.pathname.startsWith(nextPath)) {
        return;
      }
      const done = (value: unknown): void => {
        Object.defineProperty(globalThis, "__sarcostatTestV2", { value, configurable: true });
      };
      const request = <T>(req: IDBRequest<T>): Promise<T> =>
        new Promise((resolve, reject) => {
          req.onsuccess = () => resolve(req.result);
          req.onerror = () => reject(req.error);
        });
      void (async () => {
        const v1 = await request(indexedDB.open("sarcostat-study-v1"));
        const records = await request(v1.transaction("sessions", "readonly").objectStore("sessions").getAll());
        v1.close();
        const opening = indexedDB.open("sarcostat-study-v2", 1);
        opening.onupgradeneeded = () => {
          opening.result.createObjectStore("sessions", { keyPath: ["study_id", "annotator_id", "session_id", "manifest_sha256"] });
        };
        const v2 = await request(opening);
        const write = v2.transaction("sessions", "readwrite");
        for (const value of records) {
          write.objectStore("sessions").put({ ...(value as object), migrated_from: "sarcostat-study-v1" });
        }
        await new Promise((resolve) => (write.oncomplete = resolve));
        v2.close();
        done(records.length);
      })().catch((error: unknown) => done(String(error)));
    }, sites.next.path);
    await enterStudyAt(page, sites.next);
    await expect.poll(() => page.evaluate(() => (globalThis as unknown as { __sarcostatTestV2?: unknown }).__sarcostatTestV2)).toBe(1);
    expect(await readStudyDb(page)).toEqual(before);
    const databases = await page.evaluate(() => indexedDB.databases());
    expect(databases.map((entry) => [entry.name, entry.version]).sort()).toEqual([
      ["sarcostat-study-v1", 1],
      ["sarcostat-study-v2", 1],
    ]);
    const log = await storageLog(page);
    assertNeverReopened(log);
    expect(log).toContain("open sarcostat-study-v2 1");
    await resumeAt(page, sites.rc1, DEMO_KIT, 2);
  });

  test("(e) a finished session is blocked in every build, which offers only the same bytes again", async ({ page }) => {
    test.setTimeout(300_000);
    await startAt(page, sites.rc1, DEMO_KIT, 0);
    const file = await finishSession(page, DEMO_KIT);
    for (const site of [sites.b1, sites.rc1, sites.hotfix, sites.next]) {
      await enterStudyAt(page, site);
      await chooseKit(page, DEMO_KIT);
      await expect(page.locator("[data-part='locked']"), site.version).toBeVisible({ timeout: BOOT_TIMEOUT_MS });
      await expect(page.locator("[data-part='identity']")).toHaveCount(0);
      const copy = await download(page, () => page.locator("[data-part='locked'] [data-action='download-again']").click());
      expect(sha256(copy.bytes), site.version).toBe(sha256(file.bytes));
    }
  });
});

async function startAtSamePage(page: Page, kit: string, clicks: number): Promise<void> {
  await chooseKit(page, kit);
  await expect(page.locator("[data-part='identity']")).toBeVisible({ timeout: BOOT_TIMEOUT_MS });
  await confirmIdentity(page);
  for (let k = 0; k < clicks; k += 1) {
    await clickDisplay(page, 120 + 20 * k, 140);
  }
}
