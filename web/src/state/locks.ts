import { KEY_FIELDS, type SessionKey } from "./study_store_v1.ts";

export const LOCK_PREFIX = "sarcostat-study-v1:";

export interface HeldLock {
  readonly name: string;
  release(): void;
}

export function lockName(key: SessionKey): string {
  return LOCK_PREFIX + KEY_FIELDS.map((field) => key[field]).join("/");
}

export function acquireSessionLock(locks: LockManager, key: SessionKey): Promise<HeldLock | null> {
  const name = lockName(key);
  return new Promise((resolve, reject) => {
    locks
      .request(name, { ifAvailable: true }, (lock) => {
        if (lock === null) {
          resolve(null);
          return undefined;
        }
        return new Promise<void>((release) => {
          resolve({ name, release: () => release() });
        });
      })
      .catch((error: unknown) => reject(error instanceof Error ? error : new Error(String(error))));
  });
}
