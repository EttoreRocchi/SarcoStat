import crypto from "node:crypto";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

const WEB_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const LINE = /^([0-9a-f]{64}) {2}([A-Za-z0-9][A-Za-z0-9._+-]*)$/;

export const RUNTIME_DIR = path.join(WEB_DIR, "node_modules", "pyodide");
export const SITE_LIST = path.join(WEB_DIR, "pyodide-files.sha256");
export const TEST_LIST = path.join(WEB_DIR, "pyodide-test-files.sha256");
export const SITE_CORE_FILES = ["pyodide.mjs", "pyodide.asm.mjs", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json"];
export const SITE_PACKAGES = ["numpy", "scipy"];

const USAGE = [
  "Usage:",
  "  node scripts/pyodide_subset.mjs [--list <sha256 file>] [--out <dir>] (--from <local dist dir> | --download <https base URL>) [--files <name,...>]",
  "  node scripts/pyodide_subset.mjs --site [--from <local dist dir> | --download <https base URL>]",
  "  node scripts/pyodide_subset.mjs --make-list --lock <pyodide-lock.json> --packages <name,...> --write <sha256 file> [--from <local dist dir>] [--core <name,...> [--core-from <dir>]]",
  "",
  "Copies (--from) or downloads (--download) exactly the files named in the list into --out and checks the SHA-256 of each; any missing file or mismatch fails the run and nothing unverified is left in --out.",
  "Defaults: --list web/pyodide-test-files.sha256, --out web/.cache/pyodide-test/<pyodide version from web/package.json>/.",
  "--files restricts the run to some names of the list. --make-list writes a list in sha256sum format from the dependency closure of --packages in the lock file, checking each file in --from when given.",
  "--site works on the site subset web/pyodide-files.sha256: the Pyodide core files are checked in web/node_modules/pyodide, the package wheels are checked against the list and the lock file there and kept in web/.cache/pyodide-site/<pyodide version>/; a wheel missing from that folder is copied (--from) or downloaded (--download), and without either the run fails.",
  "--core adds the named files of --core-from (default web/node_modules/pyodide) to the list written by --make-list.",
].join("\n");

function fail(message) {
  console.error(`pyodide_subset: ${message}`);
  process.exit(1);
}

export function pyodideVersion() {
  const pkg = JSON.parse(fs.readFileSync(path.join(WEB_DIR, "package.json"), "utf8"));
  const version = pkg.devDependencies?.pyodide;
  if (!/^\d+\.\d+\.\d+$/.test(version ?? "")) {
    throw new Error(`web/package.json must pin pyodide to an exact version, found ${version}`);
  }
  return version;
}

export function cdnBase(version = pyodideVersion()) {
  return `https://cdn.jsdelivr.net/pyodide/v${version}/full/`;
}

export function siteCacheDir(version = pyodideVersion()) {
  return path.join(WEB_DIR, ".cache", "pyodide-site", version);
}

export function readList(file) {
  const text = fs.readFileSync(file, "utf8");
  const entries = [];
  const seen = new Set();
  text.split("\n").forEach((line, index) => {
    if (line === "") {
      return;
    }
    const match = LINE.exec(line);
    if (!match) {
      throw new Error(`${file}:${index + 1}: not a "<sha256>  <file name>" line: ${JSON.stringify(line)}`);
    }
    const [, sha256, name] = match;
    if (seen.has(name)) {
      throw new Error(`${file}:${index + 1}: ${name} is listed twice`);
    }
    seen.add(name);
    entries.push({ sha256, name });
  });
  if (entries.length === 0) {
    throw new Error(`${file} lists no files`);
  }
  return entries;
}

export async function sha256File(file) {
  const hash = crypto.createHash("sha256");
  for await (const chunk of fs.createReadStream(file)) {
    hash.update(chunk);
  }
  return hash.digest("hex");
}

function normalise(name) {
  return name.replace(/[-_.]+/g, "-").toLowerCase();
}

function packagesByName(lock) {
  return new Map(Object.entries(lock.packages).map(([key, value]) => [normalise(key), value]));
}

export function closure(lock, roots) {
  const byName = packagesByName(lock);
  const order = [];
  const seen = new Set();
  const queue = [...roots];
  while (queue.length > 0) {
    const wanted = normalise(queue.shift());
    if (seen.has(wanted)) {
      continue;
    }
    const entry = byName.get(wanted);
    if (!entry) {
      throw new Error(`package ${wanted} is not in the lock file`);
    }
    seen.add(wanted);
    order.push(entry);
    queue.push(...entry.depends);
  }
  return order;
}

async function makeList(values) {
  for (const key of ["lock", "packages", "write"]) {
    if (!values[key]) {
      fail(`--make-list needs --${key}\n${USAGE}`);
    }
  }
  const lock = JSON.parse(await fsp.readFile(values.lock, "utf8"));
  const packages = closure(lock, values.packages.split(",").filter(Boolean));
  const errors = [];
  let bytes = 0;
  for (const entry of packages) {
    if (!LINE.test(`${entry.sha256}  ${entry.file_name}`)) {
      errors.push(`unexpected lock entry for ${entry.name}: ${entry.file_name} ${entry.sha256}`);
      continue;
    }
    if (values.from) {
      const file = path.join(values.from, entry.file_name);
      if (!fs.existsSync(file)) {
        errors.push(`missing in ${values.from}: ${entry.file_name}`);
        continue;
      }
      const actual = await sha256File(file);
      if (actual !== entry.sha256) {
        errors.push(`SHA-256 mismatch for ${entry.file_name}: lock ${entry.sha256}, file ${actual}`);
        continue;
      }
      bytes += fs.statSync(file).size;
    }
  }
  const lines = packages.map((entry) => `${entry.sha256}  ${entry.file_name}`);
  const coreFrom = path.resolve(values["core-from"] ?? RUNTIME_DIR);
  const lockFiles = new Set(packages.map((entry) => entry.file_name));
  for (const name of (values.core ?? "").split(",").filter(Boolean)) {
    const file = path.join(coreFrom, name);
    if (lockFiles.has(name)) {
      errors.push(`--core ${name} is a package of the lock file`);
    } else if (!fs.existsSync(file)) {
      errors.push(`missing in ${coreFrom}: ${name}`);
    } else {
      lines.push(`${await sha256File(file)}  ${name}`);
      bytes += fs.statSync(file).size;
    }
  }
  if (errors.length > 0) {
    errors.forEach((line) => console.error(`pyodide_subset: ${line}`));
    fail(`${errors.length} error(s); list not written`);
  }
  lines.sort((a, b) => a.slice(66).localeCompare(b.slice(66)));
  await fsp.writeFile(values.write, `${lines.join("\n")}\n`);
  const checked = values.from || values.core ? `, ${bytes} bytes checked` : "";
  console.log(`pyodide_subset: wrote ${values.write}: ${lines.length} files (lock ${values.lock}, Python ${lock.info?.python ?? "?"}, ABI ${lock.info?.abi_version ?? "?"}${checked})`);
}

async function obtain(name, source, destination) {
  if (source.from) {
    const file = path.join(source.from, name);
    if (!fs.existsSync(file)) {
      throw new Error(`missing in ${source.from}: ${name}`);
    }
    await fsp.copyFile(file, destination);
    return;
  }
  const url = new URL(name, source.download);
  const response = await fetch(url, { redirect: "follow" });
  if (!response.ok) {
    throw new Error(`download failed for ${url.href}: HTTP ${response.status}`);
  }
  await fsp.writeFile(destination, Buffer.from(await response.arrayBuffer()));
}

function checkedSource(values) {
  if (values.from && values.download) {
    throw new Error("give at most one of --from and --download");
  }
  if (values.download) {
    if (!values.download.startsWith("https://")) {
      throw new Error(`--download needs an https URL, got ${values.download}`);
    }
    return { download: values.download.endsWith("/") ? values.download : `${values.download}/` };
  }
  return values.from ? { from: path.resolve(values.from) } : {};
}

async function cached(entry, out, source) {
  const destination = path.join(out, entry.name);
  if (fs.existsSync(destination) && (await sha256File(destination)) === entry.sha256) {
    return { file: destination, reused: true };
  }
  if (!source.from && !source.download) {
    throw new Error(`${entry.name} is not in ${out}; pass --from <dir> or --download <URL>`);
  }
  const partial = `${destination}.part-${process.pid}`;
  try {
    await obtain(entry.name, source, partial);
    const actual = await sha256File(partial);
    if (actual !== entry.sha256) {
      throw new Error(`SHA-256 mismatch for ${entry.name}: expected ${entry.sha256}, got ${actual}`);
    }
    await fsp.rename(partial, destination);
    return { file: destination, reused: false };
  } catch (error) {
    await fsp.rm(partial, { force: true });
    await fsp.rm(destination, { force: true });
    throw error;
  }
}

async function fetchSubset(values) {
  if (Boolean(values.from) === Boolean(values.download)) {
    fail(`give exactly one of --from and --download\n${USAGE}`);
  }
  let source;
  try {
    source = checkedSource(values);
  } catch (error) {
    fail(error.message);
  }
  const listFile = path.resolve(values.list ?? TEST_LIST);
  const out = path.resolve(values.out ?? path.join(WEB_DIR, ".cache", "pyodide-test", pyodideVersion()));
  let entries = readList(listFile);
  if (values.files) {
    const wanted = new Set(values.files.split(",").filter(Boolean));
    const unknown = [...wanted].filter((name) => !entries.some((entry) => entry.name === name));
    if (unknown.length > 0) {
      fail(`--files names not in ${listFile}: ${unknown.join(", ")}`);
    }
    entries = entries.filter((entry) => wanted.has(entry.name));
  }
  await fsp.mkdir(out, { recursive: true });
  const how = source.from ? `copy from ${source.from}` : `download from ${source.download}`;
  console.log(`pyodide_subset: ${entries.length} file(s) of ${listFile} into ${out} (${how})`);
  const errors = [];
  let bytes = 0;
  let reused = 0;
  for (const entry of entries) {
    try {
      const got = await cached(entry, out, source);
      bytes += fs.statSync(got.file).size;
      if (got.reused) {
        reused += 1;
      } else {
        console.log(`pyodide_subset: ok ${entry.name}`);
      }
    } catch (error) {
      errors.push(error.message);
    }
  }
  if (errors.length > 0) {
    errors.forEach((line) => console.error(`pyodide_subset: ERROR ${line}`));
    fail(`FAILED: ${errors.length} of ${entries.length} file(s) missing or not matching ${listFile}`);
  }
  const listed = new Set(readList(listFile).map((entry) => entry.name));
  const extra = (await fsp.readdir(out)).filter((name) => !listed.has(name));
  if (extra.length > 0) {
    console.log(`pyodide_subset: note: ${out} also holds ${extra.length} file(s) not in the list: ${extra.join(", ")}`);
  }
  console.log(`pyodide_subset: verified ${entries.length} file(s), ${bytes} bytes (${reused} already present)`);
}

export async function siteSubset({ list = SITE_LIST, cacheDir, runtimeDir = RUNTIME_DIR, from, download, log = () => {} } = {}) {
  const version = pyodideVersion();
  const runtimeVersion = JSON.parse(fs.readFileSync(path.join(runtimeDir, "package.json"), "utf8")).version;
  if (runtimeVersion !== version) {
    throw new Error(`${runtimeDir} is Pyodide ${runtimeVersion}, web/package.json pins ${version}`);
  }
  const lock = JSON.parse(fs.readFileSync(path.join(runtimeDir, "pyodide-lock.json"), "utf8"));
  const byFile = new Map(Object.values(lock.packages).map((entry) => [entry.file_name, entry]));
  const byName = packagesByName(lock);
  const entries = readList(list);
  const listed = new Set(entries.map((entry) => entry.name));
  const source = checkedSource({ from, download });
  const out = path.resolve(cacheDir ?? siteCacheDir(version));
  const errors = [];
  const files = [];
  for (const entry of entries) {
    const locked = byFile.get(entry.name);
    if (!locked) {
      const file = path.join(runtimeDir, entry.name);
      if (!fs.existsSync(file)) {
        errors.push(`core file ${entry.name} is missing in ${runtimeDir}`);
      } else if ((await sha256File(file)) !== entry.sha256) {
        errors.push(`core file ${entry.name} in ${runtimeDir} does not match ${path.basename(list)}`);
      } else {
        files.push({ ...entry, kind: "core", source: file, bytes: fs.statSync(file).size });
      }
      continue;
    }
    if (locked.sha256 !== entry.sha256) {
      errors.push(`${entry.name}: ${path.basename(list)} has ${entry.sha256}, the lock file ${locked.sha256}`);
      continue;
    }
    for (const dependency of locked.depends) {
      const needed = byName.get(normalise(dependency));
      if (!needed || !listed.has(needed.file_name)) {
        errors.push(`${entry.name} depends on ${dependency}, which ${path.basename(list)} does not list`);
      }
    }
    try {
      await fsp.mkdir(out, { recursive: true });
      const got = await cached(entry, out, source);
      if (!got.reused) {
        log(`pyodide_subset: ok ${entry.name} (${source.from ? `copied from ${source.from}` : `downloaded from ${source.download}`})`);
      }
      files.push({ ...entry, kind: "package", package: locked.name, source: got.file, bytes: fs.statSync(got.file).size });
    } catch (error) {
      errors.push(error.message);
    }
  }
  if (!listed.has("pyodide-lock.json")) {
    errors.push(`${path.basename(list)} does not list pyodide-lock.json`);
  }
  if (errors.length > 0) {
    throw new Error(`site subset not verified:\n${errors.join("\n")}`);
  }
  const bytes = files.reduce((sum, file) => sum + file.bytes, 0);
  return { version, list: path.resolve(list), cacheDir: out, runtimeDir, files, bytes };
}

async function fetchSite(values) {
  for (const key of ["list", "out", "files"]) {
    if (values[key]) {
      fail(`--site takes no --${key}\n${USAGE}`);
    }
  }
  let subset;
  try {
    subset = await siteSubset({ from: values.from, download: values.download, log: console.log });
  } catch (error) {
    fail(error.message);
  }
  const core = subset.files.filter((file) => file.kind === "core");
  const packages = subset.files.filter((file) => file.kind === "package");
  console.log(`pyodide_subset: site subset ${path.relative(WEB_DIR, subset.list)}: ${subset.files.length} file(s), ${subset.bytes} bytes (${(subset.bytes / 1e6).toFixed(2)} MB)`);
  console.log(`pyodide_subset:   ${core.length} core file(s) from ${path.relative(WEB_DIR, subset.runtimeDir)}: ${core.map((file) => file.name).join(", ")}`);
  console.log(`pyodide_subset:   ${packages.length} package wheel(s) in ${path.relative(WEB_DIR, subset.cacheDir)}, SHA-256 equal to the list and the lock file: ${packages.map((file) => file.package).join(", ")}`);
}

async function main() {
  const { values } = parseArgs({
    options: {
      list: { type: "string" },
      out: { type: "string" },
      from: { type: "string" },
      download: { type: "string" },
      files: { type: "string" },
      site: { type: "boolean", default: false },
      "make-list": { type: "boolean", default: false },
      lock: { type: "string" },
      packages: { type: "string" },
      write: { type: "string" },
      core: { type: "string" },
      "core-from": { type: "string" },
      help: { type: "boolean", default: false },
    },
    strict: true,
  });
  if (values.help) {
    console.log(USAGE);
    return;
  }
  if (values["make-list"]) {
    await makeList(values);
  } else if (values.site) {
    await fetchSite(values);
  } else {
    await fetchSubset(values);
  }
}

if (process.argv[1] && fs.realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => fail(error.stack ?? String(error)));
}
