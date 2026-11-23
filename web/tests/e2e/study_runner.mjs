import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

import { chromium } from "@playwright/test";

import { startServer } from "../../scripts/serve.mjs";

const HOST_RESOLVER_RULES = "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1";
const LOCAL_HOST = "127.0.0.1";
const VIEWPORT = { width: 1600, height: 900 };
const VERSION_DIR = /^v\d[0-9A-Za-z.+-]*$/;
const SCRIPT_FORMAT = "sarcostat-study-click-script";
const LOG_FORMAT = "sarcostat-study-runner-log";
const BOOT_TIMEOUT_MS = 90_000;
const STEP_TIMEOUT_MS = 20_000;
const STABLE_TRIES = 30;
const WHEEL_DELTA = 60;
const READY = '.statusline[data-state="ready"]';
const START = "[data-part='study-start']";
const CANVAS = "[data-part='study-canvas']";
const SHOWN = `${CANVAS}[data-zoom]`;
const STAGE = "[data-part='study-stage']";
const DOCK = "[data-part='study-dock']";
const AUTOSAVE = "[data-part='autosave']";
const SKIP_DIALOG = "[data-part='skip-dialog']";
const DOCK_ACTIONS = { undo: "undo", "m-line": "m-line", "next-myofibril": "next-myofibril", next: "next" };
const KNOWN = new Set(["click", "undo", "m-line", "next-myofibril", "branch", "next", "skip", "zoom", "pause", "resume"]);
const USAGE = "Usage: PORT=0 node web/tests/e2e/study_runner.mjs --site <site root> --kit <kit folder> --script <click script JSON> --out <folder>";

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

export function isLocalUrl(raw) {
  if (raw.startsWith("data:") || raw.startsWith("blob:") || raw === "about:blank") {
    return true;
  }
  try {
    return new URL(raw).hostname === LOCAL_HOST;
  } catch {
    return false;
  }
}

export function findAppFolder(siteRoot, wanted) {
  const candidates = [];
  for (const prefix of ["", "SarcoStat"]) {
    const dir = path.join(siteRoot, prefix);
    if (!fs.existsSync(dir) || !fs.statSync(dir).isDirectory()) {
      continue;
    }
    for (const name of fs.readdirSync(dir)) {
      if (VERSION_DIR.test(name) && fs.existsSync(path.join(dir, name, "index.html"))) {
        candidates.push(prefix === "" ? name : `${prefix}/${name}`);
      }
    }
  }
  const chosen = wanted ? candidates.filter((rel) => rel === wanted || rel.endsWith(`/${wanted}`)) : candidates;
  if (chosen.length !== 1) {
    throw new Error(`expected one version folder v<version>/index.html under ${siteRoot} (or its SarcoStat/), found ${JSON.stringify(candidates)}; set SITE_VERSION to choose`);
  }
  return chosen[0];
}

export function readScript(file) {
  const script = JSON.parse(fs.readFileSync(file, "utf8"));
  if (script.format !== SCRIPT_FORMAT || script.format_version !== 1 || !Array.isArray(script.items)) {
    throw new Error(`${path.basename(file)} is not a ${SCRIPT_FORMAT} document of format version 1`);
  }
  for (const [index, item] of script.items.entries()) {
    if (typeof item.item_id !== "string" || !Array.isArray(item.actions) || item.actions.length === 0) {
      throw new Error(`script item ${index + 1} needs an item_id and at least one action`);
    }
    for (const action of item.actions) {
      if (!KNOWN.has(action.do)) {
        throw new Error(`script item ${item.item_id}: unknown action ${JSON.stringify(action.do)}`);
      }
      if (action.do === "click" && !(Number.isFinite(action.x) && Number.isFinite(action.y))) {
        throw new Error(`script item ${item.item_id}: a click needs finite display coordinates x and y`);
      }
      if (action.do === "zoom" && !(Number.isInteger(action.step) && action.step >= 0)) {
        throw new Error(`script item ${item.item_id}: a zoom needs a step >= 0`);
      }
      if (action.do === "skip" && typeof action.other !== "string" && !Number.isInteger(action.reason)) {
        throw new Error(`script item ${item.item_id}: a skip needs a listed reason index or an other text`);
      }
    }
  }
  return script;
}

function installRunnerProbe() {
  const downs = [];
  Object.defineProperty(globalThis, "__sarcostatTestRunnerDowns", { value: downs, configurable: false, writable: false });
  window.addEventListener(
    "pointerdown",
    (event) => {
      const canvas = document.querySelector("[data-part='study-canvas']");
      const target = event.target;
      let view = null;
      if (canvas !== null && canvas.dataset.zoom !== undefined) {
        const rect = canvas.getBoundingClientRect();
        view = { left: rect.left, top: rect.top, zoom: Number(canvas.dataset.zoom), offsetX: Number(canvas.dataset.offsetX), offsetY: Number(canvas.dataset.offsetY) };
      }
      downs.push({ clientX: event.clientX, clientY: event.clientY, part: target instanceof HTMLElement ? (target.dataset.part ?? null) : null, view });
    },
    { capture: true },
  );
}

async function poll(read, done, what, timeoutMs = STEP_TIMEOUT_MS) {
  const deadline = Date.now() + timeoutMs;
  let value = await read();
  while (!done(value)) {
    if (Date.now() > deadline) {
      throw new Error(`timed out after ${timeoutMs} ms waiting for ${what} (last value ${JSON.stringify(value)})`);
    }
    await new Promise((resolve) => setTimeout(resolve, 15));
    value = await read();
  }
  return value;
}

function viewOf(page) {
  return page.locator(SHOWN).evaluate((canvas) => {
    const rect = canvas.getBoundingClientRect();
    return { left: rect.left, top: rect.top, width: rect.width, height: rect.height, zoom: Number(canvas.dataset.zoom), offsetX: Number(canvas.dataset.offsetX), offsetY: Number(canvas.dataset.offsetY) };
  });
}

function sameView(a, b) {
  return ["left", "top", "width", "height", "zoom", "offsetX", "offsetY"].every((key) => a[key] === b[key]);
}

function frames(page) {
  return page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve(null)))));
}

async function stableView(page) {
  let last = await viewOf(page);
  for (let tries = 0; tries < STABLE_TRIES; tries += 1) {
    await frames(page);
    const next = await viewOf(page);
    if (sameView(last, next)) {
      return next;
    }
    last = next;
  }
  throw new Error("the study view did not settle");
}

async function seq(page) {
  return Number(await page.locator(AUTOSAVE).getAttribute("data-seq"));
}

async function dockState(page) {
  return page.evaluate(() => ({
    notice: document.querySelector("[data-part='notice']")?.textContent ?? "",
    banner: document.querySelector("[data-part='study-banner']")?.textContent ?? "",
    autosave: document.querySelector("[data-part='autosave']")?.getAttribute("data-state") ?? "",
  }));
}

async function committed(page, before, what) {
  try {
    await poll(
      () => seq(page),
      (value) => value > before,
      what,
    );
    await poll(
      () => page.locator(AUTOSAVE).getAttribute("data-state"),
      (value) => value === "saved",
      `${what}: autosave saved`,
    );
  } catch (error) {
    throw new Error(`${error.message}; page ${JSON.stringify(await dockState(page))}`);
  }
}

async function shown(page, what) {
  await page.locator(SHOWN).waitFor({ state: "visible", timeout: STEP_TIMEOUT_MS }).catch(async () => {
    throw new Error(`${what}: the item canvas was not shown; page ${JSON.stringify(await dockState(page))}`);
  });
}

async function currentTick(page) {
  return page.locator("[data-part='ticks'] li").evaluateAll((ticks) => ticks.findIndex((tick) => tick.getAttribute("data-state") === "current"));
}

async function pressDock(page, action, what) {
  const control = page.locator(`${DOCK} [data-action='${action}']`);
  await control.waitFor({ state: "visible", timeout: STEP_TIMEOUT_MS });
  if ((await control.getAttribute("aria-disabled")) === "true") {
    throw new Error(`${what}: the ${action} control is disabled; page ${JSON.stringify(await dockState(page))}`);
  }
  const before = await seq(page);
  await control.click();
  await committed(page, before, what);
}

async function clickDisplay(page, action, what, clicks) {
  const view = await stableView(page);
  const clientX = view.left + view.offsetX + view.zoom * (action.x + 0.5);
  const clientY = view.top + view.offsetY + view.zoom * (action.y + 0.5);
  const hit = await page.evaluate(([x, y]) => document.elementFromPoint(x, y)?.getAttribute("data-part") ?? null, [clientX, clientY]);
  if (hit !== "study-canvas") {
    throw new Error(`${what}: display point (${action.x}, ${action.y}) lies at client (${clientX}, ${clientY}), where the element is ${hit} instead of the study canvas`);
  }
  const before = await seq(page);
  await page.mouse.click(clientX, clientY);
  clicks.push({ what, x: action.x, y: action.y, clientX, clientY, view });
  await committed(page, before, what);
}

async function zoomTo(page, step, what) {
  const view = await stableView(page);
  await page.mouse.move(view.left + view.width / 2, view.top + view.height / 2);
  const steps = Number(await page.locator(STAGE).getAttribute("data-zoom-steps"));
  if (!(step < steps)) {
    throw new Error(`${what}: zoom step ${step} is not reachable, the item has ${steps} zoom levels`);
  }
  const read = async () => Number(await page.locator(STAGE).getAttribute("data-zoom-step"));
  for (let guard = 0; guard < 64; guard += 1) {
    const now = await read();
    if (now === step) {
      await stableView(page);
      return;
    }
    await page.mouse.wheel(0, now < step ? -WHEEL_DELTA : WHEEL_DELTA);
    await poll(read, (value) => value !== now, `${what}: zoom step after a wheel event`);
  }
  throw new Error(`${what}: zoom step ${step} not reached`);
}

async function skipItem(page, action, what) {
  await page.locator(`${DOCK} [data-action='skip']`).click();
  const dialog = page.locator(SKIP_DIALOG);
  await dialog.waitFor({ state: "visible", timeout: STEP_TIMEOUT_MS });
  if (typeof action.other === "string") {
    await dialog.locator("input[value='other']").check();
    await dialog.locator("[data-part='skip-other']").fill(action.other);
  } else {
    const listed = dialog.locator("[data-part='skip-reason']:not([value='other'])");
    const count = await listed.count();
    if (!(action.reason >= 0 && action.reason < count)) {
      throw new Error(`${what}: skip reason ${action.reason} is not one of the ${count} listed reasons`);
    }
    await listed.nth(action.reason).check();
  }
  const before = await seq(page);
  await dialog.locator("[data-action='skip-confirm']").click();
  await committed(page, before, what);
  await dialog.waitFor({ state: "hidden", timeout: STEP_TIMEOUT_MS });
}

async function toggleBranch(page, what) {
  const control = page.locator(`${DOCK} [data-action='branch']`);
  const was = await control.getAttribute("aria-pressed");
  await control.click();
  await poll(
    () => control.getAttribute("aria-pressed"),
    (value) => value !== was,
    `${what}: 'Ends at a branch' toggled`,
  );
}

async function pause(page, what) {
  const before = await seq(page);
  await page.locator(".study__head [data-action='pause']").click();
  await committed(page, before, what);
  await page.locator("[data-part='paused']").waitFor({ state: "visible", timeout: STEP_TIMEOUT_MS });
}

async function resume(page, what) {
  await page.locator("[data-part='paused'] [data-action='resume']").click();
  await shown(page, what);
  await poll(
    () => page.locator(AUTOSAVE).getAttribute("data-state"),
    (value) => value === "saved",
    `${what}: autosave saved`,
  );
}

async function act(page, action, what, clicks) {
  switch (action.do) {
    case "click":
      await clickDisplay(page, action, what, clicks);
      return;
    case "undo":
    case "m-line":
    case "next-myofibril":
    case "next":
      await pressDock(page, DOCK_ACTIONS[action.do], what);
      return;
    case "skip":
      await skipItem(page, action, what);
      return;
    case "branch":
      await toggleBranch(page, what);
      return;
    case "zoom":
      await zoomTo(page, action.step, what);
      return;
    case "pause":
      await pause(page, what);
      return;
    case "resume":
      await resume(page, what);
      return;
  }
}

async function saveDownload(page, out, run) {
  const waiting = page.waitForEvent("download", { timeout: STEP_TIMEOUT_MS });
  await run();
  const file = await waiting;
  const name = file.suggestedFilename();
  if (name !== path.basename(name) || name.startsWith(".")) {
    throw new Error(`refused download name ${JSON.stringify(name)}`);
  }
  const target = path.join(out, name);
  await file.saveAs(target);
  return { name, sha256: sha256(fs.readFileSync(target)) };
}

async function readiness(page) {
  const rows = page.locator("[data-part='readiness'] [data-check]");
  await poll(
    () => rows.evaluateAll((items) => items.map((item) => item.getAttribute("data-state"))),
    (states) => states.length > 0 && !states.includes("pending"),
    "the readiness check",
    BOOT_TIMEOUT_MS,
  );
  return Object.fromEntries(await rows.evaluateAll((items) => items.map((item) => [item.getAttribute("data-check"), item.getAttribute("data-state")])));
}

async function openSession(page, kit, script, build, log) {
  await page.locator(READY).waitFor({ state: "visible", timeout: BOOT_TIMEOUT_MS });
  await page.locator(".mode-card--study").click();
  await page.locator(START).waitFor({ state: "visible", timeout: STEP_TIMEOUT_MS });
  await page.locator(`${START} [data-action='check']`).click();
  log.readiness = await readiness(page);
  const open = page.locator(`${START} [data-action='open-kit']`);
  await poll(
    () => open.getAttribute("aria-disabled"),
    (value) => value !== "true",
    `the open-kit control after the readiness check ${JSON.stringify(log.readiness)}`,
  );
  const chooser = page.waitForEvent("filechooser", { timeout: STEP_TIMEOUT_MS });
  await open.click();
  await (await chooser).setFiles(kit);
  const identity = page.locator("[data-part='identity']");
  const refusals = page.locator("[data-part='refusals'] .study-refused__line");
  const opened = await poll(
    async () => ({ identity: await identity.isVisible(), refused: await refusals.allTextContents() }),
    (state) => state.identity || state.refused.length > 0,
    "the identity screen",
    BOOT_TIMEOUT_MS,
  );
  if (!opened.identity) {
    throw new Error(`the kit was refused: ${opened.refused.join(" | ")}`);
  }
  const shownIds = {
    study_id: await identity.locator("[data-part='id-study']").textContent(),
    annotator_id: await identity.locator("[data-part='id-annotator']").textContent(),
    session_id: await identity.locator("[data-part='id-session']").textContent(),
    version: await identity.locator("[data-part='id-build']").textContent(),
  };
  const wanted = { study_id: script.study_id, annotator_id: script.annotator_id, session_id: script.session_id, version: build.version };
  if (JSON.stringify(shownIds) !== JSON.stringify(wanted)) {
    throw new Error(`the identity screen shows ${JSON.stringify(shownIds)}, expected ${JSON.stringify(wanted)}`);
  }
  await identity.locator("[data-action='identity-yes']").click();
  await shown(page, "the first item");
}

async function playItems(page, script, out, clicks, log) {
  const items = script.items;
  const total = await page.locator("[data-part='ticks'] li").count();
  if (total !== items.length) {
    throw new Error(`the session has ${total} items, the script ${items.length}`);
  }
  let session = null;
  for (const [index, item] of items.entries()) {
    const tick = await currentTick(page);
    if (tick !== index) {
      throw new Error(`item ${item.item_id}: the session shows item ${tick + 1}, the script expects item ${index + 1}`);
    }
    for (const [step, action] of item.actions.entries()) {
      const what = `item ${index + 1}/${items.length} ${item.item_id} action ${step + 1} ${action.do}`;
      const last = index === items.length - 1 && step === item.actions.length - 1;
      if (last) {
        session = await saveDownload(page, out, () => act(page, action, what, clicks));
      } else {
        await act(page, action, what, clicks);
        if ((action.do === "next" || action.do === "skip") && step === item.actions.length - 1) {
          await shown(page, `${what}: next item`);
        }
      }
      if (action.do === "zoom") {
        log.zooms.push(action.step);
      }
    }
    console.log(`study_runner: item ${index + 1}/${items.length} ${item.item_id} ${item.actions.length} actions`);
  }
  await page.locator("[data-part='finished']").waitFor({ state: "visible", timeout: STEP_TIMEOUT_MS });
  const report = await saveDownload(page, out, () => page.locator("[data-part='finished'] [data-action='download-report']").click());
  return { session, report };
}

async function checkClicks(page, clicks, log) {
  const downs = (await page.evaluate(() => globalThis.__sarcostatTestRunnerDowns ?? [])).filter((down) => down.part === "study-canvas");
  if (downs.length !== clicks.length) {
    throw new Error(`${clicks.length} clicks sent, ${downs.length} pointerdown events on the study canvas`);
  }
  let worst = 0;
  for (const [index, click] of clicks.entries()) {
    const down = downs[index];
    if (down.view === null || !["left", "top", "zoom", "offsetX", "offsetY"].every((key) => down.view[key] === click.view[key])) {
      throw new Error(`${click.what}: the view at pointerdown ${JSON.stringify(down.view)} differs from the view the click was mapped with ${JSON.stringify(click.view)}`);
    }
    const x = (down.clientX - down.view.left - down.view.offsetX) / down.view.zoom - 0.5;
    const y = (down.clientY - down.view.top - down.view.offsetY) / down.view.zoom - 0.5;
    const offset = Math.max(Math.abs(x - click.x), Math.abs(y - click.y));
    if (!(offset < 1 / down.view.zoom)) {
      throw new Error(`${click.what}: the delivered pointerdown maps to (${x}, ${y}), ${offset} display px from the script's (${click.x}, ${click.y})`);
    }
    worst = Math.max(worst, offset);
  }
  log.clicks = clicks.length;
  log.max_click_offset_px = worst;
}

async function main() {
  let values;
  try {
    ({ values } = parseArgs({ options: { site: { type: "string" }, kit: { type: "string" }, script: { type: "string" }, out: { type: "string" } }, strict: true }));
  } catch (error) {
    console.error(`study_runner: ${error.message}\n${USAGE}`);
    return 2;
  }
  if (!values.site || !values.kit || !values.script || !values.out) {
    console.error(USAGE);
    return 2;
  }
  const site = path.resolve(values.site);
  const kit = path.resolve(values.kit);
  const out = path.resolve(values.out);
  const log = {
    format: LOG_FORMAT,
    format_version: 1,
    ok: false,
    app: null,
    build: null,
    kit: null,
    viewport: VIEWPORT,
    readiness: null,
    zooms: [],
    clicks: 0,
    max_click_offset_px: null,
    session_file: null,
    report_file: null,
    guard: { external: [], console_errors: [], page_errors: [] },
    errors: [],
  };
  fs.mkdirSync(out, { recursive: true });
  let server = null;
  let browser = null;
  try {
    const script = readScript(path.resolve(values.script));
    const manifestSha = sha256(fs.readFileSync(path.join(kit, "manifest.json")));
    if (manifestSha !== script.manifest_sha256) {
      throw new Error(`the kit's manifest.json has SHA-256 ${manifestSha}, the script was made for ${script.manifest_sha256}`);
    }
    log.kit = { study_id: script.study_id, annotator_id: script.annotator_id, session_id: script.session_id, manifest_sha256: manifestSha };
    const app = findAppFolder(site, process.env.SITE_VERSION);
    log.app = app;
    server = await startServer({ root: site, port: Number(process.env.PORT ?? 0) });
    const appUrl = `${server.url}${app}/`;
    const served = new Uint8Array(await (await fetch(new URL("BUILD.json", appUrl))).arrayBuffer());
    const info = JSON.parse(new TextDecoder().decode(served));
    log.build = { version: info.version, source_digest: info.source_digest, wheel_sha256: info.wheel?.sha256 ?? null, build_json_sha256: sha256(served) };
    console.log(`study_runner: serving ${app} on ${LOCAL_HOST}, build ${info.version}, BUILD.json ${log.build.build_json_sha256}`);
    browser = await chromium.launch({ headless: true, args: [HOST_RESOLVER_RULES] });
    const context = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: 1, acceptDownloads: true, serviceWorkers: "block" });
    await context.route("**/*", async (route) => {
      const url = route.request().url();
      if (isLocalUrl(url)) {
        await route.continue();
        return;
      }
      log.guard.external.push(`${route.request().method()} ${url}`);
      await route.abort("blockedbyclient");
    });
    const page = await context.newPage();
    page.on("request", (request) => {
      if (!isLocalUrl(request.url()) && !log.guard.external.includes(`${request.method()} ${request.url()}`)) {
        log.guard.external.push(`${request.method()} ${request.url()}`);
      }
    });
    page.on("console", (message) => {
      if (message.type() === "error") {
        log.guard.console_errors.push(message.text());
      }
    });
    page.on("pageerror", (error) => {
      log.guard.page_errors.push(String(error?.stack ?? error));
    });
    await page.addInitScript(installRunnerProbe);
    await page.goto(`${appUrl}#/`);
    await openSession(page, kit, script, log.build, log);
    const clicks = [];
    const files = await playItems(page, script, out, clicks, log);
    log.session_file = files.session;
    log.report_file = files.report;
    const stem = `${script.study_id}_${script.annotator_id}_${script.session_id}`;
    if (files.session?.name !== `${stem}.json` || files.report.name !== `${stem}.report.json`) {
      throw new Error(`downloaded ${JSON.stringify([files.session?.name, files.report.name])}, expected ${stem}.json and ${stem}.report.json`);
    }
    await checkClicks(page, clicks, log);
    await context.close();
  } catch (error) {
    log.errors.push(String(error?.stack ?? error));
  } finally {
    await browser?.close().catch(() => undefined);
    if (server !== null) {
      await new Promise((resolve) => server.server.close(() => resolve(null)));
    }
  }
  const guard = log.guard;
  log.ok = log.errors.length === 0 && guard.external.length === 0 && guard.console_errors.length === 0 && guard.page_errors.length === 0;
  fs.writeFileSync(path.join(out, "runner.json"), `${JSON.stringify(log, null, 2)}\n`);
  for (const line of [...log.errors, ...guard.external.map((entry) => `request to another host: ${entry}`), ...guard.console_errors.map((entry) => `console error: ${entry}`), ...guard.page_errors.map((entry) => `page error: ${entry}`)]) {
    console.error(`study_runner: ${line}`);
  }
  console.log(`study_runner: ${log.ok ? "ok" : "FAILED"}; ${log.clicks} clicks; session ${log.session_file?.name ?? "none"} ${log.session_file?.sha256 ?? ""}`);
  return log.ok ? 0 : 1;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = await main();
}
