import { en } from "../../strings/en.ts";
import { fill } from "../../strings/format.ts";
import type { Session } from "../../state/session.ts";
import { absentLine, shapeGuidance, shapeItemCount, type AppState, type Guidance, type ModeItem, type Store } from "../../state/store.ts";
import { button, el, kbd, richText } from "../dom.ts";
import { icon } from "../icons.ts";
import { shapeSiPlot } from "../plots/shape_si.ts";
import { renderItems } from "../results/items_tab.ts";
import { pickSarcomere, sarcomereIds, shapeResults, type ShapeResults } from "../results/model.ts";
import { renderResults } from "../results/results_tab.ts";
import { chipList } from "../shell/guard.ts";
import { panelId, tabId } from "../shell/rail.ts";
import type { ViewKeyAction } from "../shell/shortcuts.ts";
import { createWorkspace } from "../workspace/workspace.ts";

export interface ShapeContext {
  readonly store: Store;
  readonly session: Session;
  open(): void;
  editScale(): void;
  setClickError(): void;
  estimateClickError(): void;
  remove(item: ModeItem): void;
}

export interface ShapeView {
  readonly stage: HTMLElement;
  readonly panel: HTMLElement;
  readonly workspace: HTMLElement;
  render(state: AppState): void;
  view(action: ViewKeyAction): void;
  step(step: -1 | 1): void;
}

export const RESULT_TABS = ["results", "plots", "items"] as const;
export type ResultTab = (typeof RESULT_TABS)[number];

function createHud(doc: Document): { element: HTMLElement; render(guidance: Guidance): void } {
  const count = el(doc, "span", { class: "hud__count" });
  const swatch = el(doc, "span", { class: "lm-swatch", attrs: { "aria-hidden": "true" } });
  const whatText = el(doc, "span");
  const what = el(doc, "span", { class: "hud__what" }, swatch, whatText);
  const chips = el(doc, "span", { class: "hud__chips" });
  const hint = el(doc, "p", { class: "hud__hint", data: { part: "hud-hint" } });
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
    hint,
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
        hint.textContent = en.hud.hint.ready;
      } else {
        count.replaceChildren(...richText(doc, en.hud.count, { sarcomere: guidance.sarcomere, number: guidance.number, of: guidance.of }, ["number"]));
        const landmark = guidance.landmark;
        whatText.textContent = landmark === null ? "" : en.landmarks[landmark];
        swatch.hidden = landmark === null;
        swatch.className = landmark === "C" || landmark === "D" ? "lm-swatch lm-swatch--m" : "lm-swatch";
        hint.textContent = landmark === null ? "" : en.hud.hint[landmark];
      }
      hint.hidden = hint.textContent === "";
      chips.replaceChildren(chipList(doc, guidance.chips, true));
      discardText.textContent = fill(en.hud.discard, { n: guidance.sarcomere });
    },
  };
}

function openButton(doc: Document, label: HTMLElement, onClick: () => void): HTMLButtonElement {
  const target = button(doc, "btn btn--primary btn--large", icon(doc, "i-folder", "btn__icon"), label, kbd(doc, "O"));
  target.dataset.action = "open";
  target.setAttribute("aria-keyshortcuts", "O");
  target.addEventListener("click", () => {
    if (target.getAttribute("aria-disabled") !== "true") {
      onClick();
    }
  });
  return target;
}

export function createShapeMode(doc: Document, context: ShapeContext): ShapeView {
  const workspace = el(doc, "div", { class: "stage__layer", data: { part: "workspace" } });
  const emptyTitle = el(doc, "h2", { class: "stage__title", text: en.workspace.emptyTitle });
  const emptyText = el(doc, "p", { class: "stage__text", text: en.workspace.emptyText });
  const openLabel = el(doc, "span", { text: en.workspace.open });
  const open = openButton(doc, openLabel, () => context.open());
  const empty = el(doc, "div", { class: "stage__empty", data: { part: "empty" } }, icon(doc, "i-image", "ic ic--lg"), emptyTitle, emptyText, open);
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
  const tablist = el(doc, "div", { class: "tabs", attrs: { role: "tablist", "aria-label": en.panel.tabsLabel } });
  const absent = el(doc, "p", { class: "note results__absent", attrs: { role: "note" }, data: { part: "absent-modes" } }, icon(doc, "i-warn", "note__icon"));
  const absentText = el(doc, "span");
  absent.append(absentText);
  absent.hidden = true;
  const body = el(doc, "div", { class: "panel__body" }, absent);
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
    tab.dataset.tab = name;
    tab.addEventListener("click", () => select(name, false));
    tabs.set(name, tab);
    tablist.append(tab);
    const panel = el(doc, "div", { class: "tabs__panel", attrs: { id: `result-panel-${name}`, role: "tabpanel", "aria-labelledby": tab.id, tabindex: "0" }, data: { part: `panel-${name}` } });
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

  let last: AppState | null = null;
  let results: ShapeResults | null = null;
  let shownResults: unknown = undefined;
  let selected: number | null = null;
  let knownIds: number[] = [];
  let painted = "";

  const paintPanel = (state: AppState): void => {
    const session = state.session;
    const hasImage = session?.image != null;
    const ids = sarcomereIds(session);
    const key = JSON.stringify([selected, hasImage, state.engine, session?.items.map((item) => [item.item_id, item.sarcomere_id]) ?? null, session?.controls.can_delete ?? false]);
    if (state.results === shownResults && key === painted) {
      return;
    }
    shownResults = state.results;
    painted = key;
    results = shapeResults(state.results);
    const ready = state.engine === "ready";
    renderResults(doc, panels.get("results") as HTMLElement, { results, selected, count: ids.length, hasImage, ready }, {
      select: (step) => moveSelection(step),
      editScale: () => context.editScale(),
      setClickError: () => context.setClickError(),
      estimateClickError: () => context.estimateClickError(),
    });
    const plots = panels.get("plots") as HTMLElement;
    if (results !== null && results.points.length > 0) {
      plots.replaceChildren(shapeSiPlot(doc, results.points, results.band, selected));
    } else {
      plots.replaceChildren(el(doc, "div", { class: "empty" }, el(doc, "p", { class: "empty__text", text: hasImage ? en.panel.noPlots : en.panel.noImage })));
    }
    renderItems(doc, panels.get("items") as HTMLElement, { items: session?.items ?? [], results, selected, hasImage, canDelete: ready }, {
      show: (id) => {
        selected = id;
        select("results", false);
        repaint();
      },
      remove: (item) => context.remove(item),
    });
  };

  const repaint = (): void => {
    if (last !== null) {
      painted = "";
      paintPanel(last);
    }
  };

  const moveSelection = (step: -1 | 1): void => {
    const next = pickSarcomere(knownIds, selected, step);
    if (next !== selected) {
      selected = next;
      repaint();
    }
  };

  return {
    stage,
    panel,
    workspace,
    view(action) {
      imageWorkspace.view(action);
    },
    step(direction) {
      moveSelection(direction);
    },
    render(state) {
      last = state;
      const session = state.session;
      const image = session?.image ?? null;
      const pixels = state.source !== null;
      empty.hidden = image !== null && pixels;
      if (image !== null && !pixels) {
        emptyTitle.textContent = fill(en.workspace.pixelsTitle, { file: image.file_name });
        emptyText.textContent = fill(en.workspace.pixelsText, { file: image.file_name });
        openLabel.textContent = fill(en.workspace.pixelsOpen, { file: image.file_name });
      } else {
        emptyTitle.textContent = en.workspace.emptyTitle;
        emptyText.textContent = en.workspace.emptyText;
        openLabel.textContent = en.workspace.open;
      }
      setOpenEnabled(open, state.engine === "ready");
      imageWorkspace.render(state);
      imageOpen = image !== null && pixels;
      caption.hidden = !imageOpen || imageWorkspace.status === "drawn";
      caption.textContent = image === null ? "" : fill(en.workspace.caption, { file: image.file_name, size: fill(en.topbar.size, { width: image.width_px, height: image.height_px }) });
      const guidance = pixels ? shapeGuidance(session) : null;
      hud.element.hidden = guidance === null;
      const guidanceKey = JSON.stringify(guidance);
      if (guidance !== null && guidanceKey !== shownGuidance) {
        hud.render(guidance);
      }
      shownGuidance = guidanceKey;
      count.textContent = String(shapeItemCount(session));
      const line = absentLine(session);
      absent.hidden = line === null;
      absentText.textContent = line ?? "";
      const ids = sarcomereIds(session);
      if (ids.length > knownIds.length && ids.length > 0) {
        selected = ids[ids.length - 1] ?? null;
      } else if (selected === null || !ids.includes(selected)) {
        selected = pickSarcomere(ids, selected, 0);
      }
      knownIds = ids;
      paintPanel(state);
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
