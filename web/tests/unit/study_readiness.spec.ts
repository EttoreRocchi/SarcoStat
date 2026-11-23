import { describe, expect, it } from "vitest";

import { PERSIST_WAIT_MS, requestPersistence } from "../../src/view/study/readiness.ts";

function navigatorWith(storage: Partial<StorageManager> | undefined): Navigator {
  return { storage } as unknown as Navigator;
}

describe("requestPersistence (readiness check, plan 3.5 and Q6)", () => {
  it("waits at most a few seconds for a permission prompt nobody answers, then reports persisted()", async () => {
    expect(PERSIST_WAIT_MS).toBe(3_000);
    const nav = navigatorWith({ persist: () => new Promise<boolean>(() => undefined), persisted: async () => false });
    const started = Date.now();
    await expect(requestPersistence(nav, 20)).resolves.toBe(false);
    expect(Date.now() - started).toBeLessThan(1_000);
  });

  it("reports true once the browser grants persistent storage", async () => {
    let granted = false;
    const nav = navigatorWith({
      persist: async () => {
        granted = true;
        return true;
      },
      persisted: async () => granted,
    });
    await expect(requestPersistence(nav, 1_000)).resolves.toBe(true);
  });

  it("reports false without a StorageManager, when persist() rejects, or when persisted() throws", async () => {
    await expect(requestPersistence(navigatorWith(undefined), 20)).resolves.toBe(false);
    await expect(requestPersistence(navigatorWith({ persist: () => Promise.reject(new Error("denied")), persisted: async () => false }), 20)).resolves.toBe(false);
    await expect(requestPersistence(navigatorWith({ persist: async () => true, persisted: () => Promise.reject(new Error("broken")) }), 20)).resolves.toBe(false);
  });
});
