import path from "node:path";
import { parseArgs } from "node:util";

import { build } from "vite";

import { WEB_DIR, insideDir, laneName } from "./lane.mjs";

const USAGE = [
  "Usage: npm run build -- --out dist/<lane>",
  "",
  "Builds the web app with Vite into <out>/app (inside web/dist/), with the Vite cache in web/.cache/vite/<LANE>.",
].join("\n");

const { values } = parseArgs({
  options: { out: { type: "string" }, help: { type: "boolean", default: false } },
  strict: true,
});

if (values.help) {
  console.log(USAGE);
  process.exit(0);
}
if (!values.out) {
  console.error(USAGE);
  process.exit(2);
}

const outRoot = path.resolve(WEB_DIR, values.out);
const distDir = path.join(WEB_DIR, "dist");
if (!insideDir(distDir, outRoot)) {
  console.error(`build: --out must be a folder inside ${distDir}, got ${outRoot}`);
  process.exit(2);
}
const outDir = path.join(outRoot, "app");

console.log(`build: lane ${laneName()}, node ${process.version}, out ${path.relative(WEB_DIR, outDir)}`);
await build({
  root: WEB_DIR,
  configFile: path.join(WEB_DIR, "vite.config.ts"),
  configLoader: "runner",
  logLevel: "warn",
  build: { outDir, emptyOutDir: true },
});
console.log(`build: done ${path.relative(WEB_DIR, outDir)}`);
