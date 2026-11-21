import type { ModeState } from "../../state/store.ts";
import { displayToLocal, type Point, type View } from "./viewport.ts";

export type LineKind = "z" | "m" | "chain" | "f1" | "f2";

export interface OverlayLine {
  readonly kind: LineKind;
  readonly role: string;
  readonly from: Point;
  readonly to: Point;
}

export interface OverlayDot {
  readonly kind: LineKind;
  readonly role: string;
  readonly at: Point;
}

export interface OverlayBadge {
  readonly kind: LineKind;
  readonly letter: string;
  readonly at: Point;
}

export interface OverlayTag {
  readonly text: string;
  readonly at: Point;
}

export interface OverlayGroup {
  readonly id: string;
  readonly pending: boolean;
  readonly sides: readonly (readonly Point[])[];
  readonly lines: readonly OverlayLine[];
  readonly dots: readonly OverlayDot[];
  readonly badges: readonly OverlayBadge[];
  readonly tags: readonly OverlayTag[];
}

type Mark = ModeState["pending"]["marks"][number];
type Stroke = ModeState["pending"]["strokes"][number];

export const DOT_RADIUS = 3.5;
export const BADGE_RADIUS = 8.5;
export const BADGE_GAP = 15;
const SVG_NS = "http://www.w3.org/2000/svg";
const PARTNER: Readonly<Record<string, string>> = { A: "B", B: "A", C: "D", D: "C", E: "F", F: "E" };
const LETTERS = new Set(Object.keys(PARTNER));

export function kindOfMark(role: string): LineKind {
  if (role === "C" || role === "D") {
    return "m";
  }
  if (role === "chain") {
    return "chain";
  }
  if (role === "myofibril_2") {
    return "f2";
  }
  if (role === "myofibril_1") {
    return "f1";
  }
  return "z";
}

export function kindOfStroke(role: string): LineKind {
  if (role === "m") {
    return "m";
  }
  if (role === "chain" || role === "myofibril_1" || role === "myofibril_2") {
    return kindOfMark(role);
  }
  return "z";
}

function local(x: number, y: number, view: View): Point {
  return displayToLocal({ x, y }, view);
}

function badgeAt(mark: Mark, marks: readonly Mark[], view: View): Point {
  const at = local(mark.x, mark.y, view);
  const partner = marks.find((other) => other.role === PARTNER[mark.role]);
  if (partner !== undefined) {
    const other = local(partner.x, partner.y, view);
    const dx = at.x - other.x;
    const dy = at.y - other.y;
    const length = Math.hypot(dx, dy);
    if (length > 1e-6) {
      return { x: at.x + (dx / length) * BADGE_GAP, y: at.y + (dy / length) * BADGE_GAP };
    }
  }
  return { x: at.x - BADGE_GAP * Math.SQRT1_2, y: at.y - BADGE_GAP * Math.SQRT1_2 };
}

function sidesOf(marks: readonly Mark[], view: View): Point[][] {
  const point = (role: string): Point | null => {
    const mark = marks.find((candidate) => candidate.role === role);
    return mark === undefined ? null : local(mark.x, mark.y, view);
  };
  const out: Point[][] = [];
  for (const roles of [
    ["A", "C", "E"],
    ["B", "D", "F"],
  ]) {
    const points = roles.map(point).filter((value): value is Point => value !== null);
    if (points.length >= 2 && point(roles[0] as string) !== null && point(roles[2] as string) !== null) {
      out.push(points);
    }
  }
  return out;
}

function centre(points: readonly Point[]): Point {
  const sum = points.reduce((acc, p) => ({ x: acc.x + p.x, y: acc.y + p.y }), { x: 0, y: 0 });
  return { x: sum.x / points.length, y: sum.y / points.length };
}

function group(id: string, pending: boolean, marks: readonly Mark[], strokes: readonly Stroke[], view: View, tag: string | null): OverlayGroup {
  const lines = strokes.map((stroke) => ({
    kind: kindOfStroke(stroke.role),
    role: stroke.role,
    from: local(stroke.p1[0] ?? 0, stroke.p1[1] ?? 0, view),
    to: local(stroke.p2[0] ?? 0, stroke.p2[1] ?? 0, view),
  }));
  const dots = marks.map((mark) => ({ kind: kindOfMark(mark.role), role: mark.role, at: local(mark.x, mark.y, view) }));
  const badges = pending ? marks.filter((mark) => LETTERS.has(mark.role)).map((mark) => ({ kind: kindOfMark(mark.role), letter: mark.role, at: badgeAt(mark, marks, view) })) : [];
  const sides = pending ? [] : sidesOf(marks, view);
  const tags = tag !== null && dots.length > 0 ? [{ text: tag, at: centre(dots.map((dot) => dot.at)) }] : [];
  return { id, pending, sides, lines, dots, badges, tags };
}

export function overlayModel(state: ModeState | null, view: View | null): OverlayGroup[] {
  if (state === null || view === null || state.image === null) {
    return [];
  }
  const out = state.items.map((item) =>
    group(item.item_id, false, item.marks, item.strokes, view, item.sarcomere_id === null ? null : String(item.sarcomere_id)),
  );
  if (state.pending.marks.length > 0 || state.pending.strokes.length > 0) {
    out.push(group("pending", true, state.pending.marks, state.pending.strokes, view, null));
  }
  return out;
}

function svg<K extends keyof SVGElementTagNameMap>(doc: Document, tag: K, className: string, attrs: Readonly<Record<string, string | number>>): SVGElementTagNameMap[K] {
  const element = doc.createElementNS(SVG_NS, tag);
  element.setAttribute("class", className);
  for (const [name, value] of Object.entries(attrs)) {
    element.setAttribute(name, String(value));
  }
  return element;
}

function points(list: readonly Point[]): string {
  return list.map((p) => `${p.x},${p.y}`).join(" ");
}

export function renderGroup(doc: Document, model: OverlayGroup): SVGGElement {
  const g = svg(doc, "g", model.pending ? "ov__item ov__item--pending" : "ov__item", { "data-item": model.id });
  for (const side of model.sides) {
    g.append(svg(doc, "polyline", "ov__side", { points: points(side) }));
  }
  for (const line of model.lines) {
    const ends = { x1: line.from.x, y1: line.from.y, x2: line.to.x, y2: line.to.y };
    g.append(svg(doc, "line", "ov__halo", ends));
    g.append(svg(doc, "line", `ov__line ov__line--${line.kind}`, { ...ends, "data-role": line.role }));
  }
  for (const dot of model.dots) {
    g.append(svg(doc, "circle", `ov__dot ov__dot--${dot.kind}`, { cx: dot.at.x, cy: dot.at.y, r: DOT_RADIUS, "data-role": dot.role }));
  }
  for (const badge of model.badges) {
    const b = svg(doc, "g", `ov__badge ov__badge--${badge.kind}`, { "data-role": badge.letter });
    b.append(svg(doc, "circle", "ov__badge-disc", { cx: badge.at.x, cy: badge.at.y, r: BADGE_RADIUS }));
    const text = svg(doc, "text", "ov__badge-text", { x: badge.at.x, y: badge.at.y, dy: "0.35em" });
    text.textContent = badge.letter;
    b.append(text);
    g.append(b);
  }
  for (const tag of model.tags) {
    const t = svg(doc, "g", "ov__tag", {});
    t.append(svg(doc, "rect", "ov__tag-box", { x: tag.at.x - 10, y: tag.at.y - 8, width: 20, height: 16, rx: 3 }));
    const text = svg(doc, "text", "ov__tag-text", { x: tag.at.x, y: tag.at.y, dy: "0.35em" });
    text.textContent = tag.text;
    t.append(text);
    g.append(t);
  }
  return g;
}

export interface Overlay {
  readonly element: SVGSVGElement;
  render(state: ModeState | null, view: View | null): void;
  setVisible(visible: boolean): void;
}

export function createOverlay(doc: Document): Overlay {
  const element = svg(doc, "svg", "ov", { "aria-hidden": "true", focusable: "false", "data-part": "overlay" });
  return {
    element,
    render(state, view) {
      element.replaceChildren(...overlayModel(state, view).map((model) => renderGroup(doc, model)));
    },
    setVisible(visible) {
      if (visible) {
        element.removeAttribute("visibility");
        element.dataset.visible = "true";
      } else {
        element.setAttribute("visibility", "hidden");
        element.dataset.visible = "false";
      }
    },
  };
}
