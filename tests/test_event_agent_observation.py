"""Observation reuse is semantic; deployment and executable evidence remain exact."""
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

PATH = Path(__file__).resolve().parents[1]/'scripts/event-agent/service.py'
spec = importlib.util.spec_from_file_location('semantic_observation_service', PATH)
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(service, 'ROOT', tmp_path)
    monkeypatch.setattr(service, 'STATE', tmp_path/'runtime/event-agent')
    monkeypatch.setattr(service, 'bridge', lambda cfg: tmp_path/'bridge')
    revision = {'source':'source-one', 'observation':'semantic-one'}
    monkeypatch.setattr(service, 'source_hash', lambda: revision['source'])
    monkeypatch.setattr(service, 'config_hash', lambda cfg: 'config')
    monkeypatch.setattr(service, 'observation_context', lambda cfg: revision['observation'])
    monkeypatch.setattr(service, 'service_fingerprint', lambda: 'service-fingerprint')
    monkeypatch.setattr(service, 'process_alive', lambda pid: pid == 7)
    monkeypatch.setattr(service, 'pid_from_file', lambda path: 7)
    deployments = []
    monkeypatch.setattr(service, 'deployment_issues', lambda cfg,source,context,*args: deployments.append((source,context)) or [])
    (tmp_path/'runtime').mkdir()
    db = sqlite3.connect(tmp_path/'runtime/state.sqlite3')
    db.execute('CREATE TABLE kv(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    db.execute('INSERT INTO kv VALUES(?,?)', ('observation_pass',json.dumps({'config_hash':'semantic-one','date':'1970-01-01'})))
    db.commit(); db.close()

    def certify_full_source():
        context = service.fingerprint({'config_hash':'config','source_hash':revision['source']})
        service.write_json(tmp_path/'build/acceptance.json', {'passed':True,'source_hash':revision['source']})
        service.write_json(service.STATE/'acceptance.json', {'passed':True,'source_hash':revision['source'],
            'context':context,'service_fingerprint':'service-fingerprint'})
        service.write_json(tmp_path/'bridge/ea-status.json', {'time':1000,'decision_context':context,
            'demo':True,'agent_review_required':True,'mode':'observe','orders_enabled':False})
        service.write_json(tmp_path/'bridge/snapshot.json', {'time':1000,'decision_context':context,
                           'observation_context':revision['observation']})
        return context

    certify_full_source()
    return revision, certify_full_source, deployments


def test_infrastructure_revision_preserves_semantic_pass_after_exact_revalidation(state):
    revision, certify, deployments = state
    first = service.health({},1001)[0]
    assert first['observations']['day']['passed']
    assert first['observation_context'] == 'semantic-one'
    revision['source'] = 'source-two'
    full_context = certify()
    second = service.health({},1001)[0]
    assert second['issues'] == []
    assert second['observations']['day']['passed']
    assert second['context'] == full_context != first['context']
    assert deployments[-1] == ('source-two',full_context)


def test_semantic_pass_never_substitutes_for_exact_source_acceptance(state):
    revision, _, _ = state
    revision['source'] = 'unaccepted-source'
    report = service.health({},1001)[0]
    assert report['observations']['day']['passed']
    assert 'ACCEPTANCE_MISMATCH' in report['issues']
    assert 'EVENT_AGENT_ACCEPTANCE_MISMATCH' in report['issues']
    assert 'CONTEXT_MISMATCH' in report['issues']


def test_changed_decision_context_requires_new_observation(state):
    revision, _, _ = state
    revision['observation'] = 'semantic-two'
    report = service.health({},1001)[0]
    assert report['observation_context'] == 'semantic-two'
    assert not report['observations']['day']['passed']
    assert 'OBSERVATION_CONTEXT_MISMATCH' in report['issues']


def test_legacy_full_source_certificate_is_not_implicitly_mapped(state):
    _, certify, _ = state
    full_context = certify()
    db = sqlite3.connect(service.ROOT/'runtime/state.sqlite3')
    db.execute('UPDATE kv SET value=? WHERE key=?',
               (json.dumps({'config_hash':full_context}), 'observation_pass'))
    db.commit(); db.close()
    report = service.health({},1001)[0]
    assert report['issues'] == []
    assert not report['observations']['day']['passed']
