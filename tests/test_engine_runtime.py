"""The engine put together: settings, one real tick, logs, crashes, the lock.

Run: pytest
"""

import logging
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fake_gmail import FakeGmail
from fake_llm import SameReplyLLM
from test_parser import b64, make_message
from test_pipeline import item, reply
from test_scheduler import FakeClock

from assistant.config import load_config
from assistant.engine.events import EventBus
from assistant.engine.lock import EngineLock, lock_path
from assistant.engine.logs import setup_logging
from assistant.engine.runtime import Engine, run_engine, wait_until_stopped
from assistant.errors import ConfigError, EngineRunning
from assistant.store import Store

ROOT = Path(__file__).parent.parent
NOW = datetime(2026, 10, 25, 3, tzinfo=timezone.utc)
FAKE_KEY = "sk-ant-api03-" + "Zx9_-" * 18 + "wxyz"  # the shape of a real key, not a real key
SECRET_LINE = "Interview code for candidates only: 7Q2-PRIVATE"  # stands in for a mail body


def write_config(tmp_path, engine_section=""):
    (tmp_path / "config.toml").write_text(engine_section, encoding="utf-8")
    shutil.copy(ROOT / "profile.example.toml", tmp_path / "profile.toml")
    return load_config(tmp_path / "config.toml")


@pytest.mark.parametrize("value", [0, 9, '"4"', "true"])
def test_engine_settings_refuse_nonsense(tmp_path, value):
    with pytest.raises(ConfigError, match="extract_workers"):
        write_config(tmp_path, f"[engine]\nextract_workers = {value}\n")


def test_engine_settings_refuse_unknown_keys(tmp_path):
    with pytest.raises(ConfigError, match="workers"):
        write_config(tmp_path, "[engine]\nworkers = 2\n")


def test_engine_settings_default_to_4_workers(tmp_path):
    assert write_config(tmp_path).engine.extract_workers == 4
    assert write_config(tmp_path, "[engine]\nextract_workers = 2\n").engine.extract_workers == 2


def job_alert(msg_id):
    text = f"AI Engineer Intern at Example Robotics, Thu Duc, HCMC.\n{SECRET_LINE}"
    return make_message("Job Alerts <jobalerts-noreply@example.com>", "me@x.com", "3 new jobs for you",
                        [{"mimeType": "text/plain", "body": {"data": b64(text)}}], msg_id=msg_id)


@pytest.fixture
def logger(tmp_path):
    log = setup_logging(tmp_path / "logs")
    yield log
    for handler in list(log.handlers):  # release engine.log, or Windows can't delete tmp_path
        handler.close()
        log.removeHandler(handler)


def engine_for(cfg, logger, llm=None):
    bus = EventBus()
    events = bus.subscribe()
    gmail = FakeGmail([job_alert("a"), job_alert("b")], profile="me@x.com", history_id="200")
    engine = Engine(cfg, clock=FakeClock(NOW), gmail=lambda: gmail, llm=llm or SameReplyLLM(reply([item()])),
                    bus=bus, log=logger)
    return engine, gmail, events


def drain(q):
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    return events


def test_one_engine_run_checks_mail_then_extracts_it(tmp_path, logger):
    engine, gmail, events = engine_for(write_config(tmp_path), logger)
    try:
        assert engine.scheduler.tick() == ["sweep", "check_mail", "extract", "profile_watch"]
        assert engine.scheduler.tick() == []
    finally:
        engine.close()
    store = Store(tmp_path / "assistant.db")
    assert [r[0] for r in store.conn.execute("SELECT status FROM mail_items ORDER BY message_id")] == [
        "extracted", "extracted"]
    assert store.conn.execute("SELECT seen_count FROM opportunities").fetchall()[0][0] == 2
    store.close()
    assert [e["kind"] for e in drain(events)] == ["status", "items_new"]


def test_the_engine_log_never_holds_a_key_or_a_mail_body(tmp_path, logger):
    engine, _, _ = engine_for(write_config(tmp_path), logger)
    try:
        engine.scheduler.tick()
        logger.info("the key was %s", FAKE_KEY)
    finally:
        engine.close()
    for handler in logger.handlers:
        handler.flush()
    text = (tmp_path / "logs" / "engine.log").read_text(encoding="utf-8")
    assert "the key was sk-ant-[redacted]" in text
    assert FAKE_KEY not in text and "7Q2-PRIVATE" not in text and "check_mail: 2 new" in text


def test_a_job_that_crashes_is_logged_and_announced_and_the_engine_keeps_going(tmp_path, logger, monkeypatch):
    def broken(self, days=7):
        raise ValueError("x")

    monkeypatch.setattr(Store, "purge_stale_bodies", broken)
    engine, gmail, events = engine_for(write_config(tmp_path), logger)
    try:
        assert engine.scheduler.tick()[:2] == ["sweep", "check_mail"]
    finally:
        engine.close()
    for handler in logger.handlers:
        handler.flush()
    assert "sweep failed: ValueError: x" in (tmp_path / "logs" / "engine.log").read_text(encoding="utf-8")
    assert drain(events)[0] == {"kind": "status", "problem": "job_error", "detail": "sweep: ValueError"}
    assert "getProfile" in gmail.calls


def test_a_second_engine_refuses_to_start(tmp_path, monkeypatch):
    cfg = write_config(tmp_path)

    def must_not_build(*args, **kwargs):
        raise AssertionError("built something before checking the lock")

    monkeypatch.setattr("assistant.engine.runtime.setup_logging", must_not_build)
    with EngineLock(lock_path(cfg)):
        with pytest.raises(EngineRunning):
            run_engine(cfg)


def test_ctrl_c_lets_the_current_job_finish_then_stops():
    stop, finished = threading.Event(), []

    def engine_thread():
        stop.wait(5)
        finished.append(True)

    thread = threading.Thread(target=engine_thread)
    thread.start()
    presses = iter([KeyboardInterrupt])

    def sleep(seconds):
        press = next(presses, None)
        if press:
            raise press
        thread.join(0.01)

    lines = []
    wait_until_stopped(thread, stop, sleep=sleep, say=lines.append)
    assert stop.is_set() and finished == [True] and not thread.is_alive()
    assert "Stopping" in lines[0]


def test_a_broken_profile_stops_the_start_and_frees_the_lock(tmp_path, monkeypatch, capsys):
    from assistant.engine.lock import engine_running
    from assistant.llm import AnthropicLLM

    cfg = write_config(tmp_path)
    cfg.profile_path.write_text("[boost\n", encoding="utf-8")
    monkeypatch.setattr(AnthropicLLM, "from_vault", classmethod(lambda cls: SameReplyLLM(reply([]))))
    try:
        with pytest.raises(ConfigError, match="profile.toml"):
            run_engine(cfg)
    finally:
        log = logging.getLogger("assistant.engine")
        for handler in list(log.handlers):
            handler.close()
            log.removeHandler(handler)
    assert "Engine running" not in capsys.readouterr().out
    assert not engine_running(lock_path(cfg))
