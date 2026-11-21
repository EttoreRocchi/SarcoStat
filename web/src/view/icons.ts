import iconsUrl from "../assets/icons.svg?url";

export type IconId =
  | "mark"
  | "i-home"
  | "i-shape"
  | "i-straight"
  | "i-register"
  | "i-planner"
  | "i-study"
  | "i-download"
  | "i-folder"
  | "i-keyboard"
  | "i-chev-d"
  | "i-warn"
  | "i-lock"
  | "i-image"
  | "i-file"
  | "i-plot"
  | "i-x"
  | "i-check";

const SVG_NS = "http://www.w3.org/2000/svg";

export function icon(doc: Document, id: IconId, className = "ic"): SVGSVGElement {
  const svg = doc.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", className);
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  if (id === "mark") {
    svg.setAttribute("viewBox", "0 0 48 48");
  }
  const use = doc.createElementNS(SVG_NS, "use");
  use.setAttribute("href", `${iconsUrl}#${id}`);
  svg.append(use);
  return svg;
}
