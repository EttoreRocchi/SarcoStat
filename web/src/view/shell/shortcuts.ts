import { MODE_KEYS, type RailRoute, type Route } from "../../router.ts";

export type TargetKind = "text" | "control" | "other";

export type ShortcutAction =
  | { readonly kind: "mode"; readonly route: RailRoute }
  | { readonly kind: "open" }
  | { readonly kind: "export" }
  | { readonly kind: "sheet" }
  | { readonly kind: "undo" }
  | { readonly kind: "discard" }
  | { readonly kind: "confirm" };

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

export function resolveKey(input: KeyInput, context: KeyContext): ShortcutAction | null {
  if (input.isComposing || input.ctrlKey || input.metaKey || input.altKey || input.repeat || input.target === "text") {
    return null;
  }
  const key = input.key;
  if (key === "?") {
    return { kind: "sheet" };
  }
  if (context.route === null || context.route === "study") {
    return null;
  }
  const mode = MODE_KEYS[key];
  if (mode !== undefined) {
    return { kind: "mode", route: mode };
  }
  if (key === "o" || key === "O") {
    return { kind: "open" };
  }
  if (context.route !== "shape") {
    return null;
  }
  if (key === "e" || key === "E") {
    return { kind: "export" };
  }
  if (!context.imageOpen) {
    return null;
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
  return null;
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
