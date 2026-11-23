import { en } from "../../strings/en.ts";
import { button, el, kbd } from "../dom.ts";
import type { Keyboard } from "../shell/keyboard.ts";
import { Modal } from "../shell/modal.ts";
import { studyIcon } from "./controls.ts";

const t = en.study.skip;

export type SkipItemType = "shape" | "chain" | "pair";

export interface SkipDialog {
  readonly element: HTMLElement;
  ask(type: SkipItemType): Promise<string | null>;
  close(): void;
  readonly isOpen: boolean;
}

export function skipReason(choice: string | null, other: string): string | null {
  if (choice === null) {
    return null;
  }
  if (choice !== "other") {
    return choice;
  }
  const text = other.split(/\s+/).join(" ").trim();
  return text === "" ? null : `${t.other}: ${text}`;
}

export function createSkipDialog(doc: Document, keyboard: Keyboard, background: () => readonly HTMLElement[]): SkipDialog {
  const title = el(doc, "h2", { class: "dialog__title", text: t.title, attrs: { id: "study-skip-title" } });
  const text = el(doc, "p", { class: "dialog__text", text: t.text });
  const legend = el(doc, "legend", { class: "u-sr", text: t.legend });
  const reasons = el(doc, "div", { class: "study-reasons__list" });
  const otherText = el(doc, "input", { class: "study-reasons__other", attrs: { type: "text", "aria-label": t.otherLabel, placeholder: t.otherPlaceholder, maxlength: "200" }, data: { part: "skip-other" } });
  const fieldset = el(doc, "fieldset", { class: "study-reasons" }, legend, reasons);
  const hint = el(doc, "p", { class: "dialog__state study-reasons__hint", text: t.mLineHint });
  const error = el(doc, "p", { class: "study-reasons__error", attrs: { role: "alert" }, data: { part: "skip-error" } });
  error.hidden = true;
  const confirm = button(doc, "btn btn--primary", el(doc, "span", { text: t.confirm }), kbd(doc, en.keys.enter));
  confirm.dataset.action = "skip-confirm";
  const cancel = button(doc, "btn btn--ghost", el(doc, "span", { text: t.cancel }), kbd(doc, en.keys.esc));
  cancel.dataset.action = "skip-cancel";
  const dialog = el(
    doc,
    "div",
    { class: "dialog study-skip", attrs: { role: "dialog", "aria-modal": "true", "aria-labelledby": "study-skip-title" }, data: { part: "skip-dialog" } },
    studyIcon(doc, "i-skip", "ic dialog__icon"),
    el(doc, "div", { class: "dialog__body" }, title, text, fieldset, hint, error, el(doc, "div", { class: "dialog__actions" }, confirm, cancel)),
  );
  const element = el(doc, "div", { class: "scrim", data: { part: "skip-scrim" } }, dialog);
  element.hidden = true;

  let settle: ((reason: string | null) => void) | null = null;
  const finish = (reason: string | null): void => {
    const done = settle;
    settle = null;
    modal.close();
    done?.(reason);
  };
  const chosen = (): string | null => {
    const checked = reasons.querySelector<HTMLInputElement>("input[type='radio']:checked");
    return checked === null ? null : checked.value;
  };
  const submit = (): void => {
    const reason = skipReason(chosen(), otherText.value);
    if (reason === null) {
      error.textContent = t.needReason;
      error.hidden = false;
      return;
    }
    finish(reason);
  };
  const modal = new Modal({
    layer: element,
    dialog,
    keyboard,
    background,
    onKey: (event) => {
      if (event.key === "Escape") {
        finish(null);
        return true;
      }
      if (event.key === "Enter" && !event.isComposing) {
        submit();
        return true;
      }
      return false;
    },
  });
  otherText.addEventListener("input", () => {
    const other = reasons.querySelector<HTMLInputElement>("input[type='radio'][value='other']");
    if (other !== null) {
      other.checked = true;
    }
    error.hidden = true;
  });
  confirm.addEventListener("click", () => submit());
  cancel.addEventListener("click", () => finish(null));

  const radio = (value: string, label: string, extra: HTMLElement | null): HTMLLabelElement => {
    const input = el(doc, "input", { attrs: { type: "radio", name: "study-skip-reason", value }, data: { part: "skip-reason" } });
    input.addEventListener("change", () => {
      error.hidden = true;
      if (value === "other") {
        otherText.focus();
      }
    });
    return el(doc, "label", { class: "study-reasons__option" }, input, el(doc, "span", { text: label }), extra);
  };

  return {
    element,
    get isOpen() {
      return modal.isOpen;
    },
    close() {
      if (modal.isOpen) {
        finish(null);
      }
    },
    ask(type) {
      if (modal.isOpen) {
        return Promise.resolve(null);
      }
      otherText.value = "";
      error.hidden = true;
      error.textContent = "";
      reasons.replaceChildren(...t.reasons[type].map((label) => radio(label, label, null)), radio("other", t.other, otherText));
      hint.hidden = type !== "shape";
      return new Promise((resolve) => {
        settle = resolve;
        modal.open(reasons.querySelector("input"));
      });
    },
  };
}
