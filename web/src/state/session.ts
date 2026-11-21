import { en } from "../strings/en.ts";
import type { CommandArgs, CommandName, CommandResult } from "../worker/protocol.ts";
import { pendingActions, type ModeState, type OpenedImage, type Store } from "./store.ts";

export interface EngineLike {
  call<C extends CommandName>(cmd: C, args: CommandArgs<C>): Promise<CommandResult<C>>;
}

export type ExportedFile = CommandResult<"doc.export">;

const MAX_DISCARD_STEPS = 16;

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export class Session {
  private readonly store: Store;
  private engine: EngineLike | null = null;
  private queue: Promise<unknown> = Promise.resolve();

  constructor(store: Store) {
    this.store = store;
  }

  attach(engine: EngineLike): void {
    this.engine = engine;
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

  private command<C extends CommandName>(cmd: C, args: CommandArgs<C>): Promise<ModeState | null> {
    return this.run(async (engine) => {
      const state = (await engine.call(cmd, args)) as ModeState;
      this.store.applyModeState(state);
      return state;
    });
  }

  async openImage(image: OpenedImage): Promise<boolean> {
    const state = await this.run(async (engine) => {
      const next = (await engine.call("doc.new", {
        image: { file_name: image.fileName, sha256: image.sha256, width_px: image.width, height_px: image.height },
      })) as ModeState;
      this.store.applyModeState(next, image);
      return next;
    });
    return state !== null;
  }

  click(x: number, y: number): Promise<ModeState | null> {
    return this.command("shape.click", { x, y });
  }

  undo(): Promise<ModeState | null> {
    return this.command("shape.undo", {});
  }

  confirm(): Promise<ModeState | null> {
    return this.command("shape.confirm", {});
  }

  skipMLine(): Promise<ModeState | null> {
    return this.command("shape.skip_m_line", {});
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
}
