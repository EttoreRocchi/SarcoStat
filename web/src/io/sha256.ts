export async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const view = bytes.buffer instanceof ArrayBuffer ? new Uint8Array(bytes.buffer, bytes.byteOffset, bytes.byteLength) : Uint8Array.from(bytes);
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", view));
  return Array.from(digest, (value) => value.toString(16).padStart(2, "0")).join("");
}
