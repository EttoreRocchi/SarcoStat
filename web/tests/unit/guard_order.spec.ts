import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { WEB_DIR } from "../../scripts/lane.mjs";

const SRC = path.join(WEB_DIR, "src");
const WORKER = path.join(SRC, "worker", "py.worker.ts");
const GUARD = path.join(SRC, "worker", "guard.ts");
const STATIC_IMPORT = /^\s*import\s+(type\s+)?(?:[^"';]*?\s+from\s+)?["']([^"']+)["']\s*;?/gm;

function staticImports(file: string): { specifier: string; typeOnly: boolean; index: number }[] {
  const text = fs.readFileSync(file, "utf8");
  return [...text.matchAll(STATIC_IMPORT)].map((match) => ({
    specifier: match[2] as string,
    typeOnly: match[1] !== undefined,
    index: match.index ?? 0,
  }));
}

function sources(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    return entry.isDirectory() ? sources(full) : /\.(ts|mts|js|mjs)$/.test(entry.name) ? [full] : [];
  });
}

describe("guard.ts runs before anything else in the engine worker", () => {
  const text = fs.readFileSync(WORKER, "utf8");
  const imports = staticImports(WORKER);

  it("is the first import of py.worker.ts, as a side-effect import", () => {
    expect(imports[0]).toMatchObject({ specifier: "./guard.ts", typeOnly: false });
    expect(text.trimStart().startsWith('import "./guard.ts";')).toBe(true);
  });

  it("imports nothing itself, so no other module is evaluated before it", () => {
    expect(staticImports(GUARD)).toEqual([]);
    expect(fs.readFileSync(GUARD, "utf8")).not.toMatch(/\bimport\s*\(/);
  });

  it("installs itself on import in a worker", () => {
    expect(fs.readFileSync(GUARD, "utf8")).toMatch(/\nif \("WorkerGlobalScope" in globalThis\) \{\n\s+installGuard\(globalThis as unknown as GuardScope\);\n\}\n$/);
  });

  it("is followed by Pyodide's loader only as a type import and one dynamic import() called during boot", () => {
    const pyodide = imports.filter((entry) => entry.specifier === "pyodide" || entry.specifier.startsWith("pyodide/"));
    expect(pyodide.every((entry) => entry.typeOnly)).toBe(true);
    const dynamic = [...text.matchAll(/\bimport\s*\(/g)];
    expect(dynamic).toHaveLength(1);
    const guardAt = imports[0]?.index ?? -1;
    expect(dynamic[0]?.index ?? -1).toBeGreaterThan(guardAt);
    expect(text).toMatch(/async function importLoader\(url: string\): Promise<PyodideLoader> \{\n\s+return \(await import\(url\)\) as PyodideLoader;\n\}/);
    const loads = [...text.matchAll(/\.loadPyodide\(/g)];
    expect(loads).toHaveLength(1);
    const bootAt = text.indexOf("async function boot(");
    expect(bootAt).toBeGreaterThan(0);
    expect(loads[0]?.index ?? -1).toBeGreaterThan(bootAt);
    expect(text.indexOf("importLoader(sameOriginUrl(LOADER_FILE, pyodideDir))")).toBeGreaterThan(bootAt);
  });

  it("is the only place that reaches Pyodide: no source file imports it as a value", () => {
    for (const file of sources(SRC)) {
      for (const entry of staticImports(file)) {
        if (entry.specifier === "pyodide" || entry.specifier.startsWith("pyodide/")) {
          expect(entry.typeOnly, path.relative(WEB_DIR, file)).toBe(true);
        }
      }
    }
  });

  it("points Pyodide's index, lock file and package base at the version folder", () => {
    expect(text).toMatch(/indexURL: pyodideDir,/);
    expect(text).toMatch(/lockFileURL: sameOriginUrl\(LOCK_FILE, pyodideDir\),/);
    expect(text).toMatch(/packageBaseUrl: pyodideDir,/);
    expect(text).toMatch(/loadPackage\(\["numpy"\]/);
    expect(text).not.toMatch(/cdn\.|jsdelivr|https?:\/\//);
  });
});
