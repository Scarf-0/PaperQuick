#!/bin/bash
set -euo pipefail

APP_DIR="$HOME/Library/Application Support/PaperQuick"
RENAME_WORKFLOW="$HOME/Library/Services/PaperQuick - Rename PDF.workflow"
CITE_WORKFLOW="$HOME/Library/Services/PaperQuick - Copy PPT Citation.workflow"
LOG_FILE="$HOME/Library/Logs/PaperQuick.log"

case "$APP_DIR" in
  "$HOME/Library/Application Support/PaperQuick") ;;
  *) echo "Unexpected install path; refusing to uninstall." >&2; exit 1 ;;
esac

rm -rf "$APP_DIR" "$RENAME_WORKFLOW" "$CITE_WORKFLOW"
rm -f "$LOG_FILE"
/System/Library/CoreServices/pbs -flush >/dev/null 2>&1 || true
/System/Library/CoreServices/pbs -update >/dev/null 2>&1 || true
/usr/bin/killall pbs >/dev/null 2>&1 || true
/usr/bin/killall Finder >/dev/null 2>&1 || true
echo "PaperQuick uninstalled. Your PDFs were not changed."

