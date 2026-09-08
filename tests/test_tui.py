"""TUI smoke tests — compose, render, and react to MQTT payloads.

These run headless via Textual's pilot, with no broker: the MQTT worker will
fail to connect and retry, which is itself worth asserting doesn't crash the UI.
"""

from __future__ import annotations

import pytest

from pi_panel_mqtt.config import MqttSettings, Settings
from pi_panel_mqtt.schema import OFFLINE, ONLINE, Topics, encode
from pi_panel_mqtt.tui.app import PiPanelTUI

pytestmark = pytest.mark.asyncio


def _app() -> PiPanelTUI:
    settings = Settings(
        mqtt=MqttSettings(host="127.0.0.1", port=1, device_id="testpanel")
    )
    return PiPanelTUI(settings)


async def test_app_composes_and_renders():
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#views") is not None
        assert app.query_one("#statusbar") is not None


async def test_state_messages_populate_table_and_status():
    app = _app()
    topics = Topics(base="pi-panel", device_id="testpanel")
    async with app.run_test() as pilot:
        app._handle_mqtt(topics.bridge_availability, ONLINE.encode())
        app._handle_mqtt(topics.compositor_availability, ONLINE.encode())
        app._handle_mqtt(
            topics.status,
            encode(
                {
                    "active_id": 0,
                    "active_name": "main",
                    "view_count": 2,
                    "transitioning": False,
                    "output": {
                        "name": "HDMI-A-1",
                        "width": 1920,
                        "height": 1080,
                        "refresh": 60.0,
                    },
                }
            ),
        )
        app._handle_mqtt(
            topics.views,
            encode(
                [
                    {
                        "id": 0,
                        "name": "main",
                        "app_id": "main",
                        "title": "t",
                        "active": True,
                        "mapped": True,
                        "pid": 1,
                    },
                    {
                        "id": 1,
                        "name": "immich",
                        "app_id": "immich",
                        "title": "Photo Frame",
                        "active": False,
                        "mapped": True,
                        "pid": 2,
                    },
                ]
            ),
        )
        await pilot.pause()

        assert app._bridge_up and app._compositor_up
        assert app.query_one("#views").row_count == 2
        assert app._selected_view()["name"] == "main"


async def test_offline_compositor_is_reflected():
    app = _app()
    topics = Topics(base="pi-panel", device_id="testpanel")
    async with app.run_test() as pilot:
        app._handle_mqtt(topics.bridge_availability, ONLINE.encode())
        app._handle_mqtt(topics.compositor_availability, OFFLINE.encode())
        await pilot.pause()
        assert app._bridge_up is True
        assert app._compositor_up is False


async def test_error_result_is_logged_without_crashing():
    app = _app()
    topics = Topics(base="pi-panel", device_id="testpanel")
    async with app.run_test() as pilot:
        app._handle_mqtt(
            topics.result,
            encode(
                {
                    "command": "switch-name",
                    "request": "nope",
                    "ok": False,
                    "detail": "no view with name 'nope'",
                    "ts": "now",
                }
            ),
        )
        await pilot.pause()
        # Still alive and rendering after an error result.
        assert app.query_one("#log") is not None


async def test_empty_views_payload_is_safe():
    app = _app()
    topics = Topics(base="pi-panel", device_id="testpanel")
    async with app.run_test() as pilot:
        app._handle_mqtt(topics.views, encode([]))
        await pilot.pause()
        assert app.query_one("#views").row_count == 0
        assert app._selected_view() is None
