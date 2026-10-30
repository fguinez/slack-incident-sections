#!/usr/bin/env bash
# Remove the scheduled job. Config, state and stored credentials are left in place.
set -euo pipefail

name="slack-incident-sections"
label="local.$name"
case "$(uname -s)" in
  Darwin)
    launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
    rm -f "$HOME/Library/LaunchAgents/$label.plist"
    echo "Removed $label."
    ;;
  Linux)
    unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
    systemctl --user disable --now "$name.timer" 2>/dev/null || true
    rm -f "$unit_dir/$name.service" "$unit_dir/$name.timer"
    systemctl --user daemon-reload 2>/dev/null || true
    echo "Removed $name.timer."
    ;;
  *)
    echo "Unsupported system $(uname -s); on Windows, run uninstall.ps1." >&2
    exit 1
    ;;
esac
