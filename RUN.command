#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
GAME="$HOME/Library/Application Support/Steam/steamapps/common/StarBreak"
MACOS="$GAME/MVMMOClient.app/Contents/MacOS"
EXE="$MACOS/mvmmoclient"
VENV="$ROOT/.sbpe-x86-env"
REMOTE="$ROOT/build/remote.bin"
SYMFILE="$ROOT/shell_symbols.json"

if [ ! -x "$EXE" ]; then
  echo "ERROR: StarBreak not found."
  exit 1
fi

if [ ! -f "$REMOTE" ]; then
  echo "ERROR: remote.bin has not been built yet."
  echo "Run BUILD.command first."
  exit 1
fi

if [ ! -x "$VENV/bin/python3" ]; then
  echo "ERROR: local Python environment is missing."
  echo "Run BUILD.command first."
  exit 1
fi

if [ ! -f "$SYMFILE" ]; then
  echo "ERROR: shell_symbols.json is missing."
  exit 1
fi

CFFI_SITE="$(
  "$VENV/bin/python3" - <<'PY'
import site
print(site.getsitepackages()[0])
PY
)"

cd "$MACOS"

exec env \
  PYTHONPATH="$CFFI_SITE" \
  SBPE_SYMFILE="$SYMFILE" \
  DYLD_INSERT_LIBRARIES="$REMOTE" \
  DYLD_FORCE_FLAT_NAMESPACE=1 \
  ./mvmmoclient
