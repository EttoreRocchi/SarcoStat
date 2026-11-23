#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: bash web/scripts/verify_deploy.sh <version> <local site root>" >&2
  echo "Compares the deployed folder v<version>/ with the local build <local site root>/v<version>/ by curl only: every file of the local BUILD.json and BUILD.json itself (HTTP 200, SHA-256, media type), then the root and /latest/ redirect pages and /versions.json." >&2
  echo "SARCOSTAT_PAGES_URL sets the site address (default https://ettorerocchi.github.io/SarcoStat/). Hashes are taken on curl --compressed output (the decoded bytes), never with a bare Accept-Encoding header, which would hash the gzip stream." >&2
  echo "Media types are compared on the part before ';': .wasm exactly application/wasm with no parameter; .js and .mjs text/javascript or application/javascript; .json application/json; .whl a binary type (application/octet-stream, application/zip, application/x-zip-compressed or a wheel type), never text/*; .zip application/zip or application/x-zip-compressed; .html text/html; .css text/css; .svg image/svg+xml; .woff2 font/woff2; .png image/png." >&2
}

if [ "$#" -ne 2 ]; then
  usage
  exit 2
fi
version=$1
root=$2
base="${SARCOSTAT_PAGES_URL:-https://ettorerocchi.github.io/SarcoStat/}"
base="${base%/}"
build="$root/v$version/BUILD.json"
if [ ! -f "$build" ]; then
  echo "verify_deploy: $build does not exist" >&2
  exit 2
fi
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
failures=0
checked=0
tab=$'\t'

fail() {
  echo "verify_deploy: FAIL $*"
  failures=$((failures + 1))
}

media_type() {
  printf '%s' "${1%%;*}" | tr 'A-Z' 'a-z' | tr -d ' \t\r'
}

media_ok() {
  local file=$1 header=$2 type
  type="$(media_type "$header")"
  case "$file" in
    *.wasm) [ "$(printf '%s' "$header" | tr 'A-Z' 'a-z' | tr -d ' \t\r')" = "application/wasm" ] ;;
    *.js | *.mjs) [ "$type" = "text/javascript" ] || [ "$type" = "application/javascript" ] ;;
    *.json) [ "$type" = "application/json" ] ;;
    *.whl)
      case "$type" in
        text/* | *html* | "") return 1 ;;
        application/octet-stream | application/zip | application/x-zip-compressed | *wheel*) return 0 ;;
        *) return 1 ;;
      esac
      ;;
    *.zip) [ "$type" = "application/zip" ] || [ "$type" = "application/x-zip-compressed" ] ;;
    *.html) [ "$type" = "text/html" ] ;;
    *.css) [ "$type" = "text/css" ] ;;
    *.svg) [ "$type" = "image/svg+xml" ] ;;
    *.woff2) [ "$type" = "font/woff2" ] ;;
    *.png) [ "$type" = "image/png" ] ;;
    *) return 0 ;;
  esac
}

fetch() {
  curl -sS --compressed --retry 2 -o "$2" -w "%{http_code}${tab}%{content_type}" "$1" || printf '000%s' "$tab"
}

cat > "$work/list.py" <<'PY'
import hashlib
import json
import sys

raw = open(sys.argv[1], "rb").read()
data = json.loads(raw)
for rel, digest in sorted(data["files"].items()):
    print(f"{rel}\t{digest}")
print(f"BUILD.json\t{hashlib.sha256(raw).hexdigest()}")
PY
cat > "$work/versions.py" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
entry = next((item for item in data.get("versions", []) if item.get("version") == sys.argv[2]), None)
if data.get("format") != "sarcostat-versions" or entry is None:
    raise SystemExit(f"versions.json does not list {sys.argv[2]}")
if entry.get("build_json_sha256") != sys.argv[3]:
    raise SystemExit(f"versions.json gives BUILD.json sha256 {entry.get('build_json_sha256')} for {sys.argv[2]}")
print(data["latest"])
PY
python3 -I "$work/list.py" "$build" > "$work/list.tsv"
build_sha="$(sha256sum "$build" | cut -d' ' -f1)"
echo "verify_deploy: v$version at $base/v$version/ against $build (BUILD.json sha256 $build_sha)"

while IFS="$tab" read -r rel expected; do
  case "$rel" in
    *[!A-Za-z0-9._/+-]*)
      fail "$rel: unexpected characters in the path"
      continue
      ;;
  esac
  result="$(fetch "$base/v$version/$rel" "$work/body")"
  status="${result%%"$tab"*}"
  header="${result#*"$tab"}"
  checked=$((checked + 1))
  if [ "$status" != "200" ]; then
    fail "$rel: HTTP $status"
    continue
  fi
  actual="$(sha256sum "$work/body" | cut -d' ' -f1)"
  if [ "$actual" != "$expected" ]; then
    fail "$rel: SHA-256 $actual, expected $expected"
    continue
  fi
  if ! media_ok "$rel" "$header"; then
    fail "$rel: media type '$header' is not allowed for this extension"
    continue
  fi
  echo "verify_deploy: ok $rel ($header)"
done < "$work/list.tsv"

result="$(fetch "$base/versions.json" "$work/versions.json")"
status="${result%%"$tab"*}"
header="${result#*"$tab"}"
latest=""
if [ "$status" != "200" ] || [ "$(media_type "$header")" != "application/json" ]; then
  fail "versions.json: HTTP $status, media type '$header'"
else
  if listing="$(python3 -I "$work/versions.py" "$work/versions.json" "$version" "$build_sha" 2>&1)"; then
    latest="$listing"
    echo "verify_deploy: ok versions.json lists v$version with this BUILD.json; latest is v$latest"
  else
    fail "versions.json: $listing"
  fi
fi

check_redirect() {
  local url=$1 target=$2 name=$3
  local result status header
  result="$(fetch "$url" "$work/redirect.html")"
  status="${result%%"$tab"*}"
  header="${result#*"$tab"}"
  if [ "$status" != "200" ] || [ "$(media_type "$header")" != "text/html" ]; then
    fail "$name: HTTP $status, media type '$header'"
  elif ! grep -qF "<meta name=\"sarcostat-redirect\" content=\"$target\">" "$work/redirect.html"; then
    fail "$name: not the redirect page to $target"
  else
    echo "verify_deploy: ok $name redirects to $target, keeping the hash"
  fi
}

check_redirect "$base/" "latest/" "root /"
if [ -n "$latest" ]; then
  check_redirect "$base/latest/" "../v$latest/" "/latest/"
fi

if [ "$failures" -gt 0 ]; then
  echo "verify_deploy: FAILED with $failures problem(s); $checked file(s) of v$version checked"
  exit 1
fi
echo "verify_deploy: PASS $checked file(s) of v$version equal to the local BUILD.json with allowed media types; root, /latest/ and versions.json as expected"
