"""The MQTT contract, shared by the bridge and the TUI.

Both entry points import this module, so a topic or payload change is a
one-file edit and the two halves cannot silently drift apart.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

ONLINE = "online"
OFFLINE = "offline"

# Command verbs, named to match the compositor's own IPC commands.
#
# switch / switch-name / switch-app are separate topics rather than one
# overloaded "switch" because a single topic would be ambiguous: a payload of
# "3" could mean the view with id 3, or a view *named* "3". Separate topics also
# let Home Assistant entities point straight at a command_topic with no
# templating.
CMD_SWITCH = "switch"
CMD_SWITCH_NAME = "switch-name"
CMD_SWITCH_APP = "switch-app"
CMD_LAUNCH = "launch"
CMD_CLOSE = "close"
CMD_RESTART = "restart"
CMD_QUIT = "quit"

ALL_COMMANDS = (
    CMD_SWITCH,
    CMD_SWITCH_NAME,
    CMD_SWITCH_APP,
    CMD_LAUNCH,
    CMD_CLOSE,
    CMD_RESTART,
    CMD_QUIT,
)

# Payload accepted by close/restart meaning "whatever view is active now".
ACTIVE_ALIAS = "active"


@dataclass(frozen=True, slots=True)
class Topics:
    """Every topic this bridge uses, derived from a base and a device id."""

    base: str = "pi-panel"
    device_id: str = "unknown"

    @property
    def prefix(self) -> str:
        return f"{self.base}/{self.device_id}"

    # --- published state (all retained) ---------------------------------
    @property
    def bridge_availability(self) -> str:
        """Is the *bridge* alive. Used as the MQTT last will."""
        return f"{self.prefix}/bridge/availability"

    @property
    def compositor_availability(self) -> str:
        """Is the *compositor* reachable over its IPC socket.

        Deliberately separate from bridge availability: the bridge can be
        perfectly healthy while the compositor is down, and consumers need to
        tell those two failures apart.
        """
        return f"{self.prefix}/compositor/availability"

    @property
    def status(self) -> str:
        return f"{self.prefix}/status"

    @property
    def views(self) -> str:
        return f"{self.prefix}/views"

    @property
    def version(self) -> str:
        return f"{self.prefix}/compositor/version"

    # --- commands (never retained) --------------------------------------
    def command(self, verb: str) -> str:
        return f"{self.prefix}/command/{verb}"

    @property
    def command_wildcard(self) -> str:
        return f"{self.prefix}/command/+"

    @property
    def result(self) -> str:
        """Outcome of the last command.

        Not retained: it is a point-in-time event, and retaining it would
        replay a stale error at every client that subscribes later.
        """
        return f"{self.prefix}/command/result"

    def all_state_topics(self) -> Iterable[str]:
        return (
            self.bridge_availability,
            self.compositor_availability,
            self.status,
            self.views,
            self.version,
        )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def encode(payload: Any) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode()


def decode(payload: bytes | bytearray | str) -> Any:
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode(errors="replace")
    return json.loads(payload)


def result_payload(
    command: str, request: str, ok: bool, detail: str | None = None
) -> bytes:
    """Outcome of one command, including the compositor's verbatim error text."""
    return encode(
        {
            "command": command,
            "request": request,
            "ok": ok,
            "detail": detail,
            "ts": now_iso(),
        }
    )
