"""The engine's scheduler: wall-clock due times that survive sleep and clock changes.

Run: pytest
"""

import threading
from datetime import datetime, timedelta, timezone

import pytest

from assistant.engine.scheduler import Scheduler

START = datetime(2026, 10, 25, 3, tzinfo=timezone.utc)
MIN = timedelta(minutes=1)


class FakeClock:
    def __init__(self, now=START):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, delta):
        self.now += delta


def recorder(runs, name, result=None):
    def job(now):
        runs.append(name)
        return result
    return job


def test_jobs_run_at_once_then_at_their_interval():
    clock, runs = FakeClock(), []
    sched = Scheduler(clock)
    sched.add("check", 2 * MIN, recorder(runs, "check"))
    sched.add("sweep", 60 * MIN, recorder(runs, "sweep"))
    assert sched.tick() == ["check", "sweep"]  # registration order
    clock.advance(MIN)
    assert sched.tick() == []
    clock.advance(MIN)
    assert sched.tick() == ["check"]
    assert sched.next_due("check") == clock.now + 2 * MIN and sched.next_due("sweep") == START + 60 * MIN


def test_a_job_can_ask_for_a_different_next_delay():
    clock, runs = FakeClock(), []
    sched = Scheduler(clock)
    sched.add("check", 2 * MIN, recorder(runs, "check", result=15 * MIN))  # offline: back off
    sched.tick()
    assert sched.next_due("check") == START + 15 * MIN


def test_run_soon_runs_a_job_on_the_next_tick():
    clock, runs = FakeClock(), []
    sched = Scheduler(clock)
    sched.add("extract", MIN, recorder(runs, "extract"))
    sched.tick()
    sched.run_soon("extract")
    assert sched.tick() == ["extract"]
    assert sched.tick() == []  # once, not on every tick after


def test_a_job_that_asks_for_a_later_one_gets_it_in_the_same_tick():
    clock, runs = FakeClock(), []
    sched = Scheduler(clock)
    sched.add("check", 2 * MIN, lambda now: sched.run_soon("extract"))
    sched.add("extract", 10 * MIN, recorder(runs, "extract"))
    sched.tick()
    clock.advance(2 * MIN)
    assert sched.tick() == ["check", "extract"]  # new mail is extracted without waiting a tick


def test_a_failing_job_is_reported_and_the_others_still_run():
    clock, runs, errors = FakeClock(), [], []
    sched = Scheduler(clock, on_error=lambda name, exc: errors.append((name, type(exc).__name__)))

    def broken(now):
        raise RuntimeError("boom")

    sched.add("broken", 5 * MIN, broken)
    sched.add("check", 2 * MIN, recorder(runs, "check"))
    assert sched.tick() == ["broken", "check"]
    assert errors == [("broken", "RuntimeError")] and runs == ["check"]
    assert sched.next_due("broken") == START + 5 * MIN


def test_ctrl_c_in_a_job_is_not_swallowed():
    sched = Scheduler(FakeClock(), on_error=lambda name, exc: None)

    def interrupted(now):
        raise KeyboardInterrupt

    sched.add("check", MIN, interrupted)
    with pytest.raises(KeyboardInterrupt):
        sched.tick()


def test_waking_from_sleep_runs_each_job_once_not_once_per_missed_interval():
    clock, runs = FakeClock(), []
    sched = Scheduler(clock)
    sched.add("check", 2 * MIN, recorder(runs, "check"))
    sched.tick()
    clock.advance(timedelta(hours=2))
    assert sched.tick() == ["check"]
    assert sched.next_due("check") == clock.now + 2 * MIN
    assert sched.tick() == []


def test_waking_runs_every_job_even_one_not_due_yet():
    clock, runs = FakeClock(), []
    sched = Scheduler(clock)
    sched.add("check", 2 * MIN, recorder(runs, "check"))
    sched.add("sweep", 60 * MIN, recorder(runs, "sweep"))
    sched.tick()
    clock.advance(4 * MIN)  # more than the 3-minute wake gap: the laptop slept
    assert sched.tick() == ["check", "sweep"]


def test_a_clock_that_goes_backwards_still_runs_jobs():
    clock, runs = FakeClock(), []
    sched = Scheduler(clock)
    sched.add("check", 2 * MIN, recorder(runs, "check"))
    sched.add("sweep", 60 * MIN, recorder(runs, "sweep"))
    sched.tick()
    clock.advance(10 * MIN)
    sched.tick()
    clock.advance(-timedelta(hours=1))
    assert sched.tick() == ["check", "sweep"]
    assert sched.next_due("check") == clock.now + 2 * MIN  # not an hour away


def test_run_forever_stops_when_the_event_is_set():
    clock, runs, stop = FakeClock(), [], threading.Event()

    def job(now):
        runs.append(now)
        if len(runs) == 3:
            stop.set()

    sched = Scheduler(clock)
    sched.add("check", timedelta(0), job)
    sched.run_forever(stop, poll_seconds=0.001)
    assert len(runs) == 3
