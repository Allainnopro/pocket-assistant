"""Prove the conftest.py safety nets work, so other tests can rely on them.

Run: pytest
"""

import asyncio
import os
import socket
import urllib.request

import keyring
import pytest
from conftest import NetworkBlocked


def test_sync_connection_to_the_internet_is_blocked():
    with pytest.raises(NetworkBlocked, match="blocked in tests"):
        socket.create_connection(("api.anthropic.com", 443), timeout=1)


def test_sync_connection_to_a_raw_ip_is_blocked():
    s = socket.socket()
    try:
        with pytest.raises(NetworkBlocked, match="blocked in tests"):
            s.connect(("192.0.2.1", 443))  # TEST-NET address, never routable
    finally:
        s.close()


def test_async_connection_to_the_internet_is_blocked():
    with pytest.raises(NetworkBlocked, match="blocked in tests"):
        asyncio.run(asyncio.open_connection("api.telegram.org", 443))


def test_asyncio_and_loopback_still_work():
    async def echo_roundtrip():
        async def handle(reader, writer):
            writer.write(await reader.read(5))
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("localhost", port)
        writer.write(b"hello")
        await writer.drain()
        data = await reader.read(5)
        writer.close()
        server.close()
        await server.wait_closed()
        return data

    assert asyncio.run(echo_roundtrip()) == b"hello"


def test_keyring_is_an_in_memory_fake(vault):
    keyring.set_password("pocket-assistant", "probe", "secret")
    assert vault == {("pocket-assistant", "probe"): "secret"}
    assert keyring.get_password("pocket-assistant", "probe") == "secret"


def test_proxies_are_ignored_so_they_cannot_tunnel_out():
    # A proxy on 127.0.0.1 would pass the loopback check and forward the request
    assert "HTTPS_PROXY" not in os.environ and "HTTP_PROXY" not in os.environ
    proxies = urllib.request.getproxies()
    assert "http" not in proxies and "https" not in proxies


def test_blocked_call_is_not_an_ordinary_exception():
    # HTTP clients retry anything caught by `except Exception`; this must not be
    assert not issubclass(NetworkBlocked, Exception)
