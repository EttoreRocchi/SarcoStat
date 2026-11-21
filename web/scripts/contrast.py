"""Check the colour contrast of the web app's design tokens (V-CONTRAST).

The script reads the custom properties of the top-level ``:root`` rules of a
``tokens.css`` file (``var()`` references resolved, ``@media`` blocks ignored), computes
WCAG 2.x contrast ratios and prints them as tables:

- text pairs (body text, labels, accent text, badge letters), each at least 4.5:1;
- overlay colours on the image, each against its halo (``--halo`` composited over light
  grey tissue #C8C8C8), at least 3:1 (WCAG 1.4.11);
- other non-text pairs (focus ring, accent marks, the "not exported" dot), at least 3:1;
- the landmark pairs under simulated colour-vision deficiency (Machado 2009, severity 1,
  CIE76 ΔE), for information.

A colour with alpha is composited over the background named in the pair. The exit status
is 1 when a pair is below its threshold and 2 when the file lacks a token.

Usage::

    python web/scripts/contrast.py [web/src/styles/tokens.css]
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TOKENS = Path(__file__).resolve().parents[1] / "src" / "styles" / "tokens.css"
WHITE = "#FFFFFF"
LIGHT_GREY = "#C8C8C8"
TISSUE = (("black", "#000000"), ("dark", "#3A3A3A"), ("mid", "#7F7F7F"), ("light", LIGHT_GREY))
SURFACES = ("bg-0", "bg-1", "bg-2", "bg-3", "bg-4")
MODES = ("m-home", "m-shape", "m-straight", "m-register", "m-planner", "m-study")
OVERLAYS = ("lm-z", "lm-m", "lm-chain", "lm-f1", "lm-f2", "lm-link", "lm-target")
TEXT_MIN = 4.5
NON_TEXT_MIN = 3.0

RGBA = tuple[float, float, float, float]


class TokenError(KeyError):
    """A token the checks need is missing or is not a colour."""


@dataclass(frozen=True)
class Check:
    """One foreground on one background, with the minimum ratio it needs."""

    group: str
    fg: str
    bg: str
    minimum: float
    note: str = ""


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def top_level_rules(css: str) -> list[tuple[str, str]]:
    """``(prelude, body)`` of every top-level rule; at-rule blocks come back as one rule."""
    rules = []
    i, n = 0, len(css)
    while i < n:
        start = css.find("{", i)
        if start < 0:
            break
        prelude = css[i:start].strip()
        depth, j = 1, start + 1
        while j < n and depth:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        rules.append((prelude, css[start + 1 : j - 1]))
        i = j
    return rules


def read_tokens(css: str) -> dict[str, str]:
    """Custom properties declared in the top-level ``:root`` rules (later ones win)."""
    tokens: dict[str, str] = {}
    for prelude, body in top_level_rules(_strip_comments(css)):
        if prelude.startswith("@"):
            continue
        selectors = [s.strip() for s in prelude.split(",")]
        if ":root" not in selectors:
            continue
        for name, value in re.findall(r"--([\w-]+)\s*:\s*([^;]+);", body):
            tokens[name] = value.strip()
    return tokens


def resolve(tokens: dict[str, str], value: str, depth: int = 0) -> str:
    """``value`` with every ``var(--name[, fallback])`` replaced."""
    if depth > 20:
        raise TokenError(f"var() cycle in {value!r}")
    pattern = re.compile(r"var\(\s*--([\w-]+)\s*(?:,\s*([^()]*))?\)")

    def swap(match: re.Match[str]) -> str:
        name, fallback = match.group(1), match.group(2)
        if name in tokens:
            return resolve(tokens, tokens[name], depth + 1)
        if fallback is not None:
            return resolve(tokens, fallback.strip(), depth + 1)
        raise TokenError(f"--{name} is not defined")

    return pattern.sub(swap, value)


def parse_colour(text: str) -> RGBA:
    """An sRGB colour with alpha from ``#rgb``, ``#rrggbb``, ``#rrggbbaa``, ``rgb()`` or ``rgba()``."""
    text = text.strip()
    hex_match = re.fullmatch(r"#([0-9A-Fa-f]{3}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})", text)
    if hex_match:
        digits = hex_match.group(1)
        if len(digits) == 3:
            digits = "".join(c * 2 for c in digits)
        alpha = int(digits[6:8], 16) / 255 if len(digits) == 8 else 1.0
        return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16), alpha)
    func = re.fullmatch(r"rgba?\(([^)]*)\)", text)
    if func:
        parts = [p for p in re.split(r"[\s,/]+", func.group(1).strip()) if p]
        if len(parts) not in (3, 4):
            raise ValueError(f"not a colour: {text!r}")
        channels = [float(p[:-1]) * 2.55 if p.endswith("%") else float(p) for p in parts[:3]]
        alpha = 1.0
        if len(parts) == 4:
            alpha = float(parts[3][:-1]) / 100 if parts[3].endswith("%") else float(parts[3])
        return (channels[0], channels[1], channels[2], alpha)
    raise ValueError(f"not a colour: {text!r}")


def colour(tokens: dict[str, str], ref: str) -> RGBA:
    """The colour of a token name (``bg-1``) or of a literal colour (``#C8C8C8``)."""
    if ref.startswith("#") or ref.startswith("rgb"):
        return parse_colour(ref)
    if ref not in tokens:
        raise TokenError(f"--{ref} is not defined")
    try:
        return parse_colour(resolve(tokens, tokens[ref]))
    except ValueError as exc:
        raise TokenError(f"--{ref}: {exc}") from exc


def over(top: RGBA, under: RGBA) -> RGBA:
    """``top`` composited over the opaque ``under``."""
    a = top[3]
    return (
        round(a * top[0] + (1 - a) * under[0]),
        round(a * top[1] + (1 - a) * under[1]),
        round(a * top[2] + (1 - a) * under[2]),
        1.0,
    )


def layered(tokens: dict[str, str], ref: str) -> RGBA:
    """An opaque colour from ``a@b@c``: ``a`` over ``b`` over ``c`` (the last must be opaque)."""
    layers = [colour(tokens, part) for part in ref.split("@")]
    result = layers[-1]
    if result[3] < 1:
        raise TokenError(f"{ref}: the bottom layer must be opaque")
    for top in reversed(layers[:-1]):
        result = over(top, result)
    return result


def hex_of(rgba: RGBA) -> str:
    return "#" + "".join(f"{round(v):02X}" for v in rgba[:3])


def _linear(rgba: RGBA) -> list[float]:
    values = [v / 255 for v in rgba[:3]]
    return [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in values]


def luminance(rgba: RGBA) -> float:
    r, g, b = _linear(rgba)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a: RGBA, b: RGBA) -> float:
    """WCAG 2.x contrast ratio."""
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def grade(value: float) -> str:
    if value >= 7:
        return "AAA"
    if value >= 4.5:
        return "AA"
    if value >= 3:
        return "3:1"
    return "below 3:1"


CVD = {
    "protan": (
        (0.152286, 1.052583, -0.204868),
        (0.114503, 0.786281, 0.099216),
        (-0.003882, -0.048116, 1.051998),
    ),
    "deutan": (
        (0.367322, 0.860646, -0.227968),
        (0.280085, 0.672501, 0.047413),
        (-0.011820, 0.042940, 0.968881),
    ),
    "tritan": (
        (1.255528, -0.076749, -0.178779),
        (-0.078411, 0.930809, 0.147602),
        (0.004733, 0.691367, 0.303900),
    ),
}
CVD_PAIRS = (
    ("lm-z", "lm-m", "Shape: Z-line vs M-line"),
    ("lm-z", "lm-chain", "Straightness: Z-line vs chain"),
    ("lm-f1", "lm-f2", "Register: myofibril 1 vs 2"),
    ("lm-link", "lm-f2", "Register: pair number vs myofibril 2"),
    ("lm-target", "lm-m", "Study: target box vs M-line"),
    ("lm-target", "lm-z", "Study: target box vs Z-line"),
)


def _lab(linear: list[float]) -> tuple[float, float, float]:
    r, g, b = linear
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
    f = [v ** (1 / 3) if v > 0.008856 else 7.787 * v + 16 / 116 for v in (x, y, z)]
    return 116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])


def _simulate(rgba: RGBA, kind: str) -> list[float]:
    linear = _linear(rgba)
    if kind == "normal":
        return linear
    m = CVD[kind]
    return [min(1.0, max(0.0, sum(m[r][k] * linear[k] for k in range(3)))) for r in range(3)]


def delta_e(a: RGBA, b: RGBA, kind: str) -> float:
    la, lb = _lab(_simulate(a, kind)), _lab(_simulate(b, kind))
    return sum((x - y) ** 2 for x, y in zip(la, lb, strict=True)) ** 0.5


def checks() -> list[Check]:
    """Every pair the design uses, with its threshold."""
    out: list[Check] = []
    float_white = "bg-float@" + WHITE
    tag_white = "bg-tag@" + WHITE
    for fg in ("tx-1", "tx-2"):
        out += [Check("text", fg, bg, TEXT_MIN) for bg in SURFACES]
        out.append(Check("text", fg, float_white, TEXT_MIN, "floating panel over a white pixel"))
    out += [Check("text", "tx-3", bg, TEXT_MIN) for bg in SURFACES[:4]]
    out.append(Check("text", "tx-1", tag_white, TEXT_MIN, "image tag over a white pixel"))
    out.append(Check("text", "brand-ink", "brand", TEXT_MIN, "primary button"))
    out += [Check("text", "brand", bg, TEXT_MIN, "links, wordmark") for bg in SURFACES[:3]]
    for mode in MODES:
        out += [Check("text", mode, bg, TEXT_MIN, "accent text") for bg in SURFACES[1:4]]
        out.append(Check("text", mode, float_white, TEXT_MIN, "accent in the step guidance"))
    out += [Check("text", "ok", bg, TEXT_MIN) for bg in SURFACES[:3]]
    out += [Check("text", "danger", bg, TEXT_MIN) for bg in SURFACES[1:4]]
    for fill in ("lm-z", "lm-m", "lm-chain", "lm-f2", "lm-link"):
        out.append(Check("text", "lm-ink", fill, TEXT_MIN, "letter on a landmark badge or chip"))
    halo_light = "halo@" + LIGHT_GREY
    for lm in OVERLAYS:
        out.append(Check("overlay", lm, halo_light, NON_TEXT_MIN, "on its halo over light grey"))
    out.append(Check("overlay", halo_light, LIGHT_GREY, NON_TEXT_MIN, "halo edge on light grey"))
    out += [Check("non-text", "brand", bg, NON_TEXT_MIN, "focus ring") for bg in SURFACES]
    for mode in MODES:
        out += [Check("non-text", mode, bg, NON_TEXT_MIN, "accent mark") for bg in SURFACES]
    out.append(Check("non-text", "unsaved", "bg-1", NON_TEXT_MIN, "not-exported dot"))
    return out


def run(css: str) -> tuple[list[str], list[str]]:
    """Report lines and failing lines for the tokens in ``css``."""
    tokens = read_tokens(css)
    lines = [
        "| Group | Foreground | Background | Ratio | Needs | Grade | Use |",
        "|---|---|---|---|---|---|---|",
    ]
    failures = []
    minima: dict[str, float] = {}
    for check in checks():
        fg, bg = layered(tokens, check.fg), layered(tokens, check.bg)
        value = ratio(fg, bg)
        minima[check.group] = min(value, minima.get(check.group, value))
        row = (
            f"| {check.group} | {_label(check.fg)} {hex_of(fg)} | {_label(check.bg)} {hex_of(bg)} "
            f"| {value:.2f} | {check.minimum:g} | {grade(value)} | {check.note} |"
        )
        lines.append(row)
        if value < check.minimum:
            failures.append(row)
    lines.append("")
    lines.append(
        "| Overlay | Colour | " + " | ".join(f"on {name} {h}" for name, h in TISSUE) + " |"
    )
    lines.append("|---|---|" + "---|" * len(TISSUE))
    for lm in OVERLAYS:
        c = layered(tokens, lm)
        cells = " | ".join(f"{ratio(c, parse_colour(h)):.2f}" for _, h in TISSUE)
        lines.append(f"| `--{lm}` | {hex_of(c)} | {cells} |")
    lines.append("")
    lines.append("| Landmark pair | Where | normal | protan | deutan | tritan |")
    lines.append("|---|---|---|---|---|---|")
    for a, b, where in CVD_PAIRS:
        ca, cb = layered(tokens, a), layered(tokens, b)
        cells = " | ".join(
            f"{delta_e(ca, cb, k):.0f}" for k in ("normal", "protan", "deutan", "tritan")
        )
        lines.append(f"| `--{a}` vs `--{b}` | {where} | {cells} |")
    lines.append("")
    summary = ", ".join(f"{group} {value:.2f}" for group, value in minima.items())
    lines.append(f"Minimum ratio per group: {summary}.")
    return lines, failures


def _label(ref: str) -> str:
    return " over ".join(part if part.startswith("#") else f"`--{part}`" for part in ref.split("@"))


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    path = Path(args[0]) if args else DEFAULT_TOKENS
    try:
        lines, failures = run(path.read_text(encoding="utf-8"))
    except TokenError as exc:
        print(f"contrast: {path}: {exc.args[0]}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    if failures:
        print(f"\ncontrast: {len(failures)} pair(s) below the threshold:", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("contrast: every pair meets its threshold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
