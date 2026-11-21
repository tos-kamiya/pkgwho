#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PY_VERSIONS=(3.9 3.10 3.11 3.12 3.13 3.14 3.15)

if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  echo 'Usage: scripts/run_python_matrix.sh'
  echo 'Run all pytest tests on CPython 3.9 through 3.15 using uv.'
  echo 'Creates or updates .venv-matrix/ environments and installs .[dev].'
  exit 0
fi
if [[ $# -ne 0 ]]; then
  echo "Unknown argument: $1" >&2
  exit 2
fi

for version in "${PY_VERSIONS[@]}"; do
  venv=".venv-matrix/$version"
  echo "==> Preparing CPython $version"
  if [[ ! -x "$venv/bin/python" ]]; then
    uv venv --python "cpython-$version" "$venv"
  fi
  uv pip install --python "$venv/bin/python" -e '.[dev]'
  echo "==> Testing CPython $version"
  "$venv/bin/python" -m pytest
done

echo 'All Python matrix tests passed.'
