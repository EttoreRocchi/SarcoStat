import { defineConfig } from "vite";

import { WEB_DIR, laneCacheDir } from "./scripts/lane.mjs";

export default defineConfig({
  root: WEB_DIR,
  base: "./",
  cacheDir: laneCacheDir("vite"),
  publicDir: false,
  clearScreen: false,
  build: {
    outDir: "dist/app",
    emptyOutDir: true,
    target: "es2023",
    sourcemap: false,
    assetsInlineLimit: 0,
    modulePreload: { polyfill: false },
    reportCompressedSize: false,
  },
  worker: {
    format: "es",
  },
  server: {
    host: "127.0.0.1",
    strictPort: false,
  },
  preview: {
    host: "127.0.0.1",
  },
});
