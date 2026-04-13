#!/usr/bin/env bash
# AIDE .app launcher — provisions a venv with a modern arm64 Python (PyQt6
# only ships native Apple Silicon wheels for Python 3.11+; the system
# /usr/bin/python3 is 3.9 and would install x86_64 wheels that fail to load).
set -e
PROJECT_DIR="/Users/itay.glick/Documents/GAI Apps/nanoai"
VENV="$HOME/.aide/.venv"
PY="$VENV/bin/python"
STAMP="$VENV/.deps_installed_v3"
LOG="$HOME/.aide/app.log"
mkdir -p "$HOME/.aide"

# Pick the best available Python (newest first). On Apple Silicon we strongly
# prefer Homebrew's arm64 builds; fall back to whatever is in PATH last.
find_python() {
  local candidate
  for candidate in \
      /opt/homebrew/opt/python@3.14/bin/python3.14 \
      /opt/homebrew/opt/python@3.13/bin/python3.13 \
      /opt/homebrew/opt/python@3.12/bin/python3.12 \
      /opt/homebrew/opt/python@3.11/bin/python3.11 \
      /opt/homebrew/bin/python3.14 \
      /opt/homebrew/bin/python3.13 \
      /opt/homebrew/bin/python3.12 \
      /opt/homebrew/bin/python3.11 \
      /usr/local/bin/python3.14 \
      /usr/local/bin/python3.13 \
      /usr/local/bin/python3.12 \
      /usr/local/bin/python3.11 \
      "$(command -v python3.14 2>/dev/null || true)" \
      "$(command -v python3.13 2>/dev/null || true)" \
      "$(command -v python3.12 2>/dev/null || true)" \
      "$(command -v python3.11 2>/dev/null || true)"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
      echo "$candidate"; return 0
    fi
  done
  return 1
}

if [ ! -x "$PY" ]; then
  HOST_PY="$(find_python)"
  if [ -z "$HOST_PY" ]; then
    {
      echo "[AIDE] No suitable Python found (need 3.11+ for arm64 PyQt6 wheels)."
      echo "[AIDE] Install one with:  brew install python@3.14"
    } >> "$LOG"
    osascript -e 'display alert "AIDE cannot start" message "Python 3.11 or newer is required.\n\nInstall it with:\n\n    brew install python@3.14\n\nThen reopen AIDE." as critical' >/dev/null 2>&1 || true
    exit 1
  fi
  "$HOST_PY" -m venv "$VENV"
fi
if [ ! -f "$STAMP" ]; then
  "$PY" -m pip install --quiet --upgrade pip
  "$PY" -m pip install --quiet PyQt6 pyte PyQt6-WebEngine cryptography keyring pyobjc-framework-Cocoa
  touch "$STAMP"
fi

# QtWebEngineProcess lives deep inside the venv's site-packages tree. When
# Python is launched from a .app bundle Qt looks in the wrong places, so we
# locate the binary and pass its path via env var before starting Python.
QWEP="$(find "$VENV" -name "QtWebEngineProcess" 2>/dev/null | head -1)"
[ -n "$QWEP" ] && [ -x "$QWEP" ] && export QTWEBENGINEPROCESS_PATH="$QWEP"

# Re-sign with ad-hoc identity if signature is missing or broken.
# This ensures macOS remembers granted permissions across updates.
APP_BUNDLE="$(dirname "$(dirname "$DIR")")"
if ! codesign --verify --quiet "$APP_BUNDLE" 2>/dev/null; then
  codesign --force --deep --sign - "$APP_BUNDLE" >> "$LOG" 2>&1 || true
fi

exec "$PY" "$PROJECT_DIR/AIDE.py" "$@" >> "$LOG" 2>&1
