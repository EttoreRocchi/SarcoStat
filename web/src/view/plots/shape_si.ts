import { en } from "../../strings/en.ts";
import { fill } from "../../strings/format.ts";
import { el } from "../dom.ts";
import type { PlotPoint } from "../results/model.ts";
import { formatTick, niceTicks, scaleLinear, svgEl } from "./svg.ts";

export const PLOT_WIDTH = 352;
const LEFT = 38;
const RIGHT = 10;
const TOP = 24;
const ROW = 22;
const AXIS = 34;

export interface SiDomain {
  readonly low: number;
  readonly high: number;
}

export function siDomain(points: readonly PlotPoint[], band: readonly [number, number] | null): SiDomain {
  const values: number[] = [];
  for (const point of points) {
    const spread = point.u === null ? 0 : 2 * point.u;
    values.push(point.si - spread, point.si + spread);
  }
  if (band !== null) {
    values.push(band[0], band[1]);
  }
  const finite = values.filter((value) => Number.isFinite(value));
  let low = Math.min(...finite);
  let high = Math.max(...finite);
  if (!(high > low)) {
    low -= 0.05;
    high += 0.05;
  }
  const pad = (high - low) * 0.08;
  return { low: low - pad, high: high + pad };
}

export function shapeSiPlot(doc: Document, points: readonly PlotPoint[], band: readonly [number, number] | null, selected: number | null): HTMLElement {
  const domain = siDomain(points, band);
  const height = TOP + ROW * Math.max(1, points.length) + AXIS;
  const x = scaleLinear([domain.low, domain.high], [LEFT, PLOT_WIDTH - RIGHT]);
  const bottom = TOP + ROW * Math.max(1, points.length);
  const svg = svgEl(doc, "svg", { class: "si-plot", viewBox: `0 0 ${PLOT_WIDTH} ${height}`, role: "img", "aria-labelledby": "si-plot-title" });
  svg.dataset.part = "si-plot";
  svg.append(svgEl(doc, "title", { id: "si-plot-title" }, fill(en.plots.label, { count: points.length })));
  if (band !== null) {
    const edges = [domain.low, Math.max(domain.low, band[0]), Math.min(domain.high, band[1]), domain.high];
    const names = [
      ["barrel", en.plots.barrel],
      ["rect", en.plots.rectangular],
      ["hourglass", en.plots.hourglass],
    ] as const;
    names.forEach(([kind, label], k) => {
      const from = x(edges[k] as number);
      const to = x(edges[k + 1] as number);
      if (to - from <= 0) {
        return;
      }
      svg.append(svgEl(doc, "rect", { class: `si-plot__band si-plot__band--${kind}`, x: from, y: TOP - 4, width: to - from, height: bottom - TOP + 8 }));
      if (to - from >= 34) {
        svg.append(svgEl(doc, "text", { class: "si-plot__band-label", x: (from + to) / 2, y: TOP - 8, "text-anchor": "middle" }, label));
      }
    });
  }
  const ticks = niceTicks(domain.low, domain.high, 5);
  svg.append(svgEl(doc, "line", { class: "si-plot__axis", x1: LEFT, x2: PLOT_WIDTH - RIGHT, y1: bottom + 4, y2: bottom + 4 }));
  for (const value of ticks.values) {
    const at = x(value);
    svg.append(svgEl(doc, "line", { class: "si-plot__tick", x1: at, x2: at, y1: bottom + 4, y2: bottom + 8 }));
    const label = svgEl(doc, "text", { class: "si-plot__tick-label", x: at, y: bottom + 19, "text-anchor": "middle" }, formatTick(value, ticks.decimals));
    label.dataset.tick = String(value);
    svg.append(label);
  }
  svg.append(svgEl(doc, "text", { class: "si-plot__axis-label", x: PLOT_WIDTH - RIGHT, y: height - 2, "text-anchor": "end" }, en.plots.axis));
  points.forEach((point, k) => {
    const y = TOP + ROW * k + ROW / 2;
    const now = point.id === selected;
    const row = svgEl(doc, "g", { class: now ? "si-plot__row si-plot__row--now" : "si-plot__row" });
    row.dataset.sarcomere = String(point.id);
    row.append(svgEl(doc, "text", { class: "si-plot__row-label", x: LEFT - 8, y: y + 4, "text-anchor": "end" }, fill(en.plots.row, { n: point.id })));
    if (point.u !== null) {
      row.append(svgEl(doc, "line", { class: "si-plot__u2", x1: x(point.si - 2 * point.u), x2: x(point.si + 2 * point.u), y1: y, y2: y }));
      row.append(svgEl(doc, "line", { class: "si-plot__u1", x1: x(point.si - point.u), x2: x(point.si + point.u), y1: y, y2: y }));
    }
    row.append(svgEl(doc, "circle", { class: "si-plot__dot", cx: x(point.si), cy: y, r: now ? 4.5 : 3.5 }));
    svg.append(row);
  });
  return el(doc, "figure", { class: "plot", data: { part: "plot-si" } }, svg, el(doc, "figcaption", { class: "plot__caption", text: en.plots.caption }));
}
