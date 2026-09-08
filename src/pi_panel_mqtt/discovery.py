"""Home Assistant MQTT discovery payloads.

Publishing these (retained) makes the panel appear in Home Assistant as one
device with several entities, with no YAML on the HA side.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterator

from .schema import (
    ACTIVE_ALIAS,
    CMD_RESTART,
    CMD_SWITCH_NAME,
    Topics,
    encode,
)

_SAFE = re.compile(r"[^a-zA-Z0-9_-]")


def _slug(value: str) -> str:
    return _SAFE.sub("_", value)


@dataclass(frozen=True, slots=True)
class DiscoveryMessage:
    topic: str
    payload: bytes
    retain: bool = True


class Discovery:
    def __init__(
        self,
        topics: Topics,
        prefix: str = "homeassistant",
        sw_version: str | None = None,
    ) -> None:
        self.topics = topics
        self.prefix = prefix
        self.node_id = f"pi_panel_{_slug(topics.device_id)}"
        self.sw_version = sw_version

    # --- shared blocks ---------------------------------------------------

    @property
    def _device(self) -> dict[str, Any]:
        block: dict[str, Any] = {
            "identifiers": [self.node_id],
            "name": f"Pi Panel ({self.topics.device_id})",
            "manufacturer": "pi-panel",
            "model": "pi-panel-compositor",
        }
        if self.sw_version:
            block["sw_version"] = self.sw_version
        return block

    @property
    def _availability(self) -> list[dict[str, str]]:
        """An entity is available only if BOTH the bridge and the compositor are.

        If the compositor is down the state topics hold stale values, so the
        entities must go unavailable rather than lie.
        """
        return [
            {"topic": self.topics.bridge_availability},
            {"topic": self.topics.compositor_availability},
        ]

    def _base(self, object_id: str, name: str) -> dict[str, Any]:
        return {
            "name": name,
            "unique_id": f"{self.node_id}_{object_id}",
            "object_id": f"{self.node_id}_{object_id}",
            "device": self._device,
            "availability": self._availability,
            "availability_mode": "all",
        }

    def _topic(self, component: str, object_id: str) -> str:
        return f"{self.prefix}/{component}/{self.node_id}/{object_id}/config"

    # --- entities --------------------------------------------------------

    def select_active_view(self, options: list[str]) -> DiscoveryMessage:
        """Active-view selector.

        Home Assistant reads `options` from this config payload, not from a
        state topic, so this must be republished whenever the set of view names
        changes.
        """
        cfg = self._base("active_view", "Active view") | {
            "command_topic": self.topics.command(CMD_SWITCH_NAME),
            "state_topic": self.topics.status,
            "value_template": "{{ value_json.active_name }}",
            # HA rejects a select with an empty option list.
            "options": options or ["(none)"],
            "icon": "mdi:monitor-dashboard",
        }
        return DiscoveryMessage(self._topic("select", "active_view"), encode(cfg))

    def sensor_active_view(self) -> DiscoveryMessage:
        cfg = self._base("active_view_name", "Active view name") | {
            "state_topic": self.topics.status,
            "value_template": "{{ value_json.active_name }}",
            "icon": "mdi:tab",
        }
        return DiscoveryMessage(
            self._topic("sensor", "active_view_name"), encode(cfg)
        )

    def sensor_view_count(self) -> DiscoveryMessage:
        cfg = self._base("view_count", "View count") | {
            "state_topic": self.topics.status,
            "value_template": "{{ value_json.view_count }}",
            "state_class": "measurement",
            "icon": "mdi:counter",
        }
        return DiscoveryMessage(self._topic("sensor", "view_count"), encode(cfg))

    def sensor_resolution(self) -> DiscoveryMessage:
        cfg = self._base("output_resolution", "Output resolution") | {
            "state_topic": self.topics.status,
            # Guard the template: output is null when no output is configured.
            "value_template": (
                "{% if value_json.output %}"
                "{{ value_json.output.width }}x{{ value_json.output.height }}"
                "{% else %}unknown{% endif %}"
            ),
            "icon": "mdi:monitor-screenshot",
        }
        return DiscoveryMessage(
            self._topic("sensor", "output_resolution"), encode(cfg)
        )

    def sensor_refresh(self) -> DiscoveryMessage:
        cfg = self._base("refresh_rate", "Refresh rate") | {
            "state_topic": self.topics.status,
            "value_template": (
                "{% if value_json.output %}{{ value_json.output.refresh }}"
                "{% else %}0{% endif %}"
            ),
            "unit_of_measurement": "Hz",
            "state_class": "measurement",
            "icon": "mdi:sine-wave",
        }
        return DiscoveryMessage(self._topic("sensor", "refresh_rate"), encode(cfg))

    def binary_sensor_transitioning(self) -> DiscoveryMessage:
        cfg = self._base("transitioning", "Transitioning") | {
            "state_topic": self.topics.status,
            "value_template": "{{ value_json.transitioning }}",
            "payload_on": "True",
            "payload_off": "False",
            "icon": "mdi:transition",
        }
        return DiscoveryMessage(
            self._topic("binary_sensor", "transitioning"), encode(cfg)
        )

    def binary_sensor_compositor(self) -> DiscoveryMessage:
        """Is the compositor up.

        Availability here is the bridge only — an entity whose whole purpose is
        reporting that the compositor is down must not itself go unavailable
        when the compositor goes down.
        """
        cfg = {
            "name": "Compositor",
            "unique_id": f"{self.node_id}_compositor_reachable",
            "object_id": f"{self.node_id}_compositor_reachable",
            "device": self._device,
            "availability": [{"topic": self.topics.bridge_availability}],
            "state_topic": self.topics.compositor_availability,
            "payload_on": "online",
            "payload_off": "offline",
            "device_class": "connectivity",
        }
        return DiscoveryMessage(
            self._topic("binary_sensor", "compositor_reachable"), encode(cfg)
        )

    def button_restart_active(self) -> DiscoveryMessage:
        cfg = self._base("restart_active_view", "Restart active view") | {
            "command_topic": self.topics.command(CMD_RESTART),
            "payload_press": ACTIVE_ALIAS,
            "icon": "mdi:restart",
        }
        return DiscoveryMessage(
            self._topic("button", "restart_active_view"), encode(cfg)
        )

    # NOTE: there is deliberately no quit button. `quit` shuts the compositor
    # down entirely, leaving a bare tty that can only be recovered at the
    # machine or over SSH — not something to leave one mis-tap away on a
    # dashboard. Quit stays available on the command topic and in the TUI.

    # --- bulk ------------------------------------------------------------

    def all_messages(self, view_names: list[str]) -> Iterator[DiscoveryMessage]:
        yield self.select_active_view(view_names)
        yield self.sensor_active_view()
        yield self.sensor_view_count()
        yield self.sensor_resolution()
        yield self.sensor_refresh()
        yield self.binary_sensor_transitioning()
        yield self.binary_sensor_compositor()
        yield self.button_restart_active()
