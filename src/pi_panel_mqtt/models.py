"""Data models and parsers for the pi-panel-compositor IPC wire format.

The compositor's protocol is space-separated ``key=value`` pairs with no
quoting and no escaping, and some values (``name``, ``app_id``, ``title``)
originate from Wayland clients, so they can contain spaces. A naive
``line.split()`` — which is what the compositor's own bundled Python client
does — silently mis-parses those rows.

The saving grace is that the field *order* is fixed by the C format strings in
``src/ipc.c``, and the trailing fields are strongly constrained (two booleans
and an integer). Anchoring a regex on that suffix lets the greedy groups
backtrack to the correct boundaries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any

# The compositor prints this literal for a NULL char*. It is indistinguishable
# from a real value of "(none)"; we accept that ambiguity and map it to None.
NULL_SENTINEL = "(none)"

# id=%d name=%s app_id=%s title=%s active=%s mapped=%s pid=%d
#
# The three greedy groups backtrack against the fixed `active=`/`mapped=`/`pid=`
# suffix, so a name or title containing spaces parses correctly. id/active/
# mapped/pid — the fields that actually drive switching and HA state — cannot
# contain spaces and are therefore always recovered correctly, whatever the
# client put in its title.
_LIST_ROW = re.compile(
    r"^id=(?P<id>-?\d+) "
    r"name=(?P<name>.*) "
    r"app_id=(?P<app_id>.*) "
    r"title=(?P<title>.*) "
    r"active=(?P<active>true|false) "
    r"mapped=(?P<mapped>true|false) "
    r"pid=(?P<pid>-?\d+)$"
)

# OK active_id=%d active_name=%s view_count=%d transitioning=%s
#    [output=%s output_width=%d output_height=%d refresh=%d.%03d]
#
# The output block is optional: it was added later, so a bridge built from this
# package still works against an older compositor that omits it.
_STATUS = re.compile(
    r"^OK active_id=(?P<active_id>-?\d+) "
    r"active_name=(?P<active_name>.*) "
    r"view_count=(?P<view_count>\d+) "
    r"transitioning=(?P<transitioning>true|false)"
    r"(?: output=(?P<output>.*) "
    r"output_width=(?P<output_width>-?\d+) "
    r"output_height=(?P<output_height>-?\d+) "
    r"refresh=(?P<refresh>[0-9]+\.[0-9]+))?$"
)


class ProtocolError(ValueError):
    """A response did not match the documented wire format."""


def _nullable(value: str) -> str | None:
    return None if value == NULL_SENTINEL else value


@dataclass(frozen=True, slots=True)
class View:
    id: int
    name: str | None
    app_id: str | None
    title: str | None
    active: bool
    mapped: bool
    pid: int

    @classmethod
    def parse(cls, line: str) -> View:
        m = _LIST_ROW.match(line)
        if not m:
            raise ProtocolError(f"unparseable list row: {line!r}")
        return cls(
            id=int(m["id"]),
            name=_nullable(m["name"]),
            app_id=_nullable(m["app_id"]),
            title=_nullable(m["title"]),
            active=m["active"] == "true",
            mapped=m["mapped"] == "true",
            pid=int(m["pid"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Output:
    name: str | None
    width: int
    height: int
    refresh: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Status:
    active_id: int
    active_name: str | None
    view_count: int
    transitioning: bool
    output: Output | None

    @classmethod
    def parse(cls, line: str) -> Status:
        m = _STATUS.match(line)
        if not m:
            raise ProtocolError(f"unparseable status line: {line!r}")
        output = None
        if m["output"] is not None:
            output = Output(
                name=_nullable(m["output"]),
                width=int(m["output_width"]),
                height=int(m["output_height"]),
                # The compositor formats mHz as "<int>.<3 digits>", so this is
                # exact to a millihertz; 0.000 means the backend reported none.
                refresh=float(m["refresh"]),
            )
        return cls(
            active_id=int(m["active_id"]),
            active_name=_nullable(m["active_name"]),
            view_count=int(m["view_count"]),
            transitioning=m["transitioning"] == "true",
            output=output,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_id": self.active_id,
            "active_name": self.active_name,
            "view_count": self.view_count,
            "transitioning": self.transitioning,
            "output": self.output.to_dict() if self.output else None,
        }
