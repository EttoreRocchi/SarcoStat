export const HOOK_SYMBOLS = ["__sarcostatTestProbe"] as const;
export const PROBE_HOST = "example.org";

export type ProbeResult = Record<"fetch" | "xhr" | "websocket" | "eventsource" | "worker", string>;

export interface ProbeMessage {
  t: number;
  kind: string;
  cmd: string | null;
  id: string | number | null;
  ok: boolean | null;
}

export interface WorkerProbeState {
  href: string;
  origin: string;
  attachedAt: number;
  pinned: Record<"fetch" | "XMLHttpRequest.open" | "WebSocket" | "EventSource" | "Worker", boolean>;
  inbox: ProbeMessage[];
  outbox: ProbeMessage[];
}

export function installWorkerProbe(): WorkerProbeState {
  type Scope = Record<string, unknown> & {
    location: { href: string; origin: string };
    postMessage: (...args: unknown[]) => void;
    addEventListener: (type: string, listener: (event: MessageEvent) => void) => void;
  };
  type Probe = { state(): WorkerProbeState };
  const scope = globalThis as unknown as Scope;
  const present = scope.__sarcostatTestProbe as Probe | undefined;
  if (present !== undefined) {
    return present.state();
  }
  const attachedAt = performance.now();
  const inbox: ProbeMessage[] = [];
  const outbox: ProbeMessage[] = [];
  const summary = (raw: unknown): ProbeMessage => {
    const data = (raw !== null && typeof raw === "object" ? raw : {}) as {
      kind?: unknown;
      request?: { id?: unknown; cmd?: unknown };
      response?: { id?: unknown; ok?: unknown };
    };
    const id = data.request?.id ?? data.response?.id ?? null;
    return {
      t: performance.now(),
      kind: typeof data.kind === "string" ? data.kind : typeof raw,
      cmd: typeof data.request?.cmd === "string" ? data.request.cmd : null,
      id: typeof id === "string" || typeof id === "number" ? id : null,
      ok: typeof data.response?.ok === "boolean" ? data.response.ok : null,
    };
  };
  const pinned = (owner: unknown, name: string): boolean => {
    if (owner === null || owner === undefined) {
      return false;
    }
    const descriptor = Object.getOwnPropertyDescriptor(owner, name);
    return descriptor !== undefined && descriptor.writable === false && descriptor.configurable === false;
  };
  const nativePost = scope.postMessage;
  scope.addEventListener("message", (event) => {
    inbox.push(summary(event.data));
  });
  scope.postMessage = function postMessage(this: unknown, ...args: unknown[]): void {
    outbox.push(summary(args[0]));
    nativePost.apply(this ?? scope, args);
  };
  const probe: Probe = {
    state: () => ({
      href: scope.location.href,
      origin: scope.location.origin,
      attachedAt,
      pinned: {
        fetch: pinned(scope, "fetch"),
        "XMLHttpRequest.open": pinned((scope.XMLHttpRequest as { prototype?: unknown } | undefined)?.prototype, "open"),
        WebSocket: pinned(scope, "WebSocket"),
        EventSource: pinned(scope, "EventSource"),
        Worker: pinned(scope, "Worker"),
      },
      inbox: inbox.slice(),
      outbox: outbox.slice(),
    }),
  };
  Object.defineProperty(scope, "__sarcostatTestProbe", { value: probe, configurable: false, writable: false });
  return probe.state();
}

export async function probeWorkerChannels(host: string): Promise<ProbeResult> {
  const describe = (error: unknown): string => (error instanceof Error ? error.message : String(error));
  const out: Record<string, string> = {};
  try {
    await fetch(`https://${host}/probe-fetch`);
    out.fetch = "allowed";
  } catch (error) {
    out.fetch = describe(error);
  }
  try {
    const request = new XMLHttpRequest();
    request.open("GET", `https://${host}/probe-xhr`);
    request.send();
    out.xhr = "allowed";
  } catch (error) {
    out.xhr = describe(error);
  }
  try {
    const socket = new WebSocket(`wss://${host}/probe-websocket`);
    socket.close();
    out.websocket = "allowed";
  } catch (error) {
    out.websocket = describe(error);
  }
  try {
    const source = new EventSource(`https://${host}/probe-eventsource`);
    source.close();
    out.eventsource = "allowed";
  } catch (error) {
    out.eventsource = describe(error);
  }
  try {
    const nested = new Worker(`https://${host}/probe-worker.js`, { type: "module" });
    nested.terminate();
    out.worker = "allowed";
  } catch (error) {
    out.worker = describe(error);
  }
  return out as ProbeResult;
}

export function dynamicImportExpression(url: string): string {
  return `import(${JSON.stringify(url)}).then(() => "loaded", (error) => String(error && error.message ? error.message : error))`;
}

export function dynamicImportProbe(host: string): string {
  return dynamicImportExpression(`https://${host}/probe-import.mjs`);
}
