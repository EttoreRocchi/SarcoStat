import { en } from "../../strings/en.ts";
import { fill } from "../../strings/format.ts";
import { el } from "../dom.ts";

export interface ScaleSettings {
  readonly pixel_size: number;
  readonly unit: string;
}

export interface ScaleBarModel {
  readonly length: number;
  readonly unit: string;
  readonly label: string;
  readonly width: number;
}

const PIXEL_UNITS = new Set(["px", "pixel", "pixels"]);
const MICROMETRES: Readonly<Record<string, number>> = { nm: 1e-3, "µm": 1, "μm": 1, um: 1, mm: 1e3 };
const ROUND = [1, 2, 5];
export const PREFERRED_MICROMETRES = 1;
export const BAR_MIN = 48;
export const BAR_MAX = 240;

function roundLengths(): number[] {
  const out: number[] = [];
  for (let power = -6; power <= 9; power += 1) {
    for (const base of ROUND) {
      out.push(Number((base * 10 ** power).toPrecision(12)));
    }
  }
  return out;
}

const LENGTHS = roundLengths();

function choose(cssPerUnit: number, preferred: number | null): number | null {
  if (preferred !== null) {
    const width = preferred * cssPerUnit;
    if (width >= BAR_MIN && width <= BAR_MAX) {
      return preferred;
    }
  }
  const target = Math.sqrt(BAR_MIN * BAR_MAX);
  let best: number | null = null;
  for (const length of LENGTHS) {
    const width = length * cssPerUnit;
    if (width < BAR_MIN || width > BAR_MAX) {
      continue;
    }
    if (best === null || Math.abs(Math.log(width / target)) < Math.abs(Math.log((best * cssPerUnit) / target))) {
      best = length;
    }
  }
  return best;
}

function label(length: number, unit: string): string {
  return fill(en.workspace.scaleBar, { length: Number(length.toPrecision(12)).toString(), unit });
}

export function scaleBarModel(settings: ScaleSettings | null | undefined, zoom: number): ScaleBarModel | null {
  if (settings === null || settings === undefined) {
    return null;
  }
  const { pixel_size: size, unit } = settings;
  if (!Number.isFinite(size) || size <= 0 || !Number.isFinite(zoom) || zoom <= 0 || PIXEL_UNITS.has(unit.trim().toLowerCase())) {
    return null;
  }
  const factor = MICROMETRES[unit];
  if (factor === undefined) {
    const cssPerUnit = zoom / size;
    const length = choose(cssPerUnit, null);
    return length === null ? null : { length, unit, label: label(length, unit), width: length * cssPerUnit };
  }
  const cssPerMicrometre = zoom / (size * factor);
  const micrometres = choose(cssPerMicrometre, PREFERRED_MICROMETRES);
  if (micrometres === null) {
    return null;
  }
  const width = micrometres * cssPerMicrometre;
  if (micrometres < 1) {
    const nm = Number((micrometres * 1e3).toPrecision(12));
    return { length: nm, unit: "nm", label: label(nm, "nm"), width };
  }
  if (micrometres >= 1e3) {
    const mm = Number((micrometres / 1e3).toPrecision(12));
    return { length: mm, unit: "mm", label: label(mm, "mm"), width };
  }
  return { length: micrometres, unit: "µm", label: label(micrometres, "µm"), width };
}

export interface ScaleBar {
  readonly element: HTMLElement;
  render(settings: ScaleSettings | null | undefined, zoom: number | null): void;
}

export function createScaleBar(doc: Document): ScaleBar {
  const bar = el(doc, "span", { class: "scalebar__bar", attrs: { "aria-hidden": "true" } });
  const text = el(doc, "span", { class: "scalebar__label t-num" });
  const element = el(doc, "div", { class: "scalebar", attrs: { role: "img" }, data: { part: "scalebar" } }, bar, text);
  element.hidden = true;
  let shown = "";
  return {
    element,
    render(settings, zoom) {
      const model = zoom === null ? null : scaleBarModel(settings, zoom);
      element.hidden = model === null;
      if (model === null) {
        shown = "";
        return;
      }
      const key = `${model.label} ${model.width}`;
      if (key === shown) {
        return;
      }
      shown = key;
      text.textContent = model.label;
      element.setAttribute("aria-label", fill(en.workspace.scaleBarLabel, { label: model.label }));
      element.dataset.length = String(model.length);
      element.dataset.unit = model.unit;
      bar.style.setProperty("width", `${model.width}px`);
    },
  };
}
