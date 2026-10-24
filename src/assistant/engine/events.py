"""What the engine tells its listeners (the dashboard's live stream, from M2).

Each subscriber gets its own bounded queue. A listener that stops reading loses
its own oldest events, never anyone else's, and never blocks the engine.
Events carry counts and states only: no subjects, senders or bodies.
"""

from __future__ import annotations

import queue
import threading

ITEMS_NEW = "items_new"              # {"new": n, "merged": m}
STATUS = "status"                    # {"online", "last_ok_check_at", "problem", ...}
RANKING_CHANGED = "ranking_changed"  # {"rescored": n}


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self, max_queue: int = 100) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=max_queue)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def publish(self, kind: str, payload: dict) -> None:
        event = {"kind": kind, **payload}
        with self._lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            while True:
                try:
                    q.put_nowait(event)
                    break
                except queue.Full:
                    try:
                        q.get_nowait()  # make room: this subscriber loses its oldest event
                    except queue.Empty:
                        pass
