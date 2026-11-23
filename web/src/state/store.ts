import type { DecodedImage } from "../io/image/decode.ts";
import { isAbsent, type Route } from "../router.ts";
import { en } from "../strings/en.ts";
import { fill, plural, type Params } from "../strings/format.ts";
import { ABSENT_FAMILIES, type CommandResult, type Identity } from "../worker/protocol.ts";

export type ModeState = CommandResult<"doc.new">;
export type ResultsView = CommandResult<"results.get">;
export type ModeItem = ModeState["items"][number];
export type CodeObject = NonNullable<ModeState["step"]>;
export type EngineStatus = "booting" | "ready" | "stopped";
export type AnnotationRoute = "shape" | "straightness" | "register";

export const LANDMARKS = ["A", "B", "C", "D", "E", "F"] as const;
export type Landmark = (typeof LANDMARKS)[number];
export type ChipState = "done" | "now" | "todo" | "off";

export interface Chip {
  readonly landmark: Landmark;
  readonly state: ChipState;
  readonly line: "z" | "m";
}

export interface Guidance {
  readonly kind: "click" | "ready";
  readonly sarcomere: number;
  readonly number: number;
  readonly of: number;
  readonly landmark: Landmark | null;
  readonly chips: readonly Chip[];
}

export interface UnfinishedItem {
  readonly mode: "shape";
  readonly number: number;
  readonly placed: number;
  readonly of: number;
  readonly actions: number;
  readonly chips: readonly Chip[];
}

export interface OpenedImage {
  readonly file: Blob;
  readonly fileName: string;
  readonly kind: "png" | "jpeg";
  readonly bytes: Uint8Array;
  readonly sha256: string;
  readonly width: number;
  readonly height: number;
  readonly decoded?: DecodedImage;
}

export interface AppState {
  readonly route: Route | null;
  readonly engine: EngineStatus;
  readonly identity: Identity | null;
  readonly session: ModeState | null;
  readonly source: OpenedImage | null;
  readonly results: ResultsView | null;
  readonly notExported: boolean;
  readonly unfinished: { readonly shape: UnfinishedItem | null };
  readonly notice: string | null;
  readonly noticeSeq: number;
}

export type SwitchDecision =
  | { readonly kind: "go" }
  | { readonly kind: "guard"; readonly from: Route; readonly to: Route; readonly item: UnfinishedItem; readonly completed: number; readonly notExported: number };

export interface LeaveModel {
  readonly ask: boolean;
  readonly canExport: boolean;
  readonly lines: readonly string[];
}

export interface ExportEntry {
  readonly id: ExportKind;
  readonly group: "annotations" | "results" | "plots";
  readonly title: string;
  readonly hint: string;
  readonly enabled: boolean;
  readonly reason: string | null;
  readonly key: string | null;
}

export type ExportKind = "v1" | "results_csv" | "results_json" | "plots_svg" | "plots_png";

const ITEM_ROUTES: Readonly<Record<string, Route>> = { chain: "straightness", pair: "register" };

const LINE_OF: Readonly<Record<Landmark, "z" | "m">> = { A: "z", B: "z", C: "m", D: "m", E: "z", F: "z" };

export const INITIAL_STATE: AppState = {
  route: null,
  engine: "booting",
  identity: null,
  session: null,
  source: null,
  results: null,
  notExported: false,
  unfinished: { shape: null },
  notice: null,
  noticeSeq: 0,
};

function param(code: CodeObject | null, name: string): string | number | boolean | undefined {
  return code?.params[name];
}

function isLandmark(value: unknown): value is Landmark {
  return typeof value === "string" && (LANDMARKS as readonly string[]).includes(value);
}

export function pendingRoles(state: ModeState): Set<string> {
  return new Set(state.pending.marks.map((mark) => mark.role));
}

export function mLineSkipped(state: ModeState): boolean {
  const step = state.step;
  if (step?.code === "shape_click") {
    return param(step, "of") === 4;
  }
  if (step?.code === "shape_ready") {
    return !pendingRoles(state).has("C");
  }
  return false;
}

export function pendingActions(state: ModeState | null): number {
  if (state === null || state.mode !== "shape") {
    return 0;
  }
  return state.pending.marks.length + (mLineSkipped(state) ? 1 : 0);
}

export function shapeGuidance(state: ModeState | null): Guidance | null {
  const step = state?.step ?? null;
  if (state === null || step === null || state.image === null || (step.code !== "shape_click" && step.code !== "shape_ready")) {
    return null;
  }
  const placed = pendingRoles(state);
  const skipped = mLineSkipped(state);
  const sarcomere = Number(param(step, "sarcomere") ?? 1);
  const next = step.code === "shape_click" ? param(step, "landmark") : undefined;
  const landmark = isLandmark(next) ? next : null;
  const chips = LANDMARKS.map((name): Chip => {
    let chip: ChipState = "todo";
    if (skipped && LINE_OF[name] === "m") {
      chip = "off";
    } else if (placed.has(name)) {
      chip = "done";
    } else if (name === landmark) {
      chip = "now";
    }
    return { landmark: name, state: chip, line: LINE_OF[name] };
  });
  const of = skipped ? 4 : 6;
  if (step.code === "shape_ready") {
    return { kind: "ready", sarcomere, number: of, of, landmark: null, chips };
  }
  return { kind: "click", sarcomere, number: Number(param(step, "number") ?? placed.size + 1), of: Number(param(step, "of") ?? of), landmark, chips };
}

export function shapeUnfinished(state: ModeState | null): UnfinishedItem | null {
  const actions = pendingActions(state);
  const guidance = shapeGuidance(state);
  if (state === null || actions === 0 || guidance === null) {
    return null;
  }
  return { mode: "shape", number: guidance.sarcomere, placed: state.pending.marks.length, of: guidance.of, actions, chips: guidance.chips };
}

export function shapeItemCount(state: ModeState | null): number {
  return state === null ? 0 : state.items.filter((item) => item.type === "shape").length;
}

export function unfinishedIn(state: AppState, route: Route): UnfinishedItem | null {
  return route === "shape" ? state.unfinished.shape : null;
}

export function decideSwitch(state: AppState, from: Route, to: Route): SwitchDecision {
  if (from === to) {
    return { kind: "go" };
  }
  const item = unfinishedIn(state, from);
  if (item === null) {
    return { kind: "go" };
  }
  return {
    kind: "guard",
    from,
    to,
    item,
    completed: shapeItemCount(state.session),
    notExported: state.notExported ? state.session?.items.length ?? 0 : 0,
  };
}

export function notExportedLabel(state: AppState): { text: string; clear: boolean } | null {
  const items = state.session?.items ?? [];
  if (state.session === null || items.length === 0) {
    return null;
  }
  if (!state.notExported) {
    return { text: en.topbar.allExported, clear: true };
  }
  const shapesOnly = items.every((item) => item.type === "shape");
  const t = en.topbar.notExported;
  return {
    text: shapesOnly ? plural(items.length, t.shapeOne, t.shapeOther) : plural(items.length, t.itemOne, t.itemOther),
    clear: false,
  };
}

export function leaveModel(state: AppState, nextName: string): LeaveModel {
  const current = state.session?.image?.file_name ?? "";
  const count = state.notExported ? state.session?.items.length ?? 0 : 0;
  const item = state.unfinished.shape;
  if (state.session?.image == null || (count === 0 && item === null)) {
    return { ask: false, canExport: false, lines: [] };
  }
  const lines: string[] = [];
  if (count > 0) {
    lines.push(plural(count, en.leave.notExportedOne, en.leave.notExportedOther, { current }));
  }
  if (item !== null) {
    lines.push(fill(en.leave.unfinished, { k: item.number, current }));
  }
  lines.push(fill(en.leave.consequence, { next: nextName }));
  return { ask: true, canExport: count > 0, lines };
}

export function unloadGuardActive(state: AppState): boolean {
  return state.notExported;
}

export function exportEntries(state: AppState): ExportEntry[] {
  const hasImage = state.session?.image != null;
  const hasItems = hasImage && (state.session?.items.length ?? 0) > 0;
  const m = en.exportMenu;
  const reason = hasImage ? (hasItems ? null : m.needItems) : m.needImage;
  return [
    { id: "v1", group: "annotations", title: m.annotationFile, hint: m.annotationHint, enabled: hasItems, reason, key: "E" },
    { id: "results_csv", group: "results", title: m.resultsTable, hint: m.resultsTableHint, enabled: hasItems, reason, key: null },
    { id: "results_json", group: "results", title: m.resultsJson, hint: m.resultsJsonHint, enabled: hasItems, reason, key: null },
    { id: "plots_svg", group: "plots", title: m.plotsSvg, hint: m.plotsSvgHint, enabled: false, reason: m.plotsAbsent, key: null },
    { id: "plots_png", group: "plots", title: m.plotsPng, hint: m.plotsPngHint, enabled: false, reason: m.plotsAbsent, key: null },
  ];
}

export function hasPendingClick(state: AppState): boolean {
  return pendingActions(state.session) > 0;
}

export function absentRoutes(state: ModeState | null): Route[] {
  const routes: Route[] = [];
  for (const kind of ["chain", "pair"] as const) {
    const route = ITEM_ROUTES[kind] as Route;
    if (state !== null && state.items.some((item) => item.type === kind) && isAbsent(route, ABSENT_FAMILIES)) {
      routes.push(route);
    }
  }
  return routes;
}

export function absentLine(state: ModeState | null): string | null {
  const routes = absentRoutes(state);
  const first = routes[0];
  const second = routes[1];
  if (first === undefined) {
    return null;
  }
  const kindOf = (route: Route): string => (route === "straightness" ? en.absent.kinds.chain : en.absent.kinds.pair);
  if (second === undefined) {
    return fill(en.absent.itemsOne, { mode: en.modes[first].short, kind: kindOf(first) });
  }
  return fill(en.absent.itemsOther, {
    modes: fill(en.absent.pair, { first: en.modes[first].short, second: en.modes[second].short }),
    kinds: fill(en.absent.pair, { first: kindOf(first), second: kindOf(second) }),
  });
}

export function needsScipy(state: ModeState | null): boolean {
  return state !== null && state.items.some((item) => item.type === "chain" || item.type === "pair");
}

export function sourceMatches(state: AppState, sha256: string, width: number, height: number): boolean {
  const image = state.session?.image ?? null;
  return state.source === null && image !== null && image.sha256 === sha256 && image.width_px === width && image.height_px === height;
}

export function noticeText(code: CodeObject): string {
  const table = en.notices as Readonly<Record<string, string>>;
  const template = table[code.code];
  if (template === undefined || code.code === "unknown") {
    return fill(en.notices.unknown, { code: code.code });
  }
  if (code.code === "document_loaded" && code.params.image_checked === false) {
    return `${fill(template, code.params as Params)} ${en.open.unchecked}`;
  }
  const params: Record<string, string | number | boolean> = { ...code.params };
  const line = code.params.line;
  if (typeof line === "string" && line in en.lines) {
    params.line = en.lines[line as keyof typeof en.lines];
  }
  return fill(template, params as Params);
}

export function statusItems(state: AppState): string[] {
  if (state.route !== "shape" || state.session?.image == null) {
    return [];
  }
  const n = shapeItemCount(state.session);
  return [fill(state.unfinished.shape !== null ? en.status.sarcomeresPending : en.status.sarcomeres, { n })];
}

export type Listener = (state: AppState, previous: AppState) => void;

export class Store {
  private state: AppState = INITIAL_STATE;
  private readonly listeners = new Set<Listener>();

  get(): AppState {
    return this.state;
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private set(next: AppState): void {
    const previous = this.state;
    this.state = next;
    for (const listener of [...this.listeners]) {
      listener(next, previous);
    }
  }

  setRoute(route: Route): void {
    this.set({ ...this.state, route });
  }

  setEngine(engine: EngineStatus, identity: Identity | null = this.state.identity): void {
    this.set({ ...this.state, engine, identity });
  }

  applyModeState(session: ModeState, source: OpenedImage | null = this.state.source, results: ResultsView | null = session.image === null ? null : this.state.results): void {
    const notice = session.notice === null ? null : noticeText(session.notice);
    this.set({
      ...this.state,
      session,
      source,
      results,
      notExported: session.not_exported,
      unfinished: { shape: shapeUnfinished(session) },
      notice,
      noticeSeq: notice === null ? this.state.noticeSeq : this.state.noticeSeq + 1,
    });
  }

  setResults(results: ResultsView | null): void {
    this.set({ ...this.state, results });
  }

  setSource(source: OpenedImage | null): void {
    this.set({ ...this.state, source });
  }

  markExported(): void {
    const session = this.state.session;
    this.set({ ...this.state, notExported: false, session: session === null ? null : { ...session, not_exported: false } });
  }

  setNotice(notice: string | null): void {
    this.set({ ...this.state, notice, noticeSeq: notice === null ? this.state.noticeSeq : this.state.noticeSeq + 1 });
  }
}
