import type { ImageKind } from "../files.ts";

export const ORIENTATION_TAG = 0x0112;

const EXIF_HEADER = [0x45, 0x78, 0x69, 0x66, 0x00, 0x00];
const PNG_SIGNATURE_LENGTH = 8;
const JPEG_STANDALONE = new Set([0x01, 0xd0, 0xd1, 0xd2, 0xd3, 0xd4, 0xd5, 0xd6, 0xd7]);
const SHORT = 3;

export interface OrientationEntry {
  readonly at: number;
  readonly little: boolean;
  readonly value: number;
}

export interface StoredGrid {
  readonly bytes: Uint8Array;
  readonly orientation: number | null;
  readonly changed: boolean;
}

function u16(bytes: Uint8Array, at: number, little: boolean): number {
  const a = bytes[at] ?? 0;
  const b = bytes[at + 1] ?? 0;
  return little ? a | (b << 8) : (a << 8) | b;
}

function u32(bytes: Uint8Array, at: number, little: boolean): number {
  const hi = u16(bytes, little ? at + 2 : at, little);
  const lo = u16(bytes, little ? at : at + 2, little);
  return hi * 0x10000 + lo;
}

export function tiffOrientation(bytes: Uint8Array, start: number, end: number): OrientationEntry | null {
  if (start + 8 > end) {
    return null;
  }
  const order = String.fromCharCode(bytes[start] ?? 0, bytes[start + 1] ?? 0);
  if (order !== "II" && order !== "MM") {
    return null;
  }
  const little = order === "II";
  if (u16(bytes, start + 2, little) !== 42) {
    return null;
  }
  const ifd = start + u32(bytes, start + 4, little);
  if (ifd + 2 > end) {
    return null;
  }
  const count = u16(bytes, ifd, little);
  for (let index = 0; index < count; index += 1) {
    const entry = ifd + 2 + index * 12;
    if (entry + 12 > end) {
      return null;
    }
    if (u16(bytes, entry, little) === ORIENTATION_TAG && u16(bytes, entry + 2, little) === SHORT && u32(bytes, entry + 4, little) === 1) {
      return { at: entry + 8, little, value: u16(bytes, entry + 8, little) };
    }
  }
  return null;
}

export function jpegOrientations(bytes: Uint8Array): OrientationEntry[] {
  const out: OrientationEntry[] = [];
  if (bytes.length < 4 || bytes[0] !== 0xff || bytes[1] !== 0xd8) {
    return out;
  }
  let at = 2;
  while (at + 3 < bytes.length) {
    if (bytes[at] !== 0xff) {
      return out;
    }
    const marker = bytes[at + 1] ?? 0;
    if (marker === 0xff) {
      at += 1;
      continue;
    }
    if (JPEG_STANDALONE.has(marker)) {
      at += 2;
      continue;
    }
    if (marker === 0xd9 || marker === 0xda) {
      return out;
    }
    const length = u16(bytes, at + 2, false);
    const end = Math.min(at + 2 + length, bytes.length);
    if (length < 2) {
      return out;
    }
    const data = at + 4;
    if (marker === 0xe1 && EXIF_HEADER.every((value, i) => bytes[data + i] === value)) {
      const entry = tiffOrientation(bytes, data + EXIF_HEADER.length, end);
      if (entry !== null) {
        out.push(entry);
      }
    }
    at = end;
  }
  return out;
}

export function pngExifChunks(bytes: Uint8Array): { start: number; end: number; orientation: number | null }[] {
  const out: { start: number; end: number; orientation: number | null }[] = [];
  let at = PNG_SIGNATURE_LENGTH;
  while (at + 12 <= bytes.length) {
    const length = u32(bytes, at, false);
    const type = String.fromCharCode(bytes[at + 4] ?? 0, bytes[at + 5] ?? 0, bytes[at + 6] ?? 0, bytes[at + 7] ?? 0);
    const end = at + 12 + length;
    if (end > bytes.length) {
      return out;
    }
    if (type === "eXIf") {
      out.push({ start: at, end, orientation: tiffOrientation(bytes, at + 8, at + 8 + length)?.value ?? null });
    }
    if (type === "IEND") {
      return out;
    }
    at = end;
  }
  return out;
}

export function storedGrid(bytes: Uint8Array, kind: ImageKind): StoredGrid {
  if (kind === "jpeg") {
    const entries = jpegOrientations(bytes);
    const orientation = entries[0]?.value ?? null;
    if (entries.every((entry) => entry.value === 1)) {
      return { bytes, orientation, changed: false };
    }
    const copy = bytes.slice();
    for (const entry of entries) {
      copy[entry.at] = entry.little ? 1 : 0;
      copy[entry.at + 1] = entry.little ? 0 : 1;
    }
    return { bytes: copy, orientation, changed: true };
  }
  const chunks = pngExifChunks(bytes);
  if (chunks.length === 0) {
    return { bytes, orientation: null, changed: false };
  }
  const parts: Uint8Array[] = [];
  let from = 0;
  for (const chunk of chunks) {
    parts.push(bytes.subarray(from, chunk.start));
    from = chunk.end;
  }
  parts.push(bytes.subarray(from));
  const copy = new Uint8Array(parts.reduce((sum, part) => sum + part.length, 0));
  let offset = 0;
  for (const part of parts) {
    copy.set(part, offset);
    offset += part.length;
  }
  return { bytes: copy, orientation: chunks[0]?.orientation ?? null, changed: true };
}
