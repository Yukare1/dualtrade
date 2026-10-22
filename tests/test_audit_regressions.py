"""Failure and handoff regressions; all state is isolated from live research."""
from copy import deepcopy
import json
import pytest

from dualtrade import agent_bridge as agent
from dualtrade.ai_news import apply, decision, read_state
from dualtrade.config import load
from dualtrade.context_agent import evaluate
from dualtrade.official_news import NewsError
from dualtrade.storage import fingerprint
from test_ai_news import NOW, state, saved_assessment, setup, serve, review


def fresh_sources(s, now):
    for source in s['sources'].values():
        source.update(last_success=now, next_check=now+300)
    return s


@pytest.mark.parametrize('status', ['pending', 'leased', 'expired', 'needs_manual'])
def test_unread_obligation_survives_directional_expiry(status):
    s=state({}, status=status)
    later=NOW+100*86400
    d=decision(fresh_sources(s,later),'AAPL',later)
    assert not d['ready'] and d['pending']==['event']
    assert not d['veto_buy'] and not d['veto_sell']


@pytest.mark.parametrize('direction,confidence', [('uncertain',.9),('negative',.5)])
def test_unresolved_material_analysis_does_not_silently_clear(direction,confidence):
    s=state(saved_assessment(direction,confidence))
    later=NOW+2*86400
    assert not decision(fresh_sources(s,later),'AAPL',later)['ready']


def test_late_full_review_clears_obligation_without_rejuvenating_signal(tmp_path):
    store,monitor,event=setup(tmp_path)
    serve(monitor,event)
    later=NOW+2*86400
    with store.db:
        store.db.execute("UPDATE events SET state='expired'")
    # Old records are still claimable, even after the previous queue-age policy.
    assert store.claim(later,1)[0]['id']==event['id']
    assert monitor.complete([review(event)],later)['completed']==[event['id']]
    s=read_state(later,tmp_path);s['sources']=fresh_sources(state(),later)['sources']
    d=decision(s,'AAPL',later)
    assert d['ready'] and not d['veto_buy'] and not d['veto_sell']
    assert d['material_events'][0]['published_at']==event['published']
    saved=json.loads(store.db.execute('SELECT analysis FROM events').fetchone()[0])
    assert saved['assessment']['expires_at']==event['published']+86400


def test_database_read_retains_very_old_unread_obligation(tmp_path):
    store,_,event=setup(tmp_path)
    later=NOW+100*86400
    s=read_state(later,tmp_path)
    s['sources']=fresh_sources(state(),later)['sources']
    assert not decision(s,'AAPL',later)['ready']
    assert event['id'] in decision(s,'AAPL',later)['pending']


def test_issuer_news_is_isolated_but_macro_news_blocks_entire_pool():
    s=state({},status='pending',ticker='MSFT')
    context=evaluate(['AAPL','MSFT'],NOW,s)
    assert context['ready'] and context['shared_ready'] and not context['issues']
    assert context['decisions']['AAPL']['ready'] and not context['decisions']['MSFT']['ready']
    assert 'MSFT' in context['symbol_issues']
    contracts=[dict(ticker=t,entry_allowed=True,investment_event_ok=True,swing_sources_ok=True,
                    veto_buy=False,veto_sell=False) for t in ('AAPL','MSFT')]
    apply({'contracts':contracts},s,NOW)
    assert contracts[0]['entry_allowed'] and not contracts[1]['entry_allowed']
    s['events'][0]['ticker']='MACRO'
    context=evaluate(['AAPL','MSFT'],NOW,s)
    assert not context['ready'] and not context['shared_ready']
    s=state();s['sources']['FED:press']['ok']=False
    assert not evaluate(['AAPL','MSFT'],NOW,s)['shared_ready']


def test_lease_replacement_fences_stale_reader_and_completion(tmp_path):
    store,monitor,event=setup(tmp_path);serve(monitor,event)
    first=store.claim(NOW+1801,1,owner='document-agent')[0]
    assert store.claim(NOW+1802,1,owner='execution-agent')==[]
    second=store.claim(NOW+3602,1,owner='document-agent')[0]
    assert first['lease_token']!=second['lease_token']
    r=review(event);r['lease_token']=first['lease_token']
    with pytest.raises(NewsError,match='NOT_OWNED'):
        monitor.complete([r],NOW+3603)
    with pytest.raises(NewsError,match='NOT_OWNED'):
        store.renew(event['id'],first['lease_token'],NOW+3603)
    assert store.renew(event['id'],second['lease_token'],NOW+3603)['lease_until']==NOW+5403
    r['lease_token']=second['lease_token']
    assert monitor.complete([r],NOW+3604)['completed']==[event['id']]


def test_uncertain_review_can_be_revisited_but_confirmed_review_cannot(tmp_path):
    store,monitor,event=setup(tmp_path);serve(monitor,event)
    r=review(event);r['assessment'].update(direction='uncertain')
    monitor.complete([r],NOW)
    assert store.revisit(event['id'])['requeued']==event['id']
    r['assessment']['direction']='positive'
    monitor.complete([r],NOW+1)
    with pytest.raises(NewsError,match='ONLY_UNRESOLVED'):
        store.revisit(event['id'])


def test_concurrent_completions_have_exactly_one_winner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from dualtrade.official_news import NewsStore,OfficialNews,settings
    store,monitor,event=setup(tmp_path);serve(monitor,event)
    owned=store.claim(NOW+1801,1,owner='document-agent')[0]
    r=review(event);r['lease_token']=owned['lease_token']
    barrier=Barrier(2)
    def complete_one():
        own_store=NewsStore(tmp_path)
        try:
            barrier.wait(timeout=5)
            try:
                OfficialNews(settings(),own_store).complete([r],NOW+1802)
                return 'completed'
            except NewsError as error:return str(error)
        finally:own_store.db.close()
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures=[executor.submit(complete_one) for _ in range(2)]
        outcomes=[f.result(timeout=10) for f in futures]
    assert sorted(outcomes)==['EVENT_NOT_PENDING','completed']


def test_news_handoff_change_rejects_old_technical_review(packet):
    p=deepcopy(list(packet))
    p[4]['contracts'][0]['ai_news']['reviews']=[{'event_id':'new-information'}]
    with pytest.raises(ValueError,match='NEWS_CONTEXT_CHANGED'):
        agent.validate_review(*p)


class Clock:
    def __init__(self,wall=1000): self.wall,self.elapsed=wall,0
    def time(self): return self.wall+self.elapsed
    def monotonic(self): return self.elapsed
    def sleep(self,seconds): self.elapsed+=seconds


def native_request(created=1020,expires=1055,context='a'*64):
    return 'REQUEST|1|123.0.0|'+context+f'|{created}|{expires}|AAPL.NAS|0|1|100|98|104|2|40|10|900|3|0|70|97|98|105|106\n'


def test_wait_catches_new_bar_in_same_turn_and_never_extends_expiry(tmp_path,monkeypatch):
    cfg=load();cfg['bridge_directory']=str(tmp_path)
    folder=tmp_path/'agent-requests';folder.mkdir()
    (folder/'123.0.0.txt').write_text(native_request())
    monkeypatch.setattr(agent,'current_context',lambda cfg:'a'*64)
    c=Clock()
    result=agent.wait_candidate(cfg,50,clock=c.time,monotonic=c.monotonic,sleep=c.sleep)
    assert c.elapsed==20 and result['pending'][0]['expires']==1055 and result['seconds_left']==35
    c.wall=1060
    result=agent.wait_candidate(cfg,2,clock=c.time,monotonic=c.monotonic,sleep=c.sleep)
    assert not result['pending'] and result['timed_out']


def test_wait_ignores_old_source_and_stops_with_monotonic_deadline(tmp_path,monkeypatch):
    cfg=load();cfg['bridge_directory']=str(tmp_path)
    folder=tmp_path/'agent-requests';folder.mkdir()
    (folder/'123.0.0.txt').write_text(native_request(1000,1055,context='b'*64))
    monkeypatch.setattr(agent,'current_context',lambda cfg:'a'*64)
    c=Clock()
    def backwards_sleep(seconds): c.sleep(seconds);c.wall-=1
    result=agent.wait_candidate(cfg,3,clock=c.time,monotonic=c.monotonic,sleep=backwards_sleep)
    assert result['timed_out'] and c.elapsed==3
    with pytest.raises(ValueError):agent.wait_candidate(cfg,51)


def test_duplicate_review_cannot_overwrite_first_decision_or_audit(tmp_path,monkeypatch):
    from dualtrade.storage import write_json
    cfg=load();cfg['bridge_directory']=str(tmp_path/'bridge')
    decision_path=tmp_path/'bridge/agent-decisions/123.0.0.txt'
    decision_path.parent.mkdir(parents=True)
    decision_path.write_text('original-decision')
    review_path=tmp_path/'input.json';write_json(review_path,{'request_id':'123.0.0','action':'APPROVE'})
    monkeypatch.setattr(agent,'ROOT',tmp_path)
    monkeypatch.setattr(agent,'prepare_review',lambda *a:(decision_path,'replacement'))
    with pytest.raises(ValueError,match='ALREADY_DECIDED'):
        agent.publish(cfg,review_path,1000)
    assert decision_path.read_text()=='original-decision'
    assert not (tmp_path/'runtime/agent-reviews/123.0.0.json').exists()


# Reuse the evidence fixture without depending on collection order.
from test_agent_bridge import packet
