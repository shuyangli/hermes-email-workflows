#!/bin/bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.shuyang.hermes-email-workflows"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/hermes-email-workflows"
ENTRY="$REPO_DIR/.venv/bin/hermes-email-workflows"

if [[ ! -x "$ENTRY" ]]; then
  echo "Missing executable: $ENTRY" >&2
  echo "Install the project into .venv before enabling launchd." >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"
PLIST="$PLIST" LABEL="$LABEL" ENTRY="$ENTRY" REPO_DIR="$REPO_DIR" LOG_DIR="$LOG_DIR" \
HOME="$HOME" python3 -c '
import os
import plistlib
from pathlib import Path
payload = {
    "Label": os.environ["LABEL"],
    "ProgramArguments": [os.environ["ENTRY"]],
    "WorkingDirectory": os.environ["REPO_DIR"],
    "RunAtLoad": True,
    "KeepAlive": True,
    "ThrottleInterval": 30,
    "ProcessType": "Background",
    "StandardOutPath": str(Path(os.environ["LOG_DIR"]) / "stdout.log"),
    "StandardErrorPath": str(Path(os.environ["LOG_DIR"]) / "stderr.log"),
    "EnvironmentVariables": {
        "HOME": os.environ["HOME"],
        "PATH": os.environ["HOME"] + "/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        "PYTHONUNBUFFERED": "1",
    },
}
for name in ("HEW_PORT", "GOOGLE_APPLICATION_CREDENTIALS"):
    if os.environ.get(name):
        payload["EnvironmentVariables"][name] = os.environ[name]
with open(os.environ["PLIST"], "wb") as handle:
    plistlib.dump(payload, handle)
'
chmod 600 "$PLIST"
plutil -lint "$PLIST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/$LABEL"
echo "Installed and started $LABEL"
echo "Dashboard: http://127.0.0.1:${HEW_PORT:-8787}"
