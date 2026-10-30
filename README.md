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

It runs every 5 minutes by default, as a launchd job on macOS, a systemd user timer on Linux or a
Task Scheduler task on Windows, with Python 3.11+ and no dependencies.

## Setup

### 1. Get your Slack session credentials

1. Open <https://app.slack.com/client> in your browser, signed in to your workspace.
2. Open DevTools → **Network**, filter by `api/`, and click any request.
3. **Token:** in **Payload**, click **view source** and copy the `token` value (`xoxc-…`, up to the
   next `&`). The formatted view truncates long values with `…`; don't copy from there.
4. **Cookie:** right-click the same request → **Copy** → **Copy as cURL**. The `d` cookie is in it:
   its value is the `xoxd-…` right after `d=`, up to the next `;`. On Windows pick **Copy as cURL
   (bash)**. Or skip the cURL: **Application** → **Cookies** → `https://app.slack.com` → the `d`
   row.

You need both: Slack rejects an `xoxc-` token sent without its `d` cookie.

Together they give full access to your Slack account, like being signed in. Don't share them,
commit them or paste them anywhere else.

Store both where `credentials.store` in the config says (see [Credential stores](#credential-stores)).
The default, `auto`, uses your OS's own secret store: the Keychain on macOS, the Secret Service
(GNOME Keyring, KWallet) on Linux, the Credential Manager on Windows.

**macOS Keychain.** The token prompts for the value, so it stays out of your shell history:

```bash
security add-generic-password -U -a "$USER" -s slack-incident-sections-token -w
```

With the cURL command still in the clipboard, this extracts the cookie and stores it:

```bash
pbpaste | grep -oE "(^|[ ;'\"])d=xoxd-[^;'\" ]+" | head -1 | sed -E "s/^[ ;'\"]?d=//" | xargs -I{} security add-generic-password -U -a "$USER" -s slack-incident-sections-cookie -w {}
```

Then clear the clipboard with `pbcopy < /dev/null`.

**Linux Secret Service** (needs `secret-tool`, from `libsecret-tools` or `libsecret`). Each command
prompts for the value:

```bash
secret-tool store --label="Slack token" service slack-incident-sections-token
secret-tool store --label="Slack cookie" service slack-incident-sections-cookie
```

**Windows Credential Manager.** `/pass` with no value prompts for it:

```powershell
cmdkey /generic:slack-incident-sections-token /user:slack /pass
cmdkey /generic:slack-incident-sections-cookie /user:slack /pass
```

**Anywhere:** set `SLACK_XOXC_TOKEN` and `SLACK_D_COOKIE`; they take precedence over any store.

The credentials are your browser session: they stop working when you sign out of Slack in that
browser. When the log shows `invalid_auth`, copy them again.

### 2. Configure

```bash
make install   # first run creates ~/.config/slack-incident-sections/config.json
```

On Windows, use `python` in place of `python3` below.

Edit the config, then check it against your workspace:

```bash
python3 slack_incident_sections.py --sections   # your sidebar sections, to copy their exact names
python3 slack_incident_sections.py --channels   # incident channels, their state and section
python3 slack_incident_sections.py --dry-run    # what a run would do
```

### 3. Install the job

```bash
make install                         # every 5 minutes
make install INTERVAL_SECONDS=120    # or another interval
make uninstall                       # remove it
```

The same commands work on every OS. On Windows, install make first with
`winget install ezwinports.make` (or `choco install make`, `scoop install make`), or skip make and
run the scripts directly:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1                         # every 5 minutes
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -IntervalSeconds 120    # or another interval
powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1                       # remove it
```

Without make on macOS or Linux, the same goes for `scripts/install.sh` and `scripts/uninstall.sh`.

| | Job | Log |
| --- | --- | --- |
| macOS | launchd agent `local.slack-incident-sections` | `~/Library/Logs/slack-incident-sections/sync.log` |
| Linux | systemd user timer `slack-incident-sections.timer` | `~/.local/state/slack-incident-sections/sync.log` |
| Windows | Task Scheduler task `slack-incident-sections` | `%USERPROFILE%\.local\state\slack-incident-sections\sync.log` |

State: `~/.local/state/slack-incident-sections/state.json`.

On Linux and Windows the job runs only while you're logged in, which the Secret Service and
Credential Manager need anyway. On Linux without systemd, `make install` prints a crontab line to
add instead. `--log-file PATH` appends the log to a file, which is how the Linux and Windows jobs
write theirs.

## Config

Every key is optional; missing ones take the defaults shown in
[`config.example.json`](config.example.json).

| Key | Meaning |
| --- | --- |
| `sections.active`, `sections.resolved` | Sidebar section names, exactly as Slack shows them (case-sensitive). |
| `channel_pattern` | Regex for incident channel names. incident.io's default is `inc-<number>-…`. |
| `statuses.active`, `statuses.resolved` | Your incident.io status names, by whether the incident still needs work. Add any custom statuses your organisation uses; the log warns about unknown ones. |
| `incident_app_ids`, `incident_bot_ids` | Which messages come from incident.io. The app id defaults to incident.io's public Slack app; add a `bot_id` if your workspace uses a custom bot. |
| `credentials.store`, `credentials.token`, `credentials.cookie` | Where the token and cookie are read from; see below. |
| `max_resolved_per_run` | Safety brake: a run that would resolve more incidents than this stops instead (use `--force` if it's real). |
| `state_file` | Where the last known state per channel is stored. |

### Credential stores

`SLACK_XOXC_TOKEN` and `SLACK_D_COOKIE` always win. Otherwise `credentials.store` picks the source,
and `credentials.token` / `credentials.cookie` say which item to read in it:

| `store` | `token` / `cookie` are | Example |
| --- | --- | --- |
| `auto` (default) | item names in the OS store below | `"slack-incident-sections-token"` |
| `keychain` | macOS Keychain service names | `"slack-incident-sections-token"` |
| `secret-service` | Secret Service `service` attribute (via `secret-tool`) | `"slack-incident-sections-token"` |
| `windows` | Windows Credential Manager generic credential names | `"slack-incident-sections-token"` |
| `file` | paths to files holding the value (keep them `chmod 600`) | `"~/.config/slack-incident-sections/token"` |
| `command` | a command that prints the value, as a string or an argument list | `"op read op://Private/Slack/token"`, `["pass", "show", "slack/token"]` |

`command` covers any password manager with a CLI (1Password `op`, `pass`, Bitwarden `bw`,
`gopass`, PowerShell `Get-Secret`…). It's only allowed when the config file is yours and not
writable by others, since whoever can edit it chooses what runs. Configs that still use
`keychain_token_service` / `keychain_cookie_service` keep working.

## Debugging

```bash
python3 slack_incident_sections.py --inspect C0123456789   # how each incident.io message in a channel is read
```

## Development

Needs [uv](https://docs.astral.sh/uv/). `make` lists every target.

```bash
make check       # lint, type-check for every OS, and test
make format      # fix linting and formatting
```
