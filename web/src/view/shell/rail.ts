import { isAbsent, keyOf, RAIL_ROUTES, type RailRoute, type Route } from "../../router.ts";
import { en } from "../../strings/en.ts";
import { fill } from "../../strings/format.ts";
import { button, el, kbd, setDisabled } from "../dom.ts";
import { icon, type IconId } from "../icons.ts";

export const MODE_ICONS: Readonly<Record<Route, IconId>> = {
  home: "i-home",
  shape: "i-shape",
  straightness: "i-straight",
  register: "i-register",
  planner: "i-planner",
  study: "i-study",
};

export interface RailHandlers {
  select(route: RailRoute): void;
  study(): void;
  keys(): void;
}

export interface Rail {
  readonly element: HTMLElement;
  setActive(route: Route | null): void;
  tabFor(route: RailRoute): HTMLButtonElement;
}

export function tabId(route: Route): string {
  return `tab-${route}`;
}

export function panelId(route: Route): string {
  return `panel-${route}`;
}

export function createRail(doc: Document, absentFamilies: readonly string[], handlers: RailHandlers): Rail {
  const brand = button(doc, "rail__brand", icon(doc, "mark", "mark"));
  brand.setAttribute("aria-label", en.rail.brand);
  brand.addEventListener("click", () => handlers.select("home"));
  const list = el(doc, "div", { class: "rail__list", attrs: { role: "tablist", "aria-orientation": "vertical", "aria-label": en.rail.label } });
  const tabs = new Map<RailRoute, HTMLButtonElement>();
  for (const route of RAIL_ROUTES) {
    const key = keyOf(route);
    const strings = en.modes[route];
    const tab = button(
      doc,
      "rail__item",
      icon(doc, MODE_ICONS[route], "rail__icon"),
      el(doc, "span", { class: "rail__label", text: strings.short }),
      kbd(doc, key, "key key--small rail__key"),
    );
    tab.id = tabId(route);
    tab.dataset.mode = route;
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-selected", "false");
    tab.setAttribute("aria-controls", panelId(route));
    tab.setAttribute("aria-keyshortcuts", key);
    tab.tabIndex = -1;
    const absent = isAbsent(route, absentFamilies);
    tab.setAttribute("aria-label", absent ? `${strings.title}, ${en.absent.sheet}` : strings.title);
    tab.title = absent ? fill(en.absent.notice, { mode: strings.title }) : `${strings.title} (${key})`;
    setDisabled(tab, absent);
    tab.addEventListener("click", () => handlers.select(route));
    tabs.set(route, tab);
    list.append(tab);
  }
  list.addEventListener("keydown", (event) => {
    const order = [...tabs.values()];
    const at = order.indexOf(doc.activeElement as HTMLButtonElement);
    if (at < 0) {
      return;
    }
    let next = -1;
    if (event.key === "ArrowDown") {
      next = (at + 1) % order.length;
    } else if (event.key === "ArrowUp") {
      next = (at - 1 + order.length) % order.length;
    } else if (event.key === "Home") {
      next = 0;
    } else if (event.key === "End") {
      next = order.length - 1;
    }
    if (next >= 0) {
      event.preventDefault();
      event.stopPropagation();
      order[next]?.focus();
    }
  });
  const study = button(doc, "rail__item", icon(doc, MODE_ICONS.study, "rail__icon"), el(doc, "span", { class: "rail__label", text: en.rail.study }));
  study.dataset.mode = "study";
  study.setAttribute("aria-label", en.rail.studyLabel);
  study.title = en.rail.studyLabel;
  study.addEventListener("click", () => handlers.study());
  const keys = button(doc, "rail__item", icon(doc, "i-keyboard", "rail__icon"), el(doc, "span", { class: "rail__label", text: en.rail.keys }), kbd(doc, en.keys.sheet, "key key--small rail__key"));
  keys.setAttribute("aria-label", en.rail.keysLabel);
  keys.setAttribute("aria-keyshortcuts", "?");
  keys.title = `${en.rail.keysLabel} (?)`;
  keys.dataset.action = "keys";
  keys.addEventListener("click", () => handlers.keys());
  const element = el(doc, "nav", { class: "rail", attrs: { "aria-label": en.rail.label } }, brand, list, el(doc, "div", { class: "rail__foot" }, study, keys));
  return {
    element,
    setActive(route) {
      const active = route === null || route === "study" ? null : route;
      for (const [name, tab] of tabs) {
        const selected = name === active;
        tab.setAttribute("aria-selected", String(selected));
        tab.tabIndex = selected ? 0 : -1;
      }
      if (active === null) {
        tabs.get("home")?.setAttribute("tabindex", "0");
      }
    },
    tabFor(route) {
      return tabs.get(route) as HTMLButtonElement;
    },
  };
}
