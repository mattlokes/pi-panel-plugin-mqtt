"""A minimal stand-in for pi-panel-compositor's IPC server.

Speaks enough of the real protocol — including its exact response framing and
error strings — to test the client and bridge with no compositor present.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class FakeView:
    id: int
    name: str | None = None
    app_id: str | None = None
    title: str | None = None
    active: bool = False
    mapped: bool = True
    pid: int = 1000


def _n(value: str | None) -> str:
    return value if value is not None else "(none)"


@dataclass
class FakeCompositor:
    path: str
    views: list[FakeView] = field(default_factory=list)
    output_name: str = "HDMI-A-1"
    width: int = 1920
    height: int = 1080
    refresh_mhz: int = 60000
    transitioning: bool = False
    include_output: bool = True  # False mimics an older compositor
    commands: list[str] = field(default_factory=list)
    quit_called: bool = False
    _server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_unix_server(self._handle, self.path)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    # --- protocol --------------------------------------------------------

    @property
    def _active(self) -> FakeView | None:
        return next((v for v in self.views if v.active), None)

    def _status_line(self) -> str:
        a = self._active
        line = (
            f"OK active_id={a.id if a else -1} "
            f"active_name={_n(a.name if a else None)} "
            f"view_count={len(self.views)} "
            f"transitioning={'true' if self.transitioning else 'false'}"
        )
        if self.include_output:
            line += (
                f" output={self.output_name}"
                f" output_width={self.width}"
                f" output_height={self.height}"
                f" refresh={self.refresh_mhz // 1000}.{self.refresh_mhz % 1000:03d}"
            )
        return line

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            while True:
                raw = await reader.readline()
                if not raw:
                    return
                line = raw.decode().rstrip("\r\n")
                if not line:
                    continue  # the real server ignores empty lines
                self.commands.append(line)
                for out in self._dispatch(line):
                    writer.write(out.encode())
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            return
        finally:
            with_suppress = getattr(writer, "close", None)
            if with_suppress:
                writer.close()

    def _dispatch(self, line: str) -> list[str]:
        if line == "list":
            out = [f"DATA {len(self.views)}\n"]
            for v in self.views:
                out.append(
                    f"id={v.id} name={_n(v.name)} app_id={_n(v.app_id)} "
                    f"title={_n(v.title)} "
                    f"active={'true' if v.active else 'false'} "
                    f"mapped={'true' if v.mapped else 'false'} pid={v.pid}\n"
                )
            out.append("END\n")
            return out
        if line == "status":
            return [self._status_line() + "\n"]
        if line == "version":
            return ["OK pi-panel-compositor/1.0 protocol/1\n"]
        if line == "quit":
            self.quit_called = True
            return ["OK\n"]
        if line.startswith("switch-name "):
            name = line[12:]
            target = next((v for v in self.views if v.name == name), None)
            if target is None:
                return [f"ERROR no view with name '{name}'\n"]
            if not target.mapped:
                return [f"ERROR view '{name}' is not mapped\n"]
            for v in self.views:
                v.active = v is target
            return ["OK\n"]
        if line.startswith("switch "):
            arg = line[7:]
            try:
                vid = int(arg)
            except ValueError:
                return [f"ERROR invalid id '{arg}'\n"]
            target = next((v for v in self.views if v.id == vid), None)
            if target is None:
                return [f"ERROR no view with id {vid}\n"]
            for v in self.views:
                v.active = v is target
            return ["OK\n"]
        if line.startswith("restart "):
            name = line[8:]
            target = next(
                (v for v in self.views if v.name == name or str(v.id) == name), None
            )
            if target is None:
                return [f"ERROR no view '{name}'\n"]
            target.pid += 1
            return [f"OK pid={target.pid}\n"]
        if line.startswith("close "):
            name = line[6:]
            target = next(
                (v for v in self.views if v.name == name or str(v.id) == name), None
            )
            if target is None:
                return [f"ERROR no view '{name}'\n"]
            target.mapped = False
            target.pid = 0
            return ["OK\n"]
        if line.startswith("launch "):
            rest = line[7:]
            name, _, _command = rest.partition(" ")
            if not _command:
                return ["ERROR usage: launch <name> <command>\n"]
            if any(v.name == name for v in self.views):
                return [f"ERROR view with name '{name}' already exists\n"]
            new_id = max((v.id for v in self.views), default=-1) + 1
            self.views.append(FakeView(id=new_id, name=name, app_id=name))
            return [f"OK id={new_id}\n"]
        return [f"ERROR unknown command: {line}\n"]
