# pi-panel-compositor-mqtt

An IPC→MQTT bridge for [pi-panel-compositor](../pi-panel-compositor), plus a
Textual TUI that drives the panel over MQTT.

## Environment

- Target `pi@jazz`; see the `remote-target` skill for driving it over SSH.
  `sudo` there needs a password, so privileged steps go back to the user.
- Broker `mqtt.home.local` (192.168.4.97:1883), **anonymous connections
  accepted**. Home Assistant is separate, at `hass.home.local`.
- The TUI is pure MQTT and runs fine on macOS; the bridge belongs on the Pi
  (it needs the compositor's Unix socket).

```bash
uv run pytest                  # everything is testable with no Pi and no broker
uv run pi-panel-tui --host mqtt.home.local --device-id jazz
```

`tests/fake_compositor.py` speaks the real IPC protocol, including its exact
error strings — use it rather than requiring a live compositor.

## Unlike the compositor, this IS a systemd unit

`systemctl {status,restart} pi-panel-mqtt`, `journalctl -u pi-panel-mqtt -f`.

Two things in the unit that look like tidy-up candidates and are not:

- **`PrivateTmp=false` is deliberate.** The compositor creates
  `/tmp/pi-panel.sock` from a tty1 login shell, entirely outside systemd. With a
  private `/tmp` the bridge starts cleanly and reports the compositor
  permanently offline — a very confusing failure.
- **`ExecStart` calls the venv binary directly, not `uv run`.** The Pi boots
  from a USB stick reading ~3 MB/s cold. The cost is that a dependency change
  needs an explicit `uv sync --frozen --compile-bytecode` or the service keeps
  running the old environment silently.

There is no `After=` on the compositor because it is not a unit. The bridge is
built to tolerate the socket being absent at any moment.

## Design points worth preserving

- **`schema.py` is imported by both the bridge and the TUI.** Topic names and
  payload shapes live there only, so the two halves cannot drift.
- **Bridge and compositor availability are separate topics.** The bridge can be
  healthy while the compositor is down; one combined topic could not tell "the
  panel died" from "the bridge died".
- **Command topics are never retained.** A retained `quit` or `switch` re-fires
  at every broker reconnect.
- **No Home Assistant quit button**, by explicit decision — `quit` blanks the
  panel until someone attends the machine. It stays on the command topic and in
  the TUI behind a confirmation. `restart` does get an HA button.
- **The IPC client connects per command** rather than holding a connection. Both
  work, but per-command self-heals across a compositor restart with no
  stale-socket detection.
- **`list` rows are parsed with an anchored regex**, not `split()`. Rows are
  unquoted `key=value` and `title`/`app_id` come from Wayland clients, so a
  title containing a space breaks naive splitting — which is what the
  compositor's own bundled Python client does.

## Gotchas hit while building this

- Do not name a Textual `App` method `_on_message` — it overrides
  `MessagePump._on_message` and breaks the whole app at runtime.
- In TOML a bare key after a `[section]` header belongs to that section. Keep
  top-level keys above the first header (this broke the shipped example config).
- Home Assistant reads a `select`'s options from the **discovery config**, not a
  state topic, so the bridge republishes that config whenever the view set
  changes.
