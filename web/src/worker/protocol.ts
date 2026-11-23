import type { CommandSpecs, Schema } from "./catalogue.ts";

export const BRIDGE_VERSION = 1;
export const ERROR_CODES = ["bad_request", "refused", "needs_confirmation", "internal"] as const;
export const ABSENT_FAMILIES = ["chain", "register", "planner"] as const;
export const FATAL_CODES = ["build_damaged", "bridge_mismatch", "boot_failed", "worker_died", "timeout"] as const;
export const BOOT_STAGES = ["runtime", "numpy", "wheel", "engine"] as const;
export const COMMAND_TIMEOUT_MS = 10_000;
export const SCIPY_TIMEOUT_MS = 120_000;
export const HANDSHAKE_COMMAND = "sys.identity";

export type ErrorCode = (typeof ERROR_CODES)[number];
export type FatalCode = (typeof FATAL_CODES)[number];
export type BootStage = (typeof BOOT_STAGES)[number];

type Primitive<K> = K extends "string"
  ? string
  : K extends "number" | "integer"
    ? number
    : K extends "boolean"
      ? boolean
      : K extends "null"
        ? null
        : K extends "buffer"
          ? Uint8Array
          : never;

type ObjectOf<S> = S extends { readonly properties: infer P }
  ? { -readonly [K in keyof P]: FromSchema<P[K]> }
  : S extends { readonly additionalProperties: infer A extends Schema }
    ? Record<string, FromSchema<A>>
    : Record<string, never>;

type FromKind<S, K> = K extends "object"
  ? ObjectOf<S>
  : K extends "array"
    ? S extends { readonly items: infer I }
      ? FromSchema<I>[]
      : never
    : Primitive<K>;

export type FromSchema<S> = S extends { readonly enum: readonly (infer E)[] }
  ? E
  : S extends { readonly type: infer T }
    ? T extends readonly (infer K)[]
      ? FromKind<S, K>
      : FromKind<S, T>
    : never;

export type CommandName = keyof CommandSpecs;
export type CommandArgs<C extends CommandName> = FromSchema<CommandSpecs[C]["args"]>;
export type CommandResult<C extends CommandName> = FromSchema<CommandSpecs[C]["result"]>;

export type RequestId = number | string;
export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };
export interface BufRef {
  $buf: number;
}

export interface RequestEnvelope {
  v: typeof BRIDGE_VERSION;
  id: RequestId;
  cmd: string;
  args: Record<string, unknown>;
}

export interface EngineErrorBody {
  code: ErrorCode;
  message: string;
}

export type ResponseEnvelope =
  | { v: typeof BRIDGE_VERSION; id: RequestId; ok: true; result: unknown }
  | { v: typeof BRIDGE_VERSION; id: RequestId | null; ok: false; error: EngineErrorBody };

export interface BuildInfo {
  version: string;
  wheel: { file: string; sha256: string };
  source_digest: string;
  pyodide: { version: string; dir: string };
}

export interface Identity {
  sarcostat_version: string;
  source_digest: string;
  bridge_version: number;
  pyodide_version: string;
  wheel_sha256: string;
}

export type ToWorker =
  | { kind: "start"; base: string; build: BuildInfo }
  | { kind: "request"; request: RequestEnvelope; buffers: Uint8Array[] }
  | { kind: "modules"; names: string[] };

export type FromWorker =
  | { kind: "boot"; stage: BootStage }
  | { kind: "identity"; identity: Identity }
  | { kind: "fatal"; code: FatalCode; message: string }
  | { kind: "response"; response: ResponseEnvelope; buffers: Uint8Array[] }
  | { kind: "modules"; loaded: string[] };

const HEX64 = /^[0-9a-f]{64}$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown, where: string): string {
  if (typeof value !== "string" || value === "") {
    throw new TypeError(`${where} must be a non-empty string`);
  }
  return value;
}

function hex64(value: unknown, where: string): string {
  const out = text(value, where);
  if (!HEX64.test(out)) {
    throw new TypeError(`${where} must be 64 lowercase hexadecimal digits`);
  }
  return out;
}

function relativePath(value: unknown, where: string): string {
  const out = text(value, where);
  if (out.startsWith("/") || out.includes("\\") || out.includes(":") || out.split("/").includes("..")) {
    throw new TypeError(`${where} must be a path inside the version folder`);
  }
  return out;
}

export function parseBuildInfo(raw: unknown): BuildInfo {
  if (!isRecord(raw) || raw.format !== "sarcostat-build") {
    throw new TypeError("BUILD.json is not a SarcoStat build description");
  }
  const wheel = isRecord(raw.wheel) ? raw.wheel : {};
  const pyodide = isRecord(raw.pyodide) ? raw.pyodide : {};
  const dir = relativePath(pyodide.dir, "pyodide.dir");
  return {
    version: text(raw.version, "version"),
    wheel: { file: relativePath(wheel.file, "wheel.file"), sha256: hex64(wheel.sha256, "wheel.sha256") },
    source_digest: hex64(raw.source_digest, "source_digest"),
    pyodide: { version: text(pyodide.version, "pyodide.version"), dir: dir.endsWith("/") ? dir : `${dir}/` },
  };
}

function isBufRef(value: unknown): value is BufRef {
  if (!isRecord(value)) {
    return false;
  }
  const keys = Object.keys(value);
  return keys.length === 1 && keys[0] === "$buf" && Number.isInteger(value.$buf);
}

export function encodeBuffers(value: unknown): { value: unknown; buffers: Uint8Array[] } {
  const buffers: Uint8Array[] = [];
  const walk = (node: unknown): unknown => {
    if (node instanceof Uint8Array) {
      buffers.push(node);
      return { $buf: buffers.length - 1 };
    }
    if (Array.isArray(node)) {
      return node.map(walk);
    }
    if (isRecord(node)) {
      const out: Record<string, unknown> = {};
      for (const [key, element] of Object.entries(node)) {
        out[key] = walk(element);
      }
      return out;
    }
    return node;
  };
  return { value: walk(value), buffers };
}

export function decodeBuffers(value: unknown, buffers: readonly Uint8Array[]): unknown {
  if (isBufRef(value)) {
    const buffer = buffers[value.$buf];
    if (buffer === undefined) {
      throw new RangeError(`$buf ${value.$buf} does not name one of the ${buffers.length} buffer(s)`);
    }
    return buffer;
  }
  if (Array.isArray(value)) {
    return value.map((element) => decodeBuffers(element, buffers));
  }
  if (isRecord(value)) {
    const out: Record<string, unknown> = {};
    for (const [key, element] of Object.entries(value)) {
      out[key] = decodeBuffers(element, buffers);
    }
    return out;
  }
  return value;
}

export function transferList(buffers: readonly Uint8Array[]): ArrayBuffer[] {
  const seen = new Set<ArrayBuffer>();
  for (const buffer of buffers) {
    if (buffer.buffer instanceof ArrayBuffer) {
      seen.add(buffer.buffer);
    }
  }
  return [...seen];
}

function isBufferList(value: unknown): value is Uint8Array[] {
  return Array.isArray(value) && value.every((element) => element instanceof Uint8Array);
}

function isStringList(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((element) => typeof element === "string");
}

function isRequestId(value: unknown): value is RequestId {
  return typeof value === "string" || (typeof value === "number" && Number.isInteger(value));
}

function isResponse(value: unknown): value is ResponseEnvelope {
  if (!isRecord(value) || value.v !== BRIDGE_VERSION) {
    return false;
  }
  if (value.ok === true) {
    return isRequestId(value.id) && "result" in value;
  }
  if (value.ok === false) {
    const error = value.error;
    return (
      (value.id === null || isRequestId(value.id)) &&
      isRecord(error) &&
      (ERROR_CODES as readonly unknown[]).includes(error.code) &&
      typeof error.message === "string"
    );
  }
  return false;
}

function isIdentity(value: unknown): value is Identity {
  return (
    isRecord(value) &&
    typeof value.sarcostat_version === "string" &&
    typeof value.source_digest === "string" &&
    typeof value.bridge_version === "number" &&
    typeof value.pyodide_version === "string" &&
    typeof value.wheel_sha256 === "string"
  );
}

export function isFromWorker(value: unknown): value is FromWorker {
  if (!isRecord(value)) {
    return false;
  }
  switch (value.kind) {
    case "boot":
      return (BOOT_STAGES as readonly unknown[]).includes(value.stage);
    case "identity":
      return isIdentity(value.identity);
    case "fatal":
      return (FATAL_CODES as readonly unknown[]).includes(value.code) && typeof value.message === "string";
    case "response":
      return isResponse(value.response) && isBufferList(value.buffers);
    case "modules":
      return isStringList(value.loaded);
    default:
      return false;
  }
}

export function isToWorker(value: unknown): value is ToWorker {
  if (!isRecord(value)) {
    return false;
  }
  if (value.kind === "start") {
    return typeof value.base === "string" && isRecord(value.build);
  }
  if (value.kind === "modules") {
    return isStringList(value.names);
  }
  if (value.kind === "request") {
    const request = value.request;
    return (
      isRecord(request) &&
      request.v === BRIDGE_VERSION &&
      isRequestId(request.id) &&
      typeof request.cmd === "string" &&
      isRecord(request.args) &&
      isBufferList(value.buffers)
    );
  }
  return false;
}
