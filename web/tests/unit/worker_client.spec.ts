import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EngineClient, EngineError, identityProblems, type ClientListener } from "../../src/worker/client.ts";
import { BRIDGE_VERSION, COMMAND_TIMEOUT_MS, parseBuildInfo, type FatalCode } from "../../src/worker/protocol.ts";
import { identityOf, MockWorker, pythonCatalogue, TEST_BUILD } from "./worker_mock.ts";

const catalogue = pythonCatalogue();
const BASE = "http://127.0.0.1:1/v0.2.0rc1.dev0/";

interface Seen {
  stages: string[];
  ready: number;
  fatal: { code: FatalCode; message: string }[];
}

function setup(options: ConstructorParameters<typeof MockWorker>[0] = { catalogue }): { worker: MockWorker; client: EngineClient; seen: Seen } {
  const worker = new MockWorker(options);
  const seen: Seen = { stages: [], ready: 0, fatal: [] };
  const listener: ClientListener = {
    boot: (stage) => seen.stages.push(stage),
    ready: () => {
      seen.ready += 1;
    },
    fatal: (code, message) => seen.fatal.push({ code, message }),
  };
  const client = new EngineClient(worker, { build: TEST_BUILD, base: BASE }, listener);
  return { worker, client, seen };
}

function requests(worker: MockWorker): string[] {
  return worker.posted.flatMap((entry) => (entry.message.kind === "request" ? [entry.message.request.cmd] : []));
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("engine client", () => {
  it("starts the worker with the build, reports the boot stages and becomes ready after the identity handshake", async () => {
    const { worker, client, seen } = setup();
    expect(client.state).toBe("idle");
    client.start();
    client.start();
    expect(worker.posted.filter((entry) => entry.message.kind === "start")).toHaveLength(1);
    expect(worker.posted[0]?.message).toEqual({ kind: "start", base: BASE, build: TEST_BUILD });
    expect(seen.stages).toEqual(["runtime", "numpy", "wheel", "engine"]);
    expect(client.state).toBe("handshake");
    expect(requests(worker)).toEqual(["sys.identity"]);
    await vi.runAllTimersAsync();
    expect(client.state).toBe("ready");
    expect(seen.ready).toBe(1);
    expect(seen.fatal).toEqual([]);
  });

  it("refuses commands before it is ready, without sending them", async () => {
    const { worker, client } = setup({ catalogue, silent: true });
    await expect(client.call("sys.identity", {})).rejects.toThrow("not ready");
    client.start();
    await expect(client.call("doc.export", { what: "v1" })).rejects.toBeInstanceOf(EngineError);
    expect(requests(worker)).toEqual(["sys.identity"]);
  });

  it("matches answers to requests by id and ignores answers it did not ask for", async () => {
    const { worker, client, seen } = setup();
    client.start();
    await vi.runAllTimersAsync();
    worker.silent = true;
    const first = client.call("doc.export", { what: "v1" });
    const second = client.call("doc.export", { what: "results_csv" });
    const sent = worker.received.slice(-2);
    worker.send({ kind: "response", response: { v: 1, id: "someone-else", ok: true, result: {} }, buffers: [] });
    worker.send({ kind: "response", response: { v: 1, id: sent[1]?.id, ok: true, result: { file_name: "b", text: "2" } }, buffers: [] });
    worker.send({ kind: "response", response: { v: 1, id: sent[0]?.id, ok: true, result: { file_name: "a", text: "1" } }, buffers: [] });
    await expect(first).resolves.toEqual({ file_name: "a", text: "1" });
    await expect(second).resolves.toEqual({ file_name: "b", text: "2" });
    expect(seen.fatal).toEqual([]);
  });

  it("transfers argument buffers, copying views of larger buffers first", async () => {
    const { worker, client } = setup();
    client.start();
    await vi.runAllTimersAsync();
    const whole = new Uint8Array([1, 2, 3]);
    const backing = new Uint8Array([9, 9, 4, 5, 9]);
    const view = backing.subarray(2, 4);
    void client.call("study.open_kit", { manifest: whole, manifest_sha256: view });
    const sent = worker.posted.at(-1)!;
    expect(sent.message.kind).toBe("request");
    if (sent.message.kind === "request") {
      expect(sent.message.request.args).toEqual({ manifest: { $buf: 0 }, manifest_sha256: { $buf: 1 } });
      expect(sent.message.buffers[0]).toBe(whole);
      expect(Array.from(sent.message.buffers[1] ?? [])).toEqual([4, 5]);
      expect(sent.message.buffers[1]?.buffer).not.toBe(backing.buffer);
      expect(sent.transfer).toEqual([whole.buffer, sent.message.buffers[1]?.buffer]);
    }
  });

  it("decodes result buffers into Uint8Array", async () => {
    const { worker, client } = setup();
    client.start();
    await vi.runAllTimersAsync();
    worker.silent = true;
    const pending = client.call("study.pause", { t: { mono_s: 1, wall_ms: 2 } });
    const id = worker.received.at(-1)?.id;
    const bytes = new Uint8Array([7, 8]);
    worker.send({
      kind: "response",
      response: { v: 1, id, ok: true, result: { view: null, autosave: { $buf: 0 }, seq: 3, finished: null } },
      buffers: [bytes],
    });
    await expect(pending).resolves.toMatchObject({ autosave: bytes, seq: 3 });
  });

  it("rejects arguments that cannot be posted without waiting for a timeout", async () => {
    const { worker, client, seen } = setup();
    client.start();
    await vi.runAllTimersAsync();
    const post = worker.postMessage.bind(worker);
    worker.postMessage = () => {
      throw new DOMException("could not be cloned", "DataCloneError");
    };
    const error = await client.call("doc.export", { what: "v1" }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(EngineError);
    expect((error as EngineError).code).toBe("bad_request");
    worker.postMessage = post;
    await vi.advanceTimersByTimeAsync(2 * COMMAND_TIMEOUT_MS);
    expect(seen.fatal).toEqual([]);
    expect(client.state).toBe("ready");
  });

  it("turns a missing answer into the timeout fatal state after 10 s and stops the worker", async () => {
    const { worker, client, seen } = setup();
    client.start();
    await vi.runAllTimersAsync();
    worker.silent = true;
    const pending = client.call("results.get", {});
    pending.catch(() => {});
    await vi.advanceTimersByTimeAsync(COMMAND_TIMEOUT_MS - 1);
    expect(seen.fatal).toEqual([]);
    await vi.advanceTimersByTimeAsync(1);
    expect(seen.fatal).toEqual([{ code: "timeout", message: "results.get got no answer within 10 s" }]);
    await expect(pending).rejects.toBeInstanceOf(EngineError);
    expect(worker.terminated).toBe(true);
    expect(client.state).toBe("failed");
    const before = worker.posted.length;
    await expect(client.call("sys.identity", {})).rejects.toThrow("stopped");
    expect(worker.posted).toHaveLength(before);
  });

  it("times out a handshake that never comes back", async () => {
    const { client, seen } = setup({ catalogue, silent: true });
    client.start();
    await vi.advanceTimersByTimeAsync(COMMAND_TIMEOUT_MS);
    expect(seen.fatal.map((entry) => entry.code)).toEqual(["timeout"]);
    expect(seen.ready).toBe(0);
  });

  it.each([
    ["error", "worker_died"],
    ["messageerror", "worker_died"],
  ] as const)("turns a worker %s event into the fatal state", async (type, code) => {
    const { worker, client, seen } = setup();
    client.start();
    await vi.runAllTimersAsync();
    worker.emit(type, new Event(type));
    worker.emit(type, new Event(type));
    expect(seen.fatal.map((entry) => entry.code)).toEqual([code]);
    expect(worker.terminated).toBe(true);
  });

  it("passes the worker's fatal event through and ignores everything after it", async () => {
    const { worker, client, seen } = setup({ catalogue, stages: ["runtime"] });
    worker.silent = true;
    client.start();
    worker.send({ kind: "fatal", code: "build_damaged", message: "wheels/x.whl: SHA-256 mismatch" });
    worker.send({ kind: "boot", stage: "numpy" });
    expect(seen.fatal).toEqual([{ code: "build_damaged", message: "wheels/x.whl: SHA-256 mismatch" }]);
    expect(client.state).toBe("failed");
  });

  it("treats a message it does not understand as a dead worker", () => {
    const { worker, client, seen } = setup({ catalogue, silent: true, stages: [] });
    client.start();
    worker.send({ kind: "surprise" });
    expect(seen.fatal.map((entry) => entry.code)).toEqual(["worker_died"]);
  });

  it("refuses an engine with another bridge version before sending any command", () => {
    const { worker, client, seen } = setup({ catalogue, identity: { ...identityOf(TEST_BUILD), bridge_version: BRIDGE_VERSION + 1 } });
    client.start();
    expect(seen.fatal.map((entry) => entry.code)).toEqual(["bridge_mismatch"]);
    expect(requests(worker)).toEqual([]);
  });

  it.each([
    ["sarcostat_version", "0.2.0"],
    ["source_digest", "c".repeat(64)],
    ["wheel_sha256", "d".repeat(64)],
    ["pyodide_version", "0.27.0"],
  ] as const)("refuses an engine whose %s differs from BUILD.json before sending any command", (field, value) => {
    const { worker, client, seen } = setup({ catalogue, identity: { ...identityOf(TEST_BUILD), [field]: value } });
    client.start();
    expect(seen.fatal.map((entry) => entry.code)).toEqual(["build_damaged"]);
    expect(seen.fatal[0]?.message).toContain(value);
    expect(requests(worker)).toEqual([]);
  });

  it("lists every identity problem", () => {
    expect(identityProblems(identityOf(TEST_BUILD), TEST_BUILD)).toEqual([]);
    const wrong = { ...identityOf(TEST_BUILD), source_digest: "c".repeat(64), pyodide_version: "1" };
    expect(identityProblems(wrong, TEST_BUILD)).toHaveLength(2);
  });
});

describe("BUILD.json", () => {
  const raw = {
    format: "sarcostat-build",
    format_version: 1,
    version: "0.2.0rc1.dev0",
    commit: "0".repeat(40),
    wheel: { file: "wheels/sarcostat-0.2.0rc1.dev0-py3-none-any.whl", sha256: "a".repeat(64) },
    source_digest: "b".repeat(64),
    pyodide: { version: "314.0.8", dir: "pyodide" },
    files: {},
  };

  it("keeps only the fields the app uses", () => {
    expect(parseBuildInfo(raw)).toEqual({ ...TEST_BUILD, pyodide: { version: "314.0.8", dir: "pyodide/" } });
  });

  it.each([
    ["format", { ...raw, format: "other" }],
    ["wheel.sha256", { ...raw, wheel: { ...raw.wheel, sha256: "A".repeat(64) } }],
    ["source_digest", { ...raw, source_digest: "b".repeat(63) }],
    ["wheel.file", { ...raw, wheel: { ...raw.wheel, file: "https://example.org/x.whl" } }],
    ["wheel.file", { ...raw, wheel: { ...raw.wheel, file: "../x.whl" } }],
    ["pyodide.dir", { ...raw, pyodide: { version: "314.0.8", dir: "/pyodide/" } }],
    ["pyodide.version", { ...raw, pyodide: { dir: "pyodide/" } }],
  ])("refuses a bad %s", (_field, value) => {
    expect(() => parseBuildInfo(value)).toThrow(TypeError);
  });
});
