# pi-panel-compositor-mqtt

An MQTT bridge and a Textual TUI for
[pi-panel-compositor](../pi-panel-compositor), a wlroots kiosk compositor.

The compositor is controllable only through a Unix-socket text protocol on the
machine it runs on. This package puts it on the network:

```
compositor ──unix socket──▶ bridge ──MQTT──▶ broker ──▶ Home Assistant
  (tty1)     /tmp/pi-panel.sock   (systemd)              └──▶ TUI (any machine)
```

- **`pi-panel-bridge`** — a daemon that polls the compositor's IPC socket,
  publishes state to MQTT, and turns MQTT commands back into IPC commands.
  Publishes Home Assistant discovery configs so the panel appears as entities.
- **`pi-panel-tui`** — a Textual terminal UI that drives the panel over MQTT.
  It never touches the IPC socket, so it runs anywhere with broker access.

## Install

```bash
uv sync --frozen --compile-bytecode
cp config/pi-panel-mqtt.toml.example ~/.config/pi-panel/mqtt.toml
$EDITOR ~/.config/pi-panel/mqtt.toml
```

See [`deploy/README.md`](deploy/README.md) for running the bridge as a systemd
service on the Pi.

## Run the TUI from another machine

No checkout or install needed — `uv` supplies its own Python:

```bash
uvx --from git+file:///path/to/pi-panel-compositor-mqtt pi-panel-tui \
    --host mqtt.home.local --device-id jazz
```

## MQTT topics

Base `pi-panel`, device id defaults to the hostname.

| Topic | Retained | Meaning |
|---|---|---|
| `pi-panel/<id>/bridge/availability` | yes | `online`/`offline` — the **bridge** (MQTT last will) |
| `pi-panel/<id>/compositor/availability` | yes | `online`/`offline` — the **compositor's IPC socket** |
| `pi-panel/<id>/status` | yes | JSON: active view, view count, transitioning, output geometry |
| `pi-panel/<id>/views` | yes | JSON array of views |
| `pi-panel/<id>/compositor/version` | yes | compositor version string |
| `pi-panel/<id>/command/switch` | no | view id |
| `pi-panel/<id>/command/switch-name` | no | view name |
| `pi-panel/<id>/command/switch-app` | no | Wayland app_id |
| `pi-panel/<id>/command/launch` | no | JSON `{"name": ..., "command": ...}` |
| `pi-panel/<id>/command/close` | no | view name/id, or `active` |
| `pi-panel/<id>/command/restart` | no | view name/id, or `active` |
| `pi-panel/<id>/command/quit` | no | any payload — shuts the compositor down |
| `pi-panel/<id>/command/result` | no | JSON outcome of the last command |

Two design points worth knowing:

**Bridge and compositor availability are separate topics.** The bridge can be
perfectly healthy while the compositor is down — it is started from a tty1
login shell, not systemd, so it comes and goes independently. Collapsing these
into one topic would make "the panel died" indistinguishable from "the bridge
died".

**Command topics are never retained.** A retained `quit` or `switch` would be
replayed at every broker reconnect and re-fire the command. The bridge always
publishes with `retain=False`, and so should anything else writing to them.

## Home Assistant

With `[homeassistant] enabled = true` the bridge publishes discovery configs
for a single device: a `select` for the active view, sensors for the active
view / view count / output resolution / refresh rate, a `binary_sensor` for
transitioning, and a **button to restart the active view**.

There is deliberately **no quit button** — a mis-tap on a dashboard would blank
the panel, and it could only be brought back from the machine itself or over
SSH. Quit remains available on the command topic and in the TUI.

Note that Home Assistant reads a `select`'s options from the *discovery config*,
not from a state topic, so the bridge republishes that config whenever the set
of view names changes. There is a brief staleness window between a
`launch`/`close` and that republish.

## Development

```bash
uv run pytest
```

The tests include a fake compositor (`tests/fake_compositor.py`) that speaks the
IPC protocol, so the bridge and parsers can be tested with no real compositor
and no broker.
