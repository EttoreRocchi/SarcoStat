import { en } from "../../strings/en.ts";
import { button, el, setDisabled } from "../dom.ts";
import { icon } from "../icons.ts";

export interface StudyHandlers {
  home(): void;
}

export interface StudyView {
  readonly head: HTMLElement;
  readonly stage: HTMLElement;
  readonly dock: HTMLElement;
}

export function createStudyScreen(doc: Document, handlers: StudyHandlers): StudyView {
  const back = button(doc, "btn btn--ghost", el(doc, "span", { text: en.study.back }));
  back.dataset.action = "home";
  back.addEventListener("click", () => handlers.home());
  const head = el(
    doc,
    "header",
    { class: "study__head", data: { mode: "study" } },
    icon(doc, "mark", "mark"),
    el(doc, "div", { class: "study__title" }, el(doc, "h1", { class: "study__name", text: en.modes.study.title }), el(doc, "p", { class: "study__sub", text: en.study.subtitle })),
    el(doc, "div", { class: "study__actions" }, back),
  );
  const open = button(doc, "btn btn--primary btn--large", icon(doc, "i-folder", "btn__icon"), el(doc, "span", { text: en.study.open }));
  open.dataset.action = "open-kit";
  setDisabled(open, true);
  open.setAttribute("aria-describedby", "study-open-reason");
  const stage = el(
    doc,
    "main",
    { class: "study__stage", attrs: { "aria-labelledby": "study-heading" }, data: { mode: "study" } },
    el(
      doc,
      "section",
      { class: "card study__card" },
      icon(doc, "i-study", "study__icon"),
      el(doc, "h2", { class: "study__heading", text: en.study.heading, attrs: { id: "study-heading" } }),
      el(doc, "p", { class: "study__text", text: en.study.text }),
      open,
      el(doc, "p", { class: "study__reason", text: en.study.openDisabled, attrs: { id: "study-open-reason" } }),
    ),
  );
  const dock = el(doc, "div", { class: "study__dock", data: { mode: "study" } });
  return { head, stage, dock };
}
