import {
  BRIDGE_VERSION,
  COMMAND_TIMEOUT_MS,
  SCIPY_TIMEOUT_MS,
  HANDSHAKE_COMMAND,
  decodeBuffers,
  encodeBuffers,
  isFromWorker,
  transferList,
  type BootStage,
  type BuildInfo,
  type CommandArgs,
  type CommandName,
  type CommandResult,
  type ErrorCode,
  type FatalCode,
  type FromWorker,
  type Identity,
  type RequestId,
  type ToWorker,
} from "./protocol.ts";

export interface WorkerLike {
  postMessage(message: ToWorker, transfer: Transferable[]): void;
  addEventListener(type: "message" | "messageerror" | "error", listener: (event: Event) => void): void;
  terminate(): void;
}

export interface ClientListener {
  boot(stage: BootStage): void;
  ready(identity: Identity): void;
  fatal(code: FatalCode, message: string): void;
}

export type ClientState = "idle" | "booting" | "handshake" | "ready" | "failed";

export class EngineError extends Error {
  readonly code: ErrorCode;

  constructor(code: ErrorCode, message: string) {
    super(message);
    this.name = "EngineError";
    this.code = code;
  }
}

export const STUDY_FAMILY = "study.";

interface Pending {
  cmd: string;
  resolve(value: unknown): void;
  reject(error: Error): void;
  timer: ReturnType<typeof setTimeout>;
}

function ownBuffer(buffer: Uint8Array): Uint8Array {
  return buffer.byteOffset === 0 && buffer.byteLength === buffer.buffer.byteLength ? buffer : buffer.slice();
}

export function identityProblems(identity: Identity, build: BuildInfo): string[] {
  const problems: string[] = [];
  const expect = (what: string, actual: string, wanted: string): void => {
    if (actual !== wanted) {
      problems.push(`${what} is ${actual}, BUILD.json says ${wanted}`);
    }
  };
  expect("the SarcoStat version", identity.sarcostat_version, build.version);
  expect("the wheel SHA-256", identity.wheel_sha256, build.wheel.sha256);
  expect("the source digest", identity.source_digest, build.source_digest);
  expect("the Pyodide version", identity.pyodide_version, build.pyodide.version);
  return problems;
}

export class EngineClient {
  private readonly worker: WorkerLike;
  private readonly build: BuildInfo;
  private readonly base: string;
  private readonly listener: ClientListener;
  private readonly timeoutMs: number;
  private readonly pending = new Map<RequestId, Pending>();
  private readonly probes: { resolve(loaded: string[]): void; reject(error: Error): void; timer: ReturnType<typeof setTimeout> }[] = [];
  private nextId = 1;
  private current: ClientState = "idle";

  constructor(worker: WorkerLike, options: { build: BuildInfo; base: string; timeoutMs?: number }, listener: ClientListener) {
    this.worker = worker;
    this.build = options.build;
    this.base = options.base;
    this.timeoutMs = options.timeoutMs ?? COMMAND_TIMEOUT_MS;
    this.listener = listener;
    worker.addEventListener("message", (event) => this.onMessage((event as MessageEvent<unknown>).data));
    worker.addEventListener("messageerror", () => this.fail("worker_died", "a message from the engine could not be read"));
    worker.addEventListener("error", (event) => {
      const detail = (event as Event & { message?: unknown }).message;
      const message = typeof detail === "string" && detail !== "" ? detail : "the engine worker failed";
      event.preventDefault();
      this.fail("worker_died", message);
    });
  }

  get state(): ClientState {
    return this.current;
  }

  start(): void {
    if (this.current !== "idle") {
      return;
    }
    this.current = "booting";
    this.worker.postMessage({ kind: "start", base: this.base, build: this.build }, []);
  }

  call<C extends CommandName>(cmd: C, args: CommandArgs<C>): Promise<CommandResult<C>> {
    if (this.current !== "ready") {
      return Promise.reject(new EngineError("refused", `the engine is ${this.current === "failed" ? "stopped" : "not ready"}`));
    }
    if (cmd.startsWith(STUDY_FAMILY) && [...this.pending.values()].some((pending) => pending.cmd.startsWith(STUDY_FAMILY))) {
      return Promise.reject(new EngineError("refused", `${cmd}: another study command is still in the engine; study commands run one at a time`));
    }
    return this.send(cmd, args) as Promise<CommandResult<C>>;
  }

  close(): void {
    if (this.current === "failed") {
      return;
    }
    this.current = "failed";
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(new EngineError("refused", "the engine was closed"));
    }
    this.pending.clear();
    for (const probe of this.probes.splice(0)) {
      clearTimeout(probe.timer);
      probe.reject(new EngineError("refused", "the engine was closed"));
    }
    this.worker.terminate();
  }

  modules(names: readonly string[]): Promise<string[]> {
    if (this.current !== "ready") {
      return Promise.reject(new EngineError("refused", `the engine is ${this.current === "failed" ? "stopped" : "not ready"}`));
    }
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.fail("timeout", `the module probe got no answer within ${Math.round(this.timeoutMs / 1000)} s`);
      }, this.timeoutMs);
      this.probes.push({ resolve, reject, timer });
      this.worker.postMessage({ kind: "modules", names: [...names] }, []);
    });
  }

  private send(cmd: string, args: unknown): Promise<unknown> {
    const id = this.nextId++;
    const encoded = encodeBuffers(args);
    const buffers = encoded.buffers.map(ownBuffer);
    return new Promise((resolve, reject) => {
      const limit = cmd === "sys.load_scipy" ? Math.max(this.timeoutMs, SCIPY_TIMEOUT_MS) : this.timeoutMs;
      const timer = setTimeout(() => {
        this.fail("timeout", `${cmd} got no answer within ${Math.round(limit / 1000)} s`);
      }, limit);
      this.pending.set(id, { cmd, resolve, reject, timer });
      try {
        this.worker.postMessage(
          { kind: "request", request: { v: BRIDGE_VERSION, id, cmd, args: encoded.value as Record<string, unknown> }, buffers },
          transferList(buffers),
        );
      } catch (error) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(new EngineError("bad_request", `${cmd}: the arguments cannot be sent to the engine: ${error instanceof Error ? error.message : String(error)}`));
      }
    });
  }

  private onMessage(data: unknown): void {
    if (this.current === "failed") {
      return;
    }
    if (!isFromWorker(data)) {
      this.fail("worker_died", "the engine sent a message this page does not understand");
      return;
    }
    this.route(data);
  }

  private route(message: FromWorker): void {
    switch (message.kind) {
      case "boot":
        this.listener.boot(message.stage);
        return;
      case "identity":
        this.onIdentity(message.identity);
        return;
      case "fatal":
        this.fail(message.code, message.message);
        return;
      case "response":
        this.onResponse(message);
        return;
      case "modules": {
        const probe = this.probes.shift();
        if (probe !== undefined) {
          clearTimeout(probe.timer);
          probe.resolve(message.loaded);
        }
        return;
      }
    }
  }

  private onIdentity(identity: Identity): void {
    if (this.current !== "booting") {
      return;
    }
    if (identity.bridge_version !== BRIDGE_VERSION) {
      this.fail("bridge_mismatch", `the engine speaks bridge version ${identity.bridge_version}, this page speaks ${BRIDGE_VERSION}`);
      return;
    }
    const problems = identityProblems(identity, this.build);
    if (problems.length > 0) {
      this.fail("build_damaged", problems.join("; "));
      return;
    }
    this.current = "handshake";
    this.send(HANDSHAKE_COMMAND, {}).then(
      (result) => this.onHandshake(identity, result as CommandResult<"sys.identity">),
      (error: unknown) => this.fail("boot_failed", `${HANDSHAKE_COMMAND}: ${error instanceof Error ? error.message : String(error)}`),
    );
  }

  private onHandshake(identity: Identity, result: CommandResult<"sys.identity">): void {
    if (this.current !== "handshake") {
      return;
    }
    if (result.bridge_version !== BRIDGE_VERSION) {
      this.fail("bridge_mismatch", `the engine answers with bridge version ${result.bridge_version}, this page speaks ${BRIDGE_VERSION}`);
      return;
    }
    if (result.source_digest !== identity.source_digest || result.sarcostat_version !== identity.sarcostat_version) {
      this.fail("build_damaged", "the engine's identity changed between its start and its first answer");
      return;
    }
    this.current = "ready";
    this.listener.ready(identity);
  }

  private onResponse(message: Extract<FromWorker, { kind: "response" }>): void {
    const response = message.response;
    const pending = response.id === null ? undefined : this.pending.get(response.id);
    if (pending === undefined) {
      return;
    }
    this.pending.delete(response.id as RequestId);
    clearTimeout(pending.timer);
    if (response.ok) {
      let result: unknown;
      try {
        result = decodeBuffers(response.result, message.buffers);
      } catch (error) {
        this.fail("worker_died", `${pending.cmd}: ${error instanceof Error ? error.message : String(error)}`);
        return;
      }
      pending.resolve(result);
    } else {
      pending.reject(new EngineError(response.error.code, response.error.message));
    }
  }

  private fail(code: FatalCode, message: string): void {
    if (this.current === "failed") {
      return;
    }
    this.current = "failed";
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(new EngineError("refused", `the engine stopped: ${message}`));
    }
    this.pending.clear();
    for (const probe of this.probes.splice(0)) {
      clearTimeout(probe.timer);
      probe.reject(new EngineError("refused", `the engine stopped: ${message}`));
    }
    this.worker.terminate();
    this.listener.fatal(code, message);
  }
}
