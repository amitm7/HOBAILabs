#!/bin/bash
# S34 — run the shoot batch unattended.
#
# Installs a launchd agent that scans the inbox every N minutes and shoots any SKU folder it
# has not already done. The operator never runs a command: they drop a folder into the inbox
# (a local path, or a mounted Google Drive folder) and finished images appear beside it.
#
#   tools/shoot_install_schedule.sh ~/Desktop/PhotoShoot           # every 30 min
#   tools/shoot_install_schedule.sh ~/Desktop/PhotoShoot 900       # every 15 min
#   tools/shoot_install_schedule.sh --uninstall
#
# Logs: ~/.hob_cache/shoot_watch.log   ·   Ledger keeps finished SKUs, so a re-scan is cheap.
set -euo pipefail

LABEL="ai.kevat.shoot-batch"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$HOME/.pyenv/versions/3.12.3/bin/python3.12"
LOG="$HOME/.hob_cache/shoot_watch.log"

if [[ "${1:-}" == "--uninstall" ]]; then
  launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
  rm -f "$PLIST"
  echo "uninstalled ${LABEL}"
  exit 0
fi

INBOX="${1:?usage: shoot_install_schedule.sh <inbox-folder> [interval-seconds] }"
INTERVAL="${2:-1800}"
INBOX="$(cd "$INBOX" && pwd)"          # absolute — launchd has no shell expansion
[[ -x "$PY" ]] || { echo "interpreter not found: $PY" >&2; exit 1; }
mkdir -p "$HOME/.hob_cache" "$HOME/Library/LaunchAgents"

# --cap-run is deliberate: an unattended job that can spend without limit is a bug, not a
# feature. It stops cleanly and the next tick resumes from the ledger.
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PY}</string>
    <string>${REPO}/tools/shoot_batch.py</string>
    <string>--inbox</string><string>${INBOX}</string>
    <string>--go</string>
    <string>--cap-run</string><string>20.00</string>
  </array>
  <key>WorkingDirectory</key><string>${REPO}</string>
  <key>StartInterval</key><integer>${INTERVAL}</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>${LOG}</string>
  <key>StandardErrorPath</key><string>${LOG}</string>
  <key>ProcessType</key><string>Background</string>
</dict>
</plist>
PLIST_EOF

launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo "installed ${LABEL}"
echo "  inbox    : ${INBOX}"
echo "  every    : ${INTERVAL}s"
echo "  cap      : \$20.00 per tick (stops cleanly, resumes next tick)"
echo "  log      : ${LOG}"
echo
echo "Drop SKU folders into the inbox — images appear in <SKU>/photoshoot/."
echo "Status:  ${PY} ${REPO}/tools/shoot_batch.py --inbox ${INBOX} --status"
echo "Stop:    $0 --uninstall"
