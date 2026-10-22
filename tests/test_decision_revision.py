"""Observation equivalence is separate from immutable full-source approval."""
from copy import deepcopy
from pathlib import Path
import shutil

import pytest

from dualtrade.config import ROOT, load
from dualtrade.decision_revision import (decision_manifest, mql_semantics,
                                        observation_context, observation_revision,
                                        python_semantics)


@pytest.fixture
def revision_tree(tmp_path, monkeypatch):
    for folder in ('dualtrade', 'MQL5', 'scripts/event-agent'):
        shutil.copytree(ROOT / folder, tmp_path / folder,
                        ignore=shutil.ignore_patterns('__pycache__', '*.ex5'))
    for name in ('official-news.json', 'council.example.json', 'requirements.lock.txt', 'pyproject.toml'):
        shutil.copyfile(ROOT / name, tmp_path / name)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'home'))
    return tmp_path, load(ROOT / 'config.example.json')


def test_python_comments_formatting_and_true_docstrings_do_not_change_semantics():
    before = '"""Module docs."""\ndef gate(x):\n    """Gate docs."""\n    return x >= 65\n'
    after = '# revised heading\n"""Revised docs."""\ndef gate( x ):\n    """New docs."""\n    return (x >= 65) # comment\n'
    assert python_semantics(before) == python_semantics(after)
    assert python_semantics(before) != python_semantics(after.replace('65', '64'))
    assert python_semantics('x = "Approve"') != python_semantics('x = "Reject"')


def test_mql_ordinary_whitespace_and_comments_are_ignored_but_literals_and_operators_remain():
    assert mql_semantics('int x = 1; // note\n x += 2;') == mql_semantics('int/*doc*/x=1;\nx+=2;')
    assert mql_semantics('x++ + y;') != mql_semantics('x + +y;')
    assert mql_semantics('x="a b";') != mql_semantics('x="ab";')
    assert mql_semantics("x='a';") != mql_semantics("x='b';")
    assert mql_semantics('x="http://host/*literal*/"; // comment') == mql_semantics('x = "http://host/*literal*/";')


def test_mql_directive_end_and_function_macro_adjacency_are_meaningful():
    assert mql_semantics('#define F(x) x+1\n') != mql_semantics('#define F (x) x+1\n')
    assert mql_semantics('#define X 1\n+2\n') != mql_semantics('#define X 1+2\n')
    assert mql_semantics('#define X 1 \\\n+2\n') == mql_semantics('#define X 1+2\n')
    assert mql_semantics('#define X /*comment\ncontinued*/ 1\n') == mql_semantics('#define X 1\n')
    with pytest.raises(ValueError, match='UNTERMINATED'):
        mql_semantics('x="unterminated')


def test_nondecision_files_and_decision_comments_keep_observation_key(revision_tree):
    root, cfg = revision_tree
    before = observation_context(cfg, root)
    (root / 'dualtrade/reporting.py').write_text('def completely_new_report(): return 123\n')
    (root / 'MQL5/Experts/DualTradeReplay.mq5').write_text('void FakeReplay() {}\n')
    (root / 'dualtrade/risk.py').write_text('# explanatory note\n' + (root / 'dualtrade/risk.py').read_text())
    assert observation_context(cfg, root) == before


@pytest.mark.parametrize('relative', ['dualtrade/risk.py', 'dualtrade/new_signal.py', 'MQL5/Include/NewDecision.mqh'])
def test_decision_change_and_unknown_new_production_files_invalidate(revision_tree, relative):
    root, cfg = revision_tree
    before = observation_context(cfg, root)
    path = root / relative
    path.write_text((path.read_text() if path.exists() else '') + '\n' +
                    ('int NEW_LIMIT=1;\n' if path.suffix == '.mqh' else 'NEW_LIMIT = 1\n'))
    assert observation_context(cfg, root) != before


def test_missing_required_decision_module_fails_closed(revision_tree):
    root, cfg = revision_tree
    (root / 'dualtrade/risk.py').unlink()
    with pytest.raises(ValueError, match='DECISION_SOURCE_MISSING'):
        observation_context(cfg, root)


@pytest.mark.parametrize('section,key', [('risk', 'target'), ('shared_risk', 'position_fraction'), ('research', 'day_pool_size')])
def test_loaded_decision_settings_invalidate(revision_tree, section, key):
    root, cfg = revision_tree
    before = observation_context(cfg, root)
    changed = deepcopy(cfg)
    changed[section][key] += 1
    assert observation_context(changed, root) != before


def test_contract_costs_and_unknown_configuration_keys_invalidate(revision_tree):
    root, cfg = revision_tree
    before = observation_context(cfg, root)
    changed = deepcopy(cfg)
    changed['contracts'] = {'AAPL': {'commission_round_turn_per_lot': 0.1}}
    assert observation_context(changed, root) != before
    changed = deepcopy(cfg)
    changed['new_decision_setting'] = True
    assert observation_context(changed, root) != before


def test_dispatcher_infrastructure_excluded_but_review_prompt_included(revision_tree):
    root, cfg = revision_tree
    service = root / 'scripts/event-agent/service.py'
    service.write_text('def record_failure(x): return 1\ndef prompt(x): return "Reject doubts"\n')
    before = observation_context(cfg, root)
    service.write_text('def record_failure(x): return 999\ndef prompt(x): return "Reject doubts"\n')
    assert observation_context(cfg, root) == before
    service.write_text('def record_failure(x): return 999\ndef prompt(x): return "Approve doubts"\n')
    assert observation_context(cfg, root) != before


@pytest.mark.parametrize('function', ['health', 'deployment_issues', 'service', 'entry_allowed', 'claim'])
def test_dispatch_readiness_and_duplicate_gates_are_decision_logic(revision_tree, function):
    root, cfg = revision_tree
    service = root / 'scripts/event-agent/service.py'
    service.write_text(f'def {function}(x): return False\n')
    before = observation_context(cfg, root)
    service.write_text(f'def {function}(x): return True\n')
    assert observation_context(cfg, root) != before


def test_known_build_artifacts_ignored_unknown_native_library_included(revision_tree):
    root, cfg = revision_tree
    before = observation_context(cfg, root)
    for name in ('DualTrade.ex5', 'DualTradeReplay.ex5', 'DualTradeIntegrationTests.ex5'):
        (root / 'MQL5/Experts' / name).write_bytes(b'compiled artifact')
    assert observation_context(cfg, root) == before
    (root / 'MQL5/new_native_library.dll').write_bytes(b'new unknown native dependency')
    assert observation_context(cfg, root) != before


def test_model_and_review_schema_are_decision_inputs(revision_tree):
    root, cfg = revision_tree
    before = observation_context(cfg, root)
    settings = root / 'home/.codex/config.toml'
    settings.parent.mkdir(parents=True)
    settings.write_text('model = "model-a"\nmodel_reasoning_effort = "high"\n')
    assert observation_context(cfg, root) != before
    model_key = observation_context(cfg, root)
    settings.write_text(settings.read_text() + 'unrelated_editor_setting = "dark"\n')
    assert observation_context(cfg, root) == model_key
    schema = root / 'scripts/event-agent/review.schema.json'
    schema.write_text(schema.read_text().replace('"maximum": 40', '"maximum": 41'))
    assert observation_context(cfg, root) != model_key


def test_revision_is_auditable_and_does_not_migrate_legacy_records(revision_tree):
    root, cfg = revision_tree
    revision = observation_revision(cfg, root)
    assert revision['schema'] == 1
    assert len(revision['decision_hash']) == len(revision['decision_config']) == 64
    assert 'dualtrade/collector.py' in decision_manifest(root)
    assert 'dualtrade/reporting.py' not in decision_manifest(root)
    assert observation_context(cfg, root) != 'legacy-full-source-context'
