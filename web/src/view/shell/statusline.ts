import { en } from "../../strings/en.ts";
import type { BootStage, Identity } from "../../worker/protocol.ts";
import { icon } from "../icons.ts";

export const SHORT_HASH = 12;

export interface StatusLine {
  readonly element: HTMLElement;
  showBoot(stage: BootStage): void;
  showIdentity(identity: Identity): void;
  showStopped(): void;
  setContext(items: readonly string[]): void;
}

function item(doc: Document, name: string, ...children: (Node | string)[]): HTMLElement {
  const element = doc.createElement("span");
  element.className = "statusline__item";
  element.dataset.item = name;
  element.append(...children);
  return element;
}

function hashButton(doc: Document, label: string, full: string): HTMLButtonElement {
  const button = doc.createElement("button");
  button.type = "button";
  button.className = "statusline__hash";
  button.dataset.full = full;
  const show = (expanded: boolean): void => {
    button.textContent = expanded ? full : full.slice(0, SHORT_HASH);
    button.setAttribute("aria-pressed", String(expanded));
    button.setAttribute("aria-label", `${label} ${expanded ? full : full.slice(0, SHORT_HASH)}. ${expanded ? en.status.showShort : en.status.showFull}`);
  };
  show(false);
  button.addEventListener("click", () => show(button.getAttribute("aria-pressed") !== "true"));
  return button;
}

export function createStatusLine(doc: Document): StatusLine {
  const element = doc.createElement("footer");
  element.className = "statusline";
  element.setAttribute("aria-label", en.status.label);
  element.dataset.state = "booting";
  const context = doc.createElement("span");
  context.className = "statusline__context";
  const grow = doc.createElement("span");
  grow.className = "statusline__grow";
  const local = item(doc, "local", icon(doc, "i-lock"), en.status.local);
  local.classList.add("statusline__local");
  const build = doc.createElement("span");
  build.className = "statusline__build";
  const progress = item(doc, "progress", en.status.reading);
  progress.setAttribute("role", "status");
  build.append(progress);
  element.append(context, grow, local, build);
  return {
    element,
    showBoot(stage) {
      if (element.dataset.state === "booting") {
        progress.textContent = en.status.boot[stage];
      }
    },
    showIdentity(identity) {
      element.dataset.state = "ready";
      build.replaceChildren(
        item(doc, "version", `${en.status.version} ${identity.sarcostat_version}`),
        item(doc, "wheel", `${en.status.wheel} `, hashButton(doc, en.status.wheel, identity.wheel_sha256)),
        item(doc, "source", `${en.status.source} `, hashButton(doc, en.status.source, identity.source_digest)),
        item(doc, "pyodide", `${en.status.pyodide} ${identity.pyodide_version}`),
      );
    },
    showStopped() {
      element.dataset.state = "stopped";
      progress.textContent = en.status.stopped;
      if (!progress.isConnected) {
        build.replaceChildren(progress);
      }
    },
    setContext(items) {
      context.replaceChildren(...items.map((text, i) => item(doc, `context-${i}`, text)));
    },
  };
}
