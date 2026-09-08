"""The bridge daemon: poll the compositor, publish to MQTT, act on commands."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time

import aiomqtt

from ..config import Settings
from ..discovery import Discovery
from ..ipc import CompositorIPC, IpcError
from ..models import Status, View
from ..schema import (
    ACTIVE_ALIAS,
    ALL_COMMANDS,
    CMD_CLOSE,
    CMD_LAUNCH,
    CMD_QUIT,
    CMD_RESTART,
    CMD_SWITCH,
    CMD_SWITCH_APP,
    CMD_SWITCH_NAME,
    OFFLINE,
    ONLINE,
    Topics,
    encode,
    result_payload,
)

log = logging.getLogger("pi-panel-bridge")

RECONNECT_MIN = 1.0
RECONNECT_MAX = 30.0


class Bridge:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.topics = Topics(
            base=settings.mqtt.base_topic,
            device_id=settings.mqtt.resolved_device_id(),
        )
        self.ipc = CompositorIPC(
            settings.compositor.socket_path, settings.compositor.request_timeout
        )
        self.discovery = Discovery(
            self.topics, settings.homeassistant.discovery_prefix
        )

        # Last values actually published, for publish-on-change.
        self._last_status: dict | None = None
        self._last_views: list[dict] | None = None
        self._last_view_names: list[str] | None = None
        self._compositor_online: bool | None = None
        self._last_list_poll = 0.0

    # --- publishing helpers ---------------------------------------------

    async def _publish(
        self, client: aiomqtt.Client, topic: str, payload, retain: bool = True
    ) -> None:
        await client.publish(topic, payload, qos=1, retain=retain)

    async def _set_compositor_online(
        self, client: aiomqtt.Client, online: bool
    ) -> None:
        if self._compositor_online == online:
            return
        self._compositor_online = online
        await self._publish(
            client, self.topics.compositor_availability, ONLINE if online else OFFLINE
        )
        log.info("compositor is %s", "online" if online else "offline")
        if online:
            # Version can only change across a compositor restart, so fetch it
            # on the offline->online edge rather than every poll.
            with contextlib.suppress(IpcError):
                await self._publish(client, self.topics.version, await self.ipc.version())
        else:
            # State topics now hold stale values; entity availability (which is
            # wired to this topic) is what stops consumers believing them.
            self._last_status = None
            self._last_views = None

    async def _publish_discovery(
        self, client: aiomqtt.Client, view_names: list[str]
    ) -> None:
        if not self.settings.homeassistant.enabled:
            return
        for msg in self.discovery.all_messages(view_names):
            await client.publish(msg.topic, msg.payload, qos=1, retain=msg.retain)
        log.debug("published HA discovery (%d view options)", len(view_names))

    # --- poll loop -------------------------------------------------------

    async def poll_loop(self, client: aiomqtt.Client) -> None:
        interval = self.settings.compositor.poll_interval
        while True:
            try:
                await self._poll_once(client)
            except IpcError as exc:
                log.debug("compositor unreachable: %s", exc)
                await self._set_compositor_online(client, False)
            except Exception:  # noqa: BLE001 - a poll must never kill the loop
                log.exception("unexpected error during poll")
            await asyncio.sleep(interval)

    async def _poll_once(self, client: aiomqtt.Client) -> None:
        status = await self.ipc.status()
        await self._set_compositor_online(client, True)

        payload = status.to_dict()
        if payload != self._last_status:
            previous = self._last_status
            self._last_status = payload
            await self._publish(client, self.topics.status, encode(payload))
            # A change in view_count or active view means the list is stale.
            if previous is None or (
                previous.get("view_count") != payload.get("view_count")
                or previous.get("active_id") != payload.get("active_id")
            ):
                await self._refresh_views(client)
                return

        # Otherwise refresh the list on its own slower cadence, to pick up
        # title/app_id changes that `status` cannot see.
        if time.monotonic() - self._last_list_poll >= self.settings.compositor.list_interval:
            await self._refresh_views(client)

    async def _refresh_views(self, client: aiomqtt.Client) -> None:
        self._last_list_poll = time.monotonic()
        views = await self.ipc.views()
        payload = [v.to_dict() for v in views]
        if payload != self._last_views:
            self._last_views = payload
            await self._publish(client, self.topics.views, encode(payload))

        names = [v.name for v in views if v.name]
        if names != self._last_view_names:
            self._last_view_names = names
            # HA only learns select options from the discovery config, so this
            # must be republished when the option set changes.
            await self._publish_discovery(client, names)

    # --- commands --------------------------------------------------------

    def _resolve_target(self, payload: str) -> str | None:
        """Map the `active` alias onto the currently active view's name."""
        if payload.strip().lower() != ACTIVE_ALIAS:
            return payload.strip()
        if self._last_status:
            return self._last_status.get("active_name")
        return None

    async def _build_ipc_command(self, verb: str, payload: str) -> str:
        if verb == CMD_SWITCH:
            return f"switch {payload.strip()}"
        if verb == CMD_SWITCH_NAME:
            return f"switch-name {payload.strip()}"
        if verb == CMD_SWITCH_APP:
            return f"switch-app {payload.strip()}"
        if verb == CMD_QUIT:
            return "quit"
        if verb in (CMD_CLOSE, CMD_RESTART):
            target = self._resolve_target(payload)
            if not target:
                raise ValueError("no target given and no active view known")
            return f"{verb} {target}"
        if verb == CMD_LAUNCH:
            try:
                data = json.loads(payload)
                name, command = data["name"], data["command"]
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise ValueError(
                    'launch expects JSON {"name": ..., "command": ...}'
                ) from exc
            if " " in str(name):
                # The compositor parses the name with %127s, so a name with a
                # space would silently swallow part of the command.
                raise ValueError("launch name must not contain spaces")
            return f"launch {name} {command}"
        raise ValueError(f"unknown command verb: {verb}")

    async def command_loop(self, client: aiomqtt.Client) -> None:
        async for message in client.messages:
            topic = str(message.topic)
            verb = topic.rsplit("/", 1)[-1]
            payload = (
                message.payload.decode(errors="replace")
                if isinstance(message.payload, (bytes, bytearray))
                else str(message.payload)
            )
            if verb not in ALL_COMMANDS:
                continue
            await self._handle_command(client, verb, payload)

    async def _handle_command(
        self, client: aiomqtt.Client, verb: str, payload: str
    ) -> None:
        log.info("command %s %r", verb, payload)
        try:
            command = await self._build_ipc_command(verb, payload)
        except ValueError as exc:
            await self._publish(
                client,
                self.topics.result,
                result_payload(verb, payload, False, str(exc)),
                retain=False,
            )
            return

        try:
            resp = await self.ipc.request(command)
        except IpcError as exc:
            await self._set_compositor_online(client, False)
            await self._publish(
                client,
                self.topics.result,
                result_payload(verb, payload, False, str(exc)),
                retain=False,
            )
            return

        await self._publish(
            client,
            self.topics.result,
            result_payload(verb, payload, resp.ok, resp.detail or None),
            retain=False,
        )
        if not resp.ok:
            log.warning("compositor rejected %s: %s", verb, resp.detail)
        else:
            # Reflect the change immediately rather than waiting for the tick.
            with contextlib.suppress(IpcError):
                await self._poll_once(client)

    # --- top level -------------------------------------------------------

    async def _session(self, client: aiomqtt.Client) -> None:
        await self._publish(client, self.topics.bridge_availability, ONLINE)
        # Force a full republish on every reconnect: retained state could have
        # been lost if the broker restarted without persistence.
        self._last_status = None
        self._last_views = None
        self._last_view_names = None
        self._compositor_online = None
        await self._publish_discovery(client, [])
        await client.subscribe(self.topics.command_wildcard, qos=1)
        log.info(
            "bridge online: device=%s base=%s",
            self.topics.device_id,
            self.topics.base,
        )
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self.poll_loop(client))
                tg.create_task(self.command_loop(client))
        finally:
            # On a clean disconnect (systemd stop, Ctrl-C) the broker does NOT
            # fire the last will, so availability would stay "online" forever.
            # Publish it explicitly; if the link is already gone this fails
            # harmlessly and the LWT covers us instead.
            with contextlib.suppress(Exception):
                await self._publish(
                    client, self.topics.bridge_availability, OFFLINE
                )

    async def run(self) -> None:
        mqtt = self.settings.mqtt
        will = aiomqtt.Will(
            topic=self.topics.bridge_availability,
            payload=OFFLINE,
            qos=1,
            retain=True,
        )
        backoff = RECONNECT_MIN
        while True:
            try:
                async with aiomqtt.Client(
                    hostname=mqtt.host,
                    port=mqtt.port,
                    username=mqtt.username or None,
                    password=mqtt.password or None,
                    keepalive=mqtt.keepalive,
                    will=will,
                    identifier=f"pi-panel-bridge-{self.topics.device_id}",
                ) as client:
                    backoff = RECONNECT_MIN
                    await self._session(client)
            except aiomqtt.MqttError as exc:
                # Raised by the client context manager: could not connect.
                log.warning(
                    "MQTT connection failed (%s); reconnecting in %.0fs", exc, backoff
                )
            except BaseExceptionGroup as eg:
                # asyncio.TaskGroup wraps whatever killed the session.
                log.warning(
                    "session ended (%s); reconnecting in %.0fs",
                    eg.exceptions[0],
                    backoff,
                )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX)
