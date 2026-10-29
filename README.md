# slack-incident-sections

Keeps [incident.io](https://incident.io) channels in the right section of your Slack sidebar:

- a new or reopened incident's channel moves to your **active** section;
- when the incident is resolved, closed, declined, canceled or merged, its channel moves to your
  **resolved** section.

A channel moves only when its incident changes state, so anything you place by hand stays where
you put it until the incident moves on. The first run only records where every incident stands.

## How it works

- **Status:** incident.io's bot posts every status change in the incident channel ("Incident
  created", "This incident has been resolved", "Status: ~Fixing~ → *Monitoring*"). The script reads
  those messages, so it needs no incident.io API key.
- **Sidebar:** Slack has no public API for sidebar sections. The script uses the internal
  endpoints Slack's web client calls (`users.channelSections.*`), authenticated with your browser
  session: an `xoxc-` token and the `d` cookie. These endpoints are undocumented and can change
  without notice.

It runs on macOS as a launchd job (every 5 minutes by default) with Python 3.11+ and no
dependencies.

## Setup

### 1. Get your Slack session credentials

1. Open <https://app.slack.com/client> in your browser, signed in to your workspace.
2. Open DevTools → **Network**, filter by `api/`, and click any request.
3. **Token:** in **Payload**, click **view source** and copy the `token` value (`xoxc-…`, up to the
   next `&`). The formatted view truncates long values with `…`; don't copy from there.
4. **Cookie:** right-click the same request → **Copy** → **Copy as cURL**. The `d` cookie is in it.

Store both in the macOS Keychain. The token prompts for the value, so it stays out of your shell
history:

```bash
security add-generic-password -U -a "$USER" -s slack-incident-sections-token -w
```

With the cURL command still in the clipboard, this extracts the cookie and stores it:

```bash
pbpaste | grep -oE "(^|[ ;'\"])d=xoxd-[^;'\" ]+" | head -1 | sed -E "s/^[ ;'\"]?d=//" | xargs -I{} security add-generic-password -U -a "$USER" -s slack-incident-sections-cookie -w {}
```

Then clear the clipboard with `pbcopy < /dev/null`.

On other systems, or to skip the Keychain, set `SLACK_XOXC_TOKEN` and `SLACK_D_COOKIE` instead.

The credentials are your browser session: they stop working when you sign out of Slack in that
browser. When the log shows `invalid_auth`, copy them again.

### 2. Configure

```bash
./install.sh   # first run creates ~/.config/slack-incident-sections/config.json
```

Edit the config, then check it against your workspace:

```bash
python3 slack_incident_sections.py --sections   # your sidebar sections, to copy their exact names
python3 slack_incident_sections.py --channels   # incident channels, their state and section
python3 slack_incident_sections.py --dry-run    # what a run would do
```

### 3. Install the job

```bash
./install.sh                         # every 5 minutes
INTERVAL_SECONDS=120 ./install.sh    # or another interval
./uninstall.sh                       # remove it
```

Log: `~/Library/Logs/slack-incident-sections/sync.log`. State:
`~/.local/state/slack-incident-sections/state.json`.

## Config

Every key is optional; missing ones take the defaults shown in
[`config.example.json`](config.example.json).

| Key | Meaning |
| --- | --- |
| `sections.active`, `sections.resolved` | Sidebar section names, exactly as Slack shows them (case-sensitive). |
| `channel_pattern` | Regex for incident channel names. incident.io's default is `inc-<number>-…`. |
| `statuses.active`, `statuses.resolved` | Your incident.io status names, by whether the incident still needs work. Add any custom statuses your organisation uses; the log warns about unknown ones. |
| `incident_app_ids`, `incident_bot_ids` | Which messages come from incident.io. The app id defaults to incident.io's public Slack app; add a `bot_id` if your workspace uses a custom bot. |
| `credentials.keychain_token_service`, `credentials.keychain_cookie_service` | Keychain service names for the token and cookie. |
| `max_resolved_per_run` | Safety brake: a run that would resolve more incidents than this stops instead (use `--force` if it's real). |
| `state_file` | Where the last known state per channel is stored. |

## Debugging

```bash
python3 slack_incident_sections.py --inspect C0123456789   # how each incident.io message in a channel is read
```

## Development

```bash
uv run --group dev pytest
uv run --group dev ruff check . && uv run --group dev ruff format --check .
uv run --group dev mypy
```
