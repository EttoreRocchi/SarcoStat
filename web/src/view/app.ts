import { createFilePicker, downloadText, IMAGE_ACCEPT, readImageHeader } from "../io/files.ts";
import { sha256Hex } from "../io/sha256.ts";
import { Router, type Arrival, type HashHost, type RailRoute, type Route } from "../router.ts";
import { Session, type EngineLike } from "../state/session.ts";
import { decideSwitch, leaveModel, statusItems, unloadGuardActive, type AppState, type Store } from "../state/store.ts";
import { en } from "../strings/en.ts";
import { fill } from "../strings/format.ts";
import { ABSENT_FAMILIES } from "../worker/protocol.ts";
import { el } from "./dom.ts";
import { createHome } from "./modes/home.ts";
import { createPlannerMode } from "./modes/planner.ts";
import { createRegisterMode } from "./modes/register.ts";
import { createShapeMode } from "./modes/shape.ts";
import { createStraightnessMode } from "./modes/straightness.ts";
import { createStudyScreen } from "./modes/study.ts";
import { createExportMenu, type ExportId } from "./shell/export_menu.ts";
import { createGuardDialog, createLeaveDialog } from "./shell/guard.ts";
import { installKeyboard } from "./shell/keyboard.ts";
import { createRail } from "./shell/rail.ts";
import { createSheet } from "./shell/sheet.ts";
import { keyInput, resolveKey, type ShortcutAction } from "./shell/shortcuts.ts";
import type { StatusLine } from "./shell/statusline.ts";
import { createToast } from "./shell/toast.ts";
import { createTopbar } from "./shell/topbar.ts";

export interface App {
  readonly store: Store;
  readonly session: Session;
  attachEngine(engine: EngineLike): void;
}

export function browserHashHost(win: Window): HashHost {
  return {
    readHash: () => win.location.hash,
    pushHash: (hash) => {
      win.location.hash = hash;
    },
    replaceHash: (hash) => win.history.replaceState(win.history.state, "", hash),
    onHashChange: (listener) => win.addEventListener("hashchange", listener),
  };
}

export function createApp(doc: Document, root: HTMLElement, store: Store, statusline: StatusLine): App {
  const win = doc.defaultView as Window;
  const session = new Session(store);
  const shell = el(doc, "div", { class: "shell shell--home", data: { mode: "home" } });
  const background = (): HTMLElement[] => [shell];
  const keyboard = installKeyboard(doc, (event) => onKey(event), () => root.inert);
  const toast = createToast(doc);
  const sheet = createSheet(doc, keyboard, background);
  const guard = createGuardDialog(doc, keyboard, background);
  const leave = createLeaveDialog(doc, keyboard, background);
  const picker = createFilePicker(doc, root);
  const menu = createExportMenu(doc, keyboard, (id) => void onExport(id));
  const topbar = createTopbar(doc, menu, { open: () => void openFlow(), keys: () => sheet.toggle() });
  const rail = createRail(doc, ABSENT_FAMILIES, {
    select: (route) => void router.go(route),
    study: () => void router.go("study"),
    keys: () => sheet.toggle(),
  });
  const home = createHome(doc, ABSENT_FAMILIES, {
    select: (route) => void router.go(route),
    study: () => void router.go("study", { fromStudyCard: true }),
    open: () => void openFlow(),
  });
  const shape = createShapeMode(doc, { store, session, open: () => void openFlow() });
  const study = createStudyScreen(doc, { home: () => void router.go("home") });
  const placeholders = {
    straightness: createStraightnessMode(doc).element,
    register: createRegisterMode(doc).element,
    planner: createPlannerMode(doc).element,
  };
  root.replaceChildren(shell, sheet.element, guard.element, leave.element, toast.element);

  let laidOut: Route | null = null;
  let lastNotice = store.get().noticeSeq;
  let unloadArmed = false;
  const onBeforeUnload = (event: BeforeUnloadEvent): void => {
    event.preventDefault();
    event.returnValue = "";
  };

  const layout = (route: Route): void => {
    if (route === laidOut) {
      return;
    }
    laidOut = route;
    shell.dataset.mode = route;
    if (route === "study") {
      shell.className = "shell shell--study";
      study.dock.replaceChildren(statusline.element);
      shell.replaceChildren(study.head, study.stage, study.dock);
      return;
    }
    if (route === "shape") {
      shell.className = "shell";
      shell.replaceChildren(rail.element, topbar.element, shape.stage, shape.panel, statusline.element);
      return;
    }
    shell.className = "shell shell--home";
    const main = route === "home" ? home.element : placeholders[route];
    shell.replaceChildren(rail.element, topbar.element, main, statusline.element);
  };

  const render = (state: AppState): void => {
    const route = state.route;
    if (route === null) {
      return;
    }
    layout(route);
    rail.setActive(route);
    if (route !== "study") {
      topbar.render(state, route);
    }
    home.render(state);
    shape.render(state);
    statusline.setContext(statusItems(state));
    const armed = unloadGuardActive(state);
    if (armed !== unloadArmed) {
      unloadArmed = armed;
      if (armed) {
        win.addEventListener("beforeunload", onBeforeUnload);
      } else {
        win.removeEventListener("beforeunload", onBeforeUnload);
      }
    }
    if (state.noticeSeq !== lastNotice) {
      lastNotice = state.noticeSeq;
      if (state.notice !== null) {
        toast.show(state.notice);
      }
    }
  };
  store.subscribe((state) => render(state));

  const closePopups = (): void => {
    menu.close();
    sheet.close();
  };

  const router = new Router(browserHashHost(win), {
    absentFamilies: ABSENT_FAMILIES,
    async beforeLeave(from, to) {
      closePopups();
      const decision = decideSwitch(store.get(), from, to);
      if (decision.kind === "go") {
        return true;
      }
      const choice = await guard.ask(decision, en.modes[to].title);
      if (choice === "stay") {
        return false;
      }
      if (choice === "discard") {
        await session.discardPending();
      }
      return true;
    },
    show(route: Route, arrival: Arrival) {
      store.setRoute(route);
      if (arrival.reason === "absent" && arrival.from !== null) {
        toast.show(fill(en.absent.notice, { mode: en.modes[arrival.from].title }));
      }
      if (arrival.reason === "study") {
        home.pointStudy();
      }
    },
    absent(route) {
      toast.show(fill(en.absent.notice, { mode: en.modes[route].title }));
    },
  });

  async function exportV1(): Promise<boolean> {
    const file = await session.exportV1();
    if (file === null) {
      return false;
    }
    downloadText(doc, file.file_name, file.text);
    toast.show(fill(en.exportMenu.saved, { file: file.file_name }));
    return true;
  }

  async function onExport(id: ExportId): Promise<void> {
    if (id === "v1") {
      await exportV1();
    }
  }

  async function openFlow(): Promise<void> {
    if (!session.ready) {
      toast.show(en.topbar.engineStarting);
      return;
    }
    closePopups();
    const file = await picker.pick(IMAGE_ACCEPT);
    if (file === null) {
      return;
    }
    let bytes: Uint8Array;
    try {
      bytes = new Uint8Array(await file.arrayBuffer());
    } catch {
      toast.show(fill(en.open.readFailed, { file: file.name }));
      return;
    }
    const header = readImageHeader(bytes);
    if (header === null) {
      toast.show(fill(en.open.notImage, { file: file.name }));
      return;
    }
    const model = leaveModel(store.get(), file.name);
    if (model.ask) {
      const choice = await leave.ask(model, file.name);
      if (choice === "cancel") {
        return;
      }
      if (choice === "export" && !(await exportV1())) {
        return;
      }
    }
    const sha256 = await sha256Hex(bytes);
    const opened = await session.openImage({ file, fileName: file.name, kind: header.kind, bytes, sha256, width: header.width, height: header.height });
    if (opened && store.get().route !== "shape") {
      await router.go("shape");
    }
  }

  function perform(action: ShortcutAction): void {
    switch (action.kind) {
      case "mode":
        void router.go(action.route as RailRoute);
        return;
      case "open":
        void openFlow();
        return;
      case "export":
        menu.open();
        return;
      case "sheet":
        sheet.toggle();
        return;
      case "undo":
        void session.undo();
        return;
      case "discard":
        if (store.get().unfinished.shape !== null) {
          void session.discardPending();
        }
        return;
      case "confirm":
        void session.confirm();
        return;
    }
  }

  function onKey(event: KeyboardEvent): void {
    const state = store.get();
    const action = resolveKey(keyInput(event), { route: state.route, imageOpen: state.session?.image != null });
    if (action === null || router.busy) {
      return;
    }
    event.preventDefault();
    perform(action);
  }

  router.start();
  render(store.get());

  return {
    store,
    session,
    attachEngine(engine) {
      session.attach(engine);
    },
  };
}
