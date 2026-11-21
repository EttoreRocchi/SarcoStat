import type { ImageKind } from "../files.ts";
import { storedGrid } from "./exif.ts";

export const DECODE_OPTIONS: ImageBitmapOptions = { colorSpaceConversion: "none", premultiplyAlpha: "none" };

const MEDIA_TYPES: Readonly<Record<ImageKind, string>> = { png: "image/png", jpeg: "image/jpeg" };

export interface PngHeader {
  readonly bitDepth: number;
  readonly colourType: number;
}

export interface DecodedImage {
  readonly bitmap: ImageBitmap;
  readonly kind: ImageKind;
  readonly width: number;
  readonly height: number;
  readonly bitDepth: number;
  readonly orientation: number | null;
  readonly sixteenBit: boolean;
}

export class DecodeError extends Error {
  override readonly name = "DecodeError";
}

export function pngHeader(bytes: Uint8Array): PngHeader | null {
  if (bytes.length < 26 || String.fromCharCode(bytes[12] ?? 0, bytes[13] ?? 0, bytes[14] ?? 0, bytes[15] ?? 0) !== "IHDR") {
    return null;
  }
  return { bitDepth: bytes[24] ?? 0, colourType: bytes[25] ?? 0 };
}

function ownBuffer(bytes: Uint8Array): Uint8Array<ArrayBuffer> {
  return bytes.buffer instanceof ArrayBuffer ? new Uint8Array(bytes.buffer, bytes.byteOffset, bytes.byteLength) : Uint8Array.from(bytes);
}

export async function decodeImage(bytes: Uint8Array, kind: ImageKind, width: number, height: number): Promise<DecodedImage> {
  const grid = storedGrid(bytes, kind);
  const bitDepth = kind === "png" ? (pngHeader(bytes)?.bitDepth ?? 8) : 8;
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(new Blob([ownBuffer(grid.bytes)], { type: MEDIA_TYPES[kind] }), DECODE_OPTIONS);
  } catch (error) {
    throw new DecodeError(error instanceof Error ? error.message : String(error));
  }
  if (bitmap.width !== width || bitmap.height !== height) {
    const got = `${bitmap.width} x ${bitmap.height}`;
    bitmap.close();
    throw new DecodeError(`decoded ${got}, stored ${width} x ${height}`);
  }
  return { bitmap, kind, width, height, bitDepth, orientation: grid.orientation, sixteenBit: bitDepth === 16 };
}
