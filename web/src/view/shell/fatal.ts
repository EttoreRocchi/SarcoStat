import { en } from "../../strings/en.ts";
import type { FatalCode } from "../../worker/protocol.ts";

export interface FatalScreen {
  readonly element: HTMLElement;
  show(code: FatalCode, detail: string): void;
}

export function createFatalScreen(doc: Document, blocked: HTMLElement, reload: () => void): FatalScreen {
  const element = doc.createElement("div");
  element.className = "fatal";
  element.setAttribute("role", "alertdialog");
  element.setAttribute("aria-modal", "true");
  element.setAttribute("aria-labelledby", "fatal-title");
  element.setAttribute("aria-describedby", "fatal-body");
  element.hidden = true;
  const title = doc.createElement("h2");
  title.className = "fatal__title";
  title.id = "fatal-title";
  const body = doc.createElement("p");
  body.className = "fatal__body";
  body.id = "fatal-body";
  const detail = doc.createElement("p");
  detail.className = "fatal__body";
  detail.dataset.part = "detail";
  const action = doc.createElement("button");
  action.type = "button";
  action.className = "fatal__action";
  action.textContent = en.fatal.reload;
  action.addEventListener("click", () => reload());
  element.append(title, body, detail, action);
  return {
    element,
    show(code, message) {
      if (!element.hidden) {
        return;
      }
      element.dataset.code = code;
      title.textContent = en.fatal.title[code];
      body.textContent = en.fatal.body[code];
      detail.textContent = `${en.fatal.detail} ${code}: ${message}`;
      blocked.inert = true;
      element.hidden = false;
      action.focus();
    },
  };
}
