import { parts, type Params } from "../strings/format.ts";

export type Child = Node | string | null | undefined | false;

export interface Props {
  readonly class?: string;
  readonly text?: string;
  readonly attrs?: Readonly<Record<string, string>>;
  readonly data?: Readonly<Record<string, string>>;
}

export function el<K extends keyof HTMLElementTagNameMap>(doc: Document, tag: K, props: Props = {}, ...children: Child[]): HTMLElementTagNameMap[K] {
  const element = doc.createElement(tag);
  if (props.class !== undefined) {
    element.className = props.class;
  }
  for (const [name, value] of Object.entries(props.attrs ?? {})) {
    element.setAttribute(name, value);
  }
  for (const [name, value] of Object.entries(props.data ?? {})) {
    element.dataset[name] = value;
  }
  if (props.text !== undefined) {
    element.textContent = props.text;
  }
  for (const child of children) {
    if (child !== null && child !== undefined && child !== false) {
      element.append(child);
    }
  }
  return element;
}

export function kbd(doc: Document, label: string, className = ""): HTMLElement {
  return el(doc, "kbd", className === "" ? { text: label } : { class: className, text: label });
}

export function richText(doc: Document, template: string, params: Params, strong: readonly string[] = []): Node[] {
  return parts(template, params).map((part) =>
    part.param !== null && strong.includes(part.param) ? el(doc, "b", { text: part.text }) : doc.createTextNode(part.text),
  );
}

export function button(doc: Document, className: string, ...children: Child[]): HTMLButtonElement {
  const out = el(doc, "button", { class: className }, ...children);
  out.type = "button";
  return out;
}

export function setDisabled(element: HTMLElement, disabled: boolean): void {
  if (disabled) {
    element.setAttribute("aria-disabled", "true");
  } else {
    element.removeAttribute("aria-disabled");
  }
}

export function isDisabled(element: Element): boolean {
  return element.getAttribute("aria-disabled") === "true";
}
