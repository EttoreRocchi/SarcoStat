import child_process from "node:child_process";
import crypto from "node:crypto";
import dgram from "node:dgram";
import dns from "node:dns";
import fs from "node:fs";
import fsp from "node:fs/promises";
import http from "node:http";
import http2 from "node:http2";
import https from "node:https";
import { syncBuiltinESMExports } from "node:module";
import net from "node:net";
import path from "node:path";
import tls from "node:tls";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WEB_DIR = path.resolve(HERE, "..", "..");
const MARK_EXPRESSION = "not needs_cpython and not needs_sim";
const DEFAULT_SUPPORT = ["pyproject.toml", "tests/__init__.py", "tests/conftest.py", "examples", "docs"];
const SKIPPED_NAMES = new Set(["__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"]);
const WORKDIR = "/work";

const USAGE = [
  "Usage: node tests/pyodide/run_pytest.mjs --wheel <sarcostat wheel> --tests <dir,...> [--offline] [--junit <file>]",
  "       [--min-pass <fraction>] [--root <repository root>] [--subset <dir>] [--list <sha256 file>] [--deselect-file <json>]",
  "",
  "Runs pytest on tests/<dir> for each folder of --tests inside Pyodide under Node, with the given wheel installed offline and the packages loaded from the verified test subset.",
  "--offline replaces fetch and blocks sockets, DNS, WebSocket and child processes before Pyodide loads; any attempt fails the run.",
  "Tests marked needs_cpython or needs_sim are deselected; --deselect-file (development only) adds a JSON object {node id: reason}.",
  "The run fails on any failed or erroring test, on a collection error, or when passed/collected is below --min-pass (default 0.95).",
  "Defaults: --root is the repository holding web/, --subset web/.cache/pyodide-test/<pyodide version>, --list web/pyodide-test-files.sha256.",
].join("\n");

const refusals = [];
const unhandled = [];

process.on("unhandledRejection", (reason) => {
  const text = reason?.stack ?? String(reason);
  unhandled.push(text);
  console.error(`run_pytest: unhandled rejection: ${text}`);
});

function describeTarget(args) {
  const first = args[0];
  if (first === undefined || first === null) {
    return "";
  }
  if (typeof first === "string" || typeof first === "number") {
    return String(first);
  }
  if (first instanceof URL) {
    return first.href;
  }
  if (typeof first === "object") {
    return String(first.href ?? first.url ?? first.host ?? first.hostname ?? first.path ?? JSON.stringify(first));
  }
  return String(first);
}

function refuse(route, target) {
  const message = `offline: refused ${route} ${target}`.trim();
  refusals.push(message);
  console.error(message);
  const error = new Error(message);
  error.code = "ERR_SARCOSTAT_OFFLINE";
  return error;
}

function blocker(route) {
  return function blocked(...args) {
    throw refuse(route, describeTarget(args));
  };
}

function installOfflineGuard() {
  globalThis.fetch = async function offlineFetch(input) {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : String(input?.url ?? input);
    if (!url.startsWith("file:")) {
      throw refuse("fetch", url);
    }
    return new Response(await fsp.readFile(fileURLToPath(url)), { status: 200 });
  };
  class OfflineSocket {
    constructor(url) {
      throw refuse("WebSocket", String(url));
    }
  }
  globalThis.WebSocket = OfflineSocket;
  if ("EventSource" in globalThis) {
    globalThis.EventSource = OfflineSocket;
  }
  net.connect = blocker("net.connect");
  net.createConnection = blocker("net.createConnection");
  net.Socket.prototype.connect = blocker("net.Socket.connect");
  tls.connect = blocker("tls.connect");
  http.request = blocker("http.request");
  http.get = blocker("http.get");
  https.request = blocker("https.request");
  https.get = blocker("https.get");
  http2.connect = blocker("http2.connect");
  dgram.createSocket = blocker("dgram.createSocket");
  for (const name of Object.keys(dns)) {
    if (name === "lookup" || name === "lookupService" || name.startsWith("resolve")) {
      dns[name] = blocker(`dns.${name}`);
    }
  }
  for (const name of Object.keys(dns.promises)) {
    if (name === "lookup" || name === "lookupService" || name.startsWith("resolve")) {
      dns.promises[name] = blocker(`dns.promises.${name}`);
    }
  }
  for (const name of ["spawn", "spawnSync", "exec", "execSync", "execFile", "execFileSync", "fork"]) {
    child_process[name] = blocker(`child_process.${name}`);
  }
  syncBuiltinESMExports();
}

function sha256(bytes) {
  return crypto.createHash("sha256").update(bytes).digest("hex");
}

function readList(file) {
  return fs
    .readFileSync(file, "utf8")
    .split("\n")
    .filter((line) => line !== "")
    .map((line) => {
      const match = /^([0-9a-f]{64}) {2}(\S+)$/.exec(line);
      if (!match) {
        throw new Error(`${file}: bad line ${JSON.stringify(line)}`);
      }
      return { sha256: match[1], name: match[2] };
    });
}

function verifySubset(listFile, subsetDir) {
  const entries = readList(listFile);
  const problems = [];
  let bytes = 0;
  for (const entry of entries) {
    const file = path.join(subsetDir, entry.name);
    if (!fs.existsSync(file)) {
      problems.push(`missing ${entry.name}`);
      continue;
    }
    const data = fs.readFileSync(file);
    bytes += data.length;
    const actual = sha256(data);
    if (actual !== entry.sha256) {
      problems.push(`SHA-256 mismatch ${entry.name}: expected ${entry.sha256}, got ${actual}`);
    }
  }
  return { entries, problems, bytes };
}

function copyIntoPyodide(py, hostPath, fsPath) {
  const stat = fs.statSync(hostPath);
  if (stat.isDirectory()) {
    py.FS.mkdirTree(fsPath);
    let count = 0;
    for (const name of fs.readdirSync(hostPath).sort()) {
      if (SKIPPED_NAMES.has(name) || name.endsWith(".pyc")) {
        continue;
      }
      count += copyIntoPyodide(py, path.join(hostPath, name), `${fsPath}/${name}`);
    }
    return count;
  }
  py.FS.mkdirTree(path.posix.dirname(fsPath));
  py.FS.writeFile(fsPath, fs.readFileSync(hostPath));
  return 1;
}

const PYTHON_DRIVER = `
import json
import os
import sys

sys.dont_write_bytecode = True
_cfg = json.loads(RUN_CONFIG)
os.chdir(_cfg["workdir"])

import pytest


def _marker_reason(item, deselect):
    for name in ("needs_cpython", "needs_sim"):
        mark = item.get_closest_marker(name)
        if mark is not None:
            reason = mark.kwargs.get("reason") or (mark.args[0] if mark.args else "no reason given")
            return f"{name}: {reason}"
    if item.nodeid in deselect:
        return f"deselect file: {deselect[item.nodeid]}"
    return "deselected by the -m expression"


def _skip_reason(report):
    longrepr = report.longrepr
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        return str(longrepr[2]).removeprefix("Skipped: ")
    return str(longrepr)


class SarcoStatPyodideSummary:
    def __init__(self, deselect):
        self.deselect = deselect
        self.collected = []
        self.deselected = {}
        self.outcomes = {}
        self.reasons = {}
        self.failures = {}
        self.collect_errors = {}
        self.collect_skips = {}

    def pytest_itemcollected(self, item):
        self.collected.append(item.nodeid)

    def pytest_collectreport(self, report):
        if report.failed:
            self.collect_errors[report.nodeid] = report.longreprtext[-4000:]
        elif report.skipped:
            self.collect_skips[report.nodeid] = _skip_reason(report)

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, config, items):
        dropped = [item for item in items if item.nodeid in self.deselect]
        if dropped:
            items[:] = [item for item in items if item.nodeid not in self.deselect]
            config.hook.pytest_deselected(items=dropped)

    def pytest_deselected(self, items):
        for item in items:
            self.deselected[item.nodeid] = _marker_reason(item, self.deselect)

    def pytest_runtest_logreport(self, report):
        nodeid = report.nodeid
        if report.when == "setup":
            if report.failed:
                self.outcomes[nodeid] = "error"
                self.failures[nodeid] = report.longreprtext[-4000:]
            elif report.skipped:
                self.outcomes[nodeid] = "skipped"
                self.reasons[nodeid] = _skip_reason(report)
        elif report.when == "call":
            if hasattr(report, "wasxfail"):
                self.outcomes[nodeid] = "xfailed" if report.skipped else "xpassed"
            elif report.skipped:
                self.outcomes[nodeid] = "skipped"
                self.reasons[nodeid] = _skip_reason(report)
            else:
                self.outcomes[nodeid] = report.outcome
            if report.failed:
                self.failures[nodeid] = report.longreprtext[-4000:]
        elif report.failed:
            self.outcomes[nodeid] = "error"
            self.failures[nodeid] = report.longreprtext[-4000:]


_args = list(_cfg["paths"]) + ["-p", "no:cacheprovider", "-m", _cfg["markexpr"], "-q", "-rs"]
if _cfg["junit"]:
    _args += ["--junitxml", _cfg["junit"]]
_plugin = SarcoStatPyodideSummary(_cfg["deselect"])
_code = int(pytest.main(_args, plugins=[_plugin]))
json.dumps(
    {
        "exit_code": _code,
        "args": _args,
        "collected": _plugin.collected,
        "deselected": _plugin.deselected,
        "outcomes": _plugin.outcomes,
        "reasons": _plugin.reasons,
        "failures": _plugin.failures,
        "collect_errors": _plugin.collect_errors,
        "collect_skips": _plugin.collect_skips,
        "pytest": pytest.__version__,
    }
)
`;

const PYTHON_ENVIRONMENT = `
import json
import os
import platform
import sys

import numpy
import scipy

import sarcostat

json.dumps(
    {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "numpy": numpy.__version__,
        "scipy": scipy.__version__,
        "sarcostat": sarcostat.__version__,
        "sarcostat_file": sarcostat.__file__,
        "os_link": hasattr(os, "link"),
    }
)
`;

async function main() {
  const { values } = parseArgs({
    options: {
      wheel: { type: "string" },
      tests: { type: "string" },
      offline: { type: "boolean", default: false },
      junit: { type: "string" },
      "min-pass": { type: "string", default: "0.95" },
      root: { type: "string" },
      subset: { type: "string" },
      list: { type: "string" },
      "deselect-file": { type: "string" },
      help: { type: "boolean", default: false },
    },
    strict: true,
  });
  if (values.help) {
    console.log(USAGE);
    return 0;
  }
  if (!values.wheel || !values.tests) {
    console.error(`run_pytest: --wheel and --tests are required\n${USAGE}`);
    return 2;
  }
  const minPass = Number(values["min-pass"]);
  if (!(minPass > 0 && minPass <= 1)) {
    console.error(`run_pytest: --min-pass must be in (0, 1], got ${values["min-pass"]}`);
    return 2;
  }

  console.log(`node ${process.version}`);
  if (!process.version.startsWith("v24.")) {
    console.error(`run_pytest: Node 24 is required (web/.nvmrc), this is ${process.version}`);
    return 2;
  }

  if (values.offline) {
    installOfflineGuard();
    console.log("network: offline guard on (fetch refuses every non-file: URL; sockets, DNS, WebSocket and child processes refused)");
  } else {
    console.log("network: offline guard OFF (pass --offline for V-PYODIDE)");
  }

  const pkg = JSON.parse(fs.readFileSync(path.join(WEB_DIR, "package.json"), "utf8"));
  const pyodideVersion = pkg.devDependencies?.pyodide;
  const runtimeDir = path.dirname(fileURLToPath(import.meta.resolve("pyodide/package.json")));
  const runtimeVersion = JSON.parse(fs.readFileSync(path.join(runtimeDir, "package.json"), "utf8")).version;
  if (runtimeVersion !== pyodideVersion) {
    console.error(`run_pytest: node_modules/pyodide is ${runtimeVersion}, web/package.json pins ${pyodideVersion}`);
    return 2;
  }
  const root = path.resolve(values.root ?? path.join(WEB_DIR, ".."));
  const subsetDir = path.resolve(values.subset ?? path.join(WEB_DIR, ".cache", "pyodide-test", pyodideVersion));
  const listFile = path.resolve(values.list ?? path.join(WEB_DIR, "pyodide-test-files.sha256"));
  const wheelPath = path.resolve(values.wheel);
  const wheelName = path.basename(wheelPath);
  const wheelBytes = fs.readFileSync(wheelPath);
  const wheelSha = sha256(wheelBytes);
  console.log(`wheel: ${wheelPath}`);
  console.log(`wheel sha256: ${wheelSha}  ${wheelName}`);

  const subset = verifySubset(listFile, subsetDir);
  if (subset.problems.length > 0) {
    subset.problems.forEach((line) => console.error(`run_pytest: subset ${subsetDir}: ${line}`));
    console.error(`run_pytest: the test subset is not complete; run: node scripts/pyodide_subset.mjs --list ${listFile} --out ${subsetDir} (--from <dist> | --download https://cdn.jsdelivr.net/pyodide/v${pyodideVersion}/full/)`);
    return 2;
  }
  const lock = JSON.parse(fs.readFileSync(path.join(runtimeDir, "pyodide-lock.json"), "utf8"));
  const byFile = new Map(Object.values(lock.packages).map((entry) => [entry.file_name, entry]));
  const packageNames = [];
  for (const entry of subset.entries) {
    const locked = byFile.get(entry.name);
    if (!locked || locked.sha256 !== entry.sha256) {
      console.error(`run_pytest: ${entry.name} in ${listFile} does not match ${path.join(runtimeDir, "pyodide-lock.json")}`);
      return 2;
    }
    packageNames.push(locked.name);
  }
  console.log(`subset: ${subsetDir}: ${subset.entries.length} files, ${subset.bytes} bytes, SHA-256 verified against ${listFile} and the lock file`);

  const requested = values.tests.split(",").map((name) => name.trim()).filter(Boolean);
  const present = [];
  const missing = [];
  for (const name of requested) {
    if (!/^[A-Za-z0-9_]+$/.test(name)) {
      console.error(`run_pytest: --tests takes folder names under tests/, got ${name}`);
      return 2;
    }
    (fs.existsSync(path.join(root, "tests", name)) ? present : missing).push(name);
  }
  missing.forEach((name) => console.log(`tests/${name}: not found in ${root}; skipped (not an error)`));
  if (present.length === 0) {
    console.error("run_pytest: none of the requested test folders exists");
    return 2;
  }
  let deselect = {};
  if (values["deselect-file"]) {
    deselect = JSON.parse(fs.readFileSync(values["deselect-file"], "utf8"));
    console.log(`DEVELOPMENT: deselect file ${values["deselect-file"]} with ${Object.keys(deselect).length} node id(s)`);
  }

  const timing = {};
  let mark = performance.now();
  const lap = (name) => {
    const now = performance.now();
    timing[name] = (now - mark) / 1000;
    mark = now;
  };

  const { loadPyodide } = await import("pyodide");
  const py = await loadPyodide({
    indexURL: `${runtimeDir}${path.sep}`,
    lockFileURL: path.join(runtimeDir, "pyodide-lock.json"),
    packageBaseUrl: `${subsetDir}${path.sep}`,
    packageCacheDir: `${subsetDir}${path.sep}`,
    env: { HOME: "/home/pyodide" },
    stdout: (line) => console.log(line),
    stderr: (line) => console.error(line),
  });
  lap("load_pyodide");
  if (py.version !== pyodideVersion) {
    console.error(`run_pytest: Pyodide reports ${py.version}, expected ${pyodideVersion}`);
    return 2;
  }
  await py.loadPackage(packageNames, {
    messageCallback: () => {},
    errorCallback: (message) => console.error(message),
    checkIntegrity: true,
  });
  lap("load_packages");

  py.FS.mkdirTree("/tmp/wheels");
  py.FS.writeFile(`/tmp/wheels/${wheelName}`, wheelBytes);
  const micropip = py.pyimport("micropip");
  await micropip.install.callKwargs(py.toPy([`emfs:/tmp/wheels/${wheelName}`]), { deps: false });
  micropip.destroy();
  const environment = JSON.parse(py.runPython(PYTHON_ENVIRONMENT));
  lap("install_wheel");
  console.log(`pyodide ${py.version}: Python ${environment.python}, numpy ${environment.numpy}, scipy ${environment.scipy}, ${environment.platform}`);
  console.log(`sarcostat ${environment.sarcostat} from ${environment.sarcostat_file}`);
  console.log(`hasattr(os, "link") in Pyodide: ${environment.os_link}`);

  let copied = 0;
  for (const rel of [...DEFAULT_SUPPORT, ...present.map((name) => `tests/${name}`)]) {
    const host = path.join(root, rel);
    if (fs.existsSync(host)) {
      copied += copyIntoPyodide(py, host, `${WORKDIR}/${rel}`);
    }
  }
  lap("copy_tests");
  console.log(`copied ${copied} files from ${root} into ${WORKDIR} (tests: ${present.map((name) => `tests/${name}`).join(", ")})`);

  const junitInside = values.junit ? "/tmp/pytest-junit.xml" : "";
  py.globals.set(
    "RUN_CONFIG",
    JSON.stringify({
      workdir: WORKDIR,
      paths: present.map((name) => `tests/${name}`),
      markexpr: MARK_EXPRESSION,
      junit: junitInside,
      deselect,
    }),
  );
  py.setStdout({ write: (buffer) => (process.stdout.write(Buffer.from(buffer)), buffer.length) });
  py.setStderr({ write: (buffer) => (process.stderr.write(Buffer.from(buffer)), buffer.length) });
  const result = JSON.parse(py.runPython(PYTHON_DRIVER));
  py.setStdout({ batched: (line) => console.log(line) });
  py.setStderr({ batched: (line) => console.error(line) });
  lap("pytest");
  if (values.junit) {
    const target = path.resolve(values.junit);
    await fsp.mkdir(path.dirname(target), { recursive: true });
    await fsp.writeFile(target, py.FS.readFile(junitInside));
    console.log(`junit: ${target}`);
  }

  return report(result, { minPass, wheelName, wheelSha, environment, present, missing, timing, pyodideVersion });
}

function report(result, context) {
  const counts = { passed: 0, failed: 0, error: 0, skipped: 0, xfailed: 0, xpassed: 0 };
  for (const outcome of Object.values(result.outcomes)) {
    counts[outcome] = (counts[outcome] ?? 0) + 1;
  }
  const collected = result.collected.length;
  const deselected = Object.keys(result.deselected).length;
  const ratio = collected > 0 ? counts.passed / collected : 0;
  const reasons = [];
  if (counts.failed > 0) {
    reasons.push(`${counts.failed} failed`);
  }
  if (counts.error > 0) {
    reasons.push(`${counts.error} errors`);
  }
  if (counts.xpassed > 0) {
    reasons.push(`${counts.xpassed} xpassed`);
  }
  if (Object.keys(result.collect_errors).length > 0) {
    reasons.push(`${Object.keys(result.collect_errors).length} collection errors`);
  }
  if (collected === 0) {
    reasons.push("no tests collected");
  }
  if (ratio < context.minPass) {
    reasons.push(`pass ratio ${(100 * ratio).toFixed(1)}% below ${(100 * context.minPass).toFixed(0)}%`);
  }
  if (![0, 1].includes(result.exit_code)) {
    reasons.push(`pytest exit code ${result.exit_code}`);
  }
  if (refusals.length > 0) {
    reasons.push(`${refusals.length} network attempt(s) refused`);
  }
  if (unhandled.length > 0) {
    reasons.push(`${unhandled.length} unhandled promise rejection(s)`);
  }

  const total = (performance.now() / 1000).toFixed(1);
  const lines = ["", "=== SarcoStat Pyodide pytest summary ==="];
  lines.push(`node ${process.version}; Pyodide ${context.pyodideVersion}; Python ${context.environment.python}; pytest ${result.pytest}; numpy ${context.environment.numpy}; scipy ${context.environment.scipy}`);
  lines.push(`wheel sha256: ${context.wheelSha}  ${context.wheelName}`);
  lines.push(`test folders: ${context.present.map((name) => `tests/${name}`).join(", ")}${context.missing.length ? `; not present (skipped): ${context.missing.map((name) => `tests/${name}`).join(", ")}` : ""}`);
  lines.push(`pytest args: ${result.args.join(" ")}`);
  lines.push(`collected ${collected}; passed ${counts.passed}; failed ${counts.failed}; errors ${counts.error}; skipped ${counts.skipped}; xfailed ${counts.xfailed}; xpassed ${counts.xpassed}; deselected ${deselected}; modules skipped at collection ${Object.keys(result.collect_skips).length}`);
  lines.push(`passed/collected: ${counts.passed}/${collected} = ${(100 * ratio).toFixed(1)}% (minimum ${(100 * context.minPass).toFixed(0)}%)`);
  lines.push(`hasattr(os, "link") in Pyodide: ${context.environment.os_link}`);
  lines.push(`network attempts refused: ${refusals.length}`);
  refusals.forEach((line) => lines.push(`  ${line}`));
  if (deselected > 0) {
    lines.push("deselected:");
    for (const [nodeid, reason] of Object.entries(result.deselected).sort()) {
      lines.push(`  ${nodeid} :: ${reason}`);
    }
  }
  const moduleSkips = Object.entries(result.collect_skips).sort();
  if (moduleSkips.length > 0) {
    lines.push("skipped at collection (whole module, its tests are not counted as collected):");
    for (const [nodeid, reason] of moduleSkips) {
      lines.push(`  ${nodeid} :: ${reason}`);
    }
  }
  const skipped = Object.entries(result.reasons).sort();
  if (skipped.length > 0) {
    lines.push("skipped:");
    for (const [nodeid, reason] of skipped) {
      lines.push(`  ${nodeid} :: ${reason}`);
    }
  }
  const bad = Object.entries(result.outcomes).filter(([, outcome]) => ["failed", "error", "xpassed"].includes(outcome)).sort();
  if (bad.length > 0) {
    lines.push("failed or erroring:");
    for (const [nodeid, outcome] of bad) {
      lines.push(`  ${nodeid} :: ${outcome}`);
    }
  }
  for (const [nodeid, text] of Object.entries(result.collect_errors)) {
    lines.push(`collection error in ${nodeid}:`);
    lines.push(text);
  }
  const phases = Object.entries(context.timing).map(([name, seconds]) => `${name} ${seconds.toFixed(1)} s`).join(", ");
  lines.push(`wall time: ${total} s (${phases})`);
  lines.push(reasons.length === 0 ? "RESULT: PASS" : `RESULT: FAIL (${reasons.join("; ")})`);
  console.log(lines.join("\n"));
  return reasons.length === 0 ? 0 : 1;
}

main().then(
  (code) => {
    process.exitCode = code;
  },
  (error) => {
    console.error(error?.stack ?? String(error));
    process.exitCode = 1;
  },
);
