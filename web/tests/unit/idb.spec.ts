import fs from "node:fs";
import path from "node:path";

import { IDBFactory } from "fake-indexeddb";
import { describe, expect, it } from "vitest";

import { WEB_DIR } from "../../scripts/lane.mjs";
import { DATABASE, DATABASE_VERSION, SeqRefused, StudyStore, type SessionRecord } from "../../src/state/study_store_v1.ts";

const HEX = (c: string): string => c.repeat(64);
const KEY = { study_id: "S", annotator_id: "A1", session_id: "S1", manifest_sha256: HEX("c") };
const SRC = path.join(WEB_DIR, "src");

export function record(seq: number, overrides: Partial<SessionRecord> = {}): SessionRecord {
  return {
    ...KEY,
    seq,
    status: "open",
    build_version: "0.2.0rc1",
    source_digest: HEX("a"),
    build_json_sha256: HEX("b"),
    builds_seen: [{ version: "0.2.0rc1", build_json_sha256: HEX("b"), source_digest: HEX("a") }],
    counts: { pauses: 0, resumes: 0, worker_restarts: 0 },
    autosave_bytes: new Uint8Array([seq, 1, 2]),
    finished_bytes: null,
    finished_sha256: null,
    updated: "2026-11-21T10:00:00.000Z",
    ...overrides,
  };
}

function sources(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    return entry.isDirectory() ? sources(full) : /\.(ts|js|mjs)$/.test(entry.name) ? [full] : [];
  });
}

describe("study database (IndexedDB, fake-indexeddb)", () => {
  it("is created at version 1 with its one store, and opened again at version 1", async () => {
    const factory = new IDBFactory();
    (await StudyStore.open(factory)).close();
    (await StudyStore.open(factory)).close();
    const databases = await factory.databases();
    expect(databases).toEqual([{ name: DATABASE, version: 1 }]);
    expect(DATABASE_VERSION).toBe(1);
  });

  it("stores the record and gives back the same bytes", async () => {
    const store = await StudyStore.open(new IDBFactory());
    await store.put(record(1));
    const back = await store.get(KEY);
    expect(back?.seq).toBe(1);
    expect([...(back?.autosave_bytes ?? [])]).toEqual([1, 1, 2]);
    expect(await store.get({ ...KEY, session_id: "S2" })).toBeNull();
  });

  it("refuses inside the readwrite transaction a put whose seq is not greater than the stored one", async () => {
    const store = await StudyStore.open(new IDBFactory());
    await store.put(record(6));
    await expect(store.put(record(6))).rejects.toBeInstanceOf(SeqRefused);
    await expect(store.put(record(5))).rejects.toBeInstanceOf(SeqRefused);
    expect((await store.get(KEY))?.seq).toBe(6);
    await store.put(record(7));
    expect((await store.get(KEY))?.seq).toBe(7);
  });

  it("never lets an older seq overwrite a newer one when the two puts race in the wrong order", async () => {
    const store = await StudyStore.open(new IDBFactory());
    await store.put(record(1));
    const newer = store.put(record(9));
    const older = store.put(record(8));
    await expect(newer).resolves.toBeUndefined();
    await expect(older).rejects.toBeInstanceOf(SeqRefused);
    const back = await store.get(KEY);
    expect(back?.seq).toBe(9);
    expect([...(back?.autosave_bytes ?? [])]).toEqual([9, 1, 2]);
  });

  it("keeps a finished record as it is and refuses an invalid one", async () => {
    const store = await StudyStore.open(new IDBFactory());
    await store.put(record(3, { status: "finished", finished_bytes: new Uint8Array([7]), finished_sha256: HEX("d") }));
    await expect(store.put(record(4))).rejects.toThrow("finished");
    await expect(store.put(record(1, { session_id: "S9", status: "finished" }))).rejects.toThrow("not valid");
    await expect(store.put(record(0, { session_id: "S9" }))).rejects.toThrow("seq");
    await expect(store.put(record(1, { session_id: "S9", manifest_sha256: "ABC" }))).rejects.toThrow("manifest_sha256");
  });

  it("is never deleted and never opened at another version by any source file (grep over web/src)", () => {
    const files = sources(SRC);
    const hits = (pattern: RegExp): string[] => files.filter((file) => pattern.test(fs.readFileSync(file, "utf8"))).map((file) => path.relative(SRC, file));
    expect(hits(/deleteDatabase/)).toEqual([]);
    expect(hits(/indexedDB\.open\(|factory\.open\(|\.open\(\s*name\s*,/).sort()).toEqual(["state/idb.ts"]);
    expect(hits(/openDatabase\(/).sort()).toEqual(["state/idb.ts", "state/study_store_v1.ts"]);
    const store = fs.readFileSync(path.join(SRC, "state", "study_store_v1.ts"), "utf8");
    expect(store).toMatch(/openDatabase\(factory, DATABASE, DATABASE_VERSION, upgrade\)/);
    expect(store).toMatch(/export const DATABASE_VERSION = 1;/);
    expect(hits(/sarcostat-study-v\d/).sort()).toEqual(["state/locks.ts", "state/study_session.ts", "state/study_store_v1.ts"]);
    expect(hits(/sarcostat-study-v(?!1)\d/)).toEqual([]);
  });
});
