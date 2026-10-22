from datetime import datetime, timezone, date
import pytest
from dualtrade.policy import DailyLedger, position_budget, exit_reason, floor_volume, sentiment_veto
from dualtrade.calendar import session, earnings_window, macro_gate, now_ny


@pytest.mark.parametrize("days", [1, 3, 7, 8, 14, 30, 365])
def test_swing_has_no_age_or_weekend_exit(days):
    assert exit_reason("swing", days*86400, 40000, macro_flatten=True) is None


def test_day_flattens_swing_holds_macro():
    assert exit_reason("day", 100, 10000, macro_flatten=True) == "MACRO"
    assert exit_reason("swing", 100, 10000, macro_flatten=True) is None
    assert exit_reason("day", 9700, 10000) == "DAY_CLOSE"


@pytest.mark.parametrize("style", ["day", "swing"])
def test_loss_and_earnings_override_holding(style):
    assert exit_reason(style, 400, 10000, daily_halt=True) == "DAILY_LOSS"
    assert exit_reason(style, 400, 10000, earnings_close=300, earnings_end=1000) == "EARNINGS"


def test_daily_loss_checked_before_midnight_reset():
    ledger = DailyLedger("2026-10-22", 1000, 990)
    ledger.update("2026-10-23", 790, flat=False)
    assert ledger.halted and ledger.unresolved and ledger.baseline == 1000


def test_disconnect_cannot_forgive_gap_loss():
    ledger = DailyLedger("2026-10-23", 1000, 1000)
    ledger.update("2026-10-26", 850, continuous=False)
    assert not ledger.reconciled and ledger.baseline == 1000
    ledger.update("2026-10-26", 790, continuous=False)
    assert ledger.halted


def test_cashflow_is_not_profit_or_loss():
    ledger = DailyLedger("today", 1000, 1000)
    ledger.update("today", 1500, cashflow=500)
    assert ledger.baseline == 1500 and not ledger.halted
    ledger.update("today", 1100, cashflow=-400)
    assert ledger.baseline == 1100 and not ledger.halted


def test_sizing_reduces_for_remaining_day_and_never_rounds_up():
    assert position_budget(825, 1000, 10, 40) == 15
    assert position_budget(1000, 1000, 0, 0) == 40
    assert position_budget(1000, 1000, 0, 80) == 0
    assert floor_volume(.009, .01, 100, .01) == 0
    assert floor_volume(.0299, .01, 100, .01) == .02
    assert floor_volume(float("nan"), .01, 100, .01) == 0


def test_calendar_early_close_and_dst():
    op, cl = session(date(2026, 11, 27))
    assert now_ny(cl).hour == 13
    assert session(date(2026, 12, 25)) is None
    assert datetime.fromtimestamp(session(date(2026, 10, 30))[0], timezone.utc).hour == 13
    assert datetime.fromtimestamp(session(date(2026, 11, 2))[0], timezone.utc).hour == 14


def test_earnings_closes_prior_session_and_excludes_next():
    event = earnings_window(date(2026, 10, 26))
    assert now_ny(event["close_by"]).date() == date(2026, 10, 23)
    assert now_ny(event["blocked_until"]).date() == date(2026, 10, 27)


def evidence(n, score=-.8):
    return [{"time": 100, "score": score, "reliable": True, "cluster": str(i), "author": str(i), "publisher": str(i%2)} for i in range(n)]


def test_sentiment_thresholds_and_duplicate_authors():
    # Legacy flags cannot bypass the news credibility boundary.
    assert not sentiment_veto(evidence(3), 1, "news", 200)
    assert not sentiment_veto(evidence(3), -1, "news", 200)
    assert not sentiment_veto(evidence(4), 1, "social", 200)
    assert sentiment_veto(evidence(5), 1, "social", 200)
    repeated = evidence(5)
    for e in repeated: e["author"] = "one"
    assert not sentiment_veto(repeated, 1, "social", 200)
    assert not sentiment_veto(evidence(10), 1, "social", 30000)


def test_macro_boundaries():
    events = [{"time": 10000}]
    assert macro_gate(events, 8200, "day") == (True, False)
    assert macro_gate(events, 9700, "day") == (True, True)
    assert macro_gate(events, 9700, "swing") == (True, False)


def test_earnings_closure_not_forgiven_by_disconnection():
    assert exit_reason('swing',2000,10000,earnings_close=300,earnings_end=1000)=='EARNINGS'


def test_opposing_sentiment_mean_uses_opposing_evidence():
    items=evidence(10,-.6)
    for e in items[7:]:e['score']=.8
    assert sentiment_veto(items,1,'social',200)


def test_syndicated_headlines_not_independent_evidence():
    from dualtrade.policy import deduplicate_news
    rows=[{'ticker':'AAPL','time':i,'cluster':str(i),'url':'https://news.test/'+str(i),'title':title} for i,title in enumerate(['Apple shares fall after disappointing quarterly earnings','Apple shares fall after disappointing quarterly earnings today','Apple unveils new chip with greater efficiency'])]
    assert len(deduplicate_news(rows))==2
