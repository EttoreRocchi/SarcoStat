#!/usr/bin/env bash
set -euo pipefail

web_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
sum_file="$web_dir/node-dist.sha256"
version="$(tr -d '[:space:]' < "$web_dir/.nvmrc")"
toolchain="$web_dir/.cache/toolchain"
node_dir="$toolchain/node"
stamp="$node_dir/.sha256"
local_tarball="${1:-}"

read -r expected tarball_name < "$sum_file"
if [ "$tarball_name" != "node-v$version-linux-x64.tar.xz" ]; then
  echo "fetch_node: $sum_file names $tarball_name, expected node-v$version-linux-x64.tar.xz" >&2
  exit 1
fi
if [ "${#expected}" -ne 64 ]; then
  echo "fetch_node: $sum_file does not hold a SHA-256" >&2
  exit 1
fi
if [ "$(uname -s)" != "Linux" ] || [ "$(uname -m)" != "x86_64" ]; then
  echo "fetch_node: only linux-x64 is supported, this is $(uname -s) $(uname -m)" >&2
  exit 1
fi

installed() {
  [ -x "$node_dir/bin/node" ] && [ -f "$stamp" ] \
    && [ "$(cat "$stamp")" = "$expected" ] \
    && [ "$("$node_dir/bin/node" --version)" = "v$version" ]
}

if [ -z "$local_tarball" ] && installed; then
  echo "fetch_node: Node v$version already present and verified in $node_dir"
  exit 0
fi

mkdir -p "$toolchain"
tmp="$(mktemp "$toolchain/.$tarball_name.XXXXXX")"
staging=""
cleanup() {
  rm -f "$tmp"
  if [ -n "$staging" ]; then
    rm -rf "$staging"
  fi
}
trap cleanup EXIT

if [ -n "$local_tarball" ]; then
  echo "fetch_node: using local tarball $local_tarball"
  cp "$local_tarball" "$tmp"
elif [ -f "$toolchain/$tarball_name" ]; then
  echo "fetch_node: using cached $toolchain/$tarball_name"
  cp "$toolchain/$tarball_name" "$tmp"
else
  url="https://nodejs.org/dist/v$version/$tarball_name"
  echo "fetch_node: downloading $url"
  curl -fsSL --proto '=https' --tlsv1.2 --retry 3 -o "$tmp" "$url"
fi

actual="$(sha256sum "$tmp" | cut -d ' ' -f 1)"
if [ "$actual" != "$expected" ]; then
  rm -f "$tmp"
  if [ -z "$local_tarball" ]; then
    rm -f "$toolchain/$tarball_name"
  fi
  echo "fetch_node: SHA-256 mismatch for $tarball_name: expected $expected, got $actual; file deleted, refusing to install" >&2
  exit 1
fi
echo "fetch_node: SHA-256 verified $actual  $tarball_name"
chmod 644 "$tmp"
mv -f "$tmp" "$toolchain/$tarball_name"

if installed; then
  echo "fetch_node: Node v$version already present and verified in $node_dir"
  exit 0
fi

staging="$(mktemp -d "$toolchain/.node.XXXXXX")"
tar -xJf "$toolchain/$tarball_name" -C "$staging" --strip-components=1 --no-same-owner
got="$("$staging/bin/node" --version)"
if [ "$got" != "v$version" ]; then
  echo "fetch_node: unpacked node reports $got, expected v$version" >&2
  exit 1
fi
printf '%s\n' "$expected" > "$staging/.sha256"
chmod 755 "$staging"
rm -rf "$node_dir"
mv "$staging" "$node_dir"
staging=""
echo "fetch_node: installed Node $got in $node_dir"
