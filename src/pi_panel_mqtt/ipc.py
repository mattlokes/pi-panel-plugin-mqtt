"""Async client for the pi-panel-compositor Unix-socket IPC protocol.

A fresh connection is opened per command rather than held open. The protocol
does support multiple commands per connection, but per-command connections are
self-healing: the compositor is started from a tty1 login shell, not systemd,
so it can disappear and come back at any moment. With a persistent connection
that would need explicit stale-socket detection and a reconnect state machine;
with per-command connections the next poll simply succeeds or fails cleanly
against whatever process is there now. On a local Unix socket at 1 Hz the extra
connect() costs nothing, and transient connections do not sit in one of the
compositor's 8 client slots.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from .models import Status, View

log = logging.getLogger(__name__)

DEFAULT_SOCKET = "/tmp/pi-panel.sock"
DEFAULT_TIMEOUT = 5.0


class IpcError(Exception):
    """Base class: something went wrong talking to the compositor."""


class IpcUnavailable(IpcError):
    """The compositor is not running, or the socket is not connectable."""


class IpcTimeout(IpcError):
    """The compositor accepted the connection but did not answer in time."""


class IpcProtocolError(IpcError):
    """The compositor answered with something the protocol does not describe."""


@dataclass(slots=True)
class Response:
    """One command's reply.

    ``ok`` mirrors the OK/ERROR prefix. ``detail`` is whatever followed it,
    verbatim — for errors that is the compositor's human-readable text, which
    is worth surfacing unchanged rather than rewriting.
    """

    ok: bool
    detail: str = ""
    rows: list[str] = field(default_factory=list)
    raw: str = ""  # the reply's first line, untouched


class CompositorIPC:
    def __init__(
        self, socket_path: str = DEFAULT_SOCKET, timeout: float = DEFAULT_TIMEOUT
    ) -> None:
        self.socket_path = socket_path
        self.timeout = timeout

    async def request(self, command: str) -> Response:
        """Send one command and return its reply.

        Raises IpcUnavailable / IpcTimeout / IpcProtocolError; a compositor-level
        ``ERROR ...`` is *not* an exception, it comes back as ``ok=False``.
        """
        try:
            return await asyncio.wait_for(self._exchange(command), self.timeout)
        except asyncio.TimeoutError as exc:
            raise IpcTimeout(f"no reply to {command!r} within {self.timeout}s") from exc
        except (FileNotFoundError, ConnectionRefusedError, PermissionError) as exc:
            raise IpcUnavailable(f"{self.socket_path}: {exc}") from exc
        except OSError as exc:
            raise IpcUnavailable(f"{self.socket_path}: {exc}") from exc

    async def _exchange(self, command: str) -> Response:
        reader, writer = await asyncio.open_unix_connection(self.socket_path)
        try:
            writer.write(f"{command}\n".encode())
            await writer.drain()
            return await self._read_response(reader, command)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def _read_response(
        self, reader: asyncio.StreamReader, command: str
    ) -> Response:
        first = await self._readline(reader, command)

        if first.startswith("OK"):
            # "OK" alone, or "OK <detail>".
            return Response(ok=True, detail=first[2:].strip(), raw=first)
        if first.startswith("ERROR"):
            return Response(ok=False, detail=first[5:].strip(), raw=first)
        if first.startswith("DATA"):
            # Multi-line. The DATA header carries a count, but read until the
            # END sentinel rather than trusting it — the count and the emitted
            # rows come from two separate places in the compositor.
            rows: list[str] = []
            while True:
                line = await self._readline(reader, command)
                if line == "END":
                    return Response(ok=True, rows=rows, raw=first)
                rows.append(line)

        raise IpcProtocolError(f"unexpected reply to {command!r}: {first!r}")

    async def _readline(self, reader: asyncio.StreamReader, command: str) -> str:
        raw = await reader.readline()
        if not raw:
            # EOF before a terminal line: the compositor exited mid-response,
            # or dropped us (it closes connections whose line exceeds its 4 KiB
            # read buffer).
            raise IpcUnavailable(f"connection closed during {command!r}")
        return raw.decode(errors="replace").rstrip("\r\n")

    # --- typed convenience wrappers -------------------------------------

    async def status(self) -> Status:
        resp = await self.request("status")
        if not resp.ok:
            raise IpcProtocolError(f"status returned an error: {resp.detail}")
        return Status.parse(resp.raw)

    async def views(self) -> list[View]:
        resp = await self.request("list")
        out: list[View] = []
        for row in resp.rows:
            try:
                out.append(View.parse(row))
            except ValueError as exc:
                # One malformed row must not lose the whole list.
                log.warning("skipping unparseable view row: %s", exc)
        return out

    async def version(self) -> str:
        resp = await self.request("version")
        return resp.detail

    async def is_alive(self) -> bool:
        try:
            await self.request("version")
            return True
        except IpcError:
            return False
