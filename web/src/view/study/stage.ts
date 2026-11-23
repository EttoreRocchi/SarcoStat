import display from "../../config/study_display.json";
import type { StudyViewState, Time } from "../../state/study_session.ts";
import { nowTime } from "../../state/study_session.ts";
import { el } from "../dom.ts";
import { createImageCanvas } from "../workspace/canvas.ts";
import { createLoupe } from "../workspace/loupe.ts";
import { BADGE_GAP, kindOfMark, kindOfStroke, renderGroup, type OverlayGroup } from "../workspace/overlays.ts";
import {
  clampView,
  clientToDisplay,
  centred,
  displayToLocal,
  FIT_MARGIN,
  fitZoom,
  insideImage,
  localToImage,
  panBy,
  sameView,
  zoomAround,
  type Point,
  type Size,
  type View,
} from "../workspace/viewport.ts";

export const DRAG_THRESHOLD = 4;
export const WHEEL_STEP = 50;
const WHEEL_LINE = 40;
const WHEEL_PAGE = 800;
const SVG_NS = "http://www.w3.org/2000/svg";
const PARTNER: Readonly<Record<string, string>> = { A: "B", B: "A", C: "D", D: "C", E: "F", F: "E" };

export interface StudyDisplay {
  readonly factor: number;
  readonly maxZoom: number;
  readonly loupeFactor: number;
}

export function studyDisplay(raw: unknown = display): StudyDisplay {
  const config = raw as { format?: unknown; format_version?: unknown; zoom?: { from?: unknown; factor?: unknown; max_css_px_per_image_px?: unknown }; loupe?: { factor?: unknown }; smoothing?: unknown; display_window?: unknown; zoom_percentage?: unknown };
  if (config.format !== "sarcostat-study-display" || config.format_version !== 1 || config.zoom?.from !== "fit") {
    throw new Error("study_display.json is not a study display description of format version 1");
  }
  if (config.smoothing !== false || config.display_window !== false || config.zoom_percentage !== false) {
    throw new Error("study_display.json must turn smoothing, the display window and the zoom percentage off");
  }
  const factor = Number(config.zoom.factor);
  const maxZoom = Number(config.zoom.max_css_px_per_image_px);
  const loupeFactor = Number(config.loupe?.factor);
  if (!(factor > 1) || !(maxZoom > 0) || !(loupeFactor > 1)) {
    throw new Error("study_display.json has an invalid zoom factor, zoom limit or loupe factor");
  }
  return { factor, maxZoom, loupeFactor };
}

export const DISPLAY = studyDisplay();

export function zoomLevels(fit: number, settings: StudyDisplay = DISPLAY): number[] {
  if (!(fit > 0) || fit >= settings.maxZoom) {
    return [Math.min(fit > 0 ? fit : settings.maxZoom, settings.maxZoom)];
  }
  const out: number[] = [];
  for (let k = 0; k < 256; k += 1) {
    const zoom = fit * settings.factor ** k;
    if (zoom > settings.maxZoom) {
      break;
    }
    out.push(zoom);
  }
  return out;
}

export interface StageItem {
  readonly bitmap: CanvasImageSource & Size;
  readonly width: number;
  readonly height: number;
}

export interface StageHandlers {
  click(point: Point, t: Time): void;
  outside(): void;
}

export interface StudyStage {
  readonly element: HTMLElement;
  show(item: StageItem | null, key: string | null): void;
  marks(view: StudyViewState | null): void;
  zoom(direction: 1 | -1): void;
  fit(): void;
  pan(held: boolean): void;
  setEnabled(enabled: boolean): void;
  readonly step: number;
}

type Mark = StudyViewState["marks"][number];

function badgeAt(mark: Mark, marks: readonly Mark[], view: View): Point {
  const at = displayToLocal(mark, view);
  const partner = marks.find((other) => other.role === PARTNER[mark.role]);
  if (partner !== undefined) {
    const other = displayToLocal(partner, view);
    const dx = at.x - other.x;
    const dy = at.y - other.y;
    const length = Math.hypot(dx, dy);
    if (length > 1e-6) {
      return { x: at.x + (dx / length) * BADGE_GAP, y: at.y + (dy / length) * BADGE_GAP };
    }
  }
  return { x: at.x - BADGE_GAP * Math.SQRT1_2, y: at.y - BADGE_GAP * Math.SQRT1_2 };
}

export function studyOverlay(state: StudyViewState, view: View): OverlayGroup {
  const local = (x: number, y: number): Point => displayToLocal({ x, y }, view);
  return {
    id: "study",
    pending: true,
    sides: [],
    lines: state.strokes.map((stroke) => ({
      kind: kindOfStroke(stroke.role),
      role: stroke.role,
      from: local(stroke.p1[0] ?? 0, stroke.p1[1] ?? 0),
      to: local(stroke.p2[0] ?? 0, stroke.p2[1] ?? 0),
    })),
    dots: state.marks.map((mark) => ({ kind: kindOfMark(mark.role), role: mark.role, at: local(mark.x, mark.y) })),
    badges: state.marks.filter((mark) => mark.role in PARTNER).map((mark) => ({ kind: kindOfMark(mark.role), letter: mark.role, at: badgeAt(mark, state.marks, view) })),
    tags: [],
  };
}

function svg<K extends keyof SVGElementTagNameMap>(doc: Document, tag: K, className: string, attrs: Readonly<Record<string, string | number>>): SVGElementTagNameMap[K] {
  const element = doc.createElementNS(SVG_NS, tag);
  element.setAttribute("class", className);
  for (const [name, value] of Object.entries(attrs)) {
    element.setAttribute(name, String(value));
  }
  return element;
}

function deviceExtent(reported: number | undefined, css: number, ratio: number): number {
  return reported !== undefined && Math.abs(reported - css * ratio) < 1 ? reported : Math.round(css * ratio);
}

interface Press {
  readonly id: number;
  readonly x: number;
  readonly y: number;
  readonly view: View;
  readonly display: Point | null;
  readonly t: Time;
  panning: boolean;
}

export function createStudyStage(doc: Document, handlers: StageHandlers, settings: StudyDisplay = DISPLAY): StudyStage {
  const win = doc.defaultView as Window;
  const canvas = createImageCanvas(doc, "study-stage__canvas", "study-canvas");
  const overlay = svg(doc, "svg", "ov study-stage__overlay", { "aria-hidden": "true", focusable: "false", "data-part": "study-overlay" });
  const loupe = createLoupe(doc);
  const element = el(doc, "div", { class: "study-stage", data: { part: "study-stage" } }, canvas.element, overlay, loupe.element);

  let item: StageItem | null = null;
  let itemKey: string | null = null;
  let state: StudyViewState | null = null;
  let view: View | null = null;
  let levels: number[] = [];
  let step = 0;
  let size: Size = { width: 0, height: 0 };
  let dpr = win.devicePixelRatio || 1;
  let press: Press | null = null;
  let hover: Point | null = null;
  let spaceHeld = false;
  let enabled = true;
  let wheelRest = 0;

  const panMode = (): boolean => spaceHeld;

  const describe = (): void => {
    element.dataset.zoomStep = String(step);
    element.dataset.zoomSteps = String(levels.length);
    element.classList.toggle("study-stage--pan", panMode());
    element.classList.toggle("study-stage--panning", press?.panning === true);
  };

  const updateLoupe = (): void => {
    if (item === null || view === null || hover === null || panMode() || press?.panning === true || !enabled) {
      loupe.hide();
      return;
    }
    const rect = canvas.element.getBoundingClientRect();
    const point = clientToDisplay(hover.x, hover.y, rect, view);
    if (!insideImage(point, item)) {
      loupe.hide();
      return;
    }
    const local = { x: hover.x - rect.left, y: hover.y - rect.top };
    loupe.show(item.bitmap, localToImage(local, view), local, view, size, dpr);
  };

  const drawOverlay = (): void => {
    overlay.replaceChildren();
    if (item === null || view === null || state === null) {
      return;
    }
    const roi = state.roi;
    if (roi !== null && roi.length === 4) {
      const a = displayToLocal({ x: roi[0] ?? 0, y: roi[1] ?? 0 }, view);
      const b = displayToLocal({ x: roi[2] ?? 0, y: roi[3] ?? 0 }, view);
      const box = { x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), width: Math.abs(b.x - a.x), height: Math.abs(b.y - a.y) };
      overlay.append(svg(doc, "rect", "study-target study-target--halo", box), svg(doc, "rect", "study-target", { ...box, "data-part": "study-target" }));
    }
    overlay.append(renderGroup(doc, studyOverlay(state, view)));
  };

  const redraw = (): void => {
    canvas.draw(item === null ? null : item.bitmap, item === null ? null : view);
    drawOverlay();
    describe();
    updateLoupe();
  };

  const levelsFor = (): number[] => (item === null || size.width === 0 || size.height === 0 ? [] : zoomLevels(fitZoom(item, size, FIT_MARGIN), settings));

  const fitted = (): View | null => {
    const zoom = levels[0];
    return item === null || zoom === undefined ? null : centred(zoom, item, size, dpr);
  };

  const setView = (next: View): void => {
    if (item === null) {
      return;
    }
    const clamped = clampView(next, item, size, dpr);
    if (!sameView(clamped, view)) {
      view = clamped;
    }
    redraw();
  };

  const toStep = (next: number, anchor: Point = { x: size.width / 2, y: size.height / 2 }): void => {
    if (item === null || view === null || levels.length === 0) {
      return;
    }
    const target = Math.max(0, Math.min(levels.length - 1, next));
    const zoom = levels[target] as number;
    step = target;
    if (target === 0) {
      view = fitted();
      redraw();
      return;
    }
    if (zoom !== view.zoom) {
      setView(zoomAround(view, zoom, anchor, dpr));
    } else {
      describe();
    }
  };

  const relayout = (width: number, height: number, deviceWidth: number, deviceHeight: number): void => {
    dpr = win.devicePixelRatio || 1;
    size = { width, height };
    canvas.resize(deviceWidth, deviceHeight, dpr);
    overlay.setAttribute("width", String(width));
    overlay.setAttribute("height", String(height));
    overlay.setAttribute("viewBox", `0 0 ${width} ${height}`);
    const previous = step;
    levels = levelsFor();
    step = 0;
    view = fitted();
    if (previous > 0) {
      toStep(previous);
      return;
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

  const target = canvas.element;
  target.addEventListener("pointerdown", (event) => {
    if (!enabled || item === null || view === null || press !== null || !event.isPrimary || (event.button !== 0 && event.button !== 1)) {
      return;
    }
    const t = nowTime();
    const panning = event.button === 1 || panMode();
    const point = panning ? null : clientToDisplay(event.clientX, event.clientY, target.getBoundingClientRect(), view);
    press = { id: event.pointerId, x: event.clientX, y: event.clientY, view, display: point, t, panning };
    if (event.button === 1) {
      event.preventDefault();
    }
    target.setPointerCapture(event.pointerId);
    if (panning) {
      describe();
      loupe.hide();
    }
  });
  target.addEventListener("pointermove", (event) => {
    hover = { x: event.clientX, y: event.clientY };
    if (press === null || press.id !== event.pointerId) {
      updateLoupe();
      return;
    }
    const dx = event.clientX - press.x;
    const dy = event.clientY - press.y;
    if (!press.panning && Math.hypot(dx, dy) > DRAG_THRESHOLD) {
      press.panning = true;
      describe();
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
    if (target.hasPointerCapture(event.pointerId)) {
      target.releasePointerCapture(event.pointerId);
    }
    describe();
    if (send && enabled && !done.panning && done.display !== null && item !== null) {
      if (insideImage(done.display, item)) {
        handlers.click(done.display, done.t);
      } else {
        handlers.outside();
      }
    }
    updateLoupe();
  };
  target.addEventListener("pointerup", (event) => release(event, true));
  target.addEventListener("pointercancel", (event) => release(event, false));
  target.addEventListener("pointerleave", () => {
    if (press === null) {
      hover = null;
      loupe.hide();
    }
  });
  target.addEventListener(
    "wheel",
    (event) => {
      if (item === null || view === null) {
        return;
      }
      event.preventDefault();
      if (!enabled) {
        return;
      }
      const delta = event.deltaY * (event.deltaMode === 1 ? WHEEL_LINE : event.deltaMode === 2 ? WHEEL_PAGE : 1);
      if (Math.sign(delta) !== Math.sign(wheelRest)) {
        wheelRest = 0;
      }
      wheelRest += delta;
      hover = { x: event.clientX, y: event.clientY };
      if (Math.abs(wheelRest) >= WHEEL_STEP) {
        const rect = target.getBoundingClientRect();
        const direction = wheelRest < 0 ? 1 : -1;
        wheelRest = 0;
        toStep(step + direction, { x: event.clientX - rect.left, y: event.clientY - rect.top });
      }
      updateLoupe();
    },
    { passive: false },
  );
  win.addEventListener("blur", () => {
    if (spaceHeld) {
      spaceHeld = false;
      describe();
      updateLoupe();
    }
  });

  return {
    element,
    get step() {
      return step;
    },
    show(next, key) {
      if (key === itemKey && next === item) {
        return;
      }
      item = next;
      itemKey = key;
      press = null;
      wheelRest = 0;
      levels = levelsFor();
      step = 0;
      view = fitted();
      redraw();
    },
    marks(next) {
      state = next;
      drawOverlay();
    },
    zoom(direction) {
      toStep(step + direction);
    },
    fit() {
      toStep(0);
    },
    pan(held) {
      if (spaceHeld !== held) {
        spaceHeld = held;
        describe();
        updateLoupe();
      }
    },
    setEnabled(next) {
      enabled = next;
      if (!next) {
        press = null;
        loupe.hide();
      }
      describe();
    },
  };
}
