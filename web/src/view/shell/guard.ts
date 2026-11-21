import { en } from "../../strings/en.ts";
import { fill, plural } from "../../strings/format.ts";
import type { Chip, LeaveModel, SwitchDecision } from "../../state/store.ts";
import { button, el, kbd, richText } from "../dom.ts";
import { icon } from "../icons.ts";
import type { Keyboard } from "./keyboard.ts";
import { Modal } from "./modal.ts";

export type GuardChoice = "keep" | "discard" | "stay";
export type LeaveChoice = "export" | "leave" | "cancel";
export type GuardRequest = Extract<SwitchDecision, { kind: "guard" }>;

export function guardChoiceForKey(key: string): GuardChoice | null {
  if (key === "Enter") {
    return "keep";
  }
  if (key === "d" || key === "D") {
    return "discard";
  }
  if (key === "Escape") {
    return "stay";
  }
  return null;
}

export function leaveChoiceForKey(key: string, canExport: boolean): LeaveChoice | null {
  if (key === "Enter") {
    return canExport ? "export" : "leave";
  }
  if (key === "d" || key === "D") {
    return "leave";
  }
  if (key === "Escape") {
    return "cancel";
  }
  return null;
}

export function chipList(doc: Document, chips: readonly Chip[], showNow: boolean): HTMLOListElement {
  const list = el(doc, "ol", { class: "lm-chips", attrs: { "aria-label": en.hud.chipsLabel } });
  for (const chip of chips) {
    const state = chip.state === "now" && !showNow ? "todo" : chip.state;
    const name = en.landmarks[chip.landmark];
    const classes = ["lm-chip", chip.line === "m" ? "lm-chip--m" : "", state === "off" ? "lm-chip--todo lm-chip--off" : `lm-chip--${state}`].filter((part) => part !== "");
    const item = el(doc, "li", {
      class: classes.join(" "),
      text: chip.landmark,
      attrs: { title: name, "aria-label": fill(en.hud.chip[state], { landmark: chip.landmark, name }) },
      data: { landmark: chip.landmark, state },
    });
    if (state === "now") {
      item.setAttribute("aria-current", "step");
    }
    list.append(item);
  }
  return list;
}

interface ChoiceButtons<C extends string> {
  readonly order: readonly C[];
  readonly buttons: Readonly<Record<C, HTMLButtonElement>>;
}

interface ChoiceDialog<C extends string> {
  readonly layer: HTMLElement;
  ask(initial: C, keyChoice: (event: KeyboardEvent) => C | null): Promise<C>;
}

function choiceDialog<C extends string>(
  doc: Document,
  part: string,
  keyboard: Keyboard,
  background: () => readonly HTMLElement[],
  body: HTMLElement,
  choices: ChoiceButtons<C>,
  cancel: C,
): ChoiceDialog<C> {
  const dialog = el(
    doc,
    "div",
    { class: "dialog", attrs: { role: "alertdialog", "aria-modal": "true", "aria-labelledby": `${part}-title`, "aria-describedby": `${part}-text` } },
    el(doc, "div", { class: "dialog__icon" }, icon(doc, "i-warn")),
    body,
  );
  const layer = el(doc, "div", { class: "scrim", data: { part } }, dialog);
  layer.hidden = true;
  let settle: ((choice: C) => void) | null = null;
  let keyChoice: (event: KeyboardEvent) => C | null = () => null;
  const finish = (choice: C): void => {
    const done = settle;
    settle = null;
    modal.close(choice === cancel);
    done?.(choice);
  };
  const modal = new Modal({
    layer,
    dialog,
    keyboard,
    background,
    onKey: (event) => {
      if (event.ctrlKey || event.metaKey || event.altKey) {
        return false;
      }
      if (event.key === "Enter" && doc.activeElement instanceof HTMLButtonElement && dialog.contains(doc.activeElement) && !event.repeat) {
        return false;
      }
      const choice = keyChoice(event);
      if (choice === null || event.repeat) {
        return event.key === "Enter";
      }
      finish(choice);
      return true;
    },
  });
  for (const choice of choices.order) {
    choices.buttons[choice].addEventListener("click", () => finish(choice));
  }
  return {
    layer,
    ask(initial, keys) {
      keyChoice = keys;
      return new Promise<C>((resolve) => {
        settle = resolve;
        modal.open(choices.buttons[initial]);
      });
    },
  };
}

export interface GuardDialog {
  readonly element: HTMLElement;
  ask(request: GuardRequest, modeTitle: string): Promise<GuardChoice>;
}

export function createGuardDialog(doc: Document, keyboard: Keyboard, background: () => readonly HTMLElement[]): GuardDialog {
  const title = el(doc, "h2", { class: "dialog__title", attrs: { id: "guard-title" } });
  const text = el(doc, "p", { class: "dialog__text", attrs: { id: "guard-text" } });
  const chips = el(doc, "span", { class: "dialog__chips" });
  const stateText = el(doc, "span", { class: "u-muted" });
  const keep = button(doc, "btn btn--primary", el(doc, "span", { text: en.guard.keep }), kbd(doc, en.keys.enter));
  const discardLabel = el(doc, "span");
  const discard = button(doc, "btn btn--ghost btn--danger", discardLabel, kbd(doc, "D"));
  const stay = button(doc, "btn btn--ghost", el(doc, "span", { text: en.guard.stay }), kbd(doc, en.keys.esc));
  keep.dataset.choice = "keep";
  discard.dataset.choice = "discard";
  stay.dataset.choice = "stay";
  const foot = el(doc, "p", { class: "dialog__foot" }, icon(doc, "i-download", "ic ic--sm"));
  const footText = el(doc, "span");
  foot.append(footText);
  const body = el(
    doc,
    "div",
    { class: "dialog__body" },
    title,
    text,
    el(doc, "div", { class: "dialog__state" }, chips, stateText),
    el(doc, "div", { class: "dialog__actions" }, keep, discard, stay),
    foot,
  );
  const dialog = choiceDialog<GuardChoice>(doc, "guard", keyboard, background, body, { order: ["keep", "discard", "stay"], buttons: { keep, discard, stay } }, "stay");
  return {
    element: dialog.layer,
    ask(request, modeTitle) {
      const item = request.item;
      const t = en.guard.shape;
      title.textContent = fill(t.title, { n: item.number });
      const kept = request.completed === 0 ? [] : [" ", plural(request.completed, t.keptOne, t.keptOther, { count: request.completed })];
      text.replaceChildren(...richText(doc, t.text, { mode: modeTitle, n: item.number, placed: item.placed, of: item.of }, ["mode"]), ...kept);
      chips.replaceChildren(chipList(doc, item.chips, false));
      stateText.textContent = fill(en.guard.state, { placed: item.placed, of: item.of });
      discardLabel.textContent = fill(t.discard, { n: item.number });
      foot.hidden = request.notExported === 0;
      footText.textContent = plural(request.notExported, en.guard.footOne, en.guard.footOther);
      return dialog.ask("keep", (event) => guardChoiceForKey(event.key));
    },
  };
}

export interface LeaveDialog {
  readonly element: HTMLElement;
  ask(model: LeaveModel, nextName: string): Promise<LeaveChoice>;
}

export function createLeaveDialog(doc: Document, keyboard: Keyboard, background: () => readonly HTMLElement[]): LeaveDialog {
  const title = el(doc, "h2", { class: "dialog__title", attrs: { id: "leave-title" } });
  const text = el(doc, "p", { class: "dialog__text", attrs: { id: "leave-text" } });
  const exportFirst = button(doc, "btn btn--primary", el(doc, "span", { text: en.leave.exportFirst }), kbd(doc, en.keys.enter));
  const leaveKey = kbd(doc, "D");
  const leave = button(doc, "btn btn--ghost btn--danger", el(doc, "span", { text: en.leave.leave }), leaveKey);
  const cancel = button(doc, "btn btn--ghost", el(doc, "span", { text: en.leave.cancel }), kbd(doc, en.keys.esc));
  exportFirst.dataset.choice = "export";
  leave.dataset.choice = "leave";
  cancel.dataset.choice = "cancel";
  const body = el(doc, "div", { class: "dialog__body" }, title, text, el(doc, "div", { class: "dialog__actions" }, exportFirst, leave, cancel));
  const dialog = choiceDialog<LeaveChoice>(doc, "leave", keyboard, background, body, { order: ["export", "leave", "cancel"], buttons: { export: exportFirst, leave, cancel } }, "cancel");
  return {
    element: dialog.layer,
    ask(model, nextName) {
      title.textContent = fill(en.leave.title, { next: nextName });
      text.textContent = model.lines.join(" ");
      exportFirst.hidden = !model.canExport;
      leave.className = model.canExport ? "btn btn--ghost btn--danger" : "btn btn--primary";
      return dialog.ask(model.canExport ? "export" : "leave", (event) => leaveChoiceForKey(event.key, model.canExport));
    },
  };
}
