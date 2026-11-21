import { describe, expect, it } from "vitest";

import { GUARD_MESSAGE, GUARDED_CONSTRUCTORS, installGuard, isSameOrigin, type GuardScope } from "../../src/worker/guard.ts";

const ORIGIN = "http://127.0.0.1:4173";
const LOCATION = { href: `${ORIGIN}/v0.2.0rc1.dev0/assets/py.worker-abc.js`, origin: ORIGIN };
const SAME = [
  "../pyodide/pyodide.mjs",
  "/v0.2.0rc1.dev0/BUILD.json",
  `${ORIGIN}/v0.2.0rc1.dev0/pyodide/pyodide.asm.wasm`,
  new URL(`${ORIGIN}/v0.2.0rc1.dev0/wheels/x.whl`),
  `blob:${ORIGIN}/2f1c7a7e-0000-4000-8000-000000000000`,
];
const CROSS = [
  "https://example.org/x",
  "//example.org/x",
  "http://127.0.0.1:4174/x",
  "https://127.0.0.1:4173/x",
  "http://localhost:4173/x",
  "wss://example.org/socket",
  "data:text/javascript,1",
  new URL("https://cdn.jsdelivr.net/pyodide/v314.0.8/full/pyodide.asm.wasm"),
];

class Recorder {
  static calls: string[] = [];
}

function fakeXhr() {
  return class {
    open(method: string, url: string | URL): void {
      Recorder.calls.push(`xhr ${method} ${String(url)}`);
    }
  };
}

type FakeXhr = ReturnType<typeof fakeXhr>;

function fakeConstructor(name: string) {
  return class {
    static readonly OPEN = 1;
    readonly url: string;
    constructor(url: string | URL) {
      this.url = String(url);
      Recorder.calls.push(`${name} ${this.url}`);
    }
  };
}

function fakeScope(): GuardScope & Record<string, unknown> {
  Recorder.calls = [];
  return {
    location: { ...LOCATION },
    fetch: async (input: unknown) => {
      Recorder.calls.push(`fetch ${input instanceof URL ? input.href : typeof input === "object" && input !== null && "url" in input ? String(input.url) : String(input)}`);
      return "answer";
    },
    XMLHttpRequest: fakeXhr(),
    WebSocket: fakeConstructor("WebSocket"),
    EventSource: fakeConstructor("EventSource"),
    Worker: fakeConstructor("Worker"),
    WebTransport: fakeConstructor("WebTransport"),
  };
}

type Ctor = new (url: unknown) => { url: string };

describe("worker guard", () => {
  it("tells same-origin URLs from cross-origin ones, relative to the worker's location", () => {
    for (const url of SAME) {
      expect(isSameOrigin(url, LOCATION), String(url)).toBe(true);
    }
    for (const url of CROSS) {
      expect(isSameOrigin(url, LOCATION), String(url)).toBe(false);
    }
    expect(isSameOrigin({ url: `${ORIGIN}/a` }, LOCATION)).toBe(true);
    expect(isSameOrigin({ url: "https://example.org/a" }, LOCATION)).toBe(false);
    expect(isSameOrigin("http://[bad", LOCATION)).toBe(false);
  });

  it("lets same-origin fetches through and refuses cross-origin ones before they start", async () => {
    const scope = fakeScope();
    installGuard(scope);
    const fetch = scope.fetch as (input: unknown) => Promise<unknown>;
    for (const url of SAME) {
      await expect(fetch(url)).resolves.toBe("answer");
    }
    await expect(fetch({ url: `${ORIGIN}/x` })).resolves.toBe("answer");
    const before = Recorder.calls.length;
    for (const url of CROSS) {
      await expect(fetch(url)).rejects.toThrow(`${GUARD_MESSAGE} fetch to`);
    }
    await expect(fetch({ url: "https://example.org/request" })).rejects.toThrow("https://example.org/request");
    expect(Recorder.calls.length).toBe(before);
  });

  it("refuses a cross-origin XMLHttpRequest at open()", () => {
    const scope = fakeScope();
    installGuard(scope);
    const Xhr = scope.XMLHttpRequest as FakeXhr;
    new Xhr().open("GET", "../pyodide/pyodide-lock.json");
    expect(Recorder.calls).toEqual([`xhr GET ../pyodide/pyodide-lock.json`]);
    expect(() => new Xhr().open("GET", "https://example.org/x")).toThrow(`${GUARD_MESSAGE} XMLHttpRequest to https://example.org/x`);
    expect(Recorder.calls).toHaveLength(1);
  });

  it.each(GUARDED_CONSTRUCTORS)("refuses a cross-origin %s and keeps the constructor otherwise intact", (name) => {
    const scope = fakeScope();
    const Original = scope[name] as Ctor & { OPEN: number };
    installGuard(scope);
    const Guarded = scope[name] as Ctor & { OPEN: number };
    expect(Guarded).not.toBe(Original);
    const local = new Guarded(`${ORIGIN}/v0.2.0rc1.dev0/x`);
    expect(local).toBeInstanceOf(Original);
    expect(local).toBeInstanceOf(Guarded);
    expect(Guarded.OPEN).toBe(1);
    expect(() => new Guarded("https://example.org/x")).toThrow(`${GUARD_MESSAGE} ${name} to https://example.org/x`);
    expect(Recorder.calls).toEqual([`${name} ${ORIGIN}/v0.2.0rc1.dev0/x`]);
  });

  it("cannot be undone by later code", () => {
    const scope = fakeScope();
    installGuard(scope);
    const guarded = scope.fetch;
    expect(() => {
      scope.fetch = async () => "bypass";
    }).toThrow(TypeError);
    expect(scope.fetch).toBe(guarded);
    expect(() => Object.defineProperty(scope, "WebSocket", { value: null })).toThrow(TypeError);
    const Xhr = scope.XMLHttpRequest as FakeXhr;
    expect(() => Object.defineProperty(Xhr.prototype, "open", { value: () => {} })).toThrow(TypeError);
  });

  it("skips channels the scope does not have", () => {
    const scope: GuardScope = { location: { ...LOCATION } };
    expect(() => installGuard(scope)).not.toThrow();
    expect(Object.keys(scope)).toEqual(["location"]);
  });

  it("does not install itself outside a worker", () => {
    expect("WorkerGlobalScope" in globalThis).toBe(false);
    expect(Object.getOwnPropertyDescriptor(globalThis, "fetch")?.writable).toBe(true);
  });
});
