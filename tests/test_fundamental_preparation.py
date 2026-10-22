import importlib.util
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime

import pytest

from dualtrade import calendar as cal
from dualtrade.collector import Collector
from dualtrade.storage import Store

path = Path(__file__).resolve().parents[1] / 'scripts/prepare-fundamentals.py'
spec = importlib.util.spec_from_file_location('fundamental_preparation', path)
prep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prep)


@pytest.fixture
def inputs(tmp_path):
    store = Store(tmp_path / 'state.sqlite3')
    cfg = {'symbols': {'AAPL': {'kind': 'stock', 'cik': '0000320193'}, 'SPY': {'kind': 'etf'}}}
    provider = SimpleNamespace(submissions=lambda cik: {'filings': {'recent': {'accessionNumber': ['filing-one']}}},
                               sec=lambda path: {'facts': {}})
    report = {'rows': [{}] * 8, 'investment_metrics': {'complete': False, 'missing': ['debt']}}
    yield store, cfg, provider, report, tmp_path
    store.db.close()


def test_prepared_result_is_consumed_by_existing_collector_cache_without_fetch(inputs):
    store, cfg, provider, report, tmp_path = inputs
    result = prep.prepare(cfg, store, provider, tmp_path / 'reports', clock=lambda: 1000,
                          analyse_fn=lambda *a: report)
    assert result['passed'] and len(result['items']) == 1
    collector = Collector({'bridge_directory': str(tmp_path / 'bridge')}, broker=object(), store=store)
    def forbidden():
        pytest.fail('A warmed cache must avoid repeating expensive analysis')
    cached = collector.job('fundamentals:AAPL:filing-one', 86400, forbidden, 1010)
    assert cached['data'] == report and not cached['error']
    assert result['items'][0]['missing'] == ['debt']  # Missing evidence is retained.


def test_newer_live_result_cannot_be_overwritten(inputs):
    store, cfg, provider, report, tmp_path = inputs
    latest = {'attempt': 1001, 'success': 1002, 'data': {'new': True}, 'error': ''}
    store.set('job:fundamentals:AAPL:filing-one', latest)
    result = prep.prepare(cfg, store, provider, tmp_path / 'reports', clock=lambda: 1000,
                          analyse_fn=lambda *a: report)
    assert store.get('job:fundamentals:AAPL:filing-one') == latest
    assert result['items'][0]['status'] == 'newer_cache_retained'


def test_refresh_failure_preserves_healthy_cache(inputs):
    store, cfg, provider, report, tmp_path = inputs
    old = {'attempt': 900, 'success': 900, 'data': report, 'error': ''}
    store.set('job:fundamentals:AAPL:filing-one', old)
    provider.sec = lambda _: (_ for _ in ()).throw(prep.ProviderError('SEC: network failure'))
    result = prep.prepare(cfg, store, provider, tmp_path / 'reports', clock=lambda: 1000)
    assert not result['passed']
    assert store.get('job:fundamentals:AAPL:filing-one') == old


@pytest.mark.parametrize('value,expected', [('2026-10-23T08:00:00', True),
                                           ('2026-10-23T09:25:00', False),
                                           ('2026-10-23T10:00:00', False),
                                           ('2026-10-24T08:00:00', False),
                                           ('2026-11-26T08:00:00', False)])
def test_scheduled_prep_skips_market_hours_weekends_and_holidays(value, expected):
    stamp = datetime.fromisoformat(value).replace(tzinfo=cal.NY).timestamp()
    assert prep.scheduled_window(stamp) is expected
