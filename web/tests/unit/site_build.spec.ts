import child_process from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { BuildRefused, buildSite, checkOut, parseEpoch, wheelVersion } from "../../scripts/build_site.mjs";
import { canonicalJson, checkSite, hashTree } from "../../scripts/check_site.mjs";
import { WEB_DIR } from "../../scripts/lane.mjs";
import { SITE_LIST, readList } from "../../scripts/pyodide_subset.mjs";

const PYTHON = process.env.PYTHON || process.env.SARCOSTAT_PYTHON || "python3";
const PACKAGE_DIR = path.join(WEB_DIR, "..", "src", "sarcostat");
const EPOCH = "1700000000";
const COMMIT = "0123456789abcdef0123456789abcdef01234567";

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

const TREE_DIGEST = `
import sys
sys.path.insert(0, sys.argv[1])
from sarcostat.io.provenance import source_digest
print(source_digest(sys.argv[1] + "/sarcostat"))
`;

function python(script: string, args: string[]): string {
  const run = child_process.spawnSync(PYTHON, ["-I", "-S", "-B", "-c", script, ...args], { encoding: "utf8" });
  if (run.status !== 0) {
    throw new Error(`${PYTHON} failed: ${run.stderr || run.error}`);
  }
  return run.stdout.trim();
}

function coreList(dir: string): string {
  const file = path.join(dir, "pyodide-core.sha256");
  const lines = readList(SITE_LIST)
    .filter((entry: { name: string }) => !entry.name.endsWith(".whl"))
    .map((entry: { sha256: string; name: string }) => `${entry.sha256}  ${entry.name}`);
  fs.writeFileSync(file, `${lines.join("\n")}\n`);
  return file;
}

let scratch = "";

beforeAll(() => {
  scratch = fs.mkdtempSync(path.join(os.tmpdir(), "sarcostat-site-build-"));
});

afterAll(() => {
  fs.rmSync(scratch, { recursive: true, force: true });
});

describe("BUILD.json text", () => {
  it("is canonical: sorted keys, two-space indent, ASCII escapes, final newline", () => {
    const text = canonicalJson({ b: [1, { z: null, a: "µ→" }], a: { files: {} }, c: [] });
    expect(text).toBe('{\n  "a": {\n    "files": {}\n  },\n  "b": [\n    1,\n    {\n      "a": "\\u00b5\\u2192",\n      "z": null\n    }\n  ],\n  "c": []\n}\n');
  });
});

describe("build_site.mjs inputs", () => {
  it("needs SOURCE_DATE_EPOCH as whole seconds", () => {
    for (const bad of [undefined, "", "abc", "-1", "1.5", "1e9"]) {
      expect(() => parseEpoch(bad), String(bad)).toThrow(BuildRefused);
    }
    expect(parseEpoch("0")).toBe(0);
    expect(parseEpoch(EPOCH)).toBe(1700000000);
  });

  it("takes the version from a sarcostat wheel name", () => {
    expect(wheelVersion("/x/sarcostat-0.2.0rc1.dev0-py3-none-any.whl")).toBe("0.2.0rc1.dev0");
    expect(wheelVersion("sarcostat-0.2.0-py3-none-any.whl")).toBe("0.2.0");
    expect(wheelVersion("sarcostat-0.2.0rc1.post1-py3-none-any.whl")).toBe("0.2.0rc1.post1");
    for (const bad of ["sarcostat-0.2.0+local-py3-none-any.whl", "other-0.2.0-py3-none-any.whl", "sarcostat-0.2.0.tar.gz", "sarcostat-0.2.0-cp312-cp312-linux_x86_64.whl"]) {
      expect(() => wheelVersion(bad), bad).toThrow(BuildRefused);
    }
  });

  it("refuses an --out that holds files outside web/dist/ and accepts one inside it", () => {
    const fresh = path.join(scratch, "fresh");
    expect(checkOut(fresh).existed).toBe(false);
    fs.mkdirSync(fresh);
    expect(checkOut(fresh).existed).toBe(true);
    fs.writeFileSync(path.join(fresh, "keep.txt"), "x");
    expect(() => checkOut(fresh)).toThrow(/already holds files/);
    fs.mkdirSync(path.join(WEB_DIR, "dist"), { recursive: true });
    const inDist = fs.mkdtempSync(path.join(WEB_DIR, "dist", ".site-unit-"));
    try {
      fs.writeFileSync(path.join(inDist, "other.txt"), "x");
      expect(checkOut(inDist).existed).toBe(true);
      expect(() => checkOut(path.join(WEB_DIR, "dist"))).toThrow(/already holds files/);
    } finally {
      fs.rmSync(inDist, { recursive: true, force: true });
    }
  });

  it("refuses to build without SOURCE_DATE_EPOCH and writes nothing", async () => {
    const out = path.join(scratch, "no-epoch");
    await expect(buildSite({ out, wheel: path.join(scratch, "sarcostat-0.2.0-py3-none-any.whl"), sourceDateEpoch: undefined, log: () => {} })).rejects.toThrow(/SOURCE_DATE_EPOCH/);
    expect(fs.existsSync(out)).toBe(false);
  });
});

describe("a site built twice from the same inputs", () => {
  it("has the same BUILD.json and the same file hashes, and CPython's source_digest of the wheel", async () => {
    const wheel = python(MAKE_WHEEL, [PACKAGE_DIR, scratch]);
    const list = coreList(scratch);
    const options = { wheel, commit: COMMIT, sourceDateEpoch: EPOCH, python: PYTHON, pyodideList: list, log: () => {} };
    const first = await buildSite({ ...options, out: path.join(scratch, "a") });
    const second = await buildSite({ ...options, out: path.join(scratch, "b") });
    const version = path.basename(first.versionDir);
    expect(fs.readdirSync(path.join(scratch, "a"))).toEqual([version]);
    expect(fs.readFileSync(path.join(second.versionDir, "BUILD.json"))).toEqual(fs.readFileSync(path.join(first.versionDir, "BUILD.json")));
    expect(hashTree(second.versionDir, { exclude: [] })).toEqual(hashTree(first.versionDir, { exclude: [] }));

    const record = first.record;
    expect(`v${record.version}`).toBe(version);
    expect(record.commit).toBe(COMMIT);
    expect(record.source_date_epoch).toBe(Number(EPOCH));
    expect(record.wheel.file).toBe(`wheels/${path.basename(wheel)}`);
    expect(record.files[record.wheel.file]).toBe(record.wheel.sha256);
    expect(record.source_digest).toBe(python(TREE_DIGEST, [path.dirname(PACKAGE_DIR)]));
    expect(record.pyodide).toEqual({ version: "314.0.8", dir: "pyodide/" });
    expect(record.resume_digests).toEqual([]);
    expect(Object.keys(record.tools).sort()).toEqual(["node", "python", "typescript", "vite"]);
    expect(Object.keys(record.files)).toContain("index.html");
    expect(Object.keys(record.files)).toContain("pyodide/pyodide.mjs");
    expect(Object.keys(record.files)).not.toContain("BUILD.json");
    expect(fs.statSync(path.join(first.versionDir, "index.html")).mtimeMs).toBe(Number(EPOCH) * 1000);

    const check = checkSite(first.versionDir, { pyodideList: list });
    expect(check.problems).toEqual([]);
    expect(check.ok).toBe(true);
  }, 180_000);
});
