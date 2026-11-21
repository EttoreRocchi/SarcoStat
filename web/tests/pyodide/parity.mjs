import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { parseArgs } from "node:util";

import {
  PYTHON_ENVIRONMENT,
  WEB_DIR,
  installOfflineGuard,
  installWheel,
  loadRuntime,
  offlineRefusals,
  sha256,
} from "./pyodide_runtime.mjs";

const USAGE = [
  "Usage: node tests/pyodide/parity.mjs --wheel <sarcostat wheel> --golden <dir> --rule <tolerance.json> [--offline]",
  "       [--out <dir>] [--via analysis|bridge] [--subset <dir>] [--list <sha256 file>]",
  "",
  "Recomputes every golden of <dir>/index.json inside Pyodide under Node with the given wheel and compares the results with <name>.expected.json under the rule file.",
  "A number a matches the expected b when |a - b| <= rel * max(1, |b|), rel = rel_special for paths listed in special, rel_default otherwise; keys, list lengths, strings, booleans and nulls must be equal.",
  "--via analysis (default) calls sarcostat.analysis and sarcostat.export.results_json; --via bridge sends the worker's JSON requests to sarcostat.engine.bridge.dispatch: doc.load_v1 with the golden's text and image, doc.export results_json, and doc.estimate_click_error where index.json asks for it.",
  "Writes parity.json (largest scaled difference per field path, per golden, problems) into --out (default web/test-results/parity, or web/test-results/parity-bridge with --via bridge).",
  "--offline replaces fetch and blocks sockets, DNS, WebSocket and child processes before Pyodide loads; any attempt fails the run.",
].join("\n");

const RULE_KEYS = ["approved", "rel_default", "rel_special", "rule", "special"];
const PATH_RE = /^[A-Za-z_][A-Za-z0-9_]*(\[\*\])*(\.[A-Za-z_][A-Za-z0-9_]*(\[\*\])*)*$/;
const PACKAGES = ["micropip", "numpy", "scipy"];
const WORKDIR = "/parity";

const PYTHON_ANALYSIS = `
import json

from sarcostat.analysis import analyze_file, estimate_click_error
from sarcostat.export import results_json
from sarcostat.io.annotations import read_annotations


def sarcostat_parity_run(path, checks):
    out = {"results_json": results_json(analyze_file(path))}
    if "estimate_click_error" in checks:
        doc = read_annotations(path)
        est = estimate_click_error(doc, doc["images"][0]["image_id"])
        out["estimate_click_error"] = {
            "click_sigma_z": est.click_sigma_z,
            "click_sigma_m": est.click_sigma_m,
            "click_perp_ratio": est.click_perp_ratio,
        }
    return json.dumps(out)
`;

const PYTHON_BRIDGE = `
from sarcostat.engine.bridge import dispatch


def sarcostat_parity_dispatch(request):
    text, buffers = dispatch(request)
    if buffers:
        raise ValueError("the bridge answered with buffers")
    return text
`;

const CLICK_ERROR_KEYS = ["click_sigma_z", "click_sigma_m", "click_perp_ratio"];
const IMAGE_KEYS = ["file_name", "sha256", "width_px", "height_px"];

function bridgeCaller(dispatch) {
  let id = 0;
  return function call(cmd, args) {
    id += 1;
    const reply = JSON.parse(dispatch(JSON.stringify({ v: 1, id, cmd, args })));
    if (reply.v !== 1 || reply.id !== id) {
      throw new Error(`${cmd}: the reply has v ${reply.v} and id ${reply.id}, expected 1 and ${id}`);
    }
    if (!reply.ok) {
      throw new Error(`${cmd}: ${reply.error?.code}: ${reply.error?.message}`);
    }
    return reply.result;
  };
}

function expectNotice(state, cmd, code) {
  if (state.notice?.code !== code) {
    throw new Error(`${cmd}: notice ${JSON.stringify(state.notice)}, expected ${code}`);
  }
}

const RUNNERS = {
  analysis: {
    setup(py) {
      py.runPython(PYTHON_ANALYSIS);
      return py.globals.get("sarcostat_parity_run");
    },
    run(handle, { fsPath, checks }) {
      return JSON.parse(handle(fsPath, checks));
    },
  },
  bridge: {
    setup(py) {
      py.runPython(PYTHON_BRIDGE);
      const call = bridgeCaller(py.globals.get("sarcostat_parity_dispatch"));
      const identity = call("sys.identity", {});
      if (identity.bridge_version !== 1) {
        throw new Error(`sys.identity: bridge_version ${identity.bridge_version}, expected 1`);
      }
      console.log(`bridge: sys.identity sarcostat ${identity.sarcostat_version}, bridge_version ${identity.bridge_version}, source_digest ${identity.source_digest}`);
      return { call, identity };
    },
    run(handle, { text, checks }) {
      const doc = JSON.parse(text);
      if (!Array.isArray(doc.images) || doc.images.length !== 1) {
        throw new Error("the bridge opens annotation files of one image only");
      }
      const image = Object.fromEntries(IMAGE_KEYS.map((key) => [key, doc.images[0][key]]));
      const loaded = handle.call("doc.load_v1", { text, image, confirm: false });
      expectNotice(loaded, "doc.load_v1", "document_loaded");
      const exported = handle.call("doc.export", { what: "results_json" });
      const out = { results_json: exported.text };
      if (checks.includes("estimate_click_error")) {
        const state = handle.call("doc.estimate_click_error", {});
        expectNotice(state, "doc.estimate_click_error", "click_error_estimated");
        out.estimate_click_error = Object.fromEntries(CLICK_ERROR_KEYS.map((key) => [key, state.settings[key]]));
      }
      return out;
    },
  },
};

function loadRule(file) {
  const rule = JSON.parse(fs.readFileSync(file, "utf8"));
  const keys = Object.keys(rule).sort();
  if (JSON.stringify(keys) !== JSON.stringify(RULE_KEYS)) {
    throw new Error(`${path.basename(file)}: keys must be exactly ${RULE_KEYS.join(", ")}`);
  }
  for (const key of ["rel_default", "rel_special"]) {
    if (typeof rule[key] !== "number" || !(rule[key] >= 0)) {
      throw new Error(`${path.basename(file)}: ${key} must be a non-negative number`);
    }
  }
  if (!Array.isArray(rule.special) || !rule.special.every((p) => typeof p === "string" && PATH_RE.test(p))) {
    throw new Error(`${path.basename(file)}: special must be a list of paths such as a.b[*].c`);
  }
  if (new Set(rule.special).size !== rule.special.length) {
    throw new Error(`${path.basename(file)}: special repeats a path`);
  }
  if (!(rule.approved === null || typeof rule.approved === "string") || typeof rule.rule !== "string") {
    throw new Error(`${path.basename(file)}: approved must be a DECISIONS id or null and rule a string`);
  }
  return { ...rule, specialSet: new Set(rule.special) };
}

function kind(value) {
  if (value === null) {
    return "null";
  }
  if (Array.isArray(value)) {
    return "array";
  }
  return typeof value;
}

function compare(expected, actual, rule, fields, problems, label) {
  function walk(e, a, keyPath, where) {
    const ek = kind(e);
    const ak = kind(a);
    if (ek === "object") {
      if (ak !== "object") {
        problems.push(`${label}: ${where}: expected an object, got ${ak}`);
        return;
      }
      const ekeys = Object.keys(e).sort();
      const akeys = Object.keys(a).sort();
      if (JSON.stringify(ekeys) !== JSON.stringify(akeys)) {
        problems.push(`${label}: ${where}: keys differ`);
        return;
      }
      for (const key of ekeys) {
        walk(e[key], a[key], keyPath ? `${keyPath}.${key}` : key, `${where}.${key}`);
      }
    } else if (ek === "array") {
      if (ak !== "array" || a.length !== e.length) {
        problems.push(`${label}: ${where}: list lengths differ`);
        return;
      }
      e.forEach((item, k) => walk(item, a[k], `${keyPath}[*]`, `${where}[${k}]`));
    } else if (ek === "number") {
      if (ak !== "number") {
        problems.push(`${label}: ${where}: expected the number ${e}, got ${JSON.stringify(a)}`);
        return;
      }
      const rel = rule.specialSet.has(keyPath) ? rule.rel_special : rule.rel_default;
      const diff = Math.abs(a - e);
      const scaled = diff / Math.max(1, Math.abs(e));
      const stats = fields.get(keyPath) ?? { rel, n: 0, max_abs: 0, max_scaled: 0, worst: null };
      stats.n += 1;
      stats.max_abs = Math.max(stats.max_abs, diff);
      if (scaled > stats.max_scaled) {
        stats.max_scaled = scaled;
        stats.worst = `${label}: ${where}`;
      }
      fields.set(keyPath, stats);
      if (!(scaled <= rel)) {
        problems.push(`${label}: ${where}: ${a} vs expected ${e} (scaled ${scaled.toExponential(3)} > ${rel})`);
      }
    } else if (e !== a) {
      problems.push(`${label}: ${where}: ${JSON.stringify(a)} vs expected ${JSON.stringify(e)}`);
    }
  }
  walk(expected, actual, "", "$");
}

function readIndex(goldenDir) {
  const index = JSON.parse(fs.readFileSync(path.join(goldenDir, "index.json"), "utf8"));
  if (index.format !== "sarcostat-golden-index" || !Array.isArray(index.goldens)) {
    throw new Error("index.json is not a sarcostat golden index");
  }
  const names = index.goldens.map((entry) => entry.name);
  const expectedFiles = new Set(["index.json", "tolerance.json"]);
  names.forEach((name) => {
    expectedFiles.add(`${name}.json`);
    expectedFiles.add(`${name}.expected.json`);
  });
  const extra = fs.readdirSync(goldenDir).filter((name) => !expectedFiles.has(name));
  if (extra.length > 0) {
    throw new Error(`files not listed in index.json: ${extra.join(", ")}`);
  }
  return index.goldens;
}

async function main() {
  const { values } = parseArgs({
    options: {
      wheel: { type: "string" },
      golden: { type: "string" },
      rule: { type: "string" },
      offline: { type: "boolean", default: false },
      out: { type: "string" },
      via: { type: "string", default: "analysis" },
      subset: { type: "string" },
      list: { type: "string" },
      help: { type: "boolean", default: false },
    },
    strict: true,
  });
  if (values.help) {
    console.log(USAGE);
    return 0;
  }
  if (!values.wheel || !values.golden || !values.rule) {
    console.error(`parity: --wheel, --golden and --rule are required\n${USAGE}`);
    return 2;
  }
  const runner = RUNNERS[values.via];
  if (!runner) {
    console.error(`parity: --via ${values.via} is not available; choose from ${Object.keys(RUNNERS).join(", ")}`);
    return 2;
  }
  console.log(`node ${process.version}`);
  if (!process.version.startsWith("v24.")) {
    console.error(`parity: Node 24 is required (web/.nvmrc), this is ${process.version}`);
    return 2;
  }
  if (values.offline) {
    installOfflineGuard();
    console.log("network: offline guard on (fetch refuses every non-file: URL; sockets, DNS, WebSocket and child processes refused)");
  } else {
    console.log("network: offline guard OFF (pass --offline for V-PARITY)");
  }

  const goldenDir = path.resolve(values.golden);
  const ruleFile = path.resolve(values.rule);
  if (!fs.existsSync(ruleFile)) {
    console.error(`parity: ${ruleFile} does not exist (tolerance.json needs the USER's approval, plan 3.13)`);
    return 2;
  }
  const rule = loadRule(ruleFile);
  const ruleSha = sha256(fs.readFileSync(ruleFile));
  const goldens = readIndex(goldenDir);
  const outDir = path.resolve(values.out ?? path.join(WEB_DIR, "test-results", values.via === "analysis" ? "parity" : `parity-${values.via}`));
  console.log(`rule: ${path.basename(ruleFile)} sha256 ${ruleSha}; rel_default ${rule.rel_default}, rel_special ${rule.rel_special}, ${rule.special.length} special path(s), approved ${rule.approved}`);
  console.log(`goldens: ${goldens.length} in ${path.basename(goldenDir)}`);

  const started = performance.now();
  const { py, pyodideVersion } = await loadRuntime({ subset: values.subset, list: values.list, packages: PACKAGES });
  const wheel = await installWheel(py, path.resolve(values.wheel));
  const environment = JSON.parse(py.runPython(PYTHON_ENVIRONMENT));
  console.log(`pyodide ${pyodideVersion}: Python ${environment.python}, numpy ${environment.numpy}, scipy ${environment.scipy}; sarcostat ${environment.sarcostat}`);
  console.log(`wheel sha256: ${wheel.sha256}  ${wheel.name}`);
  const handle = runner.setup(py);

  const fields = new Map();
  const problems = [];
  const perGolden = [];
  py.FS.mkdirTree(WORKDIR);
  for (const entry of goldens) {
    const inputName = `${entry.name}.json`;
    const fsPath = `${WORKDIR}/${inputName}`;
    const inputBytes = fs.readFileSync(path.join(goldenDir, inputName));
    py.FS.writeFile(fsPath, inputBytes);
    const expectedText = fs.readFileSync(path.join(goldenDir, `${entry.name}.expected.json`), "utf8");
    const before = problems.length;
    const goldenFields = new Map();
    const t0 = performance.now();
    const output = runner.run(handle, { fsPath, text: inputBytes.toString("utf8"), checks: entry.checks ?? [] });
    const seconds = (performance.now() - t0) / 1000;
    compare(JSON.parse(expectedText), JSON.parse(output.results_json), rule, goldenFields, problems, entry.name);
    if ((entry.checks ?? []).includes("estimate_click_error")) {
      const doc = JSON.parse(fs.readFileSync(path.join(goldenDir, inputName), "utf8"));
      const expected = Object.fromEntries(Object.keys(output.estimate_click_error).map((key) => [key, doc.gui[key]]));
      compare({ estimate_click_error: expected }, { estimate_click_error: output.estimate_click_error }, rule, goldenFields, problems, entry.name);
    }
    let worst = 0;
    for (const [key, stats] of goldenFields) {
      worst = Math.max(worst, stats.max_scaled);
      const total = fields.get(key) ?? { rel: stats.rel, n: 0, max_abs: 0, max_scaled: 0, worst: null };
      total.n += stats.n;
      total.max_abs = Math.max(total.max_abs, stats.max_abs);
      if (stats.max_scaled >= total.max_scaled) {
        total.max_scaled = stats.max_scaled;
        total.worst = stats.worst;
      }
      fields.set(key, total);
    }
    const numbers = [...goldenFields.values()].reduce((sum, stats) => sum + stats.n, 0);
    perGolden.push({
      name: entry.name,
      ok: problems.length === before,
      numbers,
      max_scaled: worst,
      byte_equal_to_expected: output.results_json === expectedText,
      seconds: Number(seconds.toFixed(3)),
    });
    console.log(`${problems.length === before ? "ok  " : "FAIL"} ${entry.name}: ${numbers} numbers, max scaled difference ${worst.toExponential(3)}, ${seconds.toFixed(2)} s`);
  }

  const refusals = offlineRefusals();
  const rows = [...fields.entries()].sort((a, b) => b[1].max_scaled - a[1].max_scaled || a[0].localeCompare(b[0]));
  const ok = problems.length === 0 && refusals.length === 0 && goldens.length > 0;
  const report = {
    tool: "parity.mjs",
    via: values.via,
    result: ok ? "PASS" : "FAIL",
    node: process.version,
    pyodide: pyodideVersion,
    python: environment.python,
    numpy: environment.numpy,
    scipy: environment.scipy,
    sarcostat: environment.sarcostat,
    wheel,
    ...(values.via === "bridge" ? { bridge_identity: handle.identity } : {}),
    rule: {
      file: path.basename(ruleFile),
      sha256: ruleSha,
      rel_default: rule.rel_default,
      rel_special: rule.rel_special,
      special: rule.special,
      approved: rule.approved,
    },
    goldens: perGolden,
    fields: Object.fromEntries(rows),
    rounding_boundary_cases: [],
    problems,
    network_refusals: refusals,
    seconds: Number(((performance.now() - started) / 1000).toFixed(1)),
  };
  await fsp.mkdir(outDir, { recursive: true });
  await fsp.writeFile(path.join(outDir, "parity.json"), `${JSON.stringify(report, null, 2)}\n`);

  const lines = ["", "=== SarcoStat parity summary ==="];
  lines.push(`node ${process.version}; Pyodide ${pyodideVersion}; Python ${environment.python}; numpy ${environment.numpy}; scipy ${environment.scipy}; via ${values.via}`);
  lines.push(`wheel sha256: ${wheel.sha256}  ${wheel.name}`);
  lines.push(`rule sha256: ${ruleSha}  ${path.basename(ruleFile)}`);
  lines.push(`goldens: ${perGolden.filter((g) => g.ok).length}/${perGolden.length} ok; byte-equal to the CPython expected text: ${perGolden.filter((g) => g.byte_equal_to_expected).length}/${perGolden.length}`);
  lines.push("largest scaled differences |a-b|/max(1,|b|) (field path, allowed, n):");
  rows.slice(0, 15).forEach(([key, stats]) => lines.push(`  ${stats.max_scaled.toExponential(3)}  ${String(stats.rel).padEnd(6)} ${String(stats.n).padStart(5)}  ${key}`));
  lines.push(`network attempts refused: ${refusals.length}`);
  problems.slice(0, 40).forEach((line) => lines.push(`  ${line}`));
  lines.push(`parity.json: ${path.join(outDir, "parity.json")}`);
  lines.push(ok ? "RESULT: PASS" : `RESULT: FAIL (${problems.length} problem(s), ${refusals.length} refused network attempt(s))`);
  console.log(lines.join("\n"));
  return ok ? 0 : 1;
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
