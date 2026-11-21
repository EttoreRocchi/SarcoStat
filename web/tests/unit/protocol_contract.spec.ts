import { describe, expect, expectTypeOf, it } from "vitest";

import { catalogue, COMMAND_SPECS, EXPORT_KINDS, MARK_ROLES, STROKE_ROLES } from "../../src/worker/catalogue.ts";
import {
  ABSENT_FAMILIES,
  BRIDGE_VERSION,
  ERROR_CODES,
  type CommandArgs,
  type CommandName,
  type CommandResult,
} from "../../src/worker/protocol.ts";
import { pythonCatalogue, pythonExecutable, runPython } from "./worker_mock.ts";

function pythonConstants(): Record<string, unknown> {
  const script = [
    "import json",
    "from sarcostat.engine import bridge",
    "print(json.dumps({name: list(getattr(bridge, name)) if isinstance(getattr(bridge, name), tuple) else getattr(bridge, name) for name in ('BRIDGE_VERSION', 'ERROR_CODES', 'ABSENT_FAMILIES', 'MARK_ROLES', 'STROKE_ROLES', 'EXPORT_KINDS')}))",
  ].join("\n");
  return JSON.parse(runPython(["-c", script])) as Record<string, unknown>;
}

describe(`protocol.ts against python -m sarcostat.engine.bridge --catalogue (${pythonExecutable()})`, () => {
  const python = pythonCatalogue();

  it("speaks the bridge version of the engine", () => {
    expect(BRIDGE_VERSION).toBe(1);
    expect(python.bridge_version).toBe(BRIDGE_VERSION);
  });

  it("has the engine's error codes, absent families and role names", () => {
    const constants = pythonConstants();
    expect(constants.BRIDGE_VERSION).toBe(BRIDGE_VERSION);
    expect(constants.ERROR_CODES).toEqual([...ERROR_CODES]);
    expect(constants.ABSENT_FAMILIES).toEqual([...ABSENT_FAMILIES]);
    expect(python.absent_families).toEqual([...ABSENT_FAMILIES]);
    expect(constants.MARK_ROLES).toEqual([...MARK_ROLES]);
    expect(constants.STROKE_ROLES).toEqual([...STROKE_ROLES]);
    expect(constants.EXPORT_KINDS).toEqual([...EXPORT_KINDS]);
  });

  it("lists exactly the engine's commands", () => {
    expect(Object.keys(COMMAND_SPECS).sort()).toEqual(Object.keys(python.commands).sort());
  });

  it("has, for every command, the family and the argument and result schemas of the engine", () => {
    const mine = catalogue(BRIDGE_VERSION, ABSENT_FAMILIES);
    for (const name of Object.keys(python.commands)) {
      expect(mine.commands[name], name).toEqual(python.commands[name]);
    }
    expect(mine).toEqual(python);
  });

  it("derives the TypeScript types of arguments and results from those schemas", () => {
    expectTypeOf<CommandArgs<"study.click">>().toEqualTypeOf<{ x: number; y: number; t: { mono_s: number; wall_ms: number } }>();
    expectTypeOf<CommandArgs<"doc.export">>().toEqualTypeOf<{ what: "v1" | "results_json" | "results_csv" | "provenance" }>();
    expectTypeOf<CommandArgs<"study.open_kit">>().toEqualTypeOf<{ manifest: Uint8Array; manifest_sha256: Uint8Array }>();
    expectTypeOf<CommandArgs<"study.verify_items">>().toEqualTypeOf<{ files: Record<string, Uint8Array> }>();
    expectTypeOf<CommandArgs<"study.start">["autosave"]>().toEqualTypeOf<Uint8Array | null>();
    expectTypeOf<CommandResult<"sys.identity">>().toEqualTypeOf<{ sarcostat_version: string; source_digest: string; bridge_version: number }>();
    expectTypeOf<CommandResult<"study.click">["finished"]>().toEqualTypeOf<{ file_name: string; bytes: Uint8Array; sha256: string } | null>();
    expectTypeOf<CommandResult<"shape.click">["items"][number]["type"]>().toEqualTypeOf<"shape" | "chain" | "pair">();
    expectTypeOf<CommandResult<"study.click">["view"]["controls"]["item_type"]>().toEqualTypeOf<"shape" | "chain" | "pair" | null>();
    expectTypeOf<CommandResult<"results.get">["tables"][string]["rows"]>().toEqualTypeOf<string[][]>();
    expectTypeOf<CommandName>().toEqualTypeOf<keyof typeof COMMAND_SPECS>();
  });
});
