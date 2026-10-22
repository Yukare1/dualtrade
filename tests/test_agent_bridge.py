from copy import deepcopy
import json
import pytest
from dualtrade import agent_bridge as a
from dualtrade.config import load
from dualtrade.context_agent import evaluate
from test_ai_news import state, NOW


@pytest.fixture
def packet(monkeypatch):
    monkeypatch.setattr(a, 'current_context', lambda cfg: 'a'*64)
    r=a.parse_request('REQUEST|1|123.456.0.0.900|'+ 'a'*64 + '|1000|1055|AAPL.NAS|0|1|100|98|104|2|40|10|900|3|0|70|97|98|105|106\n')
    e={'checked_at':1001,'request':r,'quote':{'time':1001,'bid':99.99,'ask':100}}
    from dualtrade.storage import fingerprint
    e['news_context_sha256']=fingerprint({'ready':True})
    monkeypatch.setattr('dualtrade.decision_revision.observation_context', lambda cfg: 'decision-one')
    e['observation_context'] = 'decision-one'
    review={'schema':1,'request_id':r['id'],'context':r['context'],'evidence_sha256':'hash',
        'action':'APPROVE','rationale_tr':'Destek �zerinde teyitli yap�; maliyetler ve stop mesafesi mevcut s�n�rlar i�inde.',
        'checks':{k:True for k in a.CHECKS},'risk_usd':15,'win_probability':None}
    snap={'time':1001,'ready':True,'observation_context':'decision-one','observation_ready':True,'contracts':[{'symbol':'AAPL.NAS','verified':True,'entry_allowed':True,'ai_news':{'ready':True}}]}
    ea={'time':1001,'demo':True,'agent_review_required':True,'decision_context':r['context'],'equity':1000}
    return review,r,e,'hash',snap,ea,load(),1002


def test_approval_is_bound_and_contains_a_risk_ceiling_not_a_fixed_lot(packet):
    text=a.validate_review(*packet)
    v=text.strip().split('|')
    assert len(v)==12 and v[6:8]==['APPROVE','15'] and v[-1]=='OPEN'
    assert 'volume' not in packet[0]


@pytest.mark.parametrize('mutation',[
    lambda p:p[0].update(risk_usd=41),
    lambda p:p[0].update(risk_usd=True),
    lambda p:p[0].update(risk_usd=float('nan')),
    lambda p:p[0].update(win_probability=.75),
    lambda p:p[0]['checks'].update(events=False),
    lambda p:p[0].update(evidence_sha256='other'),
    lambda p:p[0].update(request_id='other'),
    lambda p:p[2]['quote'].update(time=900),
    lambda p:p[5].update(time=990),
    lambda p:p[5].update(equity=300),
    lambda p:p[5].update(halt=True),
    lambda p:p[5].update(intent_pending=True),
    lambda p:p[5].update(agent_review_required=False),
    lambda p:p[5].update(decision_context='b'*64),
    lambda p:p[4].update(ready=False),
    lambda p:p[4]['contracts'][0].update(entry_allowed=False),
    lambda p:p[4]['contracts'][0].update(veto_buy=True),
    lambda p:p[2]['request'].update(stop=97),
    lambda p:p[2].update(observation_context='older-decision'),
    lambda p:p[4].update(observation_context='older-decision'),
    lambda p:p[4].update(observation_ready=False),
])
def test_stale_unsafe_or_changed_reviews_are_blocked(packet,mutation):
    # Preserve shared request identity separately: changing evidence must be detected.
    p=deepcopy(list(packet));p[2]['request']=deepcopy(p[1]);mutation(p)
    with pytest.raises(ValueError):a.validate_review(*p)


def test_expired_and_revised_source_require_a_new_candidate(packet,monkeypatch):
    p=list(packet);p[-1]=1056
    with pytest.raises(ValueError,match='EXPIRED'):a.validate_review(*p)
    monkeypatch.setattr(a,'current_context',lambda cfg:'b'*64)
    with pytest.raises(ValueError,match='CHANGED'):a.validate_review(*packet)


def test_rejection_never_confers_risk(packet):
    p=list(packet);p[0]['action']='REJECT';p[0]['risk_usd']=0;p[4]['ready']=False
    assert '|REJECT|0|' in a.validate_review(*p)
    p[0]['risk_usd']=1
    with pytest.raises(ValueError):a.validate_review(*p)


def test_native_waiver_flag_alone_cannot_override_current_observation(packet):
    p=deepcopy(list(packet));p[4]['observation_ready']=False
    p[5].update(day_observation_waiver_active=True,day_observation_waiver_until=9999)
    with pytest.raises(ValueError,match='CURRENT_DECISION_OBSERVATION_REQUIRED'):
        a.validate_review(*p)


@pytest.mark.parametrize('bad',['nan','inf','-1','100.1'])
def test_invalid_native_request_numbers(bad):
    with pytest.raises(ValueError):
        a.parse_request('REQUEST|1|123.0.0|'+ 'a'*64 +'|1000|1055|AAPL.NAS|'+bad+'|1|100|98|104|2|40|10|900|3|0|70|97|98|105|106\n')


def test_official_agent_replaces_social_without_claiming_social_coverage():
    r=evaluate(['AAPL'],NOW,state())
    assert r['ready'] and not r['social_media_coverage'] and r['win_probability'] is None
    s=state();s['sources']['SEC:AAPL']['ok']=False
    assert not evaluate(['AAPL'],NOW,s)['ready']
    s=state();s['events']=[dict(id='unread',ticker='AAPL',published=NOW-1,state='pending',analysis=None)]
    assert not evaluate(['AAPL'],NOW,s)['ready']
    assert not evaluate([],NOW,state())['ready']


def test_retired_reddit_cannot_issue_network_requests(tmp_path):
    from dualtrade.providers import Providers,ProviderError
    from dualtrade.storage import Store
    p=Providers(load(),Store(tmp_path/'state.db'))
    p.request=lambda *a,**kw:pytest.fail('Retired Reddit must never contact a network')
    with pytest.raises(ProviderError,match='REDDIT_RETIRED'):p.social(['AAPL'])


def test_observation_check_only_cannot_change_entry_control(tmp_path):
    from dualtrade.cli import start_demo
    from dualtrade.storage import Store
    cfg=load();cfg['bridge_directory']=str(tmp_path/'bridge')
    result=start_demo(cfg,Store(tmp_path/'s.db'),check_only=True,check_order_permission=False,until=NOW+3600)
    assert not result.get('eligible') and not (tmp_path/'bridge/control.txt').exists()


def test_pre_session_revision_change_does_not_poison_unstarted_observation(tmp_path):
    from dualtrade.collector import Collector
    from dualtrade.storage import Store
    c=Collector(load(),broker=object(),store=Store(tmp_path/'s.db'))
    c.observation(1500,True,(1000,10000),'old')
    c.observation(1600,True,(1000,10000),'new')
    for t in range(1900,9706,5):c.observation(t,True,(1000,10000),'new')
    assert c.store.get('observation_pass')['config_hash']=='new'


def test_revision_change_inside_observation_cannot_restart_evidence(tmp_path):
    from dualtrade.collector import Collector
    from dualtrade.storage import Store
    c=Collector(load(),broker=object(),store=Store(tmp_path/'s.db'))
    c.observation(1900,True,(1000,10000),'old')
    for t in range(1905,9706,5):c.observation(t,True,(1000,10000),'new')
    assert c.store.get('observation_pass') is None


def test_ea_health_uses_the_clock_after_slow_provider_work():
    from dualtrade.collector import Collector
    from dualtrade import __version__
    ea={'demo':True,'version':__version__,'time':1010}
    assert not Collector.ea_healthy(ea,1000)  # cycle-start clock predates new heartbeat
    assert Collector.ea_healthy(ea,1011)      # actual clock after data retrieval
    assert not Collector.ea_healthy(ea,1021)
    assert not Collector.ea_healthy({**ea,'intent_pending':True},1011)


def test_mcp_history_normalizes_broker_time_and_excludes_forming_candles():
    rows=[{'time':'2026-10-22T13:'+minute+':00','open':100.,'high':101.,'low':99.,'close':100.5} for minute in ('00','15','30')]
    now=a.broker_timestamp('2026-10-22T13:30:00',10800)
    bars=a.closed_history(rows,10800,900,now,2)
    assert len(bars)==2 and bars[-1]['time']==now-900
    assert a.broker_timestamp('2026-10-22T10:30:00Z',10800)==now
    rows[0]['high']=99
    with pytest.raises(ValueError,match='INVALID_BROKER_CANDLE'):a.closed_history(rows,10800,900,now,2)


def test_native_mcp_exit_review_binds_actual_owned_position(tmp_path,monkeypatch):
    from dualtrade.storage import write_json
    from dualtrade import mt5_mcp
    cfg=load();cfg['bridge_directory']=str(tmp_path)
    monkeypatch.setattr(a,'current_context',lambda c:'a'*64)
    write_json(tmp_path/'ea-status.json',{'demo':True,'time':1000,'agent_nonce':'nonce','decision_context':'a'*64})
    position={'ticket':'123','identifier':'456','symbol':'AAPL.NAS','volume':.2,'magic':cfg['magic_day']}
    class Native:
        def verify_demo(self):return True
        def call(self,name,args=None):
            assert name in ('get_workspace_info','get_trading_open_positions')
            return {'positions':[position]} if name=='get_trading_open_positions' else {}
    monkeypatch.setattr(mt5_mcp,'NativeMT5',Native)
    review={'schema':1,'action':'CLOSE','nonce':'nonce','context':'a'*64,'ticket':'123','identifier':'456',
        'symbol':'AAPL.NAS','volume':.2,'rationale_tr':'Destek k�r�l�m�yla mevcut teknik pozisyonun gerek�esi ge�ersizle�ti.'}
    target,text=a.prepare_close(cfg,review,1001)
    assert target==tmp_path/'agent-close.txt' and '|123|456|AAPL.NAS|0.2|' in text
    position['magic']=999
    with pytest.raises(ValueError,match='OWNERSHIP'):a.prepare_close(cfg,review,1001)
    position['magic']=cfg['magic_day'];position['identifier']='other'
    with pytest.raises(ValueError,match='IDENTITY'):a.prepare_close(cfg,review,1001)


def test_day_activation_is_independent_of_optional_swing_council(tmp_path,monkeypatch):
    from dualtrade import cli,council,__version__
    from dualtrade.config import config_hash
    from dualtrade.storage import Store,write_json,fingerprint
    cfg=load();cfg['bridge_directory']=str(tmp_path/'bridge')
    monkeypatch.setattr(cli,'ROOT',tmp_path);monkeypatch.setattr(cli,'source_hash',lambda:'source')
    monkeypatch.setattr(council,'snapshot',lambda *a,**kw:pytest.fail('Day must not acquire a swing-only council dependency'))
    store=Store(tmp_path/'s.db')
    from dualtrade.decision_revision import observation_context
    context = fingerprint({'config_hash':config_hash(cfg),'source_hash':'source'})
    store.set('observation_pass',{'config_hash':observation_context(cfg)})
    write_json(tmp_path/'build/acceptance.json',{'passed':True,'source_hash':'source'})
    write_json(tmp_path/'bridge/snapshot.json',{'time':NOW,'ready':True,'decision_context':context,'observation_context':observation_context(cfg)})
    write_json(tmp_path/'bridge/ea-status.json',{'time':NOW,'demo':True,'orders_enabled':False,'version':__version__,'decision_context':context})
    result=cli.start_demo(cfg,store,NOW,style='day',check_only=True,check_order_permission=False)
    assert result['eligible'] and not (tmp_path/'bridge/control.txt').exists()
    # A reusable observation never substitutes for current deployed-source proof.
    write_json(tmp_path/'bridge/snapshot.json',{'time':NOW,'ready':True,'decision_context':'older-source'})
    assert 'Current-source native and research context required' in cli.start_demo(
        cfg,store,NOW,style='day',check_only=True,check_order_permission=False)['issues']
