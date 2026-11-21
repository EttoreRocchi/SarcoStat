"""Subset the web app's fonts into woff2 files and write their ``@font-face`` rules.

The sources are OFL fonts from the Google Fonts repository at one pinned commit
(:data:`SOURCES`), each checked against its SHA-256. Three families carry the design
(Barlow Semi Condensed for display and labels, Atkinson Hyperlegible Next for body text,
Atkinson Hyperlegible Mono for metrics); they have no Greek beyond a few letters and no
arrows, so two OFL fallback faces fill exactly those gaps (Inter for the two proportional
stacks, Noto Sans Mono for the metric stack), declared with ``unicode-range`` and a
``size-adjust`` that matches their x-height to the face they stand in for.

Every face keeps the characters of :data:`TARGET` that it has; the Atkinson faces also get
the micro sign, the increment and the ohm sign mapped to their Greek mu, Delta and Omega
(:data:`ALIASES`). Variable sources are cut to the weights the design uses.

Usage (fontTools and brotli are needed; they are not part of the sarcostat environment)::

    python web/scripts/subset_fonts.py --src FOLDER [--download]

``--download`` fetches missing sources into ``FOLDER`` first. The script writes
``web/src/assets/fonts/<family>/`` (woff2 files and the family's ``OFL.txt``) and
``web/src/styles/fonts.css``, then prints the size and coverage of every file.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import shutil
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

try:
    from fontTools import subset
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer
except ImportError:
    sys.exit("subset_fonts: fontTools is needed (pip install fonttools brotli in a scratch venv)")

WEB = Path(__file__).resolve().parents[1]
FONTS_OUT = WEB / "src" / "assets" / "fonts"
CSS_OUT = WEB / "src" / "styles" / "fonts.css"
CSS_TO_FONTS = "../assets/fonts"
COMMIT = "869789e84da92782c144d83d69b64cec54905b5f"
BASE_URL = f"https://raw.githubusercontent.com/google/fonts/{COMMIT}/ofl/"

SOURCES: dict[str, tuple[str, str]] = {
    "barlow-semi-condensed/BarlowSemiCondensed-SemiBold.ttf": (
        "barlowsemicondensed/BarlowSemiCondensed-SemiBold.ttf",
        "bd299f4bc5b44d30be8d42e9bad3a5df7d66af1cd55d0ed72a8b8916360a1424",
    ),
    "barlow-semi-condensed/BarlowSemiCondensed-Bold.ttf": (
        "barlowsemicondensed/BarlowSemiCondensed-Bold.ttf",
        "5bd6757e459a110da81b44532f0f43058e2dab2401688abca84f2fdbde193cbb",
    ),
    "barlow-semi-condensed/OFL.txt": (
        "barlowsemicondensed/OFL.txt",
        "186d750eb496a4c17a76385f82be6aea2ac1cf2de074a811d63786cf374ea73f",
    ),
    "atkinson-hyperlegible-next/AtkinsonHyperlegibleNext[wght].ttf": (
        "atkinsonhyperlegiblenext/AtkinsonHyperlegibleNext[wght].ttf",
        "5a455d1cfa099b601ab70751bb9673e8fe1854dc4500c80e1a220d0d75e31745",
    ),
    "atkinson-hyperlegible-next/OFL.txt": (
        "atkinsonhyperlegiblenext/OFL.txt",
        "aca6a428580965d2297d1b718042dd427c2a9443ece3b0d02d758e161e0c4030",
    ),
    "atkinson-hyperlegible-mono/AtkinsonHyperlegibleMono[wght].ttf": (
        "atkinsonhyperlegiblemono/AtkinsonHyperlegibleMono[wght].ttf",
        "5ce8b1698d1ded7dff2178c1a3ad159470085a58ea239e8b2cb88f4fb4a6f646",
    ),
    "atkinson-hyperlegible-mono/OFL.txt": (
        "atkinsonhyperlegiblemono/OFL.txt",
        "1ebb31cf7393164f20d10c1d48406cddb5314feff8465531cf1e4ba37e9dd740",
    ),
    "inter/Inter[opsz,wght].ttf": (
        "inter/Inter[opsz,wght].ttf",
        "29160a80ff49ddcab2c97711247e08b1fab27a484a329ce8b813d820dc559031",
    ),
    "inter/OFL.txt": (
        "inter/OFL.txt",
        "5b9321a4298cfeb6b34354164a1c3afc3db114569984c502b9b35d988fd58c57",
    ),
    "noto-sans-mono/NotoSansMono[wdth,wght].ttf": (
        "notosansmono/NotoSansMono[wdth,wght].ttf",
        "2cb2adb378a8f574213e23df697050b83c54c27df465a2015552740b2769a081",
    ),
    "noto-sans-mono/OFL.txt": (
        "notosansmono/OFL.txt",
        "cee9892f9f0cc8fe882c9e9537ee6a89621d86ee7ceaf70b02e2b2b1c25c061a",
    ),
}
"""Local path under ``--src`` → (path under ``ofl/`` at :data:`COMMIT`, SHA-256)."""


def _ranges(*spans: tuple[int, int]) -> frozenset[int]:
    return frozenset(cp for lo, hi in spans for cp in range(lo, hi + 1))


TARGET: frozenset[int] = _ranges(
    (0x0020, 0x007E),
    (0x00A0, 0x00AC),
    (0x00AE, 0x0148),
    (0x014A, 0x017F),
    (0x0218, 0x021B),
    (0x02C6, 0x02C7),
    (0x02D8, 0x02DD),
    (0x0391, 0x03A1),
    (0x03A3, 0x03A9),
    (0x03B1, 0x03C9),
    (0x03D1, 0x03D1),
    (0x03D5, 0x03D5),
    (0x03F5, 0x03F5),
    (0x2009, 0x200A),
    (0x2010, 0x2015),
    (0x2018, 0x201E),
    (0x2020, 0x2022),
    (0x2026, 0x2026),
    (0x202F, 0x2030),
    (0x2032, 0x2033),
    (0x2039, 0x203A),
    (0x2044, 0x2044),
    (0x2070, 0x2070),
    (0x2074, 0x207B),
    (0x2080, 0x208B),
    (0x20AC, 0x20AC),
    (0x2116, 0x2116),
    (0x2122, 0x2122),
    (0x2126, 0x2126),
    (0x2190, 0x2195),
    (0x21D2, 0x21D2),
    (0x21D4, 0x21D4),
    (0x2202, 0x2202),
    (0x2206, 0x2206),
    (0x220F, 0x220F),
    (0x2211, 0x2212),
    (0x2215, 0x2215),
    (0x2219, 0x221A),
    (0x221E, 0x221E),
    (0x2248, 0x2248),
    (0x2260, 0x2260),
    (0x2264, 0x2265),
)
"""Basic Latin, Latin-1, Latin Extended-A, basic Greek, punctuation, arrows and the math signs."""

ALIASES: dict[int, int] = {0x00B5: 0x03BC, 0x2206: 0x0394, 0x2126: 0x03A9}
"""Code point → code point whose glyph it takes when the font lacks it."""


@dataclass(frozen=True)
class Face:
    """One woff2 file and its ``@font-face`` rule."""

    family: str
    folder: str
    source: str
    output: str
    weight: tuple[int, int]
    axes: dict[str, float | tuple[float, float]] = field(default_factory=dict)
    aliases: bool = False
    serves: tuple[str, ...] = ()
    size_adjust: str = ""


DISPLAY = "Barlow Semi Condensed"
BODY = "Atkinson Hyperlegible Next"
NUM = "Atkinson Hyperlegible Mono"
FALLBACK_SANS = "SarcoStat Fallback Sans"
FALLBACK_MONO = "SarcoStat Fallback Mono"

FACES: tuple[Face, ...] = (
    Face(
        DISPLAY,
        "barlow-semi-condensed",
        "barlow-semi-condensed/BarlowSemiCondensed-SemiBold.ttf",
        "barlow-semi-condensed-600.woff2",
        (600, 600),
    ),
    Face(
        DISPLAY,
        "barlow-semi-condensed",
        "barlow-semi-condensed/BarlowSemiCondensed-Bold.ttf",
        "barlow-semi-condensed-700.woff2",
        (700, 700),
    ),
    Face(
        BODY,
        "atkinson-hyperlegible-next",
        "atkinson-hyperlegible-next/AtkinsonHyperlegibleNext[wght].ttf",
        "atkinson-hyperlegible-next-400-700.woff2",
        (400, 700),
        {"wght": (400, 700)},
        aliases=True,
    ),
    Face(
        NUM,
        "atkinson-hyperlegible-mono",
        "atkinson-hyperlegible-mono/AtkinsonHyperlegibleMono[wght].ttf",
        "atkinson-hyperlegible-mono-400-700.woff2",
        (400, 700),
        {"wght": (400, 700)},
        aliases=True,
    ),
    Face(
        FALLBACK_SANS,
        "inter",
        "inter/Inter[opsz,wght].ttf",
        "inter-fallback-400-700.woff2",
        (400, 700),
        {"opsz": 14, "wght": (400, 700)},
        serves=(DISPLAY, BODY),
        size_adjust="91%",
    ),
    Face(
        FALLBACK_MONO,
        "noto-sans-mono",
        "noto-sans-mono/NotoSansMono[wdth,wght].ttf",
        "noto-sans-mono-fallback-400-700.woff2",
        (400, 700),
        {"wdth": 100, "wght": (400, 700)},
        serves=(NUM,),
        size_adjust="92.5%",
    ),
)

STACKS: dict[str, tuple[str, ...]] = {
    "--f-display": (DISPLAY, FALLBACK_SANS),
    "--f-body": (BODY, FALLBACK_SANS),
    "--f-num": (NUM, FALLBACK_MONO),
}
"""Font stacks of ``tokens.css`` (the generic family follows them there)."""


def sha256(path: Path) -> str:
    """Hex SHA-256 of a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch_sources(src: Path, download: bool) -> None:
    """Check every source under ``src`` against :data:`SOURCES`, downloading missing ones."""
    bad = []
    for local, (remote, digest) in SOURCES.items():
        path = src / local
        if not path.exists() and download:
            path.parent.mkdir(parents=True, exist_ok=True)
            url = BASE_URL + urllib.parse.quote(remote)
            with urllib.request.urlopen(url, timeout=60) as response:
                path.write_bytes(response.read())
        if not path.exists():
            bad.append(f"missing {local}")
        elif sha256(path) != digest:
            bad.append(f"SHA-256 mismatch {local}: {sha256(path)}")
    if bad:
        sys.exit("subset_fonts: " + "; ".join(bad))


def unicode_cmap(font: TTFont) -> dict[int, str]:
    """The font's best Unicode cmap."""
    return dict(font.getBestCmap() or {})


def add_aliases(font: TTFont) -> None:
    """Map each :data:`ALIASES` code point the font lacks to its partner's glyph."""
    for table in font["cmap"].tables:
        if not table.isUnicode():
            continue
        for alias, partner in ALIASES.items():
            if alias not in table.cmap and partner in table.cmap:
                table.cmap[alias] = table.cmap[partner]


def load(src: Path, face: Face) -> TTFont:
    """The source font, cut to the face's axes, with aliases when the face asks for them."""
    font = TTFont(src / face.source, recalcTimestamp=False)
    if face.axes:
        cut = instancer.instantiateVariableFont(font, dict(face.axes), updateFontNames=False)
        buffer = io.BytesIO()
        cut.save(buffer)
        buffer.seek(0)
        font = TTFont(buffer, recalcTimestamp=False)
    if face.aliases:
        add_aliases(font)
    return font


def subset_options() -> subset.Options:
    """Subsetter options: all layout features, licence names kept, woff2 output."""
    options = subset.Options()
    options.layout_features = ["*"]
    options.name_IDs = [0, 1, 2, 3, 4, 5, 6, 13, 14]
    options.name_languages = [0x0409]
    options.notdef_outline = True
    options.glyph_names = False
    options.legacy_kern = False
    options.recalc_timestamp = False
    options.flavor = "woff2"
    return options


def write_face(font: TTFont, wanted: frozenset[int], out: Path) -> frozenset[int]:
    """Subset ``font`` to ``wanted`` (within its cmap), save it as woff2, return its code points."""
    keep = sorted(wanted & unicode_cmap(font).keys())
    subsetter = subset.Subsetter(options=subset_options())
    subsetter.populate(unicodes=keep)
    subsetter.subset(font)
    out.parent.mkdir(parents=True, exist_ok=True)
    font.flavor = "woff2"
    font.save(out)
    written = TTFont(out)
    return frozenset(unicode_cmap(written))


def unicode_range(points: frozenset[int]) -> str:
    """CSS ``unicode-range`` value covering exactly ``points``."""
    spans: list[tuple[int, int]] = []
    for cp in sorted(points):
        if spans and cp == spans[-1][1] + 1:
            spans[-1] = (spans[-1][0], cp)
        else:
            spans.append((cp, cp))
    return ", ".join(f"U+{lo:X}" if lo == hi else f"U+{lo:X}-{hi:X}" for lo, hi in spans)


def css_rule(face: Face, points: frozenset[int]) -> str:
    """The ``@font-face`` rule of one face."""
    lo, hi = face.weight
    weight = str(lo) if lo == hi else f"{lo} {hi}"
    lines = [
        "@font-face {",
        f'  font-family: "{face.family}";',
        "  font-style: normal;",
        f"  font-weight: {weight};",
        "  font-display: block;",
        f'  src: url("{CSS_TO_FONTS}/{face.folder}/{face.output}") format("woff2");',
        f"  unicode-range: {unicode_range(points)};",
    ]
    if face.size_adjust:
        lines.append(f"  size-adjust: {face.size_adjust};")
    lines.append("}")
    return "\n".join(lines)


def build(src: Path) -> int:
    """Write every face, the OFL texts and ``fonts.css``; print sizes and coverage (1 if a gap is left)."""
    for folder in {face.folder for face in FACES}:
        target = FONTS_OUT / folder
        for old in target.glob("*.woff2"):
            old.unlink()
        target.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src / folder / "OFL.txt", target / "OFL.txt")
    coverage: dict[str, frozenset[int]] = {}
    written: dict[str, frozenset[int]] = {}
    for face in FACES:
        if face.serves:
            continue
        points = write_face(load(src, face), TARGET, FONTS_OUT / face.folder / face.output)
        written[face.output] = points
        coverage[face.family] = coverage.get(face.family, points) & points
    for face in FACES:
        if not face.serves:
            continue
        gaps = frozenset().union(*(TARGET - coverage[family] for family in face.serves))
        points = write_face(load(src, face), gaps, FONTS_OUT / face.folder / face.output)
        written[face.output] = points
        coverage[face.family] = points
    rules = [css_rule(face, written[face.output]) for face in FACES]
    CSS_OUT.write_text("\n\n".join(rules) + "\n", encoding="utf-8")
    total = 0
    for face in FACES:
        size = (FONTS_OUT / face.folder / face.output).stat().st_size
        total += size
        print(f"{face.folder}/{face.output}: {size} bytes, {len(written[face.output])} code points")
    print(f"total woff2: {total} bytes")
    missing = 0
    for stack, families in STACKS.items():
        covered = frozenset().union(*(coverage[family] for family in families))
        lacking = sorted(TARGET - covered)
        missing += len(lacking)
        detail = " ".join(f"U+{cp:04X}" for cp in lacking) or "none"
        print(
            f"{stack}: {len(TARGET & covered)} of {len(TARGET)} target code points; missing {detail}"
        )
    return 1 if missing else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--src", type=Path, required=True, help="folder with the source fonts")
    parser.add_argument("--download", action="store_true", help="fetch missing sources first")
    args = parser.parse_args(argv)
    fetch_sources(args.src, args.download)
    return build(args.src)


if __name__ == "__main__":
    sys.exit(main())
