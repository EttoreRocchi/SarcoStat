#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: bash web/scripts/fetch_deployed.sh <version> <dest> [<BUILD.json>]" >&2
  echo "Downloads by curl every file that the given BUILD.json lists, and the deployed BUILD.json, from the deployed folder v<version>/ into <dest>/v<version>/, checking each SHA-256; the deployed BUILD.json must equal the given one byte for byte." >&2
  echo "The default BUILD.json is the local build's, SARCOSTAT_LOCAL_SITE/v<version>/BUILD.json (default web/dist/lead/site); at the study gate it is the archived BUILD.json of the release." >&2
  echo "Use <dest> = <scratch>/deployed/SarcoStat, then run the unchanged e2e and parity suites with SITE_DIR=<scratch>/deployed (V-RETEST): the folder is served on 127.0.0.1 under /SarcoStat/v<version>/ as on GitHub Pages." >&2
  echo "SARCOSTAT_PAGES_URL sets the site address (default https://ettorerocchi.github.io/SarcoStat/). Files are fetched with curl --compressed (decoded bytes), never with a bare Accept-Encoding header." >&2
}

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
  usage
  exit 2
fi
version=$1
dest=$2
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
local_site="${SARCOSTAT_LOCAL_SITE:-$here/../dist/lead/site}"
build="${3:-$local_site/v$version/BUILD.json}"
base="${SARCOSTAT_PAGES_URL:-https://ettorerocchi.github.io/SarcoStat/}"
base="${base%/}"
if [ ! -f "$build" ]; then
  echo "fetch_deployed: $build does not exist" >&2
  exit 2
fi
target="$dest/v$version"
partial="$dest/.v$version.partial"
if [ -e "$target" ]; then
  echo "fetch_deployed: $target already exists; give a new <dest>" >&2
  exit 2
fi
rm -rf "$partial"
mkdir -p "$partial"
tab=$'\t'
failures=0
count=0

fail() {
  echo "fetch_deployed: FAIL $*"
  failures=$((failures + 1))
}

python3 -I -c '
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
found = data.get("version")
if found != sys.argv[2]:
    raise SystemExit(f"BUILD.json is for version {found}, not {sys.argv[2]}")
for rel, digest in sorted(data["files"].items()):
    print(f"{rel}\t{digest}")
' "$build" "$version" > "$partial.list"
echo "fetch_deployed: v$version from $base/v$version/ into $target, checked against $build"

while IFS="$tab" read -r rel expected; do
  case "$rel" in
    *[!A-Za-z0-9._/+-]* | /* | *..*)
      fail "$rel: unexpected path"
      continue
      ;;
  esac
  mkdir -p "$(dirname "$partial/$rel")"
  status="$(curl -sS --compressed --retry 2 -o "$partial/$rel" -w '%{http_code}' "$base/v$version/$rel" || echo 000)"
  count=$((count + 1))
  if [ "$status" != "200" ]; then
    fail "$rel: HTTP $status"
    continue
  fi
  actual="$(sha256sum "$partial/$rel" | cut -d' ' -f1)"
  if [ "$actual" != "$expected" ]; then
    fail "$rel: SHA-256 $actual, expected $expected"
  fi
done < "$partial.list"

status="$(curl -sS --compressed --retry 2 -o "$partial/BUILD.json" -w '%{http_code}' "$base/v$version/BUILD.json" || echo 000)"
if [ "$status" != "200" ]; then
  fail "BUILD.json: HTTP $status"
elif ! cmp -s "$partial/BUILD.json" "$build"; then
  fail "the deployed BUILD.json ($(sha256sum "$partial/BUILD.json" | cut -d' ' -f1)) differs from $build ($(sha256sum "$build" | cut -d' ' -f1))"
fi
rm -f "$partial.list"

if [ "$failures" -gt 0 ]; then
  echo "fetch_deployed: FAILED with $failures problem(s); the partial download stays in $partial for inspection"
  exit 1
fi
mv "$partial" "$target"
echo "fetch_deployed: PASS $count file(s) and BUILD.json of v$version in $target, every SHA-256 equal to $build"
echo "fetch_deployed: V-RETEST: SITE_DIR=$(cd "$dest/.." && pwd) PORT=0 npm run test:e2e, and parity on $target/$(python3 -I -c 'import json, sys; print(json.load(open(sys.argv[1]))["wheel"]["file"])' "$build")"
