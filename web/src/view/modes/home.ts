import { isAbsent, keyOf, type RailRoute } from "../../router.ts";
import { en } from "../../strings/en.ts";
import { fill } from "../../strings/format.ts";
import type { AppState } from "../../state/store.ts";
import { button, el, kbd, setDisabled } from "../dom.ts";
import { icon } from "../icons.ts";
import { MODE_ICONS, panelId, tabId } from "../shell/rail.ts";

export const HOME_CARDS = ["shape", "straightness", "register", "planner"] as const;

export interface HomeHandlers {
  select(route: RailRoute): void;
  study(): void;
  open(): void;
}

export interface HomeView {
  readonly element: HTMLElement;
  render(state: AppState): void;
  pointStudy(): void;
}

function modeCard(doc: Document, route: (typeof HOME_CARDS)[number] | "study", absent: boolean): HTMLButtonElement {
  const strings = en.modes[route];
  const nameId = `card-${route}-name`;
  const descId = `card-${route}-desc`;
  const absentId = `card-${route}-absent`;
  const title = el(doc, "span", { class: "mode-card__title" }, el(doc, "span", { text: strings.title, attrs: { id: nameId } }));
  if (route !== "study") {
    title.append(kbd(doc, keyOf(route)));
  }
  const body = el(
    doc,
    "span",
    { class: "mode-card__body" },
    title,
    el(doc, "span", { class: "mode-card__desc", text: strings.card, attrs: { id: descId } }),
    el(doc, "span", { class: "mode-card__meta" }, ...strings.tags.map((tag) => el(doc, "span", { class: "tag", text: tag }))),
  );
  if (absent) {
    body.append(el(doc, "span", { class: "mode-card__absent", text: en.absent.card, attrs: { id: absentId } }));
  }
  const card = button(doc, route === "study" ? "mode-card mode-card--study" : "mode-card", el(doc, "span", { class: "mode-card__tile" }, icon(doc, MODE_ICONS[route], "mode-card__icon")), body);
  card.dataset.mode = route;
  card.setAttribute("aria-labelledby", nameId);
  card.setAttribute("aria-describedby", absent ? `${descId} ${absentId}` : descId);
  if (route !== "study") {
    card.setAttribute("aria-keyshortcuts", keyOf(route));
  }
  setDisabled(card, absent);
  return card;
}

export function createHome(doc: Document, absentFamilies: readonly string[], handlers: HomeHandlers): HomeView {
  const currentName = el(doc, "p", { class: "home__current-name" });
  const currentMeta = el(doc, "p", { class: "home__current-meta t-num" });
  const openLabel = el(doc, "span");
  const open = button(doc, "btn btn--secondary", icon(doc, "i-folder", "btn__icon"), openLabel, kbd(doc, "O"));
  open.dataset.action = "open";
  open.setAttribute("aria-keyshortcuts", "O");
  open.addEventListener("click", () => {
    if (open.getAttribute("aria-disabled") !== "true") {
      handlers.open();
    }
  });
  const current = el(
    doc,
    "section",
    { class: "card home__current", attrs: { "aria-labelledby": "home-current-title" } },
    el(doc, "h2", { class: "t-eyebrow", text: en.home.current, attrs: { id: "home-current-title" } }),
    currentName,
    currentMeta,
    el(doc, "div", { class: "home__current-actions" }, open),
  );
  const hero = el(
    doc,
    "section",
    { class: "home__hero" },
    el(
      doc,
      "div",
      { class: "home__brand" },
      icon(doc, "mark", "home__mark"),
      el(
        doc,
        "div",
        {},
        el(doc, "p", { class: "home__wordmark", attrs: { "aria-hidden": "true" } }, "Sarco", el(doc, "span", { text: "Stat" })),
        el(doc, "p", { class: "home__tagline", text: en.home.tagline }),
      ),
    ),
    current,
    el(doc, "p", { class: "home__privacy", data: { part: "privacy" } }, icon(doc, "i-lock"), el(doc, "span", { text: en.home.privacy })),
  );
  const grid = el(doc, "div", { class: "home__grid" });
  for (const route of HOME_CARDS) {
    const absent = isAbsent(route, absentFamilies);
    const card = modeCard(doc, route, absent);
    card.addEventListener("click", () => handlers.select(route));
    grid.append(card);
  }
  const studyCard = modeCard(doc, "study", false);
  studyCard.addEventListener("click", () => handlers.study());
  studyCard.addEventListener("blur", () => delete studyCard.dataset.arrival);
  const modes = el(
    doc,
    "section",
    { class: "home__modes", attrs: { "aria-labelledby": "home-measure" } },
    el(doc, "h2", { class: "t-eyebrow home__heading", text: en.home.measure, attrs: { id: "home-measure" } }),
    grid,
    el(doc, "h2", { class: "t-eyebrow home__heading", text: en.home.observer, attrs: { id: "home-observer" } }),
    studyCard,
    el(doc, "p", { class: "u-sr", text: en.home.arrival, attrs: { id: "home-arrival" } }),
  );
  const element = el(
    doc,
    "main",
    { class: "shell__main home", attrs: { id: panelId("home"), role: "tabpanel", "aria-labelledby": tabId("home") }, data: { mode: "home" } },
    hero,
    modes,
  );
  return {
    element,
    render(state) {
      const image = state.session?.image ?? null;
      currentName.textContent = image === null ? en.home.noImage : image.file_name;
      currentMeta.textContent = image === null ? en.home.noImageHint : fill(en.topbar.size, { width: image.width_px, height: image.height_px });
      openLabel.textContent = image === null ? en.home.openImage : en.home.openAnother;
      const ready = state.engine === "ready";
      setDisabled(open, !ready);
      open.title = ready ? `${openLabel.textContent} (O)` : en.topbar.engineStarting;
    },
    pointStudy() {
      studyCard.dataset.arrival = "study";
      studyCard.setAttribute("aria-describedby", `card-study-desc home-arrival`);
      studyCard.focus();
    },
  };
}
