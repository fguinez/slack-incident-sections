#!/usr/bin/env bash
# Remove the launchd job. Config, state and Keychain items are left in place.
set -euo pipefail

label="local.slack-incident-sections"
launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$label.plist"
echo "Removed $label."
