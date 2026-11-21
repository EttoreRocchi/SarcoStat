import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { WEB_DIR } from "../../scripts/lane.mjs";

const STYLES_DIR = path.join(WEB_DIR, "src", "styles");
const ICONS = path.join(WEB_DIR, "src", "assets", "icons.svg");

const DESIGN_COLOURS: Record<string, string> = {
  "bg-0": "#080D12",
  "bg-1": "#0D141B",
  "bg-2": "#131D26",
  "bg-3": "#1A2632",
  "bg-4": "#22313F",
  line: "#1E2B37",
  "line-2": "#2B3C4C",
  stage: "#141414",
  "tx-1": "#E8EEF3",
  "tx-2": "#AAB8C4",
  "tx-3": "#8494A3",
  brand: "#40A0D0",
  "brand-ink": "#03121C",
  "m-home": "#40A0D0",
  "m-shape": "#56B4E9",
  "m-straight": "#2CC79F",
  "m-register": "#E59CC7",
  "m-planner": "#EDAF3C",
  "m-study": "#A9C7DD",
  ok: "#5DD3A9",
  danger: "#FF9B8A",
  unsaved: "#EDAF3C",
  "lm-z": "#56B4E9",
  "lm-m": "#E69F00",
  "lm-chain": "#17C495",
  "lm-f1": "#56B4E9",
  "lm-f2": "#F58A45",
  "lm-link": "#F0E442",
  "lm-target": "#FFFFFF",
};

const DESIGN_SCALES: Record<string, string> = {
  halo: "rgba(0,0,0,.62)",
  "sp-1": "4px",
  "sp-2": "8px",
  "sp-3": "12px",
  "sp-4": "16px",
  "sp-5": "24px",
  "sp-6": "32px",
  "sp-7": "48px",
  "r-1": "3px",
  "r-2": "5px",
  "r-3": "8px",
  "r-4": "10px",
  "dur-1": "120ms",
  "dur-2": "180ms",
  "dur-3": "240ms",
  ease: "cubic-bezier(.2,.7,.2,1)",
  "focus-width": "2px",
  "statusline-h": "28px",
};

const TYPE_SCALE = ["10.5px", "11.5px", "12.5px", "13.5px", "14px", "16px", "19px", "22px", "34px", "44px"];

const MODES: Record<string, string> = {
  home: "m-home",
  shape: "m-shape",
  straightness: "m-straight",
  register: "m-register",
  planner: "m-planner",
  study: "m-study",
};

const SHELL_CLASSES = ["statusline", "statusline__item", "statusline__hash", "fatal", "fatal__title", "fatal__body", "fatal__action"];

const COMPONENT_CLASSES = [
  "btn",
  "btn--primary",
  "btn--secondary",
  "btn--ghost",
  "key",
  "lm-chip",
  "lm-chip--done",
  "lm-chip--now",
  "lm-chip--todo",
  "card",
  "mode-card",
  "tabs",
  "tabs__tab",
  "scrim",
  "dialog",
  "menu",
  "menu__item",
  "rail",
  "rail__item",
  "topbar",
  "hud",
  "tooltip",
];

const ICON_IDS = [
  "mark", "i-home", "i-shape", "i-straight", "i-register", "i-planner", "i-study", "i-cross", "i-hand", "i-zoom-in", "i-zoom-out",
  "i-fit", "i-contrast", "i-undo", "i-download", "i-folder", "i-keyboard", "i-pause", "i-skip", "i-help", "i-check", "i-chev-l",
  "i-chev-r", "i-chev-d", "i-layers", "i-warn", "i-lock", "i-image", "i-file", "i-plot", "i-x", "i-dot",
];

interface Rule {
  prelude: string;
  body: string;
  media: string;
}

function read(name: string): string {
  return fs.readFileSync(path.join(STYLES_DIR, name), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
}

function rules(css: string, media = ""): Rule[] {
  const out: Rule[] = [];
  let i = 0;
  while (i < css.length) {
    const open = css.indexOf("{", i);
    if (open < 0) {
      break;
    }
    const prelude = css.slice(i, open).trim();
    let depth = 1;
    let j = open + 1;
    while (j < css.length && depth > 0) {
      if (css[j] === "{") {
        depth += 1;
      } else if (css[j] === "}") {
        depth -= 1;
      }
      j += 1;
    }
    const body = css.slice(open + 1, j - 1);
    if (prelude.startsWith("@media") || prelude.startsWith("@supports")) {
      out.push(...rules(body, prelude));
    } else {
      out.push({ prelude, body, media });
    }
    i = j;
  }
  return out;
}

function declarations(body: string): Map<string, string> {
  const out = new Map<string, string>();
  for (const decl of body.split(";")) {
    const colon = decl.indexOf(":");
    if (colon > 0) {
      out.set(decl.slice(0, colon).trim(), decl.slice(colon + 1).trim());
    }
  }
  return out;
}

function rootTokens(media: string): Map<string, string> {
  const out = new Map<string, string>();
  for (const rule of rules(read("tokens.css"))) {
    if (rule.media === media && rule.prelude === ":root") {
      for (const [name, value] of declarations(rule.body)) {
        if (name.startsWith("--")) {
          out.set(name.slice(2), value);
        }
      }
    }
  }
  return out;
}

function compact(value: string): string {
  return value.replace(/\s+/g, "").replace(/(?<![\d.])0\./g, ".").toLowerCase();
}

function classSelectors(css: string): Set<string> {
  const out = new Set<string>();
  for (const rule of rules(css)) {
    if (rule.prelude.startsWith("@")) {
      continue;
    }
    for (const m of rule.prelude.matchAll(/\.([A-Za-z_][\w-]*)/g)) {
      out.add(m[1] ?? "");
    }
  }
  return out;
}

const REDUCED = "@media (prefers-reduced-motion: reduce)";

describe("tokens.css", () => {
  const tokens = rootTokens("");

  it("carries every colour token of DESIGN.md direction A with its value", () => {
    for (const [name, value] of Object.entries(DESIGN_COLOURS)) {
      expect(tokens.get(name)?.toUpperCase(), `--${name}`).toBe(value);
    }
  });

  it("carries the halo, spacing, radii, motion and focus tokens", () => {
    for (const [name, value] of Object.entries(DESIGN_SCALES)) {
      expect(compact(tokens.get(name) ?? ""), `--${name}`).toBe(compact(value));
    }
    for (const name of ["e-1", "e-2", "e-3"]) {
      expect(tokens.get(name), `--${name}`).toBeTruthy();
    }
    expect(compact(tokens.get("focus-ring") ?? "")).toBe(compact("var(--focus-width) solid var(--brand)"));
  });

  it("has the type scale of DESIGN.md", () => {
    const sizes = [...tokens].filter(([name]) => name.startsWith("fs-")).map(([, value]) => value);
    expect(sizes).toEqual(TYPE_SCALE);
  });

  it("names the three design families first in the font stacks", () => {
    expect(tokens.get("f-display")).toMatch(/^"Barlow Semi Condensed",/);
    expect(tokens.get("f-body")).toMatch(/^"Atkinson Hyperlegible Next",/);
    expect(tokens.get("f-num")).toMatch(/^"Atkinson Hyperlegible Mono",/);
  });

  it("sets every duration and delay to 0 under prefers-reduced-motion", () => {
    const timing = [...tokens.keys()].filter((name) => name.startsWith("dur-") || name.startsWith("delay-"));
    expect(timing.length).toBeGreaterThanOrEqual(4);
    const reduced = rootTokens(REDUCED);
    for (const name of timing) {
      expect(reduced.get(name), `--${name}`).toMatch(/^0m?s$/);
    }
  });
});

describe("motion in the stylesheets", () => {
  it("uses the duration tokens only, never a literal duration", () => {
    for (const file of ["base.css", "components.css", "modes.css"]) {
      for (const rule of rules(read(file))) {
        for (const [name, value] of declarations(rule.body)) {
          if (/^(transition|animation)/.test(name) && rule.media !== REDUCED) {
            const literal = value.replace(/var\([^)]*\)/g, "").match(/(?<![\w.-])\d*\.?\d+m?s\b/g) ?? [];
            expect(literal.filter((t) => !/^0m?s$/.test(t)), `${file} ${rule.prelude} ${name}`).toEqual([]);
          }
        }
      }
    }
  });

  it("zeroes every transition and animation under prefers-reduced-motion in base.css", () => {
    const reduced = rules(read("base.css")).filter((rule) => rule.media === REDUCED);
    const decls = new Map(reduced.flatMap((rule) => [...declarations(rule.body)]));
    for (const name of ["transition-duration", "transition-delay", "animation-duration", "animation-delay"]) {
      expect(decls.get(name), name).toMatch(/^0s\s*!important$/);
    }
  });
});

describe("base.css", () => {
  it("draws a 2 px brand focus ring on :focus-visible", () => {
    const focus = rules(read("base.css")).find((rule) => rule.prelude === ":focus-visible");
    expect(focus).toBeDefined();
    expect(declarations(focus?.body ?? "").get("outline")).toBe("var(--focus-ring)");
  });

  it("sets the display, body and metric roles", () => {
    const css = rules(read("base.css"));
    const decl = (selector: string): Map<string, string> => declarations(css.find((rule) => rule.prelude === selector)?.body ?? "");
    expect(decl("body").get("font-family")).toBe("var(--f-body)");
    expect(decl(".t-label").get("font-family")).toBe("var(--f-display)");
    expect(decl(".t-label").get("text-transform")).toBe("uppercase");
    expect(decl(".t-num").get("font-family")).toBe("var(--f-num)");
    expect(decl(".t-num").get("font-variant-numeric")).toContain("tabular-nums");
  });
});

describe("modes.css", () => {
  it("sets the accent of every mode", () => {
    const css = rules(read("modes.css"));
    for (const [mode, token] of Object.entries(MODES)) {
      const rule = css.find((r) => r.prelude === `[data-mode="${mode}"]`);
      expect(declarations(rule?.body ?? "").get("--accent"), mode).toBe(`var(--${token})`);
    }
  });
});

describe("components.css", () => {
  const classes = classSelectors(read("components.css"));

  it("styles the wave-1 shell classes and the reusable components", () => {
    for (const name of [...SHELL_CLASSES, ...COMPONENT_CLASSES]) {
      expect(classes.has(name), name).toBe(true);
    }
  });

  it("makes the fatal screen cover and block the whole page", () => {
    const fatal = rules(read("components.css")).find((rule) => rule.prelude === ".fatal");
    const decls = declarations(fatal?.body ?? "");
    expect(decls.get("position")).toBe("fixed");
    expect(decls.get("inset")).toBe("0");
    expect(Number(decls.get("z-index"))).toBeGreaterThanOrEqual(1000);
    expect(decls.get("background")).toMatch(/var\(--bg-0\)$/);
  });

  it("follows the block__element--modifier naming scheme", () => {
    const scheme = /^[a-z][a-z0-9]*(-[a-z0-9]+)*(__[a-z0-9]+(-[a-z0-9]+)*)?(--[a-z0-9]+(-[a-z0-9]+)*)?$/;
    for (const file of ["base.css", "components.css", "modes.css"]) {
      for (const name of classSelectors(read(file))) {
        expect(name, file).toMatch(scheme);
      }
    }
  });
});

describe("app.css", () => {
  it("imports the design stylesheets in cascade order and nothing else", () => {
    const css = read("app.css").trim();
    const imports = [...css.matchAll(/@import\s+"([^"]+)";/g)].map((m) => m[1]);
    expect(imports).toEqual(["./fonts.css", "./tokens.css", "./base.css", "./components.css", "./modes.css"]);
    expect(css.replace(/@import\s+"[^"]+";/g, "").trim()).toBe("");
  });
});

describe("icons.svg", () => {
  const svg = fs.readFileSync(ICONS, "utf8");

  it("holds the logo mark and the mode and tool icons as symbols", () => {
    const ids = [...svg.matchAll(/<symbol\s+id="([^"]+)"/g)].map((m) => m[1]);
    expect(ids).toEqual(ICON_IDS);
    expect(svg.trimStart()).toMatch(/^<svg xmlns="http:\/\/www\.w3\.org\/2000\/svg">/);
  });

  it("has no script, style, event handler, link or URL other than the SVG namespace", () => {
    expect(svg).not.toMatch(/<script|<style|<foreignObject|\son\w+=|\sstyle=|href=|<image/i);
    const urls = [...svg.matchAll(/[a-z][a-z0-9+.-]*:\/\/[^\s"']+/gi)].map((m) => m[0]);
    expect(urls).toEqual(["http://www.w3.org/2000/svg"]);
  });
});
