import { describe, expect, it } from "vitest";

import { KitFolder, KitReadError, kitEntries } from "../../src/io/kit.ts";

class SpyFile extends File {
  static reads: string[] = [];
  readonly relative: string;

  constructor(relative: string, body: string) {
    super([body], relative.split("/").pop() as string);
    this.relative = relative;
    Object.defineProperty(this, "webkitRelativePath", { value: relative });
  }

  override arrayBuffer(): Promise<ArrayBuffer> {
    SpyFile.reads.push(this.relative);
    return super.arrayBuffer();
  }

  override text(): Promise<string> {
    SpyFile.reads.push(`text ${this.relative}`);
    return super.text();
  }
}

function folder(paths: Record<string, string>): KitFolder {
  SpyFile.reads = [];
  return new KitFolder(kitEntries(Object.entries(paths).map(([path, body]) => new SpyFile(`kit/${path}`, body))));
}

describe("kit folder reading", () => {
  it("strips the chosen folder's name and reads the manifest, its sidecar and each listed file exactly once", async () => {
    const kit = folder({ "manifest.json": "{}", "manifest.sha256": "x", "items/001.png": "a", "items/002.png": "b", "notes.txt": "n", "items/999.png": "z" });
    expect(kit.paths).toEqual(["items/001.png", "items/002.png", "items/999.png", "manifest.json", "manifest.sha256", "notes.txt"]);
    const manifest = await kit.readManifest();
    expect(new TextDecoder().decode(manifest.manifest)).toBe("{}");
    const items = await kit.readListed(["items/001.png", "items/002.png", "items/001.png", "items/404.png", "manifest.json"]);
    expect([...items.keys()]).toEqual(["items/001.png", "items/002.png"]);
    expect(SpyFile.reads).toEqual(["kit/manifest.json", "kit/manifest.sha256", "kit/items/001.png", "kit/items/002.png"]);
    await expect(kit.readListed(["items/001.png"])).rejects.toThrow("already read once");
    expect(SpyFile.reads).toHaveLength(4);
  });

  it("refuses an empty folder and a folder without the manifest or its sidecar before reading anything", async () => {
    expect(() => folder({})).toThrow(KitReadError);
    const missing = folder({ "manifest.json": "{}", "items/001.png": "a" });
    await expect(missing.readManifest()).rejects.toMatchObject({ refusal: { kind: "missing", file: "manifest.sha256" } });
    expect(SpyFile.reads).toEqual([]);
  });
});
