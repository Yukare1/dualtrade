from copy import deepcopy

import pytest

from dualtrade.day_pool import pool_size, select


def snapshot():
    return {'time': 100, 'decision_context': 'current', 'contracts': [
        {'ticker': ticker, 'verified': True, 'entry_allowed': allowed}
        for ticker, allowed in [('META', False), ('TSLA', False), ('AMD', True),
                                ('AAPL', True), ('MSFT', True), ('SPY', True), ('QQQ', True)]]}


def test_blocked_high_volatility_names_do_not_consume_scan_slots():
    data = snapshot(); before = deepcopy(data)
    ranked = ['META', 'TSLA', 'AMD', 'AAPL', 'MSFT', 'SPY', 'QQQ']
    assert select(ranked, [], data, 105, 'current', 5) == ['AMD', 'AAPL', 'MSFT', 'SPY', 'QQQ']
    assert data == before  # selection cannot authorize blocked contracts


@pytest.mark.parametrize('change', [
    {'time': 44}, {'time': 106}, {'time': float('nan')}, {'time': True},
    {'decision_context': 'old'}, {},
])
def test_unknown_stale_or_changed_context_waits_for_fresh_evidence(change):
    data = {**snapshot(), **change} if change else {}
    assert select(['AMD'], [], data, 105, 'current', 5) is None


def test_only_explicit_eligible_verified_symbols_are_selected():
    data = snapshot(); data['contracts'][2]['verified'] = False
    assert select(['UNKNOWN', 'AMD', 'TSLA'], [], data, 105, 'current', 5) == []


def test_holdings_survive_reduced_scan_limit_without_changing_permissions():
    data = snapshot()
    assert select(['AMD', 'AAPL'], ['META', 'TSLA'], data, 105, 'current', 1) == ['META', 'TSLA']
    assert not data['contracts'][0]['entry_allowed']


@pytest.mark.parametrize('value', [0, 31, True, 3.5, '5'])
def test_invalid_scan_limits_are_rejected(value):
    with pytest.raises(ValueError):
        pool_size({'research': {'day_pool_size': value}})


def test_default_is_ten_scanned_symbols_not_position_capacity():
    assert pool_size({}) == 10


@pytest.mark.parametrize("value", [9, 10, 30])
def test_expanded_scan_limits_are_supported(value):
    assert pool_size({"research": {"day_pool_size": value}}) == value
