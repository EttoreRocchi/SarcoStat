import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import type { Page, Request } from "@playwright/test";

import { en } from "../../src/strings/en.ts";
import { BRIDGE_VERSION, COMMAND_TIMEOUT_MS } from "../../src/worker/protocol.ts";
import { expect, test } from "./fixtures.ts";
import { installEngineHook, type EngineHook, type EngineHookConfig, type EngineRecord } from "./hooks/engine.ts";
import { installLongTaskHook, type LongTask } from "./hooks/longtasks.ts";
import { dynamicImportProbe, probeWorkerChannels, PROBE_HOST } from "./hooks/worker_probe.ts";

const BOOT_BUDGET_MS = 4_000;
const BOOT_BUDGET_BYTES = 18_000_000;
const LONG_TASK_LIMIT_MS = 200;
const READY = '.statusline[data-state="ready"]';
const BOOT_TIMEOUT_MS = 30_000;

interface Build {
  version: string;
  wheel: { file: string; sha256: string };
  source_digest: string;
  pyodide: { version: string; dir: string };
}

interface Traffic {
  urls: string[];
  bytes(): Promise<number>;
}

const PROBE_IMAGE = { file_name: "probe.png", sha256: "0".repeat(64), width_px: 200, height_px: 200 };
const TWENTY_CALLS: [string, Record<string, unknown>][] = [
  ["sys.identity", {}],
  ["doc.new", { image: PROBE_IMAGE }],
  ["doc.set_scale", { pixel_size: 0.1, unit: "um" }],
  ["shape.click", { x: 40, y: 40 }],
  ["shape.click", { x: 40, y: 160 }],
  ["shape.click", { x: 100, y: 42 }],
  ["shape.click", { x: 100, y: 158 }],
  ["shape.click", { x: 160, y: 40 }],
  ["shape.click", { x: 160, y: 160 }],
  ["shape.confirm", {}],
  ["shape.click", { x: 30, y: 30 }],
  ["shape.undo", {}],
  ["results.get", {}],
  ["doc.set_reference_direction", { deg: 90 }],
  ["doc.set_click_error", { sigma: 0.5 }],
  ["results.get", {}],
  ["doc.export", { what: "v1" }],
  ["doc.export", { what: "results_json" }],
  ["doc.export", { what: "results_csv" }],
  ["sys.identity", {}],
];

async function injectHooks(page: Page, config: Partial<EngineHookConfig> = {}): Promise<void> {
  await page.addInitScript(installLongTaskHook);
  await page.addInitScript(installEngineHook, { alterIdentity: null, terminateOnFirstRequest: false, ...config });
}

function watchTraffic(page: Page): Traffic {
  const urls: string[] = [];
  const sizes: Promise<number>[] = [];
  page.context().on("requestfinished", (request: Request) => {
    urls.push(request.url());
    sizes.push(request.sizes().then((size) => size.responseBodySize));
  });
  return {
    urls,
    bytes: async () => (await Promise.all(sizes)).reduce((sum, size) => sum + size, 0),
  };
}

async function servedBuild(page: Page, baseURL: string | undefined): Promise<Build> {
  const response = await page.request.get(new URL("BUILD.json", baseURL).href);
  expect(response.ok()).toBe(true);
  return (await response.json()) as Build;
}

async function records(page: Page): Promise<EngineRecord[]> {
  return page.evaluate(() => (globalThis as unknown as { __sarcostatTestEngine: EngineHook }).__sarcostatTestEngine.records.slice());
}

async function longTasks(page: Page): Promise<LongTask[]> {
  return page.evaluate(() => (globalThis as unknown as { __sarcostatTestLongTasks: LongTask[] }).__sarcostatTestLongTasks.slice());
}

function cpythonSourceDigest(wheelFile: string): string | null {
  const python = process.env.SARCOSTAT_PYTHON;
  if (!python) {
    return null;
  }
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "sarcostat-wheel-"));
  try {
    execFileSync(python, ["-I", "-m", "zipfile", "-e", wheelFile, dir]);
    const script = "import sys; from sarcostat.io.provenance import source_digest; print(source_digest(sys.argv[1]))";
    return execFileSync(python, ["-I", "-c", script, path.join(dir, "sarcostat")], { encoding: "utf8" }).trim();
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

async function expectFatal(page: Page, code: keyof typeof en.fatal.title): Promise<void> {
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toBeVisible({ timeout: BOOT_TIMEOUT_MS });
  await expect(dialog).toHaveAttribute("aria-modal", "true");
  await expect(dialog).toHaveAttribute("data-code", code);
  await expect(dialog.locator(".fatal__title")).toHaveText(en.fatal.title[code]);
  await expect(dialog.locator(".fatal__action")).toHaveText(en.fatal.reload);
  await expect(dialog.locator(".fatal__action")).toBeFocused();
}

test.describe("engine boot", () => {
  test("cold boot shows the identity of BUILD.json within the time and size budgets, with numpy only", async ({ page, baseURL }) => {
    await injectHooks(page);
    const traffic = watchTraffic(page);
    await page.goto("./");
    await expect(page.locator(READY)).toBeVisible({ timeout: BOOT_TIMEOUT_MS });
    const build = await servedBuild(page, baseURL);
    const log = await records(page);
    const handshake = log.find((record) => record.direction === "received" && record.kind === "response");
    expect(handshake, "the handshake answer").toBeDefined();
    const bootMs = handshake?.t ?? Number.POSITIVE_INFINITY;
    const bytes = await traffic.bytes();
    const tasks = await longTasks(page);
    const longest = Math.max(0, ...tasks.map((task) => task.duration));
    console.log(`boot: ${bootMs.toFixed(0)} ms to identity, ${bytes} bytes (${(bytes / 1e6).toFixed(2)} MB), ${traffic.urls.length} requests, longest main-thread task ${longest.toFixed(0)} ms`);
    expect(bootMs).toBeLessThanOrEqual(BOOT_BUDGET_MS);
    expect(bytes).toBeLessThanOrEqual(BOOT_BUDGET_BYTES);
    expect(traffic.urls.filter((url) => /scipy/i.test(url))).toEqual([]);
    expect(traffic.urls.some((url) => /\/pyodide\/numpy-[^/]+\.whl$/.test(url))).toBe(true);
    expect(longest).toBeLessThanOrEqual(LONG_TASK_LIMIT_MS);

    const status = page.locator(READY);
    await expect(status.locator('[data-item="version"]')).toHaveText(`${en.status.version} ${build.version}`);
    await expect(status.locator('[data-item="pyodide"]')).toHaveText(`${en.status.pyodide} ${build.pyodide.version}`);
    const wheel = status.locator('[data-item="wheel"] .statusline__hash');
    const source = status.locator('[data-item="source"] .statusline__hash');
    await expect(wheel).toHaveText(build.wheel.sha256.slice(0, 12));
    await expect(source).toHaveText(build.source_digest.slice(0, 12));
    await wheel.click();
    await expect(wheel).toHaveText(build.wheel.sha256);
    await expect(wheel).toHaveAttribute("aria-pressed", "true");
    await source.click();
    await expect(source).toHaveText(build.source_digest);
    await wheel.click();
    await expect(wheel).toHaveText(build.wheel.sha256.slice(0, 12));

    const appDir = process.env.SARCOSTAT_APP_DIR as string;
    const cpython = cpythonSourceDigest(path.join(appDir, build.wheel.file));
    test.info().annotations.push({ type: "cpython source_digest", description: cpython ?? "skipped: SARCOSTAT_PYTHON not set" });
    console.log(`source_digest: page ${await source.textContent()}, BUILD.json ${build.source_digest}, CPython ${cpython ?? "skipped (SARCOSTAT_PYTHON not set)"}`);
    if (cpython !== null) {
      expect(await source.textContent()).toBe(cpython);
    }

    const csp = await page.locator('meta[http-equiv="Content-Security-Policy"]').getAttribute("content");
    expect(csp).not.toContain("wasm-unsafe-eval");
    expect(log.filter((record) => record.direction === "posted").map((record) => [record.kind, record.cmd])).toEqual([
      ["start", null],
      ["request", "sys.identity"],
    ]);
  });

  test("twenty engine calls after boot cause no main-thread task over 200 ms", async ({ page }) => {
    await injectHooks(page);
    await page.goto("./");
    await expect(page.locator(READY)).toBeVisible({ timeout: BOOT_TIMEOUT_MS });
    const outcomes = await page.evaluate(async (calls) => {
      const engine = (globalThis as unknown as { __sarcostatTestEngine: EngineHook }).__sarcostatTestEngine;
      const out: { cmd: string; ok: boolean; message: string | null }[] = [];
      for (const [cmd, args] of calls) {
        const response = await engine.call(cmd, args);
        out.push({ cmd, ok: response.ok, message: response.error?.message ?? null });
      }
      return out;
    }, TWENTY_CALLS);
    expect(outcomes.filter((outcome) => !outcome.ok)).toEqual([]);
    expect(outcomes).toHaveLength(20);
    const tasks = await longTasks(page);
    const longest = Math.max(0, ...tasks.map((task) => task.duration));
    console.log(`long tasks: ${tasks.length} over 50 ms during boot and 20 calls, longest ${longest.toFixed(0)} ms`);
    expect(longest).toBeLessThanOrEqual(LONG_TASK_LIMIT_MS);
    await expect(page.locator(READY)).toBeVisible();
    await page.evaluate(() => {
      setTimeout(() => {
        const start = performance.now();
        while (performance.now() - start < 120) {
          Math.sqrt(start);
        }
      }, 0);
    });
    await expect
      .poll(async () => (await longTasks(page)).filter((task) => task.duration >= 100).length, { message: "the long-task observer sees a deliberate 120 ms task" })
      .toBe(1);
  });
});

test.describe("engine failures", () => {
  test("one changed byte in the wheel gives the build-damaged screen and no engine command", async ({ page, baseURL }) => {
    await injectHooks(page);
    let damaged = 0;
    await page.context().route(/\/wheels\/[^/]+\.whl$/, async (route) => {
      const response = await route.fetch();
      const body = Buffer.from(await response.body());
      const at = Math.floor(body.length / 2);
      body[at] = (body[at] as number) ^ 0x01;
      damaged += 1;
      await route.fulfill({ response, body });
    });
    await page.goto("./");
    await expectFatal(page, "build_damaged");
    expect(damaged).toBe(1);
    const build = await servedBuild(page, baseURL);
    await expect(page.locator(".fatal [data-part='detail']")).toContainText(build.wheel.sha256);
    await expect(page.locator('.statusline[data-state="stopped"]')).toBeVisible();
    await page.waitForTimeout(500);
    const log = await records(page);
    expect(log.filter((record) => record.direction === "posted").map((record) => record.kind)).toEqual(["start"]);
    expect(log.some((record) => record.kind === "identity")).toBe(false);
  });

  test("a bridge version that differs from protocol.ts gives the fatal screen", async ({ page }) => {
    await injectHooks(page, { alterIdentity: { bridge_version: BRIDGE_VERSION + 1 } });
    await page.goto("./");
    await expectFatal(page, "bridge_mismatch");
    await expect(page.locator(".fatal [data-part='detail']")).toContainText(`bridge version ${BRIDGE_VERSION + 1}`);
    const log = await records(page);
    expect(log.filter((record) => record.direction === "posted").map((record) => record.kind)).toEqual(["start"]);
  });

  test("a worker terminated in the middle of a command gives the fatal screen, and Reload restores the app", async ({ page }) => {
    test.setTimeout(COMMAND_TIMEOUT_MS + 3 * BOOT_TIMEOUT_MS);
    await injectHooks(page, { terminateOnFirstRequest: true });
    await page.goto("./");
    await expectFatal(page, "timeout");
    const log = await records(page);
    const posted = log.filter((record) => record.direction === "posted");
    const fatalAt = await page.evaluate(() => performance.now());
    expect(posted.map((record) => record.kind)).toEqual(["start", "request"]);
    expect(fatalAt - (posted[1]?.t ?? 0)).toBeGreaterThanOrEqual(COMMAND_TIMEOUT_MS - 50);
    await page.locator(".fatal__action").click();
    await expect(page.locator(READY)).toBeVisible({ timeout: BOOT_TIMEOUT_MS });
    await expect(page.getByRole("alertdialog")).toBeHidden();
  });
});

test.describe("network guards", () => {
  test("the document CSP blocks a cross-origin fetch from the page", async ({ page, guard }) => {
    guard.allow.consoleError.push(/Content Security Policy/);
    await page.goto("./");
    const outcome = await page.evaluate(async (host) => {
      const violations: string[] = [];
      document.addEventListener("securitypolicyviolation", (event) => violations.push(`${event.effectiveDirective} ${event.blockedURI}`));
      let result: string;
      try {
        await fetch(`https://${host}/page-fetch`);
        result = "allowed";
      } catch (error) {
        result = error instanceof Error ? error.name : String(error);
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
      return { result, violations };
    }, PROBE_HOST);
    expect(outcome.result).toBe("TypeError");
    expect(outcome.violations).toEqual([`connect-src https://${PROBE_HOST}/page-fetch`]);
    expect(guard.external).toEqual([]);
    await expect(page.locator(READY)).toBeVisible({ timeout: BOOT_TIMEOUT_MS });
  });

  test("inside the worker, guard.ts refuses cross-origin channels; the test guard catches the dynamic import it cannot wrap", async ({ page, guard }) => {
    guard.allow.external.push(new RegExp(`^https://${PROBE_HOST.replace(".", "\\.")}/probe-import\\.mjs$`));
    const workers: import("@playwright/test").Worker[] = [];
    page.on("worker", (worker) => workers.push(worker));
    const sockets: string[] = [];
    page.on("websocket", (socket) => sockets.push(socket.url()));
    await page.goto("./");
    await expect(page.locator(READY)).toBeVisible({ timeout: BOOT_TIMEOUT_MS });
    expect(workers).toHaveLength(1);
    const worker = workers[0] as import("@playwright/test").Worker;
    const refused = await worker.evaluate(probeWorkerChannels, PROBE_HOST);
    for (const [channel, message] of Object.entries(refused)) {
      expect(message, channel).toContain("SarcoStat worker guard: refused a cross-origin");
      expect(message, channel).toContain(PROBE_HOST);
    }
    expect(guard.external).toEqual([]);
    expect(sockets).toEqual([]);
    const imported = await worker.evaluate(dynamicImportProbe(PROBE_HOST));
    expect(imported).not.toBe("loaded");
    expect(guard.expectedExternal).toHaveLength(1);
    expect(guard.expectedExternal[0]).toMatch(new RegExp(`^(page|worker) GET https://${PROBE_HOST.replace(".", "\\.")}/probe-import\\.mjs$`));
    expect(guard.external).toEqual([]);
  });
});
