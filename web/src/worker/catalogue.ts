export type SchemaKind = "object" | "array" | "string" | "number" | "integer" | "boolean" | "null" | "buffer";

export interface Schema {
  readonly type: SchemaKind | readonly SchemaKind[];
  readonly properties?: { readonly [key: string]: Schema };
  readonly required?: readonly string[];
  readonly additionalProperties?: false | Schema;
  readonly items?: Schema;
  readonly enum?: readonly (string | null)[];
  readonly minimum?: number;
}

export const MARK_ROLES = ["A", "B", "C", "D", "E", "F", "chain", "myofibril_1", "myofibril_2"] as const;
export const STROKE_ROLES = ["z1", "m", "z2", "chain", "myofibril_1", "myofibril_2"] as const;
export const EXPORT_KINDS = ["v1", "results_json", "results_csv", "provenance"] as const;
export const ITEM_TYPES = ["shape", "chain", "pair"] as const;

const STRING = { type: "string" } as const;
const NUMBER = { type: "number" } as const;
const BOOLEAN = { type: "boolean" } as const;
const BUFFER = { type: "buffer" } as const;

type Props = { readonly [key: string]: Schema };

interface ObjectSchema<P extends Props> {
  readonly type: "object";
  readonly properties: P;
  readonly required: readonly string[];
  readonly additionalProperties: false;
}

interface NullableObjectSchema<P extends Props> {
  readonly type: readonly ["object", "null"];
  readonly properties: P;
  readonly required: readonly string[];
  readonly additionalProperties: false;
}

function object<const P extends Props>(properties: P): ObjectSchema<P> {
  return { type: "object", properties, required: Object.keys(properties), additionalProperties: false };
}

function nullableObject<const P extends Props>(properties: P): NullableObjectSchema<P> {
  return { type: ["object", "null"], properties, required: Object.keys(properties), additionalProperties: false };
}

function map<const V extends Schema>(values: V): { readonly type: "object"; readonly additionalProperties: V } {
  return { type: "object", additionalProperties: values };
}

function array<const I extends Schema>(items: I): { readonly type: "array"; readonly items: I } {
  return { type: "array", items };
}

function nullableArray<const I extends Schema>(items: I): { readonly type: readonly ["array", "null"]; readonly items: I } {
  return { type: ["array", "null"], items };
}

function nullable<const K extends SchemaKind>(kind: K): { readonly type: readonly [K, "null"] } {
  return { type: [kind, "null"] };
}

function integer(minimum: number): { readonly type: "integer"; readonly minimum: number } {
  return { type: "integer", minimum };
}

function nullableInteger(minimum: number): { readonly type: readonly ["integer", "null"]; readonly minimum: number } {
  return { type: ["integer", "null"], minimum };
}

function choice<const E extends readonly string[]>(values: E): { readonly type: "string"; readonly enum: E } {
  return { type: "string", enum: values };
}

function nullableChoice<const E extends readonly string[]>(
  values: E,
): { readonly type: readonly ["string", "null"]; readonly enum: readonly [...E, null] } {
  return { type: ["string", "null"], enum: [...values, null] };
}

const CODE_PROPS = {
  code: STRING,
  params: map({ type: ["string", "integer", "boolean"] } as const),
} as const;

function code(): ObjectSchema<typeof CODE_PROPS> {
  return object(CODE_PROPS);
}

function nullableCode(): NullableObjectSchema<typeof CODE_PROPS> {
  return nullableObject(CODE_PROPS);
}

function point() {
  return array(NUMBER);
}

function mark() {
  return object({ x: NUMBER, y: NUMBER, role: choice(MARK_ROLES) });
}

function stroke() {
  return object({ p1: point(), p2: point(), role: choice(STROKE_ROLES) });
}

const IMAGE_PROPS = {
  file_name: STRING,
  sha256: STRING,
  width_px: integer(1),
  height_px: integer(1),
} as const;

function image() {
  return object(IMAGE_PROPS);
}

function nullableImage() {
  return nullableObject(IMAGE_PROPS);
}

function modeState() {
  return object({
    mode: choice(["shape", "home"]),
    image: nullableImage(),
    settings: object({
      pixel_size: NUMBER,
      unit: STRING,
      reference_direction_deg: nullable("number"),
      click_sigma_z: nullable("number"),
      click_sigma_m: nullable("number"),
      click_perp_ratio: NUMBER,
    }),
    pending: object({ marks: array(mark()), strokes: array(stroke()) }),
    items: array(
      object({
        item_id: STRING,
        type: choice(ITEM_TYPES),
        sarcomere_id: nullableInteger(1),
        marks: array(mark()),
        strokes: array(stroke()),
      }),
    ),
    controls: object({
      can_confirm: BOOLEAN,
      can_undo: BOOLEAN,
      can_skip_m_line: BOOLEAN,
      can_delete: BOOLEAN,
    }),
    step: nullableCode(),
    notice: nullableCode(),
    not_exported: BOOLEAN,
  });
}

function time() {
  return object({ mono_s: { type: "number", minimum: 0 } as const, wall_ms: NUMBER });
}

function studyView() {
  return object({
    item_file: nullable("string"),
    roi: nullableArray(NUMBER),
    title: nullable("string"),
    marks: array(mark()),
    strokes: array(stroke()),
    controls: object({
      can_confirm: BOOLEAN,
      can_undo: BOOLEAN,
      can_skip_m_line: BOOLEAN,
      can_next_myofibril: BOOLEAN,
      shows_branch_end: BOOLEAN,
      item_type: nullableChoice(ITEM_TYPES),
    }),
    step: nullableCode(),
    progress: object({ done: integer(0), total: integer(0) }),
    notice: nullableCode(),
    paused: BOOLEAN,
    finished: nullableObject({ file_name: STRING }),
  });
}

function studyResult() {
  return object({
    view: studyView(),
    autosave: nullable("buffer"),
    seq: integer(0),
    finished: nullableObject({ file_name: STRING, bytes: BUFFER, sha256: STRING }),
  });
}

function table() {
  return object({
    title: STRING,
    columns: array(STRING),
    rows: array(array(STRING)),
    notes: array(STRING),
  });
}

function numbers() {
  return array(nullable("number"));
}

function plotData() {
  return object({
    shape: array(
      object({
        sarcomere_id: integer(1),
        shape_index: nullable("number"),
        u_shape_index: nullable("number"),
        shape_class: STRING,
      }),
    ),
    chains: array(
      object({
        chain: integer(1),
        headings_deg: numbers(),
        turning_deg: numbers(),
        mean_heading_deg: nullable("number"),
        mean_vector_length: nullable("number"),
        reference_direction_deg: nullable("number"),
      }),
    ),
    register: array(
      object({
        pair: integer(1),
        phases_deg: numbers(),
        correlation_lags: numbers(),
        correlation_values: numbers(),
      }),
    ),
  });
}

function timed() {
  return object({ t: time() });
}

function spec<const A extends Schema, const R extends Schema>(args: A, result: R): { readonly args: A; readonly result: R } {
  return { args, result };
}

export const COMMAND_SPECS = {
  "sys.identity": spec(
    object({}),
    object({ sarcostat_version: STRING, source_digest: STRING, bridge_version: integer(1) }),
  ),
  "sys.load_scipy": spec(object({}), object({ scipy_version: STRING })),
  "doc.new": spec(object({ image: image() }), modeState()),
  "doc.load_v1": spec(object({ text: STRING, image: nullableImage(), confirm: BOOLEAN }), modeState()),
  "doc.export": spec(object({ what: choice(EXPORT_KINDS) }), object({ file_name: STRING, text: STRING })),
  "doc.set_scale": spec(object({ pixel_size: NUMBER, unit: STRING }), modeState()),
  "doc.set_click_error": spec(object({ sigma: nullable("number") }), modeState()),
  "doc.estimate_click_error": spec(object({}), modeState()),
  "doc.set_reference_direction": spec(object({ deg: nullable("number") }), modeState()),
  "shape.click": spec(object({ x: NUMBER, y: NUMBER }), modeState()),
  "shape.undo": spec(object({}), modeState()),
  "shape.skip_m_line": spec(object({}), modeState()),
  "shape.confirm": spec(object({}), modeState()),
  "shape.delete": spec(object({ item_id: STRING }), modeState()),
  "shape.clear": spec(object({}), modeState()),
  "results.get": spec(
    object({}),
    object({ sentences: array(STRING), tables: map(table()), plot_data: plotData() }),
  ),
  "study.open_kit": spec(
    object({ manifest: BUFFER, manifest_sha256: BUFFER }),
    object({
      study_id: STRING,
      annotator_id: STRING,
      session_id: STRING,
      n_items: integer(0),
      files: array(STRING),
    }),
  ),
  "study.verify_items": spec(object({ files: map(BUFFER) }), object({ ok: BOOLEAN, problems: array(code()) })),
  "study.start": spec(
    object({ autosave: nullable("buffer"), seq: nullableInteger(0), t: time() }),
    studyResult(),
  ),
  "study.confirm_identity": spec(timed(), studyResult()),
  "study.click": spec(object({ x: NUMBER, y: NUMBER, t: time() }), studyResult()),
  "study.undo": spec(timed(), studyResult()),
  "study.skip_m_line": spec(timed(), studyResult()),
  "study.next_myofibril": spec(timed(), studyResult()),
  "study.confirm_item": spec(object({ branch_end: BOOLEAN, t: time() }), studyResult()),
  "study.skip_item": spec(object({ reason: STRING, t: time() }), studyResult()),
  "study.pause": spec(timed(), studyResult()),
  "study.resume": spec(timed(), studyResult()),
} as const;

export type CommandSpecs = typeof COMMAND_SPECS;

export interface CatalogueJson {
  bridge_version: number;
  commands: Record<string, { family: string; args: Schema; result: Schema }>;
  absent_families: string[];
}

export function catalogue(bridgeVersion: number, absentFamilies: readonly string[]): CatalogueJson {
  const commands: CatalogueJson["commands"] = {};
  for (const [name, { args, result }] of Object.entries(COMMAND_SPECS)) {
    commands[name] = { family: name.split(".", 1)[0] as string, args, result };
  }
  return { bridge_version: bridgeVersion, commands, absent_families: [...absentFamilies] };
}
