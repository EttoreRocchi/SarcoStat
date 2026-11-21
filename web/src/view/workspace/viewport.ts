export interface View {
  readonly zoom: number;
  readonly offsetX: number;
  readonly offsetY: number;
}

export interface Size {
  readonly width: number;
  readonly height: number;
}

export interface Point {
  readonly x: number;
  readonly y: number;
}

export interface Origin {
  readonly left: number;
  readonly top: number;
}

export const ZOOM_STEPS: readonly number[] = [1 / 32, 1 / 16, 1 / 8, 1 / 6, 1 / 4, 1 / 3, 1 / 2, 2 / 3, 1, 1.5, 2, 3, 4, 6, 8, 12, 16, 24, 32];
export const MIN_ZOOM = 1 / 32;
export const MAX_ZOOM = 32;
export const FIT_MARGIN = 24;
export const KEEP_VISIBLE = 32;
const STEP_TOLERANCE = 1e-9;

export function clientToDisplay(clientX: number, clientY: number, origin: Origin, view: View): Point {
  return {
    x: (clientX - origin.left - view.offsetX) / view.zoom - 0.5,
    y: (clientY - origin.top - view.offsetY) / view.zoom - 0.5,
  };
}

export function displayToLocal(point: Point, view: View): Point {
  return { x: view.offsetX + view.zoom * (point.x + 0.5), y: view.offsetY + view.zoom * (point.y + 0.5) };
}

export function localToImage(local: Point, view: View): Point {
  return { x: (local.x - view.offsetX) / view.zoom, y: (local.y - view.offsetY) / view.zoom };
}

export function insideImage(point: Point, image: Size): boolean {
  return point.x >= -0.5 && point.y >= -0.5 && point.x < image.width - 0.5 && point.y < image.height - 0.5;
}

export function snap(value: number, dpr: number): number {
  return Math.round(value * dpr) / dpr;
}

export function clampZoom(zoom: number): number {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom));
}

export function fitZoom(image: Size, stage: Size, margin = FIT_MARGIN): number {
  const width = Math.max(1, stage.width - 2 * margin);
  const height = Math.max(1, stage.height - 2 * margin);
  return clampZoom(Math.min(width / image.width, height / image.height));
}

export function centred(zoom: number, image: Size, stage: Size, dpr: number): View {
  return {
    zoom,
    offsetX: snap((stage.width - zoom * image.width) / 2, dpr),
    offsetY: snap((stage.height - zoom * image.height) / 2, dpr),
  };
}

export function fitView(image: Size, stage: Size, dpr: number, margin = FIT_MARGIN): View {
  return centred(fitZoom(image, stage, margin), image, stage, dpr);
}

export function stepZoom(current: number, direction: 1 | -1): number {
  if (direction > 0) {
    return ZOOM_STEPS.find((step) => step > current * (1 + STEP_TOLERANCE)) ?? clampZoom(current);
  }
  return [...ZOOM_STEPS].reverse().find((step) => step < current * (1 - STEP_TOLERANCE)) ?? clampZoom(current);
}

export function zoomAround(view: View, zoom: number, anchor: Point, dpr: number): View {
  const at = localToImage(anchor, view);
  return { zoom, offsetX: snap(anchor.x - zoom * at.x, dpr), offsetY: snap(anchor.y - zoom * at.y, dpr) };
}

export function panBy(view: View, dx: number, dy: number, dpr: number): View {
  return { zoom: view.zoom, offsetX: snap(view.offsetX + dx, dpr), offsetY: snap(view.offsetY + dy, dpr) };
}

function clampAxis(offset: number, extent: number, stage: number, keep: number): number {
  const visible = Math.min(keep, extent, stage);
  return Math.min(stage - visible, Math.max(visible - extent, offset));
}

export function clampView(view: View, image: Size, stage: Size, dpr: number, keep = KEEP_VISIBLE): View {
  const offsetX = clampAxis(view.offsetX, view.zoom * image.width, stage.width, keep);
  const offsetY = clampAxis(view.offsetY, view.zoom * image.height, stage.height, keep);
  if (offsetX === view.offsetX && offsetY === view.offsetY) {
    return view;
  }
  return { zoom: view.zoom, offsetX: snap(offsetX, dpr), offsetY: snap(offsetY, dpr) };
}

export function sameView(a: View | null, b: View | null): boolean {
  return a === b || (a !== null && b !== null && a.zoom === b.zoom && a.offsetX === b.offsetX && a.offsetY === b.offsetY);
}

export function zoomPercent(zoom: number): number {
  return Math.round(zoom * 100);
}
