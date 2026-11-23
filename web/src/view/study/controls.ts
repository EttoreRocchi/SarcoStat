import iconsUrl from "../../assets/icons.svg?url";
import type { StudyViewState } from "../../state/study_session.ts";
import { en } from "../../strings/en.ts";
import { fill } from "../../strings/format.ts";
import { button, el, kbd, setDisabled, isDisabled } from "../dom.ts";
import { chips, prompt } from "./words.ts";

const SVG_NS = "http://www.w3.org/2000/svg";
const t = en.study;

export type StudyIcon = "i-pause" | "i-undo" | "i-skip" | "i-check" | "i-warn" | "i-folder" | "i-download" | "i-study" | "i-lock" | "mark";

export function studyIcon(doc: Document, id: StudyIcon, className = "ic"): SVGSVGElement {
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

export function actionButton(doc: Document, className: string, action: string, label: string, key: string | null, iconId: StudyIcon | null = null): HTMLButtonElement {
  const out = button(doc, className, iconId === null ? null : studyIcon(doc, iconId, "ic btn__icon"), el(doc, "span", { text: label }), key === null ? null : kbd(doc, key));
  out.dataset.action = action;
  out.addEventListener("pointerdown", (event) => event.preventDefault());
  if (key !== null) {
    out.setAttribute("aria-keyshortcuts", key === "Backspace" || key === "Enter" ? key : key.toUpperCase());
  }
  return out;
}

export interface Progress {
  readonly element: HTMLElement;
  render(done: number, total: number, skipped: readonly boolean[], open: boolean): void;
}

export function createProgress(doc: Document): Progress {
  const ticks = el(doc, "ol", { class: "study-ticks", attrs: { "aria-hidden": "true" }, data: { part: "ticks" } });
  const count = el(doc, "span", { class: "study-progress__count", data: { part: "item-count" } });
  const element = el(doc, "div", { class: "study-progress", attrs: { role: "group", "aria-label": t.header.progress } }, ticks, count);
  element.hidden = true;
  return {
    element,
    render(done, total, skipped, open) {
      const items: HTMLElement[] = [];
      for (let index = 0; index < total; index += 1) {
        const state = index < done ? (skipped[index] === true ? "skipped" : "done") : index === done && open ? "current" : "todo";
        items.push(el(doc, "li", { class: `study-ticks__tick study-ticks__tick--${state}`, data: { state } }));
      }
      ticks.replaceChildren(...items);
      const index = open ? done + 1 : done;
      count.replaceChildren();
      if (total > 0 && index > 0) {
        const text = fill(t.header.item, { index, total });
        count.textContent = text;
        element.setAttribute("aria-label", `${t.header.progress}: ${text}`);
      }
      element.hidden = total === 0;
    },
  };
}

export interface Autosave {
  readonly element: HTMLElement;
  saving(): void;
  saved(seq: number): void;
  failed(): void;
  hide(): void;
}

export function createAutosave(doc: Document): Autosave {
  const label = el(doc, "span", { text: t.header.saved });
  const element = el(doc, "span", { class: "study-autosave", attrs: { role: "status" }, data: { part: "autosave", state: "saved", seq: "0" } }, studyIcon(doc, "i-check", "ic study-autosave__icon"), label);
  element.hidden = true;
  return {
    element,
    saving() {
      element.hidden = false;
      element.dataset.state = "saving";
      label.textContent = t.header.saving;
    },
    saved(seq) {
      element.hidden = false;
      element.dataset.state = "saved";
      element.dataset.seq = String(seq);
      label.textContent = t.header.saved;
    },
    failed() {
      element.hidden = false;
      element.dataset.state = "failed";
      label.textContent = t.header.notSaved;
    },
    hide() {
      element.hidden = true;
    },
  };
}

export interface DockHandlers {
  undo(): void;
  mLine(): void;
  skip(): void;
  next(): void;
  nextMyofibril(): void;
  branch(pressed: boolean): void;
}

export interface DockOptions {
  readonly branchEnd: boolean;
  readonly enabled: boolean;
}

export interface Dock {
  readonly element: HTMLElement;
  readonly buttons: Readonly<Record<"undo" | "mLine" | "skip" | "next" | "nextMyofibril" | "branch", HTMLButtonElement>>;
  render(view: StudyViewState, options: DockOptions): void;
  notice(text: string | null): void;
  saving(full: boolean): void;
  can(name: "undo" | "mLine" | "skip" | "next" | "nextMyofibril"): boolean;
}

export function createDock(doc: Document, handlers: DockHandlers): Dock {
  const counter = el(doc, "span", { class: "study-guide__count", data: { part: "click-count" } });
  const swatch = el(doc, "i", { class: "study-guide__swatch", attrs: { "aria-hidden": "true" } });
  const what = el(doc, "span", { class: "study-guide__what", data: { part: "instruction" } });
  const chipList = el(doc, "ol", { class: "lm-chips study-guide__chips", attrs: { "aria-label": t.dock.chips }, data: { part: "chips" } });
  const guide = el(doc, "div", { class: "study-guide", attrs: { role: "status", "aria-live": "polite", "aria-label": t.dock.instruction } }, counter, swatch, what, chipList);
  const buttons = {
    undo: actionButton(doc, "btn btn--ghost", "undo", t.dock.undo, "Backspace", "i-undo"),
    mLine: actionButton(doc, "btn btn--ghost", "m-line", t.dock.mLine, "M"),
    nextMyofibril: actionButton(doc, "btn btn--ghost", "next-myofibril", t.dock.nextMyofibril, "N"),
    branch: actionButton(doc, "btn btn--ghost study-dock__branch", "branch", t.dock.branch, null),
    skip: actionButton(doc, "btn btn--ghost", "skip", t.dock.skip, "S", "i-skip"),
    next: actionButton(doc, "btn btn--primary", "next", t.dock.next, "Enter"),
  };
  buttons.branch.setAttribute("aria-pressed", "false");
  const actions = el(doc, "div", { class: "study-dock__actions", data: { part: "study-actions" } }, buttons.undo, buttons.mLine, buttons.nextMyofibril, buttons.branch, buttons.skip, buttons.next);
  const hint = el(doc, "p", { class: "study-dock__hint", data: { part: "hint" } });
  const notice = el(doc, "p", { class: "study-dock__notice", attrs: { role: "alert" }, data: { part: "notice" } });
  const saving = el(doc, "p", { class: "study-dock__saving", attrs: { role: "status" }, data: { part: "saving" }, text: t.dock.saving });
  saving.hidden = true;
  notice.hidden = true;
  hint.hidden = true;
  const lines = el(doc, "div", { class: "study-dock__lines" }, hint, notice, saving);
  const element = el(doc, "div", { class: "study-dock", data: { part: "study-dock" } }, guide, actions, lines);
  element.hidden = true;

  const wire = (key: keyof typeof buttons, run: () => void): void => {
    buttons[key].addEventListener("click", () => {
      if (!isDisabled(buttons[key])) {
        run();
      }
    });
  };
  wire("undo", () => handlers.undo());
  wire("mLine", () => handlers.mLine());
  wire("skip", () => handlers.skip());
  wire("next", () => handlers.next());
  wire("nextMyofibril", () => handlers.nextMyofibril());
  wire("branch", () => handlers.branch(buttons.branch.getAttribute("aria-pressed") !== "true"));

  return {
    element,
    buttons,
    render(view, options) {
      const step = prompt(view);
      const type = view.controls.item_type;
      element.hidden = view.item_file === null;
      element.dataset.itemType = type ?? "";
      counter.replaceChildren();
      counter.hidden = step?.counter == null;
      if (step?.counter != null) {
        counter.textContent = fill(t.dock.click, { number: step.counter.number, total: step.counter.total });
      }
      swatch.className = step?.kind == null ? "study-guide__swatch" : `study-guide__swatch study-guide__swatch--${step.kind}`;
      swatch.hidden = step?.kind == null;
      what.textContent = step?.what ?? "";
      hint.textContent = step?.hint ?? "";
      hint.hidden = step?.hint == null;
      chipList.replaceChildren(
        ...chips(view).map((chip) => {
          const state = chip.state === "skipped" ? "todo lm-chip--off" : chip.state;
          const item = el(doc, "li", {
            class: `lm-chip${chip.letter === "C" || chip.letter === "D" ? " lm-chip--m" : ""} lm-chip--${state}`,
            text: chip.letter,
            attrs: { title: t.dock.landmarks[chip.letter], "aria-label": `${chip.letter}: ${t.dock.landmarks[chip.letter]}` },
            data: { landmark: chip.letter, state: chip.state },
          });
          if (chip.state === "now") {
            item.setAttribute("aria-current", "step");
          }
          return item;
        }),
      );
      chipList.hidden = type !== "shape";
      const controls = view.controls;
      const on = options.enabled;
      setDisabled(buttons.undo, !on || !controls.can_undo);
      setDisabled(buttons.mLine, !on || !controls.can_skip_m_line);
      setDisabled(buttons.next, !on || !controls.can_confirm);
      setDisabled(buttons.skip, !on || view.item_file === null);
      setDisabled(buttons.nextMyofibril, !on || !controls.can_next_myofibril);
      setDisabled(buttons.branch, !on);
      buttons.mLine.hidden = type !== "shape";
      buttons.nextMyofibril.hidden = type !== "pair";
      buttons.branch.hidden = !controls.shows_branch_end;
      buttons.branch.setAttribute("aria-pressed", String(options.branchEnd));
    },
    notice(text) {
      notice.textContent = text ?? "";
      notice.hidden = text === null;
    },
    saving(full) {
      saving.hidden = !full;
    },
    can(name) {
      return !buttons[name].hidden && !isDisabled(buttons[name]);
    },
  };
}
