"""Real collector pool persistence with isolated stores and fake read-only inputs."""
from datetime import date
from types import SimpleNamespace

import pytest

from dualtrade import __version__, calendar as cal
from dualtrade.collector import Collector
from dualtrade.config import ROOT, load
from dualtrade.storage import Store, write_json


@pytest.fixture
def world(tmp_path, monkeypatch):
    cfg = load(ROOT / 'config.example.json')
    cfg['expected_login'] = 1
    cfg['bridge_directory'] = str(tmp_path / 'bridge')
    cfg['research']['issuer_calendar_file'] = str(tmp_path / 'issuer.json')
    cfg['research']['day_pool_size'] = 2
    cfg['symbols'] = {symbol: {'kind': 'etf'} for symbol in ('SPY', 'QQQ', 'DIA')}
    mappings = {symbol: {'symbol': symbol + '.TEST', 'kind': 'etf', 'verified': True,
                        'minimum': 1, 'costs': {'broker_utc_offset_seconds': 0,
                                               'sessions': {'4': ['09:35', '15:55']}}}
                for symbol in cfg['symbols']}
    session = cal.session(date(2026, 10, 23))
    start = session[0] + 900
    state = {'now': start - 5, 'source': 'one', 'ranked': ['SPY', 'QQQ', 'DIA'],
             'positions': [], 'rank_delay': 0, 'rank_calls': 0}
    def rank(_):
        state['rank_calls'] += 1
        state['now'] += state['rank_delay']
        return list(state['ranked'])
    broker = SimpleNamespace(rank=rank, inspect=lambda: {'account': {}, 'foreign_exposure': False,
                             'positions': list(state['positions']), 'mappings': mappings})
    providers = SimpleNamespace(macro=lambda now: [], alpha=lambda *a, **k: [], news=lambda *a: [])
    store = Store(tmp_path / 'state.sqlite3')
    collector = Collector(cfg, broker=broker, providers=providers, store=store)
    monkeypatch.setattr('dualtrade.collector.time.time', lambda: state['now'])
    monkeypatch.setattr('dualtrade.cli.source_hash', lambda: state['source'])
    monkeypatch.setattr('dualtrade.ai_news.read_state', lambda *a: {
        'healthy': True, 'events': [], 'sources': {key: {'ok': True, 'last_success': state['now'],
            'next_check': state['now'] + 60} for key in ('FED:press', 'FED:speeches')}})
    monkeypatch.setattr('dualtrade.council.snapshot', lambda *a, **k: {'healthy': True, 'decisions': {}})
    monkeypatch.setattr('dualtrade.official_news.OfficialNews', lambda *a, **k: SimpleNamespace(ingest_reporting=lambda *a: 0))
    monkeypatch.setattr('dualtrade.investment_runner.build', lambda c, snapshot, *a: snapshot.update(investment_ready=False))
    monkeypatch.setattr('dualtrade.swing.build', lambda c, snapshot, *a: snapshot.update(swing_ready=False))
    monkeypatch.setattr('dualtrade.shadow_runner.run_shadow', lambda *a: None)
    monkeypatch.setattr('dualtrade.investment_shadow.run', lambda *a: None)
    monkeypatch.setattr(collector, 'previous_levels', lambda *a: (100, 90))
    write_json(collector.directory / 'control.json', {'mode': 'observe'})
    def cycle(stamp):
        state['now'] = stamp
        write_json(collector.directory / 'ea-status.json', {'demo': True, 'version': __version__, 'time': stamp})
        return collector.cycle(stamp)
    yield SimpleNamespace(cfg=cfg, state=state, store=store, cycle=cycle, start=start,
                          collector=collector, key='pool:2026-10-23')
    store.db.close()


def test_selection_freezes_and_current_exclusion_remains_in_force(world):
    world.store.set('sec_close:SPY', world.start + 10)
    world.cycle(world.start - 5)
    initial = world.cycle(world.start)
    assert initial['pool'] == ['QQQ', 'DIA']
    world.state['ranked'] = ['SPY', 'DIA', 'QQQ']
    world.store.set('sec_close:QQQ', world.start + 3600)
    later = world.cycle(world.start + 120)
    assert later['pool'] == ['QQQ', 'DIA']
    assert next(c for c in later['contracts'] if c['ticker'] == 'QQQ')['entry_allowed'] is False
    assert world.state['rank_calls'] == 1


def test_changed_context_waits_for_real_new_snapshot(world):
    world.cycle(world.start - 5)
    world.state['source'] = 'two'
    first = world.cycle(world.start)
    assert first['pool'] == []
    assert world.store.get(world.key) is None
    second = world.cycle(world.start + 5)
    assert second['pool'] == ['SPY', 'QQQ']


def test_late_start_keeps_only_holdings_and_never_reranks(world):
    world.state['positions'] = [{'symbol': 'DIA.TEST'}]
    result = world.cycle(world.start + 61)
    assert result['pool'] == ['DIA']
    assert world.store.get(world.key) == ['DIA']
    assert world.state['rank_calls'] == 0
    world.cfg['research']['day_pool_size'] = 1
    world.state['positions'].append({'symbol': 'SPY.TEST'})
    result = world.cycle(world.start + 120)
    assert set(result['pool']) == {'DIA', 'SPY'}


def test_slow_rank_cannot_freeze_after_window(world):
    world.cycle(world.start - 5)
    world.state['rank_delay'] = 61
    result = world.cycle(world.start)
    assert result['pool'] == []
    assert world.store.get(world.key) == []
    world.state['rank_delay'] = 0
    assert world.cycle(world.start + 90)['pool'] == []


def test_snapshot_freshness_uses_post_rank_clock(world):
    world.cycle(world.start - 10)
    world.state['rank_delay'] = 55
    result = world.cycle(world.start)
    assert result['pool'] == []  # prior eligibility is now 65 seconds old
    assert world.store.get(world.key) is None  # a new cycle can still use fresh evidence before cutoff
    world.state['rank_delay'] = 0
    assert world.cycle(world.start + 56)['pool'] == ['SPY', 'QQQ']
