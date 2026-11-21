import type { Size, View } from "./viewport.ts";

export interface Span {
  readonly first: number;
  readonly last: number;
}

export interface Placement {
  readonly sx: number;
  readonly sy: number;
  readonly sw: number;
  readonly sh: number;
  readonly dx: number;
  readonly dy: number;
  readonly dw: number;
  readonly dh: number;
}

export interface Backing {
  readonly width: number;
  readonly height: number;
  readonly dpr: number;
}

export function visibleSpan(offset: number, scale: number, extent: number, count: number): Span {
  const first = Math.max(0, Math.min(count, Math.floor(-offset / scale)));
  const last = Math.max(first, Math.min(count, Math.ceil((extent - offset) / scale)));
  return { first, last };
}

export function placement(image: Size, view: View, backing: Backing): Placement | null {
  const scale = view.zoom * backing.dpr;
  const ox = Math.round(view.offsetX * backing.dpr);
  const oy = Math.round(view.offsetY * backing.dpr);
  const xs = visibleSpan(ox, scale, backing.width, image.width);
  const ys = visibleSpan(oy, scale, backing.height, image.height);
  if (xs.last <= xs.first || ys.last <= ys.first) {
    return null;
  }
  return {
    sx: xs.first,
    sy: ys.first,
    sw: xs.last - xs.first,
    sh: ys.last - ys.first,
    dx: ox + xs.first * scale,
    dy: oy + ys.first * scale,
    dw: (xs.last - xs.first) * scale,
    dh: (ys.last - ys.first) * scale,
  };
}

export function paint(context: CanvasRenderingContext2D, source: CanvasImageSource & Size, view: View | null, backing: Backing): void {
  context.setTransform(1, 0, 0, 1, 0, 0);
  context.imageSmoothingEnabled = false;
  context.clearRect(0, 0, backing.width, backing.height);
  if (view === null) {
    return;
  }
  const at = placement(source, view, backing);
  if (at !== null) {
    context.drawImage(source, at.sx, at.sy, at.sw, at.sh, at.dx, at.dy, at.dw, at.dh);
  }
}

export function describeView(element: HTMLElement, view: View | null): void {
  if (view === null) {
    delete element.dataset.zoom;
    delete element.dataset.offsetX;
    delete element.dataset.offsetY;
    return;
  }
  element.dataset.zoom = String(view.zoom);
  element.dataset.offsetX = String(view.offsetX);
  element.dataset.offsetY = String(view.offsetY);
}

export interface ImageCanvas {
  readonly element: HTMLCanvasElement;
  readonly backing: Backing;
  resize(width: number, height: number, dpr: number): boolean;
  draw(source: (CanvasImageSource & Size) | null, view: View | null): void;
}

export function createImageCanvas(doc: Document, className: string, part: string): ImageCanvas {
  const element = doc.createElement("canvas");
  element.className = className;
  element.dataset.part = part;
  element.width = 0;
  element.height = 0;
  let backing: Backing = { width: 0, height: 0, dpr: 1 };
  let context: CanvasRenderingContext2D | null = null;
  return {
    element,
    get backing() {
      return backing;
    },
    resize(width, height, dpr) {
      if (width === backing.width && height === backing.height && dpr === backing.dpr) {
        return false;
      }
      backing = { width, height, dpr };
      element.width = width;
      element.height = height;
      context = null;
      return true;
    },
    draw(source, view) {
      if (backing.width === 0 || backing.height === 0) {
        return;
      }
      context ??= element.getContext("2d", { alpha: true });
      if (context === null) {
        return;
      }
      if (source === null) {
        context.setTransform(1, 0, 0, 1, 0, 0);
        context.clearRect(0, 0, backing.width, backing.height);
        describeView(element, null);
        return;
      }
      paint(context, source, view, backing);
      describeView(element, view);
    },
  };
}
