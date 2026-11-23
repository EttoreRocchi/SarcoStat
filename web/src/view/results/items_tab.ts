import type { ModeItem } from "../../state/store.ts";
import { en } from "../../strings/en.ts";
import { fill } from "../../strings/format.ts";
import { button, el } from "../dom.ts";
import { icon } from "../icons.ts";
import type { ShapeResults } from "./model.ts";

export interface ItemsActions {
  show(sarcomere: number): void;
  remove(item: ModeItem): void;
}

export interface ItemsContext {
  readonly items: readonly ModeItem[];
  readonly results: ShapeResults | null;
  readonly selected: number | null;
  readonly hasImage: boolean;
  readonly canDelete: boolean;
}

function shapeRow(doc: Document, item: ModeItem, context: ItemsContext, actions: ItemsActions): HTMLLIElement {
  const id = item.sarcomere_id ?? 0;
  const result = context.results?.sarcomeres.find((entry) => entry.id === id) ?? null;
  const detail = el(doc, "span", { class: "items__detail" });
  if (result !== null && result.measured) {
    detail.append(
      el(doc, "span", { class: "items__class", text: result.cells.shapeClass.text, data: { ref: result.cells.shapeClass.ref } }),
      el(doc, "span", { class: "items__sep", text: "·", attrs: { "aria-hidden": "true" } }),
      el(doc, "abbr", { class: "items__abbr u-nocase", text: context.results?.columns?.si.text ?? "", data: { ref: context.results?.columns?.si.ref ?? "" } }),
      el(doc, "span", { class: "items__value t-num", text: result.cells.si.text, data: { ref: result.cells.si.ref } }),
    );
  } else if (result !== null) {
    detail.append(el(doc, "span", { class: "items__status", text: result.cells.status.text, data: { ref: result.cells.status.ref } }));
  }
  const show = button(doc, "items__show", el(doc, "span", { class: "items__name", text: fill(en.panel.item, { n: id }) }), detail);
  show.setAttribute("aria-label", fill(en.panel.itemSelect, { n: id }));
  show.dataset.action = "show";
  if (id === context.selected) {
    show.setAttribute("aria-current", "true");
  }
  show.addEventListener("click", () => actions.show(id));
  const remove = button(doc, "icon-btn items__delete", icon(doc, "i-x"));
  remove.setAttribute("aria-label", fill(en.panel.itemDelete, { n: id }));
  remove.title = fill(en.panel.itemDelete, { n: id });
  remove.dataset.action = "delete";
  remove.disabled = !context.canDelete;
  remove.addEventListener("click", () => actions.remove(item));
  return el(doc, "li", { class: "items__row", data: { item: item.item_id, sarcomere: String(id) } }, show, remove);
}

function keptRow(doc: Document, item: ModeItem): HTMLLIElement {
  const name = fill(item.type === "chain" ? en.panel.itemChain : item.type === "pair" ? en.panel.itemPair : en.panel.itemOther, { id: item.item_id, type: item.type });
  return el(
    doc,
    "li",
    { class: "items__row items__row--kept", data: { item: item.item_id } },
    el(doc, "span", { class: "items__show" }, el(doc, "span", { class: "items__name", text: name }), el(doc, "span", { class: "items__detail u-muted", text: en.panel.itemKept })),
  );
}

export function renderItems(doc: Document, host: HTMLElement, context: ItemsContext, actions: ItemsActions): void {
  if (!context.hasImage || context.items.length === 0) {
    host.replaceChildren(el(doc, "div", { class: "empty" }, el(doc, "p", { class: "empty__text", text: context.hasImage ? en.panel.noItems : en.panel.noImage })));
    return;
  }
  const list = el(doc, "ul", { class: "items", attrs: { role: "list" } });
  for (const item of context.items) {
    list.append(item.type === "shape" ? shapeRow(doc, item, context, actions) : keptRow(doc, item));
  }
  host.replaceChildren(list);
}
