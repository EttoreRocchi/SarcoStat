import type { ModeState, ResultsView } from "../../state/store.ts";

export const SARCOMERE_COLUMNS = {
  id: "Sarcomere",
  shapeClass: "Class",
  si: "SI",
  asr: "AsR",
  cur: "Cur",
  stability: "Stability",
  length: "Sarcomere length",
  z1: "Z-line 1",
  z2: "Z-line 2",
  m: "M-line",
  status: "Status",
} as const;

export type ColumnKey = keyof typeof SARCOMERE_COLUMNS;

export const LENGTH_KEYS = ["length", "z1", "z2", "m"] as const satisfies readonly ColumnKey[];

export interface Cell {
  readonly text: string;
  readonly ref: string;
}

export interface SarcomereResult {
  readonly id: number;
  readonly measured: boolean;
  readonly cells: Readonly<Record<ColumnKey, Cell>>;
  readonly stability: number | null;
}

export interface PlotPoint {
  readonly id: number;
  readonly si: number;
  readonly u: number | null;
  readonly shapeClass: string;
}

export interface SummaryRow {
  readonly name: Cell;
  readonly value: Cell;
}

export interface Summary {
  readonly title: Cell;
  readonly rows: readonly SummaryRow[];
  readonly notes: readonly Cell[];
}

export interface ShapeResults {
  readonly columns: Readonly<Record<ColumnKey, Cell>> | null;
  readonly sarcomeres: readonly SarcomereResult[];
  readonly notes: { readonly si: Cell | null; readonly asrCur: Cell | null; readonly stability: Cell | null };
  readonly scale: Cell | null;
  readonly click: Cell | null;
  readonly summary: Summary | null;
  readonly points: readonly PlotPoint[];
  readonly band: readonly [number, number] | null;
}

function cell(text: string | undefined, ref: string): Cell | null {
  return text === undefined ? null : { text, ref };
}

export function splitPlusMinus(text: string): { value: string; pm: string } {
  const at = text.indexOf(" ± ");
  return at < 0 ? { value: text, pm: "" } : { value: text.slice(0, at), pm: text.slice(at) };
}

export function shapeResults(view: ResultsView | null): ShapeResults | null {
  if (view === null) {
    return null;
  }
  const table = view.tables.sarcomeres;
  const base = "tables.sarcomeres";
  let columns: Record<ColumnKey, Cell> | null = null;
  const sarcomeres: SarcomereResult[] = [];
  const stabilities = new Map<number, number | null>();
  for (const point of view.plot_data.shape) {
    stabilities.set(point.sarcomere_id, point.stability ?? null);
  }
  if (table !== undefined) {
    const index = {} as Record<ColumnKey, number>;
    const heads = {} as Record<ColumnKey, Cell>;
    for (const [key, name] of Object.entries(SARCOMERE_COLUMNS) as [ColumnKey, string][]) {
      const at = table.columns.indexOf(name);
      if (at < 0) {
        throw new Error(`results.get: the sarcomeres table has no column ${name}`);
      }
      index[key] = at;
      heads[key] = { text: name, ref: `${base}.columns.${at}` };
    }
    columns = heads;
    table.rows.forEach((row, r) => {
      const cells = {} as Record<ColumnKey, Cell>;
      for (const key of Object.keys(SARCOMERE_COLUMNS) as ColumnKey[]) {
        cells[key] = { text: row[index[key]] ?? "", ref: `${base}.rows.${r}.${index[key]}` };
      }
      const id = Number(cells.id.text);
      sarcomeres.push({ id, measured: cells.si.text !== "", cells, stability: stabilities.get(id) ?? null });
    });
  }
  const notes = table?.notes ?? [];
  const summaryTable = view.tables.shape_summary;
  const summary: Summary | null =
    summaryTable === undefined
      ? null
      : {
          title: { text: summaryTable.title, ref: "tables.shape_summary.title" },
          rows: summaryTable.rows.map((row, r) => ({
            name: { text: row[0] ?? "", ref: `tables.shape_summary.rows.${r}.0` },
            value: { text: row[1] ?? "", ref: `tables.shape_summary.rows.${r}.1` },
          })),
          notes: summaryTable.notes.map((text, n) => ({ text, ref: `tables.shape_summary.notes.${n}` })),
        };
  const points: PlotPoint[] = [];
  for (const point of view.plot_data.shape) {
    if (point.shape_index !== null) {
      points.push({ id: point.sarcomere_id, si: point.shape_index, u: point.u_shape_index, shapeClass: point.shape_class });
    }
  }
  const band = view.plot_data.shape_rectangular_band;
  return {
    columns,
    sarcomeres,
    notes: {
      si: cell(notes[0], `${base}.notes.0`),
      asrCur: cell(notes[1], `${base}.notes.1`),
      stability: cell(notes[2], `${base}.notes.2`),
    },
    scale: cell(view.sentences[0], "sentences.0"),
    click: table === undefined ? null : cell(view.sentences[1], "sentences.1"),
    summary,
    points,
    band: band.length === 2 ? [band[0] as number, band[1] as number] : null,
  };
}

export function sarcomereIds(state: ModeState | null): number[] {
  return (state?.items ?? []).flatMap((item) => (item.type === "shape" && item.sarcomere_id !== null ? [item.sarcomere_id] : []));
}

export function pickSarcomere(ids: readonly number[], current: number | null, step: -1 | 0 | 1): number | null {
  if (ids.length === 0) {
    return null;
  }
  const at = current === null ? -1 : ids.indexOf(current);
  if (at < 0) {
    return ids[ids.length - 1] ?? null;
  }
  const next = Math.min(ids.length - 1, Math.max(0, at + step));
  return ids[next] ?? null;
}

export function readRef(view: unknown, ref: string): unknown {
  let value: unknown = view;
  for (const part of ref.split(".")) {
    if (value === null || typeof value !== "object") {
      return undefined;
    }
    value = Array.isArray(value) ? value[Number(part)] : (value as Record<string, unknown>)[part];
  }
  return value;
}
