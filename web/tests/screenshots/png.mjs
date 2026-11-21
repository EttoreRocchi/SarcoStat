import zlib from "node:zlib";

export const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const CHANNELS = new Map([
  [0, 1],
  [2, 3],
  [4, 2],
  [6, 4],
]);

function chunk(type, data) {
  const head = Buffer.alloc(8);
  head.writeUInt32BE(data.length, 0);
  head.write(type, 4, "latin1");
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(zlib.crc32(Buffer.concat([head.subarray(4), data])) >>> 0, 0);
  return Buffer.concat([head, data, crc]);
}

function paeth(a, b, c) {
  const p = a + b - c;
  const pa = Math.abs(p - a);
  const pb = Math.abs(p - b);
  const pc = Math.abs(p - c);
  if (pa <= pb && pa <= pc) {
    return a;
  }
  return pb <= pc ? b : c;
}

export function decodePng(input) {
  const bytes = Buffer.isBuffer(input) ? input : Buffer.from(input);
  if (bytes.length < 8 || !bytes.subarray(0, 8).equals(PNG_SIGNATURE)) {
    throw new Error("not a PNG file");
  }
  let offset = 8;
  let header = null;
  const idat = [];
  let ended = false;
  while (offset + 12 <= bytes.length) {
    const length = bytes.readUInt32BE(offset);
    const type = bytes.toString("latin1", offset + 4, offset + 8);
    const end = offset + 12 + length;
    if (end > bytes.length) {
      throw new Error(`PNG chunk ${type} runs past the end of the file`);
    }
    const data = bytes.subarray(offset + 8, offset + 8 + length);
    const crc = bytes.readUInt32BE(offset + 8 + length);
    if ((zlib.crc32(bytes.subarray(offset + 4, offset + 8 + length)) >>> 0) !== crc) {
      throw new Error(`PNG chunk ${type} has a bad CRC`);
    }
    if (type === "IHDR") {
      header = {
        width: data.readUInt32BE(0),
        height: data.readUInt32BE(4),
        bitDepth: data[8],
        colorType: data[9],
        interlace: data[12],
      };
    } else if (type === "IDAT") {
      idat.push(data);
    } else if (type === "IEND") {
      ended = true;
      break;
    }
    offset = end;
  }
  if (header === null || !ended) {
    throw new Error("PNG without IHDR or IEND");
  }
  const channels = CHANNELS.get(header.colorType);
  if (header.bitDepth !== 8 || channels === undefined || header.interlace !== 0) {
    throw new Error(`PNG with bit depth ${header.bitDepth}, colour type ${header.colorType}, interlace ${header.interlace} is not supported (8-bit grey, grey+alpha, RGB or RGBA, not interlaced)`);
  }
  const { width, height } = header;
  const stride = width * channels;
  const raw = zlib.inflateSync(Buffer.concat(idat));
  if (raw.length !== height * (stride + 1)) {
    throw new Error(`PNG image data has ${raw.length} bytes, expected ${height * (stride + 1)}`);
  }
  const pixels = Buffer.alloc(height * stride);
  for (let y = 0; y < height; y += 1) {
    const filter = raw[y * (stride + 1)];
    const line = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1));
    const row = y * stride;
    const prev = row - stride;
    for (let x = 0; x < stride; x += 1) {
      const a = x >= channels ? pixels[row + x - channels] : 0;
      const b = y > 0 ? pixels[prev + x] : 0;
      const c = x >= channels && y > 0 ? pixels[prev + x - channels] : 0;
      let value;
      switch (filter) {
        case 0:
          value = line[x];
          break;
        case 1:
          value = line[x] + a;
          break;
        case 2:
          value = line[x] + b;
          break;
        case 3:
          value = line[x] + ((a + b) >> 1);
          break;
        case 4:
          value = line[x] + paeth(a, b, c);
          break;
        default:
          throw new Error(`PNG row ${y} has the unknown filter ${filter}`);
      }
      pixels[row + x] = value & 0xff;
    }
  }
  const rgba = Buffer.alloc(width * height * 4);
  for (let i = 0, j = 0; i < width * height; i += 1, j += channels) {
    const o = i * 4;
    if (channels === 1 || channels === 2) {
      rgba[o] = pixels[j];
      rgba[o + 1] = pixels[j];
      rgba[o + 2] = pixels[j];
      rgba[o + 3] = channels === 2 ? pixels[j + 1] : 255;
    } else {
      rgba[o] = pixels[j];
      rgba[o + 1] = pixels[j + 1];
      rgba[o + 2] = pixels[j + 2];
      rgba[o + 3] = channels === 4 ? pixels[j + 3] : 255;
    }
  }
  return { width, height, data: rgba };
}

export function encodePng(image) {
  const { width, height, data } = image;
  if (data.length !== width * height * 4) {
    throw new Error(`RGBA data has ${data.length} bytes, expected ${width * height * 4}`);
  }
  const stride = width * 4;
  const raw = Buffer.alloc(height * (stride + 1));
  for (let y = 0; y < height; y += 1) {
    raw[y * (stride + 1)] = 0;
    Buffer.from(data.buffer, data.byteOffset + y * stride, stride).copy(raw, y * (stride + 1) + 1);
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;
  ihdr[9] = 6;
  return Buffer.concat([PNG_SIGNATURE, chunk("IHDR", ihdr), chunk("IDAT", zlib.deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]);
}

export function diffImages(a, b) {
  if (a.width !== b.width || a.height !== b.height) {
    return { sameSize: false, width: b.width, height: b.height, differing: Math.max(a.width * a.height, b.width * b.height), diff: null };
  }
  const { width, height } = b;
  const out = Buffer.alloc(width * height * 4);
  let differing = 0;
  for (let o = 0; o < out.length; o += 4) {
    const same = a.data[o] === b.data[o] && a.data[o + 1] === b.data[o + 1] && a.data[o + 2] === b.data[o + 2] && a.data[o + 3] === b.data[o + 3];
    if (same) {
      const grey = Math.round((b.data[o] * 0.299 + b.data[o + 1] * 0.587 + b.data[o + 2] * 0.114) / 3 + 170);
      out[o] = grey;
      out[o + 1] = grey;
      out[o + 2] = grey;
    } else {
      differing += 1;
      out[o] = 255;
      out[o + 1] = 0;
      out[o + 2] = 0;
    }
    out[o + 3] = 255;
  }
  return { sameSize: true, width, height, differing, diff: { width, height, data: out } };
}

export function comparePngBuffers(reference, actual) {
  const identicalBytes = Buffer.compare(Buffer.from(reference), Buffer.from(actual)) === 0;
  const a = decodePng(reference);
  const b = decodePng(actual);
  const result = diffImages(a, b);
  return {
    identicalBytes,
    sameSize: result.sameSize,
    width: b.width,
    height: b.height,
    referenceWidth: a.width,
    referenceHeight: a.height,
    differing: result.differing,
    diffPng: result.differing > 0 && result.diff !== null ? encodePng(result.diff) : null,
  };
}
