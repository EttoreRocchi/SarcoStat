import { en } from "../../strings/en.ts";
import type { ExportEntry } from "../../state/store.ts";
import { button, el, isDisabled, kbd, setDisabled } from "../dom.ts";
import { icon } from "../icons.ts";
import type { Keyboard } from "./keyboard.ts";
import { Modal } from "./modal.ts";

export type ExportId = ExportEntry["id"];

export interface ExportMenu {
  readonly element: HTMLElement;
  readonly trigger: HTMLButtonElement;
  readonly isOpen: boolean;
  render(entries: readonly ExportEntry[]): void;
  open(): void;
  close(): void;
}

const GROUP_TITLES: Readonly<Record<ExportEntry["group"], string>> = {
  annotations: en.exportMenu.annotations,
  results: en.exportMenu.results,
  plots: en.exportMenu.plots,
};

export function createExportMenu(doc: Document, keyboard: Keyboard, onChoose: (id: ExportId) => void): ExportMenu {
  const trigger = button(doc, "btn btn--primary", icon(doc, "i-download", "btn__icon"), el(doc, "span", { text: en.topbar.export }), kbd(doc, "E"), icon(doc, "i-chev-d", "btn__chev"));
  trigger.setAttribute("aria-haspopup", "dialog");
  trigger.setAttribute("aria-expanded", "false");
  trigger.setAttribute("aria-controls", "export-menu");
  trigger.dataset.action = "export";
  const list = el(doc, "div", { attrs: { role: "menu", "aria-label": en.exportMenu.formatsLabel } });
  const popup = el(
    doc,
    "div",
    { class: "menu menu--below-end", attrs: { id: "export-menu", role: "dialog", "aria-modal": "true", "aria-label": en.exportMenu.label } },
    list,
    el(doc, "p", { class: "menu__note", text: en.exportMenu.note }),
  );
  popup.hidden = true;
  const anchor = el(doc, "div", { class: "menu-anchor" }, trigger, popup);
  let items: HTMLButtonElement[] = [];

  const move = (delta: number | "first" | "last"): void => {
    if (items.length === 0) {
      return;
    }
    const at = items.indexOf(doc.activeElement as HTMLButtonElement);
    let next = 0;
    if (delta === "first") {
      next = 0;
    } else if (delta === "last") {
      next = items.length - 1;
    } else {
      next = at < 0 ? 0 : (at + delta + items.length) % items.length;
    }
    items[next]?.focus();
  };
  const choose = (item: HTMLButtonElement): void => {
    if (isDisabled(item)) {
      return;
    }
    modal.close();
    onChoose(item.dataset.export as ExportId);
  };
  const modal = new Modal({
    layer: popup,
    dialog: popup,
    keyboard,
    background: () => [],
    onOutside: () => close(),
    onKey: (event) => {
      if (event.ctrlKey || event.metaKey || event.altKey) {
        return false;
      }
      switch (event.key) {
        case "Escape":
          close();
          return true;
        case "ArrowDown":
          move(1);
          return true;
        case "ArrowUp":
          move(-1);
          return true;
        case "Home":
          move("first");
          return true;
        case "End":
          move("last");
          return true;
        case "e":
        case "E": {
          const v1 = items.find((item) => item.dataset.export === "v1");
          if (v1 !== undefined && !event.repeat) {
            choose(v1);
          }
          return true;
        }
        default:
          return false;
      }
    },
  });
  const close = (): void => {
    modal.close();
    trigger.setAttribute("aria-expanded", "false");
  };
  const open = (): void => {
    if (isDisabled(trigger)) {
      return;
    }
    trigger.setAttribute("aria-expanded", "true");
    modal.open(items.find((item) => !isDisabled(item)) ?? items[0] ?? null);
  };
  trigger.addEventListener("click", () => (modal.isOpen ? close() : open()));

  return {
    element: anchor,
    trigger,
    get isOpen() {
      return modal.isOpen;
    },
    render(entries) {
      list.replaceChildren();
      items = [];
      for (const group of ["annotations", "results", "plots"] as const) {
        const groupEntries = entries.filter((entry) => entry.group === group);
        if (groupEntries.length === 0) {
          continue;
        }
        const headingId = `export-group-${group}`;
        const section = el(doc, "div", { attrs: { role: "group", "aria-labelledby": headingId } });
        section.append(el(doc, "div", { class: "menu__heading", text: GROUP_TITLES[group], attrs: { id: headingId } }));
        for (const entry of groupEntries) {
          const text = el(doc, "span", { class: "menu__text" }, el(doc, "span", { text: entry.title }), el(doc, "span", { class: "menu__hint", text: entry.hint }));
          if (entry.reason !== null) {
            const reasonId = `export-reason-${entry.id}`;
            text.append(el(doc, "span", { class: "menu__hint menu__reason", text: entry.reason, attrs: { id: reasonId } }));
          }
          const item = button(doc, "menu__item", icon(doc, group === "plots" ? "i-plot" : "i-file", "menu__icon"), text, entry.key === null ? null : kbd(doc, entry.key));
          item.setAttribute("role", "menuitem");
          item.tabIndex = -1;
          item.dataset.export = entry.id;
          setDisabled(item, !entry.enabled);
          if (entry.reason !== null) {
            item.setAttribute("aria-describedby", `export-reason-${entry.id}`);
          }
          item.addEventListener("click", () => choose(item));
          items.push(item);
          section.append(item);
        }
        list.append(section);
      }
    },
    open,
    close,
  };
}
