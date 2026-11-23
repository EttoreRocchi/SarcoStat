export type ImageKind = "png" | "jpeg";

export interface ImageHeader {
  readonly kind: ImageKind;
  readonly width: number;
  readonly height: number;
}

export const IMAGE_ACCEPT = "image/png,image/jpeg,.png,.jpg,.jpeg";
export const OPEN_ACCEPT = `${IMAGE_ACCEPT},application/json,.json`;

export function looksLikeJson(name: string, bytes: Uint8Array): boolean {
  if (/\.json$/i.test(name)) {
    return true;
  }
  for (const byte of bytes.subarray(0, 64)) {
    if (byte === 0x20 || byte === 0x09 || byte === 0x0a || byte === 0x0d || byte === 0xef || byte === 0xbb || byte === 0xbf) {
      continue;
    }
    return byte === 0x7b;
  }
  return false;
}

const PNG_SIGNATURE = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
const JPEG_SOF = new Set([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf]);
const JPEG_STANDALONE = new Set([0x01, 0xd0, 0xd1, 0xd2, 0xd3, 0xd4, 0xd5, 0xd6, 0xd7]);

function u16(bytes: Uint8Array, at: number): number {
  return ((bytes[at] ?? 0) << 8) | (bytes[at + 1] ?? 0);
}

function u32(bytes: Uint8Array, at: number): number {
  return (((bytes[at] ?? 0) << 24) >>> 0) + (((bytes[at + 1] ?? 0) << 16) | ((bytes[at + 2] ?? 0) << 8) | (bytes[at + 3] ?? 0));
}

function sized(kind: ImageKind, width: number, height: number): ImageHeader | null {
  return width >= 1 && height >= 1 ? { kind, width, height } : null;
}

function pngHeader(bytes: Uint8Array): ImageHeader | null {
  if (bytes.length < 24 || !PNG_SIGNATURE.every((value, i) => bytes[i] === value)) {
    return null;
  }
  const chunk = String.fromCharCode(bytes[12] ?? 0, bytes[13] ?? 0, bytes[14] ?? 0, bytes[15] ?? 0);
  return chunk === "IHDR" ? sized("png", u32(bytes, 16), u32(bytes, 20)) : null;
}

function jpegHeader(bytes: Uint8Array): ImageHeader | null {
  if (bytes.length < 4 || bytes[0] !== 0xff || bytes[1] !== 0xd8) {
    return null;
  }
  let at = 2;
  while (at + 1 < bytes.length) {
    if (bytes[at] !== 0xff) {
      return null;
    }
    let marker = bytes[at + 1] ?? 0;
    while (marker === 0xff && at + 2 < bytes.length) {
      at += 1;
      marker = bytes[at + 1] ?? 0;
    }
    at += 2;
    if (JPEG_STANDALONE.has(marker)) {
      continue;
    }
    if (marker === 0xd9 || marker === 0xda || at + 2 > bytes.length) {
      return null;
    }
    const length = u16(bytes, at);
    if (length < 2) {
      return null;
    }
    if (JPEG_SOF.has(marker)) {
      return at + 7 <= bytes.length ? sized("jpeg", u16(bytes, at + 5), u16(bytes, at + 3)) : null;
    }
    at += length;
  }
  return null;
}

export function readImageHeader(bytes: Uint8Array): ImageHeader | null {
  return pngHeader(bytes) ?? jpegHeader(bytes);
}

export interface FilePicker {
  pick(accept: string): Promise<File | null>;
}

export function createFilePicker(doc: Document, host: HTMLElement): FilePicker {
  const input = doc.createElement("input");
  input.type = "file";
  input.hidden = true;
  input.tabIndex = -1;
  input.dataset.part = "file-input";
  host.append(input);
  let settle: ((file: File | null) => void) | null = null;
  const finish = (file: File | null): void => {
    const done = settle;
    settle = null;
    done?.(file);
  };
  input.addEventListener("change", () => finish(input.files?.[0] ?? null));
  input.addEventListener("cancel", () => finish(null));
  return {
    pick(accept) {
      finish(null);
      input.accept = accept;
      input.value = "";
      return new Promise((resolve) => {
        settle = resolve;
        input.click();
      });
    },
  };
}

export function downloadText(doc: Document, fileName: string, text: string, type = "application/json"): void {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = doc.createElement("a");
  link.href = url;
  link.download = fileName;
  link.hidden = true;
  doc.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
