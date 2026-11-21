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

export const HERE = path.dirname(fileURLToPath(import.meta.url));
export const WEB_DIR = path.resolve(HERE, "..", "..");

const refusals = [];

export function offlineRefusals() {
  return [...refusals];
}

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

export function installOfflineGuard() {
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

export function sha256(bytes) {
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

export function verifySubset(listFile, subsetDir) {
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

export function pinnedPyodideVersion() {
  const pkg = JSON.parse(fs.readFileSync(path.join(WEB_DIR, "package.json"), "utf8"));
  return pkg.devDependencies?.pyodide;
}

export async function loadRuntime({ subset, list, packages, log = console.log }) {
  const pyodideVersion = pinnedPyodideVersion();
  const runtimeDir = path.dirname(fileURLToPath(import.meta.resolve("pyodide/package.json")));
  const runtimeVersion = JSON.parse(fs.readFileSync(path.join(runtimeDir, "package.json"), "utf8")).version;
  if (runtimeVersion !== pyodideVersion) {
    throw new Error(`node_modules/pyodide is ${runtimeVersion}, web/package.json pins ${pyodideVersion}`);
  }
  const subsetDir = path.resolve(subset ?? path.join(WEB_DIR, ".cache", "pyodide-test", pyodideVersion));
  const listFile = path.resolve(list ?? path.join(WEB_DIR, "pyodide-test-files.sha256"));
  const verified = verifySubset(listFile, subsetDir);
  if (verified.problems.length > 0) {
    throw new Error(`the test subset ${subsetDir} is not complete:\n${verified.problems.join("\n")}`);
  }
  const lock = JSON.parse(fs.readFileSync(path.join(runtimeDir, "pyodide-lock.json"), "utf8"));
  const byFile = new Map(Object.values(lock.packages).map((entry) => [entry.file_name, entry]));
  const listed = new Set();
  for (const entry of verified.entries) {
    const locked = byFile.get(entry.name);
    if (!locked || locked.sha256 !== entry.sha256) {
      throw new Error(`${entry.name} in ${path.basename(listFile)} does not match the Pyodide lock file`);
    }
    listed.add(locked.name);
  }
  const missing = packages.filter((name) => !listed.has(name));
  if (missing.length > 0) {
    throw new Error(`packages not in the verified subset: ${missing.join(", ")}`);
  }
  log(`subset: ${verified.entries.length} files, ${verified.bytes} bytes, SHA-256 verified against ${path.basename(listFile)} and the lock file`);
  const { loadPyodide } = await import("pyodide");
  const py = await loadPyodide({
    indexURL: `${runtimeDir}${path.sep}`,
    lockFileURL: path.join(runtimeDir, "pyodide-lock.json"),
    packageBaseUrl: `${subsetDir}${path.sep}`,
    packageCacheDir: `${subsetDir}${path.sep}`,
    env: { HOME: "/home/pyodide" },
    stdout: (line) => log(line),
    stderr: (line) => console.error(line),
  });
  if (py.version !== pyodideVersion) {
    throw new Error(`Pyodide reports ${py.version}, expected ${pyodideVersion}`);
  }
  await py.loadPackage(packages, {
    messageCallback: () => {},
    errorCallback: (message) => console.error(message),
    checkIntegrity: true,
  });
  return { py, pyodideVersion };
}

export async function installWheel(py, wheelPath) {
  const wheelName = path.basename(wheelPath);
  const wheelBytes = fs.readFileSync(wheelPath);
  py.FS.mkdirTree("/tmp/wheels");
  py.FS.writeFile(`/tmp/wheels/${wheelName}`, wheelBytes);
  const micropip = py.pyimport("micropip");
  await micropip.install.callKwargs(py.toPy([`emfs:/tmp/wheels/${wheelName}`]), { deps: false });
  micropip.destroy();
  return { name: wheelName, sha256: sha256(wheelBytes) };
}

export const PYTHON_ENVIRONMENT = `
import json
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
    }
)
`;
