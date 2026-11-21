import "./guard.ts";

import type { loadPyodide as LoadPyodide, PyodideInterface } from "pyodide";

import { sha256Hex } from "./digest.ts";
import {
  BRIDGE_VERSION,
  isToWorker,
  transferList,
  type BuildInfo,
  type FatalCode,
  type FromWorker,
  type Identity,
  type RequestEnvelope,
  type ResponseEnvelope,
  type ToWorker,
} from "./protocol.ts";

interface WorkerScope {
  location: { href: string; origin: string };
  postMessage(message: FromWorker, transfer: Transferable[]): void;
  addEventListener(type: "message", listener: (event: MessageEvent<unknown>) => void): void;
}

interface PyodideLoader {
  loadPyodide: typeof LoadPyodide;
}

type PyCall = ((request: string, buffers: Uint8Array[]) => { toJs(options: object): unknown; destroy(): void }) & {
  destroy(): void;
};

const scope = globalThis as unknown as WorkerScope;
const LOADER_FILE = "pyodide.mjs";
const LOCK_FILE = "pyodide-lock.json";
const SCIPY_COMMAND = "sys.load_scipy";
const ENGINE_GLUE = [
  "from sarcostat.engine.bridge import dispatch as _sarcostat_dispatch",
  "def _sarcostat_call(request_json, js_buffers):",
  "    text, out = _sarcostat_dispatch(request_json, [b.to_bytes() for b in js_buffers])",
  "    return [text, out]",
  "_sarcostat_call",
].join("\n");

let started = false;
let failed = false;
let engine: { py: PyodideInterface; call: PyCall } | null = null;
let queue: Promise<void> = Promise.resolve();
const pendingBeforeReady: { request: RequestEnvelope; buffers: Uint8Array[] }[] = [];
const runtimeErrors: string[] = [];

function post(message: FromWorker, transfer: Transferable[] = []): void {
  scope.postMessage(message, transfer);
}

function fail(code: FatalCode, message: string): void {
  if (failed) {
    return;
  }
  failed = true;
  post({ kind: "fatal", code, message });
  for (const pending of pendingBeforeReady.splice(0)) {
    refuse(pending.request, message);
  }
}

function refuse(request: RequestEnvelope, why: string): void {
  post({
    kind: "response",
    response: { v: BRIDGE_VERSION, id: request.id, ok: false, error: { code: "refused", message: `the engine is not available: ${why}` } },
    buffers: [],
  });
}

function errorText(error: unknown): string {
  const raw = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
  return raw.split(/\s+/).join(" ").slice(0, 500);
}

function sameOriginUrl(path: string, base: string): string {
  const url = new URL(path, base);
  if (url.origin !== scope.location.origin) {
    throw new TypeError(`${url.href} is not on this site`);
  }
  return url.href;
}

async function importLoader(url: string): Promise<PyodideLoader> {
  return (await import(url)) as PyodideLoader;
}

async function fetchWheel(url: string): Promise<{ bytes: Uint8Array; sha256: string }> {
  const response = await fetch(url, { cache: "no-store", credentials: "same-origin" });
  if (!response.ok) {
    throw new Error(`the wheel ${url} answered ${response.status}`);
  }
  const bytes = new Uint8Array(await response.arrayBuffer());
  return { bytes, sha256: await sha256Hex(bytes) };
}

async function boot(build: BuildInfo, base: string): Promise<void> {
  const pyodideDir = sameOriginUrl(build.pyodide.dir, base);
  const wheelUrl = sameOriginUrl(build.wheel.file, base);
  post({ kind: "boot", stage: "runtime" });
  const wheel = fetchWheel(wheelUrl);
  const runtime = importLoader(sameOriginUrl(LOADER_FILE, pyodideDir)).then((loader) =>
    loader.loadPyodide({
      indexURL: pyodideDir,
      lockFileURL: sameOriginUrl(LOCK_FILE, pyodideDir),
      packageBaseUrl: pyodideDir,
      env: { HOME: "/home/pyodide" },
      stdout: () => {},
      stderr: (line: string) => {
        runtimeErrors.push(line);
      },
    }),
  );
  wheel.catch(() => {});
  runtime.catch(() => {});
  const checked = await wheel;
  if (checked.sha256 !== build.wheel.sha256) {
    fail("build_damaged", `${build.wheel.file}: SHA-256 ${checked.sha256}, BUILD.json says ${build.wheel.sha256}`);
    return;
  }
  const py = await runtime;
  if (failed) {
    return;
  }
  post({ kind: "boot", stage: "numpy" });
  await py.loadPackage(["numpy"], {
    checkIntegrity: true,
    messageCallback: () => {},
    errorCallback: (line: string) => {
      runtimeErrors.push(line);
    },
  });
  post({ kind: "boot", stage: "wheel" });
  const sitePackages = String(py.runPython("import sysconfig; sysconfig.get_paths()['purelib']"));
  py.unpackArchive(checked.bytes, "wheel", { extractDir: sitePackages });
  post({ kind: "boot", stage: "engine" });
  const call = py.runPython(ENGINE_GLUE) as PyCall;
  engine = { py, call };
  const reply = dispatch({ v: BRIDGE_VERSION, id: "boot-identity", cmd: "sys.identity", args: {} }, []);
  const response = reply.response;
  if (!response.ok) {
    fail("boot_failed", `sys.identity: ${response.error.message}`);
    return;
  }
  const result = response.result as Omit<Identity, "pyodide_version" | "wheel_sha256">;
  post({
    kind: "identity",
    identity: {
      sarcostat_version: result.sarcostat_version,
      source_digest: result.source_digest,
      bridge_version: result.bridge_version,
      pyodide_version: py.version,
      wheel_sha256: checked.sha256,
    },
  });
  for (const pending of pendingBeforeReady.splice(0)) {
    enqueue(pending.request, pending.buffers);
  }
}

function dispatch(request: RequestEnvelope, buffers: Uint8Array[]): { response: ResponseEnvelope; buffers: Uint8Array[] } {
  if (engine === null) {
    throw new Error("the engine is not loaded");
  }
  const proxy = engine.call(JSON.stringify(request), buffers);
  try {
    const [text, out] = proxy.toJs({ create_pyproxies: false }) as [string, Uint8Array[]];
    const own = out.map((buffer) => (buffer.byteOffset === 0 && buffer.byteLength === buffer.buffer.byteLength ? buffer : buffer.slice()));
    return { response: JSON.parse(text), buffers: own };
  } finally {
    proxy.destroy();
  }
}

async function handle(request: RequestEnvelope, buffers: Uint8Array[]): Promise<void> {
  if (failed || engine === null) {
    refuse(request, "it stopped");
    return;
  }
  try {
    if (request.cmd === SCIPY_COMMAND) {
      await engine.py.loadPackage(["scipy"], {
        checkIntegrity: true,
        messageCallback: () => {},
        errorCallback: (line: string) => {
          runtimeErrors.push(line);
        },
      });
    }
    const reply = dispatch(request, buffers);
    post({ kind: "response", response: reply.response, buffers: reply.buffers }, transferList(reply.buffers));
  } catch (error) {
    fail("worker_died", `${request.cmd}: ${errorText(error)}`);
  }
}

function enqueue(request: RequestEnvelope, buffers: Uint8Array[]): void {
  queue = queue.then(() => handle(request, buffers));
}

function onMessage(message: ToWorker): void {
  if (message.kind === "start") {
    if (started) {
      return;
    }
    started = true;
    boot(message.build, message.base).catch((error: unknown) => {
      const detail = runtimeErrors.length > 0 ? ` (${runtimeErrors.slice(-3).join(" | ")})` : "";
      fail("boot_failed", `${errorText(error)}${detail}`);
    });
    return;
  }
  if (failed) {
    refuse(message.request, "it stopped");
  } else if (engine === null) {
    pendingBeforeReady.push({ request: message.request, buffers: message.buffers });
  } else {
    enqueue(message.request, message.buffers);
  }
}

scope.addEventListener("message", (event) => {
  if (isToWorker(event.data)) {
    onMessage(event.data);
  }
});
