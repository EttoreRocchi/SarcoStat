import { finished, IdbError, openDatabase, request } from "./idb.ts";

export const DATABASE = "sarcostat-study-v1";
export const DATABASE_VERSION = 1;
export const STORE = "sessions";
export const KEY_FIELDS = ["study_id", "annotator_id", "session_id", "manifest_sha256"] as const;
export const DURABILITY: IDBTransactionDurability = "strict";
export const STATUS_VALUES = ["finished", "open"] as const;

export type SessionStatus = (typeof STATUS_VALUES)[number];

export interface SessionKey {
  readonly study_id: string;
  readonly annotator_id: string;
  readonly session_id: string;
  readonly manifest_sha256: string;
}

export interface BuildSeen {
  readonly version: string;
  readonly build_json_sha256: string;
  readonly source_digest: string;
}

export interface Counts {
  readonly pauses: number;
  readonly resumes: number;
  readonly worker_restarts: number;
}

export interface SessionRecord extends SessionKey {
  readonly seq: number;
  readonly status: SessionStatus;
  readonly build_version: string;
  readonly source_digest: string;
  readonly build_json_sha256: string;
  readonly builds_seen: readonly BuildSeen[];
  readonly counts: Counts;
  readonly autosave_bytes: Uint8Array;
  readonly finished_bytes: Uint8Array | null;
  readonly finished_sha256: string | null;
  readonly updated: string;
}

export class SeqRefused extends Error {
  readonly stored: number;
  readonly offered: number;

  constructor(offered: number, stored: number) {
    super(`seq ${offered} is not greater than the stored seq ${stored}`);
    this.name = "SeqRefused";
    this.stored = stored;
    this.offered = offered;
  }
}

const HEX64 = /^[0-9a-f]{64}$/;

function count(value: unknown): boolean {
  return Number.isInteger(value) && (value as number) >= 0;
}

export function recordProblems(record: SessionRecord): string[] {
  const problems: string[] = [];
  for (const field of KEY_FIELDS) {
    if (typeof record[field] !== "string" || record[field] === "") {
      problems.push(`${field} must be a non-empty string`);
    }
  }
  if (!HEX64.test(record.manifest_sha256)) {
    problems.push("manifest_sha256 must be 64 lowercase hexadecimal digits");
  }
  if (!Number.isInteger(record.seq) || record.seq < 1) {
    problems.push("seq must be an integer of at least 1");
  }
  if (!(STATUS_VALUES as readonly string[]).includes(record.status)) {
    problems.push("status must be open or finished");
  }
  if (!HEX64.test(record.source_digest) || !HEX64.test(record.build_json_sha256) || record.build_version === "") {
    problems.push("the creating build must be named by its version, source digest and BUILD.json SHA-256");
  }
  if (record.builds_seen.length === 0 || record.builds_seen.some((seen) => !HEX64.test(seen.source_digest) || !HEX64.test(seen.build_json_sha256) || seen.version === "")) {
    problems.push("builds_seen must list every build that touched the session");
  }
  if (!count(record.counts.pauses) || !count(record.counts.resumes) || !count(record.counts.worker_restarts)) {
    problems.push("counts must be integers of at least 0");
  }
  if (!(record.autosave_bytes instanceof Uint8Array)) {
    problems.push("autosave_bytes must be bytes");
  }
  const done = record.status === "finished";
  if (done !== (record.finished_bytes instanceof Uint8Array) || done !== (record.finished_sha256 !== null && HEX64.test(record.finished_sha256))) {
    problems.push("a finished record holds its bytes and SHA-256, an open one neither");
  }
  return problems;
}

export function keyOf(key: SessionKey): string[] {
  return KEY_FIELDS.map((field) => key[field]);
}

function upgrade(db: IDBDatabase, oldVersion: number): void {
  if (oldVersion === 0) {
    db.createObjectStore(STORE, { keyPath: [...KEY_FIELDS], autoIncrement: false });
  }
}

function owned(bytes: Uint8Array): Uint8Array {
  return bytes.slice();
}

export class StudyStore {
  private readonly db: IDBDatabase;

  private constructor(db: IDBDatabase) {
    this.db = db;
  }

  static async open(factory: IDBFactory): Promise<StudyStore> {
    return new StudyStore(await openDatabase(factory, DATABASE, DATABASE_VERSION, upgrade));
  }

  async get(key: SessionKey): Promise<SessionRecord | null> {
    const transaction = this.db.transaction(STORE, "readonly");
    const found = await request<SessionRecord | undefined>(transaction.objectStore(STORE).get(keyOf(key)));
    return found ?? null;
  }

  put(record: SessionRecord): Promise<void> {
    const problems = recordProblems(record);
    if (problems.length > 0) {
      return Promise.reject(new IdbError(`the session record is not valid: ${problems.join("; ")}`));
    }
    const value: SessionRecord = {
      ...record,
      builds_seen: record.builds_seen.map((seen) => ({ version: seen.version, build_json_sha256: seen.build_json_sha256, source_digest: seen.source_digest })),
      counts: { pauses: record.counts.pauses, resumes: record.counts.resumes, worker_restarts: record.counts.worker_restarts },
      autosave_bytes: owned(record.autosave_bytes),
      finished_bytes: record.finished_bytes === null ? null : owned(record.finished_bytes),
    };
    return new Promise((resolve, reject) => {
      let refusal: Error | null = null;
      let transaction: IDBTransaction;
      try {
        transaction = this.db.transaction(STORE, "readwrite", { durability: DURABILITY });
      } catch (error) {
        reject(new IdbError(error instanceof Error ? error.message : String(error)));
        return;
      }
      const done = finished(transaction);
      const store = transaction.objectStore(STORE);
      const lookup = store.get(keyOf(record));
      lookup.addEventListener("success", () => {
        const stored = lookup.result as SessionRecord | undefined;
        if (stored !== undefined && stored.seq >= value.seq) {
          refusal = new SeqRefused(value.seq, stored.seq);
          transaction.abort();
          return;
        }
        if (stored !== undefined && stored.status === "finished") {
          refusal = new IdbError("the session is finished and cannot be changed");
          transaction.abort();
          return;
        }
        try {
          store.put(value);
        } catch (error) {
          refusal = new IdbError(`the session record could not be written: ${error instanceof Error ? error.message : String(error)}`);
          transaction.abort();
        }
      });
      done.then(resolve, (error: unknown) => reject(refusal ?? error));
    });
  }

  close(): void {
    this.db.close();
  }
}
