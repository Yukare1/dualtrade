from copy import deepcopy
from datetime import datetime, timezone, timedelta, date
from types import SimpleNamespace as N
import hashlib
import json
import pytest
from dualtrade.investment import evaluate, candidate, sized_order, update_thesis, exit_action
from dualtrade.material_news import assess, classify, verify_review, primary_url, numeric_anchor
from dualtrade.fundamentals import investment_metrics, point_in_time, accepted_filings
from dualtrade.collector import encode_snapshot
from dualtrade.config import load

NOW=datetime(2026,10,22,14,tzinfo=timezone.utc).timestamp()

def metrics(**kw):
    return dict(complete=True,revenue=1000.,previous_revenue=900.,net_income=160.,previous_net_income=140.,
        operating_income=200.,fcf=150.,cash=100.,debt=200.,shares=100.,period_end='2026-06-30',checked_at=NOW,**kw)

def analysis(): return evaluate(metrics(),NOW)

def event(kind='guidance', **kw):
    return dict(ticker='AAPL',event_id='a'*64,primary_verified=True,kind=kind,before=100.,after=110.,
                published_at=NOW-300,first_observed=NOW-200,reference_price=25.,**kw)

def spec():
    return {'contract_size':1.,'tick_size':.01,'point':.01,'minimum':1.,'maximum':1000.,'step':1.,
            'commission':.1,'costs':{'slippage_points':0.,'financing_long_per_lot_day':.001}}

def test_score_and_conservative_value():
    a=analysis();assert a['ready'] and a['score']==100 and a['fair_value']==30
    assert sum(a['components'].values())==100

@pytest.mark.parametrize('field', ['revenue','previous_revenue','net_income','fcf','cash','debt','shares'])
@pytest.mark.parametrize('bad', [None,float('nan'),True])
def test_missing_or_invalid_is_not_zero(field,bad):
    m=metrics();m[field]=bad
    assert not evaluate(m,NOW)['ready']

@pytest.mark.parametrize('field',['net_income','operating_income','fcf'])
def test_negative_profit_breaks_thesis(field):
    m=metrics();m[field]=-1
    a=evaluate(m,NOW);assert a['thesis_broken']
    assert exit_action(a,{}, {},20)[0]=='exit'

def test_ages_and_negative_base():
    m=metrics();m['checked_at']=NOW-86401;assert not evaluate(m,NOW)['ready']
    m=metrics();m['period_end']='2026-01-01';assert not evaluate(m,NOW)['ready']
    m=metrics();m['previous_net_income']=0;assert not evaluate(m,NOW)['ready']

def test_normal_vs_half_sized_news_entry():
    a=analysis();assert candidate(a,25,1,{},NOW)['allowed']
    n=assess([event()], 'AAPL',metrics(),NOW,True)
    c=candidate(a,26,1,n,NOW)
    assert c['allowed'] and c['early'] and c['risk_cap']==20 and c['nominal_fraction']==.125
    assert not candidate(a,26.51,1,n,NOW)['allowed']
    assert not candidate(a,31,10,n,NOW)['allowed']
    n['coverage_ok']=False;assert not candidate(a,26,1,n,NOW)['allowed']

def test_early_news_staleness_and_missing_reference():
    e=event();e['published_at']=NOW-72*3600-1
    assert not candidate(analysis(),26,1,assess([e],'AAPL',metrics(),NOW,True),NOW)['allowed']
    e=event();e.pop('reference_price')
    assert not candidate(analysis(),26,1,assess([e],'AAPL',metrics(),NOW,True),NOW)['allowed']

@pytest.mark.parametrize('after,action',[(110,'positive'),(109,'context'),(90,'reduce'),(75,'exit'),(100,'context')])
def test_guidance_materiality(after,action):
    e=event();e['after']=after;assert classify(e,metrics())==action

def test_contract_materiality_and_binding_period():
    e=event('contract_win',revenue_impact=100.,months=12,firm=True)
    assert classify(e,metrics())=='positive'
    e['kind']='contract_loss';assert classify(e,metrics())=='reduce'
    e['revenue_impact']=250;assert classify(e,metrics())=='exit'
    e['firm']=False;assert classify(e,metrics())=='context'

def test_duplicate_conflict_and_future_evidence():
    e=event();e['after']=75
    a=assess([e,e], 'AAPL',metrics(),NOW,True);assert len(a['events'])==1 and a['action']=='exit'
    e['first_observed']=NOW+1;assert assess([e],'AAPL',metrics(),NOW)['action']=='hold'
    e['first_observed']=NOW;e['conflicting']=True
    assert assess([e],'AAPL',metrics(),NOW)['conflict']

def test_two_distinct_weak_quarters_not_two_polls():
    a=analysis();a['score']=40
    s=update_thesis({},a);assert update_thesis(s,a)['low_quarters']==1
    a['metrics']['period_end']='2026-09-30';s=update_thesis(s,a)
    assert s['low_quarters']==2 and exit_action(a,s,{},20)[0]=='exit'

def test_holding_earnings_no_ema_or_age_exit_and_early_expiry():
    a=analysis()
    assert exit_action(a,{}, {},20,opened=NOW-400*86400,now=NOW)[0]=='hold'
    assert exit_action(a,{'entry_period':'2026-06-30'}, {},20,early=True,opened=NOW-91*86400,now=NOW)[0]=='promote'
    a['score']=60
    assert exit_action(a,{'entry_period':'2026-06-30'}, {},20,early=True,opened=NOW-91*86400,now=NOW)[0]=='exit'

def test_costs_nominal_and_minimum_lot():
    d=candidate(analysis(),20,1,{},NOW);r=sized_order(d,20,1,spec(),1000,40)
    assert r['allowed'] and r['nominal']<=250 and r['quantity']*r['unit_risk']<=40
    costly=spec();costly['costs']['financing_long_per_lot_day']=.1
    assert sized_order(d,20,1,costly,1000,40)['reason']=='COST_OR_NET_RR'
    large=spec();large['minimum']=100
    assert not sized_order(d,20,1,large,1000,40)['allowed']

def test_event_source_identity_and_quote_review():
    cfg=load();cik=cfg['symbols']['AAPL']['cik']
    assert not primary_url('https://www.sec.gov.evil.example/Archives/edgar/data/320193/x.htm','AAPL',cik)
    assert not primary_url('https://www.sec.gov/Archives/edgar/data/999/123/x.htm','AAPL',cik)
    quote='Apple increases annual net income guidance from 100 to 110 million for fiscal 2026.'
    review={'ticker':'AAPL','reviewed':True,'reviewer':'Test fixture','source':'https://investor.apple.com/news/test.html',
        'published_at':NOW-100,'quote':quote,'kind':'guidance','metric':'net_income','period':'2026','unit':'USD million',
        'before':100,'after':110,'before_text':'100','after_text':'110 million','event_key':'guidance:2026', 'reference_price':999}
    response=N(content=quote.encode(),text=quote)
    providers=N(request=lambda *a,**k:response,key=lambda name:'Test test@example.test')
    verified=verify_review(review,cfg,providers,NOW)
    assert verified['primary_verified'] and 'reference_price' not in verified
    review['quote']='A fabricated statement which has no primary source evidence at all.'
    with pytest.raises(ValueError):verify_review(review,cfg,providers,NOW)


def test_economic_anchors_cannot_disagree_with_numbers():
    assert numeric_anchor('$100 million',100,'USD million')
    assert numeric_anchor('$100 million',100_000_000,'USD')
    assert not numeric_anchor('100',1000,'USD million')
    assert not numeric_anchor('1 or 100',100,'USD')

def facts_fixture():
    nodes={};ends=['2024-09-30','2024-12-31','2025-03-31','2025-06-30','2025-09-30','2025-12-31','2026-03-31','2026-06-30']
    for tag,value in [('Revenues',100),('NetIncomeLoss',20),('OperatingIncomeLoss',25),('NetCashProvidedByUsedInOperatingActivities',30),('PaymentsToAcquirePropertyPlantAndEquipment',10)]:
        rows=[]
        for i,end in enumerate(ends):
            enddate=date.fromisoformat(end);month=enddate.month-2
            rows.append({'start':date(enddate.year,month,1).isoformat(),'end':end,'val':value+(10 if i>=4 else 0),
                'accepted_at':'2026-08-01T00:00:00Z','accn':'fixture','form':'10-Q'})
        nodes[tag]={'units':{'USD':rows}}
    for tag,value in [('CashAndCashEquivalentsAtCarryingValue',100),('DebtAndFinanceLeaseObligations',200),('CommonStockSharesOutstanding',100)]:
        nodes[tag]={'units':{'shares' if 'Shares' in tag else 'USD':[{'end':ends[-1],'val':value,'accepted_at':'2026-08-01T00:00:00Z','accn':'fixture','form':'10-Q'}]}}
    return {'facts':{'us-gaap':nodes}}

def test_eight_quarters_and_no_invented_debt():
    f=facts_fixture();m=investment_metrics(f,'2026-10-22T14:00:00Z')
    assert m['complete'] and m['revenue']==440 and m['previous_revenue']==400 and m['fcf']==80
    del f['facts']['us-gaap']['DebtAndFinanceLeaseObligations']
    assert 'debt' in investment_metrics(f,'2026-10-22T14:00:00Z')['missing']

def test_ytd_cashflows_are_not_summed_as_quarters():
    f=facts_fixture();rows=f['facts']['us-gaap']['NetCashProvidedByUsedInOperatingActivities']['units']['USD']
    # Change 2025 Q2 to a 6-month YTD figure, supplying Q1 for subtraction.
    rows[3]['start']='2025-01-01';rows[3]['val']=60
    m=investment_metrics(f,'2026-10-22T14:00:00Z')
    assert m['complete'] and m['provenance']['ocf'][3]['val']==30


def test_complete_debt_and_productive_asset_alternatives():
    f=facts_fixture();n=f['facts']['us-gaap']
    n['DebtLongtermAndShorttermCombinedAmount']=n.pop('DebtAndFinanceLeaseObligations')
    n['PaymentsToAcquireProductiveAssets']=n.pop('PaymentsToAcquirePropertyPlantAndEquipment')
    assert investment_metrics(f,'2026-10-22T14:00:00Z')['complete']
    full=n.pop('DebtLongtermAndShorttermCombinedAmount')
    n['DebtCurrent']=deepcopy(full);n['DebtCurrent']['units']['USD'][0]['val']=50
    n['LongTermDebtNoncurrent']=deepcopy(full);n['LongTermDebtNoncurrent']['units']['USD'][0]['val']=150
    assert investment_metrics(f,'2026-10-22T14:00:00Z')['debt']==200


def test_latest_share_source_wins_over_stale_alternative():
    f=facts_fixture();common=f['facts']['us-gaap']['CommonStockSharesOutstanding']['units']['shares'][0]
    newer=deepcopy(common);newer['end']='2026-07-10';newer['val']=110
    common['end']='2025-12-31'
    f['facts']['dei']={'EntityCommonStockSharesOutstanding':{'units':{'shares':[newer]}}}
    assert investment_metrics(f,'2026-10-22T14:00:00Z')['shares']==110

def test_older_acceptances_and_future_restatement():
    submissions={'filings':{'recent':{'accessionNumber':['new'],'acceptanceDateTime':['2026-12-01T00:00:00Z']}},
        'historical':[{'accessionNumber':['old'],'acceptanceDateTime':['2025-01-01T00:00:00Z']}]}
    assert 'old' in accepted_filings(submissions)
    f=facts_fixture();r=f['facts']['us-gaap']['Revenues']['units']['USD'][0];r['accn']='new'
    assert not point_in_time(f,accepted_filings(submissions),'2026-10-22T00:00:00Z')['facts']['us-gaap']['Revenues']['units']['USD']

def test_bridge_style_ready_is_independent():
    cfg=load();s={'time':NOW,'mode':'observe','ready':False,'investment_ready':True,'investment_observation_ready':False,
        'enabled_styles':['investment'],'login':1,'server':'Demo','observation_ready':False,'risk':cfg['risk'],'contracts':[],'events':[]}
    lines=encode_snapshot(s).splitlines()
    assert lines[0].startswith('HEADER|4|') and 'MODES|1|0|0|1|0|0|0' in lines
    assert lines[-1]=='END|'+str(len(lines)-1)


def test_investment_activation_does_not_require_reddit_or_council(tmp_path,monkeypatch):
    from dualtrade import cli,council
    from dualtrade.config import config_hash
    from dualtrade.storage import Store,write_json,fingerprint
    cfg=load();cfg['bridge_directory']=str(tmp_path/'bridge')
    monkeypatch.setattr(cli,'ROOT',tmp_path)
    monkeypatch.setattr(cli,'source_hash',lambda:'current')
    def unavailable(*a,**k): raise AssertionError('Investment must not call the required council gate')
    monkeypatch.setattr(council,'snapshot',unavailable)
    store=Store(tmp_path/'state.db')
    from dualtrade.decision_revision import observation_context
    context=fingerprint({'config_hash':config_hash(cfg),'source_hash':'current'})
    key=observation_context(cfg)
    store.set('investment:observation_pass',{'config_hash':key})
    write_json(tmp_path/'build/acceptance.json',{'passed':True,'source_hash':'current'})
    write_json(tmp_path/'bridge/snapshot.json',{'time':NOW,'ready':False,'investment_ready':True,'decision_context':context,'observation_context':key})
    from dualtrade import __version__
    write_json(tmp_path/'bridge/ea-status.json',{'time':NOW,'demo':True,'orders_enabled':True,'version':__version__,'decision_context':context})
    assert cli.start_demo(cfg,store,NOW,style='investment')['started']
    assert json.loads((tmp_path/'bridge/control.json').read_text())['styles']==['investment']


def test_missing_investment_acceptance_still_blocks(tmp_path,monkeypatch):
    from dualtrade import cli
    from dualtrade.storage import Store
    cfg=load();cfg['bridge_directory']=str(tmp_path)
    monkeypatch.setattr(cli,'ROOT',tmp_path)
    assert not cli.start_demo(cfg,Store(tmp_path/'s.db'),NOW,style='investment')['started']


def test_shadow_news_halves_once_and_baseline_keeps_position(tmp_path):
    from dualtrade.investment_shadow import run
    from dualtrade.storage import Store
    store=Store(tmp_path/'shadow.db')
    base={'symbol':'AAPL.NAS','style':'investment','direction':1,'entry':20.,'quantity':4.,'unit_value':1.,'stop':16.,
          'opened':NOW-100,'early':False,'entry_period':'2026-06-30','budget':40.,'costs':.1,'financing_checked':NOW}
    state={'schema':1,'started_at':NOW-100,'books':{name:{'equity':1000.,'positions':{'investment':deepcopy(base)},
        'closed':[],'peak':1000.,'max_drawdown':0.,'rejections':{}} for name in ('fundamental','fundamental_news')}}
    store.set('investment:shadow',state)
    mt=N(symbol_info_tick=lambda symbol:N(bid=20.,ask=20.01,time=NOW))
    collector=N(store=store,broker=N(mt=mt),cfg=load())
    c={'ticker':'AAPL','symbol':'AAPL.NAS','server_offset':0,'finance_long':0.,'commission':.1,'margin_rate':.2,
        'investment':{'daily_close':20.,'ready':True,'action':2}}
    a=analysis();news={'action':'reduce','event_id':'unique'}
    snap={'contracts':[c],'events':[],'investment_report':{'AAPL':{'analysis':a,'thesis':{},'news':news,'decision':{}}}}
    mapping={'AAPL':{'minimum':1.,'maximum':100.,'step':1.,'contract_size':1.}}
    run(collector,snap,mapping,NOW);run(collector,snap,mapping,NOW+1)
    saved=store.get('investment:shadow')
    assert saved['books']['fundamental']['positions']['investment']['quantity']==4
    assert saved['books']['fundamental_news']['positions']['investment']['quantity']==2
    assert len(saved['books']['fundamental_news']['realized_reductions'])==1
