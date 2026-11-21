import path from "node:path";

import { defineConfig } from "vitest/config";

import { WEB_DIR, laneCacheDir } from "./scripts/lane.mjs";

export default defineConfig({
  root: WEB_DIR,
  cacheDir: laneCacheDir("vitest"),
  publicDir: false,
  clearScreen: false,
  test: {
    include: ["tests/unit/**/*.spec.ts"],
    environment: "node",
    pool: "forks",
    fsModuleCachePath: path.join(laneCacheDir("vitest"), "modules"),
    watch: false,
    reporters: ["default"],
  },
});
