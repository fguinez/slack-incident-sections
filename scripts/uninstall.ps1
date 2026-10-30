# Remove the Task Scheduler job. Config, state and stored credentials are left in place.
$name = "slack-incident-sections"
Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "Removed $name."
