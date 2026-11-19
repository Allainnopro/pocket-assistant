"""parent_alive: the engine's check that the desktop app that started it is still there."""

import os
import subprocess
import sys

from assistant.engine.parent import parent_alive


def test_parent_alive_for_this_process_and_not_for_a_finished_one():
    assert parent_alive(os.getpid())
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait(30)
    assert not parent_alive(finished.pid)


def test_a_pid_nobody_has_is_not_alive():
    assert not parent_alive(2**31 - 7)
