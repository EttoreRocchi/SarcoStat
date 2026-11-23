import child_process from "node:child_process";
import fs from "node:fs";
import fsp from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

import { BUILD_FILE, canonicalJson, hashTree, sha256, walkFiles } from "./check_site.mjs";
import { WEB_DIR, insideDir, laneName } from "./lane.mjs";
import { SITE_LIST, cdnBase, pyodideVersion, sha256File, siteSubset } from "./pyodide_subset.mjs";

const USAGE = [
  "Usage: SOURCE_DATE_EPOCH=<seconds> node scripts/build_site.mjs --out <site root> --wheel <sarcostat wheel> [--commit <sha>] [--resume-digest <64 hex> ...]",
  "",
  "Writes one version folder <site root>/v<version>/ (version = the wheel's version): the Vite app, pyodide/ (the subset of web/pyodide-files.sha256, SHA-256 verified), wheels/<wheel> and BUILD.json.",
  "--resume-digest (repeatable) lists the source_digest of an earlier build whose open study sessions this build may resume (a hotfix lists the study build's digest); BUILD.json keeps them sorted and unique in resume_digests.",
  "web/resume-digests.txt, when the tree has it, adds one digest per line (\"<source_digest>  <label>\", for example the version it comes from), so that every build of a hotfix tree (CI, make site, the lane recipe) records the same resume_digests.",
  "SOURCE_DATE_EPOCH is required. An --out that already holds files is refused unless it is inside web/dist/, where only v<version>/ is replaced.",
  "source_digest is computed by CPython (PYTHON, default python3) with the unpacked wheel's own sarcostat.io.provenance.source_digest. Missing Pyodide wheels are downloaded from the pinned jsDelivr folder into web/.cache/pyodide-site/<pyodide version>/.",
].join("\n");

const WHEEL_NAME = /^sarcostat-(\d+(?:\.\d+)*(?:(?:a|b|rc)\d+)?(?:\.post\d+)?(?:\.dev\d+)?)-py3-none-any\.whl$/;
const COMMIT = /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/;
const DIGEST = /^[0-9a-f]{64}$/;
const RESUME_LINE = /^([0-9a-f]{64})(?:[ \t]+\S.*)?$/;

export const RESUME_FILE = path.join(WEB_DIR, "resume-digests.txt");

export function readResumeFile(file = RESUME_FILE) {
  if (!fs.existsSync(file)) {
    return [];
  }
  const digests = [];
  fs.readFileSync(file, "utf8")
    .split("\n")
    .forEach((line, index) => {
      if (line.trim() === "") {
        return;
      }
      const match = RESUME_LINE.exec(line.trimEnd());
      if (!match) {
        throw new BuildRefused(`${path.basename(file)}:${index + 1}: expected "<source_digest>  <label>", got ${JSON.stringify(line)}`);
      }
      digests.push(match[1]);
    });
  return digests;
}

export function resumeDigests(values, own) {
  const list = values ?? [];
  if (!Array.isArray(list)) {
    throw new BuildRefused("resume digests must be a list");
  }
  for (const value of list) {
    if (typeof value !== "string" || !DIGEST.test(value)) {
      throw new BuildRefused(`--resume-digest must be a source_digest (64 lowercase hex), got ${JSON.stringify(value)}`);
    }
    if (typeof own === "string" && value === own) {
      throw new BuildRefused(`--resume-digest ${value} is this build's own source_digest`);
    }
  }
  return [...new Set(list)].sort();
}

const DIGEST_SCRIPT = `
import json
import pathlib
import platform
import sys
import zipfile

wheel, dest = sys.argv[1], pathlib.Path(sys.argv[2])
with zipfile.ZipFile(wheel) as archive:
    archive.extractall(dest)
sys.path.insert(0, str(dest))
import sarcostat
from sarcostat.io.provenance import source_digest

package = (dest / "sarcostat").resolve()
if pathlib.Path(sarcostat.__file__).resolve().parent != package:
    raise SystemExit("sarcostat was imported from " + sarcostat.__file__ + ", not from the wheel")
infos = sorted(dest.glob("sarcostat-*.dist-info/METADATA"))
if len(infos) != 1:
    raise SystemExit("the wheel has no single sarcostat-*.dist-info/METADATA")
versions = [line[9:].strip() for line in infos[0].read_text("utf-8").splitlines() if line.startswith("Version: ")]
print(json.dumps({
    "source_digest": source_digest(package),
    "package_version": sarcostat.__version__,
    "metadata_version": versions[0] if len(versions) == 1 else None,
    "python": platform.python_version(),
}))
`;

export class BuildRefused extends Error {}

function toolVersion(name) {
  return JSON.parse(fs.readFileSync(path.join(WEB_DIR, "node_modules", name, "package.json"), "utf8")).version;
}

export function parseEpoch(value) {
  if (value === undefined || value === "") {
    throw new BuildRefused("SOURCE_DATE_EPOCH is not set; use the commit time, for example SOURCE_DATE_EPOCH=$(git log -1 --format=%ct HEAD)");
  }
  if (!/^\d{1,12}$/.test(value)) {
    throw new BuildRefused(`SOURCE_DATE_EPOCH must be a whole number of seconds, got ${JSON.stringify(value)}`);
  }
  return Number(value);
}

export function wheelVersion(file) {
  const match = WHEEL_NAME.exec(path.basename(file));
  if (!match) {
    throw new BuildRefused(`${path.basename(file)} is not a sarcostat-<version>-py3-none-any.whl wheel of a release version`);
  }
  return match[1];
}

export function checkOut(out) {
  const root = path.resolve(out);
  if (!fs.existsSync(root)) {
    return { root, existed: false };
  }
  if (!fs.statSync(root).isDirectory()) {
    throw new BuildRefused(`--out ${root} is not a folder`);
  }
  const held = fs.readdirSync(root);
  if (held.length > 0 && !insideDir(path.join(WEB_DIR, "dist"), root)) {
    throw new BuildRefused(`--out ${root} already holds files (${held.slice(0, 3).join(", ")}${held.length > 3 ? ", ..." : ""}); give an empty or new folder, or a folder inside web/dist/`);
  }
  return { root, existed: true };
}

export function sourceDigest(wheel, python = process.env.PYTHON || "python3") {
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "sarcostat-site-"));
  try {
    const run = child_process.spawnSync(python, ["-I", "-S", "-B", "-c", DIGEST_SCRIPT, path.resolve(wheel), path.join(scratch, "unpacked")], {
      cwd: scratch,
      encoding: "utf8",
      env: { PATH: process.env.PATH ?? "", PYTHONDONTWRITEBYTECODE: "1", PYTHONHASHSEED: "0", LC_ALL: "C.UTF-8" },
    });
    if (run.error) {
      throw new BuildRefused(`cannot run ${python}: ${run.error.message}`);
    }
    if (run.status !== 0) {
      throw new BuildRefused(`${python} could not compute source_digest of ${path.basename(wheel)}: ${(run.stderr || run.stdout).trim()}`);
    }
    return JSON.parse(run.stdout.trim().split("\n").pop());
  } finally {
    fs.rmSync(scratch, { recursive: true, force: true });
  }
}

export async function buildApp(outDir) {
  const { build } = await import("vite");
  await build({
    root: WEB_DIR,
    configFile: path.join(WEB_DIR, "vite.config.ts"),
    configLoader: "runner",
    logLevel: "warn",
    build: { outDir, emptyOutDir: true },
  });
  if (!fs.existsSync(path.join(outDir, "index.html"))) {
    throw new Error(`Vite wrote no index.html into ${outDir}`);
  }
}

async function copyVerified(source, destination, expected) {
  await fsp.mkdir(path.dirname(destination), { recursive: true });
  await fsp.copyFile(source, destination);
  const actual = await sha256File(destination);
  if (actual !== expected) {
    throw new Error(`${destination}: SHA-256 ${actual} after the copy, expected ${expected}`);
  }
}

function stampTimes(root, epoch) {
  const { files } = walkFiles(root);
  for (const rel of files) {
    fs.utimesSync(path.join(root, rel), epoch, epoch);
  }
  const dirs = new Set([root]);
  for (const rel of files) {
    let dir = path.dirname(path.join(root, rel));
    while (dir.startsWith(root) && !dirs.has(dir)) {
      dirs.add(dir);
      dir = path.dirname(dir);
    }
  }
  [...dirs].sort((a, b) => b.length - a.length).forEach((dir) => fs.utimesSync(dir, epoch, epoch));
}

export async function buildSite(options) {
  const { out, wheel, commit = null, resume = [], resumeFile = RESUME_FILE, sourceDateEpoch, python, pyodideList = SITE_LIST, download, log = console.log } = options;
  if (process.versions.node.split(".")[0] !== "24") {
    throw new BuildRefused(`Node 24 is required (web/.nvmrc), this is ${process.version}`);
  }
  const epoch = parseEpoch(sourceDateEpoch);
  const fromFile = readResumeFile(resumeFile);
  resumeDigests([...resume, ...fromFile]);
  if (!wheel) {
    throw new BuildRefused("--wheel is required");
  }
  const wheelPath = path.resolve(wheel);
  if (!fs.existsSync(wheelPath) || !fs.statSync(wheelPath).isFile()) {
    throw new BuildRefused(`--wheel ${wheelPath} does not exist`);
  }
  const version = wheelVersion(wheelPath);
  if (!(commit === null || (typeof commit === "string" && COMMIT.test(commit)))) {
    throw new BuildRefused(`--commit must be a full commit id (40 or 64 lowercase hex), got ${JSON.stringify(commit)}`);
  }
  if (!out) {
    throw new BuildRefused("--out is required");
  }
  const target = checkOut(out);
  const versionDir = path.join(target.root, `v${version}`);
  const pinned = pyodideVersion();

  const digest = sourceDigest(wheelPath, python);
  const resumeList = resumeDigests([...resume, ...fromFile], digest.source_digest);
  if (digest.metadata_version !== version || digest.package_version !== version) {
    throw new BuildRefused(`${path.basename(wheelPath)}: file name ${version}, METADATA ${digest.metadata_version}, sarcostat.__version__ ${digest.package_version}`);
  }
  const subset = await siteSubset({ list: pyodideList, download: download ?? cdnBase(pinned), log });

  log(`build_site: lane ${laneName()}, node ${process.version}, ${path.basename(wheelPath)} -> ${versionDir}`);
  if (fromFile.length > 0) {
    log(`build_site: ${fromFile.length} resume digest(s) from ${resumeFile}`);
  }
  await fsp.mkdir(target.root, { recursive: true });
  await fsp.rm(versionDir, { recursive: true, force: true });
  try {
    await buildApp(versionDir);
    for (const file of subset.files) {
      await copyVerified(file.source, path.join(versionDir, "pyodide", file.name), file.sha256);
    }
    const wheelSha = await sha256File(wheelPath);
    const wheelRel = `wheels/${path.basename(wheelPath)}`;
    await copyVerified(wheelPath, path.join(versionDir, wheelRel), wheelSha);
    const { version: viteVersion } = await import("vite");
    const record = {
      format: "sarcostat-build",
      format_version: 1,
      version,
      commit,
      source_date_epoch: epoch,
      wheel: { file: wheelRel, sha256: wheelSha },
      source_digest: digest.source_digest,
      pyodide: { version: pinned, dir: "pyodide/" },
      resume_digests: resumeList,
      tools: { node: process.versions.node, vite: viteVersion, typescript: toolVersion("typescript"), python: digest.python },
      files: hashTree(versionDir),
    };
    fs.writeFileSync(path.join(versionDir, BUILD_FILE), canonicalJson(record));
    stampTimes(versionDir, epoch);
    const { files } = walkFiles(versionDir);
    const bytes = files.reduce((sum, rel) => sum + fs.statSync(path.join(versionDir, rel)).size, 0);
    log(`build_site: wrote ${files.length} files, ${bytes} bytes (${(bytes / 1e6).toFixed(2)} MB); Pyodide subset ${subset.files.length} files, ${subset.bytes} bytes; wheel sha256 ${wheelSha}; source_digest ${digest.source_digest}`);
    return { versionDir, record, bytes, files: files.length, buildSha256: sha256(fs.readFileSync(path.join(versionDir, BUILD_FILE))) };
  } catch (error) {
    await fsp.rm(versionDir, { recursive: true, force: true });
    if (!target.existed && fs.existsSync(target.root) && fs.readdirSync(target.root).length === 0) {
      await fsp.rmdir(target.root);
    }
    throw error;
  }
}

async function main() {
  const { values } = parseArgs({
    options: {
      out: { type: "string" },
      wheel: { type: "string" },
      commit: { type: "string" },
      "resume-digest": { type: "string", multiple: true },
      help: { type: "boolean", default: false },
    },
    strict: true,
  });
  if (values.help) {
    console.log(USAGE);
    return 0;
  }
  try {
    const result = await buildSite({
      out: values.out,
      wheel: values.wheel,
      commit: values.commit ?? null,
      resume: values["resume-digest"] ?? [],
      sourceDateEpoch: process.env.SOURCE_DATE_EPOCH,
      python: process.env.PYTHON || "python3",
    });
    console.log(`build_site: BUILD.json sha256 ${result.buildSha256}`);
    console.log(`build_site: done ${result.versionDir}`);
    return 0;
  } catch (error) {
    if (error instanceof BuildRefused) {
      console.error(`build_site: refused: ${error.message}\n${USAGE}`);
      return 2;
    }
    console.error(`build_site: FAILED: ${error.stack ?? error}`);
    return 1;
  }
}

if (process.argv[1] && fs.realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = await main();
}
