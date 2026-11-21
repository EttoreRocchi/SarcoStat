import type { Keyboard } from "./keyboard.ts";

const FOCUSABLE = 'button:not([disabled]), a[href], input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export interface ModalOptions {
  readonly layer: HTMLElement;
  readonly dialog: HTMLElement;
  readonly keyboard: Keyboard;
  readonly background: () => readonly HTMLElement[];
  readonly onKey: (event: KeyboardEvent) => boolean;
  readonly onOutside?: () => void;
}

export function focusables(container: HTMLElement): HTMLElement[] {
  return [...container.querySelectorAll(FOCUSABLE)].filter(
    (element): element is HTMLElement => element instanceof HTMLElement && !element.hidden && element.closest("[hidden]") === null,
  );
}

export function trapTab(container: HTMLElement, event: KeyboardEvent): boolean {
  if (event.key !== "Tab") {
    return false;
  }
  const items = focusables(container);
  if (items.length === 0) {
    event.preventDefault();
    return true;
  }
  const doc = container.ownerDocument;
  const at = items.indexOf(doc.activeElement as HTMLElement);
  const next = event.shiftKey ? (at <= 0 ? items.length - 1 : at - 1) : at < 0 || at === items.length - 1 ? 0 : at + 1;
  event.preventDefault();
  items[next]?.focus();
  return true;
}

export class Modal {
  private readonly options: ModalOptions;
  private release: (() => void) | null = null;
  private restore: HTMLElement | null = null;
  private inerted: HTMLElement[] = [];
  private outside: ((event: PointerEvent) => void) | null = null;

  constructor(options: ModalOptions) {
    this.options = options;
  }

  get isOpen(): boolean {
    return this.release !== null;
  }

  open(focus: HTMLElement | null): void {
    if (this.isOpen) {
      return;
    }
    const { layer, dialog, keyboard } = this.options;
    const doc = layer.ownerDocument;
    this.restore = doc.activeElement instanceof HTMLElement ? doc.activeElement : null;
    this.inerted = this.options.background().filter((element) => !element.inert);
    for (const element of this.inerted) {
      element.inert = true;
    }
    layer.hidden = false;
    this.release = keyboard.push((event) => {
      if (trapTab(dialog, event)) {
        return;
      }
      if (this.options.onKey(event)) {
        event.preventDefault();
        event.stopPropagation();
      }
    });
    const onOutside = this.options.onOutside;
    if (onOutside !== undefined) {
      this.outside = (event) => {
        if (!dialog.contains(event.target as Node)) {
          onOutside();
        }
      };
      doc.addEventListener("pointerdown", this.outside, true);
    }
    (focus ?? focusables(dialog)[0] ?? dialog).focus();
  }

  close(restoreFocus = true): void {
    if (!this.isOpen) {
      return;
    }
    const { layer } = this.options;
    const doc = layer.ownerDocument;
    this.release?.();
    this.release = null;
    if (this.outside !== null) {
      doc.removeEventListener("pointerdown", this.outside, true);
      this.outside = null;
    }
    layer.hidden = true;
    for (const element of this.inerted) {
      element.inert = false;
    }
    this.inerted = [];
    const target = this.restore;
    this.restore = null;
    if (restoreFocus && target !== null && target.isConnected && !target.closest("[inert]")) {
      target.focus();
    }
  }
}
