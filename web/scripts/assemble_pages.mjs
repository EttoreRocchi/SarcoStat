import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import zlib from "node:zlib";

const USAGE = [
  "Usage:",
  "  node assemble_pages.mjs zip --site <site root>/v<version> --out <sarcostat-web-<version>.zip>",
  "  node assemble_pages.mjs verify-zip --zip <file> --build <BUILD.json>",
  "  node assemble_pages.mjs release-info --tag <tag> --init <src/sarcostat/__init__.py>",
  "  node assemble_pages.mjs plan --releases <api page.json> [--releases ...] --out <downloads.tsv>",
  "  node assemble_pages.mjs live-list --versions <live versions.json>",
  "  node assemble_pages.mjs assemble --releases <api page.json> [...] --assets <dir> --live <dir> --event <name> --run-attempt <n> --ref <git ref> --out <dir> [--known <web/releases.json>] [--summary <file>]",
  "",
  "zip writes a deterministic stored zip of one version folder (entries v<version>/<path>, sorted, dated at BUILD.json source_date_epoch) and checks it against BUILD.json; verify-zip repeats that check on any zip.",
  "release-info checks that the tag is v + __version__ and prints version= and prerelease= lines (pre-release status from the PEP 440 version string); a .dev version is refused.",
  "plan reads Releases API pages and lists, one per line, <version> TAB <asset name> TAB <asset API URL> for every published release carrying sarcostat-web-<version>.zip and BUILD.json.",
  "live-list prints the versions of a live /versions.json written by this script, one per line.",
  "assemble verifies every carried zip against its release's BUILD.json asset (and web/releases.json where listed), checks the live site (<live>/versions.json with versions.status, <live>/v<version>/BUILD.json with BUILD.json.status), picks latest by PEP 440 and writes <out>/v<version>/, <out>/latest/index.html, <out>/index.html and <out>/versions.json.",
  "Rules: a live version missing from the API set fails; a 404 on versions.json passes only when the API set has one version; a live BUILD.json that differs from its release asset fails a first-attempt release run and is repaired, with a warning, on a workflow_dispatch run or a re-run (run attempt above 1).",
  "This file imports only node: built-ins, so the hotfix runbook can copy it alone.",
].join("\n");

export const VERSION_RE = /^(\d+(?:\.\d+)*)(?:(a|b|rc)(\d+))?(?:\.post(\d+))?(?:\.dev(\d+))?$/;
export const BUILD_FILE = "BUILD.json";
export const VERSIONS_FILE = "versions.json";
export const VERSIONS_FORMAT = "sarcostat-versions";
export const RELEASES_FORMAT = "sarcostat-releases";
export const ZIP_MODE = 0o100644;

const PRE_RANK = { a: 0, b: 1, rc: 2 };
const HEX64 = /^[0-9a-f]{64}$/;
const MAX_ZIP32 = 0xffffffff;

export class AssembleError extends Error {}

export function sha256(bytes) {
  return crypto.createHash("sha256").update(bytes).digest("hex");
}

function sortedValue(value) {
  if (Array.isArray(value)) {
    return value.map(sortedValue);
  }
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, sortedValue(value[key])]),
    );
  }
  return value;
}

export function canonicalJson(value) {
  const text = JSON.stringify(sortedValue(value), null, 2).replace(/[\u0080-￿]/g, (ch) => `\\u${ch.charCodeAt(0).toString(16).padStart(4, "0")}`);
  return `${text}\n`;
}

export function parseVersion(text) {
  const match = typeof text === "string" ? VERSION_RE.exec(text) : null;
  if (!match) {
    throw new AssembleError(`${JSON.stringify(text)} is not a release version (N.N[.N][aN|bN|rcN][.postN][.devN])`);
  }
  const [, release, kind, preNumber, post, dev] = match;
  return {
    text,
    release: release.split(".").map(Number),
    pre: kind === undefined ? null : [kind, Number(preNumber)],
    post: post === undefined ? null : Number(post),
    dev: dev === undefined ? null : Number(dev),
  };
}

export function isPrerelease(version) {
  const parsed = typeof version === "string" ? parseVersion(version) : version;
  return parsed.pre !== null || parsed.dev !== null;
}

function sortKey(parsed) {
  let pre;
  if (parsed.pre === null && parsed.post === null && parsed.dev !== null) {
    pre = [-Infinity, 0];
  } else if (parsed.pre === null) {
    pre = [Infinity, 0];
  } else {
    pre = [PRE_RANK[parsed.pre[0]], parsed.pre[1]];
  }
  return { release: parsed.release, pre, post: parsed.post === null ? -Infinity : parsed.post, dev: parsed.dev === null ? Infinity : parsed.dev };
}

function compareNumbers(a, b) {
  return a < b ? -1 : a > b ? 1 : 0;
}

export function compareVersions(a, b) {
  const ka = sortKey(typeof a === "string" ? parseVersion(a) : a);
  const kb = sortKey(typeof b === "string" ? parseVersion(b) : b);
  const length = Math.max(ka.release.length, kb.release.length);
  for (let i = 0; i < length; i += 1) {
    const order = compareNumbers(ka.release[i] ?? 0, kb.release[i] ?? 0);
    if (order !== 0) {
      return order;
    }
  }
  return compareNumbers(ka.pre[0], kb.pre[0]) || compareNumbers(ka.pre[1], kb.pre[1]) || compareNumbers(ka.post, kb.post) || compareNumbers(ka.dev, kb.dev);
}

export function pickLatest(versions) {
  if (versions.length === 0) {
    throw new AssembleError("no version to pick latest from");
  }
  const sorted = [...versions].sort(compareVersions);
  const finals = sorted.filter((version) => !isPrerelease(version));
  return (finals.length > 0 ? finals : sorted).at(-1);
}

function dosDateTime(epochSeconds) {
  const date = new Date(Math.max(epochSeconds, 315532800) * 1000);
  const year = Math.min(date.getUTCFullYear(), 2107);
  return {
    time: (date.getUTCHours() << 11) | (date.getUTCMinutes() << 5) | (date.getUTCSeconds() >> 1),
    date: ((year - 1980) << 9) | ((date.getUTCMonth() + 1) << 5) | date.getUTCDate(),
  };
}

function checkEntryName(name) {
  if (typeof name !== "string" || name === "" || name.startsWith("/") || name.includes("\\") || name.includes("\0")) {
    return false;
  }
  return name.split("/").every((part, index, parts) => (part !== "" || index === parts.length - 1) && part !== "." && part !== "..");
}

export function writeZip(entries, { mtime = 315532800 } = {}) {
  const { time, date } = dosDateTime(mtime);
  const sorted = [...entries].sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
  const locals = [];
  const centrals = [];
  const seen = new Set();
  let offset = 0;
  for (const entry of sorted) {
    if (!checkEntryName(entry.name) || entry.name.endsWith("/")) {
      throw new AssembleError(`bad zip entry name ${JSON.stringify(entry.name)}`);
    }
    if (seen.has(entry.name)) {
      throw new AssembleError(`zip entry ${entry.name} given twice`);
    }
    seen.add(entry.name);
    const name = Buffer.from(entry.name, "utf8");
    const data = Buffer.isBuffer(entry.data) ? entry.data : Buffer.from(entry.data);
    const crc = zlib.crc32(data) >>> 0;
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(0x0800, 6);
    local.writeUInt16LE(0, 8);
    local.writeUInt16LE(time, 10);
    local.writeUInt16LE(date, 12);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(data.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(name.length, 26);
    local.writeUInt16LE(0, 28);
    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE((3 << 8) | 20, 4);
    central.writeUInt16LE(20, 6);
    central.writeUInt16LE(0x0800, 8);
    central.writeUInt16LE(0, 10);
    central.writeUInt16LE(time, 12);
    central.writeUInt16LE(date, 14);
    central.writeUInt32LE(crc, 16);
    central.writeUInt32LE(data.length, 20);
    central.writeUInt32LE(data.length, 24);
    central.writeUInt16LE(name.length, 28);
    central.writeUInt16LE(0, 30);
    central.writeUInt16LE(0, 32);
    central.writeUInt16LE(0, 34);
    central.writeUInt16LE(0, 36);
    central.writeUInt32LE((ZIP_MODE << 16) >>> 0, 38);
    central.writeUInt32LE(offset, 42);
    locals.push(local, name, data);
    centrals.push(central, name);
    offset += local.length + name.length + data.length;
    if (offset > MAX_ZIP32) {
      throw new AssembleError("the zip would pass 4 GB, which this writer does not support");
    }
  }
  const centralSize = centrals.reduce((sum, part) => sum + part.length, 0);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(0, 4);
  end.writeUInt16LE(0, 6);
  end.writeUInt16LE(sorted.length, 8);
  end.writeUInt16LE(sorted.length, 10);
  end.writeUInt32LE(centralSize, 12);
  end.writeUInt32LE(offset, 16);
  end.writeUInt16LE(0, 20);
  if (sorted.length > 0xffff) {
    throw new AssembleError("more than 65535 zip entries are not supported");
  }
  return Buffer.concat([...locals, ...centrals, end]);
}

export function readZip(buffer) {
  const bytes = Buffer.isBuffer(buffer) ? buffer : Buffer.from(buffer);
  let end = -1;
  for (let i = bytes.length - 22; i >= Math.max(0, bytes.length - 22 - 0xffff); i -= 1) {
    if (bytes.readUInt32LE(i) === 0x06054b50) {
      end = i;
      break;
    }
  }
  if (end < 0) {
    throw new AssembleError("not a zip file (no end of central directory record)");
  }
  const count = bytes.readUInt16LE(end + 10);
  const centralSize = bytes.readUInt32LE(end + 12);
  const centralOffset = bytes.readUInt32LE(end + 16);
  if (count === 0xffff || centralOffset === MAX_ZIP32 || centralOffset + centralSize > end) {
    throw new AssembleError("zip64 or damaged central directory");
  }
  const files = new Map();
  let at = centralOffset;
  for (let index = 0; index < count; index += 1) {
    if (at + 46 > bytes.length || bytes.readUInt32LE(at) !== 0x02014b50) {
      throw new AssembleError(`damaged central directory at entry ${index}`);
    }
    const flags = bytes.readUInt16LE(at + 8);
    const method = bytes.readUInt16LE(at + 10);
    const crc = bytes.readUInt32LE(at + 16);
    const compressed = bytes.readUInt32LE(at + 20);
    const size = bytes.readUInt32LE(at + 24);
    const nameLength = bytes.readUInt16LE(at + 28);
    const extraLength = bytes.readUInt16LE(at + 30);
    const commentLength = bytes.readUInt16LE(at + 32);
    const localOffset = bytes.readUInt32LE(at + 42);
    const name = bytes.subarray(at + 46, at + 46 + nameLength).toString("utf8");
    at += 46 + nameLength + extraLength + commentLength;
    if (!checkEntryName(name)) {
      throw new AssembleError(`unsafe zip entry name ${JSON.stringify(name)}`);
    }
    if (flags & 1) {
      throw new AssembleError(`${name} is encrypted`);
    }
    if (localOffset + 30 > bytes.length || bytes.readUInt32LE(localOffset) !== 0x04034b50) {
      throw new AssembleError(`${name}: damaged local header`);
    }
    const start = localOffset + 30 + bytes.readUInt16LE(localOffset + 26) + bytes.readUInt16LE(localOffset + 28);
    if (start + compressed > bytes.length) {
      throw new AssembleError(`${name}: data runs past the end of the file`);
    }
    if (name.endsWith("/")) {
      continue;
    }
    const raw = bytes.subarray(start, start + compressed);
    let data;
    if (method === 0) {
      data = Buffer.from(raw);
    } else if (method === 8) {
      data = zlib.inflateRawSync(raw);
    } else {
      throw new AssembleError(`${name}: compression method ${method} is not supported`);
    }
    if (data.length !== size || (zlib.crc32(data) >>> 0) !== crc) {
      throw new AssembleError(`${name}: size or CRC-32 does not match the zip directory`);
    }
    if (files.has(name)) {
      throw new AssembleError(`${name} appears twice in the zip`);
    }
    files.set(name, data);
  }
  return files;
}

function walk(root) {
  const found = [];
  function visit(dir, rel) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const relPath = rel ? `${rel}/${entry.name}` : entry.name;
      if (entry.isDirectory()) {
        visit(path.join(dir, entry.name), relPath);
      } else if (entry.isFile()) {
        found.push(relPath);
      } else {
        throw new AssembleError(`${path.join(root, relPath)} is not a regular file or folder`);
      }
    }
  }
  visit(root, "");
  return found.sort();
}

function parseBuild(buildBytes, label) {
  let build;
  try {
    build = JSON.parse(Buffer.from(buildBytes).toString("utf8"));
  } catch (error) {
    throw new AssembleError(`${label}: BUILD.json is not JSON: ${error.message}`);
  }
  if (build === null || typeof build !== "object" || build.format !== "sarcostat-build" || build.format_version !== 1) {
    throw new AssembleError(`${label}: BUILD.json is not a sarcostat-build format_version 1 record`);
  }
  if (typeof build.version !== "string" || !VERSION_RE.test(build.version)) {
    throw new AssembleError(`${label}: BUILD.json version ${JSON.stringify(build.version)} is not a release version`);
  }
  if (build.files === null || typeof build.files !== "object" || Array.isArray(build.files)) {
    throw new AssembleError(`${label}: BUILD.json has no files table`);
  }
  return build;
}

export function verifyZip(zipBytes, buildBytes, version) {
  const problems = [];
  let build;
  try {
    build = parseBuild(buildBytes, `v${version}`);
  } catch (error) {
    return { ok: false, problems: [error.message], files: new Map() };
  }
  if (build.version !== version) {
    problems.push(`BUILD.json says version ${build.version}, the release is ${version}`);
  }
  let entries;
  try {
    entries = readZip(zipBytes);
  } catch (error) {
    return { ok: false, problems: [...problems, `zip: ${error.message}`], files: new Map() };
  }
  const prefix = `v${version}/`;
  const files = new Map();
  for (const [name, data] of entries) {
    if (!name.startsWith(prefix)) {
      problems.push(`zip entry ${name} is outside ${prefix}`);
      continue;
    }
    files.set(name.slice(prefix.length), data);
  }
  const zipped = files.get(BUILD_FILE);
  if (zipped === undefined) {
    problems.push(`the zip has no ${prefix}${BUILD_FILE}`);
  } else if (!zipped.equals(Buffer.from(buildBytes))) {
    problems.push(`the zip's ${BUILD_FILE} differs from the release's ${BUILD_FILE} asset`);
  }
  for (const [rel, expected] of Object.entries(build.files)) {
    const data = files.get(rel);
    if (data === undefined) {
      problems.push(`${rel} is listed in BUILD.json but missing from the zip`);
    } else if (sha256(data) !== expected) {
      problems.push(`${rel}: SHA-256 differs from BUILD.json`);
    }
  }
  for (const rel of files.keys()) {
    if (rel !== BUILD_FILE && !(rel in build.files)) {
      problems.push(`${rel} is in the zip but not listed in BUILD.json`);
    }
  }
  return { ok: problems.length === 0, problems, files, build };
}

export function zipVersionFolder(versionDir) {
  const root = path.resolve(versionDir);
  const buildBytes = fs.readFileSync(path.join(root, BUILD_FILE));
  const build = parseBuild(buildBytes, root);
  if (path.basename(root) !== `v${build.version}`) {
    throw new AssembleError(`${root}: the folder must be named v${build.version}`);
  }
  if (!Number.isSafeInteger(build.source_date_epoch)) {
    throw new AssembleError(`${root}: BUILD.json has no integer source_date_epoch`);
  }
  const prefix = `v${build.version}/`;
  const entries = walk(root).map((rel) => ({ name: `${prefix}${rel}`, data: fs.readFileSync(path.join(root, rel)) }));
  const zip = writeZip(entries, { mtime: build.source_date_epoch });
  const check = verifyZip(zip, buildBytes, build.version);
  if (!check.ok) {
    throw new AssembleError(`${root} does not match its BUILD.json:\n${check.problems.join("\n")}`);
  }
  return { zip, version: build.version, files: entries.length, buildSha256: sha256(buildBytes) };
}

export function releaseInfo(tag, initText) {
  const match = /^__version__\s*=\s*["']([^"']+)["']\s*$/m.exec(initText);
  if (!match) {
    throw new AssembleError("no __version__ = \"...\" line in __init__.py");
  }
  const version = match[1];
  parseVersion(version);
  if (tag !== `v${version}`) {
    throw new AssembleError(`the tag ${tag} is not v + __version__ (v${version}); tag the commit whose __version__ is the release`);
  }
  if (parseVersion(version).dev !== null) {
    throw new AssembleError(`${version} is a development version; set __version__ to the release version before tagging`);
  }
  return { version, prerelease: isPrerelease(version) };
}

export function zipName(version) {
  return `sarcostat-web-${version}.zip`;
}

function readReleasePages(pages) {
  const releases = [];
  for (const page of pages) {
    const items = Array.isArray(page) ? page.flat() : null;
    if (items === null || !items.every((item) => item !== null && typeof item === "object" && !Array.isArray(item))) {
      throw new AssembleError("a Releases API page must be a JSON array of release objects");
    }
    releases.push(...items);
  }
  return releases;
}

export function releaseSet(pages) {
  const releases = readReleasePages(pages);
  const carried = [];
  const notes = [];
  const problems = [];
  for (const release of releases) {
    const tag = String(release.tag_name ?? "");
    const assets = Array.isArray(release.assets) ? release.assets : [];
    const names = new Map(assets.map((asset) => [asset.name, asset]));
    const webAssets = assets.filter((asset) => asset.name === BUILD_FILE || /^sarcostat-web-.+\.zip$/.test(asset.name ?? ""));
    if (release.draft !== false) {
      if (webAssets.length > 0) {
        notes.push(`${tag}: draft release, not carried`);
      }
      continue;
    }
    if (webAssets.length === 0) {
      notes.push(`${tag}: published without web assets, not carried`);
      continue;
    }
    const version = tag.startsWith("v") ? tag.slice(1) : "";
    if (!VERSION_RE.test(version)) {
      problems.push(`${tag}: carries web assets but its tag is not v<release version>`);
      continue;
    }
    const zip = names.get(zipName(version));
    const build = names.get(BUILD_FILE);
    if (!zip || !build) {
      notes.push(`${tag}: published with only ${webAssets.map((asset) => asset.name).join(", ")}; carried only with both ${zipName(version)} and ${BUILD_FILE}`);
      continue;
    }
    carried.push({
      version,
      tag,
      prerelease: isPrerelease(version),
      apiPrerelease: release.prerelease === true,
      immutable: release.immutable === true,
      publishedAt: release.published_at ?? null,
      zip: { name: zip.name, url: zip.url, digest: zip.digest ?? null },
      build: { name: build.name, url: build.url, digest: build.digest ?? null },
    });
  }
  carried.sort((a, b) => compareVersions(a.version, b.version));
  for (let i = 1; i < carried.length; i += 1) {
    if (compareVersions(carried[i - 1].version, carried[i].version) === 0) {
      problems.push(`${carried[i - 1].tag} and ${carried[i].tag} are the same PEP 440 version`);
    }
  }
  return { carried, notes, problems };
}

export function planDownloads(set) {
  return set.carried.flatMap((entry) => [`${entry.version}\t${entry.zip.name}\t${entry.zip.url}`, `${entry.version}\t${entry.build.name}\t${entry.build.url}`]);
}

export function readVersionsFile(text) {
  let data;
  try {
    data = JSON.parse(text);
  } catch (error) {
    throw new AssembleError(`the live versions.json is not JSON: ${error.message}`);
  }
  if (data === null || typeof data !== "object" || data.format !== VERSIONS_FORMAT || data.format_version !== 1 || !Array.isArray(data.versions)) {
    throw new AssembleError(`the live versions.json is not a ${VERSIONS_FORMAT} format_version 1 file`);
  }
  return data.versions.map((entry) => {
    if (entry === null || typeof entry !== "object" || typeof entry.version !== "string" || !VERSION_RE.test(entry.version)) {
      throw new AssembleError(`the live versions.json has a bad entry ${JSON.stringify(entry)}`);
    }
    return entry.version;
  });
}

export function readKnown(text) {
  const data = JSON.parse(text);
  if (data === null || typeof data !== "object" || data.format !== RELEASES_FORMAT || data.format_version !== 1 || !Array.isArray(data.releases)) {
    throw new AssembleError(`web/releases.json is not a ${RELEASES_FORMAT} format_version 1 file`);
  }
  const known = new Map();
  for (const entry of data.releases) {
    if (entry === null || typeof entry !== "object" || !VERSION_RE.test(entry.version ?? "") || !HEX64.test(entry.build_json_sha256 ?? "") || !HEX64.test(entry.zip_sha256 ?? "")) {
      throw new AssembleError(`web/releases.json: every entry needs version, build_json_sha256 and zip_sha256, got ${JSON.stringify(entry)}`);
    }
    if (known.has(entry.version)) {
      throw new AssembleError(`web/releases.json lists ${entry.version} twice`);
    }
    known.set(entry.version, entry);
  }
  return known;
}

function readStatus(file) {
  if (!fs.existsSync(file)) {
    return "missing";
  }
  return fs.readFileSync(file, "utf8").trim() || "missing";
}

function redirectScript(target) {
  return `location.replace(${JSON.stringify(target)} + location.search + location.hash);`;
}

export function redirectPage(target) {
  const script = redirectScript(target);
  const hash = crypto.createHash("sha256").update(script, "utf8").digest("base64");
  return [
    "<!doctype html>",
    '<html lang="en">',
    "<head>",
    '<meta charset="utf-8">',
    '<meta name="viewport" content="width=device-width, initial-scale=1">',
    `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'sha256-${hash}'">`,
    `<meta name="sarcostat-redirect" content="${target}">`,
    "<title>SarcoStat</title>",
    `<script>${script}</script>`,
    `<noscript><meta http-equiv="refresh" content="0; url=${target}"></noscript>`,
    "</head>",
    "<body>",
    `<p><a href="${target}">Open SarcoStat</a></p>`,
    "</body>",
    "</html>",
    "",
  ].join("\n");
}

function digestMatches(asset, bytes) {
  if (typeof asset.digest !== "string" || !asset.digest.startsWith("sha256:")) {
    return true;
  }
  return asset.digest.slice("sha256:".length) === sha256(bytes);
}

function short(hex) {
  return typeof hex === "string" ? hex.slice(0, 12) : "-";
}

export function assemble(options) {
  const { pages, assetsDir, liveDir, known = new Map(), event, runAttempt, ref, outDir } = options;
  const problems = [];
  const warnings = [];
  const notes = [];
  const attempt = Number(runAttempt);
  if (!Number.isSafeInteger(attempt) || attempt < 1) {
    throw new AssembleError(`--run-attempt must be a positive integer, got ${JSON.stringify(runAttempt)}`);
  }
  if (!/^refs\/tags\/v[^/]+$/.test(ref ?? "")) {
    problems.push(`the ref ${ref} is not a v* tag: Pages is deployed only from a release tag`);
  }
  const repair = event === "workflow_dispatch" || attempt > 1;
  const set = releaseSet(pages);
  problems.push(...set.problems);
  notes.push(...set.notes);
  const byVersion = new Map(set.carried.map((entry) => [entry.version, entry]));
  const refVersion = (ref ?? "").startsWith("refs/tags/v") ? ref.slice("refs/tags/v".length) : null;
  if (event === "release" && refVersion !== null && !byVersion.has(refVersion)) {
    problems.push(`the published release v${refVersion} does not carry both ${zipName(refVersion)} and ${BUILD_FILE}`);
  }
  if (event === "workflow_dispatch" && refVersion !== null && !byVersion.has(refVersion)) {
    notes.push(`dispatch on v${refVersion}, which is not a carried release; the version set still comes from the Releases API`);
  }

  const verified = new Map();
  for (const entry of set.carried) {
    const dir = path.join(assetsDir, entry.version);
    const zipFile = path.join(dir, entry.zip.name);
    const buildFile = path.join(dir, entry.build.name);
    if (!fs.existsSync(zipFile) || !fs.existsSync(buildFile)) {
      problems.push(`v${entry.version}: the downloaded assets are missing in ${dir}`);
      continue;
    }
    const zipBytes = fs.readFileSync(zipFile);
    const buildBytes = fs.readFileSync(buildFile);
    if (!digestMatches(entry.zip, zipBytes) || !digestMatches(entry.build, buildBytes)) {
      problems.push(`v${entry.version}: a downloaded asset differs from the digest the Releases API reports`);
      continue;
    }
    const check = verifyZip(zipBytes, buildBytes, entry.version);
    check.problems.forEach((line) => problems.push(`v${entry.version}: ${line}`));
    const listed = known.get(entry.version);
    const buildSha = sha256(buildBytes);
    const zipSha = sha256(zipBytes);
    if (listed) {
      if (listed.build_json_sha256 !== buildSha) {
        problems.push(`v${entry.version}: BUILD.json SHA-256 ${short(buildSha)} differs from web/releases.json (${short(listed.build_json_sha256)})`);
      }
      if (listed.zip_sha256 !== zipSha) {
        problems.push(`v${entry.version}: zip SHA-256 ${short(zipSha)} differs from web/releases.json (${short(listed.zip_sha256)})`);
      }
    }
    if (!entry.immutable) {
      warnings.push(`v${entry.version}: the release is not immutable (immutable is not true in the Releases API)`);
    }
    if (entry.apiPrerelease !== entry.prerelease) {
      notes.push(`v${entry.version}: the release is marked ${entry.apiPrerelease ? "pre-release" : "release"} on GitHub; the version string makes it a ${entry.prerelease ? "pre-release" : "release"}, which is what counts`);
    }
    if (check.ok) {
      verified.set(entry.version, { entry, files: check.files, build: check.build, buildSha, zipSha, listed: Boolean(listed), state: "new" });
    }
  }
  for (const version of known.keys()) {
    if (!byVersion.has(version)) {
      warnings.push(`v${version} is listed in web/releases.json but is not a carried release`);
    }
  }

  const versionsStatus = readStatus(path.join(liveDir, "versions.status"));
  let live = [];
  if (versionsStatus === "200") {
    try {
      live = readVersionsFile(fs.readFileSync(path.join(liveDir, VERSIONS_FILE), "utf8"));
    } catch (error) {
      problems.push(error.message);
    }
  } else if (versionsStatus === "404") {
    if (set.carried.length === 1) {
      notes.push("first deploy: the live /versions.json answered 404 and the Releases API carries exactly one version");
    } else {
      problems.push(`the live /versions.json answered 404 but the Releases API carries ${set.carried.length} versions; a 404 passes only on a true first deploy (exactly one version)`);
    }
  } else {
    problems.push(`the live /versions.json could not be read (HTTP status ${versionsStatus})`);
  }
  for (const version of live) {
    const entry = byVersion.get(version);
    if (!entry) {
      problems.push(`live version v${version} is not a published release carrying both assets: this deploy would drop it`);
      continue;
    }
    const status = readStatus(path.join(liveDir, `v${version}`, `${BUILD_FILE}.status`));
    const liveFile = path.join(liveDir, `v${version}`, BUILD_FILE);
    const liveSha = status === "200" && fs.existsSync(liveFile) ? sha256(fs.readFileSync(liveFile)) : null;
    const record = verified.get(version);
    const releaseSha = record ? record.buildSha : null;
    if (record && liveSha === releaseSha) {
      record.state = "unchanged";
      continue;
    }
    const what = liveSha === null ? `the live /v${version}/BUILD.json could not be read (HTTP status ${status})` : `the live /v${version}/BUILD.json (${short(liveSha)}) differs from its release asset (${short(releaseSha)})`;
    if (repair) {
      warnings.push(`${what}; repaired from the release zip (${event === "workflow_dispatch" ? "dispatch run" : `run attempt ${attempt}`})`);
      if (record) {
        record.state = "repaired";
      }
    } else {
      problems.push(`${what}; a first-attempt release run stops here: check the live folder, then use "Re-run all jobs" on this run to repair it from the release zip`);
      if (record) {
        record.state = "differs (not repaired)";
      }
    }
  }

  const versions = [...verified.keys()].sort(compareVersions);
  const latest = versions.length > 0 ? pickLatest(versions) : null;
  if (versions.length === 0) {
    problems.push("no carried version to deploy");
  }
  const ok = problems.length === 0;
  if (ok) {
    if (fs.existsSync(outDir) && fs.readdirSync(outDir).length > 0) {
      throw new AssembleError(`--out ${outDir} already holds files`);
    }
    for (const version of versions) {
      for (const [rel, data] of verified.get(version).files) {
        const target = path.join(outDir, `v${version}`, ...rel.split("/"));
        fs.mkdirSync(path.dirname(target), { recursive: true });
        fs.writeFileSync(target, data);
      }
    }
    fs.mkdirSync(path.join(outDir, "latest"), { recursive: true });
    fs.writeFileSync(path.join(outDir, "latest", "index.html"), redirectPage(`../v${latest}/`));
    fs.writeFileSync(path.join(outDir, "index.html"), redirectPage("latest/"));
    const listing = {
      format: VERSIONS_FORMAT,
      format_version: 1,
      latest,
      versions: versions.map((version) => {
        const record = verified.get(version);
        return {
          version,
          path: `v${version}/`,
          prerelease: record.entry.prerelease,
          build_json_sha256: record.buildSha,
          source_digest: record.build.source_digest ?? null,
          commit: record.build.commit ?? null,
        };
      }),
    };
    fs.writeFileSync(path.join(outDir, VERSIONS_FILE), canonicalJson(listing));
  }
  const rows = versions.map((version) => {
    const record = verified.get(version);
    return `| ${version}${version === latest ? " (latest)" : ""} | ${record.entry.prerelease ? "yes" : "no"} | \`${short(record.buildSha)}\` | ${record.listed ? "listed" : "not listed"} | ${record.state} | ${record.entry.immutable ? "yes" : "**no**"} |`;
  });
  const summary = [
    `## Pages assembly: ${ok ? "ready to deploy" : "FAILED"}`,
    "",
    `Ref \`${ref}\`, event \`${event}\`, run attempt ${attempt}${repair ? " (repair allowed)" : ""}.`,
    "",
    "| Version | Pre-release | BUILD.json | web/releases.json | Live folder | Immutable |",
    "|---|---|---|---|---|---|",
    ...rows,
    "",
    latest === null ? "No version to deploy." : `Latest: ${latest}. Root and /latest/ redirect to v${latest}/, keeping the hash.`,
    ...(problems.length > 0 ? ["", "### Problems", "", ...problems.map((line) => `- ${line}`)] : []),
    ...(warnings.length > 0 ? ["", "### Warnings", "", ...warnings.map((line) => `- ${line}`)] : []),
    ...(notes.length > 0 ? ["", "### Notes", "", ...notes.map((line) => `- ${line}`)] : []),
    "",
  ].join("\n");
  return { ok, problems, warnings, notes, latest, versions, summary, repair };
}

function readJsonFile(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function need(values, keys, command) {
  for (const key of keys) {
    const value = values[key];
    if (value === undefined || (Array.isArray(value) && value.length === 0)) {
      throw new AssembleError(`${command} needs --${key}`);
    }
  }
}

export function main(argv = process.argv.slice(2)) {
  const { values, positionals } = parseArgs({
    args: argv,
    options: {
      site: { type: "string" },
      out: { type: "string" },
      zip: { type: "string" },
      build: { type: "string" },
      tag: { type: "string" },
      init: { type: "string" },
      releases: { type: "string", multiple: true },
      versions: { type: "string" },
      assets: { type: "string" },
      live: { type: "string" },
      known: { type: "string" },
      event: { type: "string" },
      "run-attempt": { type: "string" },
      ref: { type: "string" },
      summary: { type: "string" },
      help: { type: "boolean", default: false },
    },
    allowPositionals: true,
    strict: true,
  });
  const command = positionals[0];
  if (values.help || positionals.length !== 1) {
    console.log(USAGE);
    return values.help ? 0 : 2;
  }
  if (command === "zip") {
    need(values, ["site", "out"], command);
    const made = zipVersionFolder(values.site);
    fs.writeFileSync(values.out, made.zip);
    console.log(`assemble_pages: zip ${values.out}: ${made.files} files of v${made.version}, ${made.zip.length} bytes, sha256 ${sha256(made.zip)}; BUILD.json sha256 ${made.buildSha256}`);
    return 0;
  }
  if (command === "verify-zip") {
    need(values, ["zip", "build"], command);
    const buildBytes = fs.readFileSync(values.build);
    const version = parseBuild(buildBytes, values.build).version;
    const check = verifyZip(fs.readFileSync(values.zip), buildBytes, version);
    check.problems.forEach((line) => console.error(`assemble_pages: FAIL ${line}`));
    console.log(check.ok ? `assemble_pages: ${values.zip} matches ${values.build} (v${version}, ${check.files.size} files)` : `assemble_pages: ${values.zip} does not match ${values.build}`);
    return check.ok ? 0 : 1;
  }
  if (command === "release-info") {
    need(values, ["tag", "init"], command);
    const info = releaseInfo(values.tag, fs.readFileSync(values.init, "utf8"));
    console.log(`version=${info.version}`);
    console.log(`prerelease=${info.prerelease}`);
    return 0;
  }
  if (command === "plan") {
    need(values, ["releases", "out"], command);
    const set = releaseSet(values.releases.map(readJsonFile));
    set.notes.forEach((line) => console.log(`assemble_pages: note: ${line}`));
    set.problems.forEach((line) => console.error(`assemble_pages: FAIL ${line}`));
    const lines = planDownloads(set);
    fs.writeFileSync(values.out, lines.length > 0 ? `${lines.join("\n")}\n` : "");
    console.log(`assemble_pages: ${set.carried.length} carried version(s): ${set.carried.map((entry) => entry.version).join(", ") || "none"}`);
    return set.problems.length === 0 ? 0 : 1;
  }
  if (command === "live-list") {
    need(values, ["versions"], command);
    readVersionsFile(fs.readFileSync(values.versions, "utf8")).forEach((version) => console.log(version));
    return 0;
  }
  if (command === "assemble") {
    need(values, ["releases", "assets", "live", "event", "run-attempt", "ref", "out"], command);
    const result = assemble({
      pages: values.releases.map(readJsonFile),
      assetsDir: values.assets,
      liveDir: values.live,
      known: values.known ? readKnown(fs.readFileSync(values.known, "utf8")) : new Map(),
      event: values.event,
      runAttempt: values["run-attempt"],
      ref: values.ref,
      outDir: values.out,
    });
    console.log(result.summary);
    if (values.summary) {
      fs.appendFileSync(values.summary, result.summary);
    }
    return result.ok ? 0 : 1;
  }
  console.error(`assemble_pages: unknown command ${command}\n${USAGE}`);
  return 2;
}

if (process.argv[1] && fs.realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    process.exitCode = main();
  } catch (error) {
    console.error(`assemble_pages: ${error instanceof AssembleError ? error.message : error.stack ?? error}`);
    process.exitCode = error instanceof AssembleError ? 1 : 2;
  }
}
