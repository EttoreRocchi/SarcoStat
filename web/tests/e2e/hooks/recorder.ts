export const HOOK_SYMBOLS = ["__sarcostatTestRecorder", "__sarcostatTestEngine"] as const;

export const TEST_ID_PREFIX = "__sarcostatTest-";

export interface RecorderConfig {
  alterIdentity: Record<string, unknown> | null;
  terminateOnFirstRequest: boolean;
}

export type EngineHookConfig = RecorderConfig;

export type Direction = "posted" | "received";
export type Origin = "app" | "test";

export interface EngineRecord {
  direction: Direction;
  kind: string;
  cmd: string | null;
  id: string | number | null;
  ok: boolean | null;
  t: number;
}

export interface EngineResponse {
  v: number;
  id: string | number | null;
  ok: boolean;
  result?: unknown;
  error?: { code: string; message: string };
}

export interface RecordedWorker {
  index: number;
  url: string;
  type: string;
  t: number;
}

export interface RecordedMessage extends EngineRecord {
  seq: number;
  origin: Origin;
  worker: number;
  data: unknown;
  buffers: number[];
}

export interface RecordedCall {
  origin: Origin;
  worker: number;
  id: string | number;
  cmd: string;
  args: unknown;
  posted: number;
  received: number | null;
  ms: number | null;
  ok: boolean | null;
  result: unknown;
  error: { code: string; message: string } | null;
}

export interface EngineHook {
  records: EngineRecord[];
  attached(): boolean;
  call(cmd: string, args: Record<string, unknown>, buffers?: Uint8Array[], timeoutMs?: number): Promise<EngineResponse>;
  terminate(): void;
}

export interface RecorderHook extends EngineHook {
  config: RecorderConfig;
  workers: RecordedWorker[];
  messages: RecordedMessage[];
  calls(): RecordedCall[];
  clear(): void;
}

export function installRecorder(options: Partial<RecorderConfig> = {}): void {
  const scope = globalThis as unknown as Record<string, unknown>;
  const present = scope.__sarcostatTestRecorder as RecorderHook | undefined;
  if (present !== undefined) {
    Object.assign(present.config, options);
    return;
  }
  const config: RecorderConfig = { alterIdentity: null, terminateOnFirstRequest: false, ...options };
  const testPrefix = "__sarcostatTest-";
  const onceKey = "__sarcostatTestTerminated";
  const NativeWorker = scope.Worker as typeof Worker;
  const proto = NativeWorker.prototype;
  const nativePost = proto.postMessage as (this: Worker, ...rest: unknown[]) => void;
  const nativeAdd = EventTarget.prototype.addEventListener;
  const tracked = new WeakMap<object, number>();
  const instances: Worker[] = [];
  const workers: RecordedWorker[] = [];
  const messages: RecordedMessage[] = [];
  const records: EngineRecord[] = [];
  const waiting = new Map<string, (response: EngineResponse) => void>();
  let seq = 0;
  let counter = 0;

  type Envelope = {
    kind?: unknown;
    request?: { id?: unknown; cmd?: unknown; args?: unknown };
    response?: EngineResponse;
    identity?: Record<string, unknown>;
    buffers?: unknown;
  };

  const plain = (value: unknown, depth: number): unknown => {
    if (typeof value === "function") {
      return "[function]";
    }
    if (value === null || typeof value !== "object") {
      return value;
    }
    if (ArrayBuffer.isView(value) || value instanceof ArrayBuffer) {
      return { $bytes: value.byteLength };
    }
    if (depth > 64) {
      return "[too deep]";
    }
    if (Array.isArray(value)) {
      return value.map((item) => plain(item, depth + 1));
    }
    const out: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value)) {
      out[key] = plain(item, depth + 1);
    }
    return out;
  };
  const envelope = (raw: unknown): Envelope => (raw !== null && typeof raw === "object" ? (raw as Envelope) : {});
  const idOf = (data: Envelope): string | number | null => {
    const id = data.request?.id ?? data.response?.id ?? null;
    return typeof id === "string" || typeof id === "number" ? id : null;
  };
  const bufferSizes = (data: Envelope): number[] =>
    Array.isArray(data.buffers) ? data.buffers.map((item: unknown) => (ArrayBuffer.isView(item) ? item.byteLength : 0)) : [];
  const record = (direction: Direction, origin: Origin, index: number, raw: unknown): void => {
    const data = envelope(raw);
    seq += 1;
    const entry: RecordedMessage = {
      seq,
      direction,
      origin,
      worker: index,
      kind: typeof data.kind === "string" ? data.kind : typeof raw,
      cmd: typeof data.request?.cmd === "string" ? data.request.cmd : null,
      id: idOf(data),
      ok: typeof data.response?.ok === "boolean" ? data.response.ok : null,
      t: performance.now(),
      data: plain(raw, 0),
      buffers: bufferSizes(data),
    };
    messages.push(entry);
    if (origin === "app") {
      records.push({ direction, kind: entry.kind, cmd: entry.cmd, id: entry.id, ok: entry.ok, t: entry.t });
    }
  };
  const isTestResponse = (raw: unknown): boolean => {
    const id = envelope(raw).response?.id;
    return typeof id === "string" && id.startsWith(testPrefix);
  };
  const describeUrl = (url: unknown): string => {
    if (url === undefined || url === null) {
      return "";
    }
    try {
      return new URL(String(url instanceof URL ? url.href : url), String((scope.location as Location | undefined)?.href ?? "")).href;
    } catch {
      return String(url);
    }
  };
  const track = (target: Worker, url: unknown, options: unknown): number => {
    const known = tracked.get(target);
    if (known !== undefined) {
      return known;
    }
    const index = workers.length;
    tracked.set(target, index);
    instances.push(target);
    const type = (options as { type?: unknown } | undefined)?.type;
    workers.push({ index, url: describeUrl(url), type: typeof type === "string" ? type : "classic", t: performance.now() });
    nativeAdd.call(target, "message", (event: Event) => {
      const raw = (event as MessageEvent).data;
      if (!isTestResponse(raw)) {
        record("received", "app", index, raw);
        return;
      }
      record("received", "test", index, raw);
      const response = envelope(raw).response as EngineResponse;
      const key = String(response.id);
      const resolve = waiting.get(key);
      waiting.delete(key);
      resolve?.(response);
    });
    return index;
  };
  const engineIndex = (): number => {
    for (let index = workers.length - 1; index >= 0; index -= 1) {
      if (/py\.worker/.test(workers[index]?.url ?? "")) {
        return index;
      }
    }
    return workers.length - 1;
  };

  const WorkerProxy: typeof Worker = new Proxy(NativeWorker, {
    construct(target, args, newTarget): object {
      const instance = Reflect.construct(target, args, newTarget === WorkerProxy ? target : newTarget) as Worker;
      track(instance, args[0], args[1]);
      return instance;
    },
  });
  Object.defineProperty(scope, "Worker", { value: WorkerProxy, writable: true, configurable: true, enumerable: false });

  proto.postMessage = function postMessage(this: Worker, ...args: unknown[]): void {
    const index = track(this, undefined, undefined);
    const data = envelope(args[0]);
    record("posted", "app", index, args[0]);
    nativePost.apply(this, args);
    if (config.terminateOnFirstRequest && data.kind === "request") {
      let done = false;
      try {
        done = sessionStorage.getItem(onceKey) === "1";
        sessionStorage.setItem(onceKey, "1");
      } catch {
        done = false;
      }
      if (!done) {
        this.terminate();
      }
    }
  } as typeof proto.postMessage;

  proto.addEventListener = function addEventListener(
    this: Worker,
    type: string,
    listener: EventListenerOrEventListenerObject | null,
    listenerOptions?: boolean | AddEventListenerOptions,
  ): void {
    if (type !== "message" || listener === null) {
      nativeAdd.call(this, type, listener, listenerOptions);
      return;
    }
    track(this, undefined, undefined);
    const deliver = (target: Worker, event: Event): void => {
      if (typeof listener === "function") {
        listener.call(target, event);
      } else {
        listener.handleEvent(event);
      }
    };
    const wrapped = function wrapped(this: Worker, event: Event): void {
      const data = envelope((event as MessageEvent).data);
      if (isTestResponse(data)) {
        return;
      }
      if (data.kind === "identity" && config.alterIdentity !== null) {
        const altered = { ...data, identity: { ...data.identity, ...config.alterIdentity } };
        deliver(this, new MessageEvent("message", { data: altered }));
        return;
      }
      deliver(this, event);
    };
    nativeAdd.call(this, type, wrapped, listenerOptions);
  } as typeof proto.addEventListener;

  const hook: RecorderHook = {
    config,
    records,
    workers,
    messages,
    attached: () => instances.length > 0,
    call(cmd, args, buffers = [], timeoutMs = 30_000) {
      const index = engineIndex();
      const target = instances[index];
      if (target === undefined) {
        return Promise.reject(new Error("no engine worker yet"));
      }
      counter += 1;
      const id = `${testPrefix}${counter}`;
      const message = { kind: "request", request: { v: 1, id, cmd, args }, buffers };
      const transfer = [...new Set(buffers.map((buffer) => buffer.buffer))];
      return new Promise<EngineResponse>((resolve, reject) => {
        const timer = setTimeout(() => {
          waiting.delete(id);
          reject(new Error(`${cmd}: the engine worker sent no answer within ${timeoutMs} ms`));
        }, timeoutMs);
        waiting.set(id, (response) => {
          clearTimeout(timer);
          resolve(response);
        });
        record("posted", "test", index, message);
        nativePost.call(target, message, transfer);
      });
    },
    terminate() {
      instances[engineIndex()]?.terminate();
    },
    calls() {
      const out: RecordedCall[] = [];
      const open = new Map<string, RecordedCall>();
      for (const entry of messages) {
        if (entry.id === null) {
          continue;
        }
        const key = `${entry.worker} ${String(entry.id)}`;
        const data = envelope(entry.data);
        if (entry.direction === "posted" && entry.kind === "request" && entry.cmd !== null) {
          const call: RecordedCall = {
            origin: entry.origin,
            worker: entry.worker,
            id: entry.id,
            cmd: entry.cmd,
            args: data.request?.args ?? null,
            posted: entry.t,
            received: null,
            ms: null,
            ok: null,
            result: null,
            error: null,
          };
          out.push(call);
          open.set(key, call);
        } else if (entry.direction === "received" && entry.kind === "response") {
          const call = open.get(key);
          if (call === undefined) {
            continue;
          }
          open.delete(key);
          call.received = entry.t;
          call.ms = entry.t - call.posted;
          call.ok = entry.ok;
          call.result = data.response?.ok ? (data.response.result ?? null) : null;
          call.error = data.response && !data.response.ok ? (data.response.error ?? null) : null;
        }
      }
      return out;
    },
    clear() {
      messages.length = 0;
      records.length = 0;
    },
  };
  Object.defineProperty(scope, "__sarcostatTestRecorder", { value: hook, configurable: false, writable: false });
  Object.defineProperty(scope, "__sarcostatTestEngine", { value: hook, configurable: false, writable: false });
}
