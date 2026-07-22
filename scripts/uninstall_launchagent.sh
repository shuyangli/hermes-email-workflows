#!/bin/bash
set -euo pipefail
LABEL="com.shuyang.hermes-email-workflows"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$PLIST"
echo "Removed $LABEL (application data was preserved)"
