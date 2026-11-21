import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

import { SITE_LIST, pyodideVersion, readList } from "./pyodide_subset.mjs";

const WEB_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

export const MAX_SITE_BYTES = 35_000_000;
export const BUILD_FILE = "BUILD.json";
export const ALLOW_LIST = path.join(WEB_DIR, "site-allowlist.json");
export const HOOKS_DIR = path.join(WEB_DIR, "tests", "e2e", "hooks");
export const HOOK_PREFIX = "__sarcostatTest";
export const TEXT_EXTENSIONS = [".html", ".js", ".mjs", ".css", ".json", ".svg", ".txt", ".map"];
export const XML_NAMESPACES = ["http://www.w3.org/2000/svg", "http://www.w3.org/1999/xlink", "http://www.w3.org/1999/xhtml"];

const HEX64 = /^[0-9a-f]{64}$/;
const COMMIT = /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/;
const VERSION = /^\d+(?:\.\d+)*(?:(?:a|b|rc)\d+)?(?:\.post\d+)?(?:\.dev\d+)?$/;
const HOOK_EXTENSIONS = new Set([".js", ".mjs", ".cjs", ".ts", ".mts", ".cts"]);
const QUOTES = new Set(['"', "'", "`"]);
const ABSOLUTE_URL = /\b(?:https?|wss?|ftp|file):\/\/[^\s"'`<>()\\]*/gi;
const RELATIVE_SCHEME_URL = /(?:\burl\(\s*["']?|\b(?:src|href|action|xlink:href)\s*=\s*["']?|@import\s+["']?|["'`])\/\/[a-z0-9-]+(?:\.[a-z0-9-]+)+[^\s"'`<>()\\]*/gi;
const HOOK_EXPORT = /export\s+(?:const|let|var)\s+HOOK_SYMBOLS\b[^=]*=\s*(?:Object\.freeze\(\s*)?\[([^\]]*)\]/;
const STRING_LITERAL = /(["'`])((?:\\.|(?!\1)[^\\])*)\1/g;
const IDENTIFIER = /^[A-Za-z_$][\w$]*$/;
const BUILD_KEYS = ["commit", "files", "format", "format_version", "pyodide", "resume_digests", "source_date_epoch", "source_digest", "tools", "version", "wheel"];
const TOOL_KEYS = ["node", "python", "typescript", "vite"];

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

export function walkFiles(root) {
  const found = [];
  const odd = [];
  function visit(dir, rel) {
    const entries = fs.readdirSync(dir, { withFileTypes: true }).sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
    for (const entry of entries) {
      const relPath = rel ? `${rel}/${entry.name}` : entry.name;
      const full = path.join(dir, entry.name);
      if (entry.isSymbolicLink()) {
        odd.push(`${relPath} is a symbolic link`);
      } else if (entry.isDirectory()) {
        visit(full, relPath);
      } else if (entry.isFile()) {
        found.push(relPath);
      } else {
        odd.push(`${relPath} is not a regular file`);
      }
    }
  }
  visit(root, "");
  return { files: found.sort(), odd };
}

export function hashTree(root, { exclude = [BUILD_FILE] } = {}) {
  const { files, odd } = walkFiles(root);
  if (odd.length > 0) {
    throw new Error(`${root}: ${odd.join("; ")}`);
  }
  const skip = new Set(exclude);
  return Object.fromEntries(files.filter((rel) => !skip.has(rel)).map((rel) => [rel, sha256(fs.readFileSync(path.join(root, rel)))]));
}

export function externalUrls(text) {
  const found = [];
  for (const match of text.matchAll(ABSOLUTE_URL)) {
    const url = match[0];
    const before = text[match.index - 1] ?? "";
    const after = text[match.index + url.length] ?? "";
    if (XML_NAMESPACES.includes(url) && (QUOTES.has(before) || before === "=") && QUOTES.has(after)) {
      continue;
    }
    found.push(url);
  }
  for (const match of text.matchAll(RELATIVE_SCHEME_URL)) {
    found.push(match[0].slice(match[0].indexOf("//")));
  }
  return found;
}

function hookFiles(dir) {
  if (!fs.existsSync(dir)) {
    return [];
  }
  return walkFiles(dir)
    .files.filter((rel) => HOOK_EXTENSIONS.has(path.extname(rel)) && !rel.endsWith(".d.ts"))
    .map((rel) => path.join(dir, rel));
}

export function hookSymbols(dir = HOOKS_DIR) {
  const symbols = new Set();
  const notes = [];
  const files = hookFiles(dir);
  for (const file of files) {
    const match = HOOK_EXPORT.exec(fs.readFileSync(file, "utf8"));
    const rel = path.relative(WEB_DIR, file);
    if (!match) {
      notes.push(`${rel} exports no HOOK_SYMBOLS list`);
      continue;
    }
    for (const literal of match[1].matchAll(STRING_LITERAL)) {
      const name = literal[2];
      if (!IDENTIFIER.test(name)) {
        notes.push(`${rel}: HOOK_SYMBOLS entry ${JSON.stringify(name)} is not an identifier`);
        continue;
      }
      if (!name.startsWith(HOOK_PREFIX)) {
        notes.push(`${rel}: HOOK_SYMBOLS entry ${name} does not start with ${HOOK_PREFIX}`);
      }
      symbols.add(name);
    }
  }
  return { symbols: [...symbols].sort(), files, notes };
}

export function readAllowList(file = ALLOW_LIST) {
  const data = JSON.parse(fs.readFileSync(file, "utf8"));
  if (data.format !== "sarcostat-site-allowlist" || data.format_version !== 1 || !Array.isArray(data.files)) {
    throw new Error(`${file} is not a sarcostat site allow-list (format sarcostat-site-allowlist, format_version 1, files)`);
  }
  const bySha = new Map();
  for (const entry of data.files) {
    if (!HEX64.test(entry.sha256 ?? "") || typeof entry.name !== "string" || typeof entry.reason !== "string" || entry.reason === "") {
      throw new Error(`${file}: every entry needs name, sha256 (64 hex) and reason, got ${JSON.stringify(entry)}`);
    }
    bySha.set(entry.sha256, entry);
  }
  return bySha;
}

function checkBuild(build, folderName, problems) {
  const keys = Object.keys(build).sort();
  if (JSON.stringify(keys) !== JSON.stringify(BUILD_KEYS)) {
    problems.push(`${BUILD_FILE}: keys must be exactly ${BUILD_KEYS.join(", ")}, found ${keys.join(", ")}`);
  }
  if (build.format !== "sarcostat-build" || build.format_version !== 1) {
    problems.push(`${BUILD_FILE}: format must be sarcostat-build, format_version 1`);
  }
  if (typeof build.version !== "string" || !VERSION.test(build.version)) {
    problems.push(`${BUILD_FILE}: version ${JSON.stringify(build.version)} is not a release version`);
  } else if (folderName !== `v${build.version}`) {
    problems.push(`${BUILD_FILE}: version ${build.version} does not match the folder name ${folderName}`);
  }
  if (!(build.commit === null || (typeof build.commit === "string" && COMMIT.test(build.commit)))) {
    problems.push(`${BUILD_FILE}: commit must be null or a full commit id`);
  }
  if (!Number.isSafeInteger(build.source_date_epoch) || build.source_date_epoch < 0) {
    problems.push(`${BUILD_FILE}: source_date_epoch must be a non-negative integer`);
  }
  const wheel = build.wheel ?? {};
  if (JSON.stringify(Object.keys(wheel).sort()) !== JSON.stringify(["file", "sha256"]) || !/^wheels\/sarcostat-[^/]+\.whl$/.test(wheel.file ?? "") || !HEX64.test(wheel.sha256 ?? "")) {
    problems.push(`${BUILD_FILE}: wheel must be {file: wheels/sarcostat-<version>-....whl, sha256}`);
  } else if (build.files?.[wheel.file] !== wheel.sha256) {
    problems.push(`${BUILD_FILE}: wheel.sha256 differs from files[${wheel.file}]`);
  }
  if (!HEX64.test(build.source_digest ?? "")) {
    problems.push(`${BUILD_FILE}: source_digest must be 64 hex digits`);
  }
  const pinned = pyodideVersion();
  if (build.pyodide?.version !== pinned || build.pyodide?.dir !== "pyodide/" || Object.keys(build.pyodide ?? {}).length !== 2) {
    problems.push(`${BUILD_FILE}: pyodide must be {version: ${pinned}, dir: pyodide/}`);
  }
  if (!Array.isArray(build.resume_digests) || !build.resume_digests.every((digest) => HEX64.test(digest))) {
    problems.push(`${BUILD_FILE}: resume_digests must be a list of 64-hex digests`);
  }
  const tools = build.tools ?? {};
  if (JSON.stringify(Object.keys(tools).sort()) !== JSON.stringify(TOOL_KEYS) || !TOOL_KEYS.every((key) => typeof tools[key] === "string" && tools[key] !== "")) {
    problems.push(`${BUILD_FILE}: tools must be {${TOOL_KEYS.join(", ")}} with version strings`);
  }
  if (build.files === null || typeof build.files !== "object" || Array.isArray(build.files)) {
    problems.push(`${BUILD_FILE}: files must be an object {path: sha256}`);
    return {};
  }
  return build.files;
}

export function checkSite(versionDir, { maxBytes = MAX_SITE_BYTES, allowList = ALLOW_LIST, hooksDir = HOOKS_DIR, pyodideList = SITE_LIST } = {}) {
  const root = path.resolve(versionDir);
  const problems = [];
  const notes = [];
  if (!fs.existsSync(root) || !fs.statSync(root).isDirectory()) {
    return { ok: false, problems: [`${root} is not a folder`], notes, bytes: 0, files: 0 };
  }
  const buildPath = path.join(root, BUILD_FILE);
  if (!fs.existsSync(buildPath)) {
    return { ok: false, problems: [`${BUILD_FILE} is missing`], notes, bytes: 0, files: 0 };
  }
  const buildText = fs.readFileSync(buildPath, "utf8");
  let build;
  try {
    build = JSON.parse(buildText);
  } catch (error) {
    return { ok: false, problems: [`${BUILD_FILE} is not JSON: ${error.message}`], notes, bytes: 0, files: 0 };
  }
  if (canonicalJson(build) !== buildText) {
    problems.push(`${BUILD_FILE} is not canonical JSON (sorted keys, two-space indent, ASCII, final newline)`);
  }
  const listed = checkBuild(build, path.basename(root), problems);

  const { files, odd } = walkFiles(root);
  odd.forEach((line) => problems.push(line));
  const present = new Set(files);
  let bytes = 0;
  const data = new Map();
  for (const rel of files) {
    const content = fs.readFileSync(path.join(root, rel));
    bytes += content.length;
    data.set(rel, content);
    if (rel === BUILD_FILE) {
      continue;
    }
    const expected = listed[rel];
    if (expected === undefined) {
      problems.push(`${rel} is not listed in ${BUILD_FILE}`);
    } else if (sha256(content) !== expected) {
      problems.push(`${rel}: SHA-256 differs from ${BUILD_FILE}`);
    }
  }
  for (const rel of Object.keys(listed)) {
    if (!present.has(rel)) {
      problems.push(`${rel} is listed in ${BUILD_FILE} but missing`);
    }
  }
  if (!present.has("index.html")) {
    problems.push("index.html is missing");
  }

  const pinned = readList(pyodideList);
  const pinnedNames = new Set(pinned.map((entry) => entry.name));
  for (const entry of pinned) {
    const content = data.get(`pyodide/${entry.name}`);
    if (content === undefined) {
      problems.push(`pyodide/${entry.name} (${path.basename(pyodideList)}) is missing`);
    } else if (sha256(content) !== entry.sha256) {
      problems.push(`pyodide/${entry.name}: SHA-256 differs from ${path.basename(pyodideList)}`);
    }
  }
  for (const rel of files) {
    if (rel.startsWith("pyodide/") && !pinnedNames.has(rel.slice("pyodide/".length))) {
      problems.push(`${rel} is not in ${path.basename(pyodideList)}`);
    }
  }

  if (bytes > maxBytes) {
    problems.push(`size ${bytes} bytes exceeds the cap of ${maxBytes} bytes`);
  }

  const allowed = readAllowList(allowList);
  const usedAllowance = new Set();
  for (const rel of files) {
    if (!TEXT_EXTENSIONS.includes(path.extname(rel).toLowerCase())) {
      continue;
    }
    const content = data.get(rel);
    const urls = externalUrls(content.toString("latin1"));
    if (urls.length === 0) {
      continue;
    }
    const digest = sha256(content);
    const allowance = allowed.get(digest);
    if (allowance) {
      usedAllowance.add(digest);
      notes.push(`${rel}: ${urls.length} external URL(s) allowed by SHA-256 (${allowance.name})`);
    } else {
      const shown = [...new Set(urls)].slice(0, 5).join(", ");
      problems.push(`${rel}: external URL(s) in a shipped text file: ${shown}`);
    }
  }
  for (const [digest, entry] of allowed) {
    if (!usedAllowance.has(digest)) {
      notes.push(`allow-list entry ${entry.name} (${digest.slice(0, 12)}) is not used by this site`);
    }
  }

  const hooks = hookSymbols(hooksDir);
  hooks.notes.forEach((line) => notes.push(`hooks: ${line}`));
  const needles = [HOOK_PREFIX, ...hooks.symbols.filter((name) => !name.startsWith(HOOK_PREFIX))];
  for (const rel of files) {
    const content = data.get(rel);
    const hits = needles.filter((needle) => content.includes(needle, 0, "latin1"));
    if (hits.length > 0) {
      problems.push(`${rel}: test-hook symbol(s) in a shipped file: ${hits.join(", ")}`);
    }
  }
  notes.push(`hooks: ${hooks.files.length} hook file(s) under ${path.relative(WEB_DIR, path.resolve(hooksDir)) || hooksDir}, ${hooks.symbols.length} HOOK_SYMBOLS name(s); searched for the prefix ${HOOK_PREFIX}${needles.length > 1 ? ` and ${needles.length - 1} other name(s)` : ""}`);

  return { ok: problems.length === 0, problems, notes, bytes, files: files.length, build };
}

function main() {
  const { values, positionals } = parseArgs({
    options: { help: { type: "boolean", default: false } },
    allowPositionals: true,
    strict: true,
  });
  if (values.help || positionals.length !== 1) {
    console.log("Usage: node scripts/check_site.mjs <site root>/v<version>");
    console.log("Checks a built version folder (V-SITE): every file against BUILD.json, the Pyodide files against web/pyodide-files.sha256, the size cap of 35 MB, no external URL in text files except those allow-listed by SHA-256 in web/site-allowlist.json and XML namespaces, no test-hook symbol.");
    return values.help ? 0 : 2;
  }
  const dir = positionals[0];
  console.log(`check_site: node ${process.version}, ${path.resolve(dir)}`);
  const result = checkSite(dir);
  result.notes.forEach((line) => console.log(`check_site: note: ${line}`));
  result.problems.forEach((line) => console.error(`check_site: FAIL ${line}`));
  const size = `${result.files} files, ${result.bytes} bytes (${(result.bytes / 1e6).toFixed(2)} MB of ${(MAX_SITE_BYTES / 1e6).toFixed(0)} MB)`;
  console.log(result.ok ? `check_site: PASS ${size}` : `check_site: FAILED with ${result.problems.length} problem(s); ${size}`);
  return result.ok ? 0 : 1;
}

if (process.argv[1] && fs.realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    process.exitCode = main();
  } catch (error) {
    console.error(`check_site: ${error.stack ?? error}`);
    process.exitCode = 2;
  }
}
