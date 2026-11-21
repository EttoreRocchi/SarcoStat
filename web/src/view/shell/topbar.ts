import type { Route } from "../../router.ts";
import { en } from "../../strings/en.ts";
import { fill } from "../../strings/format.ts";
import { exportEntries, notExportedLabel, type AppState } from "../../state/store.ts";
import { button, el, kbd, setDisabled } from "../dom.ts";
import { icon } from "../icons.ts";
import type { ExportMenu } from "./export_menu.ts";
import { MODE_ICONS } from "./rail.ts";

export interface TopbarHandlers {
  open(): void;
  keys(): void;
}

export interface Topbar {
  readonly element: HTMLElement;
  render(state: AppState, route: Route): void;
}

export function formatScale(pixelSize: number, unit: string): string {
  if (unit === "px") {
    return en.topbar.scaleUnset;
  }
  return fill(en.topbar.scale, { size: Number(pixelSize.toPrecision(6)).toString(), unit });
}

export function createTopbar(doc: Document, menu: ExportMenu, handlers: TopbarHandlers): Topbar {
  let modeIcon: SVGSVGElement = icon(doc, MODE_ICONS.home, "topbar__icon");
  const title = el(doc, "h1", { class: "topbar__title" });
  const desc = el(doc, "p", { class: "topbar__desc" });
  const mode = el(doc, "div", { class: "topbar__mode" }, modeIcon, el(doc, "div", { class: "topbar__heading" }, title, desc));
  const fileName = el(doc, "span", { class: "topbar__name" });
  const fileMeta = el(doc, "span", { class: "topbar__file-meta t-num" });
  const scale = el(doc, "span", { class: "topbar__scale", attrs: { title: en.topbar.scaleLabel } });
  const file = el(doc, "div", { class: "topbar__file", data: { part: "file" } }, icon(doc, "i-image", "ic ic--md"), fileName, fileMeta, scale);
  const unsavedText = el(doc, "span");
  const unsaved = el(doc, "span", { class: "unsaved", attrs: { role: "status" }, data: { part: "unsaved" } }, el(doc, "span", { class: "unsaved__dot", attrs: { "aria-hidden": "true" } }), unsavedText);
  const open = button(doc, "btn btn--secondary", icon(doc, "i-folder", "btn__icon"), el(doc, "span", { text: en.topbar.open }), kbd(doc, "O"));
  open.setAttribute("aria-label", en.topbar.openLabel);
  open.setAttribute("aria-keyshortcuts", "O");
  open.dataset.action = "open";
  open.addEventListener("click", () => {
    if (open.getAttribute("aria-disabled") !== "true") {
      handlers.open();
    }
  });
  menu.trigger.setAttribute("aria-keyshortcuts", "E");
  const keys = button(doc, "btn btn--ghost", icon(doc, "i-keyboard", "btn__icon"), el(doc, "span", { text: en.topbar.keys }), kbd(doc, en.keys.sheet));
  keys.setAttribute("aria-keyshortcuts", "?");
  keys.dataset.action = "keys";
  keys.addEventListener("click", () => handlers.keys());
  const actions = el(doc, "div", { class: "topbar__actions" }, unsaved, open, menu.element, keys);
  const element = el(doc, "header", { class: "topbar" }, mode, file, actions);
  let shownRoute: Route | null = null;
  return {
    element,
    render(state, route) {
      if (route !== shownRoute) {
        shownRoute = route;
        const strings = en.modes[route];
        const next = icon(doc, MODE_ICONS[route], "topbar__icon");
        modeIcon.replaceWith(next);
        modeIcon = next;
        title.textContent = strings.title;
        desc.textContent = strings.desc;
      }
      const annotating = route === "shape";
      const image = state.session?.image ?? null;
      file.hidden = !annotating || image === null;
      if (image !== null) {
        fileName.textContent = image.file_name;
        fileMeta.textContent = fill(en.topbar.size, { width: image.width_px, height: image.height_px });
        const settings = state.session?.settings;
        scale.textContent = settings === undefined ? "" : formatScale(settings.pixel_size, settings.unit);
      }
      const label = annotating ? notExportedLabel(state) : null;
      unsaved.hidden = label === null;
      unsaved.classList.toggle("unsaved--clear", label?.clear ?? false);
      if (unsavedText.textContent !== (label?.text ?? "")) {
        unsavedText.textContent = label?.text ?? "";
      }
      open.hidden = !annotating;
      menu.element.hidden = !annotating;
      keys.hidden = annotating;
      const ready = state.engine === "ready";
      setDisabled(open, !ready);
      open.title = ready ? `${en.topbar.openLabel} (O)` : en.topbar.engineStarting;
      setDisabled(menu.trigger, !ready);
      if (!menu.isOpen) {
        menu.render(exportEntries(state));
      }
    },
  };
}
