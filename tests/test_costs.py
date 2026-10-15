"""Cost arithmetic, caps and the ledger. Expected dollars are worked out by
hand from the price table (dollars per million tokens), so a wrong formula
can't pass by agreeing with itself.

Run: pytest
"""

import json
import math
import threading
from datetime import datetime, timedelta, timezone

import pytest

from assistant.costs import (
    PRICES, Caps, Usage, allowed, attempts_cost, cost_usd, digest_worst_usd, estimate_usd, known_model,
    local_period, prices_for,
)
from assistant.errors import CapReached
from assistant.store import Store


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


# --- arithmetic ------------------------------------------------------------------

def test_typical_calls_cost_what_the_plan_estimated():
    # Haiku extraction: 1,900 in x $0.10 + 300 out x $0.50 = $0.00034
    assert cost_usd("claude-haiku-5-5", Usage(1_900, 300)) == pytest.approx(0.00034)
    # Opus digest: 4,000 in x $4 + 1,700 out (700 text + 1,000 thinking) x $20 = $0.05
    assert cost_usd("claude-opus-5-5", Usage(4_000, 1_700, thinking_tokens=1_000)) == pytest.approx(0.05)
    # Sonnet digest, same tokens: 4,000 x $2 + 1,700 x $10 = $0.025
    assert cost_usd("claude-sonnet-5-5", Usage(4_000, 1_700)) == pytest.approx(0.025)


def test_thinking_tokens_are_not_counted_twice():
    assert cost_usd("claude-opus-5-5", Usage(0, 1_000, thinking_tokens=800)) == pytest.approx(0.02)


def test_cache_reads_and_writes_use_their_own_prices():
    # Opus 5.5: 1,000 read x $0.20 + 2,000 5-min write x $5 + 500 1-hour write x $8 = $0.0142
    usage = Usage(cache_read_tokens=1_000, cache_write_5m_tokens=2_000, cache_write_1h_tokens=500)
    assert cost_usd("claude-opus-5-5", usage) == pytest.approx(0.0142)
    assert PRICES["claude-sonnet-5-5"].standard.cache_read == 0.10  # the live page, not the older $0.20


def test_haiku_long_prompt_tier_counts_cached_tokens_too():
    assert cost_usd("claude-haiku-5-5", Usage(100_000)) == pytest.approx(0.01)       # exactly 100K: standard
    assert cost_usd("claude-haiku-5-5", Usage(100_001)) == pytest.approx(0.0500005)  # over: $0.50 / MTok
    # 50K fresh + 60K cache read = 110K prompt -> long tier: 50K x $0.50 + 60K x $0.05 = $0.028
    assert cost_usd("claude-haiku-5-5", Usage(50_000, cache_read_tokens=60_000)) == pytest.approx(0.028)


def test_an_unknown_model_is_priced_expensively_never_free():
    assert prices_for("claude-typo-5-5").standard.input == 10.0
    assert cost_usd("claude-typo-5-5", Usage(1_000_000)) == pytest.approx(10.0)
    assert known_model("claude-sonnet-4-6") is None  # config.py refuses these up front


def test_a_dated_snapshot_id_is_priced_as_its_model():
    assert known_model("claude-haiku-4-5-20251001") == "claude-haiku-4-5"
    assert prices_for("claude-haiku-4-5-20251001") is PRICES["claude-haiku-4-5"]


@pytest.mark.parametrize("bad", [
    dict(input_tokens=-1_000_000),       # would lower the recorded spend and unblock the caps
    dict(output_tokens=math.inf),        # would jam the caps for the rest of the month
    dict(cache_read_tokens=1.5),
    dict(input_tokens=None),             # the SDK's "no cache" value must be mapped to 0 first
    dict(input_tokens=True),
    dict(output_tokens=10, thinking_tokens=11),
])
def test_impossible_usage_is_refused(bad):
    with pytest.raises(ValueError):
        Usage(**bad)


def test_fallback_attempts_are_each_priced_at_their_own_model():
    # Opus 5.5 refused after reading 1,000 tokens ($0.004); Opus 5 answered: 1,000 x $5 + 500 x $25
    attempts = [("claude-opus-5-5", Usage(1_000, 0)), ("claude-opus-5", Usage(1_000, 500))]
    assert attempts_cost(attempts) == pytest.approx(0.0215)


def test_worst_case_prices_every_prompt_token_as_a_1_hour_cache_write():
    # Opus 5.5: 4,000 in x $8 (1-hour write, the dearest input rate) + 8,000 max out x $20 = $0.192
    assert estimate_usd("claude-opus-5-5", 4_000, 8_000) == pytest.approx(0.192)
    # ...plus a full second attempt at the unknown-model price, which is what the ledger
    # would record for a fallback model missing from the table: 4,000 x $20 + 8,000 x $50 = $0.48
    assert estimate_usd("claude-opus-5-5", 4_000, 8_000, fallback=True) == pytest.approx(0.672)
    assert digest_worst_usd("claude-opus-5-5") == pytest.approx(0.672)


SPLITS = [  # the same prompt, billed every way the API can bill it
    lambda n: dict(input_tokens=n),
    lambda n: dict(cache_read_tokens=n),
    lambda n: dict(cache_write_5m_tokens=n),
    lambda n: dict(cache_write_1h_tokens=n),
    lambda n: dict(input_tokens=n // 4, cache_read_tokens=n // 4, cache_write_5m_tokens=n // 4,
                   cache_write_1h_tokens=n - 3 * (n // 4)),
]


@pytest.mark.parametrize("model, prompt", [
    ("claude-haiku-5-5", 1_900), ("claude-haiku-5-5", 150_000), ("claude-sonnet-5-5", 4_000),
    ("claude-opus-5-5", 20_000), ("claude-typo-5-5", 4_000),
])
@pytest.mark.parametrize("split", SPLITS)
def test_the_estimate_is_never_below_what_the_call_can_bill(model, prompt, split):
    max_tokens = 1_000
    usage = Usage(output_tokens=max_tokens, **split(prompt))
    assert cost_usd(model, usage) <= estimate_usd(model, prompt, max_tokens) + 1e-12
    for fallback_model in ("claude-opus-5", "claude-opus-4-8", "claude-fallback-from-the-future"):
        billed = attempts_cost([(model, usage), (fallback_model, usage)])
        assert billed <= estimate_usd(model, prompt, max_tokens, fallback=True) + 1e-12


# --- calendar and caps ---------------------------------------------------------------

def test_months_and_days_follow_midnight_in_ho_chi_minh_city():
    assert local_period(utc(2026, 10, 31, 16, 59)) == ("2026-10", "2026-10-31")
    assert local_period(utc(2026, 10, 31, 17, 0)) == ("2026-11", "2026-11-01")  # 00:00 in HCMC


def test_a_naive_time_is_refused_not_read_as_pc_local_time():
    with pytest.raises(ValueError, match="timezone-aware"):
        local_period(datetime(2026, 10, 31, 17, 30))  # what datetime.utcnow() returns


def test_caps():
    caps = Caps(monthly_soft_usd=5.0, daily_usd=1.0)
    assert allowed("digest", 4.0, 0.2, 0.5, caps).ok
    assert allowed("digest", 4.8, 0.2, 0.5, caps).reason == "monthly-cap"     # the digest stops...
    assert allowed("extract", 4.8, 0.2, 0.5, caps).ok                         # ...extraction continues
    assert allowed("extract", 9.0, 0.8, 0.5, caps).reason == "daily-cap"      # nothing passes the daily cap
    assert allowed("extract", 0.0, 0.5, 0.5, caps).ok                         # exactly at the cap is allowed
    assert allowed("smoke", 4.8, 0.2, 0.5, caps).reason == "monthly-cap"      # only extraction is essential


def test_an_unknown_purpose_is_refused_even_when_capped():
    with pytest.raises(ValueError, match="purpose"):
        allowed("extraction", 0.0, 5.0, 0.5, Caps())


# --- the ledger ------------------------------------------------------------------------

HAIKU_REPLY = [("claude-haiku-5-5", Usage(1_900, 300))]  # $0.00034


def reserve(store, estimate, purpose="extract", now=utc(2026, 10, 15, 3), caps=Caps(5.0, 1.0)):
    return store.reserve_llm_call(now_utc=now, purpose=purpose, model="claude-haiku-5-5",
                                  estimate_usd=estimate, caps=caps)


def row(store, call_id, columns="outcome, cost_usd"):
    return tuple(store.conn.execute(f"SELECT {columns} FROM llm_calls WHERE id = ?", (call_id,)).fetchone())


def test_a_call_is_recorded_before_it_runs_then_corrected(tmp_path):
    store = Store(tmp_path / "a.db")
    call_id = reserve(store, 0.002)
    assert store.llm_spent("2026-10", "2026-10-15") == pytest.approx((0.002, 0.002))  # the estimate counts
    assert store.settle_llm_call(call_id, outcome="ok", attempts=HAIKU_REPLY, request_id="req_1",
                                 stop_reason="end_turn")
    assert store.llm_spent("2026-10", "2026-10-15") == pytest.approx((0.00034, 0.00034))
    assert row(store, call_id, "outcome, model_served, input_tokens, output_tokens, request_id") == (
        "ok", "claude-haiku-5-5", 1_900, 300, "req_1")
    assert not store.settle_llm_call(call_id, outcome="api_error")  # settling twice changes nothing
    assert row(store, call_id)[0] == "ok"


def test_a_fallback_is_priced_per_attempt_and_credited_to_the_model_that_answered(tmp_path):
    store = Store(tmp_path / "a.db")
    call_id = reserve(store, 0.5, purpose="digest")
    attempts = [("claude-opus-5-5", Usage(1_000, 0)), ("claude-opus-5", Usage(1_000, 500))]
    store.settle_llm_call(call_id, outcome="ok", attempts=attempts)
    outcome, cost, served, tokens_in, tokens_out, saved = row(
        store, call_id, "outcome, cost_usd, model_served, input_tokens, output_tokens, attempts_json")
    assert (outcome, served, tokens_in, tokens_out) == ("ok", "claude-opus-5", 2_000, 500)
    assert cost == pytest.approx(0.0215)
    assert [a["model"] for a in json.loads(saved)] == ["claude-opus-5-5", "claude-opus-5"]


def test_a_call_that_may_have_been_billed_keeps_its_estimate(tmp_path):
    store = Store(tmp_path / "a.db")
    timed_out, rejected = reserve(store, 0.4), reserve(store, 0.3)
    store.settle_llm_call(timed_out, outcome="unknown", error_class="APITimeoutError")
    store.settle_llm_call(rejected, outcome="api_error", error_class="RateLimitError")  # never billed
    assert row(store, timed_out) == ("unknown", pytest.approx(0.4))
    assert row(store, rejected) == ("api_error", 0.0)


@pytest.mark.parametrize("kwargs, message", [
    (dict(outcome="ok"), "needs the billed attempts"),
    (dict(outcome="ok", attempts=[]), "needs the billed attempts"),
    (dict(outcome="ok", attempts=(a for a in [])), "needs the billed attempts"),  # an empty generator
    (dict(outcome="ok", attempts=[("claude-haiku-5-5", Usage())]), "read no prompt tokens"),
    (dict(outcome="ok", attempts=[Usage(1_900, 300)]), "pairs"),
    (dict(outcome="unknown", attempts=HAIKU_REPLY), "keeps the estimate"),
    (dict(outcome="api_error", attempts=HAIKU_REPLY), "bills nothing"),
    (dict(outcome="pending"), "not a settled outcome"),
    (dict(outcome="skipped_cap"), "not a settled outcome"),
])
def test_settle_refuses_a_record_that_does_not_add_up(tmp_path, kwargs, message):
    store = Store(tmp_path / "a.db")
    call_id = reserve(store, 0.01)
    with pytest.raises(ValueError, match=message):
        store.settle_llm_call(call_id, **kwargs)
    assert row(store, call_id) == ("pending", 0.01)


@pytest.mark.parametrize("call_id", [999, None])
def test_settling_a_call_that_was_never_reserved_is_an_error_not_a_silent_loss(tmp_path, call_id):
    store = Store(tmp_path / "a.db")
    with pytest.raises(ValueError, match="no LLM call"):
        store.settle_llm_call(call_id, outcome="ok", attempts=HAIKU_REPLY)


def test_a_blocked_call_raises_and_is_logged_as_skipped(tmp_path):
    store = Store(tmp_path / "a.db")
    reserve(store, 0.9)
    with pytest.raises(CapReached) as blocked:
        reserve(store, 0.2)
    assert blocked.value.reason == "daily-cap"
    assert row(store, 2, "outcome, cost_usd, error_class") == ("skipped_cap", 0.0, "daily-cap")


@pytest.mark.parametrize("estimate", [-5.0, math.nan, math.inf])
def test_a_bad_estimate_is_refused_before_anything_is_written(tmp_path, estimate):
    store = Store(tmp_path / "a.db")
    with pytest.raises(ValueError, match="non-negative"):
        reserve(store, estimate)
    assert store.conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 0


def test_the_soft_cap_stops_the_digest_but_not_extraction(tmp_path):
    store = Store(tmp_path / "a.db")
    caps = Caps(monthly_soft_usd=0.01, daily_usd=1.0)
    reserve(store, 0.009, caps=caps)
    with pytest.raises(CapReached, match="monthly-cap"):
        reserve(store, 0.005, purpose="digest", caps=caps)
    assert reserve(store, 0.005, purpose="extract", caps=caps)
    assert row(store, 3, "essential") == (1,)


def test_spend_rolls_over_at_local_midnight_and_is_stamped_in_utc(tmp_path):
    store = Store(tmp_path / "a.db")
    hcmc = timezone(timedelta(hours=7))
    call_id = reserve(store, 0.5, now=datetime(2026, 11, 1, 0, 30, tzinfo=hcmc))  # = 17:30 UTC on 31 Oct
    assert store.llm_spent("2026-10", "2026-10-31") == (0.0, 0.0)
    assert store.llm_spent("2026-11", "2026-11-01") == pytest.approx((0.5, 0.5))
    assert row(store, call_id, "ts_utc") == ("2026-10-31T17:30:00+00:00",)


# --- llm_call: the wrapper later steps use ------------------------------------------------

def open_call(store, estimate=0.01, purpose="extract"):
    return store.llm_call(now_utc=utc(2026, 10, 15, 3), purpose=purpose, model="claude-haiku-5-5",
                          estimate_usd=estimate, caps=Caps(5.0, 1.0))


def test_llm_call_settles_with_what_came_back(tmp_path):
    store = Store(tmp_path / "a.db")
    with open_call(store) as call:
        call.settle("ok", HAIKU_REPLY, request_id="req_1")
    assert row(store, call.id) == ("ok", pytest.approx(0.00034))


@pytest.mark.parametrize("crash", [TimeoutError, KeyboardInterrupt])
def test_a_call_that_dies_midway_keeps_its_estimate_and_the_error_carries_on(tmp_path, crash):
    store = Store(tmp_path / "a.db")
    with pytest.raises(crash):
        with open_call(store, estimate=0.4) as call:
            raise crash()
    assert row(store, call.id, "outcome, cost_usd, error_class") == ("unknown", pytest.approx(0.4), crash.__name__)


def test_forgetting_to_settle_keeps_the_estimate(tmp_path):
    store = Store(tmp_path / "a.db")
    with open_call(store, estimate=0.4) as call:
        pass
    assert row(store, call.id, "outcome, cost_usd, error_class") == ("unknown", pytest.approx(0.4), "NotSettled")


def test_a_capped_call_never_runs(tmp_path):
    store = Store(tmp_path / "a.db")
    ran = []
    with pytest.raises(CapReached):
        with open_call(store, estimate=2.0):
            ran.append(True)
    assert ran == []


def test_two_processes_cannot_both_slip_under_the_cap(tmp_path, monkeypatch):
    db = tmp_path / "a.db"
    Store(db).close()
    results = []
    # Force the dangerous interleaving: each worker, after reading the spend,
    # waits for the other to read it too. With the lock taken BEFORE the read,
    # the second worker can't get there; the wait times out and they run in turn.
    # Without it, both read $0 and both would pass the cap check.
    both_read = threading.Barrier(2, timeout=0.5)
    real_spent = Store.llm_spent

    def spent_then_wait(self, *args):
        spent = real_spent(self, *args)
        try:
            both_read.wait()
        except threading.BrokenBarrierError:
            pass
        return spent

    monkeypatch.setattr(Store, "llm_spent", spent_then_wait)

    def worker():
        store = Store(db)
        try:
            reserve(store, 0.6)
            results.append("ok")
        except CapReached as exc:
            results.append(exc.reason)
        finally:
            store.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["daily-cap", "ok"]  # exactly one call gets through
