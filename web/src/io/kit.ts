export const MANIFEST_FILE = "manifest.json";
export const MANIFEST_SHA256_FILE = "manifest.sha256";

export interface KitEntry {
  readonly path: string;
  readonly file: File;
}

export type KitRefusal =
  | { readonly kind: "empty" }
  | { readonly kind: "missing"; readonly file: string }
  | { readonly kind: "unreadable"; readonly file: string };

export class KitReadError extends Error {
  readonly refusal: KitRefusal;

  constructor(refusal: KitRefusal) {
    super(refusal.kind === "empty" ? "the folder holds no files" : `${refusal.file}: ${refusal.kind}`);
    this.name = "KitReadError";
    this.refusal = refusal;
  }
}

export interface ManifestBytes {
  readonly manifest: Uint8Array;
  readonly sidecar: Uint8Array;
}

function kitPath(file: File): string {
  const relative = (file as File & { webkitRelativePath?: string }).webkitRelativePath ?? "";
  const parts = (relative === "" ? file.name : relative).split("/").filter((part) => part !== "");
  return (parts.length > 1 ? parts.slice(1) : parts).join("/");
}

export function kitEntries(files: Iterable<File>): KitEntry[] {
  const out: KitEntry[] = [];
  for (const file of files) {
    out.push({ path: kitPath(file), file });
  }
  return out;
}

export class KitFolder {
  private readonly byPath = new Map<string, File>();
  private readonly read = new Set<string>();

  constructor(entries: readonly KitEntry[]) {
    if (entries.length === 0) {
      throw new KitReadError({ kind: "empty" });
    }
    for (const entry of entries) {
      if (!this.byPath.has(entry.path)) {
        this.byPath.set(entry.path, entry.file);
      }
    }
  }

  has(path: string): boolean {
    return this.byPath.has(path);
  }

  get paths(): string[] {
    return [...this.byPath.keys()].sort();
  }

  get readPaths(): string[] {
    return [...this.read].sort();
  }

  private async once(path: string): Promise<Uint8Array> {
    const file = this.byPath.get(path);
    if (file === undefined) {
      throw new KitReadError({ kind: "missing", file: path });
    }
    if (this.read.has(path)) {
      throw new Error(`${path} was already read once`);
    }
    this.read.add(path);
    try {
      return new Uint8Array(await file.arrayBuffer());
    } catch {
      throw new KitReadError({ kind: "unreadable", file: path });
    }
  }

  async readManifest(): Promise<ManifestBytes> {
    for (const name of [MANIFEST_FILE, MANIFEST_SHA256_FILE]) {
      if (!this.byPath.has(name)) {
        throw new KitReadError({ kind: "missing", file: name });
      }
    }
    const manifest = await this.once(MANIFEST_FILE);
    const sidecar = await this.once(MANIFEST_SHA256_FILE);
    return { manifest, sidecar };
  }

  async readListed(listed: readonly string[]): Promise<Map<string, Uint8Array>> {
    const out = new Map<string, Uint8Array>();
    for (const path of new Set(listed)) {
      if (path === MANIFEST_FILE || path === MANIFEST_SHA256_FILE || !this.byPath.has(path)) {
        continue;
      }
      out.set(path, await this.once(path));
    }
    return out;
  }
}

export function copyOf(bytes: Uint8Array): Uint8Array {
  return bytes.slice();
}

export interface FolderPicker {
  pick(): Promise<File[] | null>;
}

export function createFolderPicker(doc: Document, host: HTMLElement): FolderPicker {
  const input = doc.createElement("input");
  input.type = "file";
  input.hidden = true;
  input.tabIndex = -1;
  input.multiple = true;
  input.setAttribute("webkitdirectory", "");
  input.dataset.part = "kit-input";
  host.append(input);
  let settle: ((files: File[] | null) => void) | null = null;
  const finish = (files: File[] | null): void => {
    const done = settle;
    settle = null;
    done?.(files);
  };
  input.addEventListener("change", () => finish(input.files === null ? null : [...input.files]));
  input.addEventListener("cancel", () => finish(null));
  return {
    pick() {
      finish(null);
      input.value = "";
      return new Promise((resolve) => {
        settle = resolve;
        input.click();
      });
    },
  };
}

export function downloadBytes(doc: Document, fileName: string, bytes: Uint8Array, type = "application/json"): void {
  const own = bytes.buffer instanceof ArrayBuffer ? new Uint8Array(bytes.buffer, bytes.byteOffset, bytes.byteLength) : Uint8Array.from(bytes);
  const url = URL.createObjectURL(new Blob([own], { type }));
  const link = doc.createElement("a");
  link.href = url;
  link.download = fileName;
  link.hidden = true;
  doc.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
