"""Entry point for the TUI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..config import DEFAULT_CONFIG_PATH, load
from .app import PiPanelTUI


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pi-panel-tui",
        description="Terminal UI for pi-panel-compositor, over MQTT.",
    )
    p.add_argument("--config", type=Path, default=None,
                   help=f"config file (default: {DEFAULT_CONFIG_PATH})")
    p.add_argument("--host", default=None, help="MQTT broker host")
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--username", default=None)
    p.add_argument("--password", default=None)
    p.add_argument("--device-id", default=None,
                   help="which panel to control (default: this hostname)")
    p.add_argument("--base-topic", default=None)
    return p


def main() -> int:
    args = build_parser().parse_args()
    overrides = {
        "mqtt.host": args.host,
        "mqtt.port": args.port,
        "mqtt.username": args.username,
        "mqtt.password": args.password,
        "mqtt.device_id": args.device_id,
        "mqtt.base_topic": args.base_topic,
    }
    try:
        settings = load(args.config, overrides)
    except (ValueError, OSError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    PiPanelTUI(settings).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
