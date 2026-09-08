"""Textual TUI driving the panel over MQTT.

This never touches the compositor's Unix socket — it only speaks MQTT — which
is what lets it run on any machine that can reach the broker.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import aiomqtt
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Static,
)

from ..config import Settings
from ..schema import (
    ACTIVE_ALIAS,
    CMD_CLOSE,
    CMD_LAUNCH,
    CMD_QUIT,
    CMD_RESTART,
    CMD_SWITCH_NAME,
    ONLINE,
    Topics,
    decode,
    encode,
)

RECONNECT_DELAY = 3.0


class ConfirmScreen(ModalScreen[bool]):
    """Yes/no gate for destructive actions."""

    def __init__(self, question: str) -> None:
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label(self.question, id="confirm-question")
            with Horizontal(id="confirm-buttons"):
                yield Button("Cancel", variant="primary", id="cancel")
                yield Button("Confirm", variant="error", id="confirm")

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")


class LaunchScreen(ModalScreen[tuple[str, str] | None]):
    """Collect a name and shell command for a new view."""

    def compose(self) -> ComposeResult:
        with Vertical(id="launch-box"):
            yield Label("Launch a new view", id="launch-title")
            yield Input(placeholder="name (no spaces)", id="launch-name")
            yield Input(placeholder="shell command", id="launch-command")
            with Horizontal(id="launch-buttons"):
                yield Button("Cancel", variant="primary", id="cancel")
                yield Button("Launch", variant="success", id="ok")

    def on_mount(self) -> None:
        self.query_one("#launch-name", Input).focus()

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        self._submit()

    @on(Input.Submitted)
    def _submitted(self) -> None:
        self._submit()

    def _submit(self) -> None:
        name = self.query_one("#launch-name", Input).value.strip()
        command = self.query_one("#launch-command", Input).value.strip()
        if not name or not command:
            self.notify("Both a name and a command are required", severity="warning")
            return
        self.dismiss((name, command))


class PiPanelTUI(App[None]):
    CSS_PATH = "app.tcss"
    TITLE = "pi-panel"

    BINDINGS = [
        Binding("enter", "switch", "Switch"),
        Binding("r", "restart", "Restart"),
        Binding("c", "close_view", "Close"),
        Binding("l", "launch", "Launch"),
        # Deliberately shift+Q, not q: q exits the TUI, and confusing the two
        # would blank the physical panel.
        Binding("Q", "quit_compositor", "Quit compositor"),
        Binding("q,ctrl+c", "quit", "Exit TUI"),
    ]

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.topics = Topics(
            base=settings.mqtt.base_topic,
            device_id=settings.mqtt.resolved_device_id(),
        )
        self._client: aiomqtt.Client | None = None
        self._broker_up = False
        self._bridge_up = False
        self._compositor_up = False
        self._status: dict[str, Any] = {}
        self._views: list[dict[str, Any]] = []

    # --- layout ----------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("connecting…", id="statusbar")
        table = DataTable(id="views", cursor_type="row", zebra_stripes=True)
        yield table
        yield RichLog(id="log", markup=True, wrap=True, max_lines=500)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#views", DataTable)
        table.add_columns("", "id", "name", "app_id", "mapped", "pid", "title")
        self.sub_title = f"{self.topics.device_id} @ {self.settings.mqtt.host}"
        self._log(f"[dim]subscribing under {self.topics.prefix}/…[/dim]")
        self.mqtt_worker()

    # --- MQTT ------------------------------------------------------------

    @work(exclusive=True)
    async def mqtt_worker(self) -> None:
        mqtt = self.settings.mqtt
        while True:
            try:
                async with aiomqtt.Client(
                    hostname=mqtt.host,
                    port=mqtt.port,
                    username=mqtt.username or None,
                    password=mqtt.password or None,
                    keepalive=mqtt.keepalive,
                    identifier=f"pi-panel-tui-{id(self):x}",
                ) as client:
                    self._client = client
                    self._broker_up = True
                    self._log("[green]connected to broker[/green]")
                    for topic in (
                        self.topics.status,
                        self.topics.views,
                        self.topics.bridge_availability,
                        self.topics.compositor_availability,
                        self.topics.result,
                    ):
                        await client.subscribe(topic, qos=1)
                    self._refresh_status()
                    async for message in client.messages:
                        self._handle_mqtt(str(message.topic), message.payload)
            except aiomqtt.MqttError as exc:
                self._client = None
                self._broker_up = False
                self._log(f"[red]broker connection lost: {exc}[/red]")
                self._refresh_status()
                await asyncio.sleep(RECONNECT_DELAY)

    def _handle_mqtt(self, topic: str, payload: bytes) -> None:
        text = payload.decode(errors="replace") if payload else ""
        if topic == self.topics.bridge_availability:
            self._bridge_up = text == ONLINE
        elif topic == self.topics.compositor_availability:
            self._compositor_up = text == ONLINE
        elif topic == self.topics.status:
            self._status = decode(text) if text else {}
        elif topic == self.topics.views:
            self._views = decode(text) if text else []
            self._refresh_table()
        elif topic == self.topics.result:
            self._on_result(decode(text) if text else {})
        self._refresh_status()

    def _on_result(self, result: dict[str, Any]) -> None:
        cmd = result.get("command", "?")
        req = result.get("request", "")
        detail = result.get("detail") or ""
        if result.get("ok"):
            self._log(f"[green]✓[/green] {cmd} {req} {detail}".rstrip())
        else:
            self._log(f"[red]✗ {cmd} {req} — {detail}[/red]")
            # A toast as well: an error scrolled off the log is an error missed.
            self.notify(detail or f"{cmd} failed", severity="error", title=cmd)

    # --- rendering -------------------------------------------------------

    def _dot(self, up: bool) -> str:
        return "[green]●[/green]" if up else "[red]●[/red]"

    def _refresh_status(self) -> None:
        out = self._status.get("output") or {}
        geometry = (
            f"{out.get('name','?')}  {out.get('width','?')}x{out.get('height','?')}"
            f"@{out.get('refresh', 0):g}Hz"
            if out
            else "no output"
        )
        active = self._status.get("active_name") or "(none)"
        transitioning = " [yellow]transitioning[/yellow]" if self._status.get(
            "transitioning"
        ) else ""
        self.query_one("#statusbar", Static).update(
            f"broker {self._dot(self._broker_up)} "
            f"bridge {self._dot(self._bridge_up)} "
            f"compositor {self._dot(self._compositor_up)}   "
            f"active: [b]{active}[/b]   {geometry}{transitioning}"
        )

    def _refresh_table(self) -> None:
        table = self.query_one("#views", DataTable)
        previous = table.cursor_row
        table.clear()
        for v in self._views:
            table.add_row(
                "▶" if v.get("active") else " ",
                str(v.get("id", "?")),
                v.get("name") or "-",
                v.get("app_id") or "-",
                "yes" if v.get("mapped") else "no",
                str(v.get("pid", 0)),
                (v.get("title") or "")[:40],
            )
        if previous is not None and previous < table.row_count:
            table.move_cursor(row=previous)

    def _log(self, message: str) -> None:
        self.query_one("#log", RichLog).write(message)

    # --- actions ---------------------------------------------------------

    def _selected_view(self) -> dict[str, Any] | None:
        table = self.query_one("#views", DataTable)
        if table.cursor_row is None or table.cursor_row >= len(self._views):
            return None
        return self._views[table.cursor_row]

    def _publish(self, verb: str, payload: str) -> None:
        if self._client is None:
            self.notify("not connected to the broker", severity="error")
            return
        # retain=False matters: a retained command would re-fire at every
        # broker reconnect.
        self.run_worker(
            self._client.publish(
                self.topics.command(verb), payload, qos=1, retain=False
            ),
            exclusive=False,
        )

    def action_switch(self) -> None:
        view = self._selected_view()
        if not view or not view.get("name"):
            self.notify("no view selected", severity="warning")
            return
        self._publish(CMD_SWITCH_NAME, view["name"])

    def action_restart(self) -> None:
        view = self._selected_view()
        if view and view.get("name"):
            self._publish(CMD_RESTART, view["name"])

    def action_close_view(self) -> None:
        view = self._selected_view()
        if view and view.get("name"):
            self._publish(CMD_CLOSE, view["name"])

    @work
    async def action_launch(self) -> None:
        result = await self.push_screen_wait(LaunchScreen())
        if result:
            name, command = result
            self._publish(CMD_LAUNCH, json.dumps({"name": name, "command": command}))

    @work
    async def action_quit_compositor(self) -> None:
        confirmed = await self.push_screen_wait(
            ConfirmScreen(
                "Shut down the compositor?\n\n"
                "The panel goes to a bare console. It can only be restarted "
                "at the machine or over SSH."
            )
        )
        if confirmed:
            self._publish(CMD_QUIT, ACTIVE_ALIAS)

    @on(DataTable.RowSelected)
    def _row_selected(self) -> None:
        self.action_switch()
