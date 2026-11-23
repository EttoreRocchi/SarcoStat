import { en } from "../strings/en.ts";
import type { CommandArgs, CommandName, CommandResult } from "../worker/protocol.ts";
import { needsScipy, pendingActions, type ModeState, type OpenedImage, type ResultsView, type Store } from "./store.ts";

export interface EngineLike {
  call<C extends CommandName>(cmd: C, args: CommandArgs<C>): Promise<CommandResult<C>>;
}

export type ExportedFile = CommandResult<"doc.export">;
export type ImageJSON = NonNullable<CommandArgs<"doc.load_v1">["image"]>;
export type ResultsKind = "results_json" | "results_csv";

export type LoadOutcome =
  | { readonly kind: "loaded"; readonly state: ModeState }
  | { readonly kind: "confirm"; readonly message: string }
  | { readonly kind: "failed" };

const MAX_DISCARD_STEPS = 16;

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function errorCode(error: unknown): string | null {
  const code = (error as { code?: unknown } | null)?.code;
  return typeof code === "string" ? code : null;
}

export function imageJson(image: OpenedImage): ImageJSON {
  return { file_name: image.fileName, sha256: image.sha256, width_px: image.width, height_px: image.height };
}

export class Session {
  private readonly store: Store;
  private engine: EngineLike | null = null;
  private queue: Promise<unknown> = Promise.resolve();
  private scipy = false;

  constructor(store: Store) {
    this.store = store;
  }

  attach(engine: EngineLike): void {
    this.engine = engine;
    this.scipy = false;
  }

  get ready(): boolean {
    return this.engine !== null && this.store.get().engine === "ready";
  }

  private run<T>(task: (engine: EngineLike) => Promise<T>): Promise<T | null> {
    const next = this.queue.then(async () => {
      const engine = this.engine;
      if (engine === null || this.store.get().engine !== "ready") {
        this.store.setNotice(en.topbar.engineStarting);
        return null;
      }
      try {
        return await task(engine);
      } catch (error) {
        this.store.setNotice(message(error));
        return null;
      }
    });
    this.queue = next.catch(() => undefined);
    return next;
  }

  private async withScipy(engine: EngineLike): Promise<void> {
    if (!this.scipy && needsScipy(this.store.get().session)) {
      await engine.call("sys.load_scipy", {});
      this.scipy = true;
    }
  }

  private async results(engine: EngineLike): Promise<ResultsView | null> {
    if (this.store.get().session?.image == null) {
      this.store.setResults(null);
      return null;
    }
    await this.withScipy(engine);
    const view = await engine.call("results.get", {});
    this.store.setResults(view);
    return view;
  }

  private command<C extends CommandName>(cmd: C, args: CommandArgs<C>, refresh = false): Promise<ModeState | null> {
    return this.run(async (engine) => {
      const state = (await engine.call(cmd, args)) as ModeState;
      this.store.applyModeState(state);
      if (refresh) {
        await this.results(engine);
      }
      return state;
    });
  }

  async openImage(image: OpenedImage): Promise<boolean> {
    const state = await this.run(async (engine) => {
      const next = (await engine.call("doc.new", { image: imageJson(image) })) as ModeState;
      this.store.applyModeState(next, image, null);
      await this.results(engine);
      return next;
    });
    return state !== null;
  }

  attachPixels(image: OpenedImage): void {
    this.store.setSource(image);
  }

  loadV1(text: string, image: OpenedImage | null, confirm: boolean): Promise<LoadOutcome> {
    return this.run(async (engine): Promise<LoadOutcome> => {
      let next: ModeState;
      try {
        next = (await engine.call("doc.load_v1", { text, image: image === null ? null : imageJson(image), confirm })) as ModeState;
      } catch (error) {
        if (errorCode(error) === "needs_confirmation") {
          return { kind: "confirm", message: message(error) };
        }
        throw error;
      }
      this.store.applyModeState(next, image, null);
      await this.results(engine);
      return { kind: "loaded", state: next };
    }).then((outcome) => outcome ?? { kind: "failed" });
  }

  click(x: number, y: number): Promise<ModeState | null> {
    return this.command("shape.click", { x, y });
  }

  undo(): Promise<ModeState | null> {
    return this.command("shape.undo", {});
  }

  confirm(): Promise<ModeState | null> {
    return this.command("shape.confirm", {}, true);
  }

  skipMLine(): Promise<ModeState | null> {
    return this.command("shape.skip_m_line", {});
  }

  deleteItem(itemId: string): Promise<ModeState | null> {
    return this.command("shape.delete", { item_id: itemId }, true);
  }

  setScale(pixelSize: number, unit: string): Promise<ModeState | null> {
    return this.command("doc.set_scale", { pixel_size: pixelSize, unit }, true);
  }

  setClickError(sigma: number | null): Promise<ModeState | null> {
    return this.command("doc.set_click_error", { sigma }, true);
  }

  estimateClickError(): Promise<ModeState | null> {
    return this.command("doc.estimate_click_error", {}, true);
  }

  refreshResults(): Promise<ResultsView | null> {
    return this.run((engine) => this.results(engine));
  }

  discardPending(): Promise<ModeState | null> {
    return this.run(async (engine) => {
      let state = this.store.get().session;
      for (let step = 0; step < MAX_DISCARD_STEPS && pendingActions(state) > 0; step += 1) {
        state = (await engine.call("shape.undo", {})) as ModeState;
        this.store.applyModeState(state);
      }
      return state;
    });
  }

  exportV1(): Promise<ExportedFile | null> {
    return this.run(async (engine) => {
      const file = await engine.call("doc.export", { what: "v1" });
      this.store.markExported();
      return file;
    });
  }

  exportResults(what: ResultsKind): Promise<readonly [ExportedFile, ExportedFile] | null> {
    return this.run(async (engine) => {
      await this.withScipy(engine);
      const file = await engine.call("doc.export", { what });
      const sidecar = await engine.call("doc.export", { what: "provenance" });
      return [file, sidecar] as const;
    });
  }
}
