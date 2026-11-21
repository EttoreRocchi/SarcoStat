import type { RailRoute } from "../../router.ts";
import { en } from "../../strings/en.ts";
import { el } from "../dom.ts";
import { icon } from "../icons.ts";
import { MODE_ICONS, panelId, tabId } from "../shell/rail.ts";

export interface PlaceholderView {
  readonly element: HTMLElement;
}

export function createPlaceholderMode(doc: Document, route: Exclude<RailRoute, "home" | "shape">): PlaceholderView {
  const element = el(
    doc,
    "main",
    { class: "shell__main", attrs: { id: panelId(route), role: "tabpanel", "aria-labelledby": tabId(route) }, data: { mode: route } },
    el(
      doc,
      "div",
      { class: "stage__empty" },
      icon(doc, MODE_ICONS[route], "ic ic--lg"),
      el(doc, "h2", { class: "stage__title", text: en.modes[route].title }),
      el(doc, "p", { class: "stage__text", text: en.absent.card }),
    ),
  );
  return { element };
}
