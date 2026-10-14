"""Test-wide safety nets: no network, no real Windows Credential Manager.

Both fixtures are autouse, so every test gets them without asking. A test
that accidentally reaches for Gmail, Anthropic or Telegram fails loudly
instead of quietly spending money or touching real accounts.
"""

from __future__ import annotations

import ipaddress
import socket

import keyring
import pytest


class NetworkBlocked(BaseException):
    """Raised when a test tries to reach a non-local host.

    A BaseException on purpose: HTTP clients (the Anthropic SDK included)
    catch Exception during a request and retry it as a "connection error",
    which would hide the mistake. BaseException passes through those
    handlers, and pytest still reports it as a test failure.
    """


def _is_local(host) -> bool:
    if host in (None, "", b""):
        return True  # getaddrinfo(None, port) is used to bind a local listener
    if isinstance(host, bytes):
        host = host.decode()
    host = str(host).strip("[]").split("%")[0]  # "[::1]" and "fe80::1%eth0" forms
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False  # any other hostname


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """Allow loopback, block everything else.

    Why not simply block socket.connect? On Windows, asyncio connects to
    127.0.0.1 internally to build its event loop, so a blanket block breaks
    every async test. And async clients connect through Windows' ConnectEx,
    which skips socket.connect entirely. Both sync and async clients resolve
    hostnames through socket.getaddrinfo first, so that is the reliable gate;
    the connect guard additionally catches sync connections to raw IPs.
    """
    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect

    def guarded_getaddrinfo(host, *args, **kwargs):
        if not _is_local(host):
            raise NetworkBlocked(f"network access blocked in tests: {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    def guarded_connect(self, address):
        if isinstance(address, tuple) and not _is_local(address[0]):
            raise NetworkBlocked(f"network access blocked in tests: {address[0]!r}")
        return real_connect(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)

    # A local proxy (VPN client, corporate agent) listens on 127.0.0.1, which
    # the guard allows, and would forward the real request. Ignore all proxies.
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    monkeypatch.setenv("NO_PROXY", "*")  # also stops urllib reading the Windows registry proxy


@pytest.fixture(autouse=True)
def vault(monkeypatch):
    """In-memory stand-in for the OS keychain, shared by every module that uses keyring."""
    store: dict[tuple[str, str], str] = {}

    def delete(service, user):
        store.pop((service, user), None)

    monkeypatch.setattr(keyring, "set_password", lambda s, u, p: store.__setitem__((s, u), p))
    monkeypatch.setattr(keyring, "get_password", lambda s, u: store.get((s, u)))
    monkeypatch.setattr(keyring, "delete_password", delete)
    return store
