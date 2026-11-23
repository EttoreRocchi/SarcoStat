import { budgetNote, timeBudget } from "./budgets.ts";
import fs from "node:fs";
import path from "node:path";

import type { Download, Page } from "@playwright/test";

import { en } from "../../src/strings/en.ts";
import { fill } from "../../src/strings/format.ts";
import { compareJson, parseRule, type FieldStats } from "./hooks/parity_rule.ts";
import { expect, test, type Recorder } from "./fixtures.ts";
import {
  boot,
  canonicalJson,
  clickPixel,
  cpython,
  GOLDEN_DIR,
  goldenText,
  ITEMS_PANEL,
  jsonPayload,
  openGoldenImage,
  openImage,
  openPayload,
  pendingDots,
  placeSarcomere,
  PLOTS_PANEL,
  RESULTS_PANEL,
  selectTab,
  WORKSPACE,
} from "./workspace_view.ts";

const GREY = "grey_64x48.png";
const SARCOMERE: readonly (readonly [number, number])[] = [
  [10, 5],
  [10, 42],
  [30, 6],
  [30, 41],
  [50, 5],
  [50, 42],
];
const SECOND: readonly (readonly [number, number])[] = [
  [8, 8],
  [12, 40],
  [28, 14],
  [30, 34],
  [52, 6],
  [50, 44],
];
const RULE = parseRule(fs.readFileSync(path.join(GOLDEN_DIR, "tolerance.json"), "utf8"));

type ResultsView = { sentences: string[]; tables: Record<string, { columns: string[]; rows: string[][]; notes: string[] }>; plot_data: { shape: { sarcomere_id: number; stability: number | null }[] } };

async function lastResult<T>(recorder: Recorder, cmd: string): Promise<T> {
  const calls = (await recorder.calls("app")).filter((call) => call.cmd === cmd && call.ok === true);
  const last = calls[calls.length - 1];
  if (last === undefined) {
    throw new Error(`the app sent no successful ${cmd}`);
  }
  return last.result as T;
}

async function appCommands(recorder: Recorder): Promise<string[]> {
  return (await recorder.calls("app")).map((call) => call.cmd);
}

function collectDownloads(page: Page): Download[] {
  const seen: Download[] = [];
  page.on("download", (download) => seen.push(download));
  return seen;
}

async function downloaded(download: Download): Promise<string> {
  return fs.readFileSync(await download.path(), "utf8");
}

async function exportVia(page: Page, downloads: Download[], id: string, count: number): Promise<Download[]> {
  const before = downloads.length;
  await page.keyboard.press("e");
  const menu = page.getByRole("dialog", { name: en.exportMenu.label });
  await expect(menu).toBeVisible();
  const items = menu.getByRole("menuitem");
  const target = items.and(page.locator(`[data-export='${id}']`));
  await expect(target).not.toHaveAttribute("aria-disabled", "true");
  await target.click();
  await expect.poll(() => downloads.length).toBe(before + count);
  return downloads.slice(before);
}

async function setScale(page: Page, size: string, unit: string): Promise<void> {
  await page.locator(".topbar [data-action='scale']").click();
  const dialog = page.getByRole("dialog", { name: en.dialogs.scale.title });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel(en.dialogs.scale.size).fill(size);
  await dialog.getByLabel(en.dialogs.scale.unit).selectOption(unit);
  await page.keyboard.press("Enter");
  await expect(dialog).toBeHidden();
}

async function setClickError(page: Page, sigma: string): Promise<void> {
  await selectTab(page, "results");
  await page.locator(`${RESULTS_PANEL} [data-action='set-click-error']`).click();
  const dialog = page.getByRole("dialog", { name: en.dialogs.clickError.title });
  await expect(dialog).toBeVisible();
  await dialog.locator("input").fill(sigma);
  await page.keyboard.press("Enter");
  await expect(dialog).toBeHidden();
}

function scipyRequests(page: Page): string[] {
  const seen: string[] = [];
  page.on("request", (request) => {
    if (/scipy/i.test(request.url())) {
      seen.push(request.url());
    }
  });
  return seen;
}

function sidecarProblems(text: string, outputs: readonly string[], image: { file_name: string; sha256: string }, build: { version: string; source_digest: string }): string[] {
  const problems: string[] = [];
  const doc = JSON.parse(text) as { outputs?: string[]; provenance?: { files?: { file_name: string; sha256: string }[]; parameters?: Record<string, unknown>; sarcostat_version?: string; source_digest?: string } };
  if (JSON.stringify(Object.keys(doc).sort()) !== JSON.stringify(["outputs", "provenance"])) {
    problems.push(`sidecar keys ${Object.keys(doc).join(", ")}`);
  }
  if (JSON.stringify(doc.outputs) !== JSON.stringify(outputs)) {
    problems.push(`outputs ${JSON.stringify(doc.outputs)}, expected ${JSON.stringify(outputs)}`);
  }
  const p = doc.provenance;
  if (p?.sarcostat_version !== build.version || p?.source_digest !== build.source_digest) {
    problems.push(`identity ${p?.sarcostat_version} ${p?.source_digest}`);
  }
  if (!(p?.files ?? []).some((file) => file.file_name === image.file_name && file.sha256 === image.sha256)) {
    problems.push(`files ${JSON.stringify(p?.files)} lack ${image.file_name} ${image.sha256}`);
  }
  if (p?.parameters === undefined || Object.keys(p.parameters).length === 0) {
    problems.push("no parameters");
  }
  const strings: string[] = [];
  const walk = (value: unknown): void => {
    if (typeof value === "string") {
      strings.push(value);
    } else if (value !== null && typeof value === "object") {
      Object.values(value as Record<string, unknown>).forEach(walk);
    }
  };
  walk(doc);
  for (const value of strings) {
    if (/^\//.test(value) || /^[A-Za-z]:[\\/]/.test(value) || /[\\/](home|Users|tmp)[\\/]/.test(value)) {
      problems.push(`absolute path ${value}`);
    }
  }
  return problems;
}

const ROUND_TRIP = [
  "import json, sys",
  "from sarcostat.io.annotations import dumps_annotations, read_annotations, validate_annotations",
  "path = sys.argv[1]",
  "text = open(path, encoding='utf-8').read()",
  "validate_annotations(json.loads(text))",
  "print(json.dumps({'identical': dumps_annotations(read_annotations(path)) == text}))",
].join("\n");

const ANALYZE = ["import sys", "from sarcostat.cli.app import app", "sys.argv = ['sarcostat', 'analyze', sys.argv[1], sys.argv[2]]", "app()"].join("\n");

function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const c = text[i] as string;
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') {
        field += '"';
        i += 1;
      } else if (c === '"') {
        quoted = false;
      } else {
        field += c;
      }
    } else if (c === '"') {
      quoted = true;
    } else if (c === ",") {
      row.push(field);
      field = "";
    } else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") {
        i += 1;
      }
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else {
      field += c;
    }
  }
  if (field !== "" || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  return rows;
}

function csvAsJson(text: string): unknown[] {
  return parseCsv(text).map((row) => row.map((cell) => (cell !== "" && Number.isFinite(Number(cell)) && !/^0\d/.test(cell) ? Number(cell) : cell)));
}

async function bootShape(page: Page): Promise<void> {
  await boot(page);
}

test.describe("shape mode (M4-1)", () => {
  test("six-click flow: Click 3 of 6 with the landmark, chips A-F and the hint; Backspace undoes only pending clicks", async ({ page, recorder }) => {
    await bootShape(page);
    await openImage(page, GREY);
    const hud = page.locator("[data-part='hud']");
    await expect(hud.locator(".hud__count")).toHaveText(fill(en.hud.count, { sarcomere: 1, number: 1, of: 6 }));
    await expect(hud.locator("[data-part='hud-hint']")).toHaveText(en.hud.hint.A);
    const landmarks = ["A", "B", "C", "D", "E", "F"] as const;
    for (let k = 0; k < 6; k += 1) {
      const [x, y] = SARCOMERE[k] as readonly [number, number];
      await expect(hud.locator(".hud__what")).toHaveText(en.landmarks[landmarks[k] as (typeof landmarks)[number]]);
      await expect(hud.locator("[data-part='hud-hint']")).toHaveText(en.hud.hint[landmarks[k] as (typeof landmarks)[number]]);
      await expect(hud.locator(".lm-chip--done")).toHaveCount(k);
      await expect(hud.locator(".lm-chip[data-state='now']")).toHaveText(landmarks[k] as string);
      if (k === 2) {
        await expect(hud.locator(".hud__count")).toHaveText(fill(en.hud.count, { sarcomere: 1, number: 3, of: 6 }));
        await expect(hud.locator(".hud__what")).toHaveText("M-line, first end");
      }
      await clickPixel(page, x, y);
      await expect(pendingDots(page)).toHaveCount(k + 1);
    }
    await expect(hud.locator("[data-part='hud-hint']")).toHaveText(en.hud.hint.ready);
    await page.keyboard.press("Backspace");
    await expect(pendingDots(page)).toHaveCount(5);
    await clickPixel(page, 50, 42);
    await page.keyboard.press("Enter");
    await expect(page.locator(`${ITEMS_PANEL} .items__row`)).toHaveCount(1);
    await expect(pendingDots(page)).toHaveCount(0);
    await recorder.clear();
    await page.keyboard.press("Backspace");
    await expect(page.locator(".toast")).toHaveText(en.hud.noPendingClick);
    await page.waitForTimeout(300);
    expect(await appCommands(recorder)).not.toContain("shape.undo");
    await expect(page.locator(`${ITEMS_PANEL} .items__row`)).toHaveCount(1);
    await expect(page.locator(".tabs__tab[data-tab='items'] .count")).toHaveText("1");
  });

  test("Results, Plots and Sarcomeres show results.get strings with units, the stability ring and the SI plot; scale, click error, estimate and delete work", async ({ page, recorder }) => {
    const scipy = scipyRequests(page);
    await bootShape(page);
    await openImage(page, GREY);
    await expect(page.locator(`${RESULTS_PANEL} .empty__text`)).toHaveText(en.panel.noResults);
    await placeSarcomere(page, SARCOMERE);
    await placeSarcomere(page, SECOND);
    await page.locator(".topbar [data-action='scale']").click();
    const scaleDialog = page.getByRole("dialog", { name: en.dialogs.scale.title });
    await scaleDialog.getByLabel(en.dialogs.scale.size).fill("abc");
    await page.keyboard.press("Enter");
    await expect(scaleDialog.getByRole("alert")).toHaveText(en.dialogs.scale.invalid);
    await page.keyboard.press("Escape");
    await expect(scaleDialog).toBeHidden();
    await setScale(page, "-1", "nm");
    await expect(page.locator(".toast")).toHaveText(en.notices.pixel_size_invalid);
    await expect(page.locator(".topbar [data-action='scale']")).toHaveText(en.topbar.scaleUnset);
    await setScale(page, "6.62", "nm");
    await expect(page.locator(".topbar [data-action='scale']")).toHaveText("6.62 nm/px");
    await setClickError(page, "22");
    const ring = page.locator(`${RESULTS_PANEL} [data-part='stability-ring']`);
    await expect(ring).toBeVisible();
    const view = await lastResult<ResultsView>(recorder, "results.get");
    const table = view.tables.sarcomeres;
    expect(table).toBeDefined();
    const row = (table?.rows[1] ?? []) as string[];
    const column = (name: string): number => table?.columns.indexOf(name) ?? -1;
    await expect(page.locator(`${RESULTS_PANEL} [data-part='results-title']`)).toContainText(fill(en.panel.item, { n: 2 }));
    await expect(page.locator(`${RESULTS_PANEL} .readout`)).toHaveText(row[column("SI")] as string);
    expect(row[column("SI")]).toMatch(/ ± /);
    for (const name of ["AsR", "Cur", "Class", "Stability", "Sarcomere length", "Z-line 1", "Z-line 2", "M-line"]) {
      await expect(page.locator(`${RESULTS_PANEL} [data-ref='tables.sarcomeres.rows.1.${column(name)}']`).first(), name).toHaveText(row[column(name)] as string);
    }
    for (const name of ["Sarcomere length", "Z-line 1", "Z-line 2", "M-line"]) {
      expect(row[column(name)], name).toMatch(/ nm$/);
    }
    await expect(ring.locator(".ring__arc")).toHaveAttribute("data-share", String(view.plot_data.shape[1]?.stability));
    for (const [n, note] of (table?.notes ?? []).entries()) {
      await expect(page.locator(`${RESULTS_PANEL} [data-ref='tables.sarcomeres.notes.${n}']`)).toHaveText(note);
    }
    await expect(page.locator(`${RESULTS_PANEL} [data-ref='sentences.0']`)).toHaveText(view.sentences[0] as string);
    await expect(page.locator(`${RESULTS_PANEL} [data-ref='sentences.1']`)).toHaveText(view.sentences[1] as string);
    expect(view.sentences[1]).toContain("σ = 22.00 nm");
    await page.keyboard.press("[");
    await expect(page.locator(`${RESULTS_PANEL} [data-part='results-title']`)).toContainText(fill(en.panel.item, { n: 1 }));
    await expect(page.locator(`${RESULTS_PANEL} .readout`)).toHaveText(table?.rows[0]?.[column("SI")] as string);
    await page.keyboard.press("]");
    await expect(page.locator(`${RESULTS_PANEL} [data-part='results-title']`)).toContainText(fill(en.panel.item, { n: 2 }));

    await selectTab(page, "plots");
    const plot = page.locator(`${PLOTS_PANEL} [data-part='si-plot']`);
    await expect(plot).toBeVisible();
    await expect(plot.locator(".si-plot__row")).toHaveCount(2);
    await expect(plot.locator(".si-plot__u1")).toHaveCount(2);
    await expect(page.locator(`${PLOTS_PANEL} .plot__caption`)).toHaveText(en.plots.caption);
    const ticks = await plot.locator(".si-plot__tick-label").allTextContents();
    expect(ticks.length).toBeGreaterThanOrEqual(2);
    const decimals = new Set(ticks.map((tick) => (tick.split(".")[1] ?? "").length));
    expect(decimals.size, `ticks ${ticks.join(" ")}`).toBe(1);

    await selectTab(page, "items");
    const rows = page.locator(`${ITEMS_PANEL} .items__row`);
    await expect(rows).toHaveCount(2);
    await expect(rows.nth(0).locator(".items__value")).toHaveText(table?.rows[0]?.[column("SI")] as string);
    await recorder.clear();
    await rows.nth(0).locator("[data-action='delete']").click();
    const confirm = page.getByRole("alertdialog", { name: fill(en.dialogs.remove.title, { n: 1 }) });
    await expect(confirm).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(confirm).toBeHidden();
    await expect(rows).toHaveCount(2);
    await rows.nth(0).locator("[data-action='delete']").click();
    await page.keyboard.press("Enter");
    await expect(rows).toHaveCount(1);
    expect(await appCommands(recorder)).toEqual(["shape.delete", "results.get"]);
    await expect(rows.nth(0)).toHaveAttribute("data-sarcomere", "1");

    await selectTab(page, "results");
    await page.locator(`${RESULTS_PANEL} [data-action='estimate-click-error']`).click();
    await expect(page.locator(".toast")).toHaveText(fill(en.notices.click_error_too_few_repeats, { n_repeats: 1 }));
    expect(scipy, "a shape-only session never requests scipy").toEqual([]);
  });

  test("the click error is estimated from repeats of one sarcomere", async ({ page, recorder }) => {
    await bootShape(page);
    await openImage(page, GREY);
    const jitter = [0, 1, -1];
    for (const d of jitter) {
      await placeSarcomere(
        page,
        SARCOMERE.map(([x, y], k) => [x + (k % 2 === 0 ? d : 0), y + (k % 3 === 0 ? d : 0)] as const),
      );
    }
    await page.locator(`${RESULTS_PANEL} [data-action='estimate-click-error']`).click();
    await expect(page.locator(".toast")).toHaveText(fill(en.notices.click_error_estimated, { n_repeats: 3 }));
    await expect(page.locator(`${RESULTS_PANEL} [data-part='click-error'] [data-ref='sentences.1']`)).toHaveText(/^Click error: σ = /);
    const view = await lastResult<ResultsView>(recorder, "results.get");
    expect(view.sentences[1]).toMatch(/^Click error: σ = /);
    await expect(page.locator(`${RESULTS_PANEL} [data-part='click-error'] [data-ref='sentences.1']`)).toHaveText(view.sentences[1] as string);
  });

  test("an image is decoded before doc.new: a failed decode leaves the session and the canvas as they were", async ({ page, recorder }) => {
    await bootShape(page);
    await openImage(page, GREY);
    await placeSarcomere(page, SARCOMERE);
    await recorder.clear();
    const png = fs.readFileSync(path.resolve(GOLDEN_DIR, "..", "..", "..", "web", "tests", "fixtures", "images", "png_grey8_37x29.png"));
    const broken = Buffer.concat([png.subarray(0, 33), Buffer.alloc(200, 7)]);
    await openPayload(page, { name: "broken.png", mimeType: "image/png", buffer: broken });
    await expect(page.locator(".toast")).toHaveText(fill(en.workspace.decodeFailed, { file: "broken.png" }));
    expect(await appCommands(recorder)).toEqual([]);
    await expect(page.locator(".topbar__name")).toHaveText(GREY);
    await expect(page.locator(WORKSPACE)).toHaveAttribute("data-state", "drawn");
    await expect(page.locator(`${ITEMS_PANEL} .items__row`)).toHaveCount(1);
  });

  test("app-saved v1 validates and survives a CPython read/dump byte for byte; results JSON and CSV equal the engine's bytes and CPython's analyze within the rule; sidecars are complete", async ({ page, recorder, engine, baseURL }, testInfo) => {
    const downloads = collectDownloads(page);
    await bootShape(page);
    await openImage(page, GREY);
    await placeSarcomere(page, SARCOMERE);
    await placeSarcomere(page, SECOND);
    await setScale(page, "6.62", "nm");
    await setClickError(page, "22");
    const build = (await (await page.request.get(new URL("BUILD.json", baseURL).href)).json()) as { version: string; source_digest: string };
    const image = (await lastResult<{ image: { file_name: string; sha256: string } }>(recorder, "doc.new")).image;

    const [annotation] = await exportVia(page, downloads, "v1", 1);
    const v1Text = await downloaded(annotation as Download);
    const v1File = testInfo.outputPath((annotation as Download).suggestedFilename());
    fs.writeFileSync(v1File, v1Text);
    expect(JSON.parse(cpython(ROUND_TRIP, [v1File]))).toEqual({ identical: true });
    await expect(page.locator("[data-part='unsaved']")).toHaveText(en.topbar.allExported);

    const problems: string[] = [];
    const fields = new Map<string, FieldStats>();
    for (const [what, flag] of [
      ["results_json", "--json"],
      ["results_csv", "--csv"],
    ] as const) {
      const [file, sidecar] = await exportVia(page, downloads, what, 2);
      const text = await downloaded(file as Download);
      const engineText = (await engine.call<{ text: string }>("doc.export", { what })).text;
      expect(text, `${what}: the download equals the Pyodide export byte for byte`).toBe(engineText);
      const cpythonText = cpython(ANALYZE, [flag, v1File]);
      if (what === "results_json") {
        compareJson(JSON.parse(cpythonText), JSON.parse(text), RULE, fields, problems, what);
        expect(JSON.parse(text)).toMatchObject({ images: [{ settings: { unit: "nm", pixel_size: 6.62 } }] });
      } else {
        compareJson(csvAsJson(cpythonText.replace(/\n$/, "")), csvAsJson(text.replace(/\r?\n$/, "")), RULE, fields, problems, what);
      }
      const sidecarText = await downloaded(sidecar as Download);
      const stem = GREY.replace(/\.png$/, "");
      expect((sidecar as Download).suggestedFilename()).toBe(`${stem}_provenance.json`);
      expect(sidecarProblems(sidecarText, [`${stem}_results.json`, `${stem}_results.csv`], image, build), what).toEqual([]);
    }
    expect(problems).toEqual([]);
    console.log(`app-saved session: results JSON and CSV within the rule of CPython analyze (${[...fields.values()].reduce((n, s) => n + s.n, 0)} numbers)`);
  });

  test("Open of a v1 file: SHA-256 mismatch asks first; without an image the pixels are absent until the matching image is opened", async ({ page, recorder }) => {
    await bootShape(page);
    await openGoldenImage(page, "human_skeletal_muscle_tem_1.jpg");
    const other = JSON.parse(goldenText("shape_click_set")) as { images: { sha256: string }[] };
    (other.images[0] as { sha256: string }).sha256 = "0".repeat(64);
    await openPayload(page, jsonPayload("other_image.json", `${JSON.stringify(other, null, 2)}\n`));
    const ask = page.getByRole("alertdialog", { name: en.dialogs.load.title });
    await expect(ask).toBeVisible();
    await expect(ask.locator(".dialog__text")).toContainText("SHA-256 differs");
    await page.keyboard.press("Escape");
    await expect(ask).toBeHidden();
    await expect(page.locator(`${ITEMS_PANEL} .items__row`)).toHaveCount(0);
    await openPayload(page, jsonPayload("other_image.json", `${JSON.stringify(other, null, 2)}\n`));
    await expect(ask).toBeVisible();
    await page.keyboard.press("Enter");
    await expect(ask).toBeHidden();
    await expect(page.locator(`${ITEMS_PANEL} .items__row`)).toHaveCount(4);
    const loads = (await recorder.calls("app")).filter((call) => call.cmd === "doc.load_v1").map((call) => (call.args as { confirm: boolean }).confirm);
    expect(loads).toEqual([false, false, true]);

    await page.goto("./#/shape");
    await page.reload();
    await expect(page.locator(".statusline[data-state='ready']")).toBeVisible({ timeout: 30_000 });
    await openPayload(page, jsonPayload("shape_click_set.json", goldenText("shape_click_set")));
    await expect(page.locator(`${ITEMS_PANEL} .items__row`)).toHaveCount(4);
    const empty = page.locator("[data-part='empty']");
    await expect(empty.locator(".stage__title")).toHaveText(fill(en.workspace.pixelsTitle, { file: "human_skeletal_muscle_tem_1.jpg" }));
    await expect(page.locator(".toast")).toContainText(en.open.unchecked);
    await expect(page.locator("[data-part='hud']")).toBeHidden();
    await recorder.clear();
    await openGoldenImage(page, "human_skeletal_muscle_tem_1.jpg");
    await expect(page.locator(".toast")).toHaveText(fill(en.open.attached, { file: "human_skeletal_muscle_tem_1.jpg" }));
    expect(await appCommands(recorder)).toEqual([]);
    await expect(empty).toBeHidden();
    await expect(page.locator(`[data-part='overlay'] .ov__item`)).toHaveCount(4);

    const two = JSON.parse(goldenText("shape_click_set")) as { images: Record<string, unknown>[] };
    two.images.push({ ...(two.images[0] as Record<string, unknown>), file_name: "b.jpg", image_id: "b", sha256: "1".repeat(64) });
    await openPayload(page, jsonPayload("two_images.json", JSON.stringify(two)));
    await expect(page.locator(".toast")).toContainText("sarcostat analyze");
    await expect(page.locator(`${ITEMS_PANEL} .items__row`)).toHaveCount(4);
  });

  test("absent modes: a file with shape, chain and pair items opens in Shape with one line; Export v1 writes the chain and pair items back byte for byte; scipy only then", async ({ page, recorder }) => {
    const scipy = scipyRequests(page);
    const downloads = collectDownloads(page);
    await bootShape(page);
    await openGoldenImage(page, "human_skeletal_muscle_tem_1.jpg");
    const input = goldenText("mixed_types");
    await openPayload(page, jsonPayload("mixed_types.json", input));
    const line = fill(en.absent.itemsOther, {
      modes: fill(en.absent.pair, { first: en.modes.straightness.short, second: en.modes.register.short }),
      kinds: fill(en.absent.pair, { first: en.absent.kinds.chain, second: en.absent.kinds.pair }),
    });
    await expect(page.locator(".panel [data-part='absent-modes']")).toHaveText(line);
    expect(line).toContain("Straightness and Register");
    await expect.poll(() => new URL(page.url()).hash).toBe("#/shape");
    await expect(page.locator(`${ITEMS_PANEL} .items__row--kept`)).toHaveCount((JSON.parse(input) as { items: { type: string }[] }).items.filter((item) => item.type !== "shape").length);
    await expect(page.locator(`${RESULTS_PANEL} [data-part='results-title']`)).toBeVisible();
    const all = await appCommands(recorder);
    const commands = all.slice(all.indexOf("doc.load_v1"));
    expect(commands.slice(0, 3)).toEqual(["doc.load_v1", "sys.load_scipy", "results.get"]);
    expect(all.filter((cmd) => cmd === "sys.load_scipy")).toHaveLength(1);
    expect(scipy.length, "scipy is requested for a document with chain and pair items").toBeGreaterThan(0);

    const [unchanged] = await exportVia(page, downloads, "v1", 1);
    expect(await downloaded(unchanged as Download)).toBe(input);
    await selectTab(page, "items");
    await page.locator(`${ITEMS_PANEL} .items__row:not(.items__row--kept)`).first().locator("[data-action='delete']").click();
    await page.keyboard.press("Enter");
    const [changed] = await exportVia(page, downloads, "v1", 1);
    const output = JSON.parse(await downloaded(changed as Download)) as { items: { type: string; item_id: string }[] };
    const kept = (doc: { items: { type: string }[] }): string[] => doc.items.filter((item) => item.type === "chain" || item.type === "pair").map(canonicalJson);
    expect(kept(output).length).toBeGreaterThan(0);
    expect(kept(output)).toEqual(kept(JSON.parse(input) as { items: { type: string }[] }));
  });

  test("absent modes: a chain-only file opens Home with the same line", async ({ page }) => {
    await bootShape(page);
    await openPayload(page, jsonPayload("chain_only_notice.json", goldenText("chain_only_notice")));
    await expect.poll(() => new URL(page.url()).hash).toBe("#/");
    await expect(page.locator(".home [data-part='absent-modes']")).toHaveText(fill(en.absent.itemsOne, { mode: en.modes.straightness.short, kind: en.absent.kinds.chain }));
  });

  test("which commands import scipy for the mixed golden in Pyodide (diagnostic, logged)", async ({ page, engine }, testInfo) => {
    await bootShape(page);
    await engine.ready();
    const text = goldenText("mixed_types");
    const first = (JSON.parse(text) as { images: Record<string, unknown>[] }).images[0] as Record<string, unknown>;
    const image = Object.fromEntries(["file_name", "sha256", "width_px", "height_px"].map((key) => [key, first[key]]));
    const outcome: Record<string, string> = {};
    const attempt = async (label: string, cmd: string, args: Record<string, unknown>): Promise<void> => {
      const response = await engine.send(cmd, args, 120_000);
      outcome[label] = response.ok ? "ok" : `${response.error?.code}: ${String(response.error?.message).split("\n")[0]}`;
    };
    await attempt("doc.load_v1", "doc.load_v1", { text, image, confirm: false });
    await attempt("results.get", "results.get", {});
    for (const what of ["v1", "results_json", "results_csv", "provenance"]) {
      await attempt(`doc.export ${what}`, "doc.export", { what });
    }
    await attempt("sys.load_scipy", "sys.load_scipy", {});
    await attempt("results.get after scipy", "results.get", {});
    await attempt("doc.export results_json after scipy", "doc.export", { what: "results_json" });
    const report = `${JSON.stringify(outcome, null, 2)}\n`;
    fs.writeFileSync(testInfo.outputPath("scipy_commands.json"), report);
    console.log(`scipy needs on the mixed golden: ${report}`);
    expect(outcome["results.get after scipy"]).toBe("ok");
    expect(outcome["doc.export results_json after scipy"]).toBe("ok");
    expect(outcome["doc.export v1"]).toBe("ok");
  });
});

const CLICK_PROBE = (): void => {
  const state = { latencies: [] as number[], fromDown: [] as number[], down: 0 };
  (globalThis as unknown as { __sarcostatTestClicks: typeof state }).__sarcostatTestClicks = state;
  const count = (): number => document.querySelectorAll("[data-part='overlay'] .ov__item--pending .ov__dot").length;
  window.addEventListener("pointerdown", () => {
    state.down = performance.now();
  }, true);
  window.addEventListener(
    "pointerup",
    (event) => {
      if (!(event.target instanceof HTMLCanvasElement) || event.target.dataset.part !== "image-canvas") {
        return;
      }
      const up = performance.now();
      const down = state.down;
      const before = count();
      const deadline = up + 2000;
      const poll = (): void => {
        if (count() !== before) {
          requestAnimationFrame(() => {
            const now = performance.now();
            state.latencies.push(now - up);
            state.fromDown.push(now - down);
          });
          return;
        }
        if (performance.now() < deadline) {
          requestAnimationFrame(poll);
        }
      };
      requestAnimationFrame(poll);
    },
    true,
  );
};

function percentile(values: readonly number[], p: number): number {
  const sorted = [...values].sort((a, b) => a - b);
  const at = Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1));
  return sorted[at] ?? Number.NaN;
}

test("click to mark drawn: p95 at most 100 ms (plan 3.12)", async ({ page }, testInfo) => {
  test.setTimeout(120_000);
  await page.addInitScript(CLICK_PROBE);
  await boot(page);
  await openImage(page, GREY);
  for (let round = 0; round < 6; round += 1) {
    await placeSarcomere(
      page,
      SARCOMERE.map(([x, y]) => [x + (round % 3) - 1, y] as const),
    );
  }
  const measured = await page.evaluate(() => (globalThis as unknown as { __sarcostatTestClicks: { latencies: number[]; fromDown: number[] } }).__sarcostatTestClicks);
  expect(measured.latencies.length).toBe(36);
  const summary = {
    clicks: measured.latencies.length,
    from: "pointerup (the app sends the click on release) to the first animation frame after the new landmark is in the overlay",
    p50_ms: Number(percentile(measured.latencies, 50).toFixed(1)),
    p95_ms: Number(percentile(measured.latencies, 95).toFixed(1)),
    max_ms: Number(Math.max(...measured.latencies).toFixed(1)),
    p95_from_pointerdown_ms: Number(percentile(measured.fromDown, 95).toFixed(1)),
    latencies_ms: measured.latencies.map((value) => Number(value.toFixed(1))),
  };
  const text = `${JSON.stringify(summary, null, 2)}\n`;
  fs.writeFileSync(testInfo.outputPath("click_to_mark.json"), text);
  console.log(`click to mark: p50 ${summary.p50_ms} ms, p95 ${summary.p95_ms} ms, max ${summary.max_ms} ms over ${summary.clicks} clicks`);
  const note = budgetNote("shape click to mark p95", summary.p95_ms, 100);
  if (note !== null) {
    console.log(note);
  }
  expect(summary.p95_ms).toBeLessThanOrEqual(timeBudget(100));
});
