import child_process from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { build } from "vite";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { buildSite } from "../../scripts/build_site.mjs";
import { BUILD_FILE, HOOK_PREFIX, HOOKS_DIR, canonicalJson, checkSite, externalUrls, hashTree, hookSymbols, walkFiles } from "../../scripts/check_site.mjs";
import { WEB_DIR, laneCacheDir } from "../../scripts/lane.mjs";
import { SITE_LIST, readList } from "../../scripts/pyodide_subset.mjs";

const PYTHON = process.env.PYTHON || process.env.SARCOSTAT_PYTHON || "python3";
const PACKAGE_DIR = path.join(WEB_DIR, "..", "src", "sarcostat");

const MAKE_WHEEL = `
import pathlib
import re
import sys
import zipfile

src, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
version = re.search(r'__version__ = "([^"]+)"', (src / "__init__.py").read_text("utf-8")).group(1)
wheel = out / f"sarcostat-{version}-py3-none-any.whl"
stamp = (1980, 1, 1, 0, 0, 0)
with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src)
        if path.is_file() and "__pycache__" not in rel.parts and not any(p.startswith(".") for p in rel.parts):
            archive.writestr(zipfile.ZipInfo("sarcostat/" + rel.as_posix(), stamp), path.read_bytes())
    meta = f"Metadata-Version: 2.1\\nName: sarcostat\\nVersion: {version}\\n"
    archive.writestr(zipfile.ZipInfo(f"sarcostat-{version}.dist-info/METADATA", stamp), meta)
print(wheel)
`;

const HOOK_SOURCE = [
  'export const HOOK_SYMBOLS = ["__sarcostatTestRecorder", "sarcostatCanaryProbe"];',
  "globalThis.__sarcostatTestRecorder = { calls: [] };",
  "globalThis.sarcostatCanaryProbe = () => globalThis.__sarcostatTestRecorder.calls.length;",
  "",
].join("\n");

type Check = ReturnType<typeof checkSite>;

let scratch = "";
let baseDir = "";
let coreList = "";
let hooksFixture = "";
let emptyHooks = "";
let cases = 0;

function makeWheel(out: string): string {
  const run = child_process.spawnSync(PYTHON, ["-I", "-S", "-B", "-c", MAKE_WHEEL, PACKAGE_DIR, out], { encoding: "utf8" });
  if (run.status !== 0) {
    throw new Error(`${PYTHON} failed: ${run.stderr || run.error}`);
  }
  return run.stdout.trim();
}

function copySite(): string {
  cases += 1;
  const dir = path.join(scratch, `case-${cases}`, path.basename(baseDir));
  fs.cpSync(baseDir, dir, { recursive: true });
  return dir;
}

function rehash(dir: string): void {
  const file = path.join(dir, BUILD_FILE);
  const record = JSON.parse(fs.readFileSync(file, "utf8"));
  record.files = hashTree(dir);
  record.wheel.sha256 = record.files[record.wheel.file];
  fs.writeFileSync(file, canonicalJson(record));
}

function firstScript(dir: string): string {
  const script = walkFiles(dir).files.find((rel: string) => rel.startsWith("assets/") && rel.endsWith(".js"));
  if (!script) {
    throw new Error(`no assets/*.js in ${dir}`);
  }
  return path.join(dir, script);
}

function check(dir: string, options: Record<string, unknown> = {}): Check {
  return checkSite(dir, { pyodideList: coreList, hooksDir: emptyHooks, ...options });
}

beforeAll(async () => {
  scratch = fs.mkdtempSync(path.join(os.tmpdir(), "sarcostat-site-check-"));
  coreList = path.join(scratch, "pyodide-core.sha256");
  const lines = readList(SITE_LIST)
    .filter((entry: { name: string }) => !entry.name.endsWith(".whl"))
    .map((entry: { sha256: string; name: string }) => `${entry.sha256}  ${entry.name}`);
  fs.writeFileSync(coreList, `${lines.join("\n")}\n`);
  hooksFixture = path.join(scratch, "hooks");
  fs.mkdirSync(hooksFixture);
  fs.writeFileSync(path.join(hooksFixture, "recorder.js"), HOOK_SOURCE);
  emptyHooks = path.join(scratch, "no-hooks");
  fs.mkdirSync(emptyHooks);
  const wheel = makeWheel(scratch);
  const built = await buildSite({ out: path.join(scratch, "site"), wheel, commit: null, sourceDateEpoch: "1700000000", python: PYTHON, pyodideList: coreList, log: () => {} });
  baseDir = built.versionDir;
}, 180_000);

afterAll(() => {
  fs.rmSync(scratch, { recursive: true, force: true });
});

describe("check_site.mjs on a built site", () => {
  it("passes the unchanged site, with the Pyodide loader files allowed by SHA-256", () => {
    const result = check(copySite());
    expect(result.problems).toEqual([]);
    expect(result.ok).toBe(true);
    expect(result.notes.some((line: string) => line.startsWith("pyodide/pyodide.mjs:") && line.includes("allowed by SHA-256"))).toBe(true);
  });

  it("fails on one changed byte, in an app file and in the wheel", () => {
    const dir = copySite();
    const script = firstScript(dir);
    const bytes = fs.readFileSync(script);
    bytes.writeUInt8(bytes.readUInt8(0) === 0x20 ? 0x21 : 0x20, 0);
    fs.writeFileSync(script, bytes);
    const record = JSON.parse(fs.readFileSync(path.join(dir, BUILD_FILE), "utf8"));
    const wheel = path.join(dir, record.wheel.file);
    const wheelBytes = fs.readFileSync(wheel);
    wheelBytes.writeUInt8(wheelBytes.readUInt8(wheelBytes.length - 1) ^ 0x01, wheelBytes.length - 1);
    fs.writeFileSync(wheel, wheelBytes);
    const result = check(dir);
    expect(result.ok).toBe(false);
    expect(result.problems).toEqual([`${path.relative(dir, script).split(path.sep).join("/")}: SHA-256 differs from BUILD.json`, `${record.wheel.file}: SHA-256 differs from BUILD.json`]);
  });

  it("fails on a file that BUILD.json does not list, and on a listed file that is missing", () => {
    const dir = copySite();
    fs.writeFileSync(path.join(dir, "assets", "extra.txt"), "not built\n");
    fs.rmSync(path.join(dir, "pyodide", "pyodide-lock.json"));
    const result = check(dir);
    expect(result.problems).toContain("assets/extra.txt is not listed in BUILD.json");
    expect(result.problems).toContain("pyodide/pyodide-lock.json is listed in BUILD.json but missing");
  });

  it("fails on an external URL in a shipped JS file even when BUILD.json is re-hashed", () => {
    const dir = copySite();
    const script = firstScript(dir);
    fs.appendFileSync(script, 'fetch("https://example.org/collect");\n');
    rehash(dir);
    const result = check(dir);
    expect(result.problems).toHaveLength(1);
    expect(result.problems[0]).toMatch(/^assets\/.+\.js: external URL\(s\) in a shipped text file: https:\/\/example\.org\/collect$/);
  });

  it("fails when src/ imports a test hook, and only on the hook symbol", async () => {
    const work = fs.mkdtempSync(path.join(laneCacheDir("vitest"), "site-hook-"));
    try {
      fs.cpSync(path.join(WEB_DIR, "src"), path.join(work, "src"), { recursive: true });
      fs.copyFileSync(path.join(WEB_DIR, "index.html"), path.join(work, "index.html"));
      fs.writeFileSync(path.join(work, "src", "test_hook.js"), HOOK_SOURCE);
      const main = path.join(work, "src", "main.ts");
      fs.writeFileSync(main, `import "./test_hook.js";\n${fs.readFileSync(main, "utf8")}`);
      const appDir = path.join(work, "app");
      await build({
        root: work,
        configFile: false,
        base: "./",
        publicDir: false,
        logLevel: "silent",
        cacheDir: path.join(work, ".vite"),
        build: { outDir: appDir, emptyOutDir: true, target: "es2023", assetsInlineLimit: 0, modulePreload: { polyfill: false } },
        worker: { format: "es" },
      });
      const dir = copySite();
      fs.rmSync(path.join(dir, "assets"), { recursive: true });
      fs.rmSync(path.join(dir, "index.html"));
      fs.cpSync(appDir, dir, { recursive: true });
      rehash(dir);
      const result = check(dir, { hooksDir: hooksFixture });
      expect(result.problems.length).toBeGreaterThan(0);
      for (const problem of result.problems) {
        expect(problem).toMatch(/^assets\/.+: test-hook symbol\(s\) in a shipped file: __sarcostatTest, sarcostatCanaryProbe$/);
      }
    } finally {
      fs.rmSync(work, { recursive: true, force: true });
    }
  }, 60_000);

  it("finds a HOOK_SYMBOLS name without the prefix only through the hook files", () => {
    const dir = copySite();
    fs.appendFileSync(firstScript(dir), "self.sarcostatCanaryProbe = 1;\n");
    rehash(dir);
    expect(check(dir).ok).toBe(true);
    const result = check(dir, { hooksDir: hooksFixture });
    expect(result.problems).toHaveLength(1);
    expect(result.problems[0]).toMatch(/: test-hook symbol\(s\) in a shipped file: sarcostatCanaryProbe$/);
  });

  it("fails above the size cap", () => {
    const dir = copySite();
    const ok = check(dir);
    expect(ok.ok).toBe(true);
    expect(check(dir, { maxBytes: ok.bytes }).ok).toBe(true);
    const over = check(dir, { maxBytes: ok.bytes - 1 });
    expect(over.problems).toEqual([`size ${ok.bytes} bytes exceeds the cap of ${ok.bytes - 1} bytes`]);
  });

  it("allows URLs by content, not by name, and pins the Pyodide files to the list", () => {
    const dir = copySite();
    fs.copyFileSync(path.join(dir, "pyodide", "pyodide.mjs"), path.join(dir, "assets", "loader.mjs"));
    rehash(dir);
    expect(check(dir).problems).toEqual([]);
    fs.appendFileSync(path.join(dir, "assets", "loader.mjs"), "\n");
    fs.appendFileSync(path.join(dir, "pyodide", "pyodide.mjs"), "\n");
    rehash(dir);
    const result = check(dir);
    expect(result.problems).toContain("pyodide/pyodide.mjs: SHA-256 differs from pyodide-core.sha256");
    expect(result.problems.some((line: string) => line.startsWith("assets/loader.mjs: external URL(s)"))).toBe(true);
    expect(result.problems.some((line: string) => line.startsWith("pyodide/pyodide.mjs: external URL(s)"))).toBe(true);
  });

  it("fails on a BUILD.json whose version, Pyodide or wheel record does not match", () => {
    const dir = copySite();
    const file = path.join(dir, BUILD_FILE);
    const record = JSON.parse(fs.readFileSync(file, "utf8"));
    fs.writeFileSync(file, canonicalJson({ ...record, version: "9.9.9", pyodide: { version: "0.0.1", dir: "pyodide/" }, wheel: { ...record.wheel, sha256: "0".repeat(64) } }));
    const problems = check(dir).problems.join("\n");
    expect(problems).toContain("does not match the folder name");
    expect(problems).toContain("pyodide must be");
    expect(problems).toContain("wheel.sha256 differs");
    fs.writeFileSync(file, JSON.stringify(record));
    expect(check(dir).problems).toEqual(["BUILD.json is not canonical JSON (sorted keys, two-space indent, ASCII, final newline)"]);
  });
});

describe("check_site.mjs rules", () => {
  it("treats only quoted XML namespace identifiers as non-URLs", () => {
    expect(externalUrls('e.createElementNS("http://www.w3.org/2000/svg","g");<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink=\'http://www.w3.org/1999/xlink\'>')).toEqual([]);
    expect(externalUrls('a="http://www.w3.org/2000/svg/extra"')).toEqual(["http://www.w3.org/2000/svg/extra"]);
    expect(externalUrls('<img src="//cdn.example.com/a.png"> url(//fonts.example.net/f.woff2) "wss://h.example/x" ftp://f.example/')).toEqual([
      "wss://h.example/x",
      "ftp://f.example/",
      "//cdn.example.com/a.png",
      "//fonts.example.net/f.woff2",
    ]);
    expect(externalUrls('import "./a.js"; fetch("../BUILD.json"); const r = /\\/\\//; x = a // b')).toEqual([]);
  });

  it("reads HOOK_SYMBOLS from the hook files, and the real hook folder uses the prefix", () => {
    expect(hookSymbols(hooksFixture).symbols).toEqual(["__sarcostatTestRecorder", "sarcostatCanaryProbe"]);
    expect(hookSymbols(path.join(scratch, "absent")).symbols).toEqual([]);
    const real = hookSymbols(HOOKS_DIR);
    for (const name of real.symbols) {
      expect(name.startsWith(HOOK_PREFIX), name).toBe(true);
    }
  });
});
