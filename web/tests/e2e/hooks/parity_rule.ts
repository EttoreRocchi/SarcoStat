export const HOOK_SYMBOLS = [] as const;

export const RULE_KEYS = ["approved", "rel_default", "rel_special", "rule", "special"] as const;
export const PATH_RE = /^[A-Za-z_][A-Za-z0-9_]*(\[\*\])*(\.[A-Za-z_][A-Za-z0-9_]*(\[\*\])*)*$/;
export const CLICK_ERROR_KEYS = ["click_sigma_z", "click_sigma_m", "click_perp_ratio"] as const;
export const IMAGE_KEYS = ["file_name", "sha256", "width_px", "height_px"] as const;

export interface ToleranceRule {
  approved: string | null;
  rel_default: number;
  rel_special: number;
  rule: string;
  special: string[];
  specialSet: Set<string>;
}

export interface FieldStats {
  rel: number;
  n: number;
  max_abs: number;
  max_scaled: number;
  worst: string | null;
}

export interface GoldenEntry {
  name: string;
  checks: string[];
  description: string;
  image: string;
}

type Json = null | boolean | number | string | Json[] | { [key: string]: Json };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseRule(text: string, label = "tolerance.json"): ToleranceRule {
  const raw: unknown = JSON.parse(text);
  if (!isRecord(raw)) {
    throw new Error(`${label}: not a JSON object`);
  }
  const keys = Object.keys(raw).sort();
  if (JSON.stringify(keys) !== JSON.stringify(RULE_KEYS)) {
    throw new Error(`${label}: keys must be exactly ${RULE_KEYS.join(", ")}`);
  }
  for (const key of ["rel_default", "rel_special"] as const) {
    const value = raw[key];
    if (typeof value !== "number" || !(value >= 0)) {
      throw new Error(`${label}: ${key} must be a non-negative number`);
    }
  }
  const special = raw.special;
  if (!Array.isArray(special) || !special.every((entry) => typeof entry === "string" && PATH_RE.test(entry))) {
    throw new Error(`${label}: special must be a list of paths such as a.b[*].c`);
  }
  if (new Set(special).size !== special.length) {
    throw new Error(`${label}: special repeats a path`);
  }
  if (!(raw.approved === null || typeof raw.approved === "string") || typeof raw.rule !== "string") {
    throw new Error(`${label}: approved must be a DECISIONS id or null and rule a string`);
  }
  return {
    approved: raw.approved,
    rel_default: raw.rel_default as number,
    rel_special: raw.rel_special as number,
    rule: raw.rule,
    special: special as string[],
    specialSet: new Set(special as string[]),
  };
}

export function parseGoldenIndex(text: string, folderFiles: readonly string[]): GoldenEntry[] {
  const index: unknown = JSON.parse(text);
  if (!isRecord(index) || index.format !== "sarcostat-golden-index" || !Array.isArray(index.goldens)) {
    throw new Error("index.json is not a sarcostat golden index");
  }
  const entries: GoldenEntry[] = index.goldens.map((entry: unknown, position: number) => {
    if (!isRecord(entry) || typeof entry.name !== "string" || entry.name === "") {
      throw new Error(`index.json: golden ${position} has no name`);
    }
    const checks = entry.checks ?? [];
    if (!Array.isArray(checks) || !checks.every((check) => typeof check === "string")) {
      throw new Error(`index.json: ${entry.name}: checks must be a list of strings`);
    }
    return {
      name: entry.name,
      checks: checks as string[],
      description: typeof entry.description === "string" ? entry.description : "",
      image: typeof entry.image === "string" ? entry.image : "",
    };
  });
  const expected = new Set(["index.json", "tolerance.json"]);
  for (const entry of entries) {
    expected.add(`${entry.name}.json`);
    expected.add(`${entry.name}.expected.json`);
  }
  const extra = folderFiles.filter((name) => !expected.has(name));
  if (extra.length > 0) {
    throw new Error(`files not listed in index.json: ${extra.join(", ")}`);
  }
  const missing = [...expected].filter((name) => !folderFiles.includes(name));
  if (missing.length > 0) {
    throw new Error(`files named by index.json are missing: ${missing.join(", ")}`);
  }
  return entries;
}

function kind(value: unknown): string {
  if (value === null) {
    return "null";
  }
  if (Array.isArray(value)) {
    return "array";
  }
  return typeof value;
}

export function compareJson(
  expected: unknown,
  actual: unknown,
  rule: ToleranceRule,
  fields: Map<string, FieldStats>,
  problems: string[],
  label: string,
): void {
  const walk = (e: unknown, a: unknown, keyPath: string, where: string): void => {
    const ek = kind(e);
    const ak = kind(a);
    if (ek === "object") {
      if (ak !== "object") {
        problems.push(`${label}: ${where}: expected an object, got ${ak}`);
        return;
      }
      const eo = e as Record<string, Json>;
      const ao = a as Record<string, Json>;
      const ekeys = Object.keys(eo).sort();
      const akeys = Object.keys(ao).sort();
      if (JSON.stringify(ekeys) !== JSON.stringify(akeys)) {
        problems.push(`${label}: ${where}: keys differ`);
        return;
      }
      for (const key of ekeys) {
        walk(eo[key], ao[key], keyPath ? `${keyPath}.${key}` : key, `${where}.${key}`);
      }
    } else if (ek === "array") {
      const ea = e as Json[];
      if (ak !== "array" || (a as Json[]).length !== ea.length) {
        problems.push(`${label}: ${where}: list lengths differ`);
        return;
      }
      ea.forEach((item, k) => walk(item, (a as Json[])[k], `${keyPath}[*]`, `${where}[${k}]`));
    } else if (ek === "number") {
      if (ak !== "number") {
        problems.push(`${label}: ${where}: expected the number ${String(e)}, got ${JSON.stringify(a)}`);
        return;
      }
      const en = e as number;
      const an = a as number;
      const rel = rule.specialSet.has(keyPath) ? rule.rel_special : rule.rel_default;
      const diff = Math.abs(an - en);
      const scaled = diff / Math.max(1, Math.abs(en));
      const stats = fields.get(keyPath) ?? { rel, n: 0, max_abs: 0, max_scaled: 0, worst: null };
      stats.n += 1;
      stats.max_abs = Math.max(stats.max_abs, diff);
      if (scaled > stats.max_scaled) {
        stats.max_scaled = scaled;
        stats.worst = `${label}: ${where}`;
      }
      fields.set(keyPath, stats);
      if (!(scaled <= rel)) {
        problems.push(`${label}: ${where}: ${an} vs expected ${en} (scaled ${scaled.toExponential(3)} > ${rel})`);
      }
    } else if (e !== a) {
      problems.push(`${label}: ${where}: ${JSON.stringify(a)} vs expected ${JSON.stringify(e)}`);
    }
  };
  walk(expected, actual, "", "$");
}

export function mergeFields(total: Map<string, FieldStats>, golden: Map<string, FieldStats>): number {
  let worst = 0;
  for (const [key, stats] of golden) {
    worst = Math.max(worst, stats.max_scaled);
    const sum = total.get(key) ?? { rel: stats.rel, n: 0, max_abs: 0, max_scaled: 0, worst: null };
    sum.n += stats.n;
    sum.max_abs = Math.max(sum.max_abs, stats.max_abs);
    if (stats.max_scaled >= sum.max_scaled) {
      sum.max_scaled = stats.max_scaled;
      sum.worst = stats.worst;
    }
    total.set(key, sum);
  }
  return worst;
}

export function rankFields(fields: Map<string, FieldStats>): [string, FieldStats][] {
  return [...fields.entries()].sort((a, b) => b[1].max_scaled - a[1].max_scaled || a[0].localeCompare(b[0]));
}
