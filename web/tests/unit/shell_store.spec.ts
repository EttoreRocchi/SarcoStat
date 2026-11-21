import { describe, expect, it } from "vitest";

import {
  decideSwitch,
  exportEntries,
  INITIAL_STATE,
  leaveModel,
  mLineSkipped,
  notExportedLabel,
  noticeText,
  pendingActions,
  shapeGuidance,
  shapeUnfinished,
  statusItems,
  Store,
  unloadGuardActive,
  type AppState,
  type ModeState,
} from "../../src/state/store.ts";
import { Session, type EngineLike } from "../../src/state/session.ts";
import { en } from "../../src/strings/en.ts";

const IMAGE = { file_name: "cell.png", sha256: "a".repeat(64), width_px: 64, height_px: 48 };
const ROLES = ["A", "B", "C", "D", "E", "F"] as const;
type Role = (typeof ROLES)[number];

function modeState(options: { placed?: readonly Role[]; skipped?: boolean; items?: number; notExported?: boolean; image?: boolean; notice?: ModeState["notice"] } = {}): ModeState {
  const placed = options.placed ?? [];
  const skipped = options.skipped ?? false;
  const order: readonly Role[] = skipped ? ["A", "B", "E", "F"] : ROLES;
  const items = Array.from({ length: options.items ?? 0 }, (_, i) => ({
    item_id: `sarcomere-${i + 1}`,
    type: "shape" as const,
    sarcomere_id: i + 1,
    marks: ROLES.map((role) => ({ x: 1, y: 1, role })),
    strokes: [],
  }));
  const sarcomere = items.length + 1;
  const step: ModeState["step"] =
    options.image === false
      ? null
      : placed.length === order.length
        ? { code: "shape_ready", params: { sarcomere } }
        : { code: "shape_click", params: { sarcomere, landmark: order[placed.length] as string, number: placed.length + 1, of: order.length, can_skip_m_line: !skipped && placed.length <= 2 } };
  return {
    mode: options.image === false ? "home" : "shape",
    image: options.image === false ? null : IMAGE,
    settings: { pixel_size: 1, unit: "px", reference_direction_deg: null, click_sigma_z: null, click_sigma_m: null, click_perp_ratio: 1 },
    pending: { marks: placed.map((role, i) => ({ x: 10 + i, y: 10, role })), strokes: [] },
    items,
    controls: { can_confirm: placed.length === order.length, can_undo: placed.length > 0 || items.length > 0, can_skip_m_line: !skipped && placed.length <= 2, can_delete: items.length > 0 },
    step,
    notice: options.notice ?? null,
    not_exported: options.notExported ?? false,
  };
}

function appState(session: ModeState | null, route: AppState["route"] = "shape"): AppState {
  const store = new Store();
  store.setRoute(route ?? "home");
  if (session !== null) {
    store.applyModeState(session);
  }
  return store.get();
}

describe("step guidance from the ModeState", () => {
  it("rings the next landmark, fills the placed ones and dashes the rest", () => {
    const guidance = shapeGuidance(modeState({ placed: ["A", "B"], items: 3 }));
    expect(guidance).toMatchObject({ kind: "click", sarcomere: 4, number: 3, of: 6, landmark: "C" });
    expect(guidance?.chips.map((chip) => `${chip.landmark}:${chip.state}:${chip.line}`)).toEqual(["A:done:z", "B:done:z", "C:now:m", "D:todo:m", "E:todo:z", "F:todo:z"]);
  });

  it("marks the M-line chips off when the M-line is not identifiable", () => {
    const state = modeState({ placed: ["A", "B"], skipped: true });
    expect(mLineSkipped(state)).toBe(true);
    expect(pendingActions(state)).toBe(3);
    expect(shapeGuidance(state)?.chips.map((chip) => chip.state)).toEqual(["done", "done", "off", "off", "now", "todo"]);
    const ready = modeState({ placed: ["A", "B", "E", "F"], skipped: true });
    expect(mLineSkipped(ready)).toBe(true);
    expect(shapeGuidance(ready)).toMatchObject({ kind: "ready", of: 4 });
  });

  it("is absent without an image", () => {
    expect(shapeGuidance(modeState({ image: false }))).toBeNull();
    expect(shapeGuidance(null)).toBeNull();
  });
});

describe("unfinished items and the mode-switch guard", () => {
  it("counts pending clicks as an unfinished sarcomere", () => {
    expect(shapeUnfinished(modeState())).toBeNull();
    expect(shapeUnfinished(modeState({ placed: ["A", "B"], items: 3 }))).toMatchObject({ mode: "shape", number: 4, placed: 2, of: 6, actions: 2 });
    expect(shapeUnfinished(modeState({ skipped: true }))).toMatchObject({ placed: 0, of: 4, actions: 1 });
  });

  it("asks only when the mode being left holds an unfinished item", () => {
    const idle = appState(modeState({ items: 2 }));
    expect(decideSwitch(idle, "shape", "home")).toEqual({ kind: "go" });
    const busy = appState(modeState({ placed: ["A", "B"], items: 3, notExported: true }));
    const decision = decideSwitch(busy, "shape", "straightness");
    expect(decision).toMatchObject({ kind: "guard", from: "shape", to: "straightness", completed: 3, notExported: 3, item: { number: 4, placed: 2, of: 6 } });
    expect(decideSwitch(busy, "home", "shape")).toEqual({ kind: "go" });
    expect(decideSwitch(busy, "shape", "shape")).toEqual({ kind: "go" });
    expect(decideSwitch(busy, "study", "home")).toEqual({ kind: "go" });
  });

  it("keeps the unfinished item in the store while another mode is shown", () => {
    const store = new Store();
    store.setRoute("shape");
    store.applyModeState(modeState({ placed: ["A", "B"] }));
    store.setRoute("home");
    expect(store.get().unfinished.shape).toMatchObject({ placed: 2 });
    store.setRoute("shape");
    expect(shapeGuidance(store.get().session)?.number).toBe(3);
  });
});

describe("not exported", () => {
  it("labels the top bar and arms beforeunload only while items differ from the last export", () => {
    expect(notExportedLabel(appState(modeState()))).toBeNull();
    const dirty = appState(modeState({ items: 3, notExported: true }));
    expect(notExportedLabel(dirty)).toEqual({ text: "3 sarcomeres not exported", clear: false });
    expect(notExportedLabel(appState(modeState({ items: 1, notExported: true })))?.text).toBe("1 sarcomere not exported");
    expect(unloadGuardActive(dirty)).toBe(true);
    const store = new Store();
    store.applyModeState(modeState({ items: 3, notExported: true }));
    store.markExported();
    expect(unloadGuardActive(store.get())).toBe(false);
    expect(notExportedLabel(store.get())).toEqual({ text: en.topbar.allExported, clear: true });
    expect(store.get().session?.not_exported).toBe(false);
    expect(unloadGuardActive(INITIAL_STATE)).toBe(false);
  });

  it("asks before another image replaces unexported or unfinished work", () => {
    expect(leaveModel(appState(modeState({ items: 2 })), "next.png").ask).toBe(false);
    expect(leaveModel(appState(null), "next.png").ask).toBe(false);
    const dirty = leaveModel(appState(modeState({ items: 2, notExported: true })), "next.png");
    expect(dirty).toEqual({
      ask: true,
      canExport: true,
      lines: ["2 sarcomeres of cell.png are not exported.", "Opening next.png starts a new session without them."],
    });
    const unfinished = leaveModel(appState(modeState({ placed: ["A"], items: 1 })), "next.png");
    expect(unfinished.canExport).toBe(false);
    expect(unfinished.lines[0]).toBe("Sarcomere 2 of cell.png is unfinished.");
  });
});

describe("export menu entries", () => {
  it("enable the annotation file once an image is open and say why the others wait", () => {
    const none = exportEntries(appState(null));
    expect(none.map((entry) => entry.enabled)).toEqual([false, false, false, false, false]);
    expect(none.every((entry) => entry.reason === en.exportMenu.needImage)).toBe(true);
    const open = exportEntries(appState(modeState()));
    expect(open.map((entry) => [entry.id, entry.enabled])).toEqual([
      ["v1", true],
      ["results_csv", false],
      ["results_json", false],
      ["plots_svg", false],
      ["plots_png", false],
    ]);
    expect(open[0]?.reason).toBeNull();
    expect(open.slice(1).every((entry) => entry.reason !== null && !entry.reason.includes("\n"))).toBe(true);
  });
});

describe("notices and status items", () => {
  it("write the engine's notice codes in the app's words", () => {
    expect(noticeText({ code: "shape_incomplete", params: { placed: 2, needed: 6 } })).toBe("Place all 6 points of the sarcomere first (2 placed).");
    expect(noticeText({ code: "line_too_short", params: { line: "m" } })).toBe("The ends of M-line are less than 1 pixel apart: undo and click them again.");
    expect(noticeText({ code: "brand_new", params: {} })).toBe("The engine answered with the notice brand_new.");
  });

  it("raise the notice counter on each notice, also when the text repeats", () => {
    const store = new Store();
    const notice = { code: "nothing_to_undo", params: {} };
    store.applyModeState(modeState({ notice }));
    store.applyModeState(modeState({ notice }));
    expect(store.get().noticeSeq).toBe(2);
    expect(store.get().notice).toBe("Nothing to undo.");
    store.applyModeState(modeState());
    expect(store.get().notice).toBeNull();
    expect(store.get().noticeSeq).toBe(2);
  });

  it("count sarcomeres in the status line", () => {
    expect(statusItems(appState(modeState({ items: 3, placed: ["A"] })))).toEqual(["Sarcomeres 3 + 1 in progress"]);
    expect(statusItems(appState(modeState({ items: 3 }), "home"))).toEqual([]);
  });
});

class FakeEngine {
  readonly calls: [string, unknown][] = [];
  state: ModeState = modeState({ image: false });
  refuse: string | null = null;

  async call(cmd: string, args: unknown): Promise<unknown> {
    this.calls.push([cmd, args]);
    if (this.refuse !== null) {
      throw new Error(this.refuse);
    }
    if (cmd === "doc.new") {
      this.state = modeState();
    } else if (cmd === "shape.undo") {
      const placed = this.state.pending.marks.map((mark) => mark.role as Role);
      this.state = modeState({ placed: placed.slice(0, -1) });
    } else if (cmd === "doc.export") {
      return { file_name: "cell_annotations.json", text: "{}" };
    }
    return this.state;
  }
}

function sessionWith(engine: FakeEngine): { store: Store; session: Session } {
  const store = new Store();
  store.setEngine("ready");
  const session = new Session(store);
  session.attach(engine as unknown as EngineLike);
  return { store, session };
}

describe("Session (engine actions behind the store)", () => {
  const opened = { file: new Blob([]), fileName: "cell.png", kind: "png" as const, bytes: new Uint8Array(), sha256: "a".repeat(64), width: 64, height: 48 };

  it("sends doc.new with the image name, SHA-256 and size and keeps the ModeState", async () => {
    const engine = new FakeEngine();
    const { store, session } = sessionWith(engine);
    expect(await session.openImage(opened)).toBe(true);
    expect(engine.calls).toEqual([["doc.new", { image: IMAGE }]]);
    expect(store.get().session?.image).toEqual(IMAGE);
    expect(store.get().source?.fileName).toBe("cell.png");
  });

  it("discards the pending clicks with one undo per pending step and never more", async () => {
    const engine = new FakeEngine();
    const { store, session } = sessionWith(engine);
    engine.state = modeState({ placed: ["A", "B", "C"] });
    store.applyModeState(engine.state);
    await session.discardPending();
    expect(engine.calls.map(([cmd]) => cmd)).toEqual(["shape.undo", "shape.undo", "shape.undo"]);
    expect(store.get().unfinished.shape).toBeNull();
    await session.discardPending();
    expect(engine.calls).toHaveLength(3);
  });

  it("marks the session exported after doc.export v1", async () => {
    const engine = new FakeEngine();
    const { store, session } = sessionWith(engine);
    store.applyModeState(modeState({ items: 1, notExported: true }));
    expect(await session.exportV1()).toEqual({ file_name: "cell_annotations.json", text: "{}" });
    expect(engine.calls).toEqual([["doc.export", { what: "v1" }]]);
    expect(store.get().notExported).toBe(false);
  });

  it("turns a refusal into a notice and leaves the state as it was", async () => {
    const engine = new FakeEngine();
    const { store, session } = sessionWith(engine);
    store.applyModeState(modeState({ items: 1, notExported: true }));
    engine.refuse = "Nothing to export: there are no annotations.";
    expect(await session.exportV1()).toBeNull();
    expect(store.get().notice).toBe("Nothing to export: there are no annotations.");
    expect(store.get().notExported).toBe(true);
  });

  it("refuses work before the engine is ready", async () => {
    const engine = new FakeEngine();
    const store = new Store();
    const session = new Session(store);
    session.attach(engine as unknown as EngineLike);
    expect(await session.undo()).toBeNull();
    expect(engine.calls).toEqual([]);
    expect(store.get().notice).toBe(en.topbar.engineStarting);
  });

  it("runs engine commands one at a time, in order", async () => {
    const engine = new FakeEngine();
    const { session } = sessionWith(engine);
    await Promise.all([session.openImage(opened), session.click(1, 2), session.confirm(), session.undo()]);
    expect(engine.calls.map(([cmd]) => cmd)).toEqual(["doc.new", "shape.click", "shape.confirm", "shape.undo"]);
  });
});
