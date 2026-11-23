import { createHash } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import zlib from "node:zlib";

import type { Page, Worker } from "@playwright/test";

import { WEB_DIR } from "../../scripts/lane.mjs";
import { expect } from "./fixtures.ts";
import { canvasView, type CanvasView } from "./workspace_view.ts";

export const KITS_DIR = path.join(WEB_DIR, "tests", "fixtures", "kits");
export const DEMO_KIT = path.join(KITS_DIR, "demo");
export const STUDY_CANVAS = "[data-part='study-canvas']";
export const STUDY_STAGE = "[data-part='study-stage']";
export const STUDY_OVERLAY = "[data-part='study-overlay']";
export const LOUPE_CANVAS = "[data-part='loupe-canvas']";
export const AUTOSAVE = "[data-part='autosave']";
export const READY = '.statusline[data-state="ready"]';
export const BOOT_TIMEOUT_MS = 30_000;
export const CLICK_RESOLUTION = 1 / 1024;
export const TRANSFORMS = ["identity", "rot90", "rot180", "rot270", "flip_h", "flip_v", "transpose", "anti_transpose"] as const;
export type Transform = (typeof TRANSFORMS)[number];

export interface ManifestItem {
  item_id: string;
  type: "shape" | "chain" | "pair";
  kit_file: string;
  kit_sha256: string;
  display_transform: Transform;
  kit_crop_offset: [number, number];
  kit_crop_size: [number, number];
  roi: [number, number, number, number];
}

export interface Manifest {
  study_id: string;
  annotator_id: string;
  session_id: string;
  items: ManifestItem[];
}

export function readManifest(kit: string): Manifest {
  return JSON.parse(fs.readFileSync(path.join(kit, "manifest.json"), "utf8")) as Manifest;
}

export function copyKit(source: string, label: string): string {
  const dest = fs.mkdtempSync(path.join(os.tmpdir(), `sarcostat-kit-${label}-`));
  const target = path.join(dest, path.basename(source));
  fs.cpSync(source, target, { recursive: true });
  return target;
}

export function displaySize(item: ManifestItem): [number, number] {
  const [w, h] = item.kit_crop_size;
  return ["rot90", "rot270", "transpose", "anti_transpose"].includes(item.display_transform) ? [h, w] : [w, h];
}

function forward(name: Transform, x: number, y: number, w: number, h: number): [number, number] {
  switch (name) {
    case "identity":
      return [x, y];
    case "rot90":
      return [y, w - 1 - x];
    case "rot180":
      return [w - 1 - x, h - 1 - y];
    case "rot270":
      return [h - 1 - y, x];
    case "flip_h":
      return [w - 1 - x, y];
    case "flip_v":
      return [x, h - 1 - y];
    case "transpose":
      return [y, x];
    case "anti_transpose":
      return [h - 1 - y, w - 1 - x];
  }
}

const INVERSE: Record<Transform, Transform> = {
  identity: "identity",
  rot90: "rot270",
  rot180: "rot180",
  rot270: "rot90",
  flip_h: "flip_h",
  flip_v: "flip_v",
  transpose: "transpose",
  anti_transpose: "anti_transpose",
};

export function quantize(value: number): number {
  const scaled = value / CLICK_RESOLUTION;
  const floor = Math.floor(scaled);
  const diff = scaled - floor;
  const rounded = diff > 0.5 ? floor + 1 : diff < 0.5 ? floor : floor % 2 === 0 ? floor : floor + 1;
  return rounded * CLICK_RESOLUTION;
}

export function toOriginal(item: ManifestItem, point: [number, number]): [number, number] {
  const [w, h] = displaySize(item);
  const [cx, cy] = forward(INVERSE[item.display_transform], point[0], point[1], w, h);
  return [cx + item.kit_crop_offset[0], cy + item.kit_crop_offset[1]];
}

export function toDisplay(item: ManifestItem, point: [number, number]): [number, number] {
  const [w, h] = item.kit_crop_size;
  return forward(item.display_transform, point[0] - item.kit_crop_offset[0], point[1] - item.kit_crop_offset[1], w, h);
}

export async function enterStudy(page: Page): Promise<void> {
  await page.goto("./#/");
  await expect(page.locator(READY)).toBeVisible({ timeout: BOOT_TIMEOUT_MS });
  await page.locator(".mode-card--study").click();
  await expect(page.locator("[data-part='study-start']")).toBeVisible();
}

export async function checkBrowser(page: Page): Promise<void> {
  await page.locator("[data-part='study-start'] [data-action='check']").click();
  await expect(page.locator("[data-part='study-start'] [data-action='open-kit']")).not.toHaveAttribute("aria-disabled", "true", { timeout: BOOT_TIMEOUT_MS });
}

export async function chooseKit(page: Page, kit: string): Promise<void> {
  const chooser = page.waitForEvent("filechooser");
  await page.locator("[data-part='study-start'] [data-action='open-kit']").click();
  await (await chooser).setFiles(kit);
}

export async function openKit(page: Page, kit: string): Promise<void> {
  await chooseKit(page, kit);
  await expect(page.locator("[data-part='identity']")).toBeVisible({ timeout: BOOT_TIMEOUT_MS });
}

export async function startSession(page: Page, kit: string): Promise<void> {
  await enterStudy(page);
  await checkBrowser(page);
  await openKit(page, kit);
  await confirmIdentity(page);
}

export async function confirmIdentity(page: Page): Promise<void> {
  await page.locator("[data-part='identity'] [data-action='identity-yes']").click();
  await expect(page.locator(STUDY_CANVAS)).toHaveAttribute("data-zoom", /.+/, { timeout: BOOT_TIMEOUT_MS });
}

export function studyView(page: Page): Promise<CanvasView> {
  return canvasView(page, STUDY_CANVAS);
}

export function pixelCentre(view: CanvasView, x: number, y: number): { x: number; y: number } {
  return { x: view.left + view.offsetX + view.zoom * (x + 0.5), y: view.top + view.offsetY + view.zoom * (y + 0.5) };
}

export async function seq(page: Page): Promise<number> {
  return Number(await page.locator(AUTOSAVE).getAttribute("data-seq"));
}

export async function clickDisplay(page: Page, x: number, y: number): Promise<void> {
  const before = await seq(page);
  const view = await studyView(page);
  const at = pixelCentre(view, x, y);
  await page.mouse.click(at.x, at.y);
  await expect.poll(() => seq(page)).toBeGreaterThan(before);
}

export async function zoomStep(page: Page): Promise<number> {
  return Number(await page.locator(STUDY_STAGE).getAttribute("data-zoom-step"));
}

export async function wheelTo(page: Page, step: number): Promise<CanvasView> {
  const view = await studyView(page);
  const centre = { x: view.left + view.width / 2, y: view.top + view.height / 2 };
  await page.mouse.move(centre.x, centre.y);
  for (let guard = 0; guard < 32 && (await zoomStep(page)) !== step; guard += 1) {
    await page.mouse.wheel(0, (await zoomStep(page)) < step ? -60 : 60);
    await page.waitForTimeout(20);
  }
  expect(await zoomStep(page)).toBe(step);
  return studyView(page);
}

export async function pressAction(page: Page, action: string): Promise<void> {
  const before = await seq(page);
  await page.locator(`[data-part='study-dock'] [data-action='${action}']`).click();
  await expect.poll(() => seq(page)).toBeGreaterThan(before);
}

export function shapeClicks(item: ManifestItem): [number, number][] {
  const [w, h] = displaySize(item);
  const cx = Math.round(w / 2);
  const cy = Math.round(h / 2);
  return [
    [cx - 40, cy - 30],
    [cx + 40, cy - 30],
    [cx - 40, cy],
    [cx + 40, cy],
    [cx - 40, cy + 30],
    [cx + 40, cy + 30],
  ];
}

export function chainClicks(item: ManifestItem, lines: number): [number, number][] {
  const [w, h] = displaySize(item);
  const out: [number, number][] = [];
  for (let k = 0; k < lines; k += 1) {
    const x = Math.round(w * 0.3 + k * 25);
    out.push([x, Math.round(h * 0.4)], [x, Math.round(h * 0.6)]);
  }
  return out;
}

export async function completeItem(page: Page, item: ManifestItem): Promise<void> {
  if (item.type === "shape") {
    for (const [x, y] of shapeClicks(item)) {
      await clickDisplay(page, x, y);
    }
  } else if (item.type === "chain") {
    for (const [x, y] of chainClicks(item, 2)) {
      await clickDisplay(page, x, y);
    }
  } else {
    for (const [x, y] of chainClicks(item, 2)) {
      await clickDisplay(page, x, y);
    }
    await pressAction(page, "next-myofibril");
    for (const [x, y] of chainClicks(item, 2)) {
      await clickDisplay(page, x, y + 40);
    }
  }
  await pressAction(page, "next");
}

export interface DownloadedFile {
  name: string;
  bytes: Buffer;
}

export async function download(page: Page, run: () => Promise<unknown>): Promise<DownloadedFile> {
  const waiting = page.waitForEvent("download");
  await run();
  const file = await waiting;
  const target = await file.path();
  return { name: file.suggestedFilename(), bytes: fs.readFileSync(target) };
}

export async function finishSession(page: Page, kit: string, from = 0): Promise<DownloadedFile> {
  const items = readManifest(kit).items;
  for (const item of items.slice(from, -1)) {
    await completeItem(page, item);
  }
  const last = items[items.length - 1] as ManifestItem;
  return download(page, () => completeItem(page, last));
}

export interface GreyImage {
  width: number;
  height: number;
  pixels: Uint8Array;
}

function paeth(a: number, b: number, c: number): number {
  const p = a + b - c;
  const pa = Math.abs(p - a);
  const pb = Math.abs(p - b);
  const pc = Math.abs(p - c);
  return pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
}

export function decodeGreyPng(bytes: Buffer): GreyImage {
  let at = 8;
  let width = 0;
  let height = 0;
  const idat: Buffer[] = [];
  while (at < bytes.length) {
    const length = bytes.readUInt32BE(at);
    const kind = bytes.toString("latin1", at + 4, at + 8);
    const body = bytes.subarray(at + 8, at + 8 + length);
    if (kind === "IHDR") {
      width = body.readUInt32BE(0);
      height = body.readUInt32BE(4);
      if (body[8] !== 8 || body[9] !== 0 || body[12] !== 0) {
        throw new Error("not a plain 8-bit greyscale PNG");
      }
    } else if (kind === "IDAT") {
      idat.push(body);
    }
    at += 12 + length;
  }
  const raw = zlib.inflateSync(Buffer.concat(idat));
  const pixels = new Uint8Array(width * height);
  for (let y = 0; y < height; y += 1) {
    const filter = raw[y * (width + 1)];
    for (let x = 0; x < width; x += 1) {
      const value = raw[y * (width + 1) + 1 + x] ?? 0;
      const left = x > 0 ? (pixels[y * width + x - 1] ?? 0) : 0;
      const up = y > 0 ? (pixels[(y - 1) * width + x] ?? 0) : 0;
      const corner = x > 0 && y > 0 ? (pixels[(y - 1) * width + x - 1] ?? 0) : 0;
      const predicted = filter === 0 ? 0 : filter === 1 ? left : filter === 2 ? up : filter === 3 ? Math.floor((left + up) / 2) : paeth(left, up, corner);
      pixels[y * width + x] = (value + predicted) & 255;
    }
  }
  return { width, height, pixels };
}

export function greyRgba(image: GreyImage): number[] {
  const out: number[] = [];
  for (const value of image.pixels) {
    out.push(value, value, value, 255);
  }
  return out;
}

export function installReadSpy(): void {
  const reads: string[] = [];
  Object.defineProperty(globalThis, "__sarcostatTestReads", { value: reads, configurable: false, writable: false });
  const name = (blob: Blob): string => {
    if (typeof File !== "undefined" && blob instanceof File) {
      return (blob as File & { webkitRelativePath?: string }).webkitRelativePath || blob.name;
    }
    return "(blob)";
  };
  const proto = Blob.prototype as unknown as Record<string, (...args: unknown[]) => unknown>;
  for (const method of ["arrayBuffer", "text", "stream", "bytes", "slice"]) {
    const original = proto[method];
    if (typeof original !== "function") {
      continue;
    }
    proto[method] = function spied(this: Blob, ...args: unknown[]): unknown {
      reads.push(`${method} ${name(this)}`);
      return original.apply(this, args);
    };
  }
  const readers = FileReader.prototype as unknown as Record<string, (...args: unknown[]) => unknown>;
  for (const method of ["readAsArrayBuffer", "readAsText", "readAsDataURL", "readAsBinaryString"]) {
    const original = readers[method];
    if (typeof original === "function") {
      readers[method] = function spied(this: FileReader, blob: unknown, ...rest: unknown[]): unknown {
        reads.push(`${method} ${blob instanceof Blob ? name(blob) : "?"}`);
        return original.call(this, blob, ...rest);
      };
    }
  }
}

export function kitReads(page: Page): Promise<string[]> {
  return page.evaluate(() => ((globalThis as unknown as { __sarcostatTestReads?: string[] }).__sarcostatTestReads ?? []).filter((entry) => !entry.endsWith("(blob)")));
}

export interface HoldState {
  on: boolean;
  held: number;
}

export function installResponseHold(): HoldState {
  const scope = globalThis as unknown as Record<string, unknown> & { postMessage(message: unknown, transfer?: Transferable[]): void };
  const present = scope.__sarcostatTestHold as { on: boolean; queue: unknown[] } | undefined;
  if (present !== undefined) {
    return { on: present.on, held: present.queue.length };
  }
  const queue: [unknown, Transferable[]][] = [];
  const state = { on: false, queue };
  const native = scope.postMessage.bind(scope);
  scope.postMessage = (message: unknown, transfer?: Transferable[]): void => {
    const kind = (message as { kind?: unknown } | null)?.kind;
    if (state.on && (kind === "response" || kind === "modules")) {
      queue.push([message, transfer ?? []]);
      return;
    }
    native(message, transfer ?? []);
  };
  Object.defineProperty(scope, "__sarcostatTestHold", { value: state, configurable: false, writable: false });
  Object.defineProperty(scope, "__sarcostatTestRelease", {
    value: () => {
      state.on = false;
      for (const [message, transfer] of queue.splice(0)) {
        native(message, transfer);
      }
    },
    configurable: false,
    writable: false,
  });
  return { on: false, held: 0 };
}

export function holdResponses(worker: Worker, on: boolean): Promise<void> {
  return worker.evaluate((value) => {
    const scope = globalThis as unknown as { __sarcostatTestHold: { on: boolean }; __sarcostatTestRelease(): void };
    if (value) {
      scope.__sarcostatTestHold.on = true;
    } else {
      scope.__sarcostatTestRelease();
    }
  }, on);
}

export function watchWorkers(page: Page): Worker[] {
  const seen: Worker[] = [];
  page.on("worker", (worker) => {
    seen.push(worker);
  });
  return seen;
}

export async function studyWorker(seen: Worker[], timeoutMs = BOOT_TIMEOUT_MS): Promise<Worker> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const engines = seen.filter((worker) => /py\.worker/.test(worker.url()));
    if (engines.length >= 2) {
      const worker = engines[engines.length - 1] as Worker;
      await worker.evaluate(installResponseHold);
      return worker;
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error("no study worker beside the annotation worker");
}

export interface ProbedDown {
  clientX: number;
  clientY: number;
  part: string | null;
  view: CanvasView | null;
}

export function installStudyPointerProbe(): void {
  const log: unknown[] = [];
  Object.defineProperty(globalThis, "__sarcostatTestPointer", { value: log, configurable: false, writable: false });
  window.addEventListener(
    "pointerdown",
    (event) => {
      const target = event.target as HTMLElement | null;
      const canvas = document.querySelector<HTMLCanvasElement>("[data-part='study-canvas']");
      let view: unknown = null;
      if (canvas !== null) {
        const rect = canvas.getBoundingClientRect();
        view = {
          left: rect.left,
          top: rect.top,
          width: rect.width,
          height: rect.height,
          zoom: Number(canvas.dataset.zoom),
          offsetX: Number(canvas.dataset.offsetX),
          offsetY: Number(canvas.dataset.offsetY),
          dpr: window.devicePixelRatio,
          backingWidth: canvas.width,
          backingHeight: canvas.height,
        };
      }
      log.push({ clientX: event.clientX, clientY: event.clientY, part: target?.dataset?.part ?? null, view });
    },
    { capture: true },
  );
}

export function probedDowns(page: Page, from = 0): Promise<ProbedDown[]> {
  return page.evaluate((at) => ((globalThis as unknown as { __sarcostatTestPointer: ProbedDown[] }).__sarcostatTestPointer ?? []).slice(at), from);
}

export interface StoredRecord {
  study_id: string;
  annotator_id: string;
  session_id: string;
  manifest_sha256: string;
  seq: number;
  status: "open" | "finished";
  build_version: string;
  source_digest: string;
  build_json_sha256: string;
  builds_seen: { version: string; build_json_sha256: string; source_digest: string }[];
  counts: { pauses: number; resumes: number; worker_restarts: number };
  autosave_bytes: number[];
  finished_bytes: number[] | null;
  finished_sha256: string | null;
  updated: string;
}

export interface StoredDatabase {
  version: number;
  stores: string[];
  records: StoredRecord[];
}

export function readStudyDb(page: Page): Promise<StoredDatabase | null> {
  return page.evaluate(async () => {
    const name = "sarcostat-study-v1";
    const known = await indexedDB.databases();
    if (!known.some((entry) => entry.name === name)) {
      return null;
    }
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const req = indexedDB.open(name);
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
    try {
      const stores = [...db.objectStoreNames];
      if (!stores.includes("sessions")) {
        return { version: db.version, stores, records: [] };
      }
      const all = await new Promise<Record<string, unknown>[]>((resolve, reject) => {
        const req = db.transaction("sessions", "readonly").objectStore("sessions").getAll();
        req.onsuccess = () => resolve(req.result as Record<string, unknown>[]);
        req.onerror = () => reject(req.error);
      });
      const plain = (value: unknown): unknown => (value instanceof Uint8Array ? [...value] : value);
      const records = all.map((entry) => Object.fromEntries(Object.entries(entry).map(([key, value]) => [key, plain(value)])));
      return { version: db.version, stores, records: records as unknown as StoredRecord[] };
    } finally {
      db.close();
    }
  });
}

export function installWallClock(offsetMs: number): void {
  const scope = globalThis as unknown as { __sarcostatTestWallOffset?: number };
  if (scope.__sarcostatTestWallOffset !== undefined) {
    return;
  }
  scope.__sarcostatTestWallOffset = offsetMs;
  const realNow = Date.now.bind(Date);
  Date.now = () => realNow() + (scope.__sarcostatTestWallOffset ?? 0);
}

export function setWallOffset(page: Page, offsetMs: number): Promise<void> {
  return page.evaluate((value) => {
    (globalThis as unknown as { __sarcostatTestWallOffset: number }).__sarcostatTestWallOffset = value;
  }, offsetMs);
}

export function installStorageLog(): void {
  const log: string[] = [];
  Object.defineProperty(globalThis, "__sarcostatTestStorageLog", { value: log, configurable: false, writable: false });
  const factory = IDBFactory.prototype;
  const open = factory.open;
  factory.open = function spied(this: IDBFactory, name: string, version?: number): IDBOpenDBRequest {
    log.push(`open ${name} ${version === undefined ? "-" : String(version)}`);
    return version === undefined ? open.call(this, name) : open.call(this, name, version);
  };
  const remove = factory.deleteDatabase;
  factory.deleteDatabase = function spied(this: IDBFactory, name: string): IDBOpenDBRequest {
    log.push(`delete ${name}`);
    return remove.call(this, name);
  };
  const transaction = IDBDatabase.prototype.transaction;
  IDBDatabase.prototype.transaction = function spied(this: IDBDatabase, ...args: Parameters<IDBDatabase["transaction"]>): IDBTransaction {
    const created = transaction.apply(this, args);
    if (args[1] === "readwrite") {
      created.addEventListener("complete", () => log.push(`complete ${this.name}`));
    }
    return created;
  } as IDBDatabase["transaction"];
  const click = HTMLAnchorElement.prototype.click;
  HTMLAnchorElement.prototype.click = function spied(this: HTMLAnchorElement): void {
    if (this.download !== "") {
      log.push(`download ${this.download}`);
      if ((globalThis as unknown as { __sarcostatTestDropDownloads?: boolean }).__sarcostatTestDropDownloads === true) {
        return;
      }
    }
    click.call(this);
  };
  const manager = (globalThis as unknown as { LockManager?: { prototype: { request: (...args: unknown[]) => Promise<unknown> } } }).LockManager;
  if (manager !== undefined) {
    const request = manager.prototype.request;
    manager.prototype.request = function spied(this: unknown, ...args: unknown[]): Promise<unknown> {
      log.push(`lock ${String(args[0])} ${JSON.stringify(typeof args[1] === "object" ? args[1] : {})}`);
      return request.apply(this, args);
    };
  }
}

export function storageLog(page: Page): Promise<string[]> {
  return page.evaluate(() => ((globalThis as unknown as { __sarcostatTestStorageLog?: string[] }).__sarcostatTestStorageLog ?? []).slice());
}

export function sha256(bytes: Uint8Array | number[]): string {
  return createHash("sha256").update(Uint8Array.from(bytes)).digest("hex");
}

export async function itemCount(page: Page): Promise<string> {
  return (await page.locator("[data-part='item-count']").textContent()) ?? "";
}
