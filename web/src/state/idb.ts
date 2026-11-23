export type Upgrade = (db: IDBDatabase, oldVersion: number, transaction: IDBTransaction) => void;

export class IdbError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "IdbError";
  }
}

function describe(error: DOMException | null | undefined, fallback: string): string {
  if (error === null || error === undefined) {
    return fallback;
  }
  return error.message === "" ? error.name : `${error.name}: ${error.message}`;
}

export function request<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    req.addEventListener("success", () => resolve(req.result));
    req.addEventListener("error", () => reject(new IdbError(describe(req.error, "the request failed"))));
  });
}

export function finished(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.addEventListener("complete", () => resolve());
    transaction.addEventListener("abort", () => reject(new IdbError(describe(transaction.error, "the transaction was aborted"))));
    transaction.addEventListener("error", (event) => event.preventDefault());
  });
}

export function openDatabase(factory: IDBFactory, name: string, version: number, upgrade: Upgrade): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    let req: IDBOpenDBRequest;
    try {
      req = factory.open(name, version);
    } catch (error) {
      reject(new IdbError(error instanceof Error ? error.message : String(error)));
      return;
    }
    req.addEventListener("upgradeneeded", (event) => {
      const transaction = req.transaction;
      if (transaction === null) {
        return;
      }
      upgrade(req.result, (event as IDBVersionChangeEvent).oldVersion, transaction);
    });
    req.addEventListener("success", () => resolve(req.result));
    req.addEventListener("error", () => reject(new IdbError(describe(req.error, `${name} could not be opened`))));
    req.addEventListener("blocked", () => reject(new IdbError(`${name} is blocked by another tab`)));
  });
}
