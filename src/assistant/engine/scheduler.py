"""A small wall-clock scheduler for the engine's jobs.

Why not sleep(interval) per job: a laptop sleeps. A timer that counted 2 minutes
before the lid closed would fire hours late, or fire every missed interval at
once after it opens. Here each job has a due time on the wall clock, checked
every 15 seconds:

- After a run, the next due time is now + the interval (never due + interval),
  so after a long sleep a job runs once, not once per interval it missed.
- A gap of more than 3 minutes between checks means the laptop slept, and a
  clock that went backwards (a time-zone or manual change) would otherwise
  leave every due time far in the future. Either way, every job runs now.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

# A job gets the time it started; it may return a delay that replaces its
# interval for the next run only (an offline check backing off, say).
JobFn = Callable[[datetime], timedelta | None]


def _print_error(name: str, exc: BaseException) -> None:
    print(f"{name} failed: {type(exc).__name__}", file=sys.stderr)


@dataclass
class _Job:
    name: str
    every: timedelta
    fn: JobFn
    due: datetime


class Scheduler:
    def __init__(self, clock: Callable[[], datetime], *, wake_gap: timedelta = timedelta(minutes=3),
                 on_error: Callable[[str, BaseException], None] = _print_error) -> None:
        self.clock = clock
        self.wake_gap = wake_gap
        self.on_error = on_error
        self._jobs: dict[str, _Job] = {}  # in registration order
        self._soon: set[str] = set()
        self._lock = threading.Lock()
        self._last_tick: datetime | None = None

    def add(self, name: str, every: timedelta, fn: JobFn) -> None:
        """The first run is due at once."""
        with self._lock:
            self._jobs[name] = _Job(name, every, fn, due=self.clock())

    def run_soon(self, name: str) -> None:
        """Run the job at the next chance: later in this tick if it hasn't been reached
        yet, otherwise the next one. Safe to call from any thread."""
        with self._lock:
            if name not in self._jobs:
                raise KeyError(name)
            self._soon.add(name)

    def next_due(self, name: str) -> datetime:
        with self._lock:
            return self._jobs[name].due

    def tick(self) -> list[str]:
        """Run every due job once, in registration order. Returns their names."""
        now = self.clock()
        if self._last_tick is not None and (now - self._last_tick > self.wake_gap or now < self._last_tick):
            with self._lock:
                self._soon.update(self._jobs)  # woke from sleep, or the clock moved back
        ran = []
        for job in list(self._jobs.values()):
            with self._lock:
                started = self.clock()
                if job.name not in self._soon and started < job.due:
                    continue
                self._soon.discard(job.name)  # a run_soon during the run asks for another
            delay = None
            try:
                delay = job.fn(started)
            except Exception as exc:  # one broken job must not stop the others
                self.on_error(job.name, exc)
            ran.append(job.name)
            with self._lock:
                job.due = self.clock() + (delay if delay is not None else job.every)
        # Measured after the jobs, so a long extraction run doesn't look like a sleep
        self._last_tick = self.clock()
        return ran

    def run_forever(self, stop: threading.Event, poll_seconds: float = 15.0) -> None:
        while not stop.is_set():
            self.tick()
            stop.wait(poll_seconds)
