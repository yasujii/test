#!/bin/bash
set -euo pipefail
umask 077
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ROOT="$PWD"
if [ ! -x .venv/bin/python ]; then
  [ ! -e .venv ] || { echo 'Existing .venv is unusable; not deleting it.' >&2; exit 2; }
  PY=""
  for candidate in python3.13 python3.12 python3.11 /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
    resolved="$(command -v "$candidate" 2>/dev/null || true)"
    [ -n "$resolved" ] || continue
    if [ "$(uname -s)" = Darwin ] && [ "$resolved" = /usr/bin/python3 ]; then continue; fi
    if "$resolved" -c 'import sys,venv; assert (3,11)<=sys.version_info[:2]<(3,15)' >/dev/null 2>&1; then PY="$resolved"; break; fi
  done
  if [ -n "$PY" ]; then "$PY" -m venv .venv
  else
    mkdir -p .local-tools
    curl --proto '=https' --tlsv1.2 -fLsS --connect-timeout 15 --max-time 120 \
      https://astral.sh/uv/0.12.18/install.sh -o .local-tools/uv-installer.sh
    env UV_UNMANAGED_INSTALL="$ROOT/.local-tools" sh .local-tools/uv-installer.sh
    export UV_PYTHON_INSTALL_DIR="$ROOT/.runtimes" UV_CACHE_DIR="$ROOT/.uv-cache"
    .local-tools/uv python install 3.12
    .local-tools/uv venv --seed --python 3.12 .venv
  fi
fi
.venv/bin/python -m pip install --disable-pip-version-check --index-url https://pypi.org/simple -r requirements.txt
.venv/bin/python -m pip check
.venv/bin/python -m unittest discover -s tests -v
mkdir -p reports
.venv/bin/python collector.py doctor > reports/environment.json
.venv/bin/python -m pip freeze > reports/installed-versions.txt
printf '\nSetup and unit tests complete. Live collection is a separate verified stage.\n'
