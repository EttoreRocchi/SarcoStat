import { en } from "../../strings/en.ts";
import { fill } from "../../strings/format.ts";
import { button, el, kbd } from "../dom.ts";
import { icon } from "../icons.ts";
import type { Keyboard } from "./keyboard.ts";
import { Modal } from "./modal.ts";

export interface ConfirmRequest {
  readonly title: string;
  readonly text: string;
  readonly confirm: string;
  readonly danger?: boolean;
}

export interface ConfirmDialog {
  readonly element: HTMLElement;
  ask(request: ConfirmRequest): Promise<boolean>;
}

let serial = 0;

function shell(doc: Document, part: string, role: "dialog" | "alertdialog", body: HTMLElement, iconId: "i-warn" | null): { layer: HTMLElement; dialog: HTMLElement } {
  const dialog = el(doc, "div", { class: "dialog", attrs: { role, "aria-modal": "true", "aria-labelledby": `${part}-title`, "aria-describedby": `${part}-text` } });
  if (iconId !== null) {
    dialog.append(el(doc, "div", { class: "dialog__icon" }, icon(doc, iconId)));
  }
  dialog.append(body);
  const layer = el(doc, "div", { class: "scrim", data: { part } }, dialog);
  layer.hidden = true;
  return { layer, dialog };
}

export function createConfirmDialog(doc: Document, keyboard: Keyboard, background: () => readonly HTMLElement[]): ConfirmDialog {
  serial += 1;
  const part = `confirm-${serial}`;
  const title = el(doc, "h2", { class: "dialog__title", attrs: { id: `${part}-title` } });
  const text = el(doc, "p", { class: "dialog__text", attrs: { id: `${part}-text` } });
  const confirmLabel = el(doc, "span");
  const confirm = button(doc, "btn btn--primary", confirmLabel, kbd(doc, en.keys.enter));
  const cancel = button(doc, "btn btn--ghost", el(doc, "span", { text: en.dialogs.cancel }), kbd(doc, en.keys.esc));
  confirm.dataset.choice = "confirm";
  cancel.dataset.choice = "cancel";
  const body = el(doc, "div", { class: "dialog__body" }, title, text, el(doc, "div", { class: "dialog__actions" }, confirm, cancel));
  const { layer, dialog } = shell(doc, part, "alertdialog", body, "i-warn");
  dialog.dataset.part = "confirm";
  let settle: ((value: boolean) => void) | null = null;
  const finish = (value: boolean): void => {
    const done = settle;
    settle = null;
    modal.close();
    done?.(value);
  };
  const modal = new Modal({
    layer,
    dialog,
    keyboard,
    background,
    onKey: (event) => {
      if (event.ctrlKey || event.metaKey || event.altKey || event.repeat) {
        return event.key === "Enter";
      }
      if (event.key === "Escape") {
        finish(false);
        return true;
      }
      if (event.key === "Enter") {
        if (doc.activeElement === cancel) {
          return false;
        }
        finish(true);
        return true;
      }
      return false;
    },
  });
  confirm.addEventListener("click", () => finish(true));
  cancel.addEventListener("click", () => finish(false));
  return {
    element: layer,
    ask(request) {
      title.textContent = request.title;
      text.textContent = request.text;
      confirmLabel.textContent = request.confirm;
      confirm.className = request.danger === true ? "btn btn--primary btn--danger" : "btn btn--primary";
      return new Promise<boolean>((resolve) => {
        settle = resolve;
        modal.open(confirm);
      });
    },
  };
}

interface FormParts {
  readonly layer: HTMLElement;
  readonly form: HTMLFormElement;
  readonly error: HTMLElement;
  open(focus: HTMLElement, submit: () => boolean): Promise<boolean>;
}

function formDialog(doc: Document, part: string, keyboard: Keyboard, background: () => readonly HTMLElement[], heading: string, intro: HTMLElement, fields: HTMLElement[], apply: string): FormParts {
  const title = el(doc, "h2", { class: "dialog__title", text: heading, attrs: { id: `${part}-title` } });
  intro.id = `${part}-text`;
  const error = el(doc, "p", { class: "dialog__error", attrs: { role: "alert" } });
  error.hidden = true;
  const ok = button(doc, "btn btn--primary", el(doc, "span", { text: apply }), kbd(doc, en.keys.enter));
  ok.type = "submit";
  ok.dataset.choice = "apply";
  const cancel = button(doc, "btn btn--ghost", el(doc, "span", { text: en.dialogs.cancel }), kbd(doc, en.keys.esc));
  cancel.dataset.choice = "cancel";
  const form = el(doc, "form", { class: "dialog__form" }, ...fields, error, el(doc, "div", { class: "dialog__actions" }, ok, cancel));
  form.noValidate = true;
  const body = el(doc, "div", { class: "dialog__body" }, title, intro, form);
  const { layer, dialog } = shell(doc, part, "dialog", body, null);
  dialog.dataset.part = part;
  let settle: ((value: boolean) => void) | null = null;
  let submit: () => boolean = () => true;
  const finish = (value: boolean): void => {
    const done = settle;
    settle = null;
    modal.close();
    done?.(value);
  };
  const modal = new Modal({
    layer,
    dialog,
    keyboard,
    background,
    onKey: (event) => {
      if (event.key === "Escape" && !event.repeat) {
        finish(false);
        return true;
      }
      return false;
    },
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (submit()) {
      finish(true);
    }
  });
  cancel.addEventListener("click", () => finish(false));
  return {
    layer,
    form,
    error,
    open(focus, onSubmit) {
      submit = onSubmit;
      error.hidden = true;
      error.textContent = "";
      return new Promise<boolean>((resolve) => {
        settle = resolve;
        modal.open(focus);
      });
    },
  };
}

function field(doc: Document, id: string, label: string, control: HTMLElement): HTMLElement {
  control.id = id;
  return el(doc, "div", { class: "field" }, el(doc, "label", { class: "field__label", text: label, attrs: { for: id } }), control);
}

export function parseNumber(text: string): number | null {
  const trimmed = text.trim().replace(",", ".");
  if (trimmed === "") {
    return null;
  }
  const value = Number(trimmed);
  return Number.isFinite(value) ? value : Number.NaN;
}

export const SCALE_UNITS = ["nm", "µm", "px"] as const;

export interface ScaleChoice {
  readonly pixelSize: number;
  readonly unit: string;
}

export interface ScaleDialog {
  readonly element: HTMLElement;
  ask(current: ScaleChoice): Promise<ScaleChoice | null>;
}

export function createScaleDialog(doc: Document, keyboard: Keyboard, background: () => readonly HTMLElement[]): ScaleDialog {
  const t = en.dialogs.scale;
  const size = el(doc, "input", { class: "field__input t-num", attrs: { type: "text", inputmode: "decimal", autocomplete: "off", spellcheck: "false" } });
  const unit = el(doc, "select", { class: "field__input" });
  const labels: Readonly<Record<(typeof SCALE_UNITS)[number], string>> = { nm: t.units.nm, "µm": t.units.um, px: t.units.px };
  for (const name of SCALE_UNITS) {
    unit.append(el(doc, "option", { text: labels[name], attrs: { value: name } }));
  }
  const parts = formDialog(doc, "scale-dialog", keyboard, background, t.title, el(doc, "p", { class: "dialog__text", text: t.text }), [field(doc, "scale-size", t.size, size), field(doc, "scale-unit", t.unit, unit)], t.apply);
  return {
    element: parts.layer,
    async ask(current) {
      size.value = String(current.pixelSize);
      unit.value = (SCALE_UNITS as readonly string[]).includes(current.unit) ? current.unit : "nm";
      let choice: ScaleChoice | null = null;
      const done = await parts.open(size, () => {
        const value = parseNumber(size.value);
        if (value === null || Number.isNaN(value)) {
          parts.error.textContent = t.invalid;
          parts.error.hidden = false;
          size.focus();
          return false;
        }
        choice = { pixelSize: value, unit: unit.value };
        return true;
      });
      return done ? choice : null;
    },
  };
}

export interface ClickErrorDialog {
  readonly element: HTMLElement;
  ask(unit: string): Promise<{ readonly sigma: number | null } | null>;
}

export function createClickErrorDialog(doc: Document, keyboard: Keyboard, background: () => readonly HTMLElement[]): ClickErrorDialog {
  const t = en.dialogs.clickError;
  const sigma = el(doc, "input", { class: "field__input t-num", attrs: { type: "text", inputmode: "decimal", autocomplete: "off", spellcheck: "false" } });
  const label = el(doc, "label", { class: "field__label", attrs: { for: "click-sigma" } });
  sigma.id = "click-sigma";
  const parts = formDialog(doc, "click-error-dialog", keyboard, background, t.title, el(doc, "p", { class: "dialog__text", text: t.text }), [el(doc, "div", { class: "field" }, label, sigma)], t.apply);
  return {
    element: parts.layer,
    async ask(unit) {
      label.textContent = fill(t.sigma, { unit });
      sigma.value = "";
      let choice: { sigma: number | null } | null = null;
      const done = await parts.open(sigma, () => {
        const value = parseNumber(sigma.value);
        if (value !== null && Number.isNaN(value)) {
          parts.error.textContent = t.invalid;
          parts.error.hidden = false;
          sigma.focus();
          return false;
        }
        choice = { sigma: value };
        return true;
      });
      return done ? choice : null;
    },
  };
}
