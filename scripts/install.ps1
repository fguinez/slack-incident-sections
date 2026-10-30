# Install the Task Scheduler job that runs slack-incident-sections every 5 minutes on Windows.
# Usage: powershell -ExecutionPolicy Bypass -File scripts\install.ps1 [-IntervalSeconds 300]
param([int]$IntervalSeconds = 300)
$ErrorActionPreference = "Stop"
if ($IntervalSeconds -lt 60) { throw "Task Scheduler repeats at most once a minute; use 60 seconds or more." }

$name = "slack-incident-sections"
$repo = Split-Path -Parent $PSScriptRoot
$configHome = if ($env:XDG_CONFIG_HOME) { $env:XDG_CONFIG_HOME } else { Join-Path $HOME ".config" }
$configDir = Join-Path $configHome $name
$logFile = Join-Path $HOME ".local\state\$name\sync.log"

$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) { throw "python not found on PATH; install Python 3.11+ from python.org." }
& $python -c "import sys; sys.exit(sys.version_info < (3, 11))"
if ($LASTEXITCODE -ne 0) { throw "$python is older than Python 3.11." }
# pythonw runs without opening a console window every few minutes.
$pythonw = & $python -c "import sys, pathlib; print(pathlib.Path(sys.executable).with_name('pythonw.exe'))"
if (-not (Test-Path $pythonw)) { $pythonw = $python }

New-Item -ItemType Directory -Force -Path $configDir | Out-Null
$config = Join-Path $configDir "config.json"
if (-not (Test-Path $config)) {
    Copy-Item (Join-Path $repo "config.example.json") $config
    Write-Host "Created $config; edit it, then rerun this script."
    exit 0
}

$arguments = "`"$repo\slack_incident_sections.py`" --config `"$config`" --log-file `"$logFile`""
$action = New-ScheduledTaskAction -Execute $pythonw -Argument $arguments -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(10) -RepetitionInterval (New-TimeSpan -Seconds $IntervalSeconds)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Description "Sort incident.io channels into Slack sidebar sections" -Force | Out-Null
Write-Host "Installed $name (every ${IntervalSeconds}s, while you're logged in). Log: $logFile"
