import { el } from "../dom.ts";

export const TOAST_MS = 6_000;

export interface Toast {
  readonly element: HTMLElement;
  show(text: string): void;
  clear(): void;
}

export function createToast(doc: Document): Toast {
  const message = el(doc, "div", { class: "toast" });
  message.hidden = true;
  const element = el(doc, "div", { class: "toast-region", attrs: { role: "status", "aria-live": "polite" }, data: { part: "toast" } }, message);
  let timer: ReturnType<typeof setTimeout> | null = null;
  const clear = (): void => {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
    message.hidden = true;
    message.textContent = "";
  };
  return {
    element,
    show(text) {
      clear();
      message.textContent = text;
      message.hidden = false;
      timer = setTimeout(clear, TOAST_MS);
    },
    clear,
  };
}
