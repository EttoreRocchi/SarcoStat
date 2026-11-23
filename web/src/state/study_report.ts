import type { BuildSeen, Counts, SessionRecord } from "./study_store_v1.ts";

export const REPORT_FORMAT = "sarcostat-session-report";
export const REPORT_FORMAT_VERSION = 1;
export const REPORT_KEYS = [
  "annotator_id",
  "builds_seen",
  "counts",
  "finished_sha256",
  "format",
  "format_version",
  "manifest_sha256",
  "readiness",
  "session_id",
  "storage_persisted",
  "study_id",
  "user_agent",
  "viewport",
] as const;
export const READINESS_KEYS = ["webassembly", "indexeddb", "web_locks", "persisted", "viewport", "identity", "core_absent"] as const;

export type ReadinessKey = (typeof READINESS_KEYS)[number];
export type Readiness = Readonly<Record<ReadinessKey, boolean>>;

export interface ReportViewport {
  readonly width: number;
  readonly height: number;
  readonly device_pixel_ratio: number;
}

export interface ReportEnvironment {
  readonly userAgent: string;
  readonly viewport: ReportViewport;
  readonly persisted: boolean;
  readonly readiness: Readiness;
}

export interface SessionReport {
  readonly format: typeof REPORT_FORMAT;
  readonly format_version: typeof REPORT_FORMAT_VERSION;
  readonly study_id: string;
  readonly annotator_id: string;
  readonly session_id: string;
  readonly manifest_sha256: string;
  readonly finished_sha256: string | null;
  readonly builds_seen: readonly BuildSeen[];
  readonly user_agent: string;
  readonly viewport: ReportViewport;
  readonly storage_persisted: boolean;
  readonly readiness: Readiness;
  readonly counts: Counts;
}

export function sessionReport(record: SessionRecord, environment: ReportEnvironment): SessionReport {
  const readiness = Object.fromEntries(READINESS_KEYS.map((key) => [key, environment.readiness[key] === true])) as Record<ReadinessKey, boolean>;
  return {
    format: REPORT_FORMAT,
    format_version: REPORT_FORMAT_VERSION,
    study_id: record.study_id,
    annotator_id: record.annotator_id,
    session_id: record.session_id,
    manifest_sha256: record.manifest_sha256,
    finished_sha256: record.finished_sha256,
    builds_seen: record.builds_seen.map((seen) => ({ version: seen.version, build_json_sha256: seen.build_json_sha256, source_digest: seen.source_digest })),
    user_agent: environment.userAgent,
    viewport: { width: environment.viewport.width, height: environment.viewport.height, device_pixel_ratio: environment.viewport.device_pixel_ratio },
    storage_persisted: environment.persisted,
    readiness,
    counts: { pauses: record.counts.pauses, resumes: record.counts.resumes, worker_restarts: record.counts.worker_restarts },
  };
}

function sorted(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(sorted);
  }
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, sorted((value as Record<string, unknown>)[key])]),
    );
  }
  return value;
}

export function reportText(report: SessionReport): string {
  return `${JSON.stringify(sorted(report), null, 2)}\n`;
}

export function reportFileName(record: Pick<SessionRecord, "study_id" | "annotator_id" | "session_id">): string {
  return `${record.study_id}_${record.annotator_id}_${record.session_id}.report.json`;
}
