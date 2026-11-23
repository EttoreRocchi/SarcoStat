import { copyOf, createFolderPicker, downloadBytes, KitFolder, kitEntries, KitReadError } from "../../io/kit.ts";
import { sha256Hex } from "../../io/sha256.ts";
import { acquireSessionLock, type HeldLock } from "../../state/locks.ts";
import { reportFileName, reportText, sessionReport, type Readiness } from "../../state/study_report.ts";
import {
  decideResume,
  nowTime,
  SessionSaver,
  sessionStorageMarker,
  StudyRunner,
  type BuildIdentity,
  type StudyInput,
  type StudyResult,
  type StudyViewState,
} from "../../state/study_session.ts";
import { StudyStore, type SessionKey, type SessionRecord } from "../../state/study_store_v1.ts";
import { en } from "../../strings/en.ts";
import { fill } from "../../strings/format.ts";
import type { ClientListener, EngineClient } from "../../worker/client.ts";
import type { BuildInfo, CommandResult, FatalCode, Identity } from "../../worker/protocol.ts";
import { button, el, setDisabled } from "../dom.ts";
import { icon } from "../icons.ts";
import type { Keyboard } from "../shell/keyboard.ts";
import type { StudyKeyAction } from "../shell/shortcuts.ts";
import type { StatusLine } from "../shell/statusline.ts";
import { createAutosave, createDock, createProgress } from "../study/controls.ts";
import { complete, fetchBuildIdentity, hasWebAssembly, hasWebLocks, passed, PROBED_MODULES, requestPersistence, sameBuild, viewportLargeEnough, type ServedBuild } from "../study/readiness.ts";
import { createBanner, createFinishedScreen, createIdentityScreen, createLockedScreen, createPausedScreen, createStartScreen, pauseButton, type StartScreen } from "../study/screens.ts";
import { createSkipDialog } from "../study/skip_dialog.ts";
import { createStudyStage } from "../study/stage.ts";
import { noticeText, skippedItems } from "../study/words.ts";

export interface StudyEnvironment {
  readonly build: BuildInfo | null;
  readonly keyboard: Keyboard;
  readonly statusline: StatusLine;
  makeEngine(listener: ClientListener): EngineClient | null;
  showFatal(code: FatalCode, message: string): void;
}

export interface StudyHandlers {
  home(): void;
}

export interface StudyView {
  readonly head: HTMLElement;
  readonly stage: HTMLElement;
  readonly dock: HTMLElement;
  enter(): void;
  leave(): void;
  key(action: StudyKeyAction): void;
}

type Phase = "start" | "checking" | "ready" | "opening" | "identity" | "session" | "finished" | "locked" | "declined";

type OpenedKit = CommandResult<"study.open_kit">;

const t = en.study;
const DECODE_OPTIONS: ImageBitmapOptions = { colorSpaceConversion: "none", premultiplyAlpha: "none" };

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function ownBytes(bytes: Uint8Array): Uint8Array<ArrayBuffer> {
  return bytes.buffer instanceof ArrayBuffer ? new Uint8Array(bytes.buffer, bytes.byteOffset, bytes.byteLength) : Uint8Array.from(bytes);
}

function sessionStorageOf(win: Window): Storage | null {
  try {
    return win.sessionStorage;
  } catch {
    return null;
  }
}

export function createStudyScreen(doc: Document, handlers: StudyHandlers, environment: StudyEnvironment): StudyView {
  const win = doc.defaultView as Window;
  const nav = win.navigator;
  const marker = sessionStorageMarker(sessionStorageOf(win));

  const progress = createProgress(doc);
  const autosave = createAutosave(doc);
  const pause = pauseButton(doc);
  const pauseLabel = pause.querySelector("span");
  pause.hidden = true;
  const back = button(doc, "btn btn--ghost", el(doc, "span", { text: t.back }));
  back.dataset.action = "home";
  back.addEventListener("click", () => handlers.home());
  const head = el(
    doc,
    "header",
    { class: "study__head", data: { mode: "study" } },
    icon(doc, "mark", "mark"),
    el(doc, "div", { class: "study__title" }, el(doc, "h1", { class: "study__name", text: en.modes.study.title }), el(doc, "p", { class: "study__sub", text: t.subtitle })),
    progress.element,
    el(doc, "div", { class: "study__actions" }, autosave.element, pause, back),
  );
  const stage = el(doc, "main", { class: "study__stage", attrs: { "aria-labelledby": "study-heading" }, data: { mode: "study", part: "study-main" } });
  const dock = el(doc, "div", { class: "study__dock", data: { mode: "study" } });
  const picker = createFolderPicker(doc, head);

  let phase: Phase = "start";
  let generation = 0;
  let engine: EngineClient | null = null;
  let store: StudyStore | null = null;
  let identity: BuildIdentity | null = null;
  let served: ServedBuild | null = null;
  let engineIdentity: Identity | null = null;
  let readiness: Partial<Readiness> = {};
  let persisted = false;
  let lock: HeldLock | null = null;
  let runner: StudyRunner | null = null;
  let saver: SessionSaver | null = null;
  let kitFiles: string[] = [];
  let verified = new Map<string, Uint8Array>();
  let bitmaps = new Map<string, Promise<ImageBitmap>>();
  let view: StudyViewState | null = null;
  let shownKey: string | null = null;
  let branchEnd = false;
  let skipped: boolean[] = [];
  let hiddenPause = false;
  let pauseQueued = false;
  let stopped = false;
  let banner: HTMLElement | null = null;
  let fatalShown = false;
  let current: HTMLElement | null = null;
  let start: StartScreen = createStartScreen(doc, { check: () => void check(), open: () => void openKit() });

  const studyStage = createStudyStage(doc, {
    click: (point, time) => submit({ kind: "click", x: point.x, y: point.y, t: time }),
    outside: () => itemDock.notice(t.dock.outside),
  });
  const itemDock = createDock(doc, {
    undo: () => act({ kind: "undo", t: nowTime() }),
    mLine: () => act({ kind: "m-line", t: nowTime() }),
    skip: () => void askSkip(),
    next: () => act({ kind: "confirm", branchEnd, t: nowTime() }),
    nextMyofibril: () => act({ kind: "next-myofibril", t: nowTime() }),
    branch: (pressed) => {
      branchEnd = pressed;
      renderDock();
    },
  });
  const skipDialog = createSkipDialog(doc, environment.keyboard, () => [head, dock, ...[...stage.children].filter((child): child is HTMLElement => child instanceof HTMLElement && child !== skipDialog.element)]);

  const showScreen = (node: HTMLElement): void => {
    current = node;
    stage.classList.toggle("study__stage--item", node === studyStage.element);
    stage.replaceChildren(...(banner === null ? [] : [banner]), node, skipDialog.element);
  };

  const setChrome = (): void => {
    const inSession = phase === "identity" || phase === "session";
    back.hidden = inSession;
    pause.hidden = phase !== "session";
    setDisabled(pause, stopped);
    itemDock.element.hidden = phase !== "session" || view === null || view.item_file === null || view.paused;
  };

  const renderDock = (): void => {
    if (view !== null) {
      itemDock.render(view, { branchEnd, enabled: !stopped });
    }
    setChrome();
  };

  const closeEngine = (): void => {
    engine?.close();
    engine = null;
  };

  const onFatal = (owner: number, code: FatalCode, message: string): void => {
    if (owner !== generation) {
      return;
    }
    if (phase === "identity" || phase === "session") {
      fatalShown = true;
      marker.set();
      runner?.stop();
      studyStage.setEnabled(false);
      environment.showFatal(code, `${t.fatal} (${message})`);
      return;
    }
    engine = null;
    if (phase === "opening") {
      start.refuse([fill(t.refused.start, { detail: message })]);
      start.setOpenable(false);
    } else if (phase === "ready") {
      phase = "start";
      start.setCheck("identity", "fail");
      start.setOpenable(false);
    }
  };

  const startEngine = (): Promise<boolean> => {
    closeEngine();
    const owner = generation;
    return new Promise((resolve) => {
      const client = environment.makeEngine({
        boot: () => {},
        ready: (id) => {
          if (owner === generation) {
            engineIdentity = id;
            environment.statusline.showIdentity(id);
          }
          resolve(true);
        },
        fatal: (code, message) => {
          resolve(false);
          onFatal(owner, code, message);
        },
      });
      if (client === null) {
        resolve(false);
        return;
      }
      engine = client;
      client.start();
    });
  };

  async function check(): Promise<void> {
    if (phase !== "start" && phase !== "ready") {
      return;
    }
    generation += 1;
    const owner = generation;
    phase = "checking";
    const persistence = requestPersistence(nav);
    start.resetChecks();
    start.refuse([]);
    start.setOpenable(false);
    start.setCheckEnabled(false);
    start.setBusy(t.checking);
    lock?.release();
    lock = null;
    const booted = startEngine();
    const results: { -readonly [K in keyof Readiness]?: boolean } = {};
    results.webassembly = hasWebAssembly(win as unknown as { WebAssembly?: typeof WebAssembly });
    start.setCheck("webassembly", results.webassembly ? "pass" : "fail");
    store?.close();
    store = null;
    try {
      store = await StudyStore.open(win.indexedDB);
      results.indexeddb = true;
    } catch {
      results.indexeddb = false;
    }
    start.setCheck("indexeddb", results.indexeddb ? "pass" : "fail");
    results.web_locks = hasWebLocks(nav);
    start.setCheck("web_locks", results.web_locks ? "pass" : "fail");
    persisted = await persistence;
    results.persisted = persisted;
    start.setCheck("persisted", persisted ? "pass" : "fail");
    results.viewport = viewportLargeEnough(win.innerWidth, win.innerHeight);
    start.setCheck("viewport", results.viewport ? "pass" : "fail");
    identity = null;
    served = null;
    engineIdentity = null;
    try {
      served = await fetchBuildIdentity(new URL("./", doc.baseURI).href);
    } catch {
      served = null;
    }
    const ready = await booted;
    if (owner !== generation) {
      return;
    }
    results.identity = ready && served !== null && engineIdentity !== null && sameBuild(served, engineIdentity, environment.build);
    identity = results.identity && served !== null ? served.identity : null;
    start.setCheck("identity", results.identity ? "pass" : "fail");
    let loaded: string[] | null = null;
    if (ready && engine !== null) {
      try {
        loaded = await engine.modules(PROBED_MODULES);
      } catch {
        loaded = null;
      }
    }
    if (owner !== generation) {
      return;
    }
    results.core_absent = loaded !== null && loaded.length === 0;
    start.setCheck("core_absent", results.core_absent ? "pass" : "fail", { modules: (loaded ?? []).join(", ") });
    readiness = results;
    const ok = passed(results);
    phase = ok ? "ready" : "start";
    start.setOpenable(ok);
    start.setCheckEnabled(true);
    start.setBusy(null);
    if (ok) {
      start.open.focus();
    }
  }

  const problemLine = (problem: { code: string; params: Record<string, unknown> }): string => {
    const table = t.refused.problems as Record<string, string>;
    const template = table[problem.code] ?? t.notices.unknown;
    const file = typeof problem.params.file === "string" ? problem.params.file : "";
    const detail = typeof problem.params.detail === "string" ? problem.params.detail : "";
    return fill(template, { file, detail });
  };

  const refusalOf = (error: unknown): string => {
    if (error instanceof KitReadError) {
      const refusal = error.refusal;
      return refusal.kind === "empty" ? t.refused.empty : fill(refusal.kind === "missing" ? t.refused.missing : t.refused.unreadable, { file: refusal.file });
    }
    return fill(t.refused.kit, { detail: errorText(error) });
  };

  const refuse = (owner: number, lines: string[]): void => {
    if (owner !== generation) {
      return;
    }
    lock?.release();
    lock = null;
    start.refuse(lines);
    start.setBusy(null);
    start.setCheckEnabled(true);
    phase = engine === null ? "start" : "ready";
    start.setOpenable(phase === "ready");
  };

  async function openKit(): Promise<void> {
    if (phase !== "ready" || engine === null || store === null || identity === null) {
      return;
    }
    const files = await picker.pick();
    if (files === null || phase !== "ready" || engine === null || store === null || identity === null) {
      return;
    }
    const owner = generation;
    const client = engine;
    const sessions = store;
    const build = identity;
    phase = "opening";
    start.refuse([]);
    start.setOpenable(false);
    start.setCheckEnabled(false);
    start.setBusy(t.opening);
    let folder: KitFolder;
    let opened: OpenedKit;
    let manifest: Uint8Array;
    try {
      folder = new KitFolder(kitEntries(files));
      const read = await folder.readManifest();
      manifest = read.manifest;
      opened = await client.call("study.open_kit", { manifest: copyOf(read.manifest), manifest_sha256: copyOf(read.sidecar) });
    } catch (error) {
      refuse(owner, [refusalOf(error)]);
      return;
    }
    let items: Map<string, Uint8Array>;
    try {
      items = await folder.readListed(opened.files);
      const sent: Record<string, Uint8Array> = {};
      for (const [path, bytes] of items) {
        sent[path] = copyOf(bytes);
      }
      const verdict = await client.call("study.verify_items", { files: sent });
      if (!verdict.ok) {
        refuse(owner, verdict.problems.map((problem) => problemLine(problem as { code: string; params: Record<string, unknown> })));
        return;
      }
    } catch (error) {
      refuse(owner, [refusalOf(error)]);
      return;
    }
    const key: SessionKey = { study_id: opened.study_id, annotator_id: opened.annotator_id, session_id: opened.session_id, manifest_sha256: await sha256Hex(manifest) };
    let record: SessionRecord | null;
    try {
      const held = await acquireSessionLock((nav as Navigator & { locks: LockManager }).locks, key);
      if (owner !== generation) {
        held?.release();
        return;
      }
      if (held === null) {
        refuse(owner, [t.refused.otherTab]);
        return;
      }
      lock = held;
      record = await sessions.get(key);
    } catch (error) {
      refuse(owner, [fill(t.refused.storage, { detail: errorText(error) })]);
      return;
    }
    const decision = decideResume(record, build);
    if (decision.kind === "other-build") {
      refuse(owner, [fill(t.refused.otherBuild, { version: decision.version })]);
      return;
    }
    if (decision.kind === "finished") {
      showLocked(decision.record);
      return;
    }
    const resumed = decision.kind === "resume" ? decision.record : null;
    let started: StudyResult;
    try {
      started = await client.call("study.start", { autosave: resumed === null ? null : copyOf(resumed.autosave_bytes), seq: resumed === null ? null : resumed.seq, t: nowTime() });
    } catch (error) {
      refuse(owner, [fill(t.refused.start, { detail: errorText(error) })]);
      return;
    }
    if (owner !== generation) {
      return;
    }
    verified = items;
    kitFiles = opened.files;
    bitmaps = new Map();
    skipped = skippedItems(resumed === null ? null : resumed.autosave_bytes) ?? [];
    saver = new SessionSaver(sessions, key, build, resumed, marker);
    runner = new StudyRunner(client, saver, {
      answered: (result, input) => answered(owner, result, input),
      committed: (result, input) => committed(owner, result, input),
      queue: (waiting, busy, saturated) => {
        if (owner === generation) {
          itemDock.saving(saturated);
          if (busy && phase === "session") {
            autosave.saving();
          }
          void waiting;
        }
      },
      failed: (stage) => failed(owner, stage),
    });
    view = started.view;
    start.setBusy(null);
    showIdentity(opened, started.view);
  }

  function showIdentity(opened: OpenedKit, state: StudyViewState): void {
    phase = "identity";
    const params = (state.step?.params ?? {}) as Record<string, unknown>;
    const resumed = state.step?.code === "identity_resumed" && typeof params.item === "number" && typeof params.total === "number" ? { item: params.item, total: params.total } : null;
    const screen = createIdentityScreen(doc, opened, served?.info.version ?? "", resumed, {
      yes: () => {
        phase = "session";
        setChrome();
        runner?.submit({ kind: "confirm-identity", t: nowTime() });
      },
      no: () => {
        phase = "declined";
        generation += 1;
        runner?.stop();
        closeEngine();
        lock?.release();
        lock = null;
        setChrome();
      },
    });
    progress.render(state.progress.done, state.progress.total, skipped, false);
    showScreen(screen.element);
    setChrome();
    screen.yes.focus();
  }

  function showLocked(record: SessionRecord): void {
    phase = "locked";
    const bytes = record.finished_bytes ?? new Uint8Array(0);
    const name = `${record.study_id}_${record.annotator_id}_${record.session_id}.json`;
    showScreen(createLockedScreen(doc, () => downloadBytes(doc, name, bytes)));
    setChrome();
  }

  const bitmapFor = (path: string): Promise<ImageBitmap> => {
    let found = bitmaps.get(path);
    if (found === undefined) {
      const bytes = verified.get(path);
      found = bytes === undefined ? Promise.reject(new Error(`${path} was not verified`)) : createImageBitmap(new Blob([ownBytes(bytes)], { type: "image/png" }), DECODE_OPTIONS);
      found.catch(() => {});
      bitmaps.set(path, found);
    }
    return found;
  };

  function showItem(state: StudyViewState): void {
    const path = state.item_file as string;
    const key = `${state.progress.done}:${path}`;
    if (key !== shownKey) {
      shownKey = key;
      branchEnd = false;
      studyStage.show(null, null);
      const owner = generation;
      bitmapFor(path).then(
        (bitmap) => {
          if (owner === generation && shownKey === key) {
            studyStage.show({ bitmap, width: bitmap.width, height: bitmap.height }, key);
          }
        },
        (error: unknown) => {
          if (owner === generation) {
            showBanner(`${t.banner.engine} (${errorText(error)})`);
          }
        },
      );
      const next = kitFiles[state.progress.done + 1];
      if (next !== undefined && next !== path) {
        void bitmapFor(next).catch(() => {});
      }
    }
    studyStage.marks(state);
    if (current !== studyStage.element) {
      showScreen(studyStage.element);
    }
  }

  function render(state: StudyViewState): void {
    view = state;
    if (state.paused) {
      shownKey = null;
      skipDialog.close();
      showScreen(createPausedScreen(doc, hiddenPause, () => togglePause()));
    } else if (state.item_file !== null) {
      hiddenPause = false;
      showItem(state);
    }
    itemDock.notice(noticeText(state.notice));
    progress.render(state.progress.done, state.progress.total, skipped, state.item_file !== null || state.paused);
    pauseLabel?.replaceChildren(state.paused ? t.header.resume : t.header.pause);
    renderDock();
  }

  function answered(owner: number, result: StudyResult, input: StudyInput): void {
    if (owner !== generation) {
      return;
    }
    const before = view?.progress.done ?? 0;
    for (let index = before; index < result.view.progress.done; index += 1) {
      skipped[index] = input.kind === "skip";
    }
    if (result.finished !== null) {
      view = result.view;
      return;
    }
    render(result.view);
  }

  function committed(owner: number, result: StudyResult, input: StudyInput): void {
    if (owner !== generation) {
      return;
    }
    autosave.saved(result.seq);
    if (input.kind === "pause") {
      pauseQueued = false;
    }
    const finished = result.finished;
    if (finished === null) {
      return;
    }
    const bytes = finished.bytes.slice();
    const name = finished.file_name;
    downloadBytes(doc, name, bytes);
    phase = "finished";
    progress.render(result.view.progress.done, result.view.progress.total, skipped, false);
    showScreen(
      createFinishedScreen(doc, name, {
        again: () => downloadBytes(doc, name, bytes),
        report: () => {
          const record = saver?.record;
          if (record === null || record === undefined) {
            return;
          }
          const report = sessionReport(record, {
            userAgent: nav.userAgent,
            viewport: { width: win.innerWidth, height: win.innerHeight, device_pixel_ratio: win.devicePixelRatio || 1 },
            persisted,
            readiness: complete(readiness),
          });
          downloadBytes(doc, reportFileName(record), new TextEncoder().encode(reportText(report)));
        },
      }),
    );
    setChrome();
  }

  function showBanner(text: string): void {
    stopped = true;
    runner?.stop();
    autosave.failed();
    banner = createBanner(doc, text);
    studyStage.setEnabled(false);
    if (current !== null) {
      showScreen(current);
    }
    renderDock();
  }

  function failed(owner: number, stage: "engine" | "commit"): void {
    if (owner !== generation || fatalShown || engine === null || engine.state === "failed") {
      return;
    }
    showBanner(stage === "commit" ? t.banner.autosave : t.banner.engine);
  }

  function submit(input: StudyInput): void {
    if (phase !== "session" || stopped || runner === null) {
      return;
    }
    if (!runner.submit(input)) {
      itemDock.saving(true);
    }
  }

  const itemOpen = (): boolean => phase === "session" && !stopped && view !== null && view.item_file !== null && !view.paused;

  function act(input: StudyInput): void {
    if (itemOpen()) {
      submit(input);
    }
  }

  function togglePause(): void {
    if (phase !== "session" || stopped || view === null) {
      return;
    }
    if (view.paused) {
      submit({ kind: "resume", t: nowTime() });
      return;
    }
    if (view.item_file !== null && !pauseQueued) {
      pauseQueued = true;
      submit({ kind: "pause", t: nowTime() });
    }
  }

  async function askSkip(): Promise<void> {
    if (!itemOpen() || skipDialog.isOpen) {
      return;
    }
    const type = view?.controls.item_type ?? "shape";
    const reason = await skipDialog.ask(type);
    if (reason !== null) {
      submit({ kind: "skip", reason, t: nowTime() });
    }
  }

  pause.addEventListener("click", () => togglePause());

  const onVisibility = (): void => {
    if (doc.visibilityState === "hidden" && itemOpen() && !pauseQueued) {
      hiddenPause = true;
      pauseQueued = true;
      submit({ kind: "pause", t: nowTime() });
    }
  };

  const reset = (): void => {
    generation += 1;
    runner?.stop();
    runner = null;
    saver = null;
    skipDialog.close();
    closeEngine();
    lock?.release();
    lock = null;
    store?.close();
    store = null;
    identity = null;
    served = null;
    engineIdentity = null;
    readiness = {};
    persisted = false;
    kitFiles = [];
    verified = new Map();
    bitmaps = new Map();
    view = null;
    shownKey = null;
    branchEnd = false;
    skipped = [];
    hiddenPause = false;
    pauseQueued = false;
    stopped = false;
    banner = null;
    fatalShown = false;
    phase = "start";
    studyStage.show(null, null);
    studyStage.marks(null);
    studyStage.setEnabled(true);
    autosave.hide();
    progress.render(0, 0, [], false);
  };

  return {
    head,
    stage,
    dock,
    enter() {
      reset();
      start = createStartScreen(doc, { check: () => void check(), open: () => void openKit() });
      start.setCheckEnabled(true);
      dock.prepend(itemDock.element);
      showScreen(start.element);
      setChrome();
      doc.addEventListener("visibilitychange", onVisibility);
    },
    leave() {
      doc.removeEventListener("visibilitychange", onVisibility);
      reset();
    },
    key(action) {
      if (phase !== "session" || stopped || skipDialog.isOpen) {
        return;
      }
      switch (action) {
        case "pause":
          togglePause();
          return;
        case "undo":
          act({ kind: "undo", t: nowTime() });
          return;
        case "m-line":
          act({ kind: "m-line", t: nowTime() });
          return;
        case "skip":
          void askSkip();
          return;
        case "next":
          act({ kind: "confirm", branchEnd, t: nowTime() });
          return;
        case "next-myofibril":
          act({ kind: "next-myofibril", t: nowTime() });
          return;
        case "zoom-in":
          if (itemOpen()) {
            studyStage.zoom(1);
          }
          return;
        case "zoom-out":
          if (itemOpen()) {
            studyStage.zoom(-1);
          }
          return;
        case "fit":
          if (itemOpen()) {
            studyStage.fit();
          }
          return;
        case "pan-start":
          studyStage.pan(true);
          return;
        case "pan-end":
          studyStage.pan(false);
          return;
      }
    },
  };
}
