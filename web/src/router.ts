export const ROUTES = ["home", "shape", "straightness", "register", "planner", "study"] as const;
export const RAIL_ROUTES = ["home", "shape", "straightness", "register", "planner"] as const;

export type Route = (typeof ROUTES)[number];
export type RailRoute = (typeof RAIL_ROUTES)[number];

export const MODE_KEYS: Readonly<Record<string, RailRoute>> = {
  "0": "home",
  "1": "shape",
  "2": "straightness",
  "3": "register",
  "4": "planner",
};

export const ROUTE_FAMILY: Readonly<Partial<Record<Route, string>>> = {
  shape: "shape",
  straightness: "chain",
  register: "register",
  planner: "planner",
  study: "study",
};

export type RedirectReason = "unknown" | "absent" | "study";

export type Resolution =
  | { readonly kind: "show"; readonly route: Route }
  | { readonly kind: "redirect"; readonly route: "home"; readonly reason: RedirectReason; readonly from: Route | null };

export interface Arrival {
  readonly reason: RedirectReason | null;
  readonly from: Route | null;
}

export function isRoute(value: string): value is Route {
  return (ROUTES as readonly string[]).includes(value);
}

export function routeFromHash(hash: string): Route | null {
  const body = hash.startsWith("#") ? hash.slice(1) : hash;
  if (body === "" || body === "/") {
    return "home";
  }
  if (!body.startsWith("/")) {
    return null;
  }
  const name = body.slice(1).replace(/\/$/, "");
  return isRoute(name) ? name : null;
}

export function hashOf(route: Route): string {
  return route === "home" ? "#/" : `#/${route}`;
}

export function keyOf(route: RailRoute): string {
  return Object.entries(MODE_KEYS).find(([, value]) => value === route)?.[0] ?? "";
}

export function isAbsent(route: Route, absentFamilies: readonly string[]): boolean {
  const family = ROUTE_FAMILY[route];
  return family !== undefined && absentFamilies.includes(family);
}

export function resolveArrival(raw: Route | null, absentFamilies: readonly string[]): Resolution {
  if (raw === null) {
    return { kind: "redirect", route: "home", reason: "unknown", from: null };
  }
  if (isAbsent(raw, absentFamilies)) {
    return { kind: "redirect", route: "home", reason: "absent", from: raw };
  }
  if (raw === "study") {
    return { kind: "redirect", route: "home", reason: "study", from: raw };
  }
  return { kind: "show", route: raw };
}

export interface HashHost {
  readHash(): string;
  pushHash(hash: string): void;
  replaceHash(hash: string): void;
  onHashChange(listener: () => void): void;
}

export interface RouterHooks {
  readonly absentFamilies: readonly string[];
  beforeLeave(from: Route, to: Route): Promise<boolean>;
  show(route: Route, arrival: Arrival): void;
  absent(route: Route): void;
}

export interface GoOptions {
  readonly fromStudyCard?: boolean;
  readonly arrival?: Arrival;
}

const NO_ARRIVAL: Arrival = { reason: null, from: null };

export class Router {
  private readonly host: HashHost;
  private readonly hooks: RouterHooks;
  private currentRoute: Route | null = null;
  private approved: { route: Route; arrival: Arrival } | null = null;
  private deciding = false;

  constructor(host: HashHost, hooks: RouterHooks) {
    this.host = host;
    this.hooks = hooks;
  }

  get current(): Route | null {
    return this.currentRoute;
  }

  get busy(): boolean {
    return this.deciding;
  }

  start(): void {
    this.host.onHashChange(() => void this.onHashChange());
    const resolution = resolveArrival(routeFromHash(this.host.readHash()), this.hooks.absentFamilies);
    if (resolution.kind === "redirect") {
      this.host.replaceHash(hashOf("home"));
      this.commit("home", { reason: resolution.reason, from: resolution.from });
      return;
    }
    this.commit(resolution.route, NO_ARRIVAL);
  }

  async go(to: Route, options: GoOptions = {}): Promise<boolean> {
    if (this.deciding || this.currentRoute === null) {
      return false;
    }
    if (isAbsent(to, this.hooks.absentFamilies)) {
      this.hooks.absent(to);
      return false;
    }
    if (to === "study" && options.fromStudyCard !== true) {
      return this.go("home", { arrival: { reason: "study", from: "study" } });
    }
    const arrival = options.arrival ?? NO_ARRIVAL;
    if (to === this.currentRoute) {
      if (arrival.reason !== null) {
        this.commit(to, arrival);
      }
      return true;
    }
    if (!(await this.decide(this.currentRoute, to))) {
      return false;
    }
    const hash = hashOf(to);
    if (this.host.readHash() === hash) {
      this.commit(to, arrival);
      return true;
    }
    this.approved = { route: to, arrival };
    this.host.pushHash(hash);
    return true;
  }

  private async decide(from: Route, to: Route): Promise<boolean> {
    this.deciding = true;
    try {
      return await this.hooks.beforeLeave(from, to);
    } finally {
      this.deciding = false;
    }
  }

  private commit(route: Route, arrival: Arrival): void {
    this.currentRoute = route;
    this.hooks.show(route, arrival);
  }

  private async onHashChange(): Promise<void> {
    const raw = routeFromHash(this.host.readHash());
    const approved = this.approved;
    this.approved = null;
    if (approved !== null && raw === approved.route) {
      this.commit(approved.route, approved.arrival);
      return;
    }
    const from = this.currentRoute;
    if (from === null) {
      return;
    }
    if (this.deciding) {
      this.host.replaceHash(hashOf(from));
      return;
    }
    const resolution = resolveArrival(raw, this.hooks.absentFamilies);
    const target = resolution.route;
    const arrival: Arrival = resolution.kind === "redirect" ? { reason: resolution.reason, from: resolution.from } : NO_ARRIVAL;
    if (target === from) {
      if (raw !== from) {
        this.host.replaceHash(hashOf(from));
      }
      if (arrival.reason !== null) {
        this.commit(from, arrival);
      }
      return;
    }
    this.host.replaceHash(hashOf(from));
    if (!(await this.decide(from, target))) {
      return;
    }
    this.host.replaceHash(hashOf(target));
    this.commit(target, arrival);
  }
}
