import { describe, expect, it } from "vitest";

import {
  decideResume,
  MAX_QUEUE,
  SessionSaver,
  StudyRunner,
  type BuildIdentity,
  type RestartMarker,
  type StudyCaller,
  type StudyInput,
  type StudyResult,
} from "../../src/state/study_session.ts";
import type { SessionRecord } from "../../src/state/study_store_v1.ts";

const T = { mono_s: 1, wall_ms: 2 };
const HEX = (c: string): string => c.repeat(64);
const BUILD: BuildIdentity = { version: "0.2.0rc1", source_digest: HEX("a"), build_json_sha256: HEX("b"), resume_digests: [] };
const KEY = { study_id: "S", annotator_id: "A1", session_id: "S1", manifest_sha256: HEX("c") };

function result(seq: number, options: { autosave?: boolean; finished?: boolean; done?: number } = {}): StudyResult {
  return {
    view: {
      item_file: "items/001.png",
      roi: [0, 0, 1, 1],
      title: null,
      marks: [],
      strokes: [],
      controls: { can_confirm: false, can_undo: false, can_skip_m_line: false, can_next_myofibril: false, shows_branch_end: false, item_type: "shape" },
      step: null,
      progress: { done: options.done ?? 0, total: 2 },
      notice: null,
      paused: false,
      finished: null,
    },
    autosave: options.autosave === false ? null : new Uint8Array([seq]),
    seq,
    finished: options.finished === true ? { file_name: "S_A1_S1.json", bytes: new Uint8Array([9, 9]), sha256: HEX("d") } : null,
  };
}

interface Deferred {
  cmd: string;
  args: unknown;
  resolve(value: StudyResult): void;
  reject(error: Error): void;
}

class FakeEngine implements StudyCaller {
  readonly calls: Deferred[] = [];
  inFlight = 0;
  maxInFlight = 0;

  call<C extends string>(cmd: C, args: unknown): Promise<never> {
    this.inFlight += 1;
    this.maxInFlight = Math.max(this.maxInFlight, this.inFlight);
    return new Promise((resolve, reject) => {
      this.calls.push({
        cmd,
        args,
        resolve: (value) => {
          this.inFlight -= 1;
          resolve(value as never);
        },
        reject: (error) => {
          this.inFlight -= 1;
          reject(error);
        },
      });
    }) as Promise<never>;
  }
}

function flush(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

function click(x: number): StudyInput {
  return { kind: "click", x, y: x, t: T };
}

function runnerWith(commit: (result: StudyResult, input: StudyInput) => Promise<void> = async () => {}) {
  const engine = new FakeEngine();
  const events: string[] = [];
  const queue: { waiting: number; busy: boolean; saturated: boolean }[] = [];
  const failures: string[] = [];
  const runner = new StudyRunner(engine as unknown as StudyCaller, {
    commit: async (res, input) => {
      events.push(`commit ${res.seq}`);
      await commit(res, input);
    },
  }, {
    answered: (res) => events.push(`answered ${res.seq}`),
    committed: (res) => events.push(`committed ${res.seq}`),
    queue: (waiting, busy, saturated) => queue.push({ waiting, busy, saturated }),
    failed: (stage, error) => failures.push(`${stage}: ${error.message}`),
  });
  return { engine, runner, events, queue, failures };
}

describe("study runner: single flight and commit barrier", () => {
  it("sends one study command at a time and answers, commits and ticks in that order", async () => {
    const { engine, runner, events } = runnerWith();
    runner.submit(click(1));
    runner.submit(click(2));
    runner.submit({ kind: "undo", t: T });
    await flush();
    expect(engine.calls.map((call) => call.cmd)).toEqual(["study.click"]);
    engine.calls[0]?.resolve(result(1));
    await flush();
    expect(events).toEqual(["answered 1", "commit 1", "committed 1"]);
    expect(engine.calls.map((call) => call.cmd)).toEqual(["study.click", "study.click"]);
    engine.calls[1]?.resolve(result(2));
    await flush();
    engine.calls[2]?.resolve(result(3));
    await flush();
    expect(engine.calls.map((call) => [call.cmd, call.args])).toEqual([
      ["study.click", { x: 1, y: 1, t: T }],
      ["study.click", { x: 2, y: 2, t: T }],
      ["study.undo", { t: T }],
    ]);
    expect(engine.maxInFlight).toBe(1);
    expect(runner.busy).toBe(false);
  });

  it("lets the next command leave only after the commit, and ticks only after it", async () => {
    let release: () => void = () => {};
    const { engine, runner, events } = runnerWith(() => new Promise((resolve) => (release = resolve)));
    runner.submit(click(1));
    runner.submit(click(2));
    await flush();
    engine.calls[0]?.resolve(result(1));
    await flush();
    expect(events).toEqual(["answered 1", "commit 1"]);
    expect(engine.calls).toHaveLength(1);
    release();
    await flush();
    expect(events).toEqual(["answered 1", "commit 1", "committed 1"]);
    expect(engine.calls).toHaveLength(2);
  });

  it("queues up to eight inputs in order during a barrier, then ignores input and reports saturation until the queue empties", async () => {
    const { engine, runner, queue } = runnerWith();
    runner.submit(click(0));
    await flush();
    const accepted = Array.from({ length: MAX_QUEUE + 3 }, (_, k) => runner.submit(click(k + 1)));
    expect(accepted.filter(Boolean)).toHaveLength(MAX_QUEUE);
    expect(accepted.slice(MAX_QUEUE)).toEqual([false, false, false]);
    expect(queue[queue.length - 1]).toEqual({ waiting: MAX_QUEUE, busy: true, saturated: true });
    for (let k = 0; k <= MAX_QUEUE; k += 1) {
      engine.calls[k]?.resolve(result(k + 1));
      await flush();
    }
    expect(engine.calls.map((call) => (call.args as { x: number }).x)).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8]);
    expect(queue[queue.length - 1]).toEqual({ waiting: 0, busy: false, saturated: false });
  });

  it("does not commit an answer without an autosave or a finished file, but still ticks", async () => {
    const { engine, runner, events } = runnerWith();
    runner.submit({ kind: "resume", t: T });
    await flush();
    engine.calls[0]?.resolve(result(4, { autosave: false }));
    await flush();
    expect(events).toEqual(["answered 4", "committed 4"]);
  });

  it("commits the finished session before it reports it, so the download follows the commit", async () => {
    const { engine, runner, events } = runnerWith();
    runner.submit({ kind: "confirm", branchEnd: false, t: T });
    await flush();
    expect(engine.calls[0]?.args).toEqual({ branch_end: false, t: T });
    engine.calls[0]?.resolve(result(9, { autosave: false, finished: true }));
    await flush();
    expect(events).toEqual(["answered 9", "commit 9", "committed 9"]);
  });

  it("stops at a failed commit or a refused command and drops the queued inputs", async () => {
    const failing = runnerWith(async () => {
      throw new Error("quota");
    });
    failing.runner.submit(click(1));
    failing.runner.submit(click(2));
    await flush();
    failing.engine.calls[0]?.resolve(result(1));
    await flush();
    expect(failing.failures).toEqual(["commit: quota"]);
    expect(failing.events).toEqual(["answered 1", "commit 1"]);
    expect(failing.engine.calls).toHaveLength(1);
    expect(failing.runner.stopped).toBe(true);
    expect(failing.runner.submit(click(3))).toBe(false);

    const refused = runnerWith();
    refused.runner.submit(click(1));
    refused.runner.submit(click(2));
    await flush();
    refused.engine.calls[0]?.reject(new Error("the engine stopped"));
    await flush();
    expect(refused.failures).toEqual(["engine: the engine stopped"]);
    expect(refused.engine.calls).toHaveLength(1);
  });
});

function record(overrides: Partial<SessionRecord> = {}): SessionRecord {
  return {
    ...KEY,
    seq: 5,
    status: "open",
    build_version: "0.2.0b1",
    source_digest: HEX("e"),
    build_json_sha256: HEX("f"),
    builds_seen: [{ version: "0.2.0b1", build_json_sha256: HEX("f"), source_digest: HEX("e") }],
    counts: { pauses: 1, resumes: 0, worker_restarts: 0 },
    autosave_bytes: new Uint8Array([5]),
    finished_bytes: null,
    finished_sha256: null,
    updated: "2026-11-21T10:00:00.000Z",
    ...overrides,
  };
}

describe("resume decision (build isolation)", () => {
  it("starts a new session, resumes an open one of this build or of a listed digest, refuses one of another build and blocks a finished one", () => {
    expect(decideResume(null, BUILD)).toEqual({ kind: "new" });
    const own = record({ source_digest: BUILD.source_digest });
    expect(decideResume(own, BUILD)).toEqual({ kind: "resume", record: own });
    const other = record();
    expect(decideResume(other, BUILD)).toEqual({ kind: "other-build", version: "0.2.0b1" });
    expect(decideResume(other, { ...BUILD, resume_digests: [HEX("e")] })).toEqual({ kind: "resume", record: other });
    const finished = record({ status: "finished", finished_bytes: new Uint8Array([1]), finished_sha256: HEX("1") });
    expect(decideResume(finished, BUILD)).toEqual({ kind: "finished", record: finished });
    expect(decideResume({ ...finished, source_digest: BUILD.source_digest }, BUILD).kind).toBe("finished");
  });
});

class Marker implements RestartMarker {
  constructor(public on: boolean) {}
  present(): boolean {
    return this.on;
  }
  clear(): void {
    this.on = false;
  }
}

describe("session saver: the record of every acknowledged command", () => {
  it("creates the record with the creating build and zero counts, then counts acknowledged pauses", async () => {
    const puts: SessionRecord[] = [];
    const saver = new SessionSaver({ put: async (value) => void puts.push(value) }, KEY, BUILD, null, new Marker(false));
    await saver.commit(result(1), { kind: "confirm-identity", t: T });
    await saver.commit(result(2), { kind: "pause", t: T });
    await saver.commit(result(3), click(1));
    expect(puts.map((value) => [value.seq, value.counts.pauses, value.status])).toEqual([
      [1, 0, "open"],
      [2, 1, "open"],
      [3, 1, "open"],
    ]);
    expect(puts[0]?.builds_seen).toEqual([{ version: BUILD.version, build_json_sha256: BUILD.build_json_sha256, source_digest: BUILD.source_digest }]);
    expect(puts[0]).toMatchObject({ ...KEY, build_version: BUILD.version, source_digest: BUILD.source_digest, build_json_sha256: BUILD.build_json_sha256, finished_bytes: null, finished_sha256: null });
  });

  it("after a reopening, appends this build and counts the resume (and a restart) in the put of the first acknowledged command only", async () => {
    const puts: SessionRecord[] = [];
    const marker = new Marker(true);
    const stored = record();
    const saver = new SessionSaver({ put: async (value) => void puts.push(value) }, KEY, { ...BUILD, resume_digests: [stored.source_digest] }, stored, marker);
    await saver.commit(result(6), { kind: "confirm-identity", t: T });
    await saver.commit(result(7), click(1));
    expect(puts[0]?.seq).toBe(6);
    expect(puts[0]?.counts).toEqual({ pauses: 1, resumes: 1, worker_restarts: 1 });
    expect(puts[0]?.builds_seen.map((seen) => seen.version)).toEqual(["0.2.0b1", BUILD.version]);
    expect(puts[0]).toMatchObject({ build_version: "0.2.0b1", source_digest: stored.source_digest, build_json_sha256: stored.build_json_sha256 });
    expect(puts[1]?.counts).toEqual({ pauses: 1, resumes: 1, worker_restarts: 1 });
    expect(puts[1]?.builds_seen).toHaveLength(2);
    expect(marker.on).toBe(false);
  });

  it("keeps the restart marker and the reopening when the put fails, so the next put still counts them", async () => {
    let fail = true;
    const puts: SessionRecord[] = [];
    const marker = new Marker(true);
    const saver = new SessionSaver(
      {
        put: async (value) => {
          if (fail) {
            throw new Error("quota");
          }
          puts.push(value);
        },
      },
      KEY,
      BUILD,
      record({ source_digest: BUILD.source_digest }),
      marker,
    );
    await expect(saver.commit(result(6), click(1))).rejects.toThrow("quota");
    expect(marker.on).toBe(true);
    fail = false;
    await saver.commit(result(7), click(2));
    expect(puts[0]?.counts).toEqual({ pauses: 1, resumes: 1, worker_restarts: 1 });
  });

  it("writes the finished record with its bytes and SHA-256 and keeps the last autosave bytes", async () => {
    const puts: SessionRecord[] = [];
    const saver = new SessionSaver({ put: async (value) => void puts.push(value) }, KEY, BUILD, null, new Marker(false));
    await saver.commit(result(1), click(1));
    await saver.commit(result(2, { autosave: false, finished: true }), { kind: "confirm", branchEnd: false, t: T });
    expect(puts[1]).toMatchObject({ seq: 2, status: "finished", finished_sha256: HEX("d") });
    expect([...(puts[1]?.finished_bytes ?? [])]).toEqual([9, 9]);
    expect([...(puts[1]?.autosave_bytes ?? [])]).toEqual([1]);
  });
});
