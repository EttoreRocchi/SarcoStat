import fs from "node:fs";
import path from "node:path";

import type { Locator, Page } from "@playwright/test";

import { WEB_DIR } from "../../scripts/lane.mjs";
import { expect } from "./fixtures.ts";

export const READY = '.statusline[data-state="ready"]';
export const BOOT_TIMEOUT_MS = 30_000;
export const IMAGES_DIR = path.join(WEB_DIR, "tests", "fixtures", "images");
export const CANVAS = "[data-part='image-canvas']";
export const LOUPE_CANVAS = "[data-part='loupe-canvas']";
export const WORKSPACE = "[data-part='workspace']";
export const OVERLAY = "[data-part='overlay']";

export interface FixtureEntry {
  kind: "png" | "jpeg";
  mode: "L" | "RGB" | "I;16";
  width: number;
  height: number;
  bit_depth: number;
  channels: number;
  orientation: number | null;
  dtype: "u8" | "u16le";
  sha256: string;
  pixels: string;
  marked?: { x: number; y: number; value: number; background: number };
}

export interface FixtureManifest {
  format: string;
  format_version: number;
  pillow: string;
  images: Record<string, FixtureEntry>;
}

export function manifest(): FixtureManifest {
  return JSON.parse(fs.readFileSync(path.join(IMAGES_DIR, "manifest.json"), "utf8")) as FixtureManifest;
}

export function fixturePath(name: string): string {
  return path.join(IMAGES_DIR, name);
}

export function samples(entry: FixtureEntry): number[] {
  const raw = Buffer.from(entry.pixels, "base64");
  if (entry.dtype === "u16le") {
    const out: number[] = [];
    for (let at = 0; at + 1 < raw.length; at += 2) {
      out.push(raw.readUInt16LE(at));
    }
    return out;
  }
  return [...raw];
}

export function rgba8(entry: FixtureEntry): number[] {
  const values = samples(entry);
  const out: number[] = [];
  for (let index = 0; index < entry.width * entry.height; index += 1) {
    if (entry.channels === 3) {
      out.push(values[index * 3] ?? -1, values[index * 3 + 1] ?? -1, values[index * 3 + 2] ?? -1, 255);
    } else {
      const v = values[index] ?? -1;
      out.push(v, v, v, 255);
    }
  }
  return out;
}

export async function boot(page: Page): Promise<void> {
  await page.goto("./#/shape");
  await expect(page.locator(READY)).toBeVisible({ timeout: BOOT_TIMEOUT_MS });
}

export async function openImage(page: Page, name: string): Promise<void> {
  const chooser = page.waitForEvent("filechooser");
  await page.keyboard.press("o");
  await (await chooser).setFiles(fixturePath(name));
  await expect(page.locator(".topbar__name")).toHaveText(name);
  await expect(page.locator(WORKSPACE)).toHaveAttribute("data-state", "drawn");
}

export interface CanvasView {
  left: number;
  top: number;
  width: number;
  height: number;
  zoom: number;
  offsetX: number;
  offsetY: number;
  dpr: number;
  backingWidth: number;
  backingHeight: number;
}

export function canvasView(page: Page, selector = CANVAS): Promise<CanvasView> {
  return page.locator(selector).evaluate((canvas: HTMLCanvasElement) => {
    const rect = canvas.getBoundingClientRect();
    return {
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
  });
}

export function pixelCentre(view: CanvasView, x: number, y: number): { x: number; y: number } {
  return { x: view.left + view.offsetX + view.zoom * (x + 0.5), y: view.top + view.offsetY + view.zoom * (y + 0.5) };
}

export function referenceMapping(clientX: number, clientY: number, view: CanvasView): { x: number; y: number } {
  return { x: (clientX - view.left - view.offsetX) / view.zoom - 0.5, y: (clientY - view.top - view.offsetY) / view.zoom - 0.5 };
}

export async function zoomTo(page: Page, zoom: number): Promise<CanvasView> {
  for (let step = 0; step < 32; step += 1) {
    const view = await canvasView(page);
    if (view.zoom === zoom) {
      return view;
    }
    await page.keyboard.press(view.zoom < zoom ? "+" : "-");
  }
  const view = await canvasView(page);
  expect(view.zoom, `zoom ${zoom} reachable with + and -`).toBe(zoom);
  return view;
}

export async function clickPixel(page: Page, x: number, y: number): Promise<void> {
  const view = await canvasView(page);
  const at = pixelCentre(view, x, y);
  const hit = await page.evaluate(([px, py]) => (document.elementFromPoint(px, py) as HTMLElement | null)?.dataset.part ?? null, [at.x, at.y] as const);
  expect(hit, `the image canvas is the element under pixel ${x} ${y}`).toBe("image-canvas");
  await page.mouse.click(at.x, at.y);
}

export function pendingDots(page: Page): Locator {
  return page.locator(`${OVERLAY} .ov__item--pending .ov__dot`);
}

export interface Readback {
  checked: number;
  inside: number;
  mismatches: number;
  first: { x: number; y: number; got: number[]; want: number[] } | null;
}

export function readback(page: Page, selector: string, width: number, height: number, expected: number[], tolerance = 0): Promise<Readback> {
  return page.locator(selector).evaluate(
    (canvas: HTMLCanvasElement, args) => {
      const context = canvas.getContext("2d");
      if (context === null) {
        throw new Error("no 2d context");
      }
      const data = context.getImageData(0, 0, canvas.width, canvas.height).data;
      const dpr = canvas.width / canvas.getBoundingClientRect().width;
      const scale = Number(canvas.dataset.zoom) * dpr;
      const ox = Math.round(Number(canvas.dataset.offsetX) * dpr);
      const oy = Math.round(Number(canvas.dataset.offsetY) * dpr);
      let mismatches = 0;
      let inside = 0;
      let first: { x: number; y: number; got: number[]; want: number[] } | null = null;
      for (let y = 0; y < canvas.height; y += 1) {
        const iy = Math.floor((y + 0.5 - oy) / scale);
        for (let x = 0; x < canvas.width; x += 1) {
          const ix = Math.floor((x + 0.5 - ox) / scale);
          const within = ix >= 0 && iy >= 0 && ix < args.width && iy < args.height;
          const at = (y * canvas.width + x) * 4;
          const got = [data[at] ?? -1, data[at + 1] ?? -1, data[at + 2] ?? -1, data[at + 3] ?? -1];
          const base = (iy * args.width + ix) * 4;
          const want = within ? [args.expected[base] ?? -1, args.expected[base + 1] ?? -1, args.expected[base + 2] ?? -1, args.expected[base + 3] ?? -1] : [0, 0, 0, 0];
          if (within) {
            inside += 1;
          }
          if (got.some((value, i) => Math.abs(value - (want[i] ?? -1)) > (within ? args.tolerance : 0))) {
            mismatches += 1;
            first ??= { x, y, got, want };
          }
        }
      }
      return { checked: canvas.width * canvas.height, inside, mismatches, first };
    },
    { width, height, expected, tolerance },
  );
}
