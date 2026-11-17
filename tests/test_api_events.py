"""The live event stream the dashboard listens to.

Run: pytest
"""

import json
import threading
import time
from datetime import timedelta

import httpx2
import pytest
from api_helpers import PASSWORD, Api, api, bearer, live_api  # noqa: F401

from assistant.engine import api_events
from assistant.engine.devices import revoke
from assistant.engine.events import ITEM_CHANGED, ITEMS_NEW


def wait_for_subscribers(api: Api, n: int, within: float = 3.0) -> None:
    deadline = time.monotonic() + within
    while len(api.bus._subscribers) != n:
        assert time.monotonic() < deadline, f"{len(api.bus._subscribers)} subscribers, expected {n}"
        time.sleep(0.01)


def events_in(lines, wanted: int) -> list[tuple[str, dict]]:
    """(event, data) pairs, read until `wanted` have arrived."""
    found, kind = [], None
    for line in lines:
        if line.startswith("event: "):
            kind = line.removeprefix("event: ")
        elif line.startswith("data: "):
            found.append((kind, json.loads(line.removeprefix("data: "))))
            if len(found) == wanted:
                break
    return found


def finishes(target, within: float = 3.0) -> bool:
    """True when target() returns within the time."""
    done = threading.Event()
    threading.Thread(target=lambda: (target(), done.set()), daemon=True).start()
    return done.wait(within)


def test_the_stream_delivers_published_events_in_order(api):
    with api.client.stream("GET", "/api/events", headers=bearer(PASSWORD)) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        wait_for_subscribers(api, 1)
        api.bus.publish(ITEMS_NEW, {"new": 2, "merged": 0})
        api.bus.publish(ITEM_CHANGED, {"id": 7, "action": "star"})
        assert events_in(r.iter_lines(), 2) == [(ITEMS_NEW, {"new": 2, "merged": 0}),
                                                 (ITEM_CHANGED, {"id": 7, "action": "star"})]
        api.stop.set()


def test_every_open_stream_gets_each_event(api):  # the PC's window and the laptop at once
    with api.client.stream("GET", "/api/events", headers=bearer(PASSWORD)) as one, \
            api.client.stream("GET", "/api/events", headers=bearer(PASSWORD)) as two:
        wait_for_subscribers(api, 2)
        api.bus.publish(ITEMS_NEW, {"new": 2, "merged": 0})
        assert events_in(one.iter_lines(), 1) == events_in(two.iter_lines(), 1) == [(ITEMS_NEW, {"new": 2, "merged": 0})]
        api.stop.set()


def test_the_stream_needs_a_secret(api):
    assert api.client.get("/api/events").status_code == 401
    assert api.bus._subscribers == []


def test_stopping_the_engine_ends_every_stream(api):
    with api.client.stream("GET", "/api/events", headers=bearer(PASSWORD)) as r:
        wait_for_subscribers(api, 1)
        api.stop.set()
        assert finishes(lambda: list(r.iter_lines()))
    wait_for_subscribers(api, 0)


def test_a_dropped_event_stream_unsubscribes(live_api):  # Review Focus 1
    api, port = live_api
    with httpx2.stream("GET", f"http://127.0.0.1:{port}/api/events", headers=bearer(PASSWORD), timeout=5) as r:
        wait_for_subscribers(api, 1)
        api.bus.publish(ITEMS_NEW, {"new": 1, "merged": 0})
        assert events_in(r.iter_lines(), 1) == [(ITEMS_NEW, {"new": 1, "merged": 0})]
    wait_for_subscribers(api, 0)  # the connection is closed, the engine still runs


def test_a_revoked_device_loses_its_open_stream(live_api, monkeypatch):  # Review Focus 4
    api, port = live_api
    monkeypatch.setattr(api_events, "RECHECK_KEY", timedelta(seconds=0.2))
    device_id, key = api.pair()
    with httpx2.stream("GET", f"http://127.0.0.1:{port}/api/events", headers=bearer(key), timeout=5) as r:
        assert r.status_code == 200
        wait_for_subscribers(api, 1)
        revoke(api.store, device_id, api.clock())
        assert finishes(lambda: list(r.iter_lines()))
    wait_for_subscribers(api, 0)


def test_a_paired_device_keeps_its_stream_while_its_key_works(live_api, monkeypatch):
    api, port = live_api
    monkeypatch.setattr(api_events, "RECHECK_KEY", timedelta(seconds=0.2))
    _, key = api.pair()
    with httpx2.stream("GET", f"http://127.0.0.1:{port}/api/events", headers=bearer(key), timeout=5) as r:
        wait_for_subscribers(api, 1)
        time.sleep(1.5)  # several re-checks
        api.bus.publish(ITEMS_NEW, {"new": 3, "merged": 1})
        assert events_in(r.iter_lines(), 1) == [(ITEMS_NEW, {"new": 3, "merged": 1})]
