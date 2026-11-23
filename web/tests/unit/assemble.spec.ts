import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import vm from "node:vm";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import {
  AssembleError,
  assemble,
  canonicalJson,
  compareVersions,
  isPrerelease,
  pickLatest,
  readKnown,
  readZip,
  redirectPage,
  releaseInfo,
  releaseSet,
  verifyZip,
  writeZip,
  zipName,
  zipVersionFolder,
} from "../../scripts/assemble_pages.mjs";
import { WEB_DIR } from "../../scripts/lane.mjs";

const SCRIPT = path.join(WEB_DIR, "scripts", "assemble_pages.mjs");
const EPOCH = 1795278094;

let scratch = "";
let counter = 0;

beforeAll(() => {
  scratch = fs.mkdtempSync(path.join(os.tmpdir(), "sarcostat-assemble-"));
});

afterAll(() => {
  fs.rmSync(scratch, { recursive: true, force: true });
});

function sha(bytes: Buffer | string): string {
  return crypto.createHash("sha256").update(bytes).digest("hex");
}

interface Folder {
  version: string;
  files: Record<string, Buffer>;
  build: Buffer;
  zip: Buffer;
}

function folder(version: string): Folder {
  const files: Record<string, Buffer> = {
    "index.html": Buffer.from(`<!doctype html><title>SarcoStat ${version}</title>\n`),
    "assets/app.js": Buffer.from(`console.log(${JSON.stringify(version)});\n`),
    [`wheels/sarcostat-${version}-py3-none-any.whl`]: Buffer.from(`wheel of ${version}`),
  };
  const record = {
    format: "sarcostat-build",
    format_version: 1,
    version,
    commit: "0123456789abcdef0123456789abcdef01234567",
    source_date_epoch: EPOCH,
    source_digest: sha(`digest ${version}`),
    files: Object.fromEntries(Object.entries(files).map(([rel, data]) => [rel, sha(data)])),
  };
  const build = Buffer.from(canonicalJson(record));
  const entries = [...Object.entries(files), ["BUILD.json", build] as const].map(([rel, data]) => ({ name: `v${version}/${rel}`, data }));
  return { version, files, build, zip: writeZip(entries, { mtime: EPOCH }) };
}

interface ReleaseOptions {
  draft?: boolean;
  immutable?: boolean | undefined;
  apiPrerelease?: boolean;
  zip?: Buffer;
  build?: Buffer;
  web?: boolean;
}

interface Scenario {
  dir: string;
  assets: string;
  live: string;
  out: string;
  pages: unknown[][];
}

function scenario(): Scenario {
  counter += 1;
  const dir = path.join(scratch, `case-${counter}`);
  const result = { dir, assets: path.join(dir, "assets"), live: path.join(dir, "live"), out: path.join(dir, "_site"), pages: [[]] as unknown[][] };
  fs.mkdirSync(result.assets, { recursive: true });
  fs.mkdirSync(result.live, { recursive: true });
  return result;
}

function release(s: Scenario, version: string, options: ReleaseOptions = {}): Folder {
  const made = folder(version);
  const zip = options.zip ?? made.zip;
  const build = options.build ?? made.build;
  const assets: Record<string, unknown>[] = [{ name: `sarcostat-${version}-py3-none-any.whl`, url: "https://example.invalid/wheel" }];
  if (options.web !== false) {
    fs.mkdirSync(path.join(s.assets, version), { recursive: true });
    fs.writeFileSync(path.join(s.assets, version, zipName(version)), zip);
    fs.writeFileSync(path.join(s.assets, version, "BUILD.json"), build);
    assets.push({ name: zipName(version), url: `https://example.invalid/${version}/zip` }, { name: "BUILD.json", url: `https://example.invalid/${version}/build` });
  }
  (s.pages[0] as unknown[]).push({
    tag_name: `v${version}`,
    draft: options.draft ?? false,
    prerelease: options.apiPrerelease ?? isPrerelease(version),
    immutable: "immutable" in options ? options.immutable : true,
    published_at: options.draft ? null : "2026-11-23T00:00:00Z",
    assets,
  });
  return made;
}

function live(s: Scenario, versions: string[] | null, builds: Record<string, Buffer> = {}): void {
  if (versions === null) {
    fs.writeFileSync(path.join(s.live, "versions.status"), "404\n");
    fs.writeFileSync(path.join(s.live, "versions.json"), "<!doctype html><title>404</title>\n");
    return;
  }
  const listing = { format: "sarcostat-versions", format_version: 1, latest: versions.at(-1), versions: versions.map((version) => ({ version, path: `v${version}/` })) };
  fs.writeFileSync(path.join(s.live, "versions.status"), "200\n");
  fs.writeFileSync(path.join(s.live, "versions.json"), canonicalJson(listing));
  for (const version of versions) {
    const dir = path.join(s.live, `v${version}`);
    fs.mkdirSync(dir, { recursive: true });
    const bytes = builds[version] ?? folder(version).build;
    fs.writeFileSync(path.join(dir, "BUILD.json"), bytes);
    fs.writeFileSync(path.join(dir, "BUILD.json.status"), "200\n");
  }
}

function run(s: Scenario, overrides: Record<string, unknown> = {}) {
  return assemble({ pages: s.pages, assetsDir: s.assets, liveDir: s.live, event: "release", runAttempt: "1", ref: "refs/tags/v0.2.0rc1", outDir: s.out, ...overrides });
}

function deployedVersions(s: Scenario): unknown {
  return JSON.parse(fs.readFileSync(path.join(s.out, "versions.json"), "utf8"));
}

describe("version order and latest (PEP 440, plan §3.10 step 5 and N18)", () => {
  it("sorts like packaging.version.Version", () => {
    const expected = ["0.1.0", "0.2.0.dev0", "0.2.0a1", "0.2.0b1.dev0", "0.2.0b1", "0.2.0b1.post1", "0.2.0rc1", "0.2.0rc1.post1", "0.2.0rc2", "0.2.0rc3", "0.2.0", "0.2.0.post1", "0.2.1", "0.3.0rc1", "0.10.0", "1.0.0rc1.post2.dev3"];
    const shuffled = [...expected].reverse().sort((a, b) => (sha(a) < sha(b) ? -1 : 1));
    expect([...shuffled].sort(compareVersions)).toEqual(expected);
    expect(compareVersions("0.2.0", "0.2")).toBe(0);
  });

  it.each([
    [["0.2.0b1"], "0.2.0b1"],
    [["0.2.0b1", "0.2.0rc1"], "0.2.0rc1"],
    [["0.2.0b1", "0.2.0rc1", "0.2.0rc2", "0.2.0rc3", "0.2.0rc1.post1"], "0.2.0rc3"],
    [["0.2.0rc1", "0.2.0"], "0.2.0"],
    [["0.2.0", "0.3.0rc1"], "0.2.0"],
  ])("latest of %j is %s", (versions, latest) => {
    expect(pickLatest(versions)).toBe(latest);
  });

  it("takes pre-release status from the version string", () => {
    expect(["0.2.0b1", "0.2.0rc1", "0.2.0rc1.post1", "0.2.0.dev0"].map(isPrerelease)).toEqual([true, true, true, true]);
    expect(["0.2.0", "0.2.0.post1", "1.0"].map(isPrerelease)).toEqual([false, false, false]);
  });
});

describe("release-info (release-build.yml, M4-6 #3)", () => {
  it("refuses a tag that is not v + __version__, and a development version", () => {
    expect(releaseInfo("v0.2.0b1", '__version__ = "0.2.0b1"\n')).toEqual({ version: "0.2.0b1", prerelease: true });
    expect(releaseInfo("v0.2.0", 'x = 1\n__version__ = "0.2.0"\n')).toEqual({ version: "0.2.0", prerelease: false });
    expect(() => releaseInfo("v0.2.0b1", '__version__ = "0.2.0rc1"\n')).toThrow(/not v \+ __version__/);
    expect(() => releaseInfo("0.2.0b1", '__version__ = "0.2.0b1"\n')).toThrow(AssembleError);
    expect(() => releaseInfo("v0.2.0rc1.dev0", '__version__ = "0.2.0rc1.dev0"\n')).toThrow(/development version/);
  });
});

describe("deterministic stored zip", () => {
  it("is byte-identical for the same files in any order, round-trips and keeps the build epoch", () => {
    const entries = [
      { name: "v1.0/b.txt", data: Buffer.from("b") },
      { name: "v1.0/a/c.txt", data: Buffer.from("c".repeat(1000)) },
    ];
    const first = writeZip(entries, { mtime: EPOCH });
    const second = writeZip([...entries].reverse(), { mtime: EPOCH });
    expect(second.equals(first)).toBe(true);
    expect([...readZip(first).entries()].map(([name, data]) => [name, data.toString()])).toEqual([
      ["v1.0/a/c.txt", "c".repeat(1000)],
      ["v1.0/b.txt", "b"],
    ]);
    expect(writeZip(entries, { mtime: EPOCH + 3600 }).equals(first)).toBe(false);
  });

  it("refuses unsafe names and a damaged entry", () => {
    expect(() => writeZip([{ name: "../x", data: Buffer.from("x") }])).toThrow(AssembleError);
    expect(() => writeZip([{ name: "/x", data: Buffer.from("x") }])).toThrow(AssembleError);
    const zip = writeZip([{ name: "v1.0/x.txt", data: Buffer.from("payload") }]);
    const damaged = Buffer.from(zip);
    const at = damaged.indexOf("payload") + 2;
    damaged.writeUInt8(damaged.readUInt8(at) ^ 1, at);
    expect(() => readZip(damaged)).toThrow(/CRC-32/);
    expect(() => readZip(Buffer.from("not a zip"))).toThrow(/not a zip/);
  });

  it("zips a version folder so that verify-zip accepts it against its BUILD.json", () => {
    const made = folder("0.2.0b1");
    const dir = path.join(scratch, "zip-folder", "v0.2.0b1");
    for (const [rel, data] of Object.entries(made.files)) {
      fs.mkdirSync(path.dirname(path.join(dir, rel)), { recursive: true });
      fs.writeFileSync(path.join(dir, rel), data);
    }
    fs.writeFileSync(path.join(dir, "BUILD.json"), made.build);
    const zipped = zipVersionFolder(dir);
    expect(zipped.zip.equals(made.zip)).toBe(true);
    expect(verifyZip(zipped.zip, made.build, "0.2.0b1").ok).toBe(true);
    fs.writeFileSync(path.join(dir, "assets", "app.js"), "changed\n");
    expect(() => zipVersionFolder(dir)).toThrow(/does not match its BUILD.json/);
  });
});

describe("assembler (M4-6 #1)", () => {
  it("carries a published version that web/releases.json does not list", () => {
    const s = scenario();
    release(s, "0.2.0b1");
    release(s, "0.2.0rc1");
    live(s, ["0.2.0b1"]);
    const result = run(s);
    expect(result.problems).toEqual([]);
    expect(result.versions).toEqual(["0.2.0b1", "0.2.0rc1"]);
    expect(fs.readFileSync(path.join(s.out, "v0.2.0rc1", "index.html"), "utf8")).toContain("SarcoStat 0.2.0rc1");
    expect(fs.readFileSync(path.join(s.out, "v0.2.0rc1", "BUILD.json"))).toEqual(folder("0.2.0rc1").build);
  });

  it("does not carry a draft release, even with a zip", () => {
    const s = scenario();
    release(s, "0.2.0b1");
    release(s, "0.2.0rc1", { draft: true });
    live(s, ["0.2.0b1"]);
    const result = run(s, { ref: "refs/tags/v0.2.0b1" });
    expect(result.ok).toBe(true);
    expect(result.versions).toEqual(["0.2.0b1"]);
    expect(result.notes).toContain("v0.2.0rc1: draft release, not carried");
    expect(fs.existsSync(path.join(s.out, "v0.2.0rc1"))).toBe(false);
  });

  it("keeps every newer folder when a hotfix built from an older tag is published, and latest stays the highest version", () => {
    const s = scenario();
    for (const version of ["0.2.0b1", "0.2.0rc1", "0.2.0rc2", "0.2.0rc1.post1"]) {
      release(s, version);
    }
    live(s, ["0.2.0b1", "0.2.0rc1", "0.2.0rc2"]);
    const result = run(s, { ref: "refs/tags/v0.2.0rc1.post1" });
    expect(result.problems).toEqual([]);
    expect(result.versions).toEqual(["0.2.0b1", "0.2.0rc1", "0.2.0rc1.post1", "0.2.0rc2"]);
    expect(result.latest).toBe("0.2.0rc2");
    expect(fs.readdirSync(s.out).sort()).toEqual(["index.html", "latest", "v0.2.0b1", "v0.2.0rc1", "v0.2.0rc1.post1", "v0.2.0rc2", "versions.json"]);
  });

  it("fails when a zip or a file in it differs from its release's BUILD.json, and writes nothing", () => {
    const s = scenario();
    release(s, "0.2.0b1");
    const good = folder("0.2.0rc1");
    const entries = Object.entries(good.files).map(([rel, data]) => ({ name: `v0.2.0rc1/${rel}`, data: rel === "assets/app.js" ? Buffer.from("tampered\n") : data }));
    entries.push({ name: "v0.2.0rc1/BUILD.json", data: good.build });
    release(s, "0.2.0rc1", { zip: writeZip(entries, { mtime: EPOCH }) });
    live(s, ["0.2.0b1"]);
    const result = run(s);
    expect(result.ok).toBe(false);
    expect(result.problems).toContain("v0.2.0rc1: assets/app.js: SHA-256 differs from BUILD.json");
    expect(fs.existsSync(s.out)).toBe(false);

    const t = scenario();
    release(t, "0.2.0rc1", { build: folder("0.2.0rc2").build });
    live(t, null);
    expect(run(t).problems).toEqual(expect.arrayContaining(["v0.2.0rc1: BUILD.json says version 0.2.0rc2, the release is 0.2.0rc1", "v0.2.0rc1: the zip's BUILD.json differs from the release's BUILD.json asset"]));
  });

  it("fails when a listed version differs from web/releases.json", () => {
    const s = scenario();
    const b1 = release(s, "0.2.0b1");
    release(s, "0.2.0rc1");
    live(s, ["0.2.0b1"]);
    const known = readKnown(canonicalJson({ format: "sarcostat-releases", format_version: 1, releases: [{ version: "0.2.0b1", build_json_sha256: sha(b1.build), zip_sha256: sha(b1.zip) }] }));
    expect(run(s, { known }).ok).toBe(true);
    const wrong = readKnown(canonicalJson({ format: "sarcostat-releases", format_version: 1, releases: [{ version: "0.2.0b1", build_json_sha256: "0".repeat(64), zip_sha256: sha(b1.zip) }] }));
    const u = scenario();
    release(u, "0.2.0b1");
    release(u, "0.2.0rc1");
    live(u, ["0.2.0b1"]);
    expect(run(u, { known: wrong }).problems.join("\n")).toMatch(/v0\.2\.0b1: BUILD\.json SHA-256 .* differs from web\/releases\.json/);
  });

  it("fails when a live version is missing from the Releases API set", () => {
    const s = scenario();
    release(s, "0.2.0rc1");
    live(s, ["0.2.0b1", "0.2.0rc1"]);
    const result = run(s);
    expect(result.ok).toBe(false);
    expect(result.problems).toContain("live version v0.2.0b1 is not a published release carrying both assets: this deploy would drop it");
  });

  it("accepts a 404 on versions.json only when the API set has exactly one version", () => {
    const first = scenario();
    release(first, "0.2.0b1");
    live(first, null);
    const ok = run(first, { ref: "refs/tags/v0.2.0b1" });
    expect(ok.ok).toBe(true);
    expect(ok.notes.join("\n")).toMatch(/first deploy/);

    const second = scenario();
    release(second, "0.2.0b1");
    release(second, "0.2.0rc1");
    live(second, null);
    const refused = run(second);
    expect(refused.ok).toBe(false);
    expect(refused.problems.join("\n")).toMatch(/answered 404 but the Releases API carries 2 versions/);

    const third = scenario();
    release(third, "0.2.0b1");
    fs.writeFileSync(path.join(third.live, "versions.status"), "503\n");
    expect(run(third, { ref: "refs/tags/v0.2.0b1" }).problems).toContain("the live /versions.json could not be read (HTTP status 503)");
  });

  it("fails a first-attempt release run on a corrupt live folder, and repairs it from the release zip on a re-run or a dispatch, reporting the mismatch", () => {
    const corrupt = { "0.2.0b1": Buffer.concat([folder("0.2.0b1").build, Buffer.from(" ")]) };
    const make = () => {
      const s = scenario();
      release(s, "0.2.0b1");
      release(s, "0.2.0rc1");
      live(s, ["0.2.0b1", "0.2.0rc1"], corrupt);
      return s;
    };
    const firstAttempt = run(make());
    expect(firstAttempt.ok).toBe(false);
    expect(firstAttempt.problems.join("\n")).toMatch(/live \/v0\.2\.0b1\/BUILD\.json .* differs from its release asset .*first-attempt release run stops here/);

    for (const overrides of [{ runAttempt: "2" }, { event: "workflow_dispatch" }]) {
      const s = make();
      const repaired = run(s, overrides);
      expect(repaired.ok).toBe(true);
      expect(repaired.repair).toBe(true);
      expect(repaired.warnings.join("\n")).toMatch(/v0\.2\.0b1\/BUILD\.json .* differs .*repaired from the release zip/);
      expect(fs.readFileSync(path.join(s.out, "v0.2.0b1", "BUILD.json"))).toEqual(folder("0.2.0b1").build);
      expect(repaired.summary).toContain("| 0.2.0b1 | yes |");
      expect(repaired.summary).toContain("repaired");
    }

    const missing = scenario();
    release(missing, "0.2.0b1");
    release(missing, "0.2.0rc1");
    live(missing, ["0.2.0b1"]);
    fs.writeFileSync(path.join(missing.live, "v0.2.0b1", "BUILD.json.status"), "404\n");
    expect(run(missing).problems.join("\n")).toMatch(/could not be read \(HTTP status 404\)/);
  });

  it("writes versions.json with pre-release status from the version string, not from the API flag", () => {
    const s = scenario();
    release(s, "0.2.0rc1", { apiPrerelease: false });
    release(s, "0.2.0", { apiPrerelease: true });
    live(s, ["0.2.0rc1"]);
    const result = run(s, { ref: "refs/tags/v0.2.0" });
    expect(result.ok).toBe(true);
    expect(result.latest).toBe("0.2.0");
    const listing = deployedVersions(s) as { latest: string; versions: { version: string; prerelease: boolean; build_json_sha256: string; path: string }[] };
    expect(listing.latest).toBe("0.2.0");
    expect(listing.versions.map((entry) => [entry.version, entry.prerelease, entry.path])).toEqual([
      ["0.2.0rc1", true, "v0.2.0rc1/"],
      ["0.2.0", false, "v0.2.0/"],
    ]);
    expect(listing.versions[1]?.build_json_sha256).toBe(sha(folder("0.2.0").build));
    expect(result.notes.join("\n")).toMatch(/v0\.2\.0rc1: the release is marked release on GitHub; the version string makes it a pre-release/);
    expect(fs.readFileSync(path.join(s.out, "versions.json"), "utf8")).toBe(canonicalJson(listing));
  });

  it("reports a published version whose release is not immutable in the summary, without blocking", () => {
    const s = scenario();
    release(s, "0.2.0b1", { immutable: false });
    release(s, "0.2.0rc1", { immutable: undefined });
    live(s, ["0.2.0b1"]);
    const result = run(s);
    expect(result.ok).toBe(true);
    expect(result.warnings).toEqual(["v0.2.0b1: the release is not immutable (immutable is not true in the Releases API)", "v0.2.0rc1: the release is not immutable (immutable is not true in the Releases API)"]);
    expect(result.summary).toContain("### Warnings");
    expect(result.summary).toContain("| **no** |");
  });

  it("refuses a ref that is not a v* tag, and a published release without its web assets", () => {
    const s = scenario();
    release(s, "0.2.0b1");
    live(s, null);
    expect(run(s, { ref: "refs/heads/develop", event: "workflow_dispatch" }).problems).toContain("the ref refs/heads/develop is not a v* tag: Pages is deployed only from a release tag");
    const t = scenario();
    release(t, "0.2.0b1");
    release(t, "0.2.0rc1", { web: false });
    live(t, ["0.2.0b1"]);
    expect(run(t).problems).toContain("the published release v0.2.0rc1 does not carry both sarcostat-web-0.2.0rc1.zip and BUILD.json");
    expect(releaseSet(t.pages).carried.map((entry: { version: string }) => entry.version)).toEqual(["0.2.0b1"]);
  });
});

describe("redirect pages", () => {
  function follow(html: string, hash: string, search = ""): string {
    const script = /<script>([^<]*)<\/script>/.exec(html)?.[1] ?? "";
    let target = "";
    vm.runInNewContext(script, { location: { hash, search, replace: (url: string) => (target = url) } });
    return target;
  }

  it("keep the hash, e.g. #/study, and allow only their own inline script", () => {
    const s = scenario();
    release(s, "0.2.0b1");
    release(s, "0.2.0rc1");
    live(s, ["0.2.0b1"]);
    expect(run(s).ok).toBe(true);
    const root = fs.readFileSync(path.join(s.out, "index.html"), "utf8");
    const latest = fs.readFileSync(path.join(s.out, "latest", "index.html"), "utf8");
    expect(follow(root, "#/study")).toBe("latest/#/study");
    expect(follow(latest, "#/study")).toBe("../v0.2.0rc1/#/study");
    expect(follow(latest, "", "?x=1")).toBe("../v0.2.0rc1/?x=1");
    for (const html of [root, latest]) {
      const script = /<script>([^<]*)<\/script>/.exec(html)?.[1] ?? "";
      const hash = crypto.createHash("sha256").update(script).digest("base64");
      expect(html).toContain(`content="default-src 'none'; script-src 'sha256-${hash}'"`);
      expect(html.match(/<script/g)).toHaveLength(1);
    }
    expect(root).toContain('<meta name="sarcostat-redirect" content="latest/">');
    expect(redirectPage("../v1.0/")).toContain('<noscript><meta http-equiv="refresh" content="0; url=../v1.0/"></noscript>');
  });
});

describe("assemble_pages.mjs is self-contained (M4-6 #8)", () => {
  it("imports only node: built-ins", () => {
    const text = fs.readFileSync(SCRIPT, "utf8");
    const specifiers = [
      ...[...text.matchAll(/^\s*import\s+(?:[^"'`;]*?\s+from\s+)?["']([^"']+)["']/gm)].map((match) => match[1]),
      ...[...text.matchAll(/^\s*export\s+[^"'`;]*?\s+from\s+["']([^"']+)["']/gm)].map((match) => match[1]),
      ...[...text.matchAll(/\bimport\s*\(\s*["'`]?([^"'`)]*)/g)].map((match) => match[1]),
      ...[...text.matchAll(/\brequire\s*\(\s*["'`]?([^"'`)]*)/g)].map((match) => match[1]),
    ];
    expect(specifiers.length).toBeGreaterThan(3);
    expect(specifiers.filter((name) => !name?.startsWith("node:"))).toEqual([]);
  });
});
