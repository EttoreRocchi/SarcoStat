import { en } from "../../src/strings/en.ts";
import { fill } from "../../src/strings/format.ts";
import { REPORT_KEYS } from "../../src/state/study_report.ts";
import { expect, test } from "./fixtures.ts";
import {
  checkBrowser,
  chooseKit,
  clickDisplay,
  completeItem,
  confirmIdentity,
  DEMO_KIT,
  download,
  enterStudy,
  finishSession,
  installStorageLog,
  installWallClock,
  itemCount,
  openKit,
  readManifest,
  readStudyDb,
  setWallOffset,
  sha256,
  shapeClicks,
  startSession,
  storageLog,
  STUDY_CANVAS,
  STUDY_OVERLAY,
  type ManifestItem,
} from "./study_view.ts";

const t = en.study;
const DEMO = readManifest(DEMO_KIT);
const FIRST = DEMO.items[0] as ManifestItem;
const FILE_NAME = `${DEMO.study_id}_${DEMO.annotator_id}_${DEMO.session_id}.json`;

interface SessionItem {
  started_utc: string;
  finished_utc: string;
  active_seconds: number;
  raw_clicks: unknown[];
}

interface SessionFile {
  created_utc: string;
  items: SessionItem[];
}

function seconds(utc: string): number {
  return Date.parse(utc) / 1000;
}

test.describe("study persistence and resume", () => {
  test("closed mid-item after three clicks and reopened 600 s later: same item, same clicks, active time without the gap, no banner, builds_seen grows with the first acknowledged command", async ({ page }) => {
    test.setTimeout(180_000);
    await startSession(page, DEMO_KIT);
    for (const [x, y] of shapeClicks(FIRST).slice(0, 3)) {
      await clickDisplay(page, x, y);
    }
    const before = await readStudyDb(page);
    const stored = before?.records[0];
    expect(before?.records).toHaveLength(1);
    expect(stored?.builds_seen).toHaveLength(1);
    expect(stored?.counts).toEqual({ pauses: 0, resumes: 0, worker_restarts: 0 });
    const context = page.context();
    await page.close();

    const again = await context.newPage();
    await again.addInitScript(installWallClock, 600_000);
    await enterStudy(again);
    await checkBrowser(again);
    await openKit(again, DEMO_KIT);
    await expect(again.locator("[data-part='resumed']")).toHaveText(fill(t.identity.resumed, { item: 1, total: DEMO.items.length }));
    await expect(again.locator("[data-part='study-banner']")).toHaveCount(0);
    expect((await readStudyDb(again))?.records[0]).toEqual(stored);
    await confirmIdentity(again);
    await expect(again.locator(`${STUDY_OVERLAY} .ov__dot`)).toHaveCount(3);
    expect(await itemCount(again)).toBe(fill(t.header.item, { index: 1, total: DEMO.items.length }));
    await expect(again.locator("[data-part='chips'] li[data-state='done']")).toHaveCount(3);
    const reopened = (await readStudyDb(again))?.records[0];
    expect(reopened?.seq).toBe((stored?.seq ?? 0) + 1);
    expect(reopened?.builds_seen).toHaveLength(2);
    expect(reopened?.builds_seen[1]).toEqual(stored?.builds_seen[0]);
    expect(reopened?.counts).toEqual({ pauses: 0, resumes: 1, worker_restarts: 0 });
    await expect(again.locator("[data-part='study-banner']")).toHaveCount(0);
    for (const [x, y] of shapeClicks(FIRST).slice(3)) {
      await clickDisplay(again, x, y);
    }
    expect((await readStudyDb(again))?.records[0]?.builds_seen).toHaveLength(2);
    await again.locator("[data-part='study-dock'] [data-action='next']").click();
    await expect(again.locator("[data-part='item-count']")).toHaveText(fill(t.header.item, { index: 2, total: DEMO.items.length }));
    const file = await finishSession(again, DEMO_KIT, 1);
    const session = JSON.parse(file.bytes.toString("utf8")) as SessionFile;
    const item = session.items[0] as SessionItem;
    expect(item.raw_clicks).toHaveLength(6);
    expect(seconds(item.finished_utc) - seconds(item.started_utc)).toBeGreaterThanOrEqual(600);
    expect(item.active_seconds).toBeLessThan(120);

    const report = await download(again, () => again.locator("[data-part='finished'] [data-action='download-report']").click());
    expect(report.name).toBe(`${DEMO.study_id}_${DEMO.annotator_id}_${DEMO.session_id}.report.json`);
    const parsed = JSON.parse(report.bytes.toString("utf8")) as Record<string, unknown>;
    expect(Object.keys(parsed).sort()).toEqual([...REPORT_KEYS].sort());
    expect(parsed).toMatchObject({ finished_sha256: sha256(file.bytes), counts: { pauses: 0, resumes: 1, worker_restarts: 0 } });
    expect((parsed.builds_seen as unknown[]).length).toBe(2);
    expect(report.bytes.toString("utf8")).not.toMatch(/raw_clicks|points|seconds|"x"|"y"/);
  });

  test("a wall clock set back one hour and then forward one day between items leaves every timestamp non-decreasing", async ({ page, recorder }) => {
    test.setTimeout(180_000);
    await page.addInitScript(installWallClock, 0);
    await startSession(page, DEMO_KIT);
    await completeItem(page, FIRST);
    await setWallOffset(page, -3_600_000);
    await completeItem(page, DEMO.items[1] as ManifestItem);
    await setWallOffset(page, 86_400_000);
    const file = await finishSession(page, DEMO_KIT, 2);
    const walls = (await recorder.calls("app")).filter((call) => call.cmd.startsWith("study.") && call.cmd !== "study.open_kit" && call.cmd !== "study.verify_items").map((call) => (call.args as { t: { wall_ms: number } }).t.wall_ms);
    expect(Math.min(...walls.slice(1).map((wall, k) => wall - (walls[k] as number)))).toBeLessThan(-3_000_000);
    const session = JSON.parse(file.bytes.toString("utf8")) as SessionFile;
    const stamps = [session.created_utc, ...session.items.flatMap((entry) => [entry.started_utc, entry.finished_utc])];
    for (let k = 1; k < stamps.length; k += 1) {
      expect(stamps[k]! >= stamps[k - 1]!, `${stamps[k - 1]} <= ${stamps[k]}`).toBe(true);
    }
  });

  test("the finished file is committed before it is downloaded; reopening offers only the same bytes again", async ({ page }) => {
    test.setTimeout(180_000);
    await page.addInitScript(installStorageLog);
    await startSession(page, DEMO_KIT);
    const file = await finishSession(page, DEMO_KIT);
    expect(file.name).toBe(FILE_NAME);
    const log = await storageLog(page);
    const at = log.indexOf(`download ${FILE_NAME}`);
    expect(at).toBeGreaterThan(0);
    expect(log[at - 1]).toBe("complete sarcostat-study-v1");
    const record = (await readStudyDb(page))?.records[0];
    expect(record).toMatchObject({ status: "finished", finished_sha256: sha256(file.bytes) });
    expect(sha256(record?.finished_bytes ?? [])).toBe(sha256(file.bytes));
    const context = page.context();
    await page.close();

    const again = await context.newPage();
    await enterStudy(again);
    await checkBrowser(again);
    await chooseKit(again, DEMO_KIT);
    await expect(again.locator("[data-part='locked']")).toBeVisible({ timeout: 30_000 });
    await expect(again.locator("[data-part='identity']")).toHaveCount(0);
    await expect(again.locator(STUDY_CANVAS)).toHaveCount(0);
    await expect(again.locator("[data-part='locked'] button")).toHaveCount(1);
    const copy = await download(again, () => again.locator("[data-part='locked'] [data-action='download-again']").click());
    expect(copy.name).toBe(FILE_NAME);
    expect(sha256(copy.bytes)).toBe(sha256(file.bytes));
  });

  test("a crash between the commit and the download leaves a finished record that can be downloaded again", async ({ page }) => {
    test.setTimeout(180_000);
    await page.addInitScript(installStorageLog);
    await startSession(page, DEMO_KIT);
    for (const entry of DEMO.items.slice(0, -1)) {
      await completeItem(page, entry);
    }
    await page.evaluate(() => Object.defineProperty(globalThis, "__sarcostatTestDropDownloads", { value: true }));
    const downloads: string[] = [];
    page.on("download", (event) => downloads.push(event.suggestedFilename()));
    const last = DEMO.items[DEMO.items.length - 1] as ManifestItem;
    for (const [x, y] of shapeClicks(last)) {
      await clickDisplay(page, x, y);
    }
    await page.locator("[data-part='study-dock'] [data-action='next']").click();
    await expect.poll(async () => (await storageLog(page)).includes(`download ${FILE_NAME}`)).toBe(true);
    expect(downloads).toEqual([]);
    const record = (await readStudyDb(page))?.records[0];
    expect(record?.status).toBe("finished");
    const context = page.context();
    await page.close();

    const again = await context.newPage();
    await enterStudy(again);
    await checkBrowser(again);
    await chooseKit(again, DEMO_KIT);
    await expect(again.locator("[data-part='locked']")).toBeVisible({ timeout: 30_000 });
    const copy = await download(again, () => again.locator("[data-part='locked'] [data-action='download-again']").click());
    expect(sha256(copy.bytes)).toBe(record?.finished_sha256);
    expect(JSON.parse(copy.bytes.toString("utf8")).items).toHaveLength(DEMO.items.length);
  });

  test("a second tab cannot open a session that is open in the first one", async ({ page }) => {
    test.setTimeout(180_000);
    await startSession(page, DEMO_KIT);
    await clickDisplay(page, 150, 150);
    const second = await page.context().newPage();
    await enterStudy(second);
    await checkBrowser(second);
    await chooseKit(second, DEMO_KIT);
    await expect(second.locator("[data-part='refusals'] .study-refused__line")).toHaveText([t.refused.otherTab], { timeout: 30_000 });
    await expect(second.locator("[data-part='identity']")).toHaveCount(0);
    await clickDisplay(page, 160, 160);
    await page.close();
    await chooseKit(second, DEMO_KIT);
    await expect(second.locator("[data-part='resumed']")).toBeVisible({ timeout: 30_000 });
    await confirmIdentity(second);
    await expect(second.locator(`${STUDY_OVERLAY} .ov__dot`)).toHaveCount(2);
  });
});
