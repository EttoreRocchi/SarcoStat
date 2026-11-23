import { budgetNote, timeBudget } from "./budgets.ts";
import fs from "node:fs";

import { expect, test } from "./fixtures.ts";
import { chainClicks, clickDisplay, completeItem, DEMO_KIT, displaySize, readManifest, startSession, type ManifestItem } from "./study_view.ts";

const ITEMS = readManifest(DEMO_KIT).items;
const MARK_BUDGET_MS = 100;
const TICK_BUDGET_MS = 250;
const CHAIN_CLICKS = 34;

interface Latency {
  downs: number[];
  marks: { dots: number; t: number; painted: number }[];
  ticks: { seq: number; t: number }[];
}

function installLatencyProbe(): void {
  const log: Latency = { downs: [], marks: [], ticks: [] };
  Object.defineProperty(globalThis, "__sarcostatTestLatency", { value: log, configurable: false, writable: false });
  window.addEventListener(
    "pointerdown",
    (event) => {
      if ((event.target as HTMLElement | null)?.dataset?.part === "study-canvas") {
        log.downs.push(event.timeStamp);
      }
    },
    { capture: true },
  );
  let dots = -1;
  let seq = -1;
  const observer = new MutationObserver(() => {
    const now = performance.now();
    const count = document.querySelectorAll("[data-part='study-overlay'] .ov__dot").length;
    if (count !== dots) {
      dots = count;
      const seen = count;
      requestAnimationFrame(() => log.marks.push({ dots: seen, t: now, painted: performance.now() }));
    }
    const current = Number(document.querySelector("[data-part='autosave']")?.getAttribute("data-seq") ?? "0");
    if (current !== seq) {
      seq = current;
      log.ticks.push({ seq: current, t: now });
    }
  });
  const start = (): void => observer.observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ["data-seq"] });
  if (document.body !== null) {
    start();
  } else {
    document.addEventListener("DOMContentLoaded", start);
  }
}

function percentile(values: number[], share: number): number {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.max(0, Math.ceil(share * sorted.length) - 1)] as number;
}

test("study click to mark drawn and click to autosave tick (strict IndexedDB write) stay within the budgets", async ({ page }, testInfo) => {
  test.setTimeout(240_000);
  await page.addInitScript(installLatencyProbe);
  await startSession(page, DEMO_KIT);
  await completeItem(page, ITEMS[0] as ManifestItem);
  const chain = ITEMS[1] as ManifestItem;
  const [w, h] = displaySize(chain);
  const points: [number, number][] = [];
  for (let k = 0; k < CHAIN_CLICKS / 2; k += 1) {
    const x = Math.round(w * 0.15 + (k * w * 0.7) / (CHAIN_CLICKS / 2));
    points.push([x, Math.round(h * 0.3)], [x, Math.round(h * 0.7)]);
  }
  expect(chainClicks(chain, 1)).toHaveLength(2);
  for (const [x, y] of points) {
    await clickDisplay(page, x, y);
  }
  await page.waitForTimeout(200);
  const log = await page.evaluate(() => (globalThis as unknown as { __sarcostatTestLatency: Latency }).__sarcostatTestLatency);
  const marks: number[] = [];
  const painted: number[] = [];
  const ticks: number[] = [];
  for (const down of log.downs) {
    const mark = log.marks.find((entry) => entry.t >= down);
    const tick = log.ticks.find((entry) => entry.t >= down);
    expect(mark, `a mark after the pointerdown at ${down}`).toBeDefined();
    expect(tick, `a tick after the pointerdown at ${down}`).toBeDefined();
    marks.push((mark as Latency["marks"][number]).t - down);
    painted.push((mark as Latency["marks"][number]).painted - down);
    ticks.push((tick as Latency["ticks"][number]).t - down);
  }
  const summary = {
    clicks: log.downs.length,
    mark_dom_ms: { p50: percentile(marks, 0.5), p95: percentile(marks, 0.95), max: Math.max(...marks) },
    mark_frame_ms: { p50: percentile(painted, 0.5), p95: percentile(painted, 0.95), max: Math.max(...painted) },
    tick_ms: { p50: percentile(ticks, 0.5), p95: percentile(ticks, 0.95), max: Math.max(...ticks) },
    budgets_ms: { mark_p95: MARK_BUDGET_MS, tick_p95: TICK_BUDGET_MS },
  };
  const out = testInfo.outputPath("study-latency.json");
  fs.writeFileSync(out, `${JSON.stringify(summary, null, 2)}\n`);
  await testInfo.attach("study-latency.json", { path: out, contentType: "application/json" });
  expect(log.downs.length).toBe(6 + CHAIN_CLICKS);
  for (const note of [budgetNote("study click to mark p95", summary.mark_frame_ms.p95, MARK_BUDGET_MS), budgetNote("study click to tick p95", summary.tick_ms.p95, TICK_BUDGET_MS)]) {
    if (note !== null) {
      console.log(note);
    }
  }
  expect(summary.mark_frame_ms.p95, JSON.stringify(summary)).toBeLessThanOrEqual(timeBudget(MARK_BUDGET_MS));
  expect(summary.tick_ms.p95, JSON.stringify(summary)).toBeLessThanOrEqual(timeBudget(TICK_BUDGET_MS));
});
