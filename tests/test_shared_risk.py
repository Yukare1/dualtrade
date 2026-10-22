from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace as N
import json
import pytest
from dualtrade.risk import budget, AccountLedger, DEFAULTS
from dualtrade.config import load, config_hash
from dualtrade.storage import Store, write_json, fingerprint
from dualtrade.swing import context, build
from dualtrade.collector import encode_snapshot


def capacity(equity=1000., **kw):
    args=dict(daily_base=1000., planned=0., downside=0., capital_base=1000., peak=1000., week_base=1000.)
    args.update(kw)
    return budget(equity, **args)


def test_shared_capacity_scales_down_but_never_up():
    assert capacity() == 40
    assert capacity(2000, daily_base=2000, peak=2000, week_base=2000) == 40
    assert capacity(500, daily_base=500, week_base=500) == 20
    assert capacity(planned=79) == 1
    assert capacity(planned=80) == 0
    assert capacity(sector_planned=55) == 5
    assert capacity(sector_planned=60) == 0


def test_all_books_share_daily_and_lifetime_capacity():
    # Day=30 + swing=30 + investment=19 consume the same 80-dollar ceiling.
    assert capacity(planned=30+30+19) == 1
    assert capacity(825, downside=20) == 5
    assert capacity(20, daily_base=20, week_base=20, downside=20) == 0
    assert capacity(1500, daily_base=1500, week_base=1500, peak=2490, downside=9) == 1
    assert capacity(410, daily_base=410, week_base=1000, downside=9) == 1
    assert capacity(float('nan')) == 0


def test_hard_loss_cannot_be_reset_by_week_or_restart():
    ledger=AccountLedger(1000, 1000, 1000, 1)
    ledger.mark(0, 2, continuous=False)
    restored=AccountLedger(**vars(ledger));restored.mark(1100, 3)
    assert restored.hard_halt and restored.unresolved
    assert restored.week == 1


def test_cashflows_adjust_all_baselines_without_creating_profit():
    ledger=AccountLedger(1000, 1000, 1000, 1)
    ledger.mark(1500, 1, cashflow=500)
    assert ledger.capital_base == ledger.peak == ledger.week_base == 1500
    assert not ledger.hard_halt
    ledger.mark(900, 1)
    assert ledger.week_halt and not ledger.hard_halt
    ledger.mark(900, 2)
    assert ledger.week_base == 900 and not ledger.week_halt


@pytest.mark.parametrize('override', [{'max_lifetime_loss':1001}, {'position_fraction':True},
    {'aggregate_fraction':float('nan')}, {'sector_fraction':0}, {'mystery':1}, {'stress_fraction':.01}])
def test_invalid_shared_limits_do_not_load(tmp_path, override):
    cfg=load();cfg['shared_risk'].update(override)
    p=tmp_path/'config.json';p.write_text(json.dumps(cfg))
    with pytest.raises(ValueError): load(p)


def test_three_unique_magics_and_existing_config_migration(tmp_path):
    cfg=load();cfg.pop('magic_investment');cfg.pop('shared_risk');cfg.pop('swing')
    p=tmp_path/'old.json';p.write_text(json.dumps(cfg))
    migrated=load(p)
    assert migrated['magic_investment'] not in (cfg['magic_day'], cfg['magic_swing'])
    assert migrated['shared_risk'] == DEFAULTS and migrated['swing']['enabled']
    cfg['magic_investment']=cfg['magic_swing'];p.write_text(json.dumps(cfg))
    with pytest.raises(ValueError): load(p)


NOW=datetime(2026,10,22,15,tzinfo=timezone.utc).timestamp()


def bars(step=.5, shift=0):
    return [dict(time=NOW-130*86400+i*86400+shift, close=100+i*step) for i in range(130)]


def test_swing_closed_daily_context_rejects_future_and_stale_prices():
    assert context(bars(), NOW)['bullish']
    assert not context(bars(shift=86400), NOW)['ready']
    assert not context(bars(shift=-8*86400), NOW)['ready']
    invalid=bars();invalid[-1]['close']=float('nan')
    assert not context(invalid, NOW)['ready']


def test_swing_is_independent_of_day_sentiment_but_not_benchmark_or_events():
    cfg=load()
    data={'SPY':bars(.1),'AAPL':bars(.5)}
    mt=N(TIMEFRAME_D1=1,copy_rates_from_pos=lambda symbol,*a:data.get(symbol))
    collector=N(cfg=cfg,broker=N(mt=mt))
    mapping={t:dict(symbol=t,verified=True,costs={'broker_utc_offset_seconds':0}) for t in data}
    c=dict(ticker='AAPL',symbol='AAPL',verified=True,server_offset=0,swing_sources_ok=True,
           entry_allowed=True,veto_buy=False,pool=False)
    snap=dict(ready=False,contracts=[c],foreign_exposure=False)
    build(collector,snap,mapping,NOW,True)
    assert snap['swing_ready'] and c['swing']['allowed'] and not snap['ready']
    c['entry_allowed']=False;build(collector,snap,mapping,NOW,True)
    assert c['swing']['ready'] and not c['swing']['allowed']
    data['SPY']=None;build(collector,snap,mapping,NOW,True)
    assert not snap['swing_ready']


def test_swing_activation_has_own_observation_and_no_social_dependency(tmp_path, monkeypatch):
    from dualtrade import cli, council, __version__
    cfg=load();cfg['bridge_directory']=str(tmp_path/'bridge')
    monkeypatch.setattr(cli,'ROOT',tmp_path);monkeypatch.setattr(cli,'source_hash',lambda:'new')
    monkeypatch.setattr(council,'snapshot',lambda *a,**kw:pytest.fail('Swing should not require council'))
    store=Store(tmp_path/'state.db')
    from dualtrade.decision_revision import observation_context
    context=fingerprint({'config_hash':config_hash(cfg),'source_hash':'new'})
    key=observation_context(cfg)
    store.set('investment:observation_pass',{'config_hash':key})
    write_json(tmp_path/'build/acceptance.json',{'passed':True,'source_hash':'new'})
    write_json(tmp_path/'bridge/snapshot.json',{'time':NOW,'swing_ready':True,'decision_context':context,'observation_context':key})
    write_json(tmp_path/'bridge/ea-status.json',{'time':NOW,'demo':True,'orders_enabled':True,'version':__version__,'decision_context':context})
    assert not cli.start_demo(cfg,store,NOW,style='swing')['started']
    store.set('swing:observation_pass',{'config_hash':key})
    assert cli.start_demo(cfg,store,NOW,style='swing')['started']
    assert json.loads((tmp_path/'bridge/control.json').read_text())['styles']==['swing']
    assert (tmp_path/'bridge/control.txt').read_text() == 'demo\nstyles|swing\n'


def test_protocol_four_carries_shared_limits_and_independent_modes():
    cfg=load();snap=dict(time=NOW,mode='observe',ready=False,login=1,server='Demo',observation_ready=False,
        risk=cfg['risk'],shared_risk=cfg['shared_risk'],enabled_styles=['swing'],swing_ready=True,
        swing_observation_ready=True,contracts=[],events=[])
    rows=encode_snapshot(snap).splitlines()
    assert 'MODES|0|0|0|0|1|1|1' in rows
    assert len(next(r for r in rows if r.startswith('RISK|')).split('|')) == 12
    assert rows[-1] == 'END|'+str(len(rows)-1)
