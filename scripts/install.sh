#!/usr/bin/env bash
# Install the job that runs slack-incident-sections every 5 minutes: launchd on macOS, a systemd
# user timer on Linux. On Windows, use scripts\install.ps1.
set -euo pipefail

name="slack-incident-sections"
label="local.$name"
repo="$(cd "$(dirname "$0")/.." && pwd)"
python="$(command -v python3)"
config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/$name"
interval="${INTERVAL_SECONDS:-300}"

if ! "$python" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
  echo "$python is older than Python 3.11; put a newer python3 first on PATH." >&2
  exit 1
fi

mkdir -p "$config_dir"
if [[ ! -f "$config_dir/config.json" ]]; then
  cp "$repo/config.example.json" "$config_dir/config.json"
  echo "Created $config_dir/config.json; edit it, then rerun this script."
  exit 0
fi

install_launchd() {
  local log_dir="$HOME/Library/Logs/$name"
  local plist="$HOME/Library/LaunchAgents/$label.plist"
  mkdir -p "$log_dir" "$(dirname "$plist")"
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
}

install_systemd() {
  local unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
  local log_dir="${XDG_STATE_HOME:-$HOME/.local/state}/$name"
  mkdir -p "$unit_dir" "$log_dir"
  cat >"$unit_dir/$name.service" <<EOF
[Unit]
Description=Sort incident.io channels into Slack sidebar sections

[Service]
Type=oneshot
ExecStart="$python" "$repo/slack_incident_sections.py" --config "$config_dir/config.json" --log-file "$log_dir/sync.log"
EOF
  cat >"$unit_dir/$name.timer" <<EOF
[Unit]
Description=Run $name every ${interval}s

[Timer]
OnActiveSec=0
OnUnitActiveSec=${interval}s

[Install]
WantedBy=timers.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable "$name.timer"
  systemctl --user restart "$name.timer"
  echo "Installed $name.timer (every ${interval}s). Log: $log_dir/sync.log"
  if [[ "$(loginctl show-user "$USER" --property=Linger --value 2>/dev/null)" != "yes" ]]; then
    echo "It runs only while you're logged in; 'loginctl enable-linger' keeps it running after logout."
  fi
}

case "$(uname -s)" in
  Darwin) install_launchd ;;
  Linux)
    if ! systemctl --user show-environment >/dev/null 2>&1; then
      minutes=$((interval / 60 > 0 ? interval / 60 : 1))
      if ((minutes < 60)); then
        schedule="*/$minutes * * * *" exact=$((interval % 60 == 0 && 60 % minutes == 0))
      else
        schedule="0 */$((minutes / 60)) * * *" exact=$((interval % 3600 == 0 && 24 % (minutes / 60) == 0))
      fi
      if ((!exact)); then
        echo "Cron can't run every ${interval}s; pick a divisor of 60 minutes or 24 hours, or adjust the line." >&2
      fi
      log_dir="${XDG_STATE_HOME:-$HOME/.local/state}/$name"
      echo "No systemd user session. Add this line with 'crontab -e' instead:" >&2
      echo "$schedule \"$python\" \"$repo/slack_incident_sections.py\" --config \"$config_dir/config.json\" --log-file \"$log_dir/sync.log\"" >&2
      echo "Cron jobs can't reach the Secret Service; use the 'file' or 'command' credential store." >&2
      exit 1
    fi
    install_systemd
    ;;
  *)
    echo "Unsupported system $(uname -s); on Windows, run scripts\\install.ps1." >&2
    exit 1
    ;;
esac
