"""The engine put together: settings, one real tick, logs, crashes, the lock.

Run: pytest
"""

import logging
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx2
import pytest
from api_helpers import bearer, free_port
from fake_mailbox import FakeMailbox
from fake_llm import SameReplyLLM
from test_parser import b64, make_message
from test_pipeline import item, reply
from test_scheduler import FakeClock

from assistant.config import load_config
from assistant.engine.events import EventBus
from assistant.engine.lock import EngineLock, lock_path
from assistant.engine.logs import setup_logging, stop_logging
from assistant.engine.runtime import Engine, run_engine, wait_until_stopped
from assistant.errors import ConfigError, EngineRunning
from assistant.store import Store

ROOT = Path(__file__).parent.parent
NOW = datetime(2026, 10, 25, 3, tzinfo=timezone.utc)
FAKE_KEY = "sk-ant-api03-" + "Zx9_-" * 18 + "wxyz"  # the shape of a real key, not a real key
SECRET_LINE = "Interview code for candidates only: 7Q2-PRIVATE"  # stands in for a mail body


def write_config(tmp_path, engine_section=""):
    # a free API port: a test must never collide with a running engine's 47615
    (tmp_path / "config.toml").write_text(f"{engine_section}\n[api]\nport = {free_port()}\n", encoding="utf-8")
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
    stop_logging()  # release engine.log, or Windows can't delete tmp_path


def engine_for(cfg, logger, llm=None):
    bus = EventBus()
    events = bus.subscribe()
    gmail = FakeMailbox([job_alert("a"), job_alert("b")])
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
    assert "3 new jobs for you" not in text  # subjects outlive the 7-day body purge: ids only


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
    assert drain(events)[0] == {"kind": "status", "source": "job:sweep", "problem": "job_error",
                                "detail": "sweep: ValueError"}
    assert "bookmark" in gmail.calls


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

    cfg = write_config(tmp_path)
    cfg.profile_path.write_text("[boost\n", encoding="utf-8")
    monkeypatch.setattr("assistant.llm.VaultLLM", lambda: SameReplyLLM(reply([])))
    try:
        with pytest.raises(ConfigError, match="profile.toml"):
            run_engine(cfg)
    finally:
        stop_logging()
    assert "Engine running" not in capsys.readouterr().out
    assert not engine_running(lock_path(cfg))


def test_a_job_that_runs_cleanly_again_ends_its_error(tmp_path, logger, monkeypatch):
    from datetime import timedelta

    real, calls = Store.purge_stale_bodies, []

    def fails_once(self, days=7):
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("x")
        return real(self, days)

    monkeypatch.setattr(Store, "purge_stale_bodies", fails_once)
    engine, _, _ = engine_for(write_config(tmp_path), logger)
    try:
        engine.scheduler.tick()
        assert engine.bus.problems()["job:sweep"]["problem"] == "job_error"
        engine.scheduler.clock.advance(timedelta(hours=1))
        engine.scheduler.tick()
        assert "job:sweep" not in engine.bus.problems()
    finally:
        engine.close()



def test_a_blocked_console_never_holds_up_the_engine(tmp_path):
    import io
    import time

    release = threading.Event()

    class FrozenConsole(io.StringIO):  # a QuickEdit selection in a classic console window
        def write(self, text):
            release.wait(5)
            return super().write(text)

    log = setup_logging(tmp_path / "logs", console=FrozenConsole())
    try:
        start = time.monotonic()
        for n in range(3):
            log.info("line %d", n)
        assert time.monotonic() - start < 1
        for handler in log.handlers:
            handler.flush()
        assert "line 2" in (tmp_path / "logs" / "engine.log").read_text(encoding="utf-8")
    finally:
        release.set()
        stop_logging()


def test_a_killed_engine_gives_back_the_attempts_it_held(tmp_path, logger):
    from test_pipeline import add_mail

    from assistant.costs import Caps

    cfg = write_config(tmp_path)
    store = Store(cfg.db_path)
    add_mail(store, "m1")
    store.claim("m1")  # the engine was killed while m1's call was on its way
    store.reserve_llm_call(now_utc=NOW, purpose="extract", model="claude-haiku-5-5", estimate_usd=0.004,
                           caps=Caps(), message_id="m1")
    store.close()
    engine, _, _ = engine_for(cfg, logger)
    engine.close()
    store = Store(cfg.db_path)
    assert store.conn.execute("SELECT attempts FROM mail_items").fetchone()[0] == 0
    assert store.conn.execute("SELECT outcome FROM llm_calls").fetchone()[0] == "unknown"  # may have been billed
    store.close()


def test_the_engine_warns_about_query_terms_its_mail_check_cannot_apply(tmp_path, logger):
    cfg = write_config(tmp_path, '[gmail]\nquery = "newer_than:{days}d -in:spam -category:social from:boss@example.com"\n')
    engine, _, _ = engine_for(cfg, logger)
    engine.close()
    for handler in logger.handlers:
        handler.flush()
    text = (tmp_path / "logs" / "engine.log").read_text(encoding="utf-8")
    assert "from:boss@example.com" in text and "-category:social" not in text


def test_a_second_ctrl_c_quits_at_once(tmp_path, monkeypatch):
    import assistant.engine.runtime as runtime

    cfg = write_config(tmp_path)
    stops, quits = [], []

    seen = {}

    class Idle:  # an engine with nothing to do: this test is about the Ctrl+C path
        def __init__(self, cfg, **kwargs):
            stops.append(kwargs["stop"])
            seen.update(kwargs)
            self.holder, self.bus = None, kwargs["bus"]

        def run(self):
            stops[0].wait(5)

        def close(self):
            pass

    def second_ctrl_c(thread, stop, **kwargs):  # one that lands outside the sleep, before stop is set
        raise KeyboardInterrupt

    def quit_now():
        quits.append(stops[0].is_set())
        raise SystemExit(130)

    monkeypatch.setattr("assistant.llm.VaultLLM", lambda: SameReplyLLM(reply([])))
    monkeypatch.setattr(runtime, "Engine", Idle)
    monkeypatch.setattr(runtime, "wait_until_stopped", second_ctrl_c)
    monkeypatch.setattr(runtime, "_quit_now", quit_now)
    try:
        with pytest.raises(SystemExit):
            run_engine(cfg)
    finally:
        stop_logging()
    assert quits == [True]  # no further email starts: the engine's stop was set first


def test_the_engine_uses_the_password_fingerprint(tmp_path, monkeypatch):
    import assistant.engine.runtime as runtime
    from assistant import gmail_imap

    cfg = write_config(tmp_path)
    seen, opened = {}, []

    class Idle:  # an engine with nothing to do: this test is about what it is handed
        def __init__(self, cfg, **kwargs):
            seen.update(kwargs)
            self.holder, self.bus = None, kwargs["bus"]

        def run(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr("assistant.llm.VaultLLM", lambda: SameReplyLLM(reply([])))
    monkeypatch.setattr(runtime, "Engine", Idle)
    monkeypatch.setattr(runtime, "wait_until_stopped", lambda thread, stop, **kwargs: thread.join(5))
    monkeypatch.setattr(gmail_imap, "open_mailbox", lambda cfg: opened.append(cfg) or "a mailbox")
    try:
        run_engine(cfg)
    finally:
        stop_logging()
    assert seen["gmail_token"] is gmail_imap.token_fingerprint  # it notices a new `assistant set-key gmail`
    assert seen["gmail"]() == "a mailbox" and opened == [cfg]


def test_quitting_now_flushes_the_log_and_exits_with_130(monkeypatch):
    import assistant.engine.runtime as runtime

    seen = []
    monkeypatch.setattr(runtime.logging, "shutdown", lambda: seen.append("flushed"))
    monkeypatch.setattr(runtime.os, "_exit", lambda code: seen.append(code))
    runtime._quit_now()
    assert seen == ["flushed", 130]


def test_a_stopping_engine_starts_no_extraction(tmp_path, logger):
    llm = SameReplyLLM(reply([item()]))
    engine, _, _ = engine_for(write_config(tmp_path), logger, llm=llm)
    try:
        engine.stop.set()  # Ctrl+C
        engine.scheduler.tick()  # (a job already running finishes; extraction starts nothing new)
    finally:
        engine.close()
    assert llm.calls == 0



def test_the_engine_switches_to_a_new_gmail_sign_in(tmp_path, logger):
    from datetime import timedelta

    saved, built = [b"A"], []
    gmail = FakeMailbox()

    def factory():
        built.append(saved[0])
        return gmail

    engine = Engine(write_config(tmp_path), clock=FakeClock(NOW), gmail=factory, llm=SameReplyLLM(reply([])),
                    bus=EventBus(), log=logger, gmail_token=lambda: saved[0])
    try:
        engine.scheduler.tick()
        saved[0] = b"B"
        engine.scheduler.clock.advance(timedelta(minutes=2))
        engine.scheduler.tick()
    finally:
        engine.close()
    assert built == [b"A", b"B"]


def start_engine(cfg, monkeypatch, capsys, **kwargs):
    """run_engine in a thread, on fakes; returns (thread, printed so far, stop it, errors)."""
    import assistant.engine.runtime as runtime
    from assistant import gmail_imap

    go, errors = threading.Event(), []

    def until_the_test_says(thread, stop, **_):
        go.wait(30)
        stop.set()
        thread.join(30)

    def run():
        try:
            run_engine(cfg, **kwargs)
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr("assistant.llm.VaultLLM", lambda: SameReplyLLM(reply([])))
    monkeypatch.setattr(gmail_imap, "open_mailbox", lambda cfg: FakeMailbox([]))
    monkeypatch.setattr(runtime, "wait_until_stopped", until_the_test_says)
    # never the checkout's real app/.launch-password: a dev engine may be running from it
    monkeypatch.setattr(runtime, "DEV_PASSWORD_FILE", cfg.db_path.parent / "app" / ".launch-password")
    runner = threading.Thread(target=run, daemon=True)
    runner.start()
    out, deadline = "", time.monotonic() + 15
    try:
        while "API on " not in out or not out.endswith("\n"):
            assert runner.is_alive() and time.monotonic() < deadline, (out, errors)
            time.sleep(0.05)
            out += capsys.readouterr().out
    except BaseException:
        go.set()  # let the engine stop, not hang on
        raise
    time.sleep(0.2)  # the password line, with --dev, follows the API line
    out += capsys.readouterr().out
    return runner, out, go, errors


def test_the_engine_serves_its_api_while_it_runs(tmp_path, monkeypatch, capsys):
    import re

    from assistant.engine import api

    cfg = write_config(tmp_path)
    contexts = []
    monkeypatch.setattr(api, "create_app", lambda ctx, build=api.create_app: contexts.append(ctx) or build(ctx))
    runner, out, go, errors = start_engine(cfg, monkeypatch, capsys, dev=True)
    try:
        port = int(re.search(r"API on http://127\.0\.0\.1:(\d+)", out)[1])
        password = re.search(r"Launch password: (\S+)", out)[1]
        assert port == cfg.api.port
        r = httpx2.get(f"http://127.0.0.1:{port}/api/today",
                       headers={**bearer(password), "Origin": "http://localhost:5173"})
        assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "http://localhost:5173"
    finally:
        go.set()
        runner.join(30)
        stop_logging()
    assert errors == [] and not runner.is_alive()
    assert contexts[0].stop.is_set()  # the engine's own stop: open event streams end with it
    with pytest.raises(httpx2.ConnectError):
        httpx2.get(f"http://127.0.0.1:{port}/api/health")
    assert password not in (tmp_path / "logs" / "engine.log").read_text(encoding="utf-8")


def test_the_launch_password_is_shown_only_with_dev(tmp_path, monkeypatch, capsys):
    runner, out, go, errors = start_engine(write_config(tmp_path), monkeypatch, capsys)
    go.set()
    runner.join(30)
    stop_logging()
    assert errors == [] and "API on http://127.0.0.1:" in out and "password" not in out.lower()


def test_a_busy_api_port_stops_the_start_and_frees_the_lock(tmp_path, monkeypatch):
    import socket

    from assistant.engine.lock import engine_running

    import assistant.engine.runtime as runtime

    cfg = write_config(tmp_path)
    monkeypatch.setattr("assistant.llm.VaultLLM", lambda: SameReplyLLM(reply([])))
    # should the start go ahead anyway, end it instead of waiting for a Ctrl+C
    monkeypatch.setattr(runtime, "wait_until_stopped", lambda thread, stop, **_: (stop.set(), thread.join(10)))
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", cfg.api.port))
        taken.listen()
        try:
            with pytest.raises(ConfigError, match="in use"):
                run_engine(cfg)
        finally:
            stop_logging()
    assert not engine_running(lock_path(cfg))
    assert not any(t.name == "engine" and t.is_alive() for t in threading.enumerate())


def test_a_second_ctrl_c_while_the_api_stops_quits_at_once(tmp_path, monkeypatch, capsys):
    import assistant.engine.runtime as runtime
    from assistant.engine.server import ApiServer

    real_stop, quits = ApiServer.stop, []

    def interrupted(self, timeout=5.0):  # the second Ctrl+C lands while the server is stopping
        real_stop(self, timeout)
        raise KeyboardInterrupt

    def quit_now():
        quits.append(True)
        raise SystemExit(130)

    monkeypatch.setattr(ApiServer, "stop", interrupted)
    monkeypatch.setattr(runtime, "_quit_now", quit_now)
    runner, out, go, errors = start_engine(write_config(tmp_path), monkeypatch, capsys)
    go.set()
    runner.join(30)
    stop_logging()
    assert quits == [True] and [type(e) for e in errors] == [SystemExit]


def test_dev_mode_hands_the_password_to_the_dashboard_and_takes_it_back(tmp_path, monkeypatch, capsys):
    # M3: the browser dashboard signs in by itself; the file lives only while the engine runs
    import re

    import assistant.engine.runtime as runtime

    handoff = tmp_path / "app" / ".launch-password"
    handoff.parent.mkdir()
    monkeypatch.setattr(runtime, "DEV_PASSWORD_FILE", handoff)
    runner, out, go, errors = start_engine(write_config(tmp_path), monkeypatch, capsys, dev=True)
    try:
        password = re.search(r"Launch password: (\S+)", out)[1]
        assert handoff.read_text(encoding="utf-8") == password
    finally:
        go.set()
        runner.join(30)
        stop_logging()
    assert errors == [] and not handoff.exists()


def test_without_dev_no_password_file_is_written(tmp_path, monkeypatch, capsys):
    import assistant.engine.runtime as runtime

    handoff = tmp_path / "app" / ".launch-password"
    handoff.parent.mkdir()
    monkeypatch.setattr(runtime, "DEV_PASSWORD_FILE", handoff)
    runner, out, go, errors = start_engine(write_config(tmp_path), monkeypatch, capsys)
    assert not handoff.exists()
    go.set()
    runner.join(30)
    stop_logging()
    assert errors == []


def test_the_engine_re_sorts_stored_mail_when_the_rules_changed(tmp_path, logger):
    from test_pipeline import add_mail

    from assistant.store import Store

    cfg = write_config(tmp_path)
    store = Store(cfg.db_path)
    add_mail(store, "m1", sender="Facebook <n@facebookmail.com>", domain="facebookmail.com", source="other")
    store.conn.execute("UPDATE mail_items SET rule_kind = NULL, rule_sure = NULL")
    store.commit()
    store.set_state("kind_rules_version", "0")  # sorted by older rules
    store.close()
    engine, _, _ = engine_for(cfg, logger)
    try:
        assert tuple(engine.store.conn.execute("SELECT rule_kind, rule_sure FROM mail_items").fetchone()) == ("social", 1)
        assert not engine.store.kinds_outdated()
    finally:
        engine.close()


def never_called():
    raise AssertionError("a dev engine must never open Gmail")


def dev_engine(cfg, logger):
    return Engine(cfg, clock=FakeClock(NOW), gmail=never_called, llm=None, bus=EventBus(), log=logger, dev=True)


def test_dev_engine_registers_no_mail_check_and_no_extraction(tmp_path, logger):
    engine = dev_engine(write_config(tmp_path), logger)
    try:
        assert list(engine.scheduler._jobs) == ["sweep", "profile_watch"]
    finally:
        engine.close()


def test_dev_engine_never_opens_gmail_even_with_a_saved_password(tmp_path, logger, vault):
    cfg = write_config(tmp_path)
    vault[("gmail", cfg.gmail_address)] = "abcdefghijklmnop"
    engine = dev_engine(cfg, logger)
    try:
        engine.scheduler.tick()
        engine.scheduler.clock.advance(timedelta(hours=1))
        engine.scheduler.tick()
    finally:
        engine.close()


def test_dev_run_builds_no_vault_llm(tmp_path, monkeypatch):
    import assistant.engine.runtime as runtime

    cfg, seen = write_config(tmp_path), {}

    class Idle:  # this test is about what the engine is handed
        def __init__(self, cfg, **kwargs):
            seen.update(kwargs)
            self.holder, self.bus = None, kwargs["bus"]

        def run(self):
            pass

        def close(self):
            pass

    def no_llm():
        raise AssertionError("--dev must not build a VaultLLM")

    monkeypatch.setattr("assistant.llm.VaultLLM", no_llm)
    monkeypatch.setattr(runtime, "Engine", Idle)
    monkeypatch.setattr(runtime, "wait_until_stopped", lambda thread, stop, **kwargs: thread.join(5))
    monkeypatch.setattr(runtime, "DEV_PASSWORD_FILE", tmp_path / "app" / ".launch-password")
    try:
        run_engine(cfg, dev=True)
    finally:
        stop_logging()
    assert seen["llm"] is None and seen["dev"] is True


def test_the_engine_command_has_no_handshake_options():
    from assistant.cli import main

    with pytest.raises(SystemExit) as bad:
        main(["engine", "--parent-pid", "1"])
    assert bad.value.code == 2


def test_an_engine_already_running_exits_with_code_3(tmp_path, capsys):
    from assistant.cli import main
    from assistant.engine.lock import EngineLock, lock_path

    cfg = write_config(tmp_path)
    held = EngineLock(lock_path(cfg))
    held.acquire()
    try:
        with pytest.raises(SystemExit) as exited:
            main(["--config", str(tmp_path / "config.toml"), "engine"])
    finally:
        held.release()
    assert exited.value.code == 3  # the desktop app tells "already running" apart from a crash
    assert "engine is running" in capsys.readouterr().err
