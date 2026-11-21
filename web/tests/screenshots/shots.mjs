import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

import { WEB_DIR, insideDir, laneName } from "../../scripts/lane.mjs";
import { comparePngBuffers } from "./png.mjs";

export const SHOTS_DIR = path.join(WEB_DIR, "tests", "screenshots");
export const DEFAULT_SET = "m3";
export const SET_NAME = /^[a-z0-9][a-z0-9._-]{0,39}$/;

const USAGE = [
  "Usage: node tests/screenshots/shots.mjs compare <run A folder> <run B folder>",
  "       node tests/screenshots/shots.mjs twice --out <empty or new folder> [-- <playwright arguments>]",
  "",
  "compare: every PNG in either folder must exist in both and have byte-identical content or zero differing pixels; exit 0 when so, 1 otherwise.",
  "twice: runs npm run test:shots two times in candidate mode (SARCOSTAT_SHOTS_CANDIDATE=<out>/run1, then <out>/run2; PW_OUT=<PW_OUT or test-results/<lane>>/shots-run1 and shots-run2), then compares the two folders (plan M3-6 acceptance 1).",
  "Neither command writes under web/tests/screenshots/: an approved set is copied there by the lead after the USER's approval.",
].join("\n");

export function refuseInsideShots(dir) {
  const abs = path.resolve(dir);
  if (abs === SHOTS_DIR || insideDir(SHOTS_DIR, abs)) {
    throw new Error(`${abs} is inside web/tests/screenshots/, where only the lead copies a set the USER approved; write candidates under test-results/<lane>/ or a scratch folder`);
  }
  return abs;
}

export function shotsSettings(env = process.env) {
  const set = env.SARCOSTAT_SHOTS_SET && env.SARCOSTAT_SHOTS_SET !== "" ? env.SARCOSTAT_SHOTS_SET : DEFAULT_SET;
  if (!SET_NAME.test(set)) {
    throw new Error(`SARCOSTAT_SHOTS_SET must match ${SET_NAME}, got ${JSON.stringify(set)}`);
  }
  const candidate = env.SARCOSTAT_SHOTS_CANDIDATE && env.SARCOSTAT_SHOTS_CANDIDATE !== "" ? refuseInsideShots(env.SARCOSTAT_SHOTS_CANDIDATE) : null;
  const ci = Boolean(env.CI);
  return {
    set,
    setDir: path.join(SHOTS_DIR, set),
    candidate,
    ci,
    compare: !ci && candidate === null,
    require: env.SARCOSTAT_SHOTS_REQUIRE === "1",
  };
}

function pngNames(dir) {
  return fs.existsSync(dir) ? fs.readdirSync(dir).filter((name) => name.endsWith(".png")) : [];
}

export function compareFolders(dirA, dirB) {
  const names = [...new Set([...pngNames(dirA), ...pngNames(dirB)])].sort();
  const rows = names.map((name) => {
    const fileA = path.join(dirA, name);
    const fileB = path.join(dirB, name);
    if (!fs.existsSync(fileA)) {
      return { name, ok: false, differing: null, status: `missing in ${dirA}` };
    }
    if (!fs.existsSync(fileB)) {
      return { name, ok: false, differing: null, status: `missing in ${dirB}` };
    }
    const result = comparePngBuffers(fs.readFileSync(fileA), fs.readFileSync(fileB));
    if (result.identicalBytes) {
      return { name, ok: true, differing: 0, status: "identical bytes" };
    }
    if (!result.sameSize) {
      return { name, ok: false, differing: result.differing, status: `size ${result.referenceWidth}x${result.referenceHeight} vs ${result.width}x${result.height}` };
    }
    return {
      name,
      ok: result.differing === 0,
      differing: result.differing,
      status: result.differing === 0 ? "0 pixels differ (the PNG bytes differ)" : `${result.differing} pixels differ`,
    };
  });
  return { ok: rows.length > 0 && rows.every((row) => row.ok), rows };
}

function printComparison(dirA, dirB, comparison) {
  console.log(`shots: comparing ${dirA} with ${dirB}`);
  for (const row of comparison.rows) {
    console.log(`  ${row.ok ? "ok  " : "FAIL"} ${row.name}: ${row.status}`);
  }
  if (comparison.rows.length === 0) {
    console.log("  no PNG files in either folder");
  }
  console.log(comparison.ok ? `shots: PASS ${comparison.rows.length} capture(s) equal` : "shots: FAIL");
}

function runShots(candidateDir, pwOut, extra) {
  const env = {
    ...process.env,
    SARCOSTAT_SHOTS_CANDIDATE: candidateDir,
    PW_OUT: pwOut,
    PLAYWRIGHT_HTML_OPEN: "never",
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD: "1",
  };
  delete env.DISPLAY;
  delete env.WAYLAND_DISPLAY;
  console.log(`shots: npm run test:shots with SARCOSTAT_SHOTS_CANDIDATE=${candidateDir} PW_OUT=${pwOut}`);
  const result = spawnSync("npm", ["run", "test:shots", "--", ...extra], { cwd: WEB_DIR, env, stdio: "inherit" });
  return result.status ?? 1;
}

function main(argv) {
  const { values, positionals } = parseArgs({
    args: argv,
    options: { out: { type: "string" }, help: { type: "boolean", default: false } },
    allowPositionals: true,
    strict: true,
  });
  const [command, ...rest] = positionals;
  if (values.help || command === undefined) {
    console.log(USAGE);
    return values.help ? 0 : 2;
  }
  if (command === "compare") {
    if (rest.length !== 2) {
      console.error(USAGE);
      return 2;
    }
    const [dirA, dirB] = rest.map((dir) => path.resolve(dir));
    const comparison = compareFolders(dirA, dirB);
    printComparison(dirA, dirB, comparison);
    return comparison.ok ? 0 : 1;
  }
  if (command === "twice") {
    if (!values.out) {
      console.error(USAGE);
      return 2;
    }
    const out = refuseInsideShots(values.out);
    if (fs.existsSync(out) && fs.readdirSync(out).length > 0) {
      console.error(`shots: ${out} is not empty; give an empty or new folder`);
      return 2;
    }
    const base = process.env.PW_OUT && process.env.PW_OUT !== "" ? process.env.PW_OUT : `test-results/${laneName()}`;
    const runA = path.join(out, "run1");
    const runB = path.join(out, "run2");
    const first = runShots(runA, `${base}/shots-run1`, rest);
    const second = runShots(runB, `${base}/shots-run2`, rest);
    const comparison = compareFolders(runA, runB);
    printComparison(runA, runB, comparison);
    if (first !== 0 || second !== 0) {
      console.error(`shots: FAIL test:shots exited ${first} and ${second}`);
      return 1;
    }
    return comparison.ok ? 0 : 1;
  }
  console.error(`shots: unknown command ${command}\n${USAGE}`);
  return 2;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = main(process.argv.slice(2));
}
