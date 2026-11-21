import { describe, expect, it } from "vitest";

import { hashOf, isAbsent, keyOf, MODE_KEYS, RAIL_ROUTES, resolveArrival, routeFromHash, Router, type Arrival, type HashHost, type Route } from "../../src/router.ts";
import { ABSENT_FAMILIES } from "../../src/worker/protocol.ts";

class FakeHost implements HashHost {
  hash: string;
  readonly pushed: string[] = [];
  readonly replaced: string[] = [];
  private listener: (() => void) | null = null;

  constructor(hash: string) {
    this.hash = hash;
  }

  readHash(): string {
    return this.hash;
  }

  pushHash(hash: string): void {
    this.pushed.push(hash);
    this.hash = hash;
    queueMicrotask(() => this.listener?.());
  }

  replaceHash(hash: string): void {
    this.replaced.push(hash);
    this.hash = hash;
  }

  onHashChange(listener: () => void): void {
    this.listener = listener;
  }

  async userNavigates(hash: string): Promise<void> {
    this.hash = hash;
    this.listener?.();
    await settle();
  }
}

async function settle(): Promise<void> {
  for (let i = 0; i < 5; i += 1) {
    await Promise.resolve();
  }
}

interface Harness {
  host: FakeHost;
  router: Router;
  shown: [Route, Arrival][];
  absent: Route[];
  asked: [Route, Route][];
  answer: { value: boolean };
}

function harness(hash: string, decide?: () => Promise<boolean>): Harness {
  const host = new FakeHost(hash);
  const shown: [Route, Arrival][] = [];
  const absent: Route[] = [];
  const asked: [Route, Route][] = [];
  const answer = { value: true };
  const router = new Router(host, {
    absentFamilies: ABSENT_FAMILIES,
    beforeLeave: async (from, to) => {
      asked.push([from, to]);
      return decide === undefined ? answer.value : decide();
    },
    show: (route, arrival) => shown.push([route, arrival]),
    absent: (route) => absent.push(route),
  });
  router.start();
  return { host, router, shown, absent, asked, answer };
}

describe("hash routes", () => {
  it("parse the six routes and the empty hash", () => {
    expect(routeFromHash("")).toBe("home");
    expect(routeFromHash("#")).toBe("home");
    expect(routeFromHash("#/")).toBe("home");
    for (const route of ["home", "shape", "straightness", "register", "planner", "study"] as const) {
      expect(routeFromHash(hashOf(route))).toBe(route);
    }
    expect(routeFromHash("#/shape/")).toBe("shape");
    expect(routeFromHash("#/nowhere")).toBeNull();
    expect(routeFromHash("#shape")).toBeNull();
    expect(hashOf("home")).toBe("#/");
    expect(hashOf("planner")).toBe("#/planner");
  });

  it("map keys 0 to 4 to the five rail entries in order", () => {
    expect(Object.keys(MODE_KEYS)).toEqual(["0", "1", "2", "3", "4"]);
    expect(RAIL_ROUTES.map(keyOf)).toEqual(["0", "1", "2", "3", "4"]);
    expect(Object.values(MODE_KEYS)).toEqual([...RAIL_ROUTES]);
  });

  it("treat the catalogue's absent families as absent modes", () => {
    expect(ABSENT_FAMILIES).toEqual(["chain", "register", "planner"]);
    expect(isAbsent("straightness", ABSENT_FAMILIES)).toBe(true);
    expect(isAbsent("register", ABSENT_FAMILIES)).toBe(true);
    expect(isAbsent("planner", ABSENT_FAMILIES)).toBe(true);
    expect(isAbsent("shape", ABSENT_FAMILIES)).toBe(false);
    expect(isAbsent("home", ABSENT_FAMILIES)).toBe(false);
    expect(isAbsent("straightness", [])).toBe(false);
  });

  it("send direct arrivals at unknown, absent and study routes to Home", () => {
    expect(resolveArrival(null, ABSENT_FAMILIES)).toEqual({ kind: "redirect", route: "home", reason: "unknown", from: null });
    expect(resolveArrival("register", ABSENT_FAMILIES)).toEqual({ kind: "redirect", route: "home", reason: "absent", from: "register" });
    expect(resolveArrival("study", ABSENT_FAMILIES)).toEqual({ kind: "redirect", route: "home", reason: "study", from: "study" });
    expect(resolveArrival("shape", ABSENT_FAMILIES)).toEqual({ kind: "show", route: "shape" });
  });
});

describe("Router", () => {
  it("loads #/study as Home with the Study card pointed out, changing only the fragment", () => {
    const h = harness("#/study");
    expect(h.host.replaced).toEqual(["#/"]);
    expect(h.shown).toEqual([["home", { reason: "study", from: "study" }]]);
    expect(h.router.current).toBe("home");
  });

  it("loads an absent mode as Home and names it", () => {
    const h = harness("#/straightness");
    expect(h.host.replaced).toEqual(["#/"]);
    expect(h.shown).toEqual([["home", { reason: "absent", from: "straightness" }]]);
  });

  it("switches modes through the guard and pushes one history entry", async () => {
    const h = harness("");
    expect(await h.router.go("shape")).toBe(true);
    await settle();
    expect(h.asked).toEqual([["home", "shape"]]);
    expect(h.host.pushed).toEqual(["#/shape"]);
    expect(h.shown.at(-1)).toEqual(["shape", { reason: null, from: null }]);
    expect(h.router.current).toBe("shape");
  });

  it("stays when the guard answers Stay", async () => {
    const h = harness("#/shape");
    h.answer.value = false;
    expect(await h.router.go("home")).toBe(false);
    await settle();
    expect(h.host.pushed).toEqual([]);
    expect(h.router.current).toBe("shape");
  });

  it("never navigates to an absent mode and reports it once", async () => {
    const h = harness("#/shape");
    expect(await h.router.go("register")).toBe(false);
    expect(h.absent).toEqual(["register"]);
    expect(h.asked).toEqual([]);
    expect(h.host.pushed).toEqual([]);
  });

  it("enters Study only from the Home card; any other way lands on Home", async () => {
    const h = harness("#/shape");
    expect(await h.router.go("study")).toBe(true);
    await settle();
    expect(h.router.current).toBe("home");
    expect(h.shown.at(-1)).toEqual(["home", { reason: "study", from: "study" }]);
    expect(await h.router.go("study", { fromStudyCard: true })).toBe(true);
    await settle();
    expect(h.router.current).toBe("study");
    expect(h.host.hash).toBe("#/study");
    await h.host.userNavigates("#/");
    expect(h.router.current).toBe("home");
    await h.host.userNavigates("#/study");
    expect(h.router.current).toBe("home");
    expect(h.host.hash).toBe("#/");
    expect(h.shown.at(-1)).toEqual(["home", { reason: "study", from: "study" }]);
  });

  it("puts the fragment back while the guard decides on a Back or typed navigation", async () => {
    let release: (value: boolean) => void = () => undefined;
    const h = harness("#/shape", () => new Promise<boolean>((resolve) => {
      release = resolve;
    }));
    await h.host.userNavigates("#/");
    expect(h.host.replaced.at(-1)).toBe("#/shape");
    expect(h.host.hash).toBe("#/shape");
    expect(h.router.busy).toBe(true);
    expect(h.router.current).toBe("shape");
    release(true);
    await settle();
    expect(h.host.hash).toBe("#/");
    expect(h.host.pushed).toEqual([]);
    expect(h.router.current).toBe("home");
  });
});
