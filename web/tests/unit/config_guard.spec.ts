import fs from "node:fs";
import net from "node:net";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { CROSS_BROWSER_SPECS, HOST_RESOLVER_RULES, makeConfig, outputRoot } from "../../playwright.config.ts";
import { WEB_DIR, laneCacheDir, laneName } from "../../scripts/lane.mjs";
import { HOST, startServer } from "../../scripts/serve.mjs";

const BANNED = ["--" + "headed", "--" + "ui", "--" + "debug", "PW" + "DEBUG", "show" + "-report", "code" + "gen", "headless: " + "false"];

const RUNNERS = [
  path.join(WEB_DIR, "tests", "e2e", "study_runner.mjs"),
  path.resolve(process.env.SARCOSTAT_PAPER_DIR ?? path.join(WEB_DIR, "..", "..", "SarcoStat-paper"), "scripts", "web_study_screenshot.mjs"),
];

function filesUnder(dir: string, exts: string[]): string[] {
  if (!fs.existsSync(dir)) {
    return [];
  }
  const out: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      out.push(...filesUnder(full, exts));
    } else if (exts.includes(path.extname(entry.name))) {
      out.push(full);
    }
  }
  return out;
}

function bannedIn(text: string): string[] {
  return BANNED.filter((token) => text.includes(token));
}

describe("Playwright configuration", () => {
  const local = makeConfig({});

  it("runs headless only, with the HTML report never opened", () => {
    expect(local.use?.headless).toBe(true);
    const reporters = local.reporter as unknown as [string, Record<string, unknown>?][];
    const html = reporters.find(([name]) => name === "html");
    expect(html?.[1]?.open).toBe("never");
    for (const project of local.projects ?? []) {
      expect(project.use?.headless, `project ${project.name}`).not.toBe(false);
    }
  });

  it("maps every host except 127.0.0.1 to nothing", () => {
    expect(HOST_RESOLVER_RULES).toBe("--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1");
    expect(local.use?.launchOptions?.args).toContain(HOST_RESOLVER_RULES);
  });

  it("has Chromium projects only, unless CI is set", () => {
    expect((local.projects ?? []).map((p) => p.use?.browserName)).toEqual(["chromium", "chromium"]);
    const ci = makeConfig({ CI: "true" });
    const extra = (ci.projects ?? []).filter((p) => p.use?.browserName !== "chromium");
    expect(extra.map((p) => p.use?.browserName).sort()).toEqual(["firefox", "webkit"]);
    for (const project of extra) {
      expect(project.testMatch).toBe(CROSS_BROWSER_SPECS);
    }
  });

  it("writes its outputs under PW_OUT or test-results/<lane>", () => {
    expect(outputRoot({ PW_OUT: "test-results/w1" })).toBe(path.join(WEB_DIR, "test-results", "w1"));
    expect(outputRoot({ LANE: "d" })).toBe(path.join(WEB_DIR, "test-results", "d"));
    const config = makeConfig({ PW_OUT: "test-results/w2" });
    expect(config.outputDir).toBe(path.join(WEB_DIR, "test-results", "w2", "artifacts"));
    const reporters = config.reporter as unknown as [string, Record<string, unknown>?][];
    expect(reporters.find(([name]) => name === "html")?.[1]?.outputFolder).toBe(path.join(WEB_DIR, "test-results", "w2", "report"));
  });

  it("keeps the configuration files free of banned flags", () => {
    for (const file of ["playwright.config.ts", "vite.config.ts", "vitest.config.ts"]) {
      expect(bannedIn(fs.readFileSync(path.join(WEB_DIR, file), "utf8")), file).toEqual([]);
    }
  });
});

describe("npm scripts", () => {
  const pkg = JSON.parse(fs.readFileSync(path.join(WEB_DIR, "package.json"), "utf8")) as { scripts: Record<string, string> };

  it("never use a headed, UI, debug, report or codegen mode", () => {
    for (const [name, command] of Object.entries(pkg.scripts)) {
      expect(bannedIn(command), name).toEqual([]);
    }
  });

  it("run Playwright only without a display and with the HTML report closed", () => {
    const playwright = Object.entries(pkg.scripts).filter(([, command]) => /\bplaywright\b/.test(command));
    expect(playwright.map(([name]) => name).sort()).toEqual(["test:e2e", "test:shots"]);
    for (const [name, command] of playwright) {
      expect(command, name).toMatch(/^env -u DISPLAY -u WAYLAND_DISPLAY PLAYWRIGHT_HTML_OPEN=never PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 playwright test /);
    }
  });

  it("type-check without incremental build files", () => {
    expect(pkg.scripts.typecheck).toMatch(/^tsc --noEmit/);
    const tsconfig = fs.readFileSync(path.join(WEB_DIR, "tsconfig.json"), "utf8");
    expect(tsconfig).not.toMatch(/incremental|composite|tsBuildInfoFile/);
  });
});

describe("test files and stand-alone runners", () => {
  it("never ask for a headed browser", () => {
    const files = [...filesUnder(path.join(WEB_DIR, "tests", "e2e"), [".ts", ".mjs", ".js"]), ...filesUnder(path.join(WEB_DIR, "scripts"), [".mjs", ".js", ".sh"])];
    expect(files.length).toBeGreaterThan(0);
    for (const file of files) {
      expect(bannedIn(fs.readFileSync(file, "utf8")), path.relative(WEB_DIR, file)).toEqual([]);
    }
  });

  for (const runner of RUNNERS) {
    it.skipIf(!fs.existsSync(runner))(`${path.basename(runner)} is headless and blocks other hosts`, () => {
      const text = fs.readFileSync(runner, "utf8");
      expect(text).toMatch(/headless:\s*true/);
      expect(text).toContain("MAP * ~NOTFOUND, EXCLUDE 127.0.0.1");
      expect(bannedIn(text)).toEqual([]);
    });
  }
});

describe("lanes", () => {
  it("give each lane its own Vite and Vitest cache", () => {
    expect(laneName({})).toBe("lead");
    expect(laneCacheDir("vite", { LANE: "w1" })).toBe(path.join(WEB_DIR, ".cache", "vite", "w1"));
    expect(laneCacheDir("vitest", { LANE: "w2" })).toBe(path.join(WEB_DIR, ".cache", "vitest", "w2"));
    expect(() => laneName({ LANE: "../x" })).toThrow();
  });
});

describe("serve.mjs", () => {
  it("binds 127.0.0.1 only and picks a free port for PORT=0", async () => {
    expect(HOST).toBe("127.0.0.1");
    const source = fs.readFileSync(path.join(WEB_DIR, "scripts", "serve.mjs"), "utf8");
    expect(source).not.toMatch(/0\.0\.0\.0|"::"|'::'|localhost/);
    const a = await startServer({ root: WEB_DIR, port: 0 });
    const b = await startServer({ root: WEB_DIR, port: 0 });
    try {
      expect(a.host).toBe("127.0.0.1");
      expect(b.host).toBe("127.0.0.1");
      expect(a.port).toBeGreaterThan(0);
      expect(a.port).not.toBe(b.port);
      expect(net.isIPv4(a.host)).toBe(true);
      const ok = await fetch(`${a.url}package.json`);
      expect(ok.status).toBe(200);
      expect(ok.headers.get("content-type")).toBe("application/json; charset=utf-8");
      const outside = await fetch(`${a.url}..%2f..%2fpyproject.toml`);
      expect([400, 404]).toContain(outside.status);
    } finally {
      await new Promise<void>((resolve) => a.server.close(() => resolve()));
      await new Promise<void>((resolve) => b.server.close(() => resolve()));
    }
  });
});
