import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { WEB_DIR, laneCacheDir } from "../../scripts/lane.mjs";
import { PNG_SIGNATURE, comparePngBuffers, decodePng, diffImages, encodePng } from "../screenshots/png.mjs";
import { DEFAULT_SET, SHOTS_DIR, compareFolders, refuseInsideShots, shotsSettings } from "../screenshots/shots.mjs";

function chunk(type: string, data: Buffer): Buffer {
  const head = Buffer.alloc(8);
  head.writeUInt32BE(data.length, 0);
  head.write(type, 4, "latin1");
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(zlib.crc32(Buffer.concat([head.subarray(4), data])) >>> 0, 0);
  return Buffer.concat([head, data, crc]);
}

function predictor(filter: number, a: number, b: number, c: number): number {
  switch (filter) {
    case 1:
      return a;
    case 2:
      return b;
    case 3:
      return (a + b) >> 1;
    case 4: {
      const p = a + b - c;
      const pa = Math.abs(p - a);
      const pb = Math.abs(p - b);
      const pc = Math.abs(p - c);
      return pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
    }
    default:
      return 0;
  }
}

function filteredPng(width: number, height: number, colorType: number, channels: number, pixels: Buffer, filters: number[]): Buffer {
  const stride = width * channels;
  const raw = Buffer.alloc(height * (stride + 1));
  for (let y = 0; y < height; y += 1) {
    const filter = filters[y % filters.length] as number;
    raw[y * (stride + 1)] = filter;
    for (let x = 0; x < stride; x += 1) {
      const value = pixels[y * stride + x] as number;
      const a = x >= channels ? (pixels[y * stride + x - channels] as number) : 0;
      const b = y > 0 ? (pixels[(y - 1) * stride + x] as number) : 0;
      const c = x >= channels && y > 0 ? (pixels[(y - 1) * stride + x - channels] as number) : 0;
      raw[y * (stride + 1) + 1 + x] = (value - predictor(filter, a, b, c)) & 0xff;
    }
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;
  ihdr[9] = colorType;
  const data = zlib.deflateSync(raw);
  const half = Math.floor(data.length / 2);
  return Buffer.concat([
    PNG_SIGNATURE,
    chunk("IHDR", ihdr),
    chunk("tEXt", Buffer.from("Software\0test", "latin1")),
    chunk("IDAT", data.subarray(0, half)),
    chunk("IDAT", data.subarray(half)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

function noise(length: number, seed: number): Buffer {
  const out = Buffer.alloc(length);
  let state = seed;
  for (let i = 0; i < length; i += 1) {
    state = (state * 1103515245 + 12345) >>> 0;
    out[i] = state >>> 24;
  }
  return out;
}

describe("PNG codec of the screenshot harness", () => {
  it("decodes RGBA rows written with each of the five PNG filters and split IDAT chunks", () => {
    const width = 7;
    const height = 10;
    const pixels = noise(width * height * 4, 7);
    const decoded = decodePng(filteredPng(width, height, 6, 4, pixels, [0, 1, 2, 3, 4]));
    expect(decoded.width).toBe(width);
    expect(decoded.height).toBe(height);
    expect(Buffer.from(decoded.data).equals(pixels)).toBe(true);
  });

  it("expands RGB and grey images to opaque RGBA", () => {
    const rgb = noise(3 * 2 * 3, 11);
    const fromRgb = decodePng(filteredPng(3, 2, 2, 3, rgb, [4, 3]));
    expect([...fromRgb.data.subarray(0, 8)]).toEqual([rgb[0], rgb[1], rgb[2], 255, rgb[3], rgb[4], rgb[5], 255]);
    const grey = Buffer.from([0, 128, 255, 7]);
    const fromGrey = decodePng(filteredPng(2, 2, 0, 1, grey, [1, 2]));
    expect([...fromGrey.data]).toEqual([0, 0, 0, 255, 128, 128, 128, 255, 255, 255, 255, 255, 7, 7, 7, 255]);
  });

  it("round-trips its own encoder and refuses damaged or unsupported files", () => {
    const image = { width: 5, height: 3, data: noise(5 * 3 * 4, 3) };
    const png = encodePng(image);
    expect(Buffer.from(decodePng(png).data).equals(image.data)).toBe(true);
    const damaged = Buffer.from(png);
    damaged[png.length - 20] = (damaged[png.length - 20] as number) ^ 0xff;
    expect(() => decodePng(damaged)).toThrow(/CRC/);
    expect(() => decodePng(Buffer.from("not a png"))).toThrow(/not a PNG/);
    const ihdr = Buffer.alloc(13);
    ihdr.writeUInt32BE(1, 0);
    ihdr.writeUInt32BE(1, 4);
    ihdr[8] = 16;
    const sixteen = Buffer.concat([PNG_SIGNATURE, chunk("IHDR", ihdr), chunk("IDAT", zlib.deflateSync(Buffer.alloc(3))), chunk("IEND", Buffer.alloc(0))]);
    expect(() => decodePng(sixteen)).toThrow(/bit depth 16/);
  });

  it("counts differing pixels and draws them red in the diff image", () => {
    const a = { width: 4, height: 2, data: Buffer.alloc(32, 40) };
    const b = { width: 4, height: 2, data: Buffer.from(a.data) };
    b.data[4 * 5 + 2] = 41;
    b.data[4 * 6 + 3] = 0;
    const diff = diffImages(a, b);
    expect(diff.differing).toBe(2);
    expect([...(diff.diff?.data ?? Buffer.alloc(0)).subarray(20, 24)]).toEqual([255, 0, 0, 255]);
    expect(diffImages(a, { width: 2, height: 4, data: a.data }).sameSize).toBe(false);
    const same = comparePngBuffers(encodePng(a), encodePng(a));
    expect(same).toMatchObject({ identicalBytes: true, sameSize: true, differing: 0, diffPng: null });
    const changed = comparePngBuffers(encodePng(a), encodePng(b));
    expect(changed.identicalBytes).toBe(false);
    expect(changed.differing).toBe(2);
    expect(decodePng(changed.diffPng as Buffer).width).toBe(4);
  });
});

describe("screenshot settings and run comparison", () => {
  let scratch = "";

  beforeAll(() => {
    fs.mkdirSync(laneCacheDir("vitest"), { recursive: true });
    scratch = fs.mkdtempSync(path.join(laneCacheDir("vitest"), "harness-shots-"));
  });

  afterAll(() => {
    fs.rmSync(scratch, { recursive: true, force: true });
  });

  it("compares with the m3 set on the dev machine, never under CI or in candidate mode", () => {
    expect(SHOTS_DIR).toBe(path.join(WEB_DIR, "tests", "screenshots"));
    const local = shotsSettings({});
    expect(local).toMatchObject({ set: DEFAULT_SET, compare: true, candidate: null, ci: false, require: false });
    expect(DEFAULT_SET).toBe("m3");
    expect(local.setDir).toBe(path.join(SHOTS_DIR, "m3"));
    expect(shotsSettings({ CI: "true" }).compare).toBe(false);
    const candidate = shotsSettings({ SARCOSTAT_SHOTS_CANDIDATE: scratch, SARCOSTAT_SHOTS_SET: "mc3" });
    expect(candidate).toMatchObject({ set: "mc3", candidate: scratch, compare: false });
    expect(shotsSettings({ SARCOSTAT_SHOTS_REQUIRE: "1" }).require).toBe(true);
    expect(() => shotsSettings({ SARCOSTAT_SHOTS_SET: "../m3" })).toThrow(/SARCOSTAT_SHOTS_SET/);
  });

  it("never writes candidates into web/tests/screenshots/ or one of its sets", () => {
    expect(() => refuseInsideShots(SHOTS_DIR)).toThrow(/only the lead copies/);
    expect(() => refuseInsideShots(path.join(SHOTS_DIR, "m3"))).toThrow(/only the lead copies/);
    expect(() => shotsSettings({ SARCOSTAT_SHOTS_CANDIDATE: path.join(SHOTS_DIR, "m3") })).toThrow(/only the lead copies/);
    expect(refuseInsideShots(scratch)).toBe(scratch);
  });

  it("passes two runs only when every capture exists in both with zero differing pixels", () => {
    const runA = path.join(scratch, "a");
    const runB = path.join(scratch, "b");
    fs.mkdirSync(runA);
    fs.mkdirSync(runB);
    expect(compareFolders(runA, runB).ok).toBe(false);
    const image = { width: 3, height: 3, data: noise(36, 5) };
    fs.writeFileSync(path.join(runA, "home.png"), encodePng(image));
    fs.writeFileSync(path.join(runB, "home.png"), encodePng(image));
    const pixels = Buffer.from(image.data);
    fs.writeFileSync(path.join(runA, "fatal.png"), encodePng({ width: 3, height: 3, data: pixels }));
    fs.writeFileSync(path.join(runB, "fatal.png"), filteredPng(3, 3, 6, 4, pixels, [4]));
    const equal = compareFolders(runA, runB);
    expect(equal.rows.map((row) => [row.name, row.status])).toEqual([
      ["fatal.png", "0 pixels differ (the PNG bytes differ)"],
      ["home.png", "identical bytes"],
    ]);
    expect(equal.ok).toBe(true);
    const changed = Buffer.from(pixels);
    changed[0] = (changed[0] as number) ^ 1;
    fs.writeFileSync(path.join(runB, "fatal.png"), encodePng({ width: 3, height: 3, data: changed }));
    fs.writeFileSync(path.join(runA, "keys-sheet.png"), encodePng(image));
    const different = compareFolders(runA, runB);
    expect(different.ok).toBe(false);
    expect(different.rows.map((row) => [row.name, row.ok])).toEqual([
      ["fatal.png", false],
      ["home.png", true],
      ["keys-sheet.png", false],
    ]);
  });
});
