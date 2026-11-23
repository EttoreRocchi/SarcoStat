import { READINESS_KEYS, type ReadinessKey } from "../../state/study_report.ts";
import { en } from "../../strings/en.ts";
import { fill } from "../../strings/format.ts";
import { button, el, kbd, setDisabled } from "../dom.ts";
import { MIN_VIEWPORT, WARNINGS } from "./readiness.ts";
import { actionButton, studyIcon } from "./controls.ts";

const t = en.study;

export type CheckState = "pending" | "pass" | "fail" | "warn";

export interface StartScreen {
  readonly element: HTMLElement;
  readonly check: HTMLButtonElement;
  readonly open: HTMLButtonElement;
  setCheck(key: ReadinessKey, state: CheckState, params?: Record<string, string>): void;
  resetChecks(): void;
  setBusy(text: string | null): void;
  setOpenable(openable: boolean): void;
  setCheckEnabled(enabled: boolean): void;
  refuse(lines: readonly string[]): void;
}

export function createStartScreen(doc: Document, handlers: { check(): void; open(): void }): StartScreen {
  const rows = new Map<ReadinessKey, { item: HTMLElement; state: HTMLElement; text: HTMLElement }>();
  const list = el(doc, "ul", { class: "study-ready__list" });
  for (const key of READINESS_KEYS) {
    const state = el(doc, "span", { class: "study-ready__state", text: t.readiness.pending });
    const text = el(doc, "span", { class: "study-ready__text" });
    const item = el(doc, "li", { class: "study-ready__item", data: { check: key, state: "pending" } }, state, el(doc, "span", { class: "study-ready__label", text: t.readiness.items[key].label }), text);
    rows.set(key, { item, state, text });
    list.append(item);
  }
  const check = actionButton(doc, "btn btn--primary", "check", t.check, null, "i-check");
  const open = button(doc, "btn btn--primary btn--large", studyIcon(doc, "i-folder", "ic btn__icon"), el(doc, "span", { text: t.open }));
  open.dataset.action = "open-kit";
  const reason = el(doc, "p", { class: "study__reason", text: t.openDisabled, attrs: { id: "study-open-reason" } });
  const busy = el(doc, "p", { class: "study-start__busy", attrs: { role: "status" }, data: { part: "study-busy" } });
  busy.hidden = true;
  const refusalList = el(doc, "ul", { class: "study-refused__list" });
  const refused = el(
    doc,
    "section",
    { class: "study-refused", attrs: { role: "alert", "aria-labelledby": "study-refused-title" }, data: { part: "refusals" } },
    el(doc, "h3", { class: "study-refused__title", text: t.refused.title, attrs: { id: "study-refused-title" } }),
    refusalList,
    el(doc, "p", { class: "study-refused__again", text: t.refused.again }),
  );
  refused.hidden = true;
  const element = el(
    doc,
    "section",
    { class: "card study__card study-start", data: { part: "study-start" } },
    studyIcon(doc, "i-study", "study__icon"),
    el(doc, "h2", { class: "study__heading", text: t.heading, attrs: { id: "study-heading" } }),
    el(doc, "p", { class: "study__text", text: t.text }),
    el(doc, "p", { class: "study__text", text: t.steps }),
    el(doc, "section", { class: "study-ready", attrs: { "aria-labelledby": "study-ready-title" }, data: { part: "readiness" } }, el(doc, "h3", { class: "study-ready__title", text: t.readiness.title, attrs: { id: "study-ready-title" } }), list),
    el(doc, "div", { class: "study-start__actions" }, check, open),
    reason,
    busy,
    refused,
  );
  check.addEventListener("click", () => {
    if (check.getAttribute("aria-disabled") !== "true") {
      handlers.check();
    }
  });
  open.addEventListener("click", () => {
    if (open.getAttribute("aria-disabled") !== "true") {
      handlers.open();
    }
  });
  const setOpenable = (openable: boolean): void => {
    setDisabled(open, !openable);
    if (openable) {
      open.removeAttribute("aria-describedby");
      reason.hidden = true;
    } else {
      open.setAttribute("aria-describedby", "study-open-reason");
      reason.hidden = false;
    }
  };
  setOpenable(false);
  return {
    element,
    check,
    open,
    setCheck(key, state, params = {}) {
      const row = rows.get(key);
      if (row === undefined) {
        return;
      }
      const shown = state === "fail" && WARNINGS.includes(key) ? "warn" : state;
      row.item.dataset.state = shown;
      row.state.textContent = shown === "pending" ? t.readiness.pending : shown === "pass" ? t.readiness.pass : shown === "warn" ? t.readiness.warn : t.readiness.fail;
      const lines = t.readiness.items[key];
      const values = { width: String(MIN_VIEWPORT.width), height: String(MIN_VIEWPORT.height), ...params };
      row.text.textContent = shown === "pending" ? "" : fill(shown === "pass" ? lines.pass : lines.fail, values);
    },
    resetChecks() {
      for (const key of READINESS_KEYS) {
        this.setCheck(key, "pending");
      }
    },
    setBusy(text) {
      busy.textContent = text ?? "";
      busy.hidden = text === null;
    },
    setOpenable,
    setCheckEnabled(enabled) {
      setDisabled(check, !enabled);
    },
    refuse(lines) {
      refusalList.replaceChildren(...lines.map((line) => el(doc, "li", { class: "study-refused__line", text: line })));
      refused.hidden = lines.length === 0;
    },
  };
}

export interface IdentityIds {
  readonly study_id: string;
  readonly annotator_id: string;
  readonly session_id: string;
}

export interface IdentityScreen {
  readonly element: HTMLElement;
  readonly yes: HTMLButtonElement;
}

export function createIdentityScreen(
  doc: Document,
  ids: IdentityIds,
  version: string,
  resumed: { item: number; total: number } | null,
  handlers: { yes(): void; no(): void },
): IdentityScreen {
  const row = (label: string, value: string, part: string): HTMLElement[] => [el(doc, "dt", { class: "study-identity__label", text: label }), el(doc, "dd", { class: "study-identity__value t-num", text: value, data: { part } })];
  const yes = actionButton(doc, "btn btn--primary btn--large", "identity-yes", resumed === null ? t.identity.yes : t.identity.yesResume, null, "i-check");
  const no = actionButton(doc, "btn btn--ghost", "identity-no", t.identity.no, null);
  const declined = el(doc, "p", { class: "study-identity__declined", attrs: { role: "alert" }, text: t.identity.declined });
  declined.hidden = true;
  const element = el(
    doc,
    "section",
    { class: "card study__card study-identity", attrs: { "aria-labelledby": "study-identity-title" }, data: { part: "identity" } },
    el(doc, "h2", { class: "study__heading", text: t.identity.title, attrs: { id: "study-identity-title" } }),
    el(doc, "dl", { class: "study-identity__ids" }, ...row(t.identity.study, ids.study_id, "id-study"), ...row(t.identity.annotator, ids.annotator_id, "id-annotator"), ...row(t.identity.session, ids.session_id, "id-session"), ...row(t.identity.build, version, "id-build")),
    el(doc, "p", { class: "study__text study-identity__question", text: fill(t.identity.question, { annotator: ids.annotator_id, session: ids.session_id }) }),
    el(doc, "p", { class: "study__text", text: t.identity.notMine }),
    resumed === null ? null : el(doc, "p", { class: "study__text study-identity__resumed", text: fill(t.identity.resumed, resumed), data: { part: "resumed" } }),
    el(doc, "div", { class: "study-start__actions" }, yes, no),
    declined,
  );
  yes.addEventListener("click", () => {
    if (yes.getAttribute("aria-disabled") !== "true") {
      setDisabled(yes, true);
      setDisabled(no, true);
      handlers.yes();
    }
  });
  no.addEventListener("click", () => {
    if (no.getAttribute("aria-disabled") !== "true") {
      setDisabled(yes, true);
      setDisabled(no, true);
      declined.hidden = false;
      handlers.no();
    }
  });
  return { element, yes };
}

export function createPausedScreen(doc: Document, byHiddenTab: boolean, resume: () => void): HTMLElement {
  const resumeButton = actionButton(doc, "btn btn--primary btn--large", "resume", t.paused.resume, "P");
  resumeButton.addEventListener("click", () => resume());
  return el(
    doc,
    "section",
    { class: "card study__card study-paused", attrs: { "aria-labelledby": "study-paused-title" }, data: { part: "paused" } },
    studyIcon(doc, "i-pause", "study__icon"),
    el(doc, "h2", { class: "study__heading", text: t.paused.title, attrs: { id: "study-paused-title" } }),
    el(doc, "p", { class: "study__text", text: t.paused.text }),
    byHiddenTab ? el(doc, "p", { class: "study__text", text: t.paused.hidden, data: { part: "paused-hidden" } }) : null,
    el(doc, "div", { class: "study-start__actions" }, resumeButton),
  );
}

export function createFinishedScreen(doc: Document, fileName: string, handlers: { again(): void; report(): void }): HTMLElement {
  const again = actionButton(doc, "btn btn--primary btn--large", "download-again", t.finished.again, null, "i-download");
  const report = actionButton(doc, "btn btn--ghost", "download-report", t.finished.report, null, "i-download");
  again.addEventListener("click", () => handlers.again());
  report.addEventListener("click", () => handlers.report());
  return el(
    doc,
    "section",
    { class: "card study__card study-finished", attrs: { "aria-labelledby": "study-finished-title" }, data: { part: "finished" } },
    studyIcon(doc, "i-check", "study__icon"),
    el(doc, "h2", { class: "study__heading", text: t.finished.title, attrs: { id: "study-finished-title" } }),
    el(doc, "p", { class: "study__text", text: fill(t.finished.text, { file: fileName }), data: { part: "finished-file" } }),
    el(doc, "p", { class: "study__text", text: t.finished.saved }),
    el(doc, "div", { class: "study-start__actions" }, again, report),
  );
}

export function createLockedScreen(doc: Document, again: () => void): HTMLElement {
  const download = actionButton(doc, "btn btn--primary btn--large", "download-again", t.finished.again, null, "i-download");
  download.addEventListener("click", () => again());
  return el(
    doc,
    "section",
    { class: "card study__card study-locked", attrs: { "aria-labelledby": "study-locked-title" }, data: { part: "locked" } },
    studyIcon(doc, "i-lock", "study__icon"),
    el(doc, "h2", { class: "study__heading", text: t.finished.lockedTitle, attrs: { id: "study-locked-title" } }),
    el(doc, "p", { class: "study__text", text: t.finished.locked }),
    el(doc, "div", { class: "study-start__actions" }, download),
  );
}

export function createBanner(doc: Document, text: string): HTMLElement {
  return el(doc, "div", { class: "study-banner", attrs: { role: "alert" }, data: { part: "study-banner" } }, studyIcon(doc, "i-warn", "ic study-banner__icon"), el(doc, "p", { class: "study-banner__text", text }));
}

export function pauseButton(doc: Document): HTMLButtonElement {
  const out = button(doc, "btn btn--ghost study__pause", studyIcon(doc, "i-pause", "ic btn__icon"), el(doc, "span", { text: t.header.pause }), kbd(doc, "P"));
  out.dataset.action = "pause";
  out.setAttribute("aria-keyshortcuts", "P");
  out.addEventListener("pointerdown", (event) => event.preventDefault());
  return out;
}
