import { sha256Hex } from "../../io/sha256.ts";
import type { BuildIdentity } from "../../state/study_session.ts";
import { READINESS_KEYS, type Readiness, type ReadinessKey } from "../../state/study_report.ts";
import { parseBuildInfo, type BuildInfo, type Identity } from "../../worker/protocol.ts";

export const MIN_VIEWPORT = { width: 1600, height: 900 } as const;
export const PROBED_MODULES = ["sarcostat.core", "sarcostat.analysis", "sarcostat.export", "scipy"] as const;
export const REQUIRED: readonly ReadinessKey[] = ["webassembly", "indexeddb", "web_locks", "identity", "core_absent"];
export const WARNINGS: readonly ReadinessKey[] = ["persisted", "viewport"];
const WASM_HEADER = new Uint8Array([0, 97, 115, 109, 1, 0, 0, 0]);
const HEX64 = /^[0-9a-f]{64}$/;

export function hasWebAssembly(scope: { WebAssembly?: typeof WebAssembly }): boolean {
  try {
    return typeof scope.WebAssembly === "object" && scope.WebAssembly.validate(WASM_HEADER);
  } catch {
    return false;
  }
}

export function hasWebLocks(nav: Navigator): boolean {
  const locks = (nav as Navigator & { locks?: LockManager }).locks;
  return typeof locks === "object" && locks !== null && typeof locks.request === "function";
}

export async function requestPersistence(nav: Navigator): Promise<boolean> {
  const storage = (nav as Navigator & { storage?: StorageManager }).storage;
  if (storage === undefined || typeof storage.persisted !== "function") {
    return false;
  }
  try {
    if (typeof storage.persist === "function") {
      await storage.persist();
    }
    return (await storage.persisted()) === true;
  } catch {
    return false;
  }
}

export function viewportLargeEnough(width: number, height: number): boolean {
  return width >= MIN_VIEWPORT.width && height >= MIN_VIEWPORT.height;
}

export function passed(results: Partial<Readiness>): boolean {
  return REQUIRED.every((key) => results[key] === true);
}

export function complete(results: Partial<Readiness>): Readiness {
  return Object.fromEntries(READINESS_KEYS.map((key) => [key, results[key] === true])) as Record<ReadinessKey, boolean>;
}

export interface ServedBuild {
  readonly info: BuildInfo;
  readonly identity: BuildIdentity;
}

export async function buildIdentity(bytes: Uint8Array): Promise<ServedBuild> {
  const raw = JSON.parse(new TextDecoder().decode(bytes)) as { resume_digests?: unknown };
  const info = parseBuildInfo(raw);
  const resume = Array.isArray(raw.resume_digests) ? raw.resume_digests.filter((digest): digest is string => typeof digest === "string" && HEX64.test(digest)) : [];
  return { info, identity: { version: info.version, source_digest: info.source_digest, build_json_sha256: await sha256Hex(bytes), resume_digests: resume } };
}

export async function fetchBuildIdentity(base: string): Promise<ServedBuild> {
  const response = await fetch(new URL("./BUILD.json", base), { cache: "no-store", credentials: "same-origin" });
  if (!response.ok) {
    throw new Error(`BUILD.json answered ${response.status}`);
  }
  return buildIdentity(new Uint8Array(await response.arrayBuffer()));
}

export function sameBuild(served: ServedBuild, engine: Identity, page: BuildInfo | null): boolean {
  const info = served.info;
  const pageOk = page === null || (page.version === info.version && page.source_digest === info.source_digest && page.wheel.sha256 === info.wheel.sha256);
  return pageOk && engine.sarcostat_version === info.version && engine.source_digest === info.source_digest && engine.wheel_sha256 === info.wheel.sha256 && engine.pyodide_version === info.pyodide.version;
}
