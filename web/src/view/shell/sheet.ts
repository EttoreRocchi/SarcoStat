import { ABSENT_FAMILIES } from "../../worker/protocol.ts";
import { isAbsent, keyOf, type Route } from "../../router.ts";
import { en } from "../../strings/en.ts";
import { button, el, kbd } from "../dom.ts";
import type { Keyboard } from "./keyboard.ts";
import { Modal } from "./modal.ts";

export interface SheetRow {
  readonly keys: readonly string[];
  readonly text: string;
  readonly route?: Route;
  readonly later?: boolean;
}

export interface SheetGroup {
  readonly id: keyof typeof en.sheet.groups;
  readonly title: string;
  readonly rows: readonly SheetRow[];
}

const r = en.sheet.rows;
const k = en.keys;

export const SHEET: readonly SheetGroup[] = [
  {
    id: "global",
    title: en.sheet.groups.global,
    rows: [
      { keys: [keyOf("home")], text: r.home, route: "home" },
      { keys: [keyOf("shape")], text: r.shape, route: "shape" },
      { keys: [keyOf("straightness")], text: r.straightness, route: "straightness" },
      { keys: [keyOf("register")], text: r.register, route: "register" },
      { keys: [keyOf("planner")], text: r.planner, route: "planner" },
      { keys: ["O"], text: r.open },
      { keys: ["E"], text: r.export },
      { keys: [k.sheet], text: r.sheet },
    ],
  },
  {
    id: "view",
    title: en.sheet.groups.view,
    rows: [
      { keys: ["+", "−"], text: r.zoom },
      { keys: ["F"], text: r.fit },
      { keys: [k.space], text: r.pan },
      { keys: ["W"], text: r.window, later: true },
      { keys: ["L"], text: r.layers },
      { keys: ["A"], text: r.annotateTool },
    ],
  },
  {
    id: "annotate",
    title: en.sheet.groups.annotate,
    rows: [
      { keys: [k.click], text: r.place },
      { keys: [k.backspace], text: r.undo },
      { keys: [k.esc], text: r.discard },
      { keys: [k.enter], text: r.confirm },
      { keys: ["N"], text: r.next },
      { keys: ["[", "]"], text: r.items },
      { keys: ["V"], text: r.reference, route: "straightness" },
    ],
  },
  {
    id: "study",
    title: en.sheet.groups.study,
    rows: [
      { keys: [k.backspace], text: r.studyUndo },
      { keys: ["M"], text: r.studyMLine },
      { keys: ["S"], text: r.studySkip },
      { keys: ["P"], text: r.studyPause },
      { keys: [k.enter], text: r.studyNext },
      { keys: ["N"], text: r.studyNextMyofibril },
      { keys: ["+", "−"], text: r.studyZoom },
      { keys: ["F"], text: r.studyFit },
      { keys: [k.space], text: r.studyPan },
    ],
  },
];

export function sheetKeys(sheet: readonly SheetGroup[] = SHEET): string[] {
  return sheet.flatMap((group) => group.rows.flatMap((row) => row.keys));
}

export interface ShortcutSheet {
  readonly element: HTMLElement;
  readonly isOpen: boolean;
  open(): void;
  close(): void;
  toggle(): void;
}

export function createSheet(doc: Document, keyboard: Keyboard, background: () => readonly HTMLElement[]): ShortcutSheet {
  const title = el(doc, "h2", { class: "dialog__title", text: en.sheet.title, attrs: { id: "sheet-title" } });
  const grid = el(doc, "div", { class: "keys-grid" });
  for (const group of SHEET) {
    const section = el(doc, "section", { class: "keys-grid__group", attrs: { "aria-labelledby": `sheet-${group.id}` } });
    section.append(el(doc, "h3", { class: "keys-grid__heading", text: group.title, attrs: { id: `sheet-${group.id}` } }));
    for (const row of group.rows) {
      const keys = el(doc, "span", { class: "keys-grid__keys" });
      row.keys.forEach((label) => keys.append(kbd(doc, label)));
      const text = el(doc, "span", { text: row.text });
      if (row.later === true || (row.route !== undefined && isAbsent(row.route, ABSENT_FAMILIES))) {
        text.append(" ", el(doc, "span", { class: "u-muted", text: `(${en.absent.sheet})` }));
      }
      section.append(el(doc, "div", { class: "keys-grid__row", data: { keys: row.keys.join(" ") } }, keys, text));
    }
    grid.append(section);
  }
  const close = button(doc, "btn btn--ghost", en.sheet.close, kbd(doc, en.keys.esc));
  const dialog = el(
    doc,
    "div",
    { class: "dialog dialog--wide", attrs: { role: "dialog", "aria-modal": "true", "aria-labelledby": "sheet-title", tabindex: "-1" } },
    title,
    grid,
    el(doc, "div", { class: "dialog__actions" }, close),
  );
  const layer = el(doc, "div", { class: "scrim", data: { part: "sheet" } }, dialog);
  layer.hidden = true;
  const modal = new Modal({
    layer,
    dialog,
    keyboard,
    background,
    onKey: (event) => {
      if (event.key === "Escape" || (event.key === "?" && !event.ctrlKey && !event.metaKey && !event.altKey)) {
        modal.close();
        return true;
      }
      return false;
    },
  });
  close.addEventListener("click", () => modal.close());
  layer.addEventListener("click", (event) => {
    if (event.target === layer) {
      modal.close();
    }
  });
  return {
    element: layer,
    get isOpen() {
      return modal.isOpen;
    },
    open: () => modal.open(close),
    close: () => modal.close(),
    toggle: () => (modal.isOpen ? modal.close() : modal.open(close)),
  };
}
