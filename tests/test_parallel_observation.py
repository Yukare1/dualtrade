"""Exercise real collector cycles; synthetic health stays in an isolated store."""
from datetime import date
from types import SimpleNamespace

import pytest

from dualtrade import __version__, calendar as cal
from dualtrade.collector import Collector
from dualtrade.config import ROOT, load
from dualtrade.storage import Store, write_json


@pytest.fixture
def cycles(tmp_path, monkeypatch):
    cfg = load(ROOT / 'config.example.json')
    cfg['expected_login'] = 1
    cfg['bridge_directory'] = str(tmp_path / 'bridge')
    cfg['research']['issuer_calendar_file'] = str(tmp_path / 'issuer.json')
    store = Store(tmp_path / 'state.sqlite3')
    session = cal.session(date(2026, 10, 23))
    start, finish = session[0] + 900, session[1] - 300
    clock = {'now': start, 'source': 'source-one', 'decision': 'decision-one', 'ea_ok': True,
             'day': True, 'swing': True, 'investment': True}
    directory = tmp_path / 'bridge'
    control = {'mode': 'demo', 'styles': ['day'], 'until': finish}
    write_json(directory / 'control.json', control)
    (directory / 'control.txt').write_text('demo\nstyles|day\n', encoding='utf-8')
    mapping = {'symbol': 'SPY.NYSE', 'kind': 'etf', 'verified': True,
               'minimum': 1, 'costs': {'broker_utc_offset_seconds': 0,
                                     'sessions': {'4': ['09:35', '15:55']}}}
    # A held position owned by this bot is allowed while observing; it is not
    # foreign exposure and does not create a flat-account observation rule.
    broker = SimpleNamespace(inspect=lambda: {
        'account': {}, 'foreign_exposure': False,
        'positions': [{'symbol': 'SPY.NYSE'}], 'mappings': {'SPY': mapping}})
    providers = SimpleNamespace(macro=lambda now: [], alpha=lambda *a, **k: [],
                                news=lambda symbols: [])
    collector = Collector(cfg, broker=broker, providers=providers, store=store)
    monkeypatch.setattr('dualtrade.collector.time.time', lambda: clock['now'])
    monkeypatch.setattr('dualtrade.cli.source_hash', lambda: clock['source'])
    monkeypatch.setattr('dualtrade.decision_revision.observation_context', lambda cfg: clock['decision'])
    monkeypatch.setattr('dualtrade.ai_news.read_state', lambda *a: {})
    monkeypatch.setattr('dualtrade.ai_news.apply',
                        lambda snapshot, *a: snapshot.update(ready=clock['day']))
    monkeypatch.setattr('dualtrade.context_agent.evaluate', lambda *a: {
        'ready': True, 'shared_ready': True, 'issues': [], 'symbol_issues': {}})
    monkeypatch.setattr('dualtrade.council.snapshot',
                        lambda *a, **k: {'healthy': True, 'decisions': {}})
    monkeypatch.setattr('dualtrade.official_news.OfficialNews',
                        lambda *a, **k: SimpleNamespace(ingest_reporting=lambda *a: 0))
    monkeypatch.setattr('dualtrade.investment_runner.build',
                        lambda c, snapshot, *a: snapshot.update(investment_ready=clock['investment']))
    monkeypatch.setattr('dualtrade.swing.build',
                        lambda c, snapshot, *a: snapshot.update(swing_ready=clock['swing']))
    monkeypatch.setattr('dualtrade.shadow_runner.run_shadow', lambda *a: None)
    monkeypatch.setattr('dualtrade.investment_shadow.run', lambda *a: None)
    monkeypatch.setattr(collector, 'previous_levels', lambda *a: (100, 90))
    store.set('pool:2026-10-23', ['SPY'])
    risk_before = (cfg['risk'].copy(), cfg['shared_risk'].copy())
    controls_before = {name: (directory / name).read_bytes()
                       for name in ('control.json', 'control.txt')}

    def cycle(stamp, *, mode=None):
        clock['now'] = stamp
        if mode is not None:
            write_json(directory / 'control.json', {**control, 'mode': mode})
        write_json(directory / 'ea-status.json', {
            'demo': True, 'version': __version__, 'time': stamp,
            'orders_enabled': True, 'intent_pending': not clock['ea_ok'],
            'day_positions': 1})
        result = collector.cycle(stamp)
        assert result['time'] == stamp
        return result

    yield SimpleNamespace(cycle=cycle, collector=collector, store=store, clock=clock,
                          start=start, finish=finish, directory=directory,
                          controls_before=controls_before, risk_before=risk_before,
                          cfg=cfg)
    store.db.close()


def run_window(cycles, mutate=None):
    result = None
    for stamp in range(cycles.start, cycles.finish + 1, 30):
        if mutate:
            mutate(stamp)
        result = cycles.cycle(stamp)
    return result


def test_demo_cycles_observe_full_real_window_without_changing_controls_or_risk(cycles):
    first = cycles.cycle(cycles.start)
    assert not first['observation_ready']  # Permission and an open bot position certify nothing.
    final = run_window(cycles)
    assert final['mode'] == 'demo' and final['observation_ready']
    for style in ('day', 'swing', 'investment'):
        prefix = '' if style == 'day' else style + ':'
        passed = cycles.store.get(prefix + 'observation_pass')
        progress = cycles.store.get(prefix + 'observation:2026-10-23')
        assert passed['config_hash'] == final['observation_context']
        assert passed['completed_at'] == cycles.finish
        assert progress['started'] == cycles.start and not progress['failed']
        assert progress['seconds'] == cycles.finish - cycles.start
    assert {name: (cycles.directory / name).read_bytes()
            for name in cycles.controls_before} == cycles.controls_before
    assert (cycles.cfg['risk'], cycles.cfg['shared_risk']) == cycles.risk_before
    assert cycles.collector.store is cycles.store


def test_stop_cycles_do_not_advance_observation(cycles):
    cycles.cycle(cycles.start, mode='observe')
    before = {style: cycles.store.get(prefix + 'observation:2026-10-23')
              for style, prefix in (('day', ''), ('swing', 'swing:'), ('investment', 'investment:'))}
    final = None
    for stamp in (cycles.start + 30, cycles.finish):
        final = cycles.cycle(stamp, mode='stop')
    assert final['mode'] == 'stop' and not final['observation_ready']
    for style, prefix in (('day', ''), ('swing', 'swing:'), ('investment', 'investment:')):
        assert cycles.store.get(prefix + 'observation:2026-10-23') == before[style]
        assert cycles.store.get(prefix + 'observation_pass') is None


def test_observe_to_demo_transition_keeps_single_observation_duration(cycles):
    cycles.cycle(cycles.start, mode='observe')
    cycles.cycle(cycles.start + 30, mode='demo')
    for stamp in range(cycles.start + 60, cycles.finish + 1, 30):
        final = cycles.cycle(stamp)
    progress = cycles.store.get('observation:2026-10-23')
    assert final['observation_ready'] and final['mode'] == 'demo'
    assert progress['started'] == cycles.start
    assert progress['seconds'] == cycles.finish - cycles.start


@pytest.mark.parametrize('failure', ['previous_failure', 'decision_change', 'gap', 'ea_unhealthy'])
def test_demo_cycles_never_certify_invalid_evidence(cycles, failure):
    cycles.cycle(cycles.start)
    if failure == 'previous_failure':
        key = 'observation:2026-10-23'
        # Simulate a persisted earlier invalid sample in this isolated store.
        cycles.store.set(key, {**cycles.store.get(key), 'failed': True})
    for stamp in range(cycles.start + 30, cycles.finish + 1, 30):
        if stamp == cycles.start + 60:
            if failure == 'decision_change':
                cycles.clock['source'] = 'source-two'
                cycles.clock['decision'] = 'decision-two'
            elif failure == 'gap':
                continue
            elif failure == 'ea_unhealthy':
                cycles.clock['ea_ok'] = False
        else:
            cycles.clock['ea_ok'] = True
        final = cycles.cycle(stamp)
    assert not final['observation_ready']
    assert cycles.store.get('observation_pass') is None
    assert cycles.store.get('observation:2026-10-23')['failed']
    assert cycles.collector.store is cycles.store


def test_infrastructure_revision_keeps_observation_but_changes_execution_context(cycles):
    first = cycles.cycle(cycles.start)
    cycles.clock['source'] = 'source-two'
    final = run_window(cycles)
    assert final['observation_ready']
    assert final['observation_context'] == first['observation_context']
    assert final['decision_context'] != first['decision_context']
    certificate = cycles.store.get('observation_pass')
    later = cycles.cycle(cycles.finish + 60)
    assert later['observation_ready']
    assert cycles.store.get('observation_pass')['config_hash'] == certificate['config_hash']


@pytest.mark.parametrize('failed_style', ['day', 'swing', 'investment'])
def test_parallel_demo_observation_sections_pass_independently(cycles, failed_style):
    def one_bad_sample(stamp):
        cycles.clock[failed_style] = stamp != cycles.start + 60
    final = run_window(cycles, one_bad_sample)
    for style in ('day', 'swing', 'investment'):
        prefix = '' if style == 'day' else style + ':'
        ready_key = 'observation_ready' if style == 'day' else style + '_observation_ready'
        assert final[ready_key] is (style != failed_style)
        assert bool(cycles.store.get(prefix + 'observation_pass')) is (style != failed_style)
        progress = cycles.store.get(prefix + 'observation:2026-10-23')
        assert progress['failed'] is (style == failed_style)


def test_supplemental_news_uses_one_shared_request_each_ten_minutes(cycles):
    calls = []
    cycles.collector.providers.news = lambda symbols: calls.append(tuple(symbols)) or []
    cycles.cycle(cycles.start)
    cycles.cycle(cycles.start + 300)
    assert len(calls) == 1
    cycles.cycle(cycles.start + 600)
    assert len(calls) == 2
