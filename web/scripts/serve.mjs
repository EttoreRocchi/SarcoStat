import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

export const HOST = "127.0.0.1";

const MEDIA_TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".map": "application/json; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
  ".sha256": "text/plain; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".tif": "image/tiff",
  ".tiff": "image/tiff",
  ".woff2": "font/woff2",
  ".wasm": "application/wasm",
  ".whl": "application/octet-stream",
  ".zip": "application/zip",
  ".tar": "application/x-tar",
  ".data": "application/octet-stream",
};

export function mediaType(file) {
  return MEDIA_TYPES[path.extname(file).toLowerCase()] ?? "application/octet-stream";
}

function resolveInside(root, urlPath) {
  let decoded;
  try {
    decoded = decodeURIComponent(urlPath);
  } catch {
    return null;
  }
  if (decoded.includes("\0")) {
    return null;
  }
  const target = path.resolve(root, `.${path.posix.normalize(`/${decoded}`)}`);
  const rel = path.relative(root, target);
  if (rel.startsWith("..") || path.isAbsolute(rel)) {
    return null;
  }
  return target;
}

function send(res, status, headers, body) {
  res.writeHead(status, { "cache-control": "no-store", ...headers });
  res.end(body);
}

export function createHandler(root) {
  const absRoot = path.resolve(root);
  return function handle(req, res) {
    if (req.method !== "GET" && req.method !== "HEAD") {
      send(res, 405, { allow: "GET, HEAD", "content-type": "text/plain; charset=utf-8" }, "method not allowed\n");
      return;
    }
    const url = new URL(req.url ?? "/", `http://${HOST}`);
    let target = resolveInside(absRoot, url.pathname);
    if (target === null) {
      send(res, 400, { "content-type": "text/plain; charset=utf-8" }, "bad path\n");
      return;
    }
    let stat;
    try {
      stat = fs.statSync(target);
    } catch {
      send(res, 404, { "content-type": "text/plain; charset=utf-8" }, "not found\n");
      return;
    }
    if (stat.isDirectory()) {
      if (!url.pathname.endsWith("/")) {
        send(res, 301, { location: `${url.pathname}/${url.search}` }, "");
        return;
      }
      target = path.join(target, "index.html");
      try {
        stat = fs.statSync(target);
      } catch {
        send(res, 404, { "content-type": "text/plain; charset=utf-8" }, "not found\n");
        return;
      }
    }
    if (!stat.isFile()) {
      send(res, 404, { "content-type": "text/plain; charset=utf-8" }, "not found\n");
      return;
    }
    const headers = { "content-type": mediaType(target), "content-length": String(stat.size) };
    if (req.method === "HEAD") {
      send(res, 200, headers, "");
      return;
    }
    res.writeHead(200, { "cache-control": "no-store", ...headers });
    fs.createReadStream(target).pipe(res);
  };
}

export function startServer({ root, port = 0 }) {
  const absRoot = path.resolve(root);
  if (!fs.existsSync(absRoot) || !fs.statSync(absRoot).isDirectory()) {
    return Promise.reject(new Error(`serve: root ${absRoot} is not a folder`));
  }
  const server = http.createServer(createHandler(absRoot));
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen({ host: HOST, port: Number(port), exclusive: true }, () => {
      const address = server.address();
      resolve({ server, root: absRoot, host: address.address, port: address.port, url: `http://${HOST}:${address.port}/` });
    });
  });
}

async function main() {
  const { values } = parseArgs({
    options: { root: { type: "string" }, port: { type: "string" } },
    strict: true,
  });
  if (!values.root) {
    console.error("Usage: PORT=0 node scripts/serve.mjs --root <folder> [--port <n>]");
    process.exit(2);
  }
  const port = Number(values.port ?? process.env.PORT ?? 0);
  if (!Number.isInteger(port) || port < 0 || port > 65535) {
    console.error(`serve: bad port ${values.port ?? process.env.PORT}`);
    process.exit(2);
  }
  const started = await startServer({ root: values.root, port });
  console.log(`serve: ${started.url} root ${started.root}`);
  const stop = () => started.server.close(() => process.exit(0));
  process.on("SIGINT", stop);
  process.on("SIGTERM", stop);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await main();
}
