#!/usr/bin/env bash
# Install the launchd job that runs slack-incident-sections every 5 minutes.
set -euo pipefail

label="local.slack-incident-sections"
repo="$(cd "$(dirname "$0")" && pwd)"
python="$(command -v python3)"
config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/slack-incident-sections"
log_dir="$HOME/Library/Logs/slack-incident-sections"
plist="$HOME/Library/LaunchAgents/$label.plist"
interval="${INTERVAL_SECONDS:-300}"

if ! "$python" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
  echo "$python is older than Python 3.11; put a newer python3 first on PATH." >&2
  exit 1
fi

mkdir -p "$config_dir" "$log_dir" "$(dirname "$plist")"
if [[ ! -f "$config_dir/config.json" ]]; then
  cp "$repo/config.example.json" "$config_dir/config.json"
  echo "Created $config_dir/config.json; edit it, then rerun this script."
  exit 0
fi

cat >"$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$label</string>
    <key>ProgramArguments</key>
    <array>
        <string>$python</string>
        <string>$repo/slack_incident_sections.py</string>
        <string>--config</string>
        <string>$config_dir/config.json</string>
    </array>
    <key>StartInterval</key>
    <integer>$interval</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>ProcessType</key>
    <string>Background</string>
    <key>StandardOutPath</key>
    <string>$log_dir/sync.log</string>
    <key>StandardErrorPath</key>
    <string>$log_dir/sync.log</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$plist"
echo "Installed $label (every ${interval}s). Log: $log_dir/sync.log"
