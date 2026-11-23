export const SVG_NS = "http://www.w3.org/2000/svg";

export type SvgAttrs = Readonly<Record<string, string | number>>;

export function svgEl<K extends keyof SVGElementTagNameMap>(doc: Document, tag: K, attrs: SvgAttrs = {}, text: string | null = null): SVGElementTagNameMap[K] {
  const element = doc.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attrs)) {
    element.setAttribute(name, typeof value === "number" ? String(round(value)) : value);
  }
  if (text !== null) {
    element.textContent = text;
  }
  return element;
}

export function round(value: number): number {
  return Math.round(value * 100) / 100;
}

export interface Ticks {
  readonly step: number;
  readonly decimals: number;
  readonly values: readonly number[];
}

const NICE = [1, 2, 2.5, 5, 10];

export function niceTicks(low: number, high: number, target = 5): Ticks {
  if (!(Number.isFinite(low) && Number.isFinite(high)) || high <= low) {
    throw new RangeError(`niceTicks needs finite low < high, got ${low} and ${high}`);
  }
  const raw = (high - low) / Math.max(1, target);
  const power = 10 ** Math.floor(Math.log10(raw));
  const factor = NICE.find((candidate) => candidate * power >= raw * (1 - 1e-9)) ?? 10;
  const step = factor * power;
  const decimals = Math.max(0, -Math.floor(Math.log10(step) + 1e-9) + (factor === 2.5 ? 1 : 0));
  const first = Math.ceil(low / step - 1e-9);
  const last = Math.floor(high / step + 1e-9);
  const values: number[] = [];
  for (let k = first; k <= last; k += 1) {
    values.push(Number((k * step).toFixed(decimals + 2)));
  }
  return { step, decimals, values };
}

export function formatTick(value: number, decimals: number): string {
  const text = value.toFixed(decimals);
  return /^-0(\.0+)?$/.test(text) ? text.slice(1) : text;
}

export function scaleLinear(domain: readonly [number, number], range: readonly [number, number]): (value: number) => number {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  const span = d1 - d0;
  return (value) => r0 + ((value - d0) / span) * (r1 - r0);
}
