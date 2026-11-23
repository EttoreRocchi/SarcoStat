import { en } from "../../src/strings/en.ts";
import { expect, test } from "./fixtures.ts";
import {
  AUTOSAVE,
  checkBrowser,
  clickDisplay,
  confirmIdentity,
  DEMO_KIT,
  enterStudy,
  holdResponses,
  openKit,
  pixelCentre,
  readStudyDb,
  studyView,
  studyWorker,
  STUDY_OVERLAY,
  watchWorkers,
} from "./study_view.ts";

const t = en.study;

test("a study worker stopped mid-item shows the Reload to resume screen; after the reload no acknowledged action is lost and the restart is counted", async ({ page, recorder }) => {
  test.setTimeout(180_000);
  const workers = watchWorkers(page);
  await enterStudy(page);
  await checkBrowser(page);
  const worker = await studyWorker(workers);
  await openKit(page, DEMO_KIT);
  await confirmIdentity(page);
  await clickDisplay(page, 140, 140);
  await clickDisplay(page, 220, 140);
  const acknowledged = (await readStudyDb(page))?.records[0];
  await holdResponses(worker, true);
  const view = await studyView(page);
  for (const [x, y] of [
    [140, 200],
    [220, 200],
    [180, 260],
  ] as const) {
    const at = pixelCentre(view, x, y);
    await page.mouse.click(at.x, at.y);
  }
  await expect(page.locator(AUTOSAVE)).toHaveAttribute("data-state", "saving");
  await page.evaluate(() => (globalThis as unknown as { __sarcostatTestRecorder: { terminate(): void } }).__sarcostatTestRecorder.terminate());
  const fatal = page.locator(".fatal[role='alertdialog']");
  await expect(fatal).toBeVisible({ timeout: 20_000 });
  await expect(fatal.locator("[data-part='detail']")).toContainText(t.fatal);
  await expect(fatal.locator(".fatal__body").first()).toContainText("Reload to resume");
  expect(await page.evaluate(() => sessionStorage.getItem("sarcostat-study-v1:restart"))).toBe("1");
  expect((await readStudyDb(page))?.records[0]).toEqual(acknowledged);
  expect((await recorder.calls("app")).filter((call) => call.cmd === "study.click")).toHaveLength(3);

  await page.reload();
  await enterStudy(page);
  await checkBrowser(page);
  await openKit(page, DEMO_KIT);
  await expect(page.locator("[data-part='resumed']")).toBeVisible();
  await confirmIdentity(page);
  await expect(page.locator(`${STUDY_OVERLAY} .ov__dot`)).toHaveCount(2);
  const record = (await readStudyDb(page))?.records[0];
  expect(record?.seq).toBe((acknowledged?.seq ?? 0) + 1);
  expect(record?.counts).toEqual({ pauses: 0, resumes: 1, worker_restarts: 1 });
  expect(await page.evaluate(() => sessionStorage.getItem("sarcostat-study-v1:restart"))).toBeNull();
});
