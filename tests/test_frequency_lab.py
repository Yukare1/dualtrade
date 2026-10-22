import importlib.util
from datetime import datetime, timezone
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("frequency_lab", Path(__file__).resolve().parents[1] / "research/frequency_lab.py")
lab = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lab)


def spec():
    return {"point": .01, "contract_size": 1,
            "costs": {"slippage_points": 1, "commission_round_turn_per_lot": .04,
                      "minimum_commission_round_turn": .04}}


def bars_for(day="2026-10-20"):
    session = lab.sessions_between(day, day)[0]
    opened, closed = session[1:]
    rows = {t: {"open": 103., "high": 104., "low": 102., "close": 103., "spread": 1.}
            for t in range(opened + 900, closed, 900)}
    for t in (opened + 900, opened + 1800):
        rows[t] = {"open": 100., "high": 101., "low": 99., "close": 100., "spread": 1.}
    rows[opened + 2700] = {"open": 100., "high": 102., "low": 100., "close": 102., "spread": 1.}
    return session, rows


def evaluate(rows, session, **kwargs):
    return lab.evaluate({"AAA": rows}, {"AAA": spec()}, [session], **kwargs)


def test_next_open_execution_and_cost_stress():
    session, rows = bars_for()
    base = evaluate(rows, session)
    trade = base["trades"][0]
    assert trade["entry_time"] == session[1] + 3600
    assert trade["entry"] == 103
    assert trade["signal_time"] == trade["entry_time"]
    assert trade["exit_time"] == session[2] - 900
    stress = evaluate(rows, session, multiplier=2)["trades"][0]
    assert stress["cost"] == 2 * trade["cost"]
    assert stress["net_r"] < trade["net_r"]


def test_stop_first_and_adverse_gap():
    session, rows = bars_for()
    stamp = session[1] + 3600
    rows[stamp].update(high=120, low=98)
    trade = evaluate(rows, session)["trades"][0]
    assert trade["exit_reason"] == "stop_first"
    assert trade["gross_r"] == -1
    session, rows = bars_for()
    rows[stamp + 900].update(open=97, low=96, high=100, close=99)
    trade = evaluate(rows, session)["trades"][0]
    assert trade["exit_reason"] == "stop_gap"
    assert trade["exit_price"] == 97
    assert trade["gross_r"] < -1


def test_scheduled_exit_precedes_bar_extremes():
    session, rows = bars_for()
    rows[session[2] - 900].update(open=104, high=120, low=90)
    trade = evaluate(rows, session)["trades"][0]
    assert trade["exit_reason"] == "session_exit"
    assert trade["exit_price"] == 104


def test_holidays_early_close_and_dst():
    assert lab.sessions_between("2026-12-25", "2026-12-25") == []
    session, rows = bars_for("2026-11-27")
    trade = evaluate(rows, session)["trades"][0]
    assert datetime.fromtimestamp(trade["exit_time"], lab.NY).strftime("%H:%M") == "12:45"
    for wall, ny in (("2026-03-16 16:45", "10:45"), ("2026-04-02 16:45", "09:45")):
        encoded = int(datetime.fromisoformat(wall).replace(tzinfo=timezone.utc).timestamp())
        actual = datetime.fromtimestamp(lab.broker_timestamp(encoded), lab.NY).strftime("%H:%M")
        assert actual == ny


def test_missing_range_or_spread_rejects_without_invention():
    session, rows = bars_for()
    rows.pop(session[1] + 1800)
    assert not evaluate(rows, session)["trades"]
    session, rows = bars_for()
    rows[session[1] + 2700]["spread"] = 0
    result = evaluate(rows, session)
    assert result["counts"]["candidates"] == 1
    assert not result["trades"]


def test_missing_position_bar_is_unresolved_and_blocks_day():
    session, rows = bars_for()
    rows.pop(session[1] + 4500)
    rows[session[1] + 5400]["close"] = 100
    rows[session[1] + 6300]["close"] = 103
    result = evaluate(rows, session)
    assert len(result["trades"]) == 1
    assert result["trades"][0]["exit_reason"] == "missing_bar_unresolved"
    assert result["trades"][0]["net_r"] is None


def test_future_data_cannot_change_prior_entries_or_completed_exits():
    session, rows = bars_for()
    entry = session[1] + 3600
    rows[entry].update(high=120, low=98)
    cutoff = entry + 900
    full = evaluate(rows, session)
    prefix = evaluate({t: r for t, r in rows.items() if t < cutoff}, session)
    before = lambda result: [t for t in result["trades"] if t["exit_time"] < cutoff]
    assert before(full) == before(prefix)
    altered = {t: dict(r) for t, r in rows.items()}
    for t, row in altered.items():
        if t >= cutoff:
            row.update(open=500, high=501, low=499, close=500)
    assert before(full) == before(evaluate(altered, session))


def test_global_single_position_and_deterministic_tie_break():
    session, rows = bars_for()
    result = lab.evaluate({"BBB": rows, "AAA": rows}, {"AAA": spec(), "BBB": spec()}, [session])
    assert result["counts"]["cost_eligible_candidates"] == 2
    assert len(result["trades"]) == 1
    assert result["trades"][0]["symbol"] == "AAA"


def test_late_breakout_cannot_enter_after_cutoff():
    session, rows = bars_for()
    for stamp in rows:
        if stamp >= session[1] + 2700:
            rows[stamp] = {"open": 100., "high": 101., "low": 99., "close": 100., "spread": 1.}
    rows[session[2] - 2700]["close"] = 102
    assert not evaluate(rows, session)["trades"]


def test_daily_cap_and_per_symbol_cooldown():
    session, rows = bars_for()
    for i, stamp in enumerate(range(session[1] + 2700, session[2], 900)):
        rows[stamp] = {"open": 103., "high": 104., "low": 98.,
                       "close": 102. if i % 2 == 0 else 100., "spread": 1.}
    result = lab.evaluate({"AAA": rows, "BBB": rows}, {"AAA": spec(), "BBB": spec()}, [session])
    assert len(result["trades"]) == 5
    assert result["counts"]["cost_eligible_candidates"] > 5
    for symbol in ("AAA", "BBB"):
        times = [t["entry_time"] for t in result["trades"] if t["symbol"] == symbol]
        assert all(b - a >= 3600 for a, b in zip(times, times[1:]))
