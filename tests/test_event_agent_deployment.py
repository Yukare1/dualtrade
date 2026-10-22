"""Deployment evidence must describe actual files, owners and current native context."""
import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


service = module('deployment_service', ROOT/'scripts/event-agent/service.py')
reload = module('deployment_reload', ROOT/'scripts/reload-observation.py')


@pytest.fixture
def packet(tmp_path, monkeypatch):
    monkeypatch.setattr(service, 'ROOT', tmp_path)
    monkeypatch.setattr(service, 'collector_family_matches', lambda pid: pid == 7)
    (tmp_path/'runtime').mkdir()
    (tmp_path/'runtime/collector.pid').write_text('7')
    binary = tmp_path/'DualTrade.ex5'
    binary.write_bytes(b'compiled reviewed expert')
    monkeypatch.setattr(service, 'deployed_binary_path', lambda *args: binary)
    receipt = dict(source_hash='source', context='context', deployed=True,
                   single_collector_writer=True, collector_pid=7,
                   binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest())
    ea = dict(time=1000, decision_context='context', demo=True, agent_review_required=True)
    snap = dict(time=1000, decision_context='context')
    return [{}, 'source', 'context', ea, snap, 1001, receipt, {}]


def test_canonical_reload_receipt_needs_no_obsolete_parallel_record(packet):
    assert service.deployment_issues(*packet) == []


def test_legacy_parallel_receipt_still_verifies_same_binary_and_writer(packet):
    del packet[6]['context']
    packet[7] = dict(source_hash='source', context='context', deployed=True,
                     single_collector_writer=True, collector_pid=7,
                     native_binary_sha256=packet[6]['binary_sha256'])
    assert service.deployment_issues(*packet) == []
    packet[7]['context'] = 'old'
    assert 'DEPLOYMENT_MISMATCH' in service.deployment_issues(*packet)


@pytest.mark.parametrize('key,value', [('source_hash','old'), ('context','old'), ('deployed',False),
                                     ('single_collector_writer',False), ('collector_pid',8)])
def test_incomplete_or_changed_receipt_fails_closed(packet, key, value):
    packet[6][key] = value
    assert 'DEPLOYMENT_MISMATCH' in service.deployment_issues(*packet)


@pytest.mark.parametrize('index,key,value', [(3,'time',980), (4,'time',900),
                                           (3,'decision_context','old'), (4,'decision_context','old'),
                                           (3,'demo',False), (3,'agent_review_required',False)])
def test_receipt_cannot_replace_fresh_native_identity_and_context(packet,index,key,value):
    packet[index][key] = value
    assert service.deployment_issues(*packet)


def test_actual_binary_change_fails_despite_matching_receipts(packet, tmp_path):
    (tmp_path/'DualTrade.ex5').write_bytes(b'different expert')
    assert 'DEPLOYED_BINARY_MISMATCH' in service.deployment_issues(*packet)


def test_unavailable_binary_discovery_fails_closed(packet, monkeypatch):
    monkeypatch.setattr(service, 'deployed_binary_path', lambda *args: (_ for _ in ()).throw(OSError()))
    assert 'DEPLOYED_BINARY_UNVERIFIED' in service.deployment_issues(*packet)


def test_duplicate_collector_blocks_current_receipt(packet, monkeypatch):
    monkeypatch.setattr(service, 'collector_family_matches', lambda pid: False)
    assert 'COLLECTOR_NOT_SINGLE_PROJECT_FAMILY' in service.deployment_issues(*packet)


def process(pid, parent, stopped=None):
    return SimpleNamespace(pid=pid, ppid=lambda: parent, info={'name':'pythonw.exe'},
                           cmdline=lambda:['pythonw.exe','-m','dualtrade.cli','collect'],
                           cwd=lambda:str(ROOT), terminate=lambda: stopped.append(pid) if stopped is not None else None)


def test_single_family_allows_launcher_child_but_not_independent_worker(monkeypatch):
    monkeypatch.setattr(service, 'ROOT', ROOT)
    workers=[process(7,1), process(8,7)]
    monkeypatch.setattr(service.psutil,'process_iter',lambda *args:workers)
    assert service.collector_family_matches(7)
    assert service.collector_family_matches(8)
    workers.append(process(9,1))
    assert not service.collector_family_matches(7)


def test_reload_stops_child_first_and_waits_for_whole_family(monkeypatch):
    stopped=[];workers=[process(7,1,stopped),process(8,7,stopped)]
    monkeypatch.setattr(reload.psutil,'wait_procs',lambda found,timeout:(found,[]))
    reload.stop_collector_family(workers)
    assert stopped == [8,7]
    stopped.clear()
    with pytest.raises(RuntimeError,match='Multiple independent'):
        reload.stop_collector_family([process(7,1,stopped),process(8,1,stopped)])
    assert stopped == []


def test_reload_requires_real_acceptance_and_explicit_active_cutover(monkeypatch):
    acceptance=dict(passed=True,source_hash='source')
    control=dict(mode='demo',until=1100)
    monkeypatch.setattr(reload,'source_hash',lambda:'source')
    monkeypatch.setattr(reload,'config_hash',lambda cfg:'config')
    monkeypatch.setattr(reload,'read_json',lambda path,default:acceptance if path.name=='acceptance.json' else control)
    with pytest.raises(RuntimeError,match='Active demo'):
        reload.deployment_precheck({},Path('bridge'),1000)
    assert reload.deployment_precheck({},Path('bridge'),1000,True)[0]=='source'
    control['until']=999
    assert reload.deployment_precheck({},Path('bridge'),1000)[0]=='source'
    acceptance['source_hash']='old'
    with pytest.raises(RuntimeError,match='Current-source'):
        reload.deployment_precheck({},Path('bridge'),1000,True)


def test_reload_receipt_wait_requires_fresh_disabled_native_and_single_writer(monkeypatch):
    ea=dict(time=1000,decision_context='context',demo=True,mode='observe',orders_enabled=False,agent_review_required=True)
    snap=dict(time=1000,decision_context='context')
    monkeypatch.setattr(reload,'read_json',lambda path,default:ea if path.name=='ea-status.json' else snap)
    monkeypatch.setattr(reload,'collector_processes',lambda:[process(7,1)])
    assert reload.wait_observation_context(Path('bridge'),'context',999,7,clock=lambda:1001,sleep=lambda _:None)[0] == ea
    ea['orders_enabled']=True
    with pytest.raises(RuntimeError,match='no deployment receipt'):
        reload.wait_observation_context(Path('bridge'),'context',999,7,clock=lambda:1001,sleep=lambda _:None)


def test_reload_accepts_slow_first_cycle_only_after_fresh_matching_state(monkeypatch):
    clock = [1000]
    def read(path, default):
        if path.name == 'ea-status.json':
            return dict(time=clock[0],decision_context='context',demo=True,mode='observe',
                        orders_enabled=False,agent_review_required=True)
        return dict(time=clock[0],decision_context='context' if clock[0]>=1050 else 'warming')
    monkeypatch.setattr(reload,'read_json',read)
    monkeypatch.setattr(reload,'collector_processes',lambda:[process(7,1)])
    result=reload.wait_observation_context(Path('bridge'),'context',999,7,
        clock=lambda:clock[0],sleep=lambda seconds:clock.__setitem__(0,clock[0]+seconds))
    assert clock[0] == 1050 and result[1]['decision_context']=='context'
