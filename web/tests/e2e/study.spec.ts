import fs from "node:fs";
import path from "node:path";

import type { Page } from "@playwright/test";

import { en } from "../../src/strings/en.ts";
import { fill } from "../../src/strings/format.ts";
import { expect, test } from "./fixtures.ts";
import {
  AUTOSAVE,
  BOOT_TIMEOUT_MS,
  checkBrowser,
  chooseKit,
  clickDisplay,
  completeItem,
  confirmIdentity,
  copyKit,
  decodeGreyPng,
  DEMO_KIT,
  displaySize,
  download,
  enterStudy,
  greyRgba,
  holdResponses,
  installReadSpy,
  installStudyPointerProbe,
  kitReads,
  KITS_DIR,
  LOUPE_CANVAS,
  openKit,
  pixelCentre,
  pressAction,
  probedDowns,
  readManifest,
  seq,
  shapeClicks,
  STUDY_CANVAS,
  STUDY_OVERLAY,
  studyView,
  studyWorker,
  watchWorkers,
  wheelTo,
  type ManifestItem,
  type ProbedDown,
} from "./study_view.ts";
import { readback, referenceMapping } from "./workspace_view.ts";

const t = en.study;
const DEMO = readManifest(DEMO_KIT);
const ITEMS = DEMO.items;

function item(index: number): ManifestItem {
  return ITEMS[index] as ManifestItem;
}

async function refusalLines(page: Page): Promise<string[]> {
  await expect(page.locator("[data-part='refusals']")).toBeVisible({ timeout: BOOT_TIMEOUT_MS });
  return page.locator("[data-part='refusals'] .study-refused__line").allTextContents();
}

interface SessionFile {
  items: {
    item_id: string;
    type: string;
    raw_clicks: { x: number; y: number; order: number; undo_count: number }[];
    points: Record<string, unknown>;
    skipped: boolean;
    skip_reason: string | null;
    skipped_parts: string[];
  }[];
}

test.describe("study mode", () => {
  test("refuses a damaged or wrong kit before any item is shown, one line each, and never reads an unlisted file", async ({ page, recorder }) => {
    test.setTimeout(120_000);
    await page.addInitScript(installReadSpy);
    await enterStudy(page);
    await checkBrowser(page);
    const cases: { name: string; kit: () => string; line: string | RegExp }[] = [
      {
        name: "sidecar missing",
        kit: () => {
          const kit = copyKit(DEMO_KIT, "nosidecar");
          fs.rmSync(path.join(kit, "manifest.sha256"));
          return kit;
        },
        line: fill(t.refused.missing, { file: "manifest.sha256" }),
      },
      {
        name: "sidecar wrong",
        kit: () => {
          const kit = copyKit(DEMO_KIT, "wrongsidecar");
          fs.writeFileSync(path.join(kit, "manifest.sha256"), `${"0".repeat(64)}  manifest.json\n`);
          return kit;
        },
        line: /^The kit was refused: SHA-256 of manifest\.json does not match manifest\.sha256\.$/,
      },
      {
        name: "manifest edited",
        kit: () => {
          const kit = copyKit(DEMO_KIT, "editedmanifest");
          const file = path.join(kit, "manifest.json");
          fs.writeFileSync(file, fs.readFileSync(file, "utf8").replace('"session_id": "S1"', '"session_id": "S2"'));
          return kit;
        },
        line: /^The kit was refused: SHA-256 of manifest\.json does not match manifest\.sha256\.$/,
      },
      {
        name: "an item edited by one byte",
        kit: () => {
          const kit = copyKit(DEMO_KIT, "editeditem");
          const file = path.join(kit, item(1).kit_file);
          const bytes = fs.readFileSync(file);
          bytes[bytes.length - 20] = (bytes[bytes.length - 20] ?? 0) ^ 1;
          fs.writeFileSync(file, bytes);
          return kit;
        },
        line: fill(t.refused.problems.item_sha256_mismatch, { file: item(1).kit_file }),
      },
      {
        name: "an item missing",
        kit: () => {
          const kit = copyKit(DEMO_KIT, "missingitem");
          fs.rmSync(path.join(kit, item(2).kit_file));
          return kit;
        },
        line: fill(t.refused.problems.item_missing, { file: item(2).kit_file }),
      },
      ...(["rgb", "grey16", "interlaced", "gama"] as const).map((name) => ({
        name: `an item replaced by a ${name} PNG`,
        kit: () => path.join(KITS_DIR, "refused", name),
        line: new RegExp(`^items/001\\.png is not a valid kit image: 'items/001\\.png' ${name === "interlaced" ? "must not be interlaced" : name === "gama" ? "has a 'gAMA' chunk" : "must be an 8-bit greyscale PNG"}`),
      })),
    ];
    for (const entry of cases) {
      await chooseKit(page, entry.kit());
      const lines = await refusalLines(page);
      expect(lines, entry.name).toHaveLength(1);
      if (typeof entry.line === "string") {
        expect(lines[0], entry.name).toBe(entry.line);
      } else {
        expect(lines[0], entry.name).toMatch(entry.line);
      }
      await expect(page.locator("[data-part='identity']"), entry.name).toHaveCount(0);
      await expect(page.locator(STUDY_CANVAS), entry.name).toHaveCount(0);
      await expect(page.locator("[data-part='study-start'] [data-action='open-kit']")).not.toHaveAttribute("aria-disabled", "true");
    }
    const commands = (await recorder.calls("app")).map((call) => call.cmd);
    expect(commands).not.toContain("study.start");

    await page.evaluate(() => (globalThis as unknown as { __sarcostatTestReads: string[] }).__sarcostatTestReads.splice(0));
    const kit = copyKit(DEMO_KIT, "unlisted");
    fs.writeFileSync(path.join(kit, "notes.txt"), "not part of the kit\n");
    fs.copyFileSync(path.join(kit, item(0).kit_file), path.join(kit, "items", "999.png"));
    fs.mkdirSync(path.join(kit, "extra"));
    fs.writeFileSync(path.join(kit, "extra", "data.json"), "{}\n");
    await openKit(page, kit);
    const root = path.basename(kit);
    const reads = await kitReads(page);
    const listed = [...new Set(ITEMS.map((entry) => entry.kit_file))];
    expect(reads.sort()).toEqual(["manifest.json", "manifest.sha256", ...listed].map((file) => `arrayBuffer ${root}/${file}`).sort());
  });

  test("verified bytes only: an item changed on disk after verification is shown from the verified bytes, which are read once", async ({ page }) => {
    test.setTimeout(120_000);
    await page.addInitScript(installReadSpy);
    const kit = copyKit(DEMO_KIT, "verified");
    const second = item(3);
    const file = path.join(kit, second.kit_file);
    const original = decodeGreyPng(fs.readFileSync(file));
    await enterStudy(page);
    await checkBrowser(page);
    await openKit(page, kit);
    fs.copyFileSync(path.join(KITS_DIR, "refused", "rgb", "items", "001.png"), file);
    await confirmIdentity(page);
    for (const entry of ITEMS.slice(0, 3)) {
      await completeItem(page, entry);
    }
    await expect(page.locator("[data-part='item-count']")).toHaveText(fill(t.header.item, { index: 4, total: ITEMS.length }));
    await expect.poll(async () => (await studyView(page)).zoom).toBeGreaterThan(0);
    const [width, height] = displaySize(second);
    expect([original.width, original.height]).toEqual([width, height]);
    const result = await readback(page, STUDY_CANVAS, width, height, greyRgba(original));
    expect(result.inside).toBeGreaterThan(0);
    expect(result.mismatches, JSON.stringify(result.first)).toBe(0);
    const root = path.basename(kit);
    expect((await kitReads(page)).filter((entry) => entry.endsWith(`${root}/${second.kit_file}`))).toEqual([`arrayBuffer ${root}/${second.kit_file}`]);
  });

  test("readback at fit, fit x 1.25, fit x 1.25^3 and in the loupe equals the kit PNG values", async ({ page }) => {
    test.setTimeout(120_000);
    await enterStudy(page);
    await checkBrowser(page);
    await openKit(page, DEMO_KIT);
    await confirmIdentity(page);
    const first = item(0);
    const image = decodeGreyPng(fs.readFileSync(path.join(DEMO_KIT, first.kit_file)));
    const expected = greyRgba(image);
    const fit = await studyView(page);
    for (const step of [0, 1, 3]) {
      const view = await wheelTo(page, step);
      expect(view.zoom).toBe(fit.zoom * 1.25 ** step);
      expect(view.zoom).toBeLessThanOrEqual(8);
      const result = await readback(page, STUDY_CANVAS, image.width, image.height, expected);
      expect(result.inside, `step ${step}`).toBeGreaterThan(0);
      expect(result.mismatches, `step ${step}: ${JSON.stringify(result.first)}`).toBe(0);
    }
    const view = await studyView(page);
    const at = pixelCentre(view, Math.round(image.width / 2), Math.round(image.height / 2));
    await page.mouse.move(at.x, at.y);
    await expect(page.locator("[data-part='loupe']")).toBeVisible();
    const loupe = await page.locator(LOUPE_CANVAS).evaluate((canvas: HTMLCanvasElement) => Number(canvas.dataset.zoom));
    expect(loupe).toBe(view.zoom * 3);
    const result = await readback(page, LOUPE_CANVAS, image.width, image.height, expected);
    expect(result.inside).toBeGreaterThan(0);
    expect(result.mismatches, JSON.stringify(result.first)).toBe(0);
    await expect(page.locator("[data-part='loupe'] .loupe__label")).toBeHidden();
  });

  test("event-time mapping: clicks queued during a barrier keep the view of their pointerdown, even after a zoom and a pan", async ({ page, recorder }) => {
    test.setTimeout(120_000);
    await page.addInitScript(installStudyPointerProbe);
    const workers = watchWorkers(page);
    await enterStudy(page);
    await checkBrowser(page);
    const worker = await studyWorker(workers);
    await openKit(page, DEMO_KIT);
    await confirmIdentity(page);
    const probed = (await probedDowns(page)).length;
    const before = await studyView(page);
    await holdResponses(worker, true);
    for (const [x, y] of [
      [150, 140],
      [230, 160],
      [180, 260],
    ] as const) {
      const at = pixelCentre(await studyView(page), x, y);
      await page.mouse.click(at.x, at.y);
    }
    await expect(page.locator(AUTOSAVE)).toHaveAttribute("data-state", "saving");
    const zoomed = await wheelTo(page, 2);
    expect(zoomed.zoom).not.toBe(before.zoom);
    const grab = { x: zoomed.left + zoomed.width / 2, y: zoomed.top + zoomed.height / 2 };
    await page.mouse.move(grab.x, grab.y);
    await page.mouse.down();
    await page.mouse.move(grab.x + 60, grab.y + 25, { steps: 6 });
    await page.mouse.up();
    const panned = await studyView(page);
    expect(panned.zoom).toBe(zoomed.zoom);
    expect([panned.offsetX, panned.offsetY]).not.toEqual([zoomed.offsetX, zoomed.offsetY]);
    const late = pixelCentre(panned, 200, 200);
    await page.mouse.click(late.x, late.y);
    expect((await recorder.calls("app")).filter((call) => call.cmd === "study.click")).toHaveLength(1);
    await holdResponses(worker, false);
    await expect.poll(async () => (await recorder.calls("app")).filter((call) => call.cmd === "study.click" && call.received !== null).length, { timeout: 10_000 }).toBe(4);
    const clicks = (await recorder.calls("app")).filter((call) => call.cmd === "study.click").map((call) => call.args as { x: number; y: number });
    const downs = (await probedDowns(page, probed)).filter((down) => down.part === "study-canvas");
    const sent = [downs[0], downs[1], downs[2], downs[downs.length - 1]] as ProbedDown[];
    expect(downs).toHaveLength(5);
    for (const [index, down] of sent.entries()) {
      const view = down.view as NonNullable<ProbedDown["view"]>;
      expect(view.zoom, `click ${index + 1} zoom`).toBe(index < 3 ? before.zoom : panned.zoom);
      const reference = referenceMapping(down.clientX, down.clientY, view);
      expect(clicks[index], `click ${index + 1}`).toEqual({ x: reference.x, y: reference.y, t: expect.any(Object) });
    }
    await expect(page.locator(`${STUDY_OVERLAY} .ov__dot`)).toHaveCount(4);
  });

  test("beyond eight inputs queued behind a barrier the dock says saving and ignores input; the eight are sent in order", async ({ page, recorder }) => {
    test.setTimeout(120_000);
    const workers = watchWorkers(page);
    await enterStudy(page);
    await checkBrowser(page);
    const worker = await studyWorker(workers);
    await openKit(page, DEMO_KIT);
    await confirmIdentity(page);
    const view = await studyView(page);
    await holdResponses(worker, true);
    const points = Array.from({ length: 12 }, (_, k) => [100 + 20 * k, 120 + 10 * k] as const);
    for (const [x, y] of points) {
      const at = pixelCentre(view, x, y);
      await page.mouse.click(at.x, at.y);
    }
    await expect(page.locator("[data-part='saving']")).toBeVisible();
    await expect(page.locator("[data-part='saving']")).toHaveText(t.dock.saving);
    expect((await recorder.calls("app")).filter((call) => call.cmd === "study.click")).toHaveLength(1);
    await holdResponses(worker, false);
    await expect.poll(async () => (await recorder.calls("app")).filter((call) => call.cmd === "study.click" && call.received !== null).length, { timeout: 10_000 }).toBe(9);
    await expect(page.locator("[data-part='saving']")).toBeHidden();
    const sent = (await recorder.calls("app")).filter((call) => call.cmd === "study.click").map((call) => call.args as { x: number; y: number });
    expect(sent.map((args) => [Math.round(args.x), Math.round(args.y)])).toEqual(points.slice(0, 9).map(([x, y]) => [x, y]));
    const engines = (await recorder.workers()).filter((entry) => /py\.worker/.test(entry.url));
    expect(engines.map((entry) => entry.type)).toEqual(["module", "module"]);
  });

  test("a full session: identity, chips, Next item only when complete, M-line skip, undo counted, the skip dialog, no metric anywhere", async ({ page, recorder }) => {
    test.setTimeout(180_000);
    await enterStudy(page);
    await checkBrowser(page);
    await openKit(page, DEMO_KIT);
    const identity = page.locator("[data-part='identity']");
    await expect(identity.locator("[data-part='id-study']")).toHaveText(DEMO.study_id);
    await expect(identity.locator("[data-part='id-annotator']")).toHaveText(DEMO.annotator_id);
    await expect(identity.locator("[data-part='id-session']")).toHaveText(DEMO.session_id);
    await expect(identity.locator("[data-part='id-build']")).toHaveText(/^\d+\.\d+\.\d+/);
    await assertNoMetric(page);
    await confirmIdentity(page);
    await expect(page.locator(".rail")).toHaveCount(0);
    await expect(page.locator("[data-part='ticks'] li")).toHaveCount(ITEMS.length);

    const next = page.locator("[data-part='study-dock'] [data-action='next']");
    const shape = item(0);
    const clicks = shapeClicks(shape);
    await page.keyboard.press("Backspace");
    await expect(page.locator("[data-part='notice']")).toHaveText(t.notices.nothing_to_undo);
    for (const [index, [x, y]] of clicks.slice(0, 3).entries()) {
      await expect(next).toHaveAttribute("aria-disabled", "true");
      await expect(page.locator("[data-part='click-count']")).toHaveText(fill(t.dock.click, { number: index + 1, total: 6 }));
      await clickDisplay(page, x, y);
    }
    await pressAction(page, "undo");
    await pressAction(page, "undo");
    await expect(page.locator(`${STUDY_OVERLAY} .ov__dot`)).toHaveCount(1);
    for (const [x, y] of clicks.slice(1, 5)) {
      await expect(next).toHaveAttribute("aria-disabled", "true");
      await clickDisplay(page, x, y);
    }
    await expect(next).toHaveAttribute("aria-disabled", "true");
    await assertNoMetric(page);
    const [lx, ly] = clicks[5] as [number, number];
    await clickDisplay(page, lx, ly);
    await expect(next).not.toHaveAttribute("aria-disabled");
    await expect(page.locator("[data-part='chips'] li[data-state='done']")).toHaveCount(6);
    await assertNoMetric(page);
    await pressAction(page, "next");
    await expect(page.locator("[data-part='item-count']")).toHaveText(fill(t.header.item, { index: 2, total: ITEMS.length }));
    await page.keyboard.press("Backspace");
    await expect(page.locator("[data-part='notice']")).toHaveText(t.notices.nothing_to_undo);
    await expect(page.locator("[data-part='item-count']")).toHaveText(fill(t.header.item, { index: 2, total: ITEMS.length }));

    await expect(page.locator("[data-part='study-dock'] [data-action='branch']")).toBeVisible();
    await expect(page.locator("[data-part='study-dock'] [data-action='m-line']")).toBeHidden();
    await clickDisplay(page, 120, 150);
    await assertNoMetric(page);
    await page.locator("[data-part='study-dock'] [data-action='skip']").click();
    const dialog = page.locator("[data-part='skip-dialog']");
    await expect(dialog).toBeVisible();
    await dialog.locator("[data-action='skip-confirm']").click();
    await expect(dialog.locator("[data-part='skip-error']")).toHaveText(t.skip.needReason);
    await dialog.locator("input[value='other']").check();
    await dialog.locator("[data-part='skip-other']").fill("   ");
    await dialog.locator("[data-action='skip-confirm']").click();
    await expect(dialog.locator("[data-part='skip-error']")).toHaveText(t.skip.needReason);
    expect((await recorder.calls("app")).filter((call) => call.cmd === "study.skip_item")).toHaveLength(0);
    await dialog.locator("[data-part='skip-other']").fill("Torn section");
    const skipBefore = await seq(page);
    await dialog.locator("[data-action='skip-confirm']").click();
    await expect.poll(() => seq(page)).toBeGreaterThan(skipBefore);
    await expect(dialog).toBeHidden();
    await expect(page.locator("[data-part='ticks'] li[data-state='skipped']")).toHaveCount(1);

    await expect(page.locator("[data-part='study-dock'] [data-action='next-myofibril']")).toBeVisible();
    await completeItem(page, item(2));
    await assertNoMetric(page);

    const last = item(3);
    const lastClicks = shapeClicks(last);
    await clickDisplay(page, ...(lastClicks[0] as [number, number]));
    await clickDisplay(page, ...(lastClicks[1] as [number, number]));
    await pressAction(page, "m-line");
    await expect(page.locator("[data-part='click-count']")).toHaveText(fill(t.dock.click, { number: 3, total: 4 }));
    await expect(page.locator("[data-part='chips'] li[data-state='skipped']")).toHaveCount(2);
    await clickDisplay(page, ...(lastClicks[4] as [number, number]));
    await assertNoMetric(page);
    await clickDisplay(page, ...(lastClicks[5] as [number, number]));
    const file = await download(page, () => page.locator("[data-part='study-dock'] [data-action='next']").click());
    await expect(page.locator("[data-part='finished']")).toBeVisible();
    await assertNoMetric(page);
    expect(file.name).toBe(`${DEMO.study_id}_${DEMO.annotator_id}_${DEMO.session_id}.json`);
    const session = JSON.parse(file.bytes.toString("utf8")) as SessionFile;
    const [first, chain, pair, mSkipped] = session.items as [SessionFile["items"][number], SessionFile["items"][number], SessionFile["items"][number], SessionFile["items"][number]];
    expect(first.raw_clicks.map((click) => click.undo_count)).toEqual([0, 2, 2, 2, 2, 2]);
    expect(Object.keys(first.points).sort()).toEqual(["m", "z1", "z2"]);
    expect(chain.skipped).toBe(true);
    expect(chain.skip_reason).toBe(`${t.skip.other}: Torn section`);
    expect(pair.type).toBe("pair");
    expect((pair.points.myofibrils as unknown[]).length).toBe(2);
    expect(mSkipped.skipped_parts).toEqual(["m_line"]);
    expect(Object.keys(mSkipped.points).sort()).toEqual(["z1", "z2"]);
    expect(mSkipped.raw_clicks).toHaveLength(4);
    const commands = new Set((await recorder.calls("app")).map((call) => call.cmd));
    expect([...commands].filter((cmd) => cmd !== "sys.identity" && !cmd.startsWith("study."))).toEqual([]);
  });

  test("Pause hides the item and stops; a hidden tab pauses; the fatal screen says Reload to resume", async ({ page, recorder }) => {
    test.setTimeout(120_000);
    await enterStudy(page);
    await checkBrowser(page);
    await openKit(page, DEMO_KIT);
    await confirmIdentity(page);
    await clickDisplay(page, 150, 150);
    const before = await seq(page);
    await page.locator(".study__pause").click();
    await expect(page.locator("[data-part='paused']")).toBeVisible();
    await expect.poll(() => seq(page)).toBeGreaterThan(before);
    await expect(page.locator(STUDY_CANVAS)).toHaveCount(0);
    await page.locator("[data-part='paused'] [data-action='resume']").click();
    await expect(page.locator(STUDY_CANVAS)).toBeVisible();
    await expect(page.locator(`${STUDY_OVERLAY} .ov__dot`)).toHaveCount(1);
    await page.evaluate(() => {
      Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await expect(page.locator("[data-part='paused']")).toBeVisible();
    await expect(page.locator("[data-part='paused-hidden']")).toHaveText(t.paused.hidden);
    const pauses = (await recorder.calls("app")).filter((call) => call.cmd === "study.pause");
    expect(pauses).toHaveLength(2);
    await page.evaluate(() => {
      Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
    });
    await page.locator("[data-part='paused'] [data-action='resume']").click();
    await expect(page.locator(STUDY_CANVAS)).toBeVisible();
    await page.evaluate(() => (globalThis as unknown as { __sarcostatTestRecorder: { terminate(): void } }).__sarcostatTestRecorder.terminate());
    await clickDisplay(page, 160, 160).catch(() => undefined);
    const fatal = page.locator(".fatal[role='alertdialog']");
    await expect(fatal).toBeVisible({ timeout: 15_000 });
    await expect(fatal.locator("[data-part='detail']")).toContainText(t.fatal);
  });
});

async function assertNoMetric(page: Page): Promise<void> {
  const codes = [DEMO.study_id, DEMO.annotator_id, DEMO.session_id];
  const offending = await page.evaluate((ids) => {
    const allowed = ["[data-part='item-count']", "[data-part='click-count']", "[data-part^='id-']", "[data-part='resumed']", ".statusline"];
    const out: string[] = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node !== null; node = walker.nextNode()) {
      const text = node.textContent ?? "";
      const parent = node.parentElement;
      if (!/\d/.test(text) || parent === null || !parent.checkVisibility({ visibilityProperty: true })) {
        continue;
      }
      if (allowed.some((selector) => parent.closest(selector) !== null)) {
        continue;
      }
      if (parent.closest("[data-part='identity'], [data-part='finished'], [data-part='locked']") !== null && !/\d/.test(ids.reduce((rest, id) => rest.split(id).join(""), text))) {
        continue;
      }
      const dock = parent.closest("[data-part='instruction']");
      const type = document.querySelector("[data-part='study-dock']")?.getAttribute("data-item-type");
      if (dock !== null && (type === "chain" || type === "pair") && /^(First myofibril, |Second myofibril, )?Z-line \d+, (first|second) end$/.test(text)) {
        continue;
      }
      out.push(text);
    }
    return out;
  }, codes);
  expect(offending, "visible digits outside the item counter, click counter, Z-line ordinals, identity codes and footer identity").toEqual([]);
}
