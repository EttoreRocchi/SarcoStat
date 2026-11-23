import { execFileSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { buildSite } from "../../scripts/build_site.mjs";
import { WEB_DIR, laneName } from "../../scripts/lane.mjs";

export const REPO_DIR = path.resolve(WEB_DIR, "..");
export const BUILDS = [
  { key: "b1", version: "0.2.0b1", resumeFrom: null },
  { key: "rc1", version: "0.2.0rc1", resumeFrom: null },
  { key: "hotfix", version: "0.2.0rc1.post1", resumeFrom: "rc1" },
  { key: "next", version: "0.2.0rc2.dev0", resumeFrom: null },
];
const EXCLUDES = ["/web", "/paper", "/.omc", "/dist", "/build", "/.git", "/.venv", "/.mypy_cache", "/.ruff_cache", "/.pytest_cache", "/node_modules"];
const VERSION_LINE = /^__version__ = "[^"]*"$/m;

export function buildsRoot(lane = laneName()) {
  return path.join(WEB_DIR, "dist", lane, "builds");
}

function workDir(lane) {
  return path.join(WEB_DIR, "dist", lane, "builds-work");
}

function treeDigest(root, rels) {
  const hash = crypto.createHash("sha256");
  const walk = (rel) => {
    const full = path.join(root, rel);
    if (!fs.existsSync(full)) {
      return;
    }
    if (fs.statSync(full).isDirectory()) {
      for (const name of fs.readdirSync(full).sort()) {
        if (name !== "__pycache__") {
          walk(path.join(rel, name));
        }
      }
      return;
    }
    hash.update(`${rel}\n`);
    hash.update(fs.readFileSync(full));
  };
  rels.forEach(walk);
  return hash.digest("hex");
}

export const FALLBACK_EPOCH = "315532800";

export function sourceDateEpoch(env = process.env) {
  if (env.SOURCE_DATE_EPOCH && /^\d+$/.test(env.SOURCE_DATE_EPOCH)) {
    return env.SOURCE_DATE_EPOCH;
  }
  try {
    return execFileSync("git", ["-C", REPO_DIR, "--no-optional-locks", "log", "-1", "--format=%ct", "HEAD"], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
  } catch {
    return FALLBACK_EPOCH;
  }
}

function wheelFor(version, work, python, epoch, log) {
  const src = path.join(work, `src-${version}`);
  fs.mkdirSync(src, { recursive: true });
  execFileSync("rsync", ["-a", "--delete", ...EXCLUDES.flatMap((pattern) => ["--exclude", pattern]), `${REPO_DIR}/`, `${src}/`]);
  const init = path.join(src, "src", "sarcostat", "__init__.py");
  const text = fs.readFileSync(init, "utf8");
  if (!VERSION_LINE.test(text)) {
    throw new Error(`${init} has no __version__ line`);
  }
  fs.writeFileSync(init, text.replace(VERSION_LINE, `__version__ = "${version}"`));
  const key = treeDigest(src, ["pyproject.toml", "MANIFEST.in", "setup.py", "setup.cfg", path.join("src", "sarcostat")]);
  const out = path.join(work, `wheel-${version}`);
  const stamp = path.join(out, "SOURCE_KEY");
  const wheelName = `sarcostat-${version}-py3-none-any.whl`;
  const wheel = path.join(out, wheelName);
  if (fs.existsSync(stamp) && fs.readFileSync(stamp, "utf8") === key && fs.existsSync(wheel)) {
    log(`builds: ${wheelName} cached (source ${key.slice(0, 12)})`);
    return wheel;
  }
  fs.rmSync(path.join(src, "build"), { recursive: true, force: true });
  fs.rmSync(out, { recursive: true, force: true });
  execFileSync(python, ["-m", "build", "--wheel", "--outdir", out, src], { env: { ...process.env, SOURCE_DATE_EPOCH: epoch }, stdio: ["ignore", "ignore", "pipe"] });
  if (!fs.existsSync(wheel)) {
    throw new Error(`python -m build wrote no ${wheelName} into ${out}`);
  }
  fs.writeFileSync(stamp, key);
  log(`builds: ${wheelName} built (source ${key.slice(0, 12)})`);
  return wheel;
}

export async function makeBuilds({ lane = laneName(), python = process.env.SARCOSTAT_PYTHON || process.env.PYTHON || "python3", log = console.log } = {}) {
  const root = buildsRoot(lane);
  const work = workDir(lane);
  const epoch = sourceDateEpoch();
  fs.mkdirSync(work, { recursive: true });
  const sites = {};
  for (const build of BUILDS) {
    const wheel = wheelFor(build.version, work, python, epoch, log);
    const resume = build.resumeFrom === null ? [] : [sites[build.resumeFrom].source_digest];
    await buildSite({ out: path.join(root, "SarcoStat"), wheel, resume, resumeFile: path.join(work, "no-resume-digests.txt"), sourceDateEpoch: epoch, python, log: () => {} });
    const dir = path.join(root, "SarcoStat", `v${build.version}`);
    const bytes = fs.readFileSync(path.join(dir, "BUILD.json"));
    const info = JSON.parse(bytes.toString("utf8"));
    sites[build.key] = {
      key: build.key,
      version: build.version,
      dir,
      path: `/SarcoStat/v${build.version}/`,
      source_digest: info.source_digest,
      resume_digests: info.resume_digests,
      build_json_sha256: crypto.createHash("sha256").update(bytes).digest("hex"),
    };
    log(`builds: ${sites[build.key].path} source ${info.source_digest.slice(0, 12)} resume ${JSON.stringify(info.resume_digests.map((digest) => digest.slice(0, 12)))}`);
  }
  return { root, sites };
}

if (process.argv[1] !== undefined && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  makeBuilds().then(
    ({ root }) => console.log(`builds: done ${root}`),
    (error) => {
      console.error(error instanceof Error ? error.message : String(error));
      process.exit(1);
    },
  );
}
