#!/bin/bash
set -euo pipefail

REPO="Scarf-0/PaperQuick"
APP_DIR="$HOME/Library/Application Support/PaperQuick"
SERVICES_DIR="$HOME/Library/Services"
TMP_DIR=""

cleanup() {
  if [ -n "$TMP_DIR" ] && [ -d "$TMP_DIR" ]; then
    rm -rf "$TMP_DIR"
  fi
}
trap cleanup EXIT

if [ "$(uname -s)" != "Darwin" ]; then
  echo "PaperQuick supports macOS only." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3.9+ is required. Install it with: brew install python" >&2
  exit 1
fi

if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 9))'; then
  echo "Python 3.9+ is required. Install it with: brew install python" >&2
  exit 1
fi

SCRIPT_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/pyproject.toml" ]; then
  SOURCE_DIR="$SCRIPT_DIR"
else
  TMP_DIR="$(mktemp -d)"
  curl -fsSL "https://github.com/$REPO/archive/refs/heads/main.tar.gz" \
    | tar -xz -C "$TMP_DIR"
  SOURCE_DIR="$TMP_DIR/PaperQuick-main"
fi

mkdir -p "$APP_DIR" "$SERVICES_DIR"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/python" -m pip install --quiet --upgrade pip
"$APP_DIR/venv/bin/python" -m pip install --quiet --upgrade "$SOURCE_DIR"

for workflow in "PaperQuick - Rename PDF.workflow" "PaperQuick - Copy PPT Citation.workflow"; do
  destination="$SERVICES_DIR/$workflow"
  if [ -e "$destination" ]; then
    rm -rf "$destination"
  fi
  cp -R "$SOURCE_DIR/workflows/$workflow" "$destination"
  mkdir -p "$destination/Contents/Resources"
  cp "$destination/Contents/document.wflow" \
    "$destination/Contents/Resources/document.wflow"
done

cp "$SOURCE_DIR/uninstall.sh" "$APP_DIR/uninstall.sh"
chmod +x "$APP_DIR/uninstall.sh"
/System/Library/CoreServices/pbs -flush >/dev/null 2>&1 || true
/System/Library/CoreServices/pbs -update >/dev/null 2>&1 || true
/usr/bin/killall pbs >/dev/null 2>&1 || true
/usr/bin/killall Finder >/dev/null 2>&1 || true

echo "PaperQuick installed. Select PDFs in Finder, right-click, and open Services."

