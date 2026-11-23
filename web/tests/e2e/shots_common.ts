import type { Locator, Page } from "@playwright/test";

import { expect, type Engine, type Recorder } from "./fixtures.ts";
import { openImage, READY } from "./workspace_view.ts";

export const REACH_TIMEOUT_MS = 5_000;
export const BOOT_TIMEOUT_MS = 30_000;

export const IMAGE = "grey_64x48.png";
export const GUARD_CLICKS: readonly (readonly [number, number])[] = [
  [10, 5],
  [10, 40],
];

export interface Reach {
  page: Page;
  engine: Engine;
  recorder: Recorder;
}

export interface ShotState {
  name: string;
  title: string;
  story: string;
  reach(context: Reach): Promise<string | null>;
}

export async function shown(locator: Locator, timeout = REACH_TIMEOUT_MS): Promise<boolean> {
  try {
    await locator.first().waitFor({ state: "visible", timeout });
    return true;
  } catch {
    return false;
  }
}

export async function attempt(what: string, step: () => Promise<unknown>): Promise<string | null> {
  try {
    await step();
    return null;
  } catch (error) {
    return `${what}: ${(error instanceof Error ? error.message : String(error)).split("\n")[0]}`;
  }
}

export async function openApp(context: Reach, hash: string): Promise<string | null> {
  await context.page.goto(`./${hash}`);
  await context.engine.ready();
  return (await shown(context.page.locator(READY), BOOT_TIMEOUT_MS)) ? null : "the status line never reached data-state=ready";
}

export async function inShape(context: Reach, withImage: boolean): Promise<string | null> {
  const missing = await openApp(context, "#/shape");
  if (missing !== null) {
    return missing;
  }
  if (!(await shown(context.page.locator('.shell[data-mode="shape"] [data-part="empty"]')))) {
    return "#/shape shows no shape-mode shell with its empty stage ([data-part=empty])";
  }
  return withImage ? attempt(`opening ${IMAGE} with O and the file chooser`, () => openImage(context.page, IMAGE)) : null;
}

export { expect };
