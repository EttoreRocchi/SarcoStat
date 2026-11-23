import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import { IDBFactory } from "fake-indexeddb";
import { describe, expect, it } from "vitest";

import { WEB_DIR } from "../../scripts/lane.mjs";
import { acquireSessionLock, LOCK_PREFIX, lockName } from "../../src/state/locks.ts";
import { DURABILITY, StudyStore, type SessionRecord } from "../../src/state/study_store_v1.ts";

export const CONTRACT_FILE = path.join(WEB_DIR, "src", "state", "study_store_v1.contract.json");
export const CONTRACT_SHA256: string | null = "d9edd24d0de868fe157e2539e1074dd1ea32252592ab4a1d0dc43facce0d083c";

interface Contract {
  contract: string;
  contract_version: number;
  database: string;
  database_version: number;
  durability: string;
  lock_prefix: string;
  record: { builds_seen_fields: string[]; counts_fields: string[]; key_fields: string[]; status_values: string[]; value_fields: string[] };
  stores: { auto_increment: boolean; indexes: string[]; key_path: string[]; name: string }[];
}

const CONTRACT = JSON.parse(fs.readFileSync(CONTRACT_FILE, "utf8")) as Contract;
const HEX = (c: string): string => c.repeat(64);
const KEY = { study_id: "S", annotator_id: "A1", session_id: "S1", manifest_sha256: HEX("c") };

function record(): SessionRecord {
  return {
    ...KEY,
    seq: 1,
    status: "open",
    build_version: "0.2.0b1",
    source_digest: HEX("a"),
    build_json_sha256: HEX("b"),
    builds_seen: [{ version: "0.2.0b1", build_json_sha256: HEX("b"), source_digest: HEX("a") }],
    counts: { pauses: 0, resumes: 0, worker_restarts: 0 },
    autosave_bytes: new Uint8Array([1]),
    finished_bytes: null,
    finished_sha256: null,
    updated: "2026-11-21T10:00:00.000Z",
  };
}

function request<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

describe("study storage contract v1", () => {
  it.skipIf(CONTRACT_SHA256 === null)("is the frozen file, pinned by its SHA-256", () => {
    expect(createHash("sha256").update(fs.readFileSync(CONTRACT_FILE)).digest("hex")).toBe(CONTRACT_SHA256);
  });

  it("is canonical: sorted keys, two-space indent, final newline", () => {
    const text = fs.readFileSync(CONTRACT_FILE, "utf8");
    expect(text.endsWith("}\n")).toBe(true);
    const sortedKeys = (value: unknown): boolean =>
      Array.isArray(value) ? value.every(sortedKeys) : value !== null && typeof value === "object" ? Object.keys(value).join("\n") === Object.keys(value).sort().join("\n") && Object.values(value).every(sortedKeys) : true;
    expect(sortedKeys(CONTRACT)).toBe(true);
    expect(text.split("\n").every((line) => /^( {2})*\S|^$/.test(line))).toBe(true);
  });

  it("equals the runtime schema read back from IndexedDB: name, version, store, key path, indexes and the fields of a written record", async () => {
    const factory = new IDBFactory();
    const store = await StudyStore.open(factory);
    await store.put(record());
    await store.put({ ...record(), session_id: "S2", seq: 2, status: "finished", finished_bytes: new Uint8Array([2]), finished_sha256: HEX("d") });
    store.close();
    const db = await request(factory.open(CONTRACT.database));
    expect(db.name).toBe(CONTRACT.database);
    expect(db.version).toBe(CONTRACT.database_version);
    expect([...db.objectStoreNames]).toEqual(CONTRACT.stores.map((entry) => entry.name));
    const contractStore = CONTRACT.stores[0] as Contract["stores"][number];
    const transaction = db.transaction(contractStore.name, "readonly");
    const objectStore = transaction.objectStore(contractStore.name);
    expect(objectStore.keyPath).toEqual(contractStore.key_path);
    expect(objectStore.autoIncrement).toBe(contractStore.auto_increment);
    expect([...objectStore.indexNames]).toEqual(contractStore.indexes);
    const records = (await request(objectStore.getAll())) as Record<string, unknown>[];
    expect(records).toHaveLength(2);
    expect(CONTRACT.record.key_fields).toEqual(contractStore.key_path);
    for (const value of records) {
      const fields = Object.keys(value).filter((key) => !CONTRACT.record.key_fields.includes(key)).sort();
      expect(fields).toEqual(CONTRACT.record.value_fields);
      expect(Object.keys(value.counts as object).sort()).toEqual(CONTRACT.record.counts_fields);
      for (const seen of value.builds_seen as object[]) {
        expect(Object.keys(seen).sort()).toEqual(CONTRACT.record.builds_seen_fields);
      }
      expect(CONTRACT.record.status_values).toContain(value.status);
      expect(value.autosave_bytes).toBeInstanceOf(Uint8Array);
    }
    expect(records.map((value) => value.status).sort()).toEqual(CONTRACT.record.status_values);
    db.close();
  });

  it("writes with the contract's durability", async () => {
    const factory = new IDBFactory();
    const store = await StudyStore.open(factory);
    const seen: unknown[] = [];
    const dbProto = Object.getPrototypeOf((store as unknown as { db: object }).db) as { transaction: (...args: unknown[]) => unknown };
    const original = dbProto.transaction;
    dbProto.transaction = function spied(this: unknown, ...args: unknown[]): unknown {
      seen.push(args);
      return original.apply(this, args);
    };
    try {
      await store.put(record());
    } finally {
      dbProto.transaction = original;
    }
    expect(DURABILITY).toBe(CONTRACT.durability);
    expect(seen).toContainEqual(["sessions", "readwrite", { durability: CONTRACT.durability }]);
  });

  it("names the session lock with the contract's prefix, read from the name passed to navigator.locks.request", async () => {
    const requests: { name: string; options: LockOptions }[] = [];
    const locks = {
      request(name: string, options: LockOptions, callback: (lock: Lock | null) => unknown) {
        requests.push({ name, options });
        return Promise.resolve(callback({ name, mode: "exclusive" } as Lock));
      },
    } as unknown as LockManager;
    const held = await acquireSessionLock(locks, KEY);
    expect(held?.name).toBe(lockName(KEY));
    expect(requests).toEqual([{ name: `${CONTRACT.lock_prefix}S/A1/S1/${HEX("c")}`, options: { ifAvailable: true } }]);
    expect(LOCK_PREFIX).toBe(CONTRACT.lock_prefix);
    const busy = { request: (_name: string, _options: LockOptions, callback: (lock: Lock | null) => unknown) => Promise.resolve(callback(null)) } as unknown as LockManager;
    expect(await acquireSessionLock(busy, KEY)).toBeNull();
  });
});
