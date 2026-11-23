import { en } from "../../strings/en.ts";
import { fill } from "../../strings/format.ts";
import { button, el, kbd } from "../dom.ts";
import { icon } from "../icons.ts";
import { svgEl } from "../plots/svg.ts";
import { LENGTH_KEYS, splitPlusMinus, type Cell, type SarcomereResult, type ShapeResults } from "./model.ts";

export interface ResultsActions {
  select(step: -1 | 1): void;
  editScale(): void;
  setClickError(): void;
  estimateClickError(): void;
}

export interface ResultsContext {
  readonly results: ShapeResults | null;
  readonly selected: number | null;
  readonly count: number;
  readonly hasImage: boolean;
  readonly ready: boolean;
}

const RING_RADIUS = 21;

function text(doc: Document, tag: "p" | "span" | "b" | "abbr" | "dt" | "dd", cell: Cell, className: string): HTMLElement {
  return el(doc, tag, { class: className, text: cell.text, data: { ref: cell.ref } });
}

function plusMinus(doc: Document, cell: Cell, block: string, valueClass: string, pmClass: string): HTMLElement {
  const parts = splitPlusMinus(cell.text);
  const out = el(doc, block === "dd" ? "dd" : "p", { class: block === "dd" ? "metric__value" : "readout", data: { ref: cell.ref } }, el(doc, "span", { class: valueClass, text: parts.value }));
  if (parts.pm !== "") {
    out.append(el(doc, "span", { class: pmClass, text: parts.pm }));
  }
  return out;
}

export function stabilityRing(doc: Document, share: number, label: Cell): HTMLElement {
  const length = 2 * Math.PI * RING_RADIUS;
  const clamped = Math.min(1, Math.max(0, share));
  const svg = svgEl(doc, "svg", { class: "ring__svg", viewBox: "0 0 54 54", "aria-hidden": "true", focusable: "false" });
  svg.append(svgEl(doc, "circle", { class: "ring__track", cx: 27, cy: 27, r: RING_RADIUS }));
  const arc = svgEl(doc, "circle", { class: "ring__arc", cx: 27, cy: 27, r: RING_RADIUS, "stroke-dasharray": `${(clamped * length).toFixed(2)} ${length.toFixed(2)}`, transform: "rotate(-90 27 27)" });
  arc.dataset.share = String(share);
  svg.append(arc);
  const angle = -Math.PI / 2 + clamped * 2 * Math.PI;
  const tipX = 27 + RING_RADIUS * Math.cos(angle);
  const tipY = 27 + RING_RADIUS * Math.sin(angle);
  const heading = (angle * 180) / Math.PI + 90;
  if (clamped > 0.02) {
    svg.append(svgEl(doc, "path", { class: "ring__tip", d: "M -3.2 -2.6 L 3.2 0 L -3.2 2.6 Z", transform: `translate(${tipX.toFixed(2)} ${tipY.toFixed(2)}) rotate(${heading.toFixed(1)})` }));
  }
  return el(doc, "div", { class: "ring", data: { part: "stability-ring" } }, svg, text(doc, "span", label, "ring__label t-num"));
}

function nav(doc: Document, sarcomere: SarcomereResult, count: number, actions: ResultsActions): HTMLElement {
  const previous = button(doc, "icon-btn", icon(doc, "i-chev-l"));
  previous.setAttribute("aria-label", en.results.previous);
  previous.setAttribute("aria-keyshortcuts", "[");
  previous.dataset.action = "previous";
  previous.addEventListener("click", () => actions.select(-1));
  const next = button(doc, "icon-btn", icon(doc, "i-chev-r"));
  next.setAttribute("aria-label", en.results.next);
  next.setAttribute("aria-keyshortcuts", "]");
  next.dataset.action = "next";
  next.addEventListener("click", () => actions.select(1));
  const title = el(
    doc,
    "p",
    { class: "results__title", data: { part: "results-title" } },
    el(doc, "b", { text: fill(en.panel.item, { n: sarcomere.id }) }),
    " ",
    el(doc, "span", { class: "u-muted", text: fill(en.results.of, { count }) }),
  );
  if (sarcomere.measured) {
    title.append(el(doc, "span", { class: "u-muted", text: " · " }), text(doc, "span", sarcomere.cells.shapeClass, "results__class-tag"));
  }
  return el(
    doc,
    "div",
    { class: "results__nav", attrs: { role: "group", "aria-label": en.results.navLabel } },
    previous,
    title,
    next,
    el(doc, "span", { class: "results__keys", attrs: { "aria-hidden": "true" } }, kbd(doc, "["), kbd(doc, "]")),
  );
}

function measured(doc: Document, results: ShapeResults, sarcomere: SarcomereResult): HTMLElement[] {
  const columns = results.columns;
  if (columns === null) {
    return [];
  }
  const cells = sarcomere.cells;
  const out: HTMLElement[] = [];
  const si = el(
    doc,
    "section",
    { class: "card card--accent results__si", attrs: { "aria-labelledby": "results-si-title" } },
    el(doc, "h3", { class: "card__title", attrs: { id: "results-si-title" } }, `${en.results.shapeIndex} `, text(doc, "abbr", columns.si, "u-nocase")),
    plusMinus(doc, cells.si, "p", "readout__value", "readout__pm"),
  );
  if (results.notes.si !== null) {
    si.append(text(doc, "p", results.notes.si, "t-explain"));
  }
  out.push(si);
  const classBody = el(
    doc,
    "div",
    { class: "results__class-body" },
    el(doc, "p", { class: "t-eyebrow", text: en.results.shapeClass }),
    text(doc, "p", cells.shapeClass, "results__class-name"),
  );
  const classRow = el(doc, "section", { class: "results__class", data: { part: "shape-class" } });
  if (sarcomere.stability !== null && cells.stability.text !== "") {
    const ring = stabilityRing(doc, sarcomere.stability, cells.stability);
    ring.setAttribute("role", "img");
    ring.setAttribute("aria-label", fill(en.results.stability, { share: cells.stability.text }));
    classRow.append(ring);
    if (results.notes.stability !== null) {
      classBody.append(text(doc, "p", results.notes.stability, "t-explain"));
    }
  } else if (results.click !== null) {
    classBody.append(text(doc, "p", results.click, "t-explain"));
  }
  classRow.append(classBody);
  out.push(classRow);
  const metrics = el(
    doc,
    "dl",
    { class: "metrics results__metrics" },
    el(doc, "div", { class: "metric" }, el(doc, "dt", { class: "metric__name" }, `${en.results.aspectRatio} `, text(doc, "abbr", columns.asr, "metric__abbr u-nocase")), plusMinus(doc, cells.asr, "dd", "metric__number", "metric__pm")),
    el(doc, "div", { class: "metric" }, el(doc, "dt", { class: "metric__name" }, `${en.results.curviness} `, text(doc, "abbr", columns.cur, "metric__abbr u-nocase")), plusMinus(doc, cells.cur, "dd", "metric__number", "metric__pm")),
  );
  if (results.notes.asrCur !== null) {
    metrics.append(text(doc, "p", results.notes.asrCur, "metric__explain"));
  }
  out.push(metrics);
  const lengths = el(doc, "dl", { class: "results__lengths", attrs: { "aria-label": en.results.lengths } });
  for (const key of LENGTH_KEYS) {
    lengths.append(el(doc, "div", { class: "results__length" }, text(doc, "dt", columns[key], "results__length-name"), text(doc, "dd", cells[key], "results__length-value t-num")));
  }
  out.push(lengths);
  return out;
}

function summary(doc: Document, results: ShapeResults): HTMLElement | null {
  const table = results.summary;
  if (table === null) {
    return null;
  }
  const list = el(doc, "dl", { class: "metrics results__summary" });
  for (const row of table.rows) {
    list.append(el(doc, "div", { class: "metric" }, text(doc, "dt", row.name, "metric__name"), text(doc, "dd", row.value, "metric__value")));
  }
  for (const note of table.notes) {
    list.append(text(doc, "p", note, "metric__explain"));
  }
  return el(doc, "section", { class: "results__block", attrs: { "aria-labelledby": "results-summary-title" } }, el(doc, "h3", { class: "t-eyebrow", text: en.results.summary, attrs: { id: "results-summary-title" } }), list);
}

function settings(doc: Document, results: ShapeResults | null, context: ResultsContext, actions: ResultsActions): HTMLElement {
  const scaleButton = button(doc, "btn btn--small", el(doc, "span", { text: en.results.editScale }));
  scaleButton.setAttribute("aria-label", en.results.editScaleLabel);
  scaleButton.dataset.action = "edit-scale";
  scaleButton.addEventListener("click", () => actions.editScale());
  const setButton = button(doc, "btn btn--small", el(doc, "span", { text: en.results.setClickError }));
  setButton.setAttribute("aria-label", en.results.setClickErrorLabel);
  setButton.dataset.action = "set-click-error";
  setButton.addEventListener("click", () => actions.setClickError());
  const estimateButton = button(doc, "btn btn--small", el(doc, "span", { text: en.results.estimate }));
  estimateButton.setAttribute("aria-label", en.results.estimateLabel);
  estimateButton.dataset.action = "estimate-click-error";
  estimateButton.addEventListener("click", () => actions.estimateClickError());
  for (const target of [scaleButton, setButton, estimateButton]) {
    target.disabled = !context.ready;
  }
  const scale = el(doc, "div", { class: "results__setting", data: { part: "scale-setting" } }, el(doc, "h3", { class: "t-eyebrow", text: en.results.scale }));
  if (results?.scale != null) {
    scale.append(text(doc, "p", results.scale, "results__setting-text"));
  }
  scale.append(el(doc, "div", { class: "results__setting-actions" }, scaleButton));
  const click = el(
    doc,
    "div",
    { class: "results__setting", data: { part: "click-error" } },
    el(doc, "h3", { class: "t-eyebrow" }, `${en.results.clickError} `, el(doc, "span", { class: "u-nocase", text: "σ" })),
  );
  if (results?.click != null) {
    click.append(text(doc, "p", results.click, "results__setting-text"));
  }
  click.append(el(doc, "div", { class: "results__setting-actions" }, setButton, estimateButton));
  return el(doc, "section", { class: "results__settings" }, scale, click);
}

export function renderResults(doc: Document, host: HTMLElement, context: ResultsContext, actions: ResultsActions): void {
  const children: HTMLElement[] = [];
  const results = context.results;
  const sarcomere = results?.sarcomeres.find((entry) => entry.id === context.selected) ?? null;
  if (!context.hasImage) {
    children.push(el(doc, "div", { class: "empty" }, el(doc, "p", { class: "empty__text", text: en.panel.noImage })));
  } else if (results === null || sarcomere === null) {
    children.push(el(doc, "div", { class: "empty" }, el(doc, "p", { class: "empty__text", text: results !== null && results.sarcomeres.length > 0 ? en.panel.noMeasured : en.panel.noResults })));
  } else {
    children.push(nav(doc, sarcomere, context.count, actions));
    if (sarcomere.measured) {
      children.push(...measured(doc, results, sarcomere));
    } else {
      children.push(el(doc, "p", { class: "note", data: { ref: sarcomere.cells.status.ref } }, icon(doc, "i-warn", "note__icon"), el(doc, "span", { text: sarcomere.cells.status.text })));
    }
  }
  if (context.hasImage) {
    children.push(settings(doc, results, context, actions));
  }
  const block = results !== null && sarcomere !== null ? summary(doc, results) : null;
  if (block !== null) {
    children.push(block);
  }
  host.replaceChildren(...children);
}
