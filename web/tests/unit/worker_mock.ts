import { execFileSync } from "node:child_process";
import path from "node:path";

import { WEB_DIR } from "../../scripts/lane.mjs";
import type { CatalogueJson, Schema, SchemaKind } from "../../src/worker/catalogue.ts";
import type { WorkerLike } from "../../src/worker/client.ts";
import {
  BRIDGE_VERSION,
  type BootStage,
  type BuildInfo,
  type FromWorker,
  type Identity,
  type RequestEnvelope,
  type ResponseEnvelope,
  type ToWorker,
} from "../../src/worker/protocol.ts";

export const SOURCE_DIR = path.resolve(WEB_DIR, "..", "src");

export function pythonExecutable(): string {
  return process.env.SARCOSTAT_PYTHON && process.env.SARCOSTAT_PYTHON !== "" ? process.env.SARCOSTAT_PYTHON : "python3";
}

export function runPython(args: string[]): string {
  const inherited = process.env.PYTHONPATH ? [process.env.PYTHONPATH] : [];
  return execFileSync(pythonExecutable(), args, {
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: [SOURCE_DIR, ...inherited].join(path.delimiter) },
    maxBuffer: 16 * 1024 * 1024,
  });
}

let cached: CatalogueJson | null = null;

export function pythonCatalogue(): CatalogueJson {
  if (cached === null) {
    cached = JSON.parse(runPython(["-m", "sarcostat.engine.bridge", "--catalogue"])) as CatalogueJson;
  }
  return structuredClone(cached);
}

function kinds(schema: Schema): readonly SchemaKind[] {
  return typeof schema.type === "string" ? [schema.type] : schema.type;
}

function isBufRef(value: unknown): value is { $buf: unknown } {
  return typeof value === "object" && value !== null && !Array.isArray(value) && Object.keys(value).length === 1 && "$buf" in value;
}

function jsonKind(value: unknown): string {
  if (value === null) {
    return "null";
  }
  if (Array.isArray(value)) {
    return "array";
  }
  if (typeof value === "number") {
    return Number.isInteger(value) ? "integer" : "number";
  }
  return typeof value;
}

export function validate(value: unknown, schema: Schema, nBuffers: number, where = "value"): string[] {
  const allowed = kinds(schema);
  if (allowed.includes("buffer") && isBufRef(value)) {
    const index = value.$buf;
    return typeof index === "number" && Number.isInteger(index) && index >= 0 && index < nBuffers
      ? []
      : [`${where}: $buf ${String(index)} does not name one of the ${nBuffers} buffer(s)`];
  }
  let actual = jsonKind(value);
  if (actual === "integer" && !allowed.includes("integer") && allowed.includes("number")) {
    actual = "number";
  }
  if (!(allowed as readonly string[]).includes(actual)) {
    return [`${where}: expected ${allowed.join(" or ")}, got ${actual}`];
  }
  if ((actual === "number" || actual === "integer") && !Number.isFinite(value)) {
    return [`${where}: must be a finite number`];
  }
  if (schema.enum !== undefined && !schema.enum.includes(value as string | null)) {
    return [`${where}: ${JSON.stringify(value)} is not one of ${JSON.stringify(schema.enum)}`];
  }
  if (schema.minimum !== undefined && typeof value === "number" && value < schema.minimum) {
    return [`${where}: ${value} < ${schema.minimum}`];
  }
  if (actual === "array") {
    return (value as unknown[]).flatMap((element, k) => validate(element, schema.items as Schema, nBuffers, `${where}[${k}]`));
  }
  if (actual === "object") {
    const record = value as Record<string, unknown>;
    const properties = schema.properties;
    const extra = schema.additionalProperties ?? false;
    if (properties === undefined) {
      return extra === false ? [] : Object.entries(record).flatMap(([key, element]) => validate(element, extra, nBuffers, `${where}[${key}]`));
    }
    const problems = (schema.required ?? []).filter((key) => !(key in record)).map((key) => `${where}: missing key ${key}`);
    for (const [key, element] of Object.entries(record)) {
      const property = properties[key];
      if (property !== undefined) {
        problems.push(...validate(element, property, nBuffers, `${where}.${key}`));
      } else if (extra === false) {
        problems.push(`${where}: unexpected key ${key}`);
      } else {
        problems.push(...validate(element, extra, nBuffers, `${where}[${key}]`));
      }
    }
    return problems;
  }
  return [];
}

export function sample(schema: Schema, buffers: Uint8Array[]): unknown {
  const kind = kinds(schema).find((candidate) => candidate !== "null") ?? "null";
  if (schema.enum !== undefined) {
    return schema.enum.find((choice) => choice !== null) ?? null;
  }
  switch (kind) {
    case "object": {
      if (schema.properties === undefined) {
        const extra = schema.additionalProperties;
        return extra === undefined || extra === false ? {} : { key: sample(extra, buffers) };
      }
      const out: Record<string, unknown> = {};
      for (const [key, property] of Object.entries(schema.properties)) {
        out[key] = sample(property, buffers);
      }
      return out;
    }
    case "array":
      return [sample(schema.items as Schema, buffers)];
    case "string":
      return "text";
    case "integer":
      return Math.max(schema.minimum ?? 0, 2);
    case "number":
      return Math.max(schema.minimum ?? 0, 0.5);
    case "boolean":
      return true;
    case "buffer":
      buffers.push(new Uint8Array([buffers.length, 255]));
      return { $buf: buffers.length - 1 };
    default:
      return null;
  }
}

export function mockAnswer(
  catalogue: CatalogueJson,
  request: RequestEnvelope,
  buffers: readonly Uint8Array[],
  fixed: Record<string, unknown> = {},
): { response: ResponseEnvelope; buffers: Uint8Array[] } {
  const fail = (code: "bad_request" | "refused", message: string) => ({
    response: { v: BRIDGE_VERSION, id: request.id, ok: false, error: { code, message } } as ResponseEnvelope,
    buffers: [],
  });
  const family = request.cmd.split(".", 1)[0] as string;
  if (catalogue.absent_families.includes(family)) {
    return fail("refused", `${request.cmd}: not in this build`);
  }
  const spec = catalogue.commands[request.cmd];
  if (spec === undefined) {
    return fail("bad_request", `unknown command ${request.cmd}`);
  }
  const problems = validate(request.args, spec.args, buffers.length, "args");
  if (problems.length > 0) {
    return fail("bad_request", `${request.cmd}: ${problems[0]}`);
  }
  const out: Uint8Array[] = [];
  const result = request.cmd in fixed ? fixed[request.cmd] : sample(spec.result, out);
  return { response: { v: BRIDGE_VERSION, id: request.id, ok: true, result }, buffers: out };
}

export function identityOf(build: BuildInfo): Identity {
  return {
    sarcostat_version: build.version,
    source_digest: build.source_digest,
    bridge_version: BRIDGE_VERSION,
    pyodide_version: build.pyodide.version,
    wheel_sha256: build.wheel.sha256,
  };
}

export const TEST_BUILD: BuildInfo = {
  version: "0.2.0rc1.dev0",
  wheel: { file: "wheels/sarcostat-0.2.0rc1.dev0-py3-none-any.whl", sha256: "a".repeat(64) },
  source_digest: "b".repeat(64),
  pyodide: { version: "314.0.8", dir: "pyodide/" },
};

export interface MockOptions {
  catalogue: CatalogueJson;
  identity?: Identity;
  silent?: boolean;
  stages?: BootStage[];
  loadedModules?: readonly string[];
}

export class MockWorker implements WorkerLike {
  readonly posted: { message: ToWorker; transfer: Transferable[] }[] = [];
  readonly received: RequestEnvelope[] = [];
  readonly probed: string[][] = [];
  terminated = false;
  silent: boolean;
  private readonly listeners = new Map<string, ((event: Event) => void)[]>();
  private readonly options: MockOptions;
  private identity: Identity | null = null;

  constructor(options: MockOptions) {
    this.options = options;
    this.silent = options.silent ?? false;
  }

  addEventListener(type: "message" | "messageerror" | "error", listener: (event: Event) => void): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  emit(type: "message" | "messageerror" | "error", event: Event): void {
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event);
    }
  }

  send(message: FromWorker | unknown): void {
    this.emit("message", new MessageEvent("message", { data: message }));
  }

  postMessage(message: ToWorker, transfer: Transferable[]): void {
    this.posted.push({ message, transfer });
    if (this.terminated) {
      return;
    }
    if (message.kind === "start") {
      this.identity = this.options.identity ?? identityOf(message.build);
      for (const stage of this.options.stages ?? ["runtime", "numpy", "wheel", "engine"]) {
        this.send({ kind: "boot", stage });
      }
      this.send({ kind: "identity", identity: this.identity });
      return;
    }
    if (message.kind === "modules") {
      this.probed.push(message.names);
      if (!this.silent) {
        const present = new Set(this.options.loadedModules ?? []);
        const loaded = message.names.filter((name) => present.has(name));
        queueMicrotask(() => {
          if (!this.terminated) {
            this.send({ kind: "modules", loaded });
          }
        });
      }
      return;
    }
    this.received.push(message.request);
    if (this.silent) {
      return;
    }
    const identity = this.identity;
    const fixed =
      identity === null
        ? {}
        : {
            "sys.identity": {
              sarcostat_version: identity.sarcostat_version,
              source_digest: identity.source_digest,
              bridge_version: identity.bridge_version,
            },
          };
    const answer = mockAnswer(this.options.catalogue, message.request, message.buffers, fixed);
    queueMicrotask(() => {
      if (!this.terminated) {
        this.send({ kind: "response", response: answer.response, buffers: answer.buffers });
      }
    });
  }

  terminate(): void {
    this.terminated = true;
  }
}
