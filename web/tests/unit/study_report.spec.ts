import { describe, expect, it } from "vitest";

import { READINESS_KEYS, REPORT_KEYS, reportFileName, reportText, sessionReport } from "../../src/state/study_report.ts";
import type { SessionRecord } from "../../src/state/study_store_v1.ts";

const HEX = (c: string): string => c.repeat(64);

const RECORD: SessionRecord = {
  study_id: "S",
  annotator_id: "A1",
  session_id: "S1",
  manifest_sha256: HEX("c"),
  seq: 42,
  status: "finished",
  build_version: "0.2.0b1",
  source_digest: HEX("a"),
  build_json_sha256: HEX("b"),
  builds_seen: [
    { version: "0.2.0b1", build_json_sha256: HEX("b"), source_digest: HEX("a") },
    { version: "0.2.0rc1.post1", build_json_sha256: HEX("d"), source_digest: HEX("e") },
  ],
  counts: { pauses: 2, resumes: 1, worker_restarts: 1 },
  autosave_bytes: new TextEncoder().encode('{"records":[{"raw_clicks":[{"x":12.5,"y":3,"seconds":4.2}]}]}'),
  finished_bytes: new TextEncoder().encode('{"items":[{"points":{"z1":[[1,2],[3,4]]},"active_seconds":9.5}]}'),
  finished_sha256: HEX("f"),
  updated: "2026-11-21T10:00:00.000Z",
};

describe("session report", () => {
  const report = sessionReport(RECORD, {
    userAgent: "Mozilla/5.0 test",
    viewport: { width: 1600, height: 900, device_pixel_ratio: 2 },
    persisted: false,
    readiness: { webassembly: true, indexeddb: true, web_locks: true, persisted: false, viewport: true, identity: true, core_absent: true },
  });

  it("holds exactly the whitelisted keys, with booleans for the readiness checks", () => {
    expect(Object.keys(report).sort()).toEqual([...REPORT_KEYS].sort());
    expect(Object.keys(report.readiness).sort()).toEqual([...READINESS_KEYS].sort());
    expect(Object.keys(report.viewport).sort()).toEqual(["device_pixel_ratio", "height", "width"]);
    expect(Object.keys(report.counts).sort()).toEqual(["pauses", "resumes", "worker_restarts"]);
    expect(report).toMatchObject({ format: "sarcostat-session-report", format_version: 1, storage_persisted: false, finished_sha256: HEX("f"), counts: { pauses: 2, resumes: 1, worker_restarts: 1 } });
  });

  it("lists every build that touched the session", () => {
    expect(report.builds_seen.map((seen) => seen.version)).toEqual(["0.2.0b1", "0.2.0rc1.post1"]);
    for (const seen of report.builds_seen) {
      expect(Object.keys(seen).sort()).toEqual(["build_json_sha256", "source_digest", "version"]);
    }
  });

  it("carries no coordinate, timing or metric from the session", () => {
    const text = reportText(report);
    for (const word of ["raw_clicks", "points", "seconds", "active_seconds", "x", "y", "seq", "updated", "autosave", "12.5", "9.5"]) {
      expect(text).not.toContain(`"${word}"`);
    }
    expect(text).not.toContain("12.5");
    expect(text.endsWith("}\n")).toBe(true);
    expect(JSON.parse(text)).toEqual(JSON.parse(JSON.stringify(report)));
    expect(reportFileName(RECORD)).toBe("S_A1_S1.report.json");
  });
});
