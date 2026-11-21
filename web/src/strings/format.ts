export type Params = Readonly<Record<string, string | number | boolean>>;

const PLACEHOLDER = /\{([a-z_]+)\}/g;

export function fill(template: string, params: Params = {}): string {
  return template.replace(PLACEHOLDER, (whole, name: string) => (name in params ? String(params[name]) : whole));
}

export interface Part {
  readonly text: string;
  readonly param: string | null;
}

export function parts(template: string, params: Params = {}): Part[] {
  const out: Part[] = [];
  let last = 0;
  for (const match of template.matchAll(PLACEHOLDER)) {
    const at = match.index ?? 0;
    if (at > last) {
      out.push({ text: template.slice(last, at), param: null });
    }
    const name = match[1] ?? "";
    out.push(name in params ? { text: String(params[name]), param: name } : { text: match[0], param: null });
    last = at + match[0].length;
  }
  if (last < template.length) {
    out.push({ text: template.slice(last), param: null });
  }
  return out;
}

export function plural(n: number, one: string, other: string, params: Params = {}): string {
  return fill(n === 1 ? one : other, { n, ...params });
}

export function placeholders(template: string): string[] {
  return [...template.matchAll(PLACEHOLDER)].map((match) => match[1] ?? "");
}
