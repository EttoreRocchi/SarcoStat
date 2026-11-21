import { assertGuardClean, expect, test, type GuardLog } from "./fixtures.ts";
import { dynamicImportExpression } from "./hooks/probe.ts";

const TARGET = "https://example.org/";
const BLOCKED = /^net::ERR_BLOCKED_BY_CLIENT\b/;
const CAUGHT = /^(page|worker) GET https:\/\/example\.org\/$/;

function provenToFail(guard: GuardLog): string {
  let failure: unknown = null;
  try {
    assertGuardClean(guard);
  } catch (error) {
    failure = error;
  }
  expect(failure, "the guard's own end-of-test check fails on this log").not.toBeNull();
  const message = failure instanceof Error ? failure.message : String(failure);
  expect(message).toContain("requests to hosts other than 127.0.0.1");
  expect(message).toContain(TARGET);
  const caught = guard.external.splice(0);
  test.info().annotations.push({ type: "guard caught", description: caught.join("; ") });
  console.log(`guard caught: ${caught.join("; ")}`);
  return caught.join("; ");
}

test.describe("request guard mutations (plan M3-6 acceptance 2)", () => {
  test("a page request to https://example.org/ fails the request guard", async ({ page, guard }) => {
    await page.goto("./");
    expect(guard.external).toEqual([]);
    const failed = page.waitForEvent("requestfailed", (request) => request.url() === TARGET);
    await page.evaluate((url) => {
      window.location.assign(url);
    }, TARGET);
    const request = await failed;
    expect(guard.expectedExternal).toEqual([]);
    expect(guard.external).toEqual([`page GET ${TARGET}`]);
    expect(request.failure()?.errorText).toMatch(BLOCKED);
    expect(request.isNavigationRequest()).toBe(true);
    expect(provenToFail(guard)).toBe(`page GET ${TARGET}`);
    expect(page.url()).not.toContain("example.org");
  });

  test("a worker request to https://example.org/ fails the request guard", async ({ page, guard, engine, workerProbe }) => {
    await page.goto("./");
    await engine.ready();
    const worker = await workerProbe.engineWorker();
    expect(worker.url()).toMatch(/^http:\/\/127\.0\.0\.1:\d+\/.*py\.worker[^/]*\.js$/);
    expect(guard.external).toEqual([]);
    const failed = page.waitForEvent("requestfailed", (request) => request.url() === TARGET);
    const outcome = await worker.evaluate(dynamicImportExpression(TARGET));
    expect(outcome).not.toBe("loaded");
    await expect.poll(() => guard.external.length, { message: "the guard logs the worker's request" }).toBe(1);
    expect(guard.expectedExternal).toEqual([]);
    expect(guard.external[0]).toMatch(CAUGHT);
    expect((await failed).failure()?.errorText).toMatch(BLOCKED);
    expect(provenToFail(guard)).toMatch(CAUGHT);
    const state = await workerProbe.state(worker);
    expect(state.pinned.fetch).toBe(true);
    expect(await engine.call<{ bridge_version: number }>("sys.identity", {})).toMatchObject({ bridge_version: 1 });
  });
});
