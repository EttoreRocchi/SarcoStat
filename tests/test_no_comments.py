"""The repository's code files carry no explanatory comments.

Python is read with :mod:`tokenize`; TypeScript, JavaScript, CSS, HTML, shell and workflow YAML
are read with small lexers that skip strings, template and regular-expression literals, quoted
URLs and here-documents. Tool directives stay allowed: ``noqa``, ``type: ignore``, ``pragma``,
``fmt``, ``eslint-disable*``, ``@ts-expect-error``, ``/// <reference ...>`` and a first-line
shebang. ``Makefile`` and ``.gitignore`` are not scanned.
"""

from __future__ import annotations

import io
import os
import re
import tokenize
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

SCAN_ROOTS = ("src", "tests", "tools", "web", ".github/workflows")
EXCLUDED_DIR_NAMES = frozenset(
    {
        "node_modules",
        "dist",
        ".cache",
        "test-results",
        "playwright-report",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
)
EXCLUDED_PATHS = frozenset({"web/src/assets/fonts"})

PY_SUFFIXES = frozenset({".py"})
JS_SUFFIXES = frozenset({".ts", ".js", ".mjs", ".cjs", ".mts", ".cts"})
CSS_SUFFIXES = frozenset({".css"})
HTML_SUFFIXES = frozenset({".html"})
SH_SUFFIXES = frozenset({".sh"})
YAML_SUFFIXES = frozenset({".yml", ".yaml"})

PY_DIRECTIVES = ("noqa", "type: ignore", "pragma", "fmt:")
JS_DIRECTIVES = ("eslint-disable", "@ts-expect-error")
REGEX_PRECEDING_WORDS = frozenset(
    {
        "return",
        "typeof",
        "instanceof",
        "case",
        "do",
        "else",
        "in",
        "of",
        "new",
        "delete",
        "void",
        "throw",
        "yield",
        "await",
    }
)
REGEX_PRECEDING_CHARS = frozenset("(,=:[!&|?{};+-*%<>~^")
SHELL_WORD_BREAKS = frozenset(" \t\n;&|()<>")

Finding = tuple[int, str]


def _python_comments(text: str) -> list[Finding]:
    """Return the comments of a Python source that are not tool directives."""
    found: list[Finding] = []
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type != tokenize.COMMENT:
            continue
        line = token.start[0]
        body = token.string[1:].strip()
        if line == 1 and token.start[1] == 0 and token.string.startswith("#!"):
            continue
        if body.startswith(PY_DIRECTIVES):
            continue
        found.append((line, token.string))
    return found


def _js_comment_allowed(comment: str) -> bool:
    """Tell whether a JavaScript or TypeScript comment is a tool directive."""
    if comment.startswith("///"):
        return comment[3:].strip().startswith("<reference")
    body = comment[2:-2] if comment.startswith("/*") else comment[2:]
    return body.strip().startswith(JS_DIRECTIVES)


def _skip_quoted(text: str, start: int, quote: str) -> int:
    """Return the index just after the string literal opened at ``start``."""
    j = start + 1
    while j < len(text):
        ch = text[j]
        if ch == "\\":
            j += 2
            continue
        if ch == "\n":
            return j
        if ch == quote:
            return j + 1
        j += 1
    return j


def _skip_regex(text: str, start: int) -> int:
    """Return the index just after the regular-expression literal opened at ``start``."""
    j = start + 1
    in_class = False
    while j < len(text):
        ch = text[j]
        if ch == "\\":
            j += 2
            continue
        if ch == "\n":
            return j
        if ch == "[":
            in_class = True
        elif ch == "]":
            in_class = False
        elif ch == "/" and not in_class:
            j += 1
            while j < len(text) and (text[j].isalpha()):
                j += 1
            return j
        j += 1
    return j


def _regex_allowed(previous: str) -> bool:
    """Tell whether a slash after ``previous`` opens a regular expression."""
    if not previous:
        return True
    if previous in REGEX_PRECEDING_WORDS:
        return True
    return len(previous) == 1 and previous in REGEX_PRECEDING_CHARS


def _js_comments(text: str, first_line: int = 1) -> list[Finding]:
    """Return the non-directive comments of JavaScript or TypeScript source."""
    found: list[Finding] = []
    i, n = 0, len(text)
    line = first_line
    if text.startswith("#!"):
        i = text.find("\n")
        i = n if i < 0 else i
    modes: list[list] = [["code", 0]]
    previous = ""
    while i < n:
        mode = modes[-1]
        ch = text[i]
        if mode[0] == "template":
            if ch == "\\":
                i += 2
                continue
            if ch == "`":
                modes.pop()
                previous = "`"
                i += 1
                continue
            if text.startswith("${", i):
                modes.append(["code", 0])
                previous = "{"
                i += 2
                continue
            if ch == "\n":
                line += 1
            i += 1
            continue
        if ch == "\n":
            line += 1
            i += 1
            continue
        if ch in " \t\r":
            i += 1
            continue
        if text.startswith("//", i):
            end = text.find("\n", i)
            end = n if end < 0 else end
            comment = text[i:end]
            if not _js_comment_allowed(comment):
                found.append((line, comment))
            i = end
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            comment = text[i:end]
            if not _js_comment_allowed(comment):
                found.append((line, comment))
            line += comment.count("\n")
            i = end
            continue
        if ch in "'\"":
            i = _skip_quoted(text, i, ch)
            previous = ch
            continue
        if ch == "`":
            modes.append(["template", 0])
            i += 1
            continue
        if ch == "/" and _regex_allowed(previous):
            end = _skip_regex(text, i)
            line += text.count("\n", i, end)
            i = end
            previous = "/re/"
            continue
        if ch == "{":
            mode[1] += 1
        elif ch == "}":
            if mode[1] == 0 and len(modes) > 1:
                modes.pop()
                i += 1
                continue
            mode[1] -= 1
        if ch.isalnum() or ch in "_$#":
            j = i + 1
            while j < n and (text[j].isalnum() or text[j] in "_$"):
                j += 1
            previous = text[i:j]
            i = j
            continue
        previous = ch
        i += 1
    return found


def _css_comments(text: str, first_line: int = 1) -> list[Finding]:
    """Return every comment of a CSS source."""
    found: list[Finding] = []
    i, n = 0, len(text)
    line = first_line
    while i < n:
        ch = text[i]
        if ch == "\n":
            line += 1
            i += 1
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            comment = text[i:end]
            found.append((line, comment))
            line += comment.count("\n")
            i = end
            continue
        if ch in "'\"":
            i = _skip_quoted(text, i, ch)
            continue
        if text[i : i + 4].lower() == "url(":
            end = text.find(")", i)
            end = n if end < 0 else end + 1
            line += text.count("\n", i, end)
            i = end
            continue
        i += 1
    return found


_EMBEDDED = re.compile(r"<(script|style)\b[^>]*>(.*?)</\1\s*>", re.IGNORECASE | re.DOTALL)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def _html_comments(text: str) -> list[Finding]:
    """Return the comments of an HTML page, including inline scripts and styles."""
    found: list[Finding] = []
    blanked = text
    for match in _EMBEDDED.finditer(text):
        body_start = match.start(2)
        first_line = text.count("\n", 0, body_start) + 1
        body = match.group(2)
        if match.group(1).lower() == "script":
            found.extend(_js_comments(body, first_line))
        else:
            found.extend(_css_comments(body, first_line))
        filler = re.sub(r"[^\n]", " ", match.group(0))
        blanked = blanked[: match.start()] + filler + blanked[match.end() :]
    for match in _HTML_COMMENT.finditer(blanked):
        found.append((blanked.count("\n", 0, match.start()) + 1, match.group(0)))
    return sorted(found)


_HEREDOC = re.compile(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")


def _shell_comments(text: str) -> list[Finding]:
    """Return the comments of a shell script, skipping quotes and here-documents."""
    found: list[Finding] = []
    lines = text.split("\n")
    pending: list[tuple[str, bool]] = []
    in_single = in_double = False
    index = 0
    while index < len(lines):
        number = index + 1
        raw = lines[index]
        index += 1
        if pending and not in_single and not in_double:
            delimiter, strip_tabs = pending.pop(0)
            current = raw
            while True:
                candidate = current.lstrip("\t") if strip_tabs else current
                if candidate == delimiter or index >= len(lines):
                    break
                current = lines[index]
                index += 1
            continue
        if number == 1 and raw.startswith("#!"):
            continue
        i = 0
        while i < len(raw):
            ch = raw[i]
            if in_single:
                if ch == "'":
                    in_single = False
                i += 1
                continue
            if ch == "\\":
                i += 2
                continue
            if in_double:
                if ch == '"':
                    in_double = False
                i += 1
                continue
            if ch == "'":
                in_single = True
            elif ch == '"':
                in_double = True
            elif raw.startswith("<<", i) and not raw.startswith("<<<", i):
                match = _HEREDOC.match(raw, i)
                if match:
                    pending.append((match.group(3), match.group(1) == "-"))
                    i = match.end()
                    continue
            elif ch == "#" and (i == 0 or raw[i - 1] in SHELL_WORD_BREAKS):
                found.append((number, raw[i:]))
                break
            i += 1
    return found


def _yaml_comments(text: str) -> list[Finding]:
    """Return the comments of a YAML file, including those inside shell blocks."""
    found: list[Finding] = []
    for number, raw in enumerate(text.split("\n"), start=1):
        quote = ""
        i = 0
        while i < len(raw):
            ch = raw[i]
            if quote:
                if ch == "\\" and quote == '"':
                    i += 2
                    continue
                if ch == quote:
                    quote = ""
            elif ch in "'\"":
                quote = ch
            elif ch == "#" and (i == 0 or raw[i - 1] in " \t"):
                found.append((number, raw[i:]))
                break
            i += 1
    return found


def find_comments(path: Path) -> list[Finding]:
    """Return ``(line, text)`` for every explanatory comment in ``path``."""
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")
    if suffix in PY_SUFFIXES:
        return _python_comments(text)
    if suffix in JS_SUFFIXES:
        return _js_comments(text)
    if suffix in CSS_SUFFIXES:
        return _css_comments(text)
    if suffix in HTML_SUFFIXES:
        return _html_comments(text)
    if suffix in SH_SUFFIXES:
        return _shell_comments(text)
    if suffix in YAML_SUFFIXES:
        return _yaml_comments(text)
    return []


CODE_SUFFIXES = PY_SUFFIXES | JS_SUFFIXES | CSS_SUFFIXES | HTML_SUFFIXES | SH_SUFFIXES


def scanned_files(root: Path = ROOT) -> list[Path]:
    """List the code files under the scanned folders, minus generated and vendored ones."""
    files: list[Path] = []
    for scan_root in SCAN_ROOTS:
        base = root / scan_root
        if not base.is_dir():
            continue
        for directory, dirnames, filenames in os.walk(base):
            relative_dir = Path(directory).relative_to(root).as_posix()
            dirnames[:] = sorted(
                name
                for name in dirnames
                if name not in EXCLUDED_DIR_NAMES and f"{relative_dir}/{name}" not in EXCLUDED_PATHS
            )
            wanted = YAML_SUFFIXES if scan_root == ".github/workflows" else CODE_SUFFIXES
            for name in sorted(filenames):
                path = Path(directory) / name
                if path.suffix.lower() in wanted:
                    files.append(path)
    return files


def test_repository_has_no_explanatory_comments():
    report = []
    for path in scanned_files():
        for line, comment in find_comments(path):
            report.append(f"{path.relative_to(ROOT).as_posix()}:{line}: {comment.strip()}")
    assert not report, "explanatory comments found:\n" + "\n".join(report)


def test_scan_covers_the_code_folders():
    relative = {path.relative_to(ROOT).as_posix() for path in scanned_files()}
    assert "tests/test_no_comments.py" in relative
    assert any(name.startswith("src/sarcostat/core/") for name in relative)
    assert any(name.startswith("src/sarcostat/engine/") for name in relative)
    assert not any("/node_modules/" in name for name in relative)
    assert not any(name.endswith((".yml", ".yaml")) for name in relative if name.startswith("src/"))
    if (ROOT / ".github/workflows/ci.yml").is_file():
        assert ".github/workflows/ci.yml" in relative


BAD_SAMPLES = {
    "sample.py": ["x = 1  # note", "# note"],
    "sample.sh": ["echo hi  # note", "# note"],
    "workflow.yml": ["key: value  # note", "# note", "run: |\n  echo hi\n  # note"],
    "sample.ts": ["const x = 1;  // note", "/* note */", "const y = `a${1}`;  // note"],
    "sample.js": ["let x = 1;  // note", "/* note */"],
    "sample.mjs": ["export const x = 1;  // note", "/* note */"],
    "sample.css": ["a { color: red; }  /* note */", "/* note */"],
    "sample.html": [
        "<p>a</p>\n<!-- note -->",
        "<script>\nconst x = 1;  // note\n</script>",
        "<style>\n/* note */\n</style>",
    ],
}

GOOD_SAMPLES = {
    "sample.py": [
        "import os  # noqa: E402",
        "x: int = 'a'  # type: ignore[assignment]",
        "if x:  # pragma: no cover\n    pass",
        "y = 2  # fmt: skip",
        "#!/usr/bin/env python3\nz = 3",
        "s = '# not a comment'",
        "t = '''\n# not a comment\n'''",
    ],
    "sample.sh": [
        "#!/usr/bin/env bash\nset -euo pipefail",
        'echo "$#"',
        'echo "${#v}"',
        'echo "${v#prefix}"',
        "echo ${#v} ${v#prefix} $#",
        "echo 'a # b' \"c # d\"",
        "cat <<EOF\n# not a comment\nEOF",
        "cat <<-'EOF'\n\t# not a comment\n\tEOF",
        "url=https://example.org/a#b",
    ],
    "workflow.yml": [
        "run: echo '#'",
        'run: echo "a # b"',
        "url: https://example.org/a#b",
    ],
    "sample.ts": [
        "// @ts-expect-error the fixture is deliberately untyped\nconst x: number = 'a';",
        '/// <reference types="vite/client" />',
        "// eslint-disable-next-line no-console\nconsole.log(1);",
        "/* eslint-disable */\nconst y = 1;",
        "const u = 'https://example.org/a';",
        "const r = /\\/\\/ not a comment/g;",
        "const t = `// not a comment ${'/* nor this */'}`;",
        "class A { #priv = 1; }",
        "const d = a / b / c;",
    ],
    "sample.mjs": ["#!/usr/bin/env node\nexport const x = 1;"],
    "sample.css": [
        "a { background: url(https://example.org/x.png); }",
        "b::after { content: '/* not a comment */'; }",
    ],
    "sample.html": [
        '<!doctype html>\n<a href="https://example.org/">x</a>',
        "<script>\nconst u = 'https://example.org/';\n</script>",
    ],
}


@pytest.mark.parametrize(
    ("name", "source"),
    [(name, source) for name, sources in BAD_SAMPLES.items() for source in sources],
)
def test_comment_is_reported(tmp_path, name, source):
    path = tmp_path / name
    path.write_text(source + "\n", encoding="utf-8")
    assert find_comments(path), f"{name}: comment not reported in {source!r}"


@pytest.mark.parametrize(
    ("name", "source"),
    [(name, source) for name, sources in GOOD_SAMPLES.items() for source in sources],
)
def test_directive_or_literal_is_not_reported(tmp_path, name, source):
    path = tmp_path / name
    path.write_text(source + "\n", encoding="utf-8")
    assert find_comments(path) == [], f"{name}: false report in {source!r}"


@pytest.mark.parametrize(
    "relative",
    ["web/scripts/fetch_node.sh", "web/scripts/verify_deploy.sh", "tools/check_install.sh"],
)
def test_shell_scripts_pass(relative):
    path = ROOT / relative
    if not path.is_file():
        pytest.skip(f"{relative} does not exist yet")
    assert find_comments(path) == []
