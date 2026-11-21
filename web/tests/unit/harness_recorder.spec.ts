import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";

import { describe, expect, it } from "vitest";

import { HOOK_PREFIX, HOOKS_DIR, hookSymbols } from "../../scripts/check_site.mjs";
import { HOOK_SYMBOLS as ENGINE_SYMBOLS, installEngineHook } from "../e2e/hooks/engine.ts";
import { HOOK_SYMBOLS as PROBE_SYMBOLS, installWorkerProbe, type WorkerProbeState } from "../e2e/hooks/probe.ts";
import {
  HOOK_SYMBOLS as RECORDER_SYMBOLS,
  TEST_ID_PREFIX,
  installRecorder,
  type RecorderConfig,
  type RecorderHook,
} from "../e2e/hooks/recorder.ts";

const PAGE = "http://127.0.0.1:4173/SarcoStat/v0.2.0/";

const FAKE_WORKER = `
globalThis.Worker = class Worker extends EventTarget {
  constructor(url, options) {
    super();
    this.url = String(url);
    this.options = options;
    this.terminated = false;
  }
  postMessage(message, transfer) {
    if (this.terminated || this.url.includes("silent")) {
      return;
    }
    const reply = (data) => setTimeout(() => this.dispatchEvent(new MessageEvent("message", { data })), 0);
    if (message.kind === "start") {
      reply({ kind: "boot", stage: "runtime" });
      reply({ kind: "identity", identity: { sarcostat_version: "0.2.0", bridge_version: 1, source_digest: "a".repeat(64), pyodide_version: "314.0.8", wheel_sha256: "b".repeat(64) } });
      return;
    }
    const { id, cmd, args } = message.request;
    if (cmd === "fail") {
      reply({ kind: "response", response: { v: 1, id, ok: false, error: { code: "refused", message: "refused on purpose" } }, buffers: [] });
      return;
    }
    reply({ kind: "response", response: { v: 1, id, ok: true, result: { cmd, args, worker: this.url, sizes: message.buffers.map((b) => b.byteLength) } }, buffers: [] });
  }
  terminate() {
    this.terminated = true;
  }
};
globalThis.startApp = (file) => {
  const seen = [];
  const worker = new Worker(new URL(file, location.href), { type: "module" });
  worker.addEventListener("message", (event) => seen.push(event.data));
  worker.postMessage({ kind: "start", base: location.href, build: {} }, []);
  return { seen, worker, request: (id, cmd, args) => worker.postMessage({ kind: "request", request: { v: 1, id, cmd, args }, buffers: [] }, []) };
};
`;

interface App {
  seen: { kind: string; identity?: Record<string, unknown>; response?: { id: unknown } }[];
  worker: { terminated: boolean; url: string };
  request(id: number, cmd: string, args: Record<string, unknown>): void;
}

interface PageRealm {
  context: vm.Context;
  install(config?: Partial<RecorderConfig>, installer?: (config: Partial<RecorderConfig>) => void): void;
  hook(): RecorderHook;
  start(file?: string): App;
}

function memoryStorage(): Storage {
  const store = new Map<string, string>();
  return {
    get length() {
      return store.size;
    },
    clear: () => store.clear(),
    getItem: (key: string) => store.get(key) ?? null,
    key: (index: number) => [...store.keys()][index] ?? null,
    removeItem: (key: string) => void store.delete(key),
    setItem: (key: string, value: string) => void store.set(key, String(value)),
  };
}

function pageRealm(storage: Storage = memoryStorage()): PageRealm {
  const context = vm.createContext({
    EventTarget,
    Event,
    MessageEvent,
    URL,
    performance,
    setTimeout,
    clearTimeout,
    location: { href: PAGE },
    sessionStorage: storage,
  });
  vm.runInContext(FAKE_WORKER, context);
  return {
    context,
    install(config = {}, installer = installRecorder) {
      vm.runInContext(`(${installer.toString()})(${JSON.stringify(config)})`, context);
    },
    hook: () => context.__sarcostatTestRecorder as RecorderHook,
    start: (file = "assets/py.worker-abc123.js") => (context.startApp as (file: string) => App)(file),
  };
}

const tick = (ms = 10): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

describe("injected engine recorder", () => {
  it("is self-contained: its serialized source installs both hook globals in a fresh realm", () => {
    const realm = pageRealm();
    const before = new Set(Object.getOwnPropertyNames(realm.context));
    realm.install();
    const added = Object.getOwnPropertyNames(realm.context).filter((name) => !before.has(name));
    expect(added.sort()).toEqual([...RECORDER_SYMBOLS].sort());
    expect(realm.context.__sarcostatTestEngine).toBe(realm.context.__sarcostatTestRecorder);
    for (const name of added) {
      expect(name.startsWith(HOOK_PREFIX), name).toBe(true);
    }
    expect(installEngineHook).toBe(installRecorder);
  });

  it("records the worker, every envelope posted and received with timestamps, and pairs them into calls", async () => {
    const realm = pageRealm();
    realm.install();
    const app = realm.start();
    app.request(1, "sys.identity", {});
    await tick();
    const hook = realm.hook();
    expect(hook.attached()).toBe(true);
    expect(hook.workers).toEqual([{ index: 0, url: `${PAGE}assets/py.worker-abc123.js`, type: "module", t: expect.any(Number) }]);
    expect(hook.messages.map((entry) => [entry.direction, entry.origin, entry.kind, entry.cmd, entry.id])).toEqual([
      ["posted", "app", "start", null, null],
      ["posted", "app", "request", "sys.identity", 1],
      ["received", "app", "boot", null, null],
      ["received", "app", "identity", null, null],
      ["received", "app", "response", null, 1],
    ]);
    expect(hook.messages.map((entry) => entry.seq)).toEqual([1, 2, 3, 4, 5]);
    const times = hook.messages.map((entry) => entry.t);
    expect([...times].sort((a, b) => a - b)).toEqual(times);
    expect(hook.records.map((entry) => [entry.direction, entry.kind, entry.cmd])).toEqual([
      ["posted", "start", null],
      ["posted", "request", "sys.identity"],
      ["received", "boot", null],
      ["received", "identity", null],
      ["received", "response", null],
    ]);
    const [call] = hook.calls();
    expect(call).toMatchObject({ origin: "app", worker: 0, id: 1, cmd: "sys.identity", args: {}, ok: true, error: null });
    expect(call?.ms).toBeGreaterThanOrEqual(0);
    expect(call?.result).toMatchObject({ cmd: "sys.identity" });
    expect(app.seen.map((data) => data.kind)).toEqual(["boot", "identity", "response"]);
  });

  it("sends test calls on the app's own engine worker without the app seeing their answers", async () => {
    const realm = pageRealm();
    realm.install();
    const app = realm.start();
    await tick();
    const hook = realm.hook();
    const bytes = new Uint8Array([1, 2, 3, 4, 5]);
    const answer = await hook.call("doc.export", { what: "results_json" }, [bytes]);
    expect(answer).toMatchObject({ ok: true, result: { cmd: "doc.export", args: { what: "results_json" }, sizes: [5] } });
    expect(String(answer.id).startsWith(TEST_ID_PREFIX)).toBe(true);
    const refused = await hook.call("fail", {});
    expect(refused).toMatchObject({ ok: false, error: { code: "refused" } });
    expect(app.seen.filter((data) => data.kind === "response")).toEqual([]);
    expect(hook.records.filter((entry) => entry.direction === "posted").map((entry) => entry.kind)).toEqual(["start"]);
    const tests = hook.calls().filter((entry) => entry.origin === "test");
    expect(tests.map((entry) => [entry.cmd, entry.ok])).toEqual([
      ["doc.export", true],
      ["fail", false],
    ]);
    const posted = hook.messages.find((entry) => entry.origin === "test" && entry.direction === "posted");
    expect(posted?.buffers).toEqual([5]);
    expect(posted?.data).toMatchObject({ buffers: [{ $bytes: 5 }] });
    hook.clear();
    expect(hook.messages).toEqual([]);
    expect(hook.records).toEqual([]);
    expect(hook.workers).toHaveLength(1);
  });

  it("targets the py.worker among several workers and times out when it never answers", async () => {
    const realm = pageRealm();
    realm.install();
    realm.start("assets/py.worker-abc.js");
    realm.start("assets/decode.worker-def.js");
    await tick();
    const answer = await realm.hook().call("sys.identity", {});
    expect((answer.result as { worker: string }).worker).toContain("py.worker-abc.js");
    const silent = pageRealm();
    silent.install();
    silent.start("assets/py.worker-silent.js");
    await expect(silent.hook().call("sys.identity", {}, [], 30)).rejects.toThrow(/no answer within 30 ms/);
    const empty = pageRealm();
    empty.install();
    await expect(empty.hook().call("sys.identity", {})).rejects.toThrow(/no engine worker yet/);
  });

  it("alters the identity the app sees, never the recorded one, and merges the config of a second install", async () => {
    const realm = pageRealm();
    realm.install();
    realm.install({ alterIdentity: { bridge_version: 2 } }, installEngineHook);
    expect(realm.hook().config).toEqual({ alterIdentity: { bridge_version: 2 }, terminateOnFirstRequest: false });
    const app = realm.start();
    await tick();
    const identity = app.seen.find((data) => data.kind === "identity");
    expect(identity?.identity?.bridge_version).toBe(2);
    const recorded = realm.hook().messages.find((entry) => entry.kind === "identity");
    expect((recorded?.data as { identity: { bridge_version: number } }).identity.bridge_version).toBe(1);
    expect(realm.hook().messages.filter((entry) => entry.kind === "start")).toHaveLength(1);
  });

  it("terminates the engine worker on its first request once per tab session", async () => {
    const storage = memoryStorage();
    const first = pageRealm(storage);
    first.install({ terminateOnFirstRequest: true });
    const app = first.start();
    app.request(1, "sys.identity", {});
    expect(app.worker.terminated).toBe(true);
    const reloaded = pageRealm(storage);
    reloaded.install({ terminateOnFirstRequest: true });
    const again = reloaded.start();
    again.request(1, "sys.identity", {});
    expect(again.worker.terminated).toBe(false);
  });
});

describe("injected in-worker probe", () => {
  function workerRealm(): { context: vm.Context; target: EventTarget; posted: unknown[] } {
    const target = new EventTarget();
    const posted: unknown[] = [];
    const xhr = function XMLHttpRequest(): void {};
    Object.defineProperty(xhr.prototype, "open", { value: () => undefined, writable: false, configurable: false });
    const context = vm.createContext({
      performance,
      location: { href: `${PAGE}assets/py.worker-abc.js`, origin: "http://127.0.0.1:4173" },
      addEventListener: target.addEventListener.bind(target),
      postMessage: (message: unknown) => void posted.push(message),
      XMLHttpRequest: xhr,
    });
    return { context, target, posted };
  }

  it("is self-contained and records the envelopes the worker receives and posts after it attaches", () => {
    const { context, target, posted } = workerRealm();
    const before = new Set(Object.getOwnPropertyNames(context));
    const first = vm.runInContext(`(${installWorkerProbe.toString()})()`, context) as WorkerProbeState;
    expect(first.href).toBe(`${PAGE}assets/py.worker-abc.js`);
    expect(first.pinned["XMLHttpRequest.open"]).toBe(true);
    expect(first.pinned.WebSocket).toBe(false);
    const added = Object.getOwnPropertyNames(context).filter((name) => !before.has(name));
    expect(added).toEqual([...PROBE_SYMBOLS]);
    target.dispatchEvent(new MessageEvent("message", { data: { kind: "request", request: { v: 1, id: 7, cmd: "results.get", args: {} }, buffers: [] } }));
    (context.postMessage as (message: unknown) => void)({ kind: "response", response: { v: 1, id: 7, ok: true, result: {} }, buffers: [] });
    const again = vm.runInContext(`(${installWorkerProbe.toString()})()`, context) as WorkerProbeState;
    expect(again.attachedAt).toBe(first.attachedAt);
    expect(again.inbox.map((entry) => [entry.kind, entry.cmd, entry.id])).toEqual([["request", "results.get", 7]]);
    expect(again.outbox.map((entry) => [entry.kind, entry.id, entry.ok])).toEqual([["response", 7, true]]);
    expect(posted).toHaveLength(1);
  });
});

describe("hook files", () => {
  it("each export a HOOK_SYMBOLS list that check_site.mjs reads, every name with the test prefix", () => {
    const scanned = hookSymbols(HOOKS_DIR);
    expect(scanned.notes).toEqual([]);
    const files = fs.readdirSync(HOOKS_DIR).filter((name) => name.endsWith(".ts")).sort();
    expect(scanned.files.map((file: string) => path.basename(file)).sort()).toEqual(files);
    for (const name of [...RECORDER_SYMBOLS, ...ENGINE_SYMBOLS, ...PROBE_SYMBOLS]) {
      expect(scanned.symbols).toContain(name);
    }
    for (const name of scanned.symbols) {
      expect(name.startsWith(HOOK_PREFIX), name).toBe(true);
    }
  });
});
