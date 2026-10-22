from copy import deepcopy
import pytest
from dualtrade import cli
from dualtrade.config import load, config_hash
from dualtrade.demo_exception import active, account_pin, AUTHORIZATION, START, END
from dualtrade.storage import fingerprint, Store, write_json
from dualtrade import __version__


def authorized(cfg, now):
    return {'schema': 1, 'approved': True, 'authorization': AUTHORIZATION, 'scope': 'day-demo',
            'issued_at': now-1, 'expires': END, 'account_pin': account_pin(cfg),
            'context': fingerprint({'config_hash': config_hash(cfg), 'source_hash': cli.source_hash()})}


@pytest.mark.parametrize('change', ['missing', 'late', 'future', 'longer', 'shorter', 'swing',
                                    'all', 'context', 'account', 'approval', 'scope', 'id', 'issued'])
def test_waiver_is_exactly_one_authorized_demo_context_and_deadline(change):
    cfg = load(); now = START+3600; r = authorized(cfg, now); style = 'day'; until = END
    assert active(cfg, now, style, until, r)
    if change == 'missing': r = {}
    elif change == 'late': now = END
    elif change == 'future': now = END+86400
    elif change == 'longer': until += 1
    elif change == 'shorter': until -= 1
    elif change in ('swing', 'all'): style = change
    elif change == 'context': r['context'] = 'stale'
    elif change == 'account': r['account_pin'] = 'other'
    elif change == 'approval': r['approved'] = False
    elif change == 'scope': r['scope'] = 'all'
    elif change == 'id': r['authorization'] = 'different'
    elif change == 'issued': r['issued_at'] = now+1
    assert active(cfg, now, style, until, r) is None


def test_waiver_skips_only_observation_and_never_creates_a_pass(monkeypatch, tmp_path):
    cfg = load(); cfg['bridge_directory'] = str(tmp_path/'bridge'); now = START+3600
    monkeypatch.setattr(cli, 'ROOT', tmp_path)
    monkeypatch.setattr(cli, 'source_hash', lambda: 'a'*64)
    record = authorized(cfg, now)
    monkeypatch.setattr('dualtrade.demo_exception.read_json', lambda *a: record)
    store = Store(tmp_path/'state.db')
    from dualtrade.decision_revision import observation_context
    snap = {'time': now, 'ready': True, 'decision_context': record['context'], 'observation_context': observation_context(cfg)}
    ea = {'time': now, 'demo': True, 'agent_review_required': True,
          'orders_enabled': False, 'version': __version__, 'decision_context': record['context']}
    write_json(tmp_path/'build/acceptance.json', {'passed': True, 'source_hash': 'a'*64})
    def check():
        write_json(tmp_path/'bridge/snapshot.json', snap)
        write_json(tmp_path/'bridge/ea-status.json', ea)
        return cli.start_demo(cfg, store, now, 'day', check_only=True,
                              check_order_permission=False, until=END, observation_waiver=True)
    assert check()['eligible']
    normal = cli.start_demo(cfg, store, now, 'day', check_only=True,
                            check_order_permission=False, until=END)
    assert 'day: Full healthy observation session required' in normal['issues']
    assert store.get('observation_pass', {}) == {}
    snap['ready'] = False
    assert 'day: Research blocked' in check()['issues']
    snap['ready'] = True; ea['halt'] = True
    assert 'EA risk/reconciliation gate active' in check()['issues']
    ea['halt'] = False; snap['time'] = now-61
    assert 'Research snapshot unavailable or stale' in check()['issues']
    snap['time'] = now
    write_json(tmp_path/'build/acceptance.json', {'passed': False, 'source_hash': 'a'*64})
    assert 'Current-source functional acceptance required' in check()['issues']
    assert store.get('observation_pass', {}) == {}
    assert not (tmp_path/'bridge/control.txt').exists()
