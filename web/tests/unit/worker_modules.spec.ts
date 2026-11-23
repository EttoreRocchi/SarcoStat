import fs from "node:fs";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { WEB_DIR } from "../../scripts/lane.mjs";
import { EngineClient, EngineError, type ClientListener } from "../../src/worker/client.ts";
import { COMMAND_TIMEOUT_MS, isFromWorker, isToWorker, type FatalCode } from "../../src/worker/protocol.ts";
import { MockWorker, pythonCatalogue, TEST_BUILD } from "./worker_mock.ts";

const catalogue = pythonCatalogue();
const BASE = "http://127.0.0.1:1/v0.2.0rc1.dev0/";
const PROBED = ["sarcostat.core", "sarcostat.analysis", "sarcostat.export", "scipy"];

function setup(options: Partial<ConstructorParameters<typeof MockWorker>[0]> = {}): { worker: MockWorker; client: EngineClient; fatal: FatalCode[] } {
  const worker = new MockWorker({ catalogue, ...options });
  const fatal: FatalCode[] = [];
  const listener: ClientListener = { boot: () => {}, ready: () => {}, fatal: (code) => fatal.push(code) };
  const client = new EngineClient(worker, { build: TEST_BUILD, base: BASE }, listener);
  return { worker, client, fatal };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("module probe outside the bridge", () => {
  it("types the worker-level messages both ways", () => {
    expect(isToWorker({ kind: "modules", names: PROBED })).toBe(true);
    expect(isToWorker({ kind: "modules", names: [1] })).toBe(false);
    expect(isFromWorker({ kind: "modules", loaded: ["scipy"] })).toBe(true);
    expect(isFromWorker({ kind: "modules", loaded: "scipy" })).toBe(false);
  });

  it("asks the worker which of the names are in sys.modules without sending a bridge request", async () => {
    const { worker, client } = setup({ loadedModules: ["numpy", "sarcostat.io"] });
    client.start();
    await vi.runAllTimersAsync();
    const before = worker.received.length;
    const absent = client.modules(PROBED);
    await vi.runAllTimersAsync();
    await expect(absent).resolves.toEqual([]);
    const present = client.modules(["numpy", "scipy"]);
    await vi.runAllTimersAsync();
    await expect(present).resolves.toEqual(["numpy"]);
    expect(worker.received).toHaveLength(before);
    expect(worker.probed).toEqual([PROBED, ["numpy", "scipy"]]);
  });

  it("refuses before the engine is ready and times out like a command", async () => {
    const { worker, client, fatal } = setup();
    await expect(client.modules(PROBED)).rejects.toBeInstanceOf(EngineError);
    client.start();
    await vi.runAllTimersAsync();
    worker.silent = true;
    const pending = client.modules(PROBED);
    pending.catch(() => {});
    await vi.advanceTimersByTimeAsync(COMMAND_TIMEOUT_MS);
    expect(fatal).toEqual(["timeout"]);
    await expect(pending).rejects.toThrow("stopped");
  });

  it("is answered by the engine worker from its own queue, after the commands sent before it", () => {
    const source = fs.readFileSync(path.join(WEB_DIR, "src", "worker", "py.worker.ts"), "utf8");
    expect(source).toMatch(/message\.kind === "modules"/);
    expect(source).toMatch(/queue = queue\.then\(\(\) => \{/);
    expect(source).toMatch(/pyimport\("sys"\)/);
  });
});

describe("study single flight and closing", () => {
  it("refuses a second study command while one is in the engine, but not other families", async () => {
    const { worker, client } = setup();
    client.start();
    await vi.runAllTimersAsync();
    worker.silent = true;
    const first = client.call("study.pause", { t: { mono_s: 1, wall_ms: 2 } });
    first.catch(() => {});
    await expect(client.call("study.resume", { t: { mono_s: 1, wall_ms: 2 } })).rejects.toThrow("one at a time");
    const other = client.call("results.get", {});
    other.catch(() => {});
    expect(worker.received.map((request) => request.cmd)).toEqual(["sys.identity", "study.pause", "results.get"]);
  });

  it("closes without a fatal state: pending calls and probes are rejected and the worker is terminated", async () => {
    const { worker, client, fatal } = setup();
    client.start();
    await vi.runAllTimersAsync();
    worker.silent = true;
    const pending = client.call("study.pause", { t: { mono_s: 1, wall_ms: 2 } });
    const probe = client.modules(PROBED);
    client.close();
    await expect(pending).rejects.toThrow("closed");
    await expect(probe).rejects.toThrow("closed");
    expect(worker.terminated).toBe(true);
    expect(fatal).toEqual([]);
    expect(client.state).toBe("failed");
    await vi.advanceTimersByTimeAsync(2 * COMMAND_TIMEOUT_MS);
    expect(fatal).toEqual([]);
  });
});
