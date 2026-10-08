#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
GAME="$HOME/Library/Application Support/Steam/steamapps/common/StarBreak"
MACOS="$GAME/MVMMOClient.app/Contents/MacOS"
EXE="$MACOS/mvmmoclient"
SDL="$MACOS/libSDL2-2.0.0.dylib"
VENV="$ROOT/.sbpe-x86-env"

echo "=== squatmastr78 Mac SBPE build ==="

if [ ! -x "$EXE" ]; then
  echo "ERROR: StarBreak was not found at:"
  echo "  $EXE"
  exit 1
fi

if [ ! -f "$SDL" ]; then
  echo "ERROR: StarBreak SDL library was not found at:"
  echo "  $SDL"
  exit 1
fi

if ! xcode-select -p >/dev/null 2>&1; then
  echo "ERROR: Apple Command Line Tools are required."
  echo "Run: xcode-select --install"
  exit 1
fi

PYTHON=""
for p in \
  /usr/bin/python3 \
  /Library/Developer/CommandLineTools/usr/bin/python3 \
  /Applications/Xcode.app/Contents/Developer/usr/bin/python3
do
  if [ -x "$p" ]; then
    if arch -x86_64 "$p" -c 'import platform; assert platform.machine() == "x86_64"' >/dev/null 2>&1; then
      PYTHON="$p"
      break
    fi
  fi
done

if [ -z "$PYTHON" ]; then
  echo "ERROR: Could not find an x86_64-capable Apple Python 3."
  echo "Install/update Apple Command Line Tools and try again."
  exit 1
fi

if [ ! -x "$VENV/bin/python3" ]; then
  echo "Creating local x86_64 Python environment..."
  arch -x86_64 "$PYTHON" -m venv "$VENV"
fi

echo "Installing/updating cffi..."
arch -x86_64 "$VENV/bin/python3" -m pip install --upgrade pip cffi

cd "$ROOT"
rm -rf build

echo "Building x86_64 remote.bin..."
ARCHFLAGS="-arch x86_64" \
CFLAGS="-arch x86_64" \
LDFLAGS="-arch x86_64" \
arch -x86_64 "$VENV/bin/python3" builder.py

if [ ! -f "$ROOT/build/remote.bin" ]; then
  echo "ERROR: build/remote.bin was not produced."
  exit 1
fi

echo "Patching SDL path for this Mac..."
install_name_tool \
  -change "@loader_path/libSDL2-2.0.0.dylib" \
  "$SDL" \
  "$ROOT/build/remote.bin" || true

PY_DEP="$(
  otool -L "$ROOT/build/remote.bin" |
  awk '/Python3\.framework\/Versions\// {print $1; exit}'
)"

if [ -n "$PY_DEP" ]; then
  PY_VER="$(
    printf '%s\n' "$PY_DEP" |
    sed -E 's#.*Python3\.framework/Versions/([^/]+)/Python3#\1#'
  )"

  PYFRAME=""
  for candidate in \
    "/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/$PY_VER/Python3" \
    "/Applications/Xcode.app/Contents/Developer/Library/Frameworks/Python3.framework/Versions/$PY_VER/Python3"
  do
    if [ -f "$candidate" ]; then
      PYFRAME="$candidate"
      break
    fi
  done

  if [ -n "$PYFRAME" ]; then
    echo "Patching Python framework for this Mac..."
    install_name_tool \
      -change "$PY_DEP" \
      "$PYFRAME" \
      "$ROOT/build/remote.bin"
  else
    echo "WARNING: Could not locate a matching Python3.framework for:"
    echo "  $PY_DEP"
    echo "The build completed, but RUN.command may fail until this is patched."
  fi
fi

echo
file "$ROOT/build/remote.bin"
echo
echo "BUILD COMPLETE."
echo "Now run RUN.command."
