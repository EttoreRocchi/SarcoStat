import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";

import { describe, expect, it } from "vitest";

import { WEB_DIR } from "../../scripts/lane.mjs";

const REPO_DIR = path.resolve(WEB_DIR, "..");
const STYLES_DIR = path.join(WEB_DIR, "src", "styles");
const ASSETS_DIR = path.join(WEB_DIR, "src", "assets");
const FONTS_DIR = path.join(ASSETS_DIR, "fonts");
const STRINGS_DIR = path.join(WEB_DIR, "src", "strings");
const REPORTS_PY = path.join(REPO_DIR, "src", "sarcostat", "engine", "reports.py");
const STUDY_PY = path.join(REPO_DIR, "src", "sarcostat", "engine", "study.py");
const WOFF2_BUDGET = 300_000;
const PRIMARY_FAMILIES = ["Barlow Semi Condensed", "Atkinson Hyperlegible Next", "Atkinson Hyperlegible Mono"];
const STACK_TOKENS = ["--f-display", "--f-body", "--f-num"];
const SYMBOLS = "σκρψ±→÷µ°×−≤≥…";

const WOFF2_TAGS = [
  "cmap", "head", "hhea", "hmtx", "maxp", "name", "OS/2", "post", "cvt ", "fpgm", "glyf", "loca", "prep", "CFF ", "VORG", "EBDT",
  "EBLC", "gasp", "hdmx", "kern", "LTSH", "PCLT", "VDMX", "vhea", "vmtx", "BASE", "GDEF", "GPOS", "GSUB", "EBSC", "JSTF", "MATH",
  "CBDT", "CBLC", "COLR", "CPAL", "SVG ", "sbix", "acnt", "avar", "bdat", "bloc", "bsln", "cvar", "fdsc", "feat", "fmtx", "fvar",
  "gvar", "hsty", "just", "lcar", "mort", "morx", "opbd", "prop", "trak", "Zapf", "Silf", "Glat", "Gloc", "Feat", "Sill",
];

interface FontFace {
  family: string;
  style: string;
  weight: [number, number];
  srcs: string[];
  formats: string[];
  ranges: [number, number][] | null;
  file: string;
  cssFile: string;
}

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
  return out.sort();
}

function uintBase128(bytes: Buffer, start: number): { value: number; next: number } {
  let value = 0;
  for (let i = 0; i < 5; i += 1) {
    const byte = bytes[start + i];
    if (byte === undefined) {
      throw new Error("truncated UIntBase128");
    }
    if (i === 0 && byte === 0x80) {
      throw new Error("UIntBase128 with a leading zero");
    }
    value = value * 128 + (byte & 0x7f);
    if ((byte & 0x80) === 0) {
      return { value, next: start + i + 1 };
    }
  }
  throw new Error("UIntBase128 longer than five bytes");
}

function woff2Table(bytes: Buffer, wanted: string): Buffer {
  if (bytes.readUInt32BE(0) !== 0x774f4632) {
    throw new Error("not a WOFF2 file");
  }
  if (bytes.readUInt32BE(4) === 0x74746366) {
    throw new Error("WOFF2 font collections are not read");
  }
  const numTables = bytes.readUInt16BE(12);
  const compressedSize = bytes.readUInt32BE(20);
  let pos = 48;
  const entries: { tag: string; length: number }[] = [];
  for (let i = 0; i < numTables; i += 1) {
    const flags = bytes[pos] ?? 0;
    pos += 1;
    const index = flags & 0x3f;
    const version = flags >> 6;
    let tag: string;
    if (index === 63) {
      tag = bytes.toString("latin1", pos, pos + 4);
      pos += 4;
    } else {
      tag = WOFF2_TAGS[index] ?? "";
    }
    const orig = uintBase128(bytes, pos);
    pos = orig.next;
    let length = orig.value;
    const transformed = tag === "glyf" || tag === "loca" ? version === 0 : version !== 0;
    if (transformed) {
      const stored = uintBase128(bytes, pos);
      pos = stored.next;
      length = stored.value;
    }
    entries.push({ tag, length });
  }
  const stream = zlib.brotliDecompressSync(bytes.subarray(pos, pos + compressedSize));
  let offset = 0;
  for (const entry of entries) {
    if (entry.tag === wanted) {
      return stream.subarray(offset, offset + entry.length);
    }
    offset += entry.length;
  }
  throw new Error(`no ${wanted} table`);
}

function readFormat4(cmap: Buffer, start: number, points: Set<number>): void {
  const segX2 = cmap.readUInt16BE(start + 6);
  const ends = start + 14;
  const starts = ends + segX2 + 2;
  const deltas = starts + segX2;
  const rangeOffsets = deltas + segX2;
  for (let s = 0; s < segX2 / 2; s += 1) {
    const end = cmap.readUInt16BE(ends + 2 * s);
    const first = cmap.readUInt16BE(starts + 2 * s);
    const delta = cmap.readUInt16BE(deltas + 2 * s);
    const rangeOffset = cmap.readUInt16BE(rangeOffsets + 2 * s);
    for (let c = first; c <= end && c !== 0xffff; c += 1) {
      let glyph: number;
      if (rangeOffset === 0) {
        glyph = (c + delta) & 0xffff;
      } else {
        glyph = cmap.readUInt16BE(rangeOffsets + 2 * s + rangeOffset + 2 * (c - first));
        if (glyph !== 0) {
          glyph = (glyph + delta) & 0xffff;
        }
      }
      if (glyph !== 0) {
        points.add(c);
      }
    }
  }
}

function readFormat12(cmap: Buffer, start: number, points: Set<number>): void {
  const groups = cmap.readUInt32BE(start + 12);
  for (let g = 0; g < groups; g += 1) {
    const at = start + 16 + 12 * g;
    const first = cmap.readUInt32BE(at);
    const last = cmap.readUInt32BE(at + 4);
    const glyph = cmap.readUInt32BE(at + 8);
    for (let c = first; c <= last; c += 1) {
      if (glyph + (c - first) !== 0) {
        points.add(c);
      }
    }
  }
}

function cmapCodePoints(cmap: Buffer): Set<number> {
  const points = new Set<number>();
  const numTables = cmap.readUInt16BE(2);
  for (let i = 0; i < numTables; i += 1) {
    const record = 4 + 8 * i;
    const platform = cmap.readUInt16BE(record);
    const encoding = cmap.readUInt16BE(record + 2);
    const offset = cmap.readUInt32BE(record + 4);
    const unicode = platform === 0 ? encoding !== 5 : platform === 3 && (encoding === 1 || encoding === 10);
    if (!unicode) {
      continue;
    }
    const format = cmap.readUInt16BE(offset);
    if (format === 4) {
      readFormat4(cmap, offset, points);
    } else if (format === 12) {
      readFormat12(cmap, offset, points);
    } else if (format === 6) {
      const first = cmap.readUInt16BE(offset + 6);
      const count = cmap.readUInt16BE(offset + 8);
      for (let k = 0; k < count; k += 1) {
        if (cmap.readUInt16BE(offset + 10 + 2 * k) !== 0) {
          points.add(first + k);
        }
      }
    } else if (format === 0) {
      for (let k = 0; k < 256; k += 1) {
        if ((cmap[offset + 6 + k] ?? 0) !== 0) {
          points.add(k);
        }
      }
    } else {
      throw new Error(`cmap subtable format ${format} is not read`);
    }
  }
  return points;
}

const cmapCache = new Map<string, Set<number>>();

function woff2CodePoints(file: string): Set<number> {
  let points = cmapCache.get(file);
  if (!points) {
    points = cmapCodePoints(woff2Table(fs.readFileSync(file), "cmap"));
    cmapCache.set(file, points);
  }
  return points;
}

function stripComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

function parseUnicodeRange(value: string): [number, number][] {
  return value.split(",").map((part) => {
    const item = part.trim().toUpperCase();
    const match = /^U\+([0-9A-F?]{1,6})(?:-([0-9A-F]{1,6}))?$/.exec(item);
    if (!match?.[1]) {
      throw new Error(`bad unicode-range item ${item}`);
    }
    if (match[1].includes("?")) {
      return [parseInt(match[1].replace(/\?/g, "0"), 16), parseInt(match[1].replace(/\?/g, "F"), 16)];
    }
    const lo = parseInt(match[1], 16);
    return [lo, match[2] ? parseInt(match[2], 16) : lo];
  });
}

function parseFontFaces(cssFile: string): FontFace[] {
  const css = stripComments(fs.readFileSync(cssFile, "utf8"));
  const faces: FontFace[] = [];
  for (const block of css.matchAll(/@font-face\s*\{([^}]*)\}/g)) {
    const decls = new Map<string, string>();
    for (const decl of (block[1] ?? "").split(";")) {
      const colon = decl.indexOf(":");
      if (colon > 0) {
        decls.set(decl.slice(0, colon).trim().toLowerCase(), decl.slice(colon + 1).trim());
      }
    }
    const src = decls.get("src") ?? "";
    const srcs = [...src.matchAll(/url\(\s*(["']?)([^"')]*)\1\s*\)/g)].map((m) => m[2] ?? "");
    const formats = [...src.matchAll(/format\(\s*["']?([^"')]*)["']?\s*\)/g)].map((m) => m[1] ?? "");
    const weights = (decls.get("font-weight") ?? "400").split(/\s+/).map(Number);
    const range = decls.get("unicode-range");
    faces.push({
      family: (decls.get("font-family") ?? "").replace(/^["']|["']$/g, ""),
      style: decls.get("font-style") ?? "normal",
      weight: [weights[0] ?? 400, weights[1] ?? weights[0] ?? 400],
      srcs,
      formats,
      ranges: range ? parseUnicodeRange(range) : null,
      file: srcs[0] ? path.resolve(path.dirname(cssFile), srcs[0]) : "",
      cssFile,
    });
  }
  return faces;
}

function rootTokens(css: string): Map<string, string> {
  const tokens = new Map<string, string>();
  let depth = 0;
  let prelude = "";
  let body = "";
  let inRoot = false;
  for (const ch of stripComments(css)) {
    if (ch === "{") {
      depth += 1;
      inRoot = depth === 1 && prelude.split(",").map((s) => s.trim()).includes(":root");
      body = "";
      continue;
    }
    if (ch === "}") {
      if (depth === 1 && inRoot) {
        for (const m of body.matchAll(/--([\w-]+)\s*:\s*([^;]+);/g)) {
          tokens.set(m[1] ?? "", (m[2] ?? "").trim());
        }
      }
      depth -= 1;
      prelude = "";
      inRoot = false;
      continue;
    }
    if (depth === 0) {
      prelude += ch;
    } else {
      body += ch;
    }
  }
  return tokens;
}

function familyList(value: string): string[] {
  return value.split(",").map((part) => part.trim().replace(/^["']|["']$/g, ""));
}

function decodeEscapes(text: string, python: boolean): string {
  if (python && text.includes("\\N{")) {
    throw new Error("\\N{...} escapes are not decoded; write the character itself");
  }
  const pattern = python ? /\\U([0-9a-fA-F]{8})|\\u([0-9a-fA-F]{4})|\\x([0-9a-fA-F]{2})/g : /\\u\{([0-9a-fA-F]{1,6})\}|\\u([0-9a-fA-F]{4})|\\x([0-9a-fA-F]{2})/g;
  let out = "";
  for (const m of text.matchAll(pattern)) {
    out += String.fromCodePoint(parseInt(m[1] ?? m[2] ?? m[3] ?? "0", 16));
  }
  return out;
}

function noticeTable(source: string): string {
  const lines = source.split("\n");
  const first = lines.findIndex((line) => /^NOTICES\b/.test(line));
  if (first < 0) {
    throw new Error("NOTICES table not found in study.py");
  }
  const last = lines.findIndex((line, i) => i > first && line === "}");
  if (last < 0) {
    throw new Error("end of the NOTICES table not found in study.py");
  }
  return lines.slice(first, last + 1).join("\n");
}

function needsGlyph(cp: number): boolean {
  return !/[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/u.test(String.fromCodePoint(cp));
}

function codePoints(text: string): Set<number> {
  const out = new Set<number>();
  for (const ch of text) {
    const cp = ch.codePointAt(0) ?? 0;
    if (needsGlyph(cp)) {
      out.add(cp);
    }
  }
  return out;
}

function greekAlphabet(): string {
  let out = "";
  for (let cp = 0x391; cp <= 0x3c9; cp += 1) {
    if (cp !== 0x3a2 && (cp <= 0x3a9 || cp >= 0x3b1)) {
      out += String.fromCodePoint(cp);
    }
  }
  return out;
}

function stringFiles(): string[] {
  return filesUnder(STRINGS_DIR, [".ts", ".json"]);
}

function requiredText(): Map<string, string> {
  const sources = new Map<string, string>();
  for (const file of stringFiles()) {
    const text = fs.readFileSync(file, "utf8");
    sources.set(path.relative(REPO_DIR, file), text + decodeEscapes(text, false));
  }
  const reports = fs.readFileSync(REPORTS_PY, "utf8");
  sources.set("src/sarcostat/engine/reports.py", reports + decodeEscapes(reports, true));
  const notices = noticeTable(fs.readFileSync(STUDY_PY, "utf8"));
  sources.set("src/sarcostat/engine/study.py NOTICES", notices + decodeEscapes(notices, true));
  sources.set("symbols", SYMBOLS);
  sources.set("Greek alphabet", greekAlphabet());
  return sources;
}

function inRanges(face: FontFace, cp: number): boolean {
  return face.ranges === null || face.ranges.some(([lo, hi]) => cp >= lo && cp <= hi);
}

function matchWeight(faces: FontFace[], weight: number): FontFace[] {
  const distance = (face: FontFace): number => (weight < face.weight[0] ? face.weight[0] - weight : weight > face.weight[1] ? weight - face.weight[1] : 0);
  const best = Math.min(...faces.map(distance));
  return faces.filter((face) => distance(face) === best);
}

function renderingFace(faces: FontFace[], stack: string[], weight: number, cp: number): FontFace | undefined {
  for (const family of stack) {
    const own = faces.filter((face) => face.family === family && face.style === "normal");
    if (own.length === 0) {
      continue;
    }
    const candidates = matchWeight(own, weight).reverse();
    const face = candidates.find((f) => inRanges(f, cp) && woff2CodePoints(f.file).has(cp));
    if (face) {
      return face;
    }
  }
  return undefined;
}

function testWeights(faces: FontFace[], family: string): number[] {
  const weights = new Set<number>();
  for (const face of faces.filter((f) => f.family === family)) {
    weights.add(face.weight[0]);
    weights.add(face.weight[1]);
    for (let w = Math.ceil(face.weight[0] / 100) * 100; w <= face.weight[1]; w += 100) {
      weights.add(w);
    }
  }
  return [...weights].sort((a, b) => a - b);
}

function hex(cp: number): string {
  return `U+${cp.toString(16).toUpperCase().padStart(4, "0")}`;
}

const cssFiles = filesUnder(STYLES_DIR, [".css"]);
const faces = cssFiles.flatMap(parseFontFaces);
const woff2Files = filesUnder(FONTS_DIR, [".woff2"]);
const tokens = rootTokens(fs.readFileSync(path.join(STYLES_DIR, "tokens.css"), "utf8"));

describe("vendored woff2 files", () => {
  it("are WOFF2 fonts with a readable Unicode cmap", () => {
    expect(woff2Files.length).toBeGreaterThan(0);
    for (const file of woff2Files) {
      expect(woff2CodePoints(file).size, path.relative(WEB_DIR, file)).toBeGreaterThan(50);
    }
  });

  it("stay within the 300 KB budget in total", () => {
    const total = woff2Files.reduce((sum, file) => sum + fs.statSync(file).size, 0);
    expect(total).toBeLessThanOrEqual(WOFF2_BUDGET);
  });

  it("each carry the OFL text of their family", () => {
    const folders = new Set(woff2Files.map((file) => path.dirname(file)));
    for (const folder of folders) {
      const licence = path.join(folder, "OFL.txt");
      expect(fs.existsSync(licence), path.relative(WEB_DIR, licence)).toBe(true);
      expect(fs.readFileSync(licence, "utf8")).toContain("SIL Open Font License, Version 1.1");
    }
  });

  it("are all declared by an @font-face rule, and nothing else is", () => {
    const declared = new Set(faces.map((face) => face.file));
    expect([...declared].sort()).toEqual(woff2Files);
  });
});

describe("@font-face sources", () => {
  it("are relative url()s of woff2 files under web/src/assets/fonts, nothing remote, inline or local", () => {
    expect(faces.length).toBeGreaterThan(0);
    for (const face of faces) {
      const label = `${face.family} ${face.weight.join("-")} in ${path.relative(WEB_DIR, face.cssFile)}`;
      const src = fs.readFileSync(face.cssFile, "utf8");
      expect(src, label).not.toMatch(/local\(/);
      expect(face.srcs.length, label).toBe(1);
      expect(face.formats, label).toEqual(["woff2"]);
      for (const url of face.srcs) {
        expect(url, label).not.toMatch(/^[a-z][a-z0-9+.-]*:/i);
        expect(url, label).not.toMatch(/^\/|^\\/);
        expect(url, label).toMatch(/\.woff2$/);
        const target = path.resolve(path.dirname(face.cssFile), url);
        expect(path.relative(FONTS_DIR, target).startsWith(".."), label).toBe(false);
        expect(fs.existsSync(target), label).toBe(true);
      }
    }
  });

  it("leave every other url() in the stylesheets relative to a file under web/src/assets", () => {
    for (const file of cssFiles) {
      const css = stripComments(fs.readFileSync(file, "utf8"));
      expect(css, path.basename(file)).not.toMatch(/https?:|data:|\/\//i);
      for (const m of css.matchAll(/url\(\s*(["']?)([^"')]*)\1\s*\)/g)) {
        const url = m[2] ?? "";
        const target = path.resolve(path.dirname(file), url.split("#")[0] ?? "");
        expect(path.relative(ASSETS_DIR, target).startsWith(".."), `${path.basename(file)}: ${url}`).toBe(false);
        expect(fs.existsSync(target), `${path.basename(file)}: ${url}`).toBe(true);
      }
      for (const m of css.matchAll(/@import\s+([^;]+);/g)) {
        expect(m[1] ?? "", path.basename(file)).toMatch(/^["']\.\/[a-z]+\.css["']$/);
      }
    }
  });

  it("declare unicode ranges that claim only glyphs the file has", () => {
    for (const face of faces) {
      expect(face.ranges, face.family).not.toBeNull();
      const cmap = woff2CodePoints(face.file);
      const claimed = (face.ranges ?? []).flatMap(([lo, hi]) => Array.from({ length: hi - lo + 1 }, (_, k) => lo + k));
      const absent = claimed.filter((cp) => !cmap.has(cp)).map(hex);
      expect(absent, `${face.family} ${path.basename(face.file)}`).toEqual([]);
    }
  });
});

describe("glyph coverage", () => {
  const required = requiredText();
  const points = new Set<number>();
  for (const text of required.values()) {
    for (const cp of codePoints(text)) {
      points.add(cp);
    }
  }

  it("reads the strings file whenever it exists and the formatter alphabet", () => {
    for (const file of stringFiles()) {
      expect(required.has(path.relative(REPO_DIR, file))).toBe(true);
    }
    for (const ch of "±°σ÷") {
      expect(points.has(ch.codePointAt(0) ?? 0), ch).toBe(true);
    }
  });

  it("names the three design families and an OFL fallback in each stack", () => {
    const stacks = STACK_TOKENS.map((name) => familyList(tokens.get(name.slice(2)) ?? ""));
    expect(stacks.map((stack) => stack[0])).toEqual(PRIMARY_FAMILIES);
    for (const stack of stacks) {
      const fallbacks = stack.slice(1).filter((family) => faces.some((face) => face.family === family));
      expect(fallbacks.length, stack.join(", ")).toBeGreaterThan(0);
    }
  });

  for (const token of STACK_TOKENS) {
    it(`covers every required character in ${token} at every weight it declares`, () => {
      const stack = familyList(tokens.get(token.slice(2)) ?? "");
      const primary = stack[0] ?? "";
      const missing: string[] = [];
      for (const weight of testWeights(faces, primary)) {
        for (const cp of points) {
          const face = renderingFace(faces, stack, weight, cp);
          if (!face) {
            missing.push(`${weight} ${hex(cp)} ${String.fromCodePoint(cp)}`);
          } else if (face.family !== primary) {
            expect(PRIMARY_FAMILIES, hex(cp)).not.toContain(face.family);
            expect(face.ranges, face.family).not.toBeNull();
            expect(fs.existsSync(path.join(path.dirname(face.file), "OFL.txt")), face.family).toBe(true);
          }
        }
      }
      expect(testWeights(faces, primary).length).toBeGreaterThan(0);
      expect(missing).toEqual([]);
    });
  }
});
