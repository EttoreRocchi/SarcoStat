import path from "node:path";
import { fileURLToPath } from "node:url";

export const WEB_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const LANE_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$/;

export function laneName(env = process.env) {
  const lane = env.LANE && env.LANE !== "" ? env.LANE : "lead";
  if (!LANE_RE.test(lane)) {
    throw new Error(`LANE must match ${LANE_RE}, got ${JSON.stringify(lane)}`);
  }
  return lane;
}

export function laneCacheDir(tool, env = process.env) {
  return path.join(WEB_DIR, ".cache", tool, laneName(env));
}

export function insideDir(parent, child) {
  const rel = path.relative(parent, child);
  return rel !== "" && !rel.startsWith("..") && !path.isAbsolute(rel);
}
