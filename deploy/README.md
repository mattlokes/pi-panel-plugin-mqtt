# Deploying the bridge on the Pi

| File | Installs to | Purpose |
|---|---|---|
| `systemd/pi-panel-mqtt.service` | `/etc/systemd/system/` | runs the bridge as a service |
| `config/pi-panel-mqtt.toml.example` | `~/.config/pi-panel/mqtt.toml` | broker + polling settings |
| (optional) | `~/.config/pi-panel/mqtt.env` | `PI_PANEL_MQTT_PASSWORD=...`, chmod 600 |

## Install

```bash
cd ~/pi-panel-compositor-mqtt
~/.local/bin/uv sync --frozen --compile-bytecode
mkdir -p ~/.config/pi-panel
cp config/pi-panel-mqtt.toml.example ~/.config/pi-panel/mqtt.toml
$EDITOR ~/.config/pi-panel/mqtt.toml

sudo cp systemd/pi-panel-mqtt.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pi-panel-mqtt.service
journalctl -u pi-panel-mqtt -f
```

`--compile-bytecode` matters here: this Pi boots from a USB stick reading about
3 MB/s cold, and precompiling means Python does not write `__pycache__` on first
import — which also keeps `ProtectSystem`/read-only hardening from silently
costing startup time.

## Updating

The unit deliberately never runs `uv sync`, so a dependency change needs it
explicitly — otherwise the service silently keeps running the old environment:

```bash
cd ~/pi-panel-compositor-mqtt
git pull
~/.local/bin/uv sync --frozen --compile-bytecode
sudo systemctl restart pi-panel-mqtt
```

## Two things not to change

**`PrivateTmp` must stay `false`.** The compositor creates `/tmp/pi-panel.sock`
from a tty1 login shell, entirely outside systemd. With a private `/tmp` the
bridge would start cleanly and report the compositor permanently offline.

**No `After=` on the compositor.** It is not a systemd unit, so there is nothing
to order against. The bridge is built to tolerate the socket being absent and
to pick it up whenever it appears.

## Checking it

```bash
systemctl status pi-panel-mqtt
journalctl -u pi-panel-mqtt -n 50

# watch the topics without installing mosquitto-clients
uv run --with aiomqtt scripts/mqtt-tap.py sub 'pi-panel/#'
```
