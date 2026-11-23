import iconsUrl from "../../assets/icons.svg?url";
import { decodeImage, type DecodedImage } from "../../io/image/decode.ts";
import type { AppState, ModeState, OpenedImage } from "../../state/store.ts";
import { en } from "../../strings/en.ts";
import { fill } from "../../strings/format.ts";
import { button, el, kbd } from "../dom.ts";
import type { ViewKeyAction } from "../shell/shortcuts.ts";
import { createImageCanvas } from "./canvas.ts";
import { createLoupe } from "./loupe.ts";
import { createOverlay } from "./overlays.ts";
import { createScaleBar, type ScaleSettings } from "./scalebar.ts";
import {
  clampView,
  clientToDisplay,
  fitView,
  insideImage,
  localToImage,
  panBy,
  sameView,
  stepZoom,
  zoomAround,
  zoomPercent,
  type Point,
  type Size,
  type View,
} from "./viewport.ts";

export const DRAG_THRESHOLD = 4;
export const WHEEL_STEP = 50;
const WHEEL_LINE = 40;
const WHEEL_PAGE = 800;
const SVG_NS = "http://www.w3.org/2000/svg";

export type Tool = "annotate" | "pan";
export type WorkspaceStatus = "empty" | "decoding" | "drawn" | "failed";

export interface Clicker {
  click(x: number, y: number): Promise<unknown>;
}

export interface WorkspaceContext {
  readonly session: Clicker;
  onStatus?(status: WorkspaceStatus): void;
}

export interface Workspace {
  readonly element: HTMLElement;
  readonly status: WorkspaceStatus;
  render(state: AppState): void;
  view(action: ViewKeyAction): void;
}

interface Press {
  readonly id: number;
  readonly x: number;
  readonly y: number;
  readonly view: View;
  display: Point | null;
  panning: boolean;
}

type ToolIcon = "i-cross" | "i-hand" | "i-zoom-in" | "i-zoom-out" | "i-fit" | "i-layers";

function toolIcon(doc: Document, id: ToolIcon): SVGSVGElement {
  const svg = doc.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", "ic");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  const use = doc.createElementNS(SVG_NS, "use");
  use.setAttribute("href", `${iconsUrl}#${id}`);
  svg.append(use);
  return svg;
}

function toolButton(doc: Document, id: ToolIcon, label: string, title: string, key: string | null, shortcut: string | null, action: string): HTMLButtonElement {
  const out = button(doc, "tools__btn", toolIcon(doc, id), key === null ? null : kbd(doc, key, "key key--plain tools__key"));
  out.setAttribute("aria-label", label);
  out.title = title;
  out.dataset.action = action;
  if (shortcut !== null) {
    out.setAttribute("aria-keyshortcuts", shortcut);
  }
  out.addEventListener("pointerdown", (event) => event.preventDefault());
  return out;
}

export function deviceExtent(reported: number | undefined, css: number, ratio: number): number {
  return reported !== undefined && Math.abs(reported - css * ratio) < 1 ? reported : Math.round(css * ratio);
}

export function createWorkspace(doc: Document, host: HTMLElement, context: WorkspaceContext): Workspace {
  const win = doc.defaultView as Window;
  const t = en.workspace;
  const canvas = createImageCanvas(doc, "workspace__canvas", "image-canvas");
  canvas.element.setAttribute("role", "img");
  const overlay = createOverlay(doc);
  const loupe = createLoupe(doc);
  const scaleBar = createScaleBar(doc);
  const zoomBox = el(doc, "div", { class: "zoombox t-num", attrs: { "aria-label": t.zoomLabel }, data: { part: "zoom" } });
  const notice = el(doc, "p", { class: "workspace__notice", attrs: { role: "note" }, data: { part: "image-notice" } });
  const annotateButton = toolButton(doc, "i-cross", t.annotate, t.annotateTitle, "A", "A", "annotate");
  const panButton = toolButton(doc, "i-hand", t.pan, t.panTitle, null, "Space", "pan");
  const zoomInButton = toolButton(doc, "i-zoom-in", t.zoomIn, t.zoomInTitle, "+", "+", "zoom-in");
  const zoomOutButton = toolButton(doc, "i-zoom-out", t.zoomOut, t.zoomOutTitle, "−", "-", "zoom-out");
  const fitButton = toolButton(doc, "i-fit", t.fit, t.fitTitle, "F", "F", "fit");
  const layersButton = toolButton(doc, "i-layers", t.layers, t.layersTitle, "L", "L", "layers");
  const separator = (): HTMLElement => el(doc, "span", { class: "tools__sep", attrs: { role: "separator" } });
  const toolbar = el(
    doc,
    "div",
    { class: "tools", attrs: { role: "toolbar", "aria-label": t.tools, "aria-orientation": "vertical" }, data: { part: "tools" } },
    annotateButton,
    panButton,
    separator(),
    zoomInButton,
    zoomOutButton,
    fitButton,
    separator(),
    layersButton,
  );
  host.classList.add("workspace");
  host.append(canvas.element, overlay.element, loupe.element, toolbar, scaleBar.element, zoomBox, notice);

  let shown: OpenedImage | null = null;
  let image: DecodedImage | null = null;
  let token = 0;
  let view: View | null = null;
  let fitted = true;
  let stage: Size = { width: 0, height: 0 };
  let dpr = win.devicePixelRatio || 1;
  let tool: Tool = "annotate";
  let spaceHeld = false;
  let layers = true;
  let press: Press | null = null;
  let hover: Point | null = null;
  let session: ModeState | null = null;
  let settings: ScaleSettings | null = null;
  let wheelRest = 0;
  let status: WorkspaceStatus = "empty";

  const setStatus = (next: WorkspaceStatus): void => {
    host.dataset.state = next;
    if (next !== status) {
      status = next;
      context.onStatus?.(next);
    }
  };

  const panMode = (): boolean => tool === "pan" || spaceHeld;

  const refreshTools = (): void => {
    annotateButton.setAttribute("aria-pressed", String(tool === "annotate" && !spaceHeld));
    panButton.setAttribute("aria-pressed", String(panMode()));
    layersButton.setAttribute("aria-pressed", String(layers));
    canvas.element.classList.toggle("workspace__canvas--pan", panMode());
    canvas.element.classList.toggle("workspace__canvas--panning", press?.panning === true);
    overlay.setVisible(layers);
  };

  const updateLoupe = (): void => {
    if (image === null || view === null || hover === null || panMode() || press?.panning === true) {
      loupe.hide();
      return;
    }
    const rect = canvas.element.getBoundingClientRect();
    const display = clientToDisplay(hover.x, hover.y, rect, view);
    if (!insideImage(display, image)) {
      loupe.hide();
      return;
    }
    const local = { x: hover.x - rect.left, y: hover.y - rect.top };
    loupe.show(image.bitmap, localToImage(local, view), local, view, stage, dpr);
  };

  const redraw = (): void => {
    if (image !== null && view === null && stage.width > 0 && stage.height > 0) {
      view = fitView(image, stage, dpr);
      fitted = true;
    }
    const live = image !== null && view !== null;
    canvas.draw(image === null ? null : image.bitmap, view);
    overlay.render(live ? session : null, view);
    scaleBar.render(settings, live && view !== null ? view.zoom : null);
    toolbar.hidden = image === null;
    zoomBox.hidden = !live;
    zoomBox.textContent = live && view !== null ? fill(t.zoom, { percent: zoomPercent(view.zoom) }) : "";
    refreshTools();
    updateLoupe();
    if (live && status === "decoding") {
      setStatus("drawn");
    }
  };

  const setView = (next: View, keepFit = false): void => {
    if (image === null) {
      return;
    }
    const clamped = clampView(next, image, stage, dpr);
    fitted = keepFit;
    if (!sameView(clamped, view)) {
      view = clamped;
      redraw();
    }
  };

  const fit = (): void => {
    if (image !== null && stage.width > 0 && stage.height > 0) {
      setView(fitView(image, stage, dpr), true);
    }
  };

  const zoomStep = (direction: 1 | -1, anchor: Point = { x: stage.width / 2, y: stage.height / 2 }): void => {
    if (view === null) {
      return;
    }
    const next = stepZoom(view.zoom, direction);
    if (next !== view.zoom) {
      setView(zoomAround(view, next, anchor, dpr));
    }
  };

  const toggleLayers = (): void => {
    layers = !layers;
    refreshTools();
  };

  const setTool = (next: Tool): void => {
    tool = next;
    refreshTools();
    updateLoupe();
  };

  const load = (source: OpenedImage | null): void => {
    shown = source;
    token += 1;
    const mine = token;
    image?.bitmap.close();
    image = null;
    view = null;
    press = null;
    notice.hidden = true;
    notice.textContent = "";
    canvas.element.removeAttribute("aria-label");
    if (source === null) {
      setStatus("empty");
      redraw();
      return;
    }
    const show = (decoded: DecodedImage): void => {
      image = decoded;
      fitted = true;
      if (decoded.sixteenBit) {
        notice.textContent = t.sixteenBit;
        notice.hidden = false;
      }
      canvas.element.setAttribute("aria-label", fill(t.canvasLabel, { file: source.fileName, width: decoded.width, height: decoded.height }));
      redraw();
    };
    setStatus("decoding");
    if (source.decoded !== undefined) {
      redraw();
      show(source.decoded);
      return;
    }
    redraw();
    decodeImage(source.bytes, source.kind, source.width, source.height).then(
      (decoded) => {
        if (mine !== token) {
          decoded.bitmap.close();
          return;
        }
        show(decoded);
      },
      () => {
        if (mine !== token) {
          return;
        }
        notice.textContent = fill(t.decodeFailed, { file: source.fileName });
        notice.hidden = false;
        setStatus("failed");
      },
    );
  };

  const relayout = (width: number, height: number, deviceWidth: number, deviceHeight: number): void => {
    dpr = win.devicePixelRatio || 1;
    stage = { width, height };
    canvas.resize(deviceWidth, deviceHeight, dpr);
    if (image !== null && view !== null) {
      view = fitted ? fitView(image, stage, dpr) : clampView(view, image, stage, dpr);
    }
    redraw();
  };

  const observer = new ResizeObserver((entries) => {
    const entry = entries[entries.length - 1];
    if (entry === undefined) {
      return;
    }
    const ratio = win.devicePixelRatio || 1;
    const box = entry.devicePixelContentBoxSize?.[0];
    const { width, height } = entry.contentRect;
    relayout(width, height, deviceExtent(box?.inlineSize, width, ratio), deviceExtent(box?.blockSize, height, ratio));
  });
  try {
    observer.observe(canvas.element, { box: "device-pixel-content-box" });
  } catch {
    observer.observe(canvas.element);
  }
  let ratioQuery: MediaQueryList | null = null;
  const watchRatio = (): void => {
    ratioQuery?.removeEventListener("change", onRatio);
    ratioQuery = win.matchMedia(`(resolution: ${win.devicePixelRatio || 1}dppx)`);
    ratioQuery.addEventListener("change", onRatio);
  };
  function onRatio(): void {
    watchRatio();
    const rect = canvas.element.getBoundingClientRect();
    const ratio = win.devicePixelRatio || 1;
    relayout(rect.width, rect.height, Math.round(rect.width * ratio), Math.round(rect.height * ratio));
  }
  watchRatio();

  const element = canvas.element;
  element.addEventListener("pointerdown", (event) => {
    if (image === null || view === null || press !== null || !event.isPrimary || (event.button !== 0 && event.button !== 1)) {
      return;
    }
    const panning = event.button === 1 || panMode();
    const display = panning ? null : clientToDisplay(event.clientX, event.clientY, element.getBoundingClientRect(), view);
    press = { id: event.pointerId, x: event.clientX, y: event.clientY, view, display, panning };
    if (event.button === 1) {
      event.preventDefault();
    }
    element.setPointerCapture(event.pointerId);
    if (panning) {
      refreshTools();
      loupe.hide();
    }
  });
  element.addEventListener("pointermove", (event) => {
    hover = { x: event.clientX, y: event.clientY };
    if (press === null || press.id !== event.pointerId) {
      updateLoupe();
      return;
    }
    const dx = event.clientX - press.x;
    const dy = event.clientY - press.y;
    if (!press.panning && Math.hypot(dx, dy) > DRAG_THRESHOLD) {
      press.panning = true;
      press.display = null;
      refreshTools();
      loupe.hide();
    }
    if (press.panning) {
      setView(panBy(press.view, dx, dy, dpr));
    } else {
      updateLoupe();
    }
  });
  const release = (event: PointerEvent, send: boolean): void => {
    if (press === null || press.id !== event.pointerId) {
      return;
    }
    const done = press;
    press = null;
    if (element.hasPointerCapture(event.pointerId)) {
      element.releasePointerCapture(event.pointerId);
    }
    refreshTools();
    if (send && !done.panning && done.display !== null) {
      void context.session.click(done.display.x, done.display.y);
    }
    updateLoupe();
  };
  element.addEventListener("pointerup", (event) => release(event, true));
  element.addEventListener("pointercancel", (event) => release(event, false));
  element.addEventListener("pointerleave", () => {
    if (press === null) {
      hover = null;
      loupe.hide();
    }
  });
  element.addEventListener(
    "wheel",
    (event) => {
      if (image === null || view === null) {
        return;
      }
      event.preventDefault();
      const delta = event.deltaY * (event.deltaMode === 1 ? WHEEL_LINE : event.deltaMode === 2 ? WHEEL_PAGE : 1);
      if (Math.sign(delta) !== Math.sign(wheelRest)) {
        wheelRest = 0;
      }
      wheelRest += delta;
      hover = { x: event.clientX, y: event.clientY };
      if (Math.abs(wheelRest) >= WHEEL_STEP) {
        const rect = element.getBoundingClientRect();
        const direction = wheelRest < 0 ? 1 : -1;
        wheelRest = 0;
        zoomStep(direction, { x: event.clientX - rect.left, y: event.clientY - rect.top });
      }
      updateLoupe();
    },
    { passive: false },
  );

  annotateButton.addEventListener("click", () => setTool("annotate"));
  panButton.addEventListener("click", () => setTool(tool === "pan" ? "annotate" : "pan"));
  zoomInButton.addEventListener("click", () => zoomStep(1));
  zoomOutButton.addEventListener("click", () => zoomStep(-1));
  fitButton.addEventListener("click", () => fit());
  layersButton.addEventListener("click", () => toggleLayers());

  const setSpace = (held: boolean): void => {
    if (spaceHeld !== held) {
      spaceHeld = held;
      refreshTools();
      updateLoupe();
    }
  };

  const onView = (action: ViewKeyAction): void => {
    if (action === "pan-end") {
      setSpace(false);
      return;
    }
    if (image === null || view === null || !host.isConnected) {
      return;
    }
    switch (action) {
      case "zoom-in":
        zoomStep(1);
        return;
      case "zoom-out":
        zoomStep(-1);
        return;
      case "fit":
        fit();
        return;
      case "layers":
        toggleLayers();
        return;
      case "annotate":
        setTool("annotate");
        return;
      case "pan-start":
        setSpace(true);
        return;
    }
  };

  setStatus("empty");
  redraw();

  return {
    element: host,
    get status() {
      return status;
    },
    view: (action) => onView(action),
    render(state) {
      if (state.session !== session) {
        session = state.session;
        settings = state.session?.settings ?? null;
        if (state.source === shown) {
          redraw();
        }
      }
      if (state.source !== shown) {
        load(state.source);
      }
    },
  };
}
