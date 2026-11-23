import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { parseArgs } from "node:util";

import { installOfflineGuard, offlineRefusals, sha256 } from "./pyodide_runtime.mjs";

const USAGE = [
  "Usage: node tests/pyodide/site_boot.mjs --site <site root>/v<version> [--offline]",
  "",
  "Boots Pyodide under Node from the version folder's own pyodide/ files, as the worker does in the browser: numpy only, the wheel checked against BUILD.json and unpacked with unpackArchive (no micropip), sys.identity through the bridge, then scipy on demand for sys.load_scipy.",
  "Passes when sys.identity gives BUILD.json's version and source_digest and bridge_version 1, scipy is not loaded before sys.load_scipy, and (with --offline) no network attempt was made.",
].join("\n");

const PYTHON_DISPATCH = `
from sarcostat.engine.bridge import dispatch


def sarcostat_site_dispatch(request):
    text, buffers = dispatch(request)
    if buffers:
        raise ValueError("the bridge answered with buffers")
    return text
`;

async function main() {
  const { values } = parseArgs({
    options: {
      site: { type: "string" },
      offline: { type: "boolean", default: false },
      help: { type: "boolean", default: false },
    },
    strict: true,
  });
  if (values.help || !values.site) {
    console.log(USAGE);
    return values.help ? 0 : 2;
  }
  console.log(`node ${process.version}`);
  const siteDir = path.resolve(values.site);
  const build = JSON.parse(fs.readFileSync(path.join(siteDir, "BUILD.json"), "utf8"));
  const pyodideDir = path.join(siteDir, build.pyodide.dir);
  const wheelBytes = fs.readFileSync(path.join(siteDir, build.wheel.file));
  const problems = [];
  if (sha256(wheelBytes) !== build.wheel.sha256) {
    console.error(`site_boot: FAIL ${build.wheel.file}: SHA-256 differs from BUILD.json; the wheel is not installed (the worker's build_damaged state)`);
    console.log("RESULT: FAIL (1 problem(s))");
    return 1;
  }
  if (values.offline) {
    installOfflineGuard();
    console.log("network: offline guard on");
  }
  const started = performance.now();
  const { loadPyodide } = await import(pathToFileURL(path.join(pyodideDir, "pyodide.mjs")).href);
  const py = await loadPyodide({
    indexURL: `${pyodideDir}${path.sep}`,
    lockFileURL: path.join(pyodideDir, "pyodide-lock.json"),
    packageBaseUrl: `${pyodideDir}${path.sep}`,
    packageCacheDir: `${pyodideDir}${path.sep}`,
    env: { HOME: "/home/pyodide" },
    stdout: (line) => console.log(line),
    stderr: (line) => console.error(line),
  });
  if (py.version !== build.pyodide.version) {
    problems.push(`Pyodide reports ${py.version}, BUILD.json ${build.pyodide.version}`);
  }
  const quiet = { messageCallback: () => {}, errorCallback: (line) => console.error(line), checkIntegrity: true };
  await py.loadPackage(["numpy"], quiet);
  py.unpackArchive(new Uint8Array(wheelBytes), "wheel");
  py.runPython(PYTHON_DISPATCH);
  const dispatch = py.globals.get("sarcostat_site_dispatch");
  let id = 0;
  const call = (cmd, args) => {
    id += 1;
    const reply = JSON.parse(dispatch(JSON.stringify({ v: 1, id, cmd, args })));
    if (!reply.ok) {
      throw new Error(`${cmd}: ${reply.error.code}: ${reply.error.message}`);
    }
    return reply.result;
  };
  const identity = call("sys.identity", {});
  const bootSeconds = (performance.now() - started) / 1000;
  const loadedBefore = Object.keys(py.loadedPackages).sort();
  if (loadedBefore.includes("scipy")) {
    problems.push("scipy was loaded before sys.load_scipy");
  }
  if (identity.sarcostat_version !== build.version) {
    problems.push(`sys.identity version ${identity.sarcostat_version}, BUILD.json ${build.version}`);
  }
  if (identity.source_digest !== build.source_digest) {
    problems.push(`sys.identity source_digest ${identity.source_digest}, BUILD.json ${build.source_digest}`);
  }
  if (identity.bridge_version !== 1) {
    problems.push(`sys.identity bridge_version ${identity.bridge_version}`);
  }
  await py.loadPackage(["scipy"], quiet);
  const scipy = call("sys.load_scipy", {});
  const refusals = offlineRefusals();
  problems.push(...refusals);
  console.log(`site: ${siteDir}`);
  console.log(`boot (numpy only, wheel unpacked, sys.identity): ${bootSeconds.toFixed(2)} s; packages loaded then: ${loadedBefore.join(", ")}`);
  console.log(`sys.identity: sarcostat ${identity.sarcostat_version}, bridge_version ${identity.bridge_version}, source_digest ${identity.source_digest}`);
  console.log(`BUILD.json:   version ${build.version}, source_digest ${build.source_digest}`);
  console.log(`sys.load_scipy: scipy ${scipy.scipy_version}`);
  console.log(`network attempts refused: ${refusals.length}`);
  problems.forEach((line) => console.error(`site_boot: FAIL ${line}`));
  console.log(problems.length === 0 ? "RESULT: PASS" : `RESULT: FAIL (${problems.length} problem(s))`);
  return problems.length === 0 ? 0 : 1;
}

main().then(
  (code) => {
    process.exitCode = code;
  },
  (error) => {
    console.error(error?.stack ?? String(error));
    process.exitCode = 2;
  },
);
