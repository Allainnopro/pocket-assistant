"""One engine at a time, and no CLI writer racing it.

Run: pytest
"""

import os
import shutil
from datetime import datetime, timezone
import subprocess
import sys
from pathlib import Path

import pytest

from assistant.cli import main
from assistant.config import load_config
from assistant.engine.lock import EngineLock, engine_running, lock_path
from assistant.errors import EngineRunning

ROOT = Path(__file__).parent.parent
HOLDER = """
import sys
from pathlib import Path
from assistant.engine.lock import EngineLock
EngineLock(Path(sys.argv[1])).acquire()
print("held", flush=True)
sys.stdin.read()  # hold it until killed
"""


@pytest.fixture
def config_file(tmp_path):
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    shutil.copy(ROOT / "profile.example.toml", tmp_path / "profile.toml")
    return tmp_path / "config.toml"


def test_a_second_lock_is_refused_while_the_first_is_held(tmp_path):
    path = tmp_path / "engine.lock"
    assert not engine_running(path)
    with EngineLock(path):
        assert engine_running(path)
        with pytest.raises(EngineRunning):
            EngineLock(path).acquire(wait_seconds=0)
        assert path.read_text(encoding="utf-8").split() == [str(os.getpid()), "engine"]  # for a human looking
    assert not engine_running(path)
    with EngineLock(path):  # and it can be taken again
        pass


def test_the_lock_frees_when_its_holder_process_dies(tmp_path):
    path = tmp_path / "engine.lock"
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    holder = subprocess.Popen([sys.executable, "-c", HOLDER, str(path)], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True, env=env)
    try:
        assert holder.stdout.readline().strip() == "held"
        assert engine_running(path)
        with pytest.raises(EngineRunning):
            EngineLock(path).acquire(wait_seconds=0)
    finally:
        holder.kill()  # as Task Manager or a crash would: no clean-up code runs
        holder.wait(timeout=10)
    # Windows frees a dead holder's lock moments after the process ends, not at once
    # (measured: 15-30 ms); a restart straight after a crash must still get it
    with EngineLock(path):
        pass
    assert not engine_running(path)


@pytest.mark.parametrize("command", [["sync"], ["filter"], ["extract"], ["rescore"]])
def test_writing_commands_refuse_while_the_engine_runs(config_file, command):
    with EngineLock(lock_path(load_config(config_file))):
        with pytest.raises(SystemExit) as info:
            main(["--config", str(config_file), *command])
    assert "engine is running" in str(info.value.code)


@pytest.mark.parametrize("command", [["stats"], ["extract", "--estimate"], ["filter", "--dry-run"],
                                     ["opportunities"], ["costs"]])
def test_reading_commands_still_work_while_the_engine_runs(config_file, command, capsys):
    with EngineLock(lock_path(load_config(config_file))):
        main(["--config", str(config_file), *command])  # no SystemExit



def test_a_writing_command_holds_the_lock_while_it_runs(config_file, monkeypatch):
    import assistant.cli as cli

    seen = []
    monkeypatch.setattr(cli, "rescore", lambda cfg: seen.append(engine_running(lock_path(cfg))))
    main(["--config", str(config_file), "rescore"])
    assert seen == [True]  # an engine started now would be refused, not run alongside
    assert not engine_running(lock_path(load_config(config_file)))


def test_a_refusal_names_the_command_that_holds_the_lock(tmp_path):
    path = tmp_path / "engine.lock"
    with EngineLock(path, role="sync"):
        with pytest.raises(EngineRunning, match="assistant sync"):
            EngineLock(path).acquire(wait_seconds=0)


def test_cli_extract_gives_back_attempts_held_by_a_killed_run(config_file):
    from fake_llm import SameReplyLLM
    from test_pipeline import add_mail, item, reply

    from assistant.cli import extract
    from assistant.costs import Caps
    from assistant.store import Store

    cfg = load_config(config_file)
    store = Store(cfg.db_path)
    add_mail(store, "m1")
    store.claim("m1")
    store.reserve_llm_call(now_utc=datetime(2026, 10, 16, 3, tzinfo=timezone.utc), purpose="extract",
                           model="claude-haiku-5-5", estimate_usd=0.004, caps=Caps(), message_id="m1")
    store.close()
    extract(cfg, llm=SameReplyLLM(reply([item()])), clock=lambda: datetime(2026, 10, 16, 3, tzinfo=timezone.utc))
    store = Store(cfg.db_path)
    assert store.conn.execute("SELECT attempts, status FROM mail_items").fetchone()[:] == (1, "extracted")
    store.close()
