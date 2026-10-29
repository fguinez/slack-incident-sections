import json
from pathlib import Path
from typing import Any

import pytest

from slack_incident_sections import (
    ACTIVE,
    RESOLVED,
    Config,
    Move,
    latest_state,
    message_state,
    plan_moves,
    section_index,
)

CONFIG = Config()
CHANNELS = {"C1": "inc-1-foo", "C2": "inc-2-bar", "C9": "general"}


def bot_message(text: str, **extra: Any) -> dict[str, Any]:
    return {"app_id": "A01DEGPUHHC", "bot_id": "B1", "ts": "1.0", "text": text, **extra}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("This incident has been resolved\nIncident resolved and closed\nDone.", RESOLVED),
        ("Incident declined", RESOLVED),
        ("Incident created\nfoo\nIncident resolved in the description", ACTIVE),
        ("Ana shared an update:  Severity: *Minor* | Status: ~Triage~ → *Investigating*", ACTIVE),
        ("Ana shared an update:  Status: ~Fixing~ → *Documenting*", RESOLVED),
        ("Incident reopened", ACTIVE),
        ("Another incident has been merged into this one", None),
        ("This incident has been merged into INC-3", RESOLVED),
        ("4 alerts have been attached to this incident\nResolved - HTTP - service", None),
        ("Incident escalated", None),
        ("Ana shared an update:\nfoo\nIncident resolved, waiting on bar", None),
        ("Ana shared an update:  Status: ~Triage~ → *Custom stage*", None),
        ("incident.io completed a follow-up\nStatus: ~Outstanding~ → *Completed*", None),
    ],
    ids=[
        "resolved",
        "declined",
        "created",
        "status-arrow-active",
        "status-arrow-resolved",
        "reopened",
        "other-merged-in",
        "merged-away",
        "alerts",
        "escalated",
        "free-text-below-header",
        "unknown-status",
        "follow-up",
    ],
)
def test_message_state_reads_incident_bot_status(text: str, expected: str | None) -> None:
    """Status changes are read from the header or the status arrow, ignoring other bot messages."""
    assert message_state(bot_message(text), CONFIG) == expected


def test_message_state_reads_blocks() -> None:
    """The status arrow is found inside blocks when the fallback text lacks it."""
    message = bot_message(
        "Incident update",
        blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": "Status: ~Fixing~ → *Monitoring*"}}],
    )

    assert message_state(message, CONFIG) == ACTIVE


@pytest.mark.parametrize(
    "message",
    [
        {"user": "U1", "ts": "1.0", "text": "Incident resolved"},
        {"app_id": "A_OTHER", "bot_id": "B_OTHER", "ts": "1.0", "text": "Incident resolved"},
    ],
    ids=["human", "other-bot"],
)
def test_message_state_ignores_other_authors(message: dict[str, Any]) -> None:
    """Only messages from incident.io's bot count."""
    assert message_state(message, CONFIG) is None


def test_configured_statuses_and_bot_ids_are_used() -> None:
    """Custom statuses and bot ids from the config extend the defaults."""
    config = Config.from_dict({"statuses": {"active": ["Accepted"]}, "incident_bot_ids": ["B_CUSTOM"]})
    message = {"bot_id": "B_CUSTOM", "ts": "1.0", "text": "Status: ~Triage~ → *Accepted*"}

    assert message_state(message, config) == ACTIVE


def test_latest_state_uses_newest_status_message() -> None:
    """The newest status message wins over older ones."""
    messages = [bot_message("Incident escalated"), bot_message("Incident reopened"), bot_message("Incident closed")]

    assert latest_state(messages, CONFIG) == ACTIVE


def test_first_run_does_not_move() -> None:
    """The first run only records where each incident stands."""
    assert plan_moves(CHANNELS, {"C1": ACTIVE, "C2": RESOLVED}, None, CONFIG) == []


@pytest.mark.parametrize(
    ("previous", "states", "expected"),
    [
        ({"C2": RESOLVED}, {"C1": ACTIVE, "C2": RESOLVED}, [Move("C1", "inc-1-foo", "Active incidents")]),
        ({"C2": RESOLVED}, {"C1": RESOLVED, "C2": RESOLVED}, [Move("C1", "inc-1-foo", "Resolved incidents")]),
        ({"C1": ACTIVE}, {"C1": RESOLVED}, [Move("C1", "inc-1-foo", "Resolved incidents")]),
        ({"C1": RESOLVED}, {"C1": ACTIVE}, [Move("C1", "inc-1-foo", "Active incidents")]),
        ({"C1": ACTIVE}, {"C1": ACTIVE}, []),
    ],
    ids=["new-active", "new-resolved", "resolved", "reopened", "unchanged"],
)
def test_moves_only_on_state_change(previous: dict[str, str], states: dict[str, str], expected: list[Move]) -> None:
    """A channel moves only when its incident is new or changed state."""
    assert plan_moves(CHANNELS, states, previous, CONFIG) == expected


def test_config_loads_from_file(tmp_path: Path) -> None:
    """A partial config file overrides only the keys it sets."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"sections": {"active": "On fire"}, "channel_pattern": "^incident-"}))

    config = Config.load(path)

    assert (config.active_section, config.resolved_section) == ("On fire", "Resolved incidents")
    assert config.is_incident_channel("incident-42") and not config.is_incident_channel("inc-42")


def test_section_index_maps_names_and_channels() -> None:
    """Sections are indexed by name and by the channels they list."""
    sections: list[dict[str, Any]] = [
        {"channel_section_id": "L1", "name": "Active incidents", "channel_ids_page": {"channel_ids": ["C1"]}},
        {"channel_section_id": "L2", "name": "", "type": "channels"},
    ]

    by_name, by_channel = section_index(sections)

    assert by_name == {"Active incidents": "L1"}
    assert by_channel == {"C1": "L1"}
