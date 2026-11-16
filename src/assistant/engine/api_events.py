"""The live event stream (server-sent events): what the engine tells the dashboard as it happens.

A stream ends when the engine stops, when the client goes away (Starlette cancels it;
the generator's `finally` unsubscribes), or, for a paired device, when its key stops
working: a revoked laptop mustn't keep listening for days. FastAPI itself sends a
": ping" comment after 15 s of silence.
"""

from __future__ import annotations

import queue
import time
from collections.abc import AsyncIterator
from datetime import timedelta

import anyio
from fastapi import APIRouter, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

from .api_deps import Authorized, Ctx, Db
from .devices import device_for_key

router = APIRouter()

RECHECK_KEY = timedelta(seconds=15)
WAIT = 1.0  # seconds per wait for an event: how soon a stop is noticed


@router.get("/api/events", response_class=EventSourceResponse)
async def events(who: Authorized, ctx: Ctx, db: Db, request: Request) -> AsyncIterator[ServerSentEvent]:
    key = request.headers["authorization"].partition(" ")[2]  # caller() already checked its form
    q = ctx.bus.subscribe(max_queue=100)
    try:
        recheck_at = time.monotonic() + RECHECK_KEY.total_seconds()
        while not ctx.stop.is_set():
            try:
                event = await anyio.to_thread.run_sync(q.get, True, WAIT, abandon_on_cancel=True)
            except queue.Empty:
                pass
            else:
                # every subscriber gets the same dict: copy, never change it
                yield ServerSentEvent(event=event["kind"], data={k: v for k, v in event.items() if k != "kind"})
            if who.kind == "device" and time.monotonic() >= recheck_at:
                # in a thread: noting "last seen" may wait up to 10 s for the database's write lock
                if await anyio.to_thread.run_sync(device_for_key, db, key, ctx.clock()) != who.device_id:
                    return
                recheck_at = time.monotonic() + RECHECK_KEY.total_seconds()
    finally:
        ctx.bus.unsubscribe(q)
