from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest
import requests

from dualtrade.x_watch import (
    Ledger, XWatch, WatchError, analyse, build_query, load_settings,
    research_window, POST_PRICE, USER_PRICE, PAGE_SIZE,
)
from dualtrade.storage import fingerprint

NOW = datetime(2026, 10, 22, 14, 0, tzinfo=timezone.utc).timestamp()


class FakeHTTP:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, deepcopy(kwargs)))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, int):
            return SimpleNamespace(status_code=value)
        return SimpleNamespace(status_code=200, json=lambda: value)


def post(number='100', author='1', text='Nvidia announces earnings guidance', at=NOW - 60):
    return {'id': number, 'author_id': author, 'text': text,
            'created_at': datetime.fromtimestamp(at, timezone.utc).isoformat()}


def page(*posts, token=None):
    meta = {'result_count': len(posts)}
    if token:
        meta['next_token'] = token
    return {'data': list(posts), 'meta': meta}


def watch(tmp_path, monkeypatch, *responses):
    monkeypatch.setenv('X_BEARER_TOKEN', 'test-private-token')
    cfg = load_settings()
    ledger = Ledger(tmp_path / 'x.db')
    cfg['enabled'] = True  # Production X monitoring is intentionally disabled.
    names = cfg['news_accounts'] + cfg['macro_accounts']
    ledger.set('accounts', {'signature': fingerprint(names), 'resolved_at': NOW,
                           'ids': {str(i + 1): name for i, name in enumerate(names)}})
    http = FakeHTTP(*responses)
    return XWatch(cfg, ledger, http, tmp_path / 'reports'), http


def test_authorized_limits_cannot_be_raised_by_config(tmp_path):
    cfg = load_settings()
    for key, value in [('monthly_limit_usd', 15.01), ('daily_limit_usd', .51),
                       ('monthly_limit_usd', float('nan')), ('poll_seconds', 1)]:
        invalid = deepcopy(cfg)
        invalid[key] = value
        path = tmp_path / 'config.json'
        path.write_text(json.dumps(invalid))
        with pytest.raises(ValueError):
            load_settings(path)


def test_budgets_survive_restart_and_concurrent_connections(tmp_path):
    cfg = load_settings()
    path = tmp_path / 'x.db'
    one, two = Ledger(path), Ledger(path)
    for _ in range(10):
        one.reserve(cfg, NOW, PAGE_SIZE, POST_PRICE)
    with pytest.raises(WatchError, match='DAILY_BUDGET_LIMIT'):
        two.reserve(cfg, NOW, PAGE_SIZE, POST_PRICE)
    assert two.budget(cfg, NOW)['daily_accounted_usd'] == .5
    with pytest.raises(WatchError, match='CLOCK_MOVED_BACKWARDS'):
        two.reserve(cfg, NOW - 1, 1, POST_PRICE)


def test_rolling_limit_survives_calendar_month_reset(tmp_path):
    cfg = load_settings()
    ledger = Ledger(tmp_path / 'x.db')
    # Both UTC monthly and a rolling 31-day ceiling apply across billing dates.
    start = datetime(2026, 10, 1, 12, tzinfo=timezone.utc).timestamp()
    for day in range(30):
        ledger.reserve(cfg, start + day * 86400, 100, POST_PRICE)
    with pytest.raises(WatchError, match='MONTHLY_BUDGET_LIMIT'):
        ledger.reserve(cfg, start + 31 * 86400, 10, POST_PRICE)
    assert ledger.budget(cfg, start + 31 * 86400)['rolling_31_days_accounted_usd'] == 15


def test_settlement_releases_only_known_unused_capacity(tmp_path):
    cfg = load_settings()
    ledger = Ledger(tmp_path / 'x.db')
    request = ledger.reserve(cfg, NOW, 10, POST_PRICE)
    ledger.settle(request, 2)
    assert ledger.budget(cfg, NOW)['daily_accounted_usd'] == .01
    for count in [0, -1, 11, True]:
        with pytest.raises(WatchError):
            ledger.settle(request, count)


@pytest.mark.parametrize('failure', [requests.Timeout('secret-in-url'), 401, 402, 403, 429, 500,
                                    {'errors': [{'detail': 'private-token'}]},
                                    {'data': [post()], 'meta': {'result_count': 0}}])
def test_failed_requests_stay_reserved_and_do_not_leak_secrets(tmp_path, monkeypatch, failure):
    monitor, http = watch(tmp_path, monkeypatch, failure)
    result = monitor.check(NOW)
    assert result['status'] != 'OK'
    assert result['budget']['daily_accounted_usd'] == .05
    assert not result['last_success']
    assert monitor.ledger.get('checkpoint') is None
    serialized = json.dumps(result)
    assert 'test-private-token' not in serialized
    assert 'secret-in-url' not in serialized
    assert 'private-token' not in serialized
    monitor.check(NOW + 10)
    assert len(http.calls) == 1


def test_unknown_author_cannot_be_admitted(tmp_path, monkeypatch):
    monitor, _ = watch(tmp_path, monkeypatch, page(post(author='999')))
    result = monitor.check(NOW)
    assert result['status'] == 'INVALID_X_POST'
    assert result['items'] == []
    assert result['budget']['daily_accounted_usd'] == .05


def test_pagination_checkpoint_survives_restart_without_skipping_older_posts(tmp_path, monkeypatch):
    monitor, http = watch(tmp_path, monkeypatch,
                          page(post('200'), token='next-page'), page(post('100')), page())
    result = monitor.check(NOW)
    assert result['pagination_pending']
    checkpoint = monitor.ledger.get('checkpoint')
    assert checkpoint.get('since_id') is None
    first_params = http.calls[0][1]['params']
    assert 'since_id' not in first_params
    assert first_params['max_results'] == 10
    assert 'expansions' not in first_params
    assert http.calls[0][1]['allow_redirects'] is False
    # A new process resumes the exact paid query/window and next_token.
    monitor.ledger = Ledger(tmp_path / 'x.db')
    result = monitor.check(NOW + 120)
    second_params = http.calls[1][1]['params']
    assert second_params['next_token'] == 'next-page'
    assert second_params['end_time'] == first_params['end_time']
    assert {p['id'] for p in result['items']} == {'100', '200'}
    assert not result['pagination_pending']
    assert monitor.ledger.get('checkpoint')['since_id'] == '200'
    assert result['budget']['daily_accounted_usd'] == .01
    monitor.check(NOW + 240)
    third_params = http.calls[2][1]['params']
    assert third_params['start_time'] == first_params['end_time']
    assert 'since_id' not in third_params


def test_empty_pages_are_not_billed_as_ten_posts(tmp_path, monkeypatch):
    monitor, _ = watch(tmp_path, monkeypatch, {'meta': {'result_count': 0}})
    result = monitor.check(NOW)
    assert result['status'] == 'OK'
    assert result['budget']['daily_accounted_usd'] == 0


def test_account_lookup_is_budgeted_once_and_cached(tmp_path, monkeypatch):
    monitor, http = watch(tmp_path, monkeypatch)
    monitor.ledger.set('accounts', {})
    names = monitor.cfg['news_accounts'] + monitor.cfg['macro_accounts']
    http.responses = [{'data': [{'id': str(i + 1), 'username': name} for i, name in enumerate(names)]},
                      page(), page()]
    result = monitor.check(NOW)
    assert http.calls[0][0].endswith('/users/by')
    assert result['budget']['daily_accounted_usd'] == len(names) * USER_PRICE / 1_000_000
    monitor.check(NOW + 120)
    assert len(http.calls) == 3


def test_no_requests_without_key_outside_hours_or_after_stale_price_review(tmp_path, monkeypatch):
    monitor, http = watch(tmp_path, monkeypatch)
    monkeypatch.delenv('X_BEARER_TOKEN')
    assert monitor.check(NOW)['status'] == 'KEY_REQUIRED'
    monkeypatch.setenv('X_BEARER_TOKEN', 'test-private-token')
    saturday = datetime(2026, 10, 24, 14, tzinfo=timezone.utc).timestamp()
    assert monitor.check(saturday)['status'] == 'OUTSIDE_RESEARCH_HOURS'
    monitor.cfg['price_reviewed_on'] = '2026-08-01'
    assert monitor.check(NOW)['status'] == 'API_PRICE_REVIEW_REQUIRED'
    assert http.calls == []


def test_exchange_window_observes_dst_holidays_and_early_closes():
    def at(value):
        return datetime.fromisoformat(value).timestamp()
    assert research_window(at('2026-10-22T12:00:00+00:00')) is not None
    assert research_window(at('2026-11-02T12:30:00+00:00')) is None
    assert research_window(at('2026-11-02T13:00:00+00:00')) is not None
    assert research_window(at('2026-11-26T15:00:00+00:00')) is None
    assert research_window(at('2026-11-27T17:59:00+00:00')) is not None
    assert research_window(at('2026-11-27T18:00:00+00:00')) is None


def test_research_is_unverified_and_never_a_trade_signal():
    cfg = load_settings()
    row = analyse(post(text='Nvidia bankruptcy rumor https://example.org'), 'ReutersBiz', cfg, NOW)
    assert row['tickers'] == ['NVDA']
    assert row['priority'] == 'high'
    assert row['verification'] == 'UNVERIFIED_X_CLAIM'
    assert row['eligible_for_trading'] is False
    assert 'henüz doğrulanmadı' in row['summary_tr']
    assert len(build_query(cfg)) <= 512
    assert '-is:retweet' in build_query(cfg)


def test_duplicate_posts_do_not_repeat_in_text_report_and_old_rows_are_removed(tmp_path, monkeypatch):
    monitor, http = watch(tmp_path, monkeypatch, page(post('200'), post('100', author='2')))
    result = monitor.check(NOW)
    assert len(result['items']) == 2
    assert all(len(row['same_text_sources']) == 2 for row in result['items'])
    text = (tmp_path / 'reports/x-watch.txt').read_text(encoding='utf-8')
    assert text.count('Nvidia announces earnings guidance') == 1
    monitor.ledger.prune(NOW + 73 * 3600)
    assert monitor.ledger.rows(NOW + 73 * 3600) == []


def test_budget_blocked_pending_page_is_not_discarded(tmp_path, monkeypatch):
    monitor, http = watch(tmp_path, monkeypatch, page(post('200'), token='more'))
    monitor.check(NOW)
    # Leave less than the required maximum $0.05 reservation.
    monitor.ledger.reserve(monitor.cfg, NOW, 90, POST_PRICE)
    monitor.ledger.reserve(monitor.cfg, NOW, 4, POST_PRICE)
    result = monitor.check(NOW + 120)
    assert result['status'] == 'DAILY_BUDGET_LIMIT'
    assert result['pagination_pending']
    assert len(http.calls) == 1
    assert result['budget']['daily_accounted_usd'] <= .5
