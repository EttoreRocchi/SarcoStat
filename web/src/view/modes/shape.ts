import { en } from "../../strings/en.ts";
import { fill } from "../../strings/format.ts";
import type { Session } from "../../state/session.ts";
import { shapeGuidance, shapeItemCount, type AppState, type Guidance, type Store } from "../../state/store.ts";
import { button, el, kbd, richText } from "../dom.ts";
import { icon } from "../icons.ts";
import { chipList } from "../shell/guard.ts";
import { panelId, tabId } from "../shell/rail.ts";
import { createWorkspace } from "../workspace/workspace.ts";

export interface ShapeContext {
  readonly store: Store;
  readonly session: Session;
  open(): void;
}

export interface ShapeView {
  readonly stage: HTMLElement;
  readonly panel: HTMLElement;
  readonly workspace: HTMLElement;
  render(state: AppState): void;
}

const RESULT_TABS = ["results", "plots", "items"] as const;
type ResultTab = (typeof RESULT_TABS)[number];

function emptyNote(doc: Document): HTMLParagraphElement {
  return el(doc, "p", { class: "empty__text" });
}

function createHud(doc: Document): { element: HTMLElement; render(guidance: Guidance): void } {
  const count = el(doc, "span", { class: "hud__count" });
  const swatch = el(doc, "span", { class: "lm-swatch", attrs: { "aria-hidden": "true" } });
  const whatText = el(doc, "span");
  const what = el(doc, "span", { class: "hud__what" }, swatch, whatText);
  const chips = el(doc, "span", { class: "hud__chips" });
  const discardText = el(doc, "span");
  const keys = el(
    doc,
    "div",
    { class: "hud__keys", data: { part: "hud-keys" } },
    kbd(doc, en.keys.backspace),
    el(doc, "span", { text: en.hud.undo }),
    el(doc, "span", { class: "hud__sep", text: "·", attrs: { "aria-hidden": "true" } }),
    kbd(doc, en.keys.esc),
    discardText,
    el(doc, "span", { class: "hud__sep", text: "·", attrs: { "aria-hidden": "true" } }),
    kbd(doc, en.keys.enter),
    el(doc, "span", { text: en.hud.confirm }),
  );
  const element = el(
    doc,
    "div",
    { class: "hud", attrs: { role: "status", "aria-live": "polite", "aria-label": en.hud.label }, data: { part: "hud" } },
    el(doc, "div", { class: "hud__row" }, count, what, chips),
    keys,
  );
  element.hidden = true;
  return {
    element,
    render(guidance) {
      if (guidance.kind === "ready") {
        count.replaceChildren(fill(en.hud.ready, { sarcomere: guidance.sarcomere }));
        whatText.textContent = en.hud.readyWhat;
        swatch.className = "lm-swatch";
        swatch.hidden = true;
      } else {
        count.replaceChildren(...richText(doc, en.hud.count, { sarcomere: guidance.sarcomere, number: guidance.number, of: guidance.of }, ["number"]));
        const landmark = guidance.landmark;
        whatText.textContent = landmark === null ? "" : en.landmarks[landmark];
        swatch.hidden = landmark === null;
        swatch.className = landmark === "C" || landmark === "D" ? "lm-swatch lm-swatch--m" : "lm-swatch";
      }
      chips.replaceChildren(chipList(doc, guidance.chips, true));
      discardText.textContent = fill(en.hud.discard, { n: guidance.sarcomere });
    },
  };
}

export function createShapeMode(doc: Document, context: ShapeContext): ShapeView {
  const workspace = el(doc, "div", { class: "stage__layer", data: { part: "workspace" } });
  const openButton = button(doc, "btn btn--primary btn--large", icon(doc, "i-folder", "btn__icon"), el(doc, "span", { text: en.workspace.open }), kbd(doc, "O"));
  openButton.dataset.action = "open";
  openButton.setAttribute("aria-keyshortcuts", "O");
  openButton.addEventListener("click", () => {
    if (openButton.getAttribute("aria-disabled") !== "true") {
      context.open();
    }
  });
  const empty = el(
    doc,
    "div",
    { class: "stage__empty", data: { part: "empty" } },
    icon(doc, "i-image", "ic ic--lg"),
    el(doc, "h2", { class: "stage__title", text: en.workspace.emptyTitle }),
    el(doc, "p", { class: "stage__text", text: en.workspace.emptyText }),
    openButton,
  );
  const caption = el(doc, "p", { class: "stage__caption t-num", data: { part: "image-caption" } });
  let imageOpen = false;
  const imageWorkspace = createWorkspace(doc, workspace, {
    session: context.session,
    onStatus: (status) => {
      caption.hidden = !imageOpen || status === "drawn";
    },
  });
  const hud = createHud(doc);
  let shownGuidance = "";
  const stage = el(
    doc,
    "section",
    { class: "stage", attrs: { id: panelId("shape"), role: "tabpanel", "aria-labelledby": tabId("shape") }, data: { mode: "shape" } },
    workspace,
    empty,
    caption,
    hud.element,
  );

  const count = el(doc, "span", { class: "count", text: "0" });
  const tabs = new Map<ResultTab, HTMLButtonElement>();
  const panels = new Map<ResultTab, HTMLElement>();
  const notes = new Map<ResultTab, HTMLParagraphElement>();
  const itemList = el(doc, "ul", { class: "items", attrs: { role: "list" } });
  const tablist = el(doc, "div", { class: "tabs", attrs: { role: "tablist", "aria-label": en.panel.tabsLabel } });
  const body = el(doc, "div", { class: "panel__body" });
  const select = (name: ResultTab, focus: boolean): void => {
    for (const tabName of RESULT_TABS) {
      const selected = tabName === name;
      const tab = tabs.get(tabName) as HTMLButtonElement;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      (panels.get(tabName) as HTMLElement).hidden = !selected;
    }
    if (focus) {
      tabs.get(name)?.focus();
    }
  };
  for (const name of RESULT_TABS) {
    const label = { results: en.panel.results, plots: en.panel.plots, items: en.panel.items }[name];
    const tab = button(doc, "tabs__tab", label);
    if (name === "items") {
      tab.append(count);
    }
    tab.id = `result-tab-${name}`;
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-controls", `result-panel-${name}`);
    tab.addEventListener("click", () => select(name, false));
    tabs.set(name, tab);
    tablist.append(tab);
    const note = emptyNote(doc);
    notes.set(name, note);
    const panel = el(doc, "div", { class: "tabs__panel", attrs: { id: `result-panel-${name}`, role: "tabpanel", "aria-labelledby": tab.id, tabindex: "0" } }, el(doc, "div", { class: "empty" }, note));
    if (name === "items") {
      panel.append(itemList);
    }
    panels.set(name, panel);
    body.append(panel);
  }
  tablist.addEventListener("keydown", (event) => {
    const at = RESULT_TABS.findIndex((name) => tabs.get(name) === doc.activeElement);
    if (at < 0) {
      return;
    }
    let next = -1;
    if (event.key === "ArrowRight") {
      next = (at + 1) % RESULT_TABS.length;
    } else if (event.key === "ArrowLeft") {
      next = (at - 1 + RESULT_TABS.length) % RESULT_TABS.length;
    } else if (event.key === "Home") {
      next = 0;
    } else if (event.key === "End") {
      next = RESULT_TABS.length - 1;
    }
    if (next >= 0) {
      event.preventDefault();
      event.stopPropagation();
      select(RESULT_TABS[next] as ResultTab, true);
    }
  });
  select("results", false);
  const panel = el(doc, "aside", { class: "panel", attrs: { "aria-label": en.panel.label }, data: { mode: "shape" } }, tablist, body);

  return {
    stage,
    panel,
    workspace,
    render(state) {
      const session = state.session;
      const image = session?.image ?? null;
      empty.hidden = image !== null;
      setOpenEnabled(openButton, state.engine === "ready");
      imageWorkspace.render(state);
      imageOpen = image !== null;
      caption.hidden = !imageOpen || imageWorkspace.status === "drawn";
      caption.textContent = image === null ? "" : fill(en.workspace.caption, { file: image.file_name, size: fill(en.topbar.size, { width: image.width_px, height: image.height_px }) });
      const guidance = shapeGuidance(session);
      hud.element.hidden = guidance === null;
      const key = JSON.stringify(guidance);
      if (guidance !== null && key !== shownGuidance) {
        hud.render(guidance);
      }
      shownGuidance = key;
      const n = shapeItemCount(session);
      count.textContent = String(n);
      (notes.get("results") as HTMLParagraphElement).textContent = image === null ? en.panel.noImage : en.panel.noResults;
      (notes.get("plots") as HTMLParagraphElement).textContent = image === null ? en.panel.noImage : en.panel.noPlots;
      (notes.get("items") as HTMLParagraphElement).textContent = image === null ? en.panel.noImage : en.panel.noItems;
      const items = session?.items ?? [];
      (notes.get("items") as HTMLParagraphElement).hidden = items.length > 0;
      itemList.replaceChildren(
        ...items.map((item) => {
          const skipped = item.type === "shape" && item.marks.length === 4;
          const text =
            item.type === "shape" && item.sarcomere_id !== null
              ? fill(skipped ? en.panel.itemSkipped : en.panel.item, { n: item.sarcomere_id })
              : fill(en.panel.itemOther, { type: item.type, id: item.item_id });
          return el(doc, "li", { class: "items__row", text, data: { item: item.item_id } });
        }),
      );
    },
  };
}

function setOpenEnabled(target: HTMLButtonElement, enabled: boolean): void {
  if (enabled) {
    target.removeAttribute("aria-disabled");
    target.title = `${en.workspace.open} (O)`;
  } else {
    target.setAttribute("aria-disabled", "true");
    target.title = en.topbar.engineStarting;
  }
}
