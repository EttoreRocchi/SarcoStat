import { en } from "../../strings/en.ts";
import { fill } from "../../strings/format.ts";
import { el } from "../dom.ts";
import { createImageCanvas } from "./canvas.ts";
import { snap, type Point, type Size, type View } from "./viewport.ts";

export const LOUPE_FACTOR = 3;
export const LOUPE_SIZE = 116;
export const LOUPE_OFFSET: Point = { x: 94, y: -76 };
export const LOUPE_LABEL_SPACE = 20;

export function loupeView(imagePoint: Point, view: View, dpr: number, factor = LOUPE_FACTOR, size = LOUPE_SIZE): View {
  const zoom = view.zoom * factor;
  const half = size / 2;
  return { zoom, offsetX: snap(half - zoom * imagePoint.x, dpr), offsetY: snap(half - zoom * imagePoint.y, dpr) };
}

export function loupePosition(cursor: Point, stage: Size, size = LOUPE_SIZE, offset = LOUPE_OFFSET): Point {
  const half = size / 2;
  let x = cursor.x + offset.x;
  let y = cursor.y + offset.y;
  if (x + half > stage.width) {
    x = cursor.x - offset.x;
  }
  if (y - half < 0) {
    y = cursor.y - offset.y;
  }
  if (y + half + LOUPE_LABEL_SPACE > stage.height) {
    y = stage.height - half - LOUPE_LABEL_SPACE;
  }
  return { x: Math.max(half, x) - half, y: Math.max(half, y) - half };
}

export interface Loupe {
  readonly element: HTMLElement;
  readonly canvas: HTMLCanvasElement;
  show(source: CanvasImageSource & Size, imagePoint: Point, cursor: Point, view: View, stage: Size, dpr: number): void;
  hide(): void;
}

export function createLoupe(doc: Document): Loupe {
  const canvas = createImageCanvas(doc, "loupe__canvas", "loupe-canvas");
  const label = el(doc, "span", { class: "loupe__label t-num", text: fill(en.workspace.loupe, { factor: LOUPE_FACTOR }) });
  const element = el(
    doc,
    "div",
    { class: "loupe", attrs: { "aria-hidden": "true" }, data: { part: "loupe", factor: String(LOUPE_FACTOR) } },
    canvas.element,
    el(doc, "span", { class: "loupe__cross loupe__cross--h" }),
    el(doc, "span", { class: "loupe__cross loupe__cross--v" }),
    el(doc, "span", { class: "loupe__ring" }),
    label,
  );
  element.hidden = true;
  return {
    element,
    canvas: canvas.element,
    show(source, imagePoint, cursor, view, stage, dpr) {
      const device = Math.round(LOUPE_SIZE * dpr);
      canvas.resize(device, device, dpr);
      canvas.draw(source, loupeView(imagePoint, view, dpr));
      const at = loupePosition(cursor, stage);
      element.style.setProperty("transform", `translate(${snap(at.x, dpr)}px, ${snap(at.y, dpr)}px)`);
      element.hidden = false;
    },
    hide() {
      element.hidden = true;
    },
  };
}
