"""IPC client tests against the fake compositor."""

from __future__ import annotations

import os
import shutil
import tempfile

import pytest

from pi_panel_mqtt.ipc import CompositorIPC, IpcUnavailable

from .fake_compositor import FakeCompositor, FakeView


@pytest.fixture
def sock_dir():
    """A short directory: AF_UNIX paths are capped near 104 bytes, and
    pytest's tmp_path on macOS is far longer than that."""
    d = tempfile.mkdtemp(prefix="pp", dir="/tmp")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
async def compositor(sock_dir: str):
    fake = FakeCompositor(
        path=os.path.join(sock_dir, "s"),
        views=[
            FakeView(id=0, name="main", app_id="main", title="a shell", active=True),
            FakeView(id=1, name="immich", app_id="immich", title="Photo Frame"),
        ],
    )
    await fake.start()
    yield fake
    await fake.stop()


@pytest.fixture
def client(compositor: FakeCompositor) -> CompositorIPC:
    return CompositorIPC(compositor.path, timeout=2.0)


async def test_status(client: CompositorIPC):
    s = await client.status()
    assert s.active_id == 0 and s.active_name == "main"
    assert s.view_count == 2
    assert s.output is not None and s.output.width == 1920
    assert s.output.refresh == pytest.approx(60.0)


async def test_status_without_output(compositor: FakeCompositor):
    """An older compositor omits the geometry block entirely."""
    compositor.include_output = False
    s = await CompositorIPC(compositor.path).status()
    assert s.output is None
    assert s.view_count == 2


async def test_list_views(client: CompositorIPC):
    views = await client.views()
    assert [v.name for v in views] == ["main", "immich"]
    # A title with a space must survive the round trip.
    assert views[1].title == "Photo Frame"
    assert views[0].active is True


async def test_switch_name_ok(client: CompositorIPC, compositor: FakeCompositor):
    resp = await client.request("switch-name immich")
    assert resp.ok
    assert compositor.views[1].active is True


async def test_switch_name_error_is_not_an_exception(client: CompositorIPC):
    resp = await client.request("switch-name nope")
    assert resp.ok is False
    # The compositor's own wording is preserved for the operator.
    assert resp.detail == "no view with name 'nope'"


async def test_unknown_command(client: CompositorIPC):
    resp = await client.request("frobnicate")
    assert resp.ok is False
    assert "unknown command" in resp.detail


async def test_socket_missing_raises_unavailable(sock_dir: str):
    client = CompositorIPC(os.path.join(sock_dir, "absent"), timeout=1.0)
    with pytest.raises(IpcUnavailable):
        await client.status()


async def test_compositor_disappearing_mid_life(
    client: CompositorIPC, compositor: FakeCompositor
):
    """The bridge must survive the compositor going away and coming back."""
    assert (await client.status()).view_count == 2

    await compositor.stop()
    with pytest.raises(IpcUnavailable):
        await client.status()

    # Per-command connections mean recovery needs no reconnect logic at all.
    await compositor.start()
    assert (await client.status()).view_count == 2


async def test_empty_view_list(compositor: FakeCompositor):
    compositor.views.clear()
    views = await CompositorIPC(compositor.path).views()
    assert views == []


async def test_is_alive(client: CompositorIPC, compositor: FakeCompositor):
    assert await client.is_alive() is True
    await compositor.stop()
    assert await client.is_alive() is False
