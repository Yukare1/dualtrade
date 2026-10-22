import math

from dualtrade.config import ROOT, load
from dualtrade.universe import STOCKS
from dualtrade.investment import UNIVERSE
from dualtrade.official_news import COMPANIES, settings, sources
from dualtrade.ai_news import decision
from dualtrade.collector import encode_snapshot


def test_thirty_issuers_match_across_configuration_news_and_fundamentals():
    cfg = load(ROOT / 'config.example.json')
    stocks = {t: v for t, v in cfg['symbols'].items() if v['kind'] == 'stock'}
    assert len(stocks) == len(STOCKS) == 30 and stocks == STOCKS
    assert set(UNIVERSE) == set(COMPANIES) == set(settings()['tickers']) == set(stocks)
    assert all(COMPANIES[t][0] == v['cik'] for t, v in stocks.items())
    assert 'GOOG' in stocks and 'GOOGL' not in stocks
    assert set(cfg['symbols']) - set(stocks) == {'SPY', 'QQQ'}
    assert cfg['research']['day_pool_size'] == 10 and cfg['risk']['daily_entries'] == 5


def test_every_issuer_has_mandatory_sec_coverage_and_own_failure_gate():
    cfg = settings(); catalog = sources(cfg); now = 10000
    sec = {x['ticker']: x for x in catalog if x['kind'] == 'sec'}
    assert set(sec) == set(STOCKS)
    state = {'healthy': True, 'events': [], 'sources': {
        s['id']: {'ok': True, 'last_success': now, 'next_check': now + 300} for s in catalog}}
    assert decision(state, 'V', now)['ready']
    state['sources']['SEC:V']['ok'] = False
    assert not decision(state, 'V', now)['ready']
    assert decision(state, 'AAPL', now)['ready']


def test_expanded_discovery_budget_preserves_poll_intervals_with_headroom():
    cfg = settings(); total = 0; per_source = []
    for source in sources(cfg):
        interval = cfg['sec_poll_seconds'] if source['kind'] == 'sec' else cfg['feed_poll_seconds']
        requests = math.ceil(13 * 3600 / interval) + math.ceil(11 * 3600 / cfg['quiet_poll_seconds'])
        total += requests; per_source.append(requests)
    assert cfg['sec_poll_seconds'] == 300 and cfg['feed_poll_seconds'] == 120
    assert cfg['daily_request_limit'] >= total * 1.1
    assert max(per_source) < cfg['source_daily_request_limit']


def test_bridge_carries_all_thirty_stocks_plus_etfs_without_truncation():
    cfg = load(ROOT / 'config.example.json')
    base = dict(verified=True, commission=.04, slippage=20, margin_rate=.2,
                finance_long=0, finance_short=0, server_offset=10800, open=1, close=2,
                pool=False, entry_allowed=False, veto_buy=False, veto_sell=False,
                earnings_close=0, earnings_end=0, sec_close_until=0, previous_high=0, previous_low=0)
    contracts = [dict(base, ticker=t, symbol=t) for t in cfg['symbols']]
    snapshot = dict(time=1, mode='observe', ready=False, login=1, server='Demo',
                    observation_ready=False, risk=cfg['risk'], shared_risk=cfg['shared_risk'],
                    contracts=contracts, events=[])
    rows = encode_snapshot(snapshot).splitlines()
    actual = [row.split('|')[1] for row in rows if row.startswith('CONTRACT|')]
    assert len(actual) == 32 and set(actual) == set(cfg['symbols'])
