export const GUARD_MESSAGE = "SarcoStat worker guard: refused a cross-origin";
export const GUARDED_CONSTRUCTORS = ["WebSocket", "EventSource", "Worker", "WebTransport"] as const;

export interface GuardScope {
  location: { href: string; origin: string };
  fetch?: unknown;
  XMLHttpRequest?: unknown;
  WebSocket?: unknown;
  EventSource?: unknown;
  Worker?: unknown;
  WebTransport?: unknown;
}

type AnyFunction = (...args: unknown[]) => unknown;
type AnyConstructor = new (...args: unknown[]) => object;

export function targetUrl(input: unknown): string {
  if (typeof input === "string") {
    return input;
  }
  if (input instanceof URL) {
    return input.href;
  }
  if (typeof input === "object" && input !== null && "url" in input && typeof input.url === "string") {
    return input.url;
  }
  return String(input);
}

export function isSameOrigin(input: unknown, base: { href: string; origin: string }): boolean {
  let resolved: URL;
  try {
    resolved = new URL(targetUrl(input), base.href);
  } catch {
    return false;
  }
  return resolved.origin === base.origin;
}

function refusal(channel: string, input: unknown, base: { href: string }): TypeError {
  let shown: string;
  try {
    shown = new URL(targetUrl(input), base.href).href;
  } catch {
    shown = targetUrl(input);
  }
  return new TypeError(`${GUARD_MESSAGE} ${channel} to ${shown}`);
}

function pin(owner: object, name: string, value: unknown): void {
  Object.defineProperty(owner, name, { value, writable: false, configurable: false, enumerable: true });
}

export function installGuard(scope: GuardScope): void {
  const base = { href: scope.location.href, origin: scope.location.origin };
  const originalFetch = scope.fetch;
  if (typeof originalFetch === "function") {
    const fetch = (originalFetch as AnyFunction).bind(scope);
    pin(scope, "fetch", function guardedFetch(input: unknown, init?: unknown): unknown {
      if (!isSameOrigin(input, base)) {
        return Promise.reject(refusal("fetch", input, base));
      }
      return fetch(input, init);
    });
  }
  const Xhr = scope.XMLHttpRequest;
  if (typeof Xhr === "function" && typeof Xhr.prototype?.open === "function") {
    const open = Xhr.prototype.open as AnyFunction;
    pin(Xhr.prototype, "open", function guardedOpen(this: unknown, method: unknown, url: unknown, ...rest: unknown[]): unknown {
      if (!isSameOrigin(url, base)) {
        throw refusal("XMLHttpRequest", url, base);
      }
      return open.call(this, method, url, ...rest);
    });
  }
  for (const name of GUARDED_CONSTRUCTORS) {
    const Original = scope[name];
    if (typeof Original !== "function") {
      continue;
    }
    const guarded: AnyConstructor = new Proxy(Original as AnyConstructor, {
      construct(target, args, newTarget): object {
        if (!isSameOrigin(args[0], base)) {
          throw refusal(name, args[0], base);
        }
        return Reflect.construct(target, args, newTarget === guarded ? target : newTarget) as object;
      },
    });
    pin(scope, name, guarded);
  }
}

if ("WorkerGlobalScope" in globalThis) {
  installGuard(globalThis as unknown as GuardScope);
}
