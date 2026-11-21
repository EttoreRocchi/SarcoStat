import { describe, expect, it } from "vitest";

import { EngineClient, EngineError } from "../../src/worker/client.ts";
import type { CommandName } from "../../src/worker/protocol.ts";
import { BRIDGE_VERSION } from "../../src/worker/protocol.ts";
import { mockAnswer, MockWorker, pythonCatalogue, sample, TEST_BUILD, validate } from "./worker_mock.ts";

const catalogue = pythonCatalogue();
const names = Object.keys(catalogue.commands).sort();

async function readyClient(): Promise<{ client: EngineClient; worker: MockWorker }> {
  const worker = new MockWorker({ catalogue });
  let ready: () => void = () => {};
  const done = new Promise<void>((resolve) => {
    ready = resolve;
  });
  const client = new EngineClient(worker, { build: TEST_BUILD, base: "http://127.0.0.1:1/v0.2.0rc1.dev0/" }, {
    boot: () => {},
    ready: () => ready(),
    fatal: (code, message) => {
      throw new Error(`unexpected fatal ${code}: ${message}`);
    },
  });
  client.start();
  await done;
  return { client, worker };
}

describe("mock worker generated from the catalogue JSON", () => {
  it("covers every catalogue command", () => {
    expect(names.length).toBeGreaterThanOrEqual(28);
  });

  it.each(names)("answers %s with a response that matches its result schema", (name) => {
    const spec = catalogue.commands[name];
    expect(spec).toBeDefined();
    const argBuffers: Uint8Array[] = [];
    const args = sample(spec!.args, argBuffers) as Record<string, unknown>;
    expect(validate(args, spec!.args, argBuffers.length, "args")).toEqual([]);
    const answer = mockAnswer(catalogue, { v: BRIDGE_VERSION, id: 7, cmd: name, args }, argBuffers);
    expect(answer.response.ok, JSON.stringify(answer.response)).toBe(true);
    expect(answer.response.id).toBe(7);
    if (answer.response.ok) {
      expect(validate(answer.response.result, spec!.result, answer.buffers.length, "result")).toEqual([]);
    }
  });

  it("refuses commands of absent families and unknown commands, and bad arguments", () => {
    const refuse = mockAnswer(catalogue, { v: BRIDGE_VERSION, id: 1, cmd: "chain.click", args: {} }, []);
    expect(refuse.response).toMatchObject({ ok: false, error: { code: "refused" } });
    const unknown = mockAnswer(catalogue, { v: BRIDGE_VERSION, id: 2, cmd: "doc.nothing", args: {} }, []);
    expect(unknown.response).toMatchObject({ ok: false, error: { code: "bad_request" } });
    const bad = mockAnswer(catalogue, { v: BRIDGE_VERSION, id: 3, cmd: "shape.click", args: { x: "1", y: 2 } }, []);
    expect(bad.response).toMatchObject({ ok: false, error: { code: "bad_request" } });
  });

  it.each(names)("carries %s through client.ts with buffers as Uint8Array both ways", async (name) => {
    const { client, worker } = await readyClient();
    const spec = catalogue.commands[name]!;
    const wireBuffers: Uint8Array[] = [];
    const wire = sample(spec.args, wireBuffers);
    const args = JSON.parse(JSON.stringify(wire), (_key, value: unknown) => {
      if (typeof value === "object" && value !== null && "$buf" in value) {
        return wireBuffers[(value as { $buf: number }).$buf];
      }
      return value;
    }) as Record<string, unknown>;
    const result = await client.call(name as CommandName, args as never);
    const sent = worker.posted.at(-1)!;
    expect(sent.message.kind).toBe("request");
    if (sent.message.kind === "request") {
      expect(sent.message.request.cmd).toBe(name);
      expect(validate(sent.message.request.args, spec.args, sent.message.buffers.length, "args")).toEqual([]);
      expect(sent.message.buffers).toHaveLength(wireBuffers.length);
      expect(sent.transfer).toHaveLength(wireBuffers.length);
    }
    const resultBuffers: Uint8Array[] = [];
    const expected = name === "sys.identity" ? result : sample(spec.result, resultBuffers);
    expect(result).toEqual(JSON.parse(JSON.stringify(expected), (_key, value: unknown) => {
      if (typeof value === "object" && value !== null && "$buf" in value) {
        return resultBuffers[(value as { $buf: number }).$buf];
      }
      return value;
    }));
  });

  it("rejects an engine error with its code and message", async () => {
    const { client } = await readyClient();
    const error = await client.call("shape.click", { x: "nope" as unknown as number, y: 1 }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(EngineError);
    expect((error as EngineError).code).toBe("bad_request");
  });
});
