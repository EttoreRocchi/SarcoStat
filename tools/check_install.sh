#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "usage: tools/check_install.sh <wheel> [work dir]" >&2
  echo "Installs the wheel in fresh core-only, [cli] and [sim] venvs and checks each one." >&2
  echo "PYTHON selects the interpreter (default python3); SMOKE_CONFIG runs 'sim sarc' in [sim]." >&2
  exit 2
fi

wheel=$(realpath "$1")
work=${2:-$(mktemp -d)}
python=${PYTHON:-python3}
mkdir -p "$work"
work=$(realpath "$work")
heavy="pingouin matplotlib pandas seaborn typer rich yaml PIL tifffile"
lean_modules="sarcostat.core sarcostat.io sarcostat.io.annotations sarcostat.io.provenance sarcostat.io.study"

fail() {
  echo "check_install: FAIL: $*" >&2
  exit 1
}

pass() {
  echo "check_install: ok: $*"
}

make_venv() {
  rm -rf "${work:?}/$1"
  "$python" -m venv "$work/$1"
  "$work/$1/bin/python" -m pip install --quiet --disable-pip-version-check "$2"
}

distributions() {
  "$work/$1/bin/python" -m pip list --format=freeze --disable-pip-version-check \
    | cut -d= -f1 | tr 'A-Z_' 'a-z-' | grep -vxE 'pip|setuptools|wheel|sarcostat' | sort | tr '\n' ' '
}

not_loaded() {
  local venv=$1 statement=$2
  shift 2
  "$work/$venv/bin/python" - "$statement" "$@" <<'PY'
import sys
statement, banned = sys.argv[1], set(sys.argv[2:])
exec(statement)
loaded = {name.split(".")[0] for name in sys.modules} & banned
if loaded:
    sys.exit(f"loaded: {sorted(loaded)}")
PY
}

run() {
  local status=0
  output=$("$@" 2>&1) || status=$?
  return "$status"
}

echo "check_install: wheel $wheel"
echo "check_install: work dir $work"
"$python" --version

make_venv core "$wheel"
dists=$(distributions core)
[ "$dists" = "numpy scipy " ] || fail "core install pulls in: $dists"
pass "core install: numpy and scipy only"
not_loaded core "import $(echo "$lean_modules" | tr ' ' ',')" $heavy scipy \
  || fail "core and io imports are not lean"
pass "core and io load numpy only"
run "$work/core/bin/sarcostat" --version || fail "sarcostat --version: $output"
version=$("$work/core/bin/python" -c "import sarcostat; print(sarcostat.__version__)")
[ "$output" = "sarcostat $version" ] || fail "sarcostat --version printed: $output"
pass "core: $output"
if run "$work/core/bin/sarcostat" analyze x; then fail "core: analyze exited 0"; fi
[[ "$output" == *'pip install "sarcostat[cli]"'* ]] || fail "core: analyze printed: $output"
pass "core: analyze names [cli] and exits 1"
if run "$work/core/bin/sarcostat" sim chain; then fail "core: sim exited 0"; fi
[[ "$output" == *'pip install "sarcostat[sim]"'* ]] || fail "core: sim printed: $output"
pass "core: sim names [sim] and exits 1"
"$work/core/bin/python" - "$wheel" <<'PY' || fail "wheel METADATA lacks an extra"
import sys
import zipfile

with zipfile.ZipFile(sys.argv[1]) as archive:
    name = next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
    lines = archive.read(name).decode().splitlines()
extras = {line.split(":", 1)[1].strip() for line in lines if line.startswith("Provides-Extra:")}
missing = {"cli", "sim", "dev"} - extras
if missing:
    sys.exit(f"missing Provides-Extra: {sorted(missing)}")
print("check_install: ok: Provides-Extra " + ", ".join(sorted(extras)))
PY

make_venv cli "$wheel[cli]"
dists=$(distributions cli)
for name in pandas matplotlib seaborn pyyaml pingouin pillow tifffile; do
  [[ " $dists" != *" $name "* ]] || fail "[cli] install pulls in $name"
done
pass "[cli] install: $dists"
run "$work/cli/bin/sarcostat" --help || fail "[cli]: --help: $output"
pass "[cli]: sarcostat --help exits 0"
not_loaded cli "import sarcostat.cli.app" pingouin matplotlib pandas seaborn yaml PIL tifffile \
  || fail "[cli]: sarcostat.cli.app is not lean"
pass "[cli]: the Typer application loads without the simulation stack"
if run "$work/cli/bin/sarcostat" sim chain; then fail "[cli]: sim exited 0"; fi
[[ "$output" == *'pip install "sarcostat[sim]"'* ]] || fail "[cli]: sim printed: $output"
pass "[cli]: sim names [sim] and exits 1"

make_venv sim "$wheel[sim]"
run "$work/sim/bin/sarcostat" sim --help || fail "[sim]: sim --help: $output"
pass "[sim]: sarcostat sim --help exits 0"
if [ -n "${SMOKE_CONFIG:-}" ]; then
  config=$(realpath "$SMOKE_CONFIG")
  mkdir -p "$work/smoke"
  (cd "$work/smoke" && env -u DISPLAY MPLBACKEND=Agg "$work/sim/bin/sarcostat" sim sarc --config "$config") \
    || fail "[sim]: smoke run failed"
  pass "[sim]: smoke run of $config"
fi

echo "check_install: all checks passed"
