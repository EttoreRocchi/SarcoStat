import type { StudyNotice, StudyViewState } from "../../state/study_session.ts";
import { en } from "../../strings/en.ts";
import { fill } from "../../strings/format.ts";

export const LANDMARKS = ["A", "B", "C", "D", "E", "F"] as const;
export type Landmark = (typeof LANDMARKS)[number];

const t = en.study;

export type NoticeCode = Exclude<keyof typeof t.notices, "parts" | "lines">;

function word<T extends Record<string, string>>(table: T, value: unknown): string | null {
  return typeof value === "string" && value in table ? (table[value as keyof T] as string) : null;
}

export function noticeText(notice: StudyNotice | null): string | null {
  if (notice === null) {
    return null;
  }
  const code = notice.code as NoticeCode;
  const template = code in t.notices && code !== ("parts" as string) && code !== ("lines" as string) ? (t.notices[code] as string) : t.notices.unknown;
  const params = notice.params as Record<string, unknown>;
  const part = word(t.notices.parts, params.part) ?? "";
  const line = word(t.notices.lines, params.line) ?? "";
  return fill(template, { part, line });
}

export interface Prompt {
  readonly counter: { readonly number: number; readonly total: number } | null;
  readonly what: string;
  readonly hint: string | null;
  readonly landmark: Landmark | null;
  readonly kind: "z" | "m" | "chain" | "f1" | "f2" | null;
}

function int(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

function isLandmark(value: unknown): value is Landmark {
  return typeof value === "string" && (LANDMARKS as readonly string[]).includes(value);
}

export function prompt(view: StudyViewState): Prompt | null {
  const step = view.step;
  if (step === null || view.item_file === null) {
    return null;
  }
  const params = step.params as Record<string, unknown>;
  const zline = int(params.zline);
  const end = params.end === "second" ? t.dock.ends.second : t.dock.ends.first;
  switch (step.code) {
    case "shape_click":
    case "shape_click_or_skip_m_line": {
      const landmark = isLandmark(params.landmark) ? params.landmark : null;
      const number = int(params.index);
      const total = int(params.total);
      return {
        counter: number !== null && total !== null ? { number, total } : null,
        what: landmark === null ? t.notices.shape_click : t.dock.landmarks[landmark],
        hint: null,
        landmark,
        kind: landmark === "C" || landmark === "D" ? "m" : "z",
      };
    }
    case "shape_complete":
      return { counter: null, what: t.dock.shapeDone, hint: null, landmark: null, kind: null };
    case "chain_first_end":
    case "chain_second_end":
      return {
        counter: null,
        what: fill(t.dock.chainEnd, { zline: zline ?? "", end: step.code === "chain_second_end" ? t.dock.ends.second : t.dock.ends.first }),
        hint: t.dock.chainHint,
        landmark: null,
        kind: "chain",
      };
    case "pair_myofibril_1":
    case "pair_myofibril_2": {
      const second = step.code === "pair_myofibril_2";
      return {
        counter: null,
        what: fill(t.dock.pairEnd, { fibril: second ? t.dock.fibrils.second : t.dock.fibrils.first, zline: zline ?? "", end }),
        hint: second ? t.dock.pairHint2 : t.dock.pairHint1,
        landmark: null,
        kind: second ? "f2" : "f1",
      };
    }
    default:
      return { counter: null, what: noticeText(step) ?? "", hint: null, landmark: null, kind: null };
  }
}

export type ChipState = "done" | "now" | "todo" | "skipped";

export interface Chip {
  readonly letter: Landmark;
  readonly state: ChipState;
}

export function chips(view: StudyViewState): Chip[] {
  if (view.controls.item_type !== "shape" || view.item_file === null) {
    return [];
  }
  const placed = new Set(view.marks.map((mark) => mark.role));
  const params = (view.step?.params ?? {}) as Record<string, unknown>;
  const next = isLandmark(params.landmark) ? params.landmark : null;
  const skippedM = int(params.total) === 4 || (view.step?.code === "shape_complete" && placed.size === 4 && !placed.has("C"));
  return LANDMARKS.map((letter) => {
    if (placed.has(letter)) {
      return { letter, state: "done" };
    }
    if (skippedM && (letter === "C" || letter === "D")) {
      return { letter, state: "skipped" };
    }
    return { letter, state: letter === next ? "now" : "todo" };
  });
}

export function skippedItems(autosave: Uint8Array | null): boolean[] | null {
  if (autosave === null) {
    return null;
  }
  try {
    const doc = JSON.parse(new TextDecoder().decode(autosave)) as { records?: unknown };
    if (!Array.isArray(doc.records)) {
      return null;
    }
    return doc.records.map((record) => typeof record === "object" && record !== null && (record as { skipped?: unknown }).skipped === true);
  } catch {
    return null;
  }
}
