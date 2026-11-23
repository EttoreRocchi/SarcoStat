import { MODE_KEYS, type RailRoute, type Route } from "../../router.ts";

export type TargetKind = "text" | "control" | "other";

export type ViewKeyAction = "zoom-in" | "zoom-out" | "fit" | "layers" | "annotate" | "pan-start" | "pan-end";

export type StudyKeyAction = "undo" | "m-line" | "skip" | "pause" | "next" | "next-myofibril" | "zoom-in" | "zoom-out" | "fit" | "pan-start" | "pan-end";

export type ShortcutAction =
  | { readonly kind: "mode"; readonly route: RailRoute }
  | { readonly kind: "open" }
  | { readonly kind: "export" }
  | { readonly kind: "sheet" }
  | { readonly kind: "undo" }
  | { readonly kind: "discard" }
  | { readonly kind: "confirm" }
  | { readonly kind: "item"; readonly step: -1 | 1 }
  | { readonly kind: "view"; readonly action: ViewKeyAction }
  | { readonly kind: "study"; readonly action: StudyKeyAction };

export interface KeyInput {
  readonly key: string;
  readonly ctrlKey: boolean;
  readonly metaKey: boolean;
  readonly altKey: boolean;
  readonly repeat: boolean;
  readonly isComposing: boolean;
  readonly target: TargetKind;
}

export interface KeyContext {
  readonly route: Route | null;
  readonly imageOpen: boolean;
}

const TEXT_INPUT_TYPES = new Set(["text", "search", "email", "number", "password", "tel", "url", "date", "datetime-local", "month", "time", "week"]);
const CONTROL_ROLES = new Set(["button", "tab", "menuitem", "link", "checkbox", "radio", "switch", "option"]);

export function targetKind(target: EventTarget | null): TargetKind {
  if (target === null || typeof (target as Partial<Element>).closest !== "function") {
    return "other";
  }
  const element = target as Element;
  if (element.closest("textarea, select, [contenteditable=''], [contenteditable='true'], [role='textbox'], [role='combobox'], [role='spinbutton']") !== null) {
    return "text";
  }
  if (element.tagName === "INPUT") {
    const type = (element.getAttribute("type") ?? "text").toLowerCase();
    return TEXT_INPUT_TYPES.has(type) ? "text" : "control";
  }
  if (element.closest("button, a[href], summary") !== null) {
    return "control";
  }
  const role = element.getAttribute("role");
  return role !== null && CONTROL_ROLES.has(role) ? "control" : "other";
}

const ZOOM_IN_KEYS = new Set(["+", "="]);
const ZOOM_OUT_KEYS = new Set(["-", "_", "\u2212"]);
const SPACE = " ";

function zoomAction(key: string): "zoom-in" | "zoom-out" | null {
  if (ZOOM_IN_KEYS.has(key)) {
    return "zoom-in";
  }
  return ZOOM_OUT_KEYS.has(key) ? "zoom-out" : null;
}

function upper(key: string): string {
  return key.length === 1 ? key.toUpperCase() : key;
}

function studyKey(input: KeyInput): ShortcutAction | null {
  const key = input.key;
  const zoom = zoomAction(key);
  if (zoom !== null) {
    return { kind: "study", action: zoom };
  }
  if (key === SPACE) {
    return input.target === "control" ? null : { kind: "study", action: "pan-start" };
  }
  if (input.repeat) {
    return null;
  }
  if (key === "Backspace") {
    return { kind: "study", action: "undo" };
  }
  if (key === "Enter") {
    return input.target === "control" ? null : { kind: "study", action: "next" };
  }
  const action = ({ M: "m-line", S: "skip", P: "pause", N: "next-myofibril", F: "fit" } as const)[upper(key) as "M" | "S" | "P" | "N" | "F"];
  return action === undefined ? null : { kind: "study", action };
}

function viewKey(input: KeyInput): ShortcutAction | null {
  const key = input.key;
  const zoom = zoomAction(key);
  if (zoom !== null) {
    return { kind: "view", action: zoom };
  }
  if (key === SPACE) {
    return input.target === "control" ? null : { kind: "view", action: "pan-start" };
  }
  if (input.repeat) {
    return null;
  }
  const action = ({ F: "fit", L: "layers", A: "annotate" } as const)[upper(key) as "F" | "L" | "A"];
  return action === undefined ? null : { kind: "view", action };
}

export function resolveKey(input: KeyInput, context: KeyContext): ShortcutAction | null {
  if (input.isComposing || input.ctrlKey || input.metaKey || input.altKey || input.target === "text") {
    return null;
  }
  const key = input.key;
  if (key === "?") {
    return input.repeat ? null : { kind: "sheet" };
  }
  if (context.route === null) {
    return null;
  }
  if (context.route === "study") {
    return studyKey(input);
  }
  if (!input.repeat) {
    const mode = MODE_KEYS[key];
    if (mode !== undefined) {
      return { kind: "mode", route: mode };
    }
    if (key === "o" || key === "O") {
      return { kind: "open" };
    }
  }
  if (context.route !== "shape") {
    return null;
  }
  if ((key === "e" || key === "E") && !input.repeat) {
    return { kind: "export" };
  }
  if (!context.imageOpen) {
    return null;
  }
  const view = viewKey(input);
  if (view !== null || input.repeat) {
    return view;
  }
  if (key === "Backspace") {
    return { kind: "undo" };
  }
  if (key === "Escape") {
    return { kind: "discard" };
  }
  if (key === "Enter" && input.target !== "control") {
    return { kind: "confirm" };
  }
  if (key === "[" || key === "]") {
    return { kind: "item", step: key === "[" ? -1 : 1 };
  }
  return null;
}

export function resolveKeyUp(input: KeyInput, context: KeyContext): ShortcutAction | null {
  if (input.key !== SPACE) {
    return null;
  }
  if (context.route === "study") {
    return { kind: "study", action: "pan-end" };
  }
  return context.route === "shape" ? { kind: "view", action: "pan-end" } : null;
}

export function keyInput(event: KeyboardEvent): KeyInput {
  return {
    key: event.key,
    ctrlKey: event.ctrlKey,
    metaKey: event.metaKey,
    altKey: event.altKey,
    repeat: event.repeat,
    isComposing: event.isComposing,
    target: targetKind(event.target),
  };
}
