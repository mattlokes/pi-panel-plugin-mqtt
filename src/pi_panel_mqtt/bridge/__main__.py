"""Entry point for the bridge daemon."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import sys
from pathlib import Path

from ..config import DEFAULT_CONFIG_PATH, load
from .daemon import Bridge

log = logging.getLogger("pi-panel-bridge")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pi-panel-bridge",
        description="Bridge pi-panel-compositor's IPC socket to MQTT.",
    )
    p.add_argument("--config", type=Path, default=None,
                   help=f"config file (default: {DEFAULT_CONFIG_PATH})")
    p.add_argument("--host", default=None, help="MQTT broker host")
    p.add_argument("--port", type=int, default=None, help="MQTT broker port")
    p.add_argument("--username", default=None)
    p.add_argument("--password", default=None)
    p.add_argument("--device-id", default=None,
                   help="topic device id (default: hostname)")
    p.add_argument("--base-topic", default=None)
    p.add_argument("--socket", dest="socket_path", default=None,
                   help="compositor IPC socket path")
    p.add_argument("--poll-interval", type=float, default=None)
    p.add_argument("--no-discovery", action="store_true",
                   help="do not publish Home Assistant discovery configs")
    p.add_argument("--log-level", default=None)
    return p


def _overrides(args: argparse.Namespace) -> dict:
    """CLI flags as dotted section.field keys. None means 'not given'."""
    ov = {
        "mqtt.host": args.host,
        "mqtt.port": args.port,
        "mqtt.username": args.username,
        "mqtt.password": args.password,
        "mqtt.device_id": args.device_id,
        "mqtt.base_topic": args.base_topic,
        "compositor.socket_path": args.socket_path,
        "compositor.poll_interval": args.poll_interval,
        "log_level": args.log_level,
    }
    if args.no_discovery:
        ov["homeassistant.enabled"] = False
    return ov


def _configure_logging(level: str) -> None:
    # systemd routes stdout/stderr to the journal, which adds its own
    # timestamps, so only include them when running on a terminal.
    on_tty = sys.stderr.isatty()
    fmt = "%(asctime)s %(levelname)-7s %(message)s" if on_tty else "%(levelname)-7s %(message)s"
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        stream=sys.stderr,
    )


async def _run(bridge: Bridge) -> None:
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    task = asyncio.create_task(bridge.run())
    await stop.wait()
    log.info("shutting down")
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def main() -> int:
    args = build_parser().parse_args()
    try:
        settings = load(args.config, _overrides(args))
    except (ValueError, OSError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    _configure_logging(settings.log_level)
    bridge = Bridge(settings)
    log.info(
        "connecting to %s:%d as device %r (socket %s)",
        settings.mqtt.host,
        settings.mqtt.port,
        bridge.topics.device_id,
        settings.compositor.socket_path,
    )
    try:
        asyncio.run(_run(bridge))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
