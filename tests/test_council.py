from copy import deepcopy
from datetime import datetime,timezone
import pytest
from dualtrade.council import load_council,household_weights,evaluate,evidence_rows,parse_pdf,import_review
from dualtrade.storage import fingerprint
from dualtrade.collector import encode_snapshot
from dualtrade.config import load,config_hash
from dualtrade.shadow import ShadowLedger,ShadowPosition
from pathlib import Path
from types import SimpleNamespace
from dualtrade.storage import write_json

NOW=datetime(2026,10,22,14,tzinfo=timezone.utc).timestamp()


def source(rows):
    rules=load_council()
    documents=[]
    for i,(member,ticker,kind,owner,asset_type,day) in enumerate(rows):
        row={'ticker':ticker,'type':kind,'owner':owner,'owner_name':'spouse' if owner=='SP' else 'self',
             'asset_type':asset_type,'date':day,'status':'New','amount':'$1,001 - $15,000','amount_low':1001,'amount_high':15000}
        documents.append({'doc_id':str(i),'year':2026,'filed':'2026-10-20','member_id':member,'first_seen_at':NOW-60,'sha256':'example',
                          'url':'https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/'+str(i)+'.pdf','rows':[row]})
    return {'healthy':True,'success':NOW-30,'rules_hash':fingerprint(rules),'documents':documents},rules


def test_importance_weighting_is_normalized_bounded_and_unequal():
    weights=household_weights(load_council())
    assert sum(weights.values())==pytest.approx(1)
    assert weights['pelosi']>weights['mccaul']>weights['gottheimer']>weights['crenshaw']
    assert max(weights.values())<=.4
    rules=load_council();rules['members'][0]['importance']=100
    # load validates custom files; evaluate expects already validated configuration.
    assert max(household_weights(rules).values())>.4


def test_single_pelosi_household_cannot_veto_even_with_spouse_and_repeated_rows():
    data,rules=source([('pelosi','NVDA','P',o,'ST','2026-10-01') for o in ('','SP','JT','SP')])
    result=evaluate(data,rules,['NVDA'],NOW)['decisions']['NVDA']
    assert len(result['household_votes'])==1 and not result['veto_sell'] and result['sell_allowed']


def test_two_important_households_can_veto_short_against_purchase_evidence():
    data,rules=source([(m,'NVDA','P','SP','ST','2026-10-01') for m in ('pelosi','mccaul')])
    d=evaluate(data,rules,['NVDA'],NOW)['decisions']['NVDA']
    assert d['veto_sell'] and not d['sell_allowed'] and d['buy_allowed']


def test_two_small_households_do_not_reach_minimum_council_coverage():
    data,rules=source([(m,'NVDA','P','','ST','2026-10-01') for m in ('khanna','crenshaw')])
    d=evaluate(data,rules,['NVDA'],NOW)['decisions']['NVDA']
    assert d['status']=='INSUFFICIENT_EVIDENCE' and d['sell_allowed']


def test_sales_are_reduction_evidence_and_can_veto_long_never_inferred_shorts():
    data,rules=source([(m,'AAPL','S','SP','ST','2026-10-01') for m in ('pelosi','mccaul')])
    d=evaluate(data,rules,['AAPL'],NOW)['decisions']['AAPL']
    assert not d['buy_allowed'] and d['sell_allowed']
    assert all(r['holding_status']=='sale_reported_short_not_inferred' for r in evidence_rows(data,rules,NOW))
    assert all(r['current_open_position_confirmed'] is False for r in evidence_rows(data,rules,NOW))


def test_options_cannot_masquerade_as_stock_votes():
    data,rules=source([(m,'NVDA','P','SP','OP','2026-10-01') for m in ('pelosi','mccaul')])
    assert not evaluate(data,rules,['NVDA'],NOW)['decisions']['NVDA']['household_votes']


def test_as_of_never_uses_trade_before_its_actual_first_public_observation():
    data,rules=source([(m,'AAPL','P','','ST','2026-09-01') for m in ('pelosi','mccaul')])
    assert not evidence_rows(data,rules,NOW-120)
    assert len(evidence_rows(data,rules,NOW))==2


def test_future_and_stale_rows_dont_vote_and_stale_feed_blocks_only_council():
    data,rules=source([('pelosi','AAPL','P','','ST','2026-10-25'),('mccaul','AAPL','P','','ST','2025-10-01')])
    assert not evaluate(data,rules,['AAPL'],NOW)['decisions']['AAPL']['household_votes']
    data['success']=NOW-86401
    d=evaluate(data,rules,['AAPL'],NOW)['decisions']['AAPL']
    assert not d['ready'] and not d['buy_allowed'] and not d['sell_allowed']


def test_later_ambiguous_amendment_cancels_old_vote_instead_of_silently_ignoring_it():
    data,rules=source([('pelosi','AAPL','P','','ST','2026-09-01'),('pelosi','AAPL','E','','ST','2026-10-01')])
    assert not evaluate(data,rules,['AAPL'],NOW)['decisions']['AAPL']['household_votes']


def test_opposing_household_owners_same_day_abstain():
    data,rules=source([('pelosi','AAPL','P','','ST','2026-10-01'),('pelosi','AAPL','S','SP','ST','2026-10-01')])
    assert not evaluate(data,rules,['AAPL'],NOW)['decisions']['AAPL']['household_votes']


def test_changed_membership_weights_and_expired_roles_cannot_reuse_source_health():
    data,rules=source([]);rules=deepcopy(rules);rules['members'][0]['importance']=2.9
    assert not evaluate(data,rules,['AAPL'],NOW)['healthy']
    data,rules=source([]);rules['roles_valid_until']='2026-10-21'
    assert not evaluate(data,rules,['AAPL'],NOW)['healthy']


def test_fourth_shadow_book_applies_council_to_swing_entries_only():
    p=ShadowPosition('AAPL','swing','pullback',1,NOW,100,99,102,1,1,.1,1,NOW+1000)
    s=ShadowLedger();s.enter(p,True,True,council_ok=False)
    assert s.state['events_sentiment']['positions']
    assert not s.state['events_sentiment_council']['positions']
    p.style='day';p.symbol='MSFT';s.enter(p,True,True,council_ok=False)
    assert s.state['events_sentiment_council']['positions']['day']


def test_bridge_v3_encodes_separate_council_without_overwriting_shared_event_gate():
    cfg=load();c={'ticker':'AAPL','symbol':'AAPL.NAS','verified':True,'commission':.4,'slippage':20,'margin_rate':.2,'finance_long':0,'finance_short':0,'server_offset':10800,
                 'open':1,'close':999,'pool':True,'entry_allowed':True,'veto_buy':False,'veto_sell':False,'earnings_close':0,'earnings_end':0,'sec_close_until':0,'previous_high':0,'previous_low':0,
                 'council':{'ready':True,'buy_allowed':False,'sell_allowed':True,'as_of':100}}
    snap={'time':100,'mode':'observe','ready':False,'login':123,'server':'Demo','observation_ready':False,'risk':cfg['risk'],'contracts':[c],'events':[]}
    lines=encode_snapshot(snap).splitlines()
    assert lines[0].startswith('HEADER|4|') and 'COUNCIL|AAPL.NAS|1|0|1|100' in lines
    assert lines[-1]=='END|8'


def test_official_pelosi_pdf_matches_visually_checked_complete_seven_row_table():
    raw=(Path(__file__).parent/'fixtures/house-20035143.pdf').read_bytes()
    filing={'doc_id':'20035143','filed':'2026-08-21','first':'Nancy','last':'Pelosi'}
    result=parse_pdf(raw,filing)
    assert result['page_count']==2 and result['row_count']==7
    assert [r['ticker'] for r in result['rows']]==['BE','BE','BE','BE','INTC','INTC',None]
    assert all(r['owner']=='SP' for r in result['rows'])
    assert result['rows'][0]['amount_low']==1_000_001 and result['rows'][0]['amount_high']==5_000_000
    assert result['rows'][1]['asset_type']=='OP' and result['rows'][1]['option']['contracts']==100
    assert result['rows'][1]['option']['strike']==100 and result['rows'][1]['option']['expires']=='2027-06-17'
    assert result['rows'][0]['days_to_disclose']==28


def test_scanned_page_cannot_be_silently_lost_from_multi_page_disclosure(monkeypatch):
    class PDF:
        pages=[SimpleNamespace(extract_text=lambda **kw:'Some readable table'),SimpleNamespace(extract_text=lambda **kw:'')]
        def __enter__(self):return self
        def __exit__(self,*args):pass
    monkeypatch.setattr('pdfplumber.open',lambda *args:PDF())
    with pytest.raises(ValueError,match='SCANNED_PDF'):parse_pdf(b'example',{})


def test_unreviewed_manual_table_cannot_be_imported(tmp_path):
    review=tmp_path/'review.json';write_json(review,{'year':2026,'doc_id':'1','reviewed_complete_document':False})
    with pytest.raises(ValueError,match='Complete visual review'):import_review(review,now=NOW)


def test_config_rejects_dominant_household_instead_of_silently_normalizing(tmp_path):
    rules=load_council();rules['members'][0]['importance']=100
    path=tmp_path/'council.json';write_json(path,rules)
    with pytest.raises(ValueError,match='40% cap'):load_council({'council_file':str(path)})
