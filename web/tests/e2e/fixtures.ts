import { expect, test as base, type Page, type Request, type Worker } from "@playwright/test";

import { HANDSHAKE_COMMAND } from "../../src/worker/protocol.ts";
import {
  installRecorder,
  type EngineResponse,
  type RecordedCall,
  type RecordedMessage,
  type RecordedWorker,
  type RecorderConfig,
  type RecorderHook,
} from "./hooks/recorder.ts";
import { installWorkerProbe, type WorkerProbeState } from "./hooks/probe.ts";

export const LOCAL_HOST = "127.0.0.1";
export const ENGINE_WORKER_URL = /py\.worker/;

export function isLocalUrl(raw: string): boolean {
  if (raw.startsWith("data:") || raw.startsWith("blob:") || raw === "about:blank") {
    return true;
  }
  try {
    return new URL(raw).hostname === LOCAL_HOST;
  } catch {
    return false;
  }
}

export interface GuardLog {
  external: string[];
  expectedExternal: string[];
  consoleErrors: string[];
  pageErrors: string[];
  allow: { consoleError: RegExp[]; external: RegExp[] };
}

function logExternal(log: GuardLog, line: string, url: string): void {
  const target = log.allow.external.some((re) => re.test(url)) ? log.expectedExternal : log.external;
  if (!target.includes(line)) {
    target.push(line);
  }
}

function requestSource(request: Request): string {
  const worker = request.serviceWorker();
  if (worker) {
    return "service worker";
  }
  try {
    return request.frame() ? "page" : "worker";
  } catch {
    return "worker";
  }
}

async function installGuards(page: Page, log: GuardLog): Promise<void> {
  await page.context().route("**/*", async (route) => {
    const url = route.request().url();
    if (isLocalUrl(url)) {
      await route.continue();
      return;
    }
    logExternal(log, `${requestSource(route.request())} ${route.request().method()} ${url}`, url);
    await route.abort("blockedbyclient");
  });
  page.on("request", (request) => {
    if (!isLocalUrl(request.url())) {
      logExternal(log, `${requestSource(request)} ${request.method()} ${request.url()}`, request.url());
    }
  });
  page.on("console", (message) => {
    if (message.type() === "error") {
      const text = message.text();
      if (!log.allow.consoleError.some((re) => re.test(text))) {
        log.consoleErrors.push(text);
      }
    }
  });
  page.on("pageerror", (error) => {
    log.pageErrors.push(String(error?.stack ?? error));
  });
}

export function assertGuardClean(log: GuardLog): void {
  expect(log.external, "requests to hosts other than 127.0.0.1").toEqual([]);
  expect(log.consoleErrors, "console errors").toEqual([]);
  expect(log.pageErrors, "uncaught page errors").toEqual([]);
}

export class EngineCallError extends Error {
  readonly cmd: string;
  readonly code: string;

  constructor(cmd: string, code: string, message: string) {
    super(`${cmd}: ${code}: ${message}`);
    this.name = "EngineCallError";
    this.cmd = cmd;
    this.code = code;
  }
}

export interface Recorder {
  configure(config: Partial<RecorderConfig>): Promise<void>;
  messages(): Promise<RecordedMessage[]>;
  calls(origin?: "app" | "test"): Promise<RecordedCall[]>;
  workers(): Promise<RecordedWorker[]>;
  clear(): Promise<void>;
}

export interface Engine {
  ready(timeoutMs?: number): Promise<RecordedCall>;
  send(cmd: string, args: Record<string, unknown>, timeoutMs?: number): Promise<EngineResponse>;
  call<T = unknown>(cmd: string, args: Record<string, unknown>, timeoutMs?: number): Promise<T>;
}

export interface WorkerProbe {
  workers(): Worker[];
  engineWorker(timeoutMs?: number): Promise<Worker>;
  state(worker?: Worker): Promise<WorkerProbeState>;
}

type RecorderGlobal = { __sarcostatTestRecorder?: RecorderHook };

function makeRecorder(page: Page): Recorder {
  return {
    async configure(config) {
      await page.addInitScript(installRecorder, config);
      await page.evaluate((next) => {
        const hook = (globalThis as unknown as RecorderGlobal).__sarcostatTestRecorder;
        if (hook) {
          Object.assign(hook.config, next);
        }
      }, config);
    },
    messages: () => page.evaluate(() => (globalThis as unknown as RecorderGlobal).__sarcostatTestRecorder?.messages.slice() ?? []),
    calls: async (origin) => {
      const all = await page.evaluate(() => (globalThis as unknown as RecorderGlobal).__sarcostatTestRecorder?.calls() ?? []);
      return origin === undefined ? all : all.filter((call) => call.origin === origin);
    },
    workers: () => page.evaluate(() => (globalThis as unknown as RecorderGlobal).__sarcostatTestRecorder?.workers.slice() ?? []),
    clear: () => page.evaluate(() => (globalThis as unknown as RecorderGlobal).__sarcostatTestRecorder?.clear()),
  };
}

function makeEngine(page: Page): Engine {
  const send = (cmd: string, args: Record<string, unknown>, timeoutMs = 30_000): Promise<EngineResponse> =>
    page.evaluate(
      ([name, body, limit]) => {
        const hook = (globalThis as unknown as RecorderGlobal).__sarcostatTestRecorder;
        if (!hook) {
          throw new Error("the recorder is not installed in this page");
        }
        return hook.call(name, body, [], limit);
      },
      [cmd, args, timeoutMs] as const,
    );
  return {
    async ready(timeoutMs = 30_000) {
      const deadline = Date.now() + timeoutMs;
      let state = "no recorder";
      while (Date.now() < deadline) {
        const seen = await page.evaluate((handshake) => {
          const hook = (globalThis as unknown as RecorderGlobal).__sarcostatTestRecorder;
          if (!hook) {
            return { state: "no recorder", call: null };
          }
          const fatal = hook.messages.find((entry) => entry.direction === "received" && entry.kind === "fatal");
          if (fatal) {
            return { state: `fatal ${JSON.stringify(fatal.data)}`, call: null };
          }
          const call = hook.calls().find((entry) => entry.origin === "app" && entry.cmd === handshake && entry.received !== null) ?? null;
          return { state: call === null ? "booting" : call.ok ? "ready" : `handshake refused ${JSON.stringify(call.error)}`, call };
        }, HANDSHAKE_COMMAND);
        state = seen.state;
        if (seen.call !== null && state === "ready") {
          return seen.call;
        }
        if (state.startsWith("fatal") || state.startsWith("handshake refused")) {
          break;
        }
        await page.waitForTimeout(50);
      }
      throw new Error(`the app's engine did not answer its ${HANDSHAKE_COMMAND} handshake: ${state}`);
    },
    send,
    async call<T>(cmd: string, args: Record<string, unknown>, timeoutMs?: number): Promise<T> {
      const response = await send(cmd, args, timeoutMs);
      if (!response.ok) {
        throw new EngineCallError(cmd, response.error?.code ?? "unknown", response.error?.message ?? "no message");
      }
      return response.result as T;
    },
  };
}

interface HarnessFixtures {
  guard: GuardLog;
  recorderConfig: Partial<RecorderConfig>;
  recorder: Recorder;
  engine: Engine;
  workerProbe: WorkerProbe;
}

export const test = base.extend<HarnessFixtures>({
  recorderConfig: [{}, { option: true }],
  guard: [
    async ({ page }, use) => {
      const log: GuardLog = {
        external: [],
        expectedExternal: [],
        consoleErrors: [],
        pageErrors: [],
        allow: { consoleError: [], external: [] },
      };
      await installGuards(page, log);
      await use(log);
      assertGuardClean(log);
    },
    { auto: true },
  ],
  recorder: async ({ page, recorderConfig }, use) => {
    await page.addInitScript(installRecorder, recorderConfig);
    await use(makeRecorder(page));
  },
  engine: async ({ page, recorder }, use) => {
    void recorder;
    await use(makeEngine(page));
  },
  workerProbe: async ({ page }, use) => {
    const seen: Worker[] = [];
    const installs: Promise<unknown>[] = [];
    const onWorker = (worker: Worker): void => {
      seen.push(worker);
      installs.push(worker.evaluate(installWorkerProbe).catch(() => null));
    };
    page.on("worker", onWorker);
    const engineWorker = async (timeoutMs = 30_000): Promise<Worker> => {
      const deadline = Date.now() + timeoutMs;
      while (Date.now() < deadline) {
        const found = [...seen].reverse().find((worker) => ENGINE_WORKER_URL.test(worker.url()));
        if (found) {
          await found.evaluate(installWorkerProbe);
          return found;
        }
        await page.waitForTimeout(50);
      }
      throw new Error(`no engine worker (${ENGINE_WORKER_URL}) among ${JSON.stringify(seen.map((worker) => worker.url()))}`);
    };
    await use({
      workers: () => seen.slice(),
      engineWorker,
      state: async (worker) => (worker ?? (await engineWorker())).evaluate(installWorkerProbe),
    });
    page.off("worker", onWorker);
    await Promise.allSettled(installs);
  },
});

export { expect };
