import type { CommandArgs, CommandName, CommandResult } from "../worker/protocol.ts";
import type { BuildSeen, Counts, SessionKey, SessionRecord } from "./study_store_v1.ts";

export type StudyResult = CommandResult<"study.click">;
export type StudyViewState = StudyResult["view"];
export type StudyNotice = NonNullable<StudyViewState["notice"]>;

export interface Time {
  readonly mono_s: number;
  readonly wall_ms: number;
}

export type StudyInput =
  | { readonly kind: "confirm-identity"; readonly t: Time }
  | { readonly kind: "click"; readonly x: number; readonly y: number; readonly t: Time }
  | { readonly kind: "undo"; readonly t: Time }
  | { readonly kind: "m-line"; readonly t: Time }
  | { readonly kind: "next-myofibril"; readonly t: Time }
  | { readonly kind: "confirm"; readonly branchEnd: boolean; readonly t: Time }
  | { readonly kind: "skip"; readonly reason: string; readonly t: Time }
  | { readonly kind: "pause"; readonly t: Time }
  | { readonly kind: "resume"; readonly t: Time };

export interface StudyCaller {
  call<C extends CommandName>(cmd: C, args: CommandArgs<C>): Promise<CommandResult<C>>;
}

export interface CommitSink {
  commit(result: StudyResult, input: StudyInput): Promise<void>;
}

export interface RunnerListener {
  answered(result: StudyResult, input: StudyInput): void;
  committed(result: StudyResult, input: StudyInput): void;
  queue(waiting: number, busy: boolean, saturated: boolean): void;
  failed(stage: "engine" | "commit", error: Error): void;
}

export const MAX_QUEUE = 8;

export function nowTime(): Time {
  return { mono_s: performance.now() / 1000, wall_ms: Date.now() };
}

function send(engine: StudyCaller, input: StudyInput): Promise<StudyResult> {
  const t = { mono_s: input.t.mono_s, wall_ms: input.t.wall_ms };
  switch (input.kind) {
    case "confirm-identity":
      return engine.call("study.confirm_identity", { t });
    case "click":
      return engine.call("study.click", { x: input.x, y: input.y, t });
    case "undo":
      return engine.call("study.undo", { t });
    case "m-line":
      return engine.call("study.skip_m_line", { t });
    case "next-myofibril":
      return engine.call("study.next_myofibril", { t });
    case "confirm":
      return engine.call("study.confirm_item", { branch_end: input.branchEnd, t });
    case "skip":
      return engine.call("study.skip_item", { reason: input.reason, t });
    case "pause":
      return engine.call("study.pause", { t });
    case "resume":
      return engine.call("study.resume", { t });
  }
}

export function needsCommit(result: StudyResult): boolean {
  return result.autosave !== null || result.finished !== null;
}

export class StudyRunner {
  private readonly engine: StudyCaller;
  private readonly sink: CommitSink;
  private readonly listener: RunnerListener;
  private readonly waiting: StudyInput[] = [];
  private inFlight = false;
  private halted = false;
  private saturated = false;

  constructor(engine: StudyCaller, sink: CommitSink, listener: RunnerListener) {
    this.engine = engine;
    this.sink = sink;
    this.listener = listener;
  }

  get busy(): boolean {
    return this.inFlight || this.waiting.length > 0;
  }

  get stopped(): boolean {
    return this.halted;
  }

  get queued(): readonly StudyInput[] {
    return this.waiting.slice();
  }

  submit(input: StudyInput): boolean {
    if (this.halted) {
      return false;
    }
    if (this.inFlight && this.waiting.length >= MAX_QUEUE) {
      this.saturated = true;
      this.report();
      return false;
    }
    this.waiting.push(input);
    this.report();
    void this.pump();
    return true;
  }

  stop(): void {
    this.halted = true;
    this.waiting.length = 0;
    this.report();
  }

  private report(): void {
    if (!this.inFlight && this.waiting.length === 0) {
      this.saturated = false;
    }
    this.listener.queue(this.waiting.length, this.inFlight, this.saturated);
  }

  private async pump(): Promise<void> {
    if (this.inFlight) {
      return;
    }
    for (;;) {
      const input = this.halted ? undefined : this.waiting.shift();
      if (input === undefined) {
        break;
      }
      this.inFlight = true;
      this.report();
      let result: StudyResult;
      try {
        result = await send(this.engine, input);
      } catch (error) {
        this.inFlight = false;
        this.stop();
        this.listener.failed("engine", error instanceof Error ? error : new Error(String(error)));
        return;
      }
      if (this.halted) {
        break;
      }
      this.listener.answered(result, input);
      if (needsCommit(result)) {
        try {
          await this.sink.commit(result, input);
        } catch (error) {
          this.inFlight = false;
          this.stop();
          this.listener.failed("commit", error instanceof Error ? error : new Error(String(error)));
          return;
        }
      }
      this.inFlight = false;
      this.listener.committed(result, input);
    }
    this.inFlight = false;
    this.report();
  }
}

export interface BuildIdentity {
  readonly version: string;
  readonly source_digest: string;
  readonly build_json_sha256: string;
  readonly resume_digests: readonly string[];
}

export type ResumeDecision =
  | { readonly kind: "new" }
  | { readonly kind: "resume"; readonly record: SessionRecord }
  | { readonly kind: "finished"; readonly record: SessionRecord }
  | { readonly kind: "other-build"; readonly version: string };

export function decideResume(record: SessionRecord | null, build: BuildIdentity): ResumeDecision {
  if (record === null) {
    return { kind: "new" };
  }
  if (record.status === "finished") {
    return { kind: "finished", record };
  }
  if (record.source_digest === build.source_digest || build.resume_digests.includes(record.source_digest)) {
    return { kind: "resume", record };
  }
  return { kind: "other-build", version: record.build_version };
}

export interface RecordStore {
  put(record: SessionRecord): Promise<void>;
}

export interface RestartMarker {
  present(): boolean;
  clear(): void;
}

export const RESTART_MARKER = "sarcostat-study-v1:restart";

export function sessionStorageMarker(storage: Storage | null): RestartMarker & { set(): void } {
  return {
    present() {
      try {
        return storage?.getItem(RESTART_MARKER) === "1";
      } catch {
        return false;
      }
    },
    clear() {
      try {
        storage?.removeItem(RESTART_MARKER);
      } catch {
        return;
      }
    },
    set() {
      try {
        storage?.setItem(RESTART_MARKER, "1");
      } catch {
        return;
      }
    },
  };
}

const NO_COUNTS: Counts = { pauses: 0, resumes: 0, worker_restarts: 0 };

export class SessionSaver implements CommitSink {
  private readonly store: RecordStore;
  private readonly key: SessionKey;
  private readonly build: BuildIdentity;
  private readonly marker: RestartMarker;
  private last: SessionRecord | null;
  private reopened: boolean;

  constructor(store: RecordStore, key: SessionKey, build: BuildIdentity, existing: SessionRecord | null, marker: RestartMarker) {
    this.store = store;
    this.key = key;
    this.build = build;
    this.marker = marker;
    this.last = existing;
    this.reopened = existing !== null;
  }

  get record(): SessionRecord | null {
    return this.last;
  }

  private seen(): BuildSeen {
    return { version: this.build.version, build_json_sha256: this.build.build_json_sha256, source_digest: this.build.source_digest };
  }

  next(result: StudyResult, input: StudyInput, updated: string): SessionRecord {
    const base = this.last;
    const restarted = this.marker.present() && (base === null || this.reopened);
    let counts: Counts = base?.counts ?? NO_COUNTS;
    let buildsSeen: readonly BuildSeen[] = base?.builds_seen ?? [this.seen()];
    if (base !== null && this.reopened) {
      counts = { ...counts, resumes: counts.resumes + 1 };
      buildsSeen = [...buildsSeen, this.seen()];
    }
    if (restarted) {
      counts = { ...counts, worker_restarts: counts.worker_restarts + 1 };
    }
    if (input.kind === "pause") {
      counts = { ...counts, pauses: counts.pauses + 1 };
    }
    const finished = result.finished;
    return {
      study_id: this.key.study_id,
      annotator_id: this.key.annotator_id,
      session_id: this.key.session_id,
      manifest_sha256: this.key.manifest_sha256,
      seq: result.seq,
      status: finished === null ? "open" : "finished",
      build_version: base?.build_version ?? this.build.version,
      source_digest: base?.source_digest ?? this.build.source_digest,
      build_json_sha256: base?.build_json_sha256 ?? this.build.build_json_sha256,
      builds_seen: buildsSeen,
      counts,
      autosave_bytes: result.autosave ?? base?.autosave_bytes ?? new Uint8Array(0),
      finished_bytes: finished === null ? null : finished.bytes,
      finished_sha256: finished === null ? null : finished.sha256,
      updated,
    };
  }

  async commit(result: StudyResult, input: StudyInput): Promise<void> {
    const restarted = this.marker.present() && (this.last === null || this.reopened);
    const record = this.next(result, input, new Date().toISOString());
    await this.store.put(record);
    this.last = record;
    this.reopened = false;
    if (restarted) {
      this.marker.clear();
    }
  }
}
