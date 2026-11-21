import { spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

import { WEB_DIR, insideDir, laneName } from "../../scripts/lane.mjs";

const USAGE = [
  "Usage: node tests/e2e/rehearse_retest.mjs --into <deployed root> [--site <site root or its v<version> folder>] [-- <playwright arguments>]",
  "",
  "Rehearses V-RETEST (plan M3-6 acceptance 4) on local bytes: copies every file listed in BUILD.json of the version folder, and BUILD.json itself, into <deployed root>/SarcoStat/v<version>/, checks each SHA-256, runs check_site.mjs on the copy, then runs the unchanged e2e suite (npm run test:e2e) with SITE_DIR=<deployed root>, so the app is served on 127.0.0.1 under /SarcoStat/v<version>/ as on GitHub Pages.",
  "--site defaults to web/dist/<LANE>/site; PW_OUT defaults to test-results/<LANE>/retest; PORT defaults to 0. The deployed root must lie outside web/dist/ and the site, and may hold only SarcoStat/v<version>/ folders; the copied version folder is replaced.",
].join("\n");

const VERSION_DIR = /^v\d[0-9A-Za-z.+-]*$/;
const BUILD_FILE = "BUILD.json";

function sha256(file) {
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

export function findVersionFolder(site) {
  const abs = path.resolve(site);
  if (VERSION_DIR.test(path.basename(abs)) && fs.existsSync(path.join(abs, BUILD_FILE))) {
    return abs;
  }
  const found = fs.existsSync(abs) ? fs.readdirSync(abs).filter((name) => VERSION_DIR.test(name) && fs.existsSync(path.join(abs, name, BUILD_FILE))) : [];
  if (found.length !== 1) {
    throw new Error(`expected one v<version>/${BUILD_FILE} under ${abs}, found ${JSON.stringify(found)}; build the site first or pass --site`);
  }
  return path.join(abs, found[0]);
}

export function checkDeployedRoot(root, source) {
  const abs = path.resolve(root);
  const dist = path.join(WEB_DIR, "dist");
  if (abs === source || insideDir(source, abs) || insideDir(abs, source) || abs === dist || insideDir(dist, abs)) {
    throw new Error(`the deployed root ${abs} must lie outside web/dist/ and outside the site ${source}`);
  }
  if (fs.existsSync(abs)) {
    const extra = fs.readdirSync(abs).filter((name) => name !== "SarcoStat");
    const inner = path.join(abs, "SarcoStat");
    const odd = fs.existsSync(inner) ? fs.readdirSync(inner).filter((name) => !VERSION_DIR.test(name)) : [];
    if (extra.length > 0 || odd.length > 0) {
      throw new Error(`${abs} holds ${[...extra, ...odd.map((name) => `SarcoStat/${name}`)].join(", ")}; give an empty folder or one this script made`);
    }
  }
  return abs;
}

export function copyBuild(source, root) {
  const build = JSON.parse(fs.readFileSync(path.join(source, BUILD_FILE), "utf8"));
  const folder = `v${build.version}`;
  if (path.basename(source) !== folder) {
    throw new Error(`${source} holds ${BUILD_FILE} of version ${build.version}, expected the folder name ${folder}`);
  }
  const dest = path.join(root, "SarcoStat", folder);
  fs.rmSync(dest, { recursive: true, force: true });
  let bytes = 0;
  const entries = Object.entries(build.files ?? {});
  for (const [rel, digest] of entries) {
    if (path.isAbsolute(rel) || rel.includes("\\") || rel.split("/").includes("..") || rel === BUILD_FILE) {
      throw new Error(`${BUILD_FILE} lists the path ${JSON.stringify(rel)}, which is not a file inside the version folder`);
    }
    const target = path.join(dest, rel);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.copyFileSync(path.join(source, rel), target);
    const got = sha256(target);
    if (got !== digest) {
      throw new Error(`${rel}: SHA-256 ${got} differs from ${BUILD_FILE} ${digest}`);
    }
    bytes += fs.statSync(target).size;
  }
  fs.copyFileSync(path.join(source, BUILD_FILE), path.join(dest, BUILD_FILE));
  return { version: build.version, folder, dest, files: entries.length, bytes };
}

function main(argv) {
  const { values, positionals } = parseArgs({
    args: argv,
    options: { into: { type: "string" }, site: { type: "string" }, help: { type: "boolean", default: false } },
    allowPositionals: true,
    strict: true,
  });
  if (values.help) {
    console.log(USAGE);
    return 0;
  }
  if (!values.into) {
    console.error(`rehearse_retest: --into is required\n${USAGE}`);
    return 2;
  }
  console.log(`rehearse_retest: node ${process.version}`);
  const lane = laneName();
  const source = findVersionFolder(values.site ?? path.join(WEB_DIR, "dist", lane, "site"));
  const root = checkDeployedRoot(values.into, source);
  const copy = copyBuild(source, root);
  console.log(`rehearse_retest: copied ${copy.files} listed files (${copy.bytes} bytes, every SHA-256 equal to ${BUILD_FILE}) and ${BUILD_FILE} from ${source} to ${copy.dest}`);
  const check = spawnSync(process.execPath, [path.join(WEB_DIR, "scripts", "check_site.mjs"), copy.dest], { cwd: WEB_DIR, stdio: "inherit" });
  if (check.status !== 0) {
    console.error(`rehearse_retest: FAIL check_site.mjs exited ${check.status} on the copy`);
    return 1;
  }
  const env = {
    ...process.env,
    LANE: lane,
    SITE_DIR: root,
    SITE_VERSION: copy.folder,
    PORT: process.env.PORT && process.env.PORT !== "" ? process.env.PORT : "0",
    PW_OUT: process.env.PW_OUT && process.env.PW_OUT !== "" ? process.env.PW_OUT : `test-results/${lane}/retest`,
    PLAYWRIGHT_HTML_OPEN: "never",
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD: "1",
  };
  delete env.DISPLAY;
  delete env.WAYLAND_DISPLAY;
  console.log(`rehearse_retest: npm run test:e2e with SITE_DIR=${root}, app under /SarcoStat/${copy.folder}/, PW_OUT=${env.PW_OUT}`);
  const run = spawnSync("npm", ["run", "test:e2e", "--", ...positionals], { cwd: WEB_DIR, env, stdio: "inherit" });
  const code = run.status ?? 1;
  console.log(`rehearse_retest: ${code === 0 ? "PASS" : "FAIL"} the unchanged e2e suite on the copy served under /SarcoStat/${copy.folder}/ (exit ${code})`);
  return code;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    process.exitCode = main(process.argv.slice(2));
  } catch (error) {
    console.error(`rehearse_retest: ${error instanceof Error ? error.message : String(error)}`);
    process.exitCode = 2;
  }
}
