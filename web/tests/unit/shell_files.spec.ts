import crypto from "node:crypto";
import zlib from "node:zlib";

import { describe, expect, it } from "vitest";

import { readImageHeader } from "../../src/io/files.ts";
import { sha256Hex } from "../../src/io/sha256.ts";

function chunk(type: string, data: Buffer): Buffer {
  const head = Buffer.alloc(8);
  head.writeUInt32BE(data.length, 0);
  head.write(type, 4, "ascii");
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(zlib.crc32(Buffer.concat([Buffer.from(type, "ascii"), data])) >>> 0, 0);
  return Buffer.concat([head, data, crc]);
}

function png(width: number, height: number): Uint8Array {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr.set([8, 0, 0, 0, 0], 8);
  const rows = Buffer.alloc((width + 1) * height);
  return new Uint8Array(
    Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk("IHDR", ihdr), chunk("IDAT", zlib.deflateSync(rows)), chunk("IEND", Buffer.alloc(0))]),
  );
}

function jpeg(width: number, height: number, sof = 0xc0): Uint8Array {
  const app0 = [0xff, 0xe0, 0x00, 0x10, 0x4a, 0x46, 0x49, 0x46, 0x00, 0x01, 0x01, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00];
  const exif = [0xff, 0xe1, 0x00, 0x08, 0x45, 0x78, 0x69, 0x66, 0x00, 0x00];
  const frame = [0xff, sof, 0x00, 0x0b, 0x08, height >> 8, height & 0xff, width >> 8, width & 0xff, 0x01, 0x01, 0x11, 0x00];
  return new Uint8Array([0xff, 0xd8, ...app0, ...exif, 0xff, 0xff, ...frame.slice(1), 0xff, 0xda, 0x00, 0x02, 0xff, 0xd9]);
}

describe("image headers", () => {
  it("read the stored size of a PNG from its IHDR", () => {
    expect(readImageHeader(png(64, 48))).toEqual({ kind: "png", width: 64, height: 48 });
    expect(readImageHeader(png(70000, 1))).toEqual({ kind: "png", width: 70000, height: 1 });
  });

  it("read the stored size of a baseline or progressive JPEG from its frame header, past APPn segments and fill bytes", () => {
    expect(readImageHeader(jpeg(2200, 1611))).toEqual({ kind: "jpeg", width: 2200, height: 1611 });
    expect(readImageHeader(jpeg(31, 17, 0xc2))).toEqual({ kind: "jpeg", width: 31, height: 17 });
  });

  it("refuse anything else", () => {
    expect(readImageHeader(new Uint8Array())).toBeNull();
    expect(readImageHeader(new TextEncoder().encode("{\"format\": \"sarcostat-annotations\"}"))).toBeNull();
    expect(readImageHeader(new Uint8Array([0x49, 0x49, 0x2a, 0x00, 8, 0, 0, 0]))).toBeNull();
    expect(readImageHeader(png(0, 5))).toBeNull();
    expect(readImageHeader(png(5, 4).slice(0, 20))).toBeNull();
    expect(readImageHeader(new Uint8Array([0xff, 0xd8, 0xff, 0xda, 0x00, 0x02]))).toBeNull();
  });
});

describe("SHA-256", () => {
  it("equals the digest of the same bytes in Node, also for a view into a larger buffer", async () => {
    const bytes = png(64, 48);
    const expected = crypto.createHash("sha256").update(bytes).digest("hex");
    expect(await sha256Hex(bytes)).toBe(expected);
    const padded = new Uint8Array(bytes.length + 10);
    padded.set(bytes, 5);
    expect(await sha256Hex(padded.subarray(5, 5 + bytes.length))).toBe(expected);
    expect(await sha256Hex(new Uint8Array())).toBe("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
  });
});
