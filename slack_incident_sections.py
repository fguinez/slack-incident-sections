#!/usr/bin/env python3
"""Keep incident.io channels in the right Slack sidebar section.

Reads each incident's status from the messages incident.io's bot posts in its
channel, and moves a channel only when that status changes: a new or reopened
incident goes to the active section, a newly resolved one to the resolved
section. Channels you moved by hand stay where you put them.

Slack has no public API for sidebar sections, so this uses the internal
endpoints the web client calls, authenticated with the browser session
(xoxc token + `d` cookie). They can change without notice.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ACTIVE = "active"
RESOLVED = "resolved"

CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
DEFAULT_CONFIG = CONFIG_HOME / "slack-incident-sections/config.json"
DEFAULT_STATE = Path.home() / ".local/state/slack-incident-sections/state.json"
TOKEN_ENV = "SLACK_XOXC_TOKEN"
COOKIE_ENV = "SLACK_D_COOKIE"
# Pages of history read for a channel seen for the first time.
FIRST_SCAN_PAGES = 3

STATUS_CHANGE = re.compile(r"Status:\*?\s*~[^~]+~\s*(?:→|->|&gt;)\s*\*([^*]+)\*")
HEADER_STATES = [
    (
        re.compile(
            r"^(?:Incident|This incident has been) (?:resolved|closed|declined|cancell?ed|merged into)\b",
            re.IGNORECASE,
        ),
        RESOLVED,
    ),
    (
        re.compile(
            r"^(?:Incident|This incident has been) (?:created|declared|accepted|reopened|resumed|paused)\b",
            re.IGNORECASE,
        ),
        ACTIVE,
    ),
]
# The fallback text and the block header; anything below is free text written by people.
HEADER_LINES = 2

log = logging.getLogger("slack-incident-sections")


@dataclass(frozen=True)
class Config:
    active_section: str = "Active incidents"
    resolved_section: str = "Resolved incidents"
    channel_pattern: str = "^inc-"
    # incident.io's Slack app, the same in every workspace.
    incident_app_ids: tuple[str, ...] = ("A01DEGPUHHC",)
    incident_bot_ids: tuple[str, ...] = ()
    active_statuses: tuple[str, ...] = ("triage", "investigating", "fixing", "monitoring", "paused")
    resolved_statuses: tuple[str, ...] = ("documenting", "reviewing", "closed", "merged", "declined", "canceled")
    # Where the token and cookie are read from when the environment doesn't set them; see read_secret.
    credential_store: str = "auto"
    token_ref: str | list[str] = "slack-incident-sections-token"
    cookie_ref: str | list[str] = "slack-incident-sections-cookie"
    # More resolutions than this in one run more likely means a parsing bug.
    max_resolved_per_run: int = 15
    state_file: Path = DEFAULT_STATE
    status_states: dict[str, str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        states = {s.lower(): ACTIVE for s in self.active_statuses}
        states |= {s.lower(): RESOLVED for s in self.resolved_statuses}
        object.__setattr__(self, "status_states", states)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Config:
        sections = raw.get("sections", {})
        statuses = raw.get("statuses", {})
        credentials = raw.get("credentials", {})
        defaults = cls()
        return cls(
            active_section=sections.get("active", defaults.active_section),
            resolved_section=sections.get("resolved", defaults.resolved_section),
            channel_pattern=raw.get("channel_pattern", defaults.channel_pattern),
            incident_app_ids=tuple(raw.get("incident_app_ids", defaults.incident_app_ids)),
            incident_bot_ids=tuple(raw.get("incident_bot_ids", defaults.incident_bot_ids)),
            active_statuses=tuple(statuses.get("active", defaults.active_statuses)),
            resolved_statuses=tuple(statuses.get("resolved", defaults.resolved_statuses)),
            credential_store=credentials.get("store", defaults.credential_store),
            token_ref=credentials.get("token", credentials.get("keychain_token_service", defaults.token_ref)),
            cookie_ref=credentials.get("cookie", credentials.get("keychain_cookie_service", defaults.cookie_ref)),
            max_resolved_per_run=raw.get("max_resolved_per_run", defaults.max_resolved_per_run),
            state_file=Path(raw.get("state_file", defaults.state_file)).expanduser(),
        )

    @classmethod
    def load(cls, path: Path) -> Config:
        if not path.exists():
            log.info("No config at %s; using defaults", path)
            return cls()
        config = cls.from_dict(json.loads(path.read_text()))
        if config.credential_store not in CREDENTIAL_STORES:
            raise SystemExit(f"Unknown credential store {config.credential_store!r} in {path}")
        if config.credential_store == "command" and sys.platform != "win32":
            info = path.stat()
            if info.st_uid != os.getuid() or info.st_mode & 0o022:
                raise SystemExit(f"{path} must be yours and not writable by others to use the 'command' store")
        return config

    def is_incident_bot(self, message: dict[str, Any]) -> bool:
        return message.get("app_id") in self.incident_app_ids or message.get("bot_id") in self.incident_bot_ids

    def is_incident_channel(self, name: str) -> bool:
        return re.search(self.channel_pattern, name) is not None


@dataclass(frozen=True)
class Move:
    channel_id: str
    channel_name: str
    target: str


def message_text(message: dict[str, Any]) -> str:
    """Flatten a message's fallback text, blocks and attachments into one string."""
    parts: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("text", "fallback", "pretext", "title") and isinstance(value, str):
                    parts.append(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk({k: message.get(k) for k in ("text", "blocks", "attachments")})
    return "\n".join(parts)


def message_state(message: dict[str, Any], config: Config) -> str | None:
    """Return the incident state a bot message announces, or None if it isn't a status change."""
    if not config.is_incident_bot(message):
        return None
    text = message_text(message)
    headers = [line.strip() for line in text.splitlines() if line.strip()][:HEADER_LINES]
    # Follow-ups announce their own status changes in the same format.
    if any("follow-up" in header.lower() for header in headers):
        return None
    change = STATUS_CHANGE.search(text)
    if change:
        status = change.group(1).strip()
        state = config.status_states.get(status.lower())
        if state is None:
            log.warning("Unknown incident status %r; add it to `statuses` in the config", status)
        return state
    return next(
        (state for header in headers for pattern, state in HEADER_STATES if pattern.search(header)),
        None,
    )


def latest_state(messages: list[dict[str, Any]], config: Config) -> str | None:
    """Return the state announced by the newest status message (messages are newest first)."""
    return next((state for message in messages if (state := message_state(message, config))), None)


def plan_moves(
    channels: dict[str, str],
    states: dict[str, str],
    previous: dict[str, str] | None,
    config: Config,
) -> list[Move]:
    """Return the moves for channels whose incident state changed since the previous run."""
    if previous is None:
        return []
    return [
        Move(
            channel_id,
            channels[channel_id],
            config.active_section if state == ACTIVE else config.resolved_section,
        )
        for channel_id, state in sorted(states.items(), key=lambda item: channels[item[0]])
        if previous.get(channel_id) != state
    ]


CREDENTIAL_STORES = ("auto", "keychain", "secret-service", "windows", "file", "command")


def native_store() -> str:
    if sys.platform == "darwin":
        return "keychain"
    if sys.platform == "win32":
        return "windows"
    return "secret-service"


def run_secret_command(argv: list[str], what: str) -> str:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise SystemExit(f"{argv[0]!r} not found, needed to read {what}; see README.md") from None
    if result.returncode != 0 or not result.stdout.strip():
        raise SystemExit(f"Could not read {what}; see README.md")
    return result.stdout.strip()


def windows_credential(target: str) -> str:
    """Read a generic credential from the Windows Credential Manager (as stored by `cmdkey /generic`)."""
    if sys.platform != "win32":
        raise SystemExit("The 'windows' credential store only works on Windows")
    import ctypes
    from ctypes import wintypes

    class Credential(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    credential = ctypes.POINTER(Credential)()
    cred_type_generic = 1
    if not advapi32.CredReadW(target, cred_type_generic, 0, ctypes.byref(credential)):
        raise SystemExit(f"Missing Windows credential {target!r}; see README.md")
    try:
        blob = ctypes.string_at(credential.contents.CredentialBlob, credential.contents.CredentialBlobSize)
    finally:
        advapi32.CredFree(credential)
    try:
        value = blob.decode("utf-16-le").strip()
    except UnicodeDecodeError:
        raise SystemExit(f"Windows credential {target!r} isn't UTF-16 text; store it with cmdkey") from None
    if not value:
        raise SystemExit(f"Windows credential {target!r} is empty")
    return value


def split_command(command: str) -> list[str]:
    """Split a command line, keeping Windows backslash paths intact."""
    if sys.platform != "win32":
        return shlex.split(command)
    args = shlex.split(command, posix=False)
    return [arg[1:-1] if len(arg) > 1 and arg[0] == arg[-1] == '"' else arg for arg in args]


def read_secret(store: str, ref: str | list[str]) -> str:
    """Read one secret; `ref` is an item name, a file path or a command, depending on the store."""
    if store == "auto":
        store = native_store()
    if store == "command":
        argv = split_command(ref) if isinstance(ref, str) else ref
        if not argv:
            raise SystemExit("The 'command' credential store needs a command")
        return run_secret_command(argv, f"the output of {argv[0]!r}")
    if not isinstance(ref, str):
        raise SystemExit(f"Credential store {store!r} takes a string, not a list")
    if store == "keychain":
        argv = ["security", "find-generic-password", "-a", os.environ.get("USER", ""), "-s", ref, "-w"]
        return run_secret_command(argv, f"Keychain item {ref!r}")
    if store == "secret-service":
        return run_secret_command(["secret-tool", "lookup", "service", ref], f"Secret Service item {ref!r}")
    if store == "windows":
        return windows_credential(ref)
    if store == "file":
        path = Path(ref).expanduser()
        if not path.is_file():
            raise SystemExit(f"Missing credential file {str(path)!r}; see README.md")
        if os.name == "posix" and path.stat().st_mode & 0o077:
            log.warning("%s is readable by other users; run chmod 600 on it", path)
        return path.read_text().strip()
    raise SystemExit(f"Unknown credential store {store!r}; use one of {', '.join(CREDENTIAL_STORES)}")


def credentials(config: Config) -> tuple[str, str]:
    """Return the Slack token and cookie, from the environment or the configured credential store."""
    token = os.environ.get(TOKEN_ENV) or read_secret(config.credential_store, config.token_ref)
    cookie = os.environ.get(COOKIE_ENV) or read_secret(config.credential_store, config.cookie_ref)
    return token, cookie


class SlackClient:
    def __init__(self, token: str, cookie: str) -> None:
        self.token = token
        self.cookie = cookie

    def call(self, method: str, **fields: str) -> dict[str, Any]:
        request = urllib.request.Request(
            f"https://slack.com/api/{method}",
            data=urllib.parse.urlencode({"token": self.token, **fields}).encode(),
            headers={"Cookie": f"d={self.cookie}"},
        )
        for _ in range(5):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    body: dict[str, Any] = json.load(response)
                break
            except urllib.error.HTTPError as error:
                if error.code != 429:
                    raise SystemExit(f"Slack {method} returned HTTP {error.code}") from None
                time.sleep(int(error.headers.get("Retry-After", "5")))
        else:
            raise SystemExit(f"Slack {method} kept rate limiting")
        if not body.get("ok"):
            raise SystemExit(f"Slack {method} failed: {body.get('error')}")
        return body

    def member_channels(self) -> dict[str, str]:
        channels: dict[str, str] = {}
        cursor = ""
        while True:
            page = self.call(
                "users.conversations",
                types="public_channel,private_channel",
                exclude_archived="true",
                limit="1000",
                cursor=cursor,
            )
            channels.update({c["id"]: c["name"] for c in page["channels"]})
            cursor = page.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                return channels

    def history(self, channel_id: str, oldest: str | None) -> list[dict[str, Any]]:
        """Return messages newest first: those after `oldest`, or the last few pages if None."""
        messages: list[dict[str, Any]] = []
        cursor = ""
        for _ in range(FIRST_SCAN_PAGES if oldest is None else 100):
            page = self.call(
                "conversations.history", channel=channel_id, limit="200", oldest=oldest or "0", cursor=cursor
            )
            messages.extend(page["messages"])
            cursor = page.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                break
        return messages

    def sections(self) -> list[dict[str, Any]]:
        sections: list[dict[str, Any]] = self.call("users.channelSections.list")["channel_sections"]
        return sections

    def move(self, channel_ids: list[str], from_section: str | None, to_section: str) -> None:
        fields = {"insert": json.dumps([{"channel_section_id": to_section, "channel_ids": channel_ids}])}
        if from_section:
            fields["remove"] = json.dumps([{"channel_section_id": from_section, "channel_ids": channel_ids}])
        self.call("users.channelSections.channels.bulkUpdate", **fields)


def section_index(sections: list[dict[str, Any]]) -> tuple[dict[str, str], dict[str, str]]:
    """Return section name → id and channel id → section id."""
    by_name = {s["name"]: s["channel_section_id"] for s in sections if s.get("name")}
    by_channel = {
        channel_id: s["channel_section_id"]
        for s in sections
        for channel_id in s.get("channel_ids_page", {}).get("channel_ids", [])
    }
    return by_name, by_channel


def apply_moves(slack: SlackClient, moves: list[Move], config: Config, dry_run: bool) -> None:
    by_name, by_channel = section_index(slack.sections())
    missing = {config.active_section, config.resolved_section} - by_name.keys()
    if missing:
        raise SystemExit(f"Sidebar sections not found: {sorted(missing)}; check `sections` in the config")

    groups: dict[tuple[str | None, str], list[Move]] = {}
    for move in moves:
        target_id = by_name[move.target]
        source_id = by_channel.get(move.channel_id)
        if source_id == target_id:
            continue
        groups.setdefault((source_id, target_id), []).append(move)

    for (source_id, target_id), group in groups.items():
        for move in group:
            log.info("%s #%s → %s", "Would move" if dry_run else "Moving", move.channel_name, move.target)
        if not dry_run:
            slack.move([m.channel_id for m in group], source_id, target_id)


def load_state(path: Path) -> dict[str, dict[str, str]] | None:
    if not path.exists():
        return None
    state: dict[str, dict[str, str]] = json.loads(path.read_text())["channels"]
    return state


def save_state(path: Path, channels: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"channels": channels}, indent=2, sort_keys=True))
    tmp.replace(path)


def read_states(
    slack: SlackClient, channels: dict[str, str], saved: dict[str, dict[str, str]], config: Config
) -> dict[str, dict[str, str]]:
    """Return channel id → {state, ts} for incident channels, reading only messages since the last run."""
    current: dict[str, dict[str, str]] = {}
    for channel_id, name in channels.items():
        if not config.is_incident_channel(name):
            continue
        known = saved.get(channel_id, {})
        messages = slack.history(channel_id, known.get("ts"))
        state = latest_state(messages, config) or known.get("state")
        if state is None:
            log.warning("No status message found in #%s; skipping it", name)
            continue
        newest = max((m["ts"] for m in messages), default=known.get("ts", "0"), key=float)
        current[channel_id] = {"state": state, "ts": newest}
    return current


def show_sections(slack: SlackClient) -> None:
    for section in slack.sections():
        page = section.get("channel_ids_page", {})
        name = section.get("name") or "-"
        print(f"{name:<30} type={section.get('type'):<20} channels={page.get('count')}")


def show_channels(slack: SlackClient, config: Config) -> None:
    by_name, by_channel = section_index(slack.sections())
    section_names = {section_id: name for name, section_id in by_name.items()}
    channels = slack.member_channels()
    states = read_states(slack, channels, {}, config)
    for channel_id, name in sorted(channels.items(), key=lambda item: item[1]):
        if config.is_incident_channel(name):
            state = states.get(channel_id, {}).get("state", "?")
            section = section_names.get(by_channel.get(channel_id, ""), "(default)")
            print(f"{state:<9} {section:<25} {channel_id}  #{name}")


def inspect_channel(slack: SlackClient, channel_id: str, config: Config) -> None:
    for message in slack.history(channel_id, None):
        if config.is_incident_bot(message):
            header = next((line for line in message_text(message).splitlines() if line.strip()), "")
            print(f"{message['ts']}  {message_state(message, config) or '-':<9} {header[:90]}")


def run(slack: SlackClient, config: Config, dry_run: bool, force: bool) -> None:
    saved = load_state(config.state_file)
    channels = slack.member_channels()
    current = read_states(slack, channels, saved or {}, config)
    previous = None if saved is None else {channel_id: entry["state"] for channel_id, entry in saved.items()}
    states = {channel_id: entry["state"] for channel_id, entry in current.items()}
    moves = plan_moves(channels, states, previous, config)

    if previous is None:
        active = sum(state == ACTIVE for state in states.values())
        log.info("First run: recorded %d incident channels (%d active), no moves", len(current), active)
    resolved_count = sum(m.target == config.resolved_section for m in moves)
    if resolved_count > config.max_resolved_per_run and not force:
        raise SystemExit(f"{resolved_count} incidents resolved at once looks wrong; rerun with --force if real")

    apply_moves(slack, moves, config, dry_run)
    if not dry_run:
        save_state(config.state_file, current)


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help=f"config file (default: {DEFAULT_CONFIG})")
    parser.add_argument("--dry-run", action="store_true", help="print the moves without making them or saving state")
    parser.add_argument("--sections", action="store_true", help="list your sidebar sections and exit")
    parser.add_argument("--channels", action="store_true", help="list incident channels, their state and section")
    parser.add_argument("--inspect", metavar="CHANNEL_ID", help="show how each incident.io message is classified")
    parser.add_argument("--force", action="store_true", help="allow more resolutions than max_resolved_per_run")
    parser.add_argument("--log-file", type=Path, help="append the log to this file instead of printing it")
    args = parser.parse_args()
    log_format = "%(asctime)s %(levelname)s %(message)s"
    if args.log_file:
        args.log_file.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(level=logging.INFO, format=log_format, filename=args.log_file, encoding="utf-8")
    else:
        logging.basicConfig(level=logging.INFO, format=log_format, stream=sys.stdout)

    try:
        config = Config.load(args.config)
        slack = SlackClient(*credentials(config))
        if args.sections:
            show_sections(slack)
        elif args.channels:
            show_channels(slack, config)
        elif args.inspect:
            inspect_channel(slack, args.inspect, config)
        else:
            run(slack, config, args.dry_run, args.force)
    except SystemExit as error:
        # Scheduled runs may have no visible stderr, so the reason must reach the log file.
        if args.log_file and isinstance(error.code, str):
            log.error("%s", error.code)
        raise
    except Exception:
        if args.log_file:
            log.exception("Run failed")
        raise


if __name__ == "__main__":
    main()
