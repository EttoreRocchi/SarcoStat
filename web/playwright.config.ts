import path from "node:path";

import { defineConfig, type PlaywrightTestConfig } from "@playwright/test";

import { WEB_DIR, laneName } from "./scripts/lane.mjs";

export const HOST_RESOLVER_RULES = "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1";
export const CROSS_BROWSER_SPECS = /(pixel_centre|persistence)\.spec\.ts$/;

type Env = Record<string, string | undefined>;

export function outputRoot(env: Env): string {
  const out = env.PW_OUT && env.PW_OUT !== "" ? env.PW_OUT : `test-results/${laneName(env)}`;
  return path.resolve(WEB_DIR, out);
}

export function makeConfig(env: Env): PlaywrightTestConfig {
  const out = outputRoot(env);
  const projects: NonNullable<PlaywrightTestConfig["projects"]> = [
    {
      name: "chromium",
      testIgnore: /screenshots\.spec\.ts$/,
      use: { browserName: "chromium" },
    },
    {
      name: "shots",
      testMatch: /screenshots\.spec\.ts$/,
      use: { browserName: "chromium" },
    },
  ];
  if (env.CI) {
    projects.push(
      { name: "firefox", testMatch: CROSS_BROWSER_SPECS, use: { browserName: "firefox" } },
      { name: "webkit", testMatch: CROSS_BROWSER_SPECS, use: { browserName: "webkit" } },
    );
  }
  return {
    testDir: path.join(WEB_DIR, "tests", "e2e"),
    outputDir: path.join(out, "artifacts"),
    globalSetup: path.join(WEB_DIR, "tests", "e2e", "global_setup.ts"),
    fullyParallel: false,
    forbidOnly: true,
    retries: 0,
    timeout: 60_000,
    reporter: [["list"], ["html", { open: "never", outputFolder: path.join(out, "report") }]],
    use: {
      headless: true,
      baseURL: env.SARCOSTAT_APP_URL,
      viewport: { width: 1440, height: 900 },
      deviceScaleFactor: 1,
      serviceWorkers: "block",
      trace: "retain-on-failure",
      launchOptions: { args: [HOST_RESOLVER_RULES] },
    },
    projects,
  };
}

export default defineConfig(makeConfig(process.env));
