import { createFilePicker, downloadText, looksLikeJson, OPEN_ACCEPT, readImageHeader } from "../io/files.ts";
import { decodeImage, type DecodedImage } from "../io/image/decode.ts";
import { sha256Hex } from "../io/sha256.ts";
import { Router, type Arrival, type HashHost, type RailRoute, type Route } from "../router.ts";
import { Session, type EngineLike } from "../state/session.ts";
import { decideSwitch, hasPendingClick, leaveModel, sourceMatches, statusItems, unloadGuardActive, type AppState, type ModeItem, type OpenedImage, type Store } from "../state/store.ts";
import type { ClientListener, EngineClient } from "../worker/client.ts";
import type { BuildInfo, FatalCode } from "../worker/protocol.ts";
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
import { createClickErrorDialog, createConfirmDialog, createScaleDialog } from "./shell/dialogs.ts";
import { createExportMenu, type ExportId } from "./shell/export_menu.ts";
import { createGuardDialog, createLeaveDialog } from "./shell/guard.ts";
import { installKeyboard } from "./shell/keyboard.ts";
import { createRail } from "./shell/rail.ts";
import { createSheet } from "./shell/sheet.ts";
import { keyInput, resolveKey, resolveKeyUp, type ShortcutAction } from "./shell/shortcuts.ts";
import type { StatusLine } from "./shell/statusline.ts";
import { createToast } from "./shell/toast.ts";
import { createTopbar } from "./shell/topbar.ts";

export interface App {
  readonly store: Store;
  readonly session: Session;
  attachEngine(engine: EngineLike): void;
}

export interface AppOptions {
  readonly build: BuildInfo | null;
  makeEngine(listener: ClientListener): EngineClient | null;
  showFatal(code: FatalCode, message: string): void;
}

const NO_ENGINES: AppOptions = { build: null, makeEngine: () => null, showFatal: () => undefined };

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

export function createApp(doc: Document, root: HTMLElement, store: Store, statusline: StatusLine, options: AppOptions = NO_ENGINES): App {
  const win = doc.defaultView as Window;
  const session = new Session(store);
  const shell = el(doc, "div", { class: "shell shell--home", data: { mode: "home" } });
  const background = (): HTMLElement[] => [shell];
  const keyboard = installKeyboard(
    doc,
    (event) => onKey(event),
    () => root.inert,
    (event) => onKeyUp(event),
  );
  const toast = createToast(doc);
  const sheet = createSheet(doc, keyboard, background);
  const guard = createGuardDialog(doc, keyboard, background);
  const leave = createLeaveDialog(doc, keyboard, background);
  const confirmDialog = createConfirmDialog(doc, keyboard, background);
  const scaleDialog = createScaleDialog(doc, keyboard, background);
  const clickDialog = createClickErrorDialog(doc, keyboard, background);
  const picker = createFilePicker(doc, root);
  const menu = createExportMenu(doc, keyboard, (id) => void onExport(id));
  const topbar = createTopbar(doc, menu, { open: () => void openFlow(), keys: () => sheet.toggle(), scale: () => void editScale() });
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
  const shape = createShapeMode(doc, {
    store,
    session,
    open: () => void openFlow(),
    editScale: () => void editScale(),
    setClickError: () => void setClickError(),
    estimateClickError: () => void session.estimateClickError(),
    remove: (item) => void removeItem(item),
  });
  const study = createStudyScreen(
    doc,
    { home: () => void router.go("home") },
    {
      get build() {
        return options.build;
      },
      keyboard,
      statusline,
      makeEngine: (listener) => options.makeEngine(listener),
      showFatal: (code, message) => options.showFatal(code, message),
    },
  );
  const placeholders = {
    straightness: createStraightnessMode(doc).element,
    register: createRegisterMode(doc).element,
    planner: createPlannerMode(doc).element,
  };
  root.replaceChildren(shell, sheet.element, guard.element, leave.element, confirmDialog.element, scaleDialog.element, clickDialog.element, toast.element);

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
    if (laidOut === "study") {
      study.leave();
    }
    laidOut = route;
    shell.dataset.mode = route;
    if (route === "study") {
      shell.className = "shell shell--study";
      study.dock.replaceChildren(statusline.element);
      shell.replaceChildren(study.head, study.stage, study.dock);
      study.enter();
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

  async function exportResults(what: "results_json" | "results_csv"): Promise<void> {
    const files = await session.exportResults(what);
    if (files === null) {
      return;
    }
    const [file, sidecar] = files;
    downloadText(doc, file.file_name, file.text, what === "results_csv" ? "text/csv" : "application/json");
    downloadText(doc, sidecar.file_name, sidecar.text);
    toast.show(fill(en.exportMenu.savedTwo, { file: file.file_name, sidecar: sidecar.file_name }));
  }

  async function onExport(id: ExportId): Promise<void> {
    if (id === "v1") {
      await exportV1();
    } else if (id === "results_json" || id === "results_csv") {
      await exportResults(id);
    }
  }

  async function askToLeave(nextName: string): Promise<boolean> {
    const model = leaveModel(store.get(), nextName);
    if (!model.ask) {
      return true;
    }
    const choice = await leave.ask(model, nextName);
    if (choice === "cancel") {
      return false;
    }
    return choice !== "export" || (await exportV1());
  }

  async function openAnnotations(file: File, bytes: Uint8Array): Promise<void> {
    let text: string;
    try {
      text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    } catch {
      toast.show(fill(en.open.notImage, { file: file.name }));
      return;
    }
    if (!(await askToLeave(file.name))) {
      return;
    }
    const image = store.get().source;
    let outcome = await session.loadV1(text, image, false);
    if (outcome.kind === "confirm") {
      const yes = await confirmDialog.ask({ title: en.dialogs.load.title, text: outcome.message, confirm: en.dialogs.load.confirm });
      if (!yes) {
        return;
      }
      outcome = await session.loadV1(text, image, true);
    }
    if (outcome.kind !== "loaded") {
      return;
    }
    await router.go(outcome.state.mode === "shape" ? "shape" : "home");
  }

  async function openPicture(file: File, bytes: Uint8Array): Promise<void> {
    const header = readImageHeader(bytes);
    if (header === null) {
      toast.show(fill(en.open.notImage, { file: file.name }));
      return;
    }
    let decoded: DecodedImage;
    try {
      decoded = await decodeImage(bytes, header.kind, header.width, header.height);
    } catch {
      toast.show(fill(en.workspace.decodeFailed, { file: file.name }));
      return;
    }
    const sha256 = await sha256Hex(bytes);
    const opened: OpenedImage = { file, fileName: file.name, kind: header.kind, bytes, sha256, width: header.width, height: header.height, decoded };
    if (sourceMatches(store.get(), sha256, header.width, header.height)) {
      session.attachPixels(opened);
      toast.show(fill(en.open.attached, { file: file.name }));
      if (store.get().route !== "shape") {
        await router.go("shape");
      }
      return;
    }
    if (!(await askToLeave(file.name))) {
      decoded.bitmap.close();
      return;
    }
    const done = await session.openImage(opened);
    if (!done) {
      decoded.bitmap.close();
      return;
    }
    if (store.get().route !== "shape") {
      await router.go("shape");
    }
  }

  async function openFlow(): Promise<void> {
    if (!session.ready) {
      toast.show(en.topbar.engineStarting);
      return;
    }
    closePopups();
    const file = await picker.pick(OPEN_ACCEPT);
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
    if (readImageHeader(bytes) === null && looksLikeJson(file.name, bytes)) {
      await openAnnotations(file, bytes);
      return;
    }
    await openPicture(file, bytes);
  }

  async function editScale(): Promise<void> {
    const settings = store.get().session?.settings;
    if (settings === undefined || !session.ready) {
      return;
    }
    const choice = await scaleDialog.ask({ pixelSize: settings.pixel_size, unit: settings.unit });
    if (choice !== null) {
      await session.setScale(choice.pixelSize, choice.unit);
    }
  }

  async function setClickError(): Promise<void> {
    const settings = store.get().session?.settings;
    if (settings === undefined || !session.ready) {
      return;
    }
    const choice = await clickDialog.ask(settings.unit);
    if (choice !== null) {
      await session.setClickError(choice.sigma);
    }
  }

  async function removeItem(item: ModeItem): Promise<void> {
    if (item.type !== "shape" || item.sarcomere_id === null) {
      return;
    }
    const n = item.sarcomere_id;
    const yes = await confirmDialog.ask({ title: fill(en.dialogs.remove.title, { n }), text: en.dialogs.remove.text, confirm: en.dialogs.remove.confirm, danger: true });
    if (yes) {
      await session.deleteItem(item.item_id);
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
        if (hasPendingClick(store.get())) {
          void session.undo();
        } else {
          toast.show(en.hud.noPendingClick);
        }
        return;
      case "discard":
        if (store.get().unfinished.shape !== null) {
          void session.discardPending();
        }
        return;
      case "confirm":
        void session.confirm();
        return;
      case "view":
        shape.view(action.action);
        return;
      case "item":
        shape.step(action.step);
        return;
      case "study":
        study.key(action.action);
        return;
    }
  }

  const keyContext = (state: AppState): { route: Route | null; imageOpen: boolean } => ({ route: state.route, imageOpen: state.session?.image != null && state.source !== null });

  function onKey(event: KeyboardEvent): void {
    const action = resolveKey(keyInput(event), keyContext(store.get()));
    if (action === null || router.busy) {
      return;
    }
    event.preventDefault();
    perform(action);
  }

  function onKeyUp(event: KeyboardEvent): void {
    const action = resolveKeyUp(keyInput(event), keyContext(store.get()));
    if (action !== null) {
      perform(action);
    }
  }

  win.addEventListener("blur", () => {
    shape.view("pan-end");
    if (store.get().route === "study") {
      study.key("pan-end");
    }
  });

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
