import time
import pytest
from dualtrade.providers import verified_macro,ProviderError,Providers
from dualtrade.storage import Store
from dualtrade.broker import native_sessions,interest_cost
from dualtrade.config import load
from dualtrade.alpha_mcp import Gateway


def test_native_sessions_require_fresh_clock(tmp_path):
    p=tmp_path/'native.txt';p.write_text('CLOCK|1000|11800|11800\nSESSION|AAPL.NAS|1|0|59700|82500\n')
    assert native_sessions(p,1001)[0]==10800
    assert native_sessions(p,1020) is None


def test_financing_does_not_offset_risk_with_credit():
    assert interest_cost(100,1,-3.6)==pytest.approx(.01)
    assert interest_cost(100,1,3.6)==0


def test_verified_calendar_expiration_and_source():
    now=100000
    m={'verified':True,'checked_at':now,'coverage_from':now-1,'coverage_until':now+31*86400,'source':'https://www.bls.gov/schedule/','events':[]}
    assert verified_macro('BLS',m,now)==[]
    with pytest.raises(ProviderError):verified_macro('BLS',m,now+86401)
    m['source']='https://www.bls.gov.attacker.example/schedule/'
    with pytest.raises(ProviderError):verified_macro('BLS',m,now)


def test_official_mcp_shares_direct_api_budget(tmp_path,monkeypatch):
    monkeypatch.setenv('ALPHAVANTAGE_API_KEY','test-secret')
    cfg=load();cfg['research']['alpha_daily_limit']=1
    store=Store(tmp_path/'s.db');g=Gateway(cfg,store)
    class Fake:
        status_code=200;headers={}
        def json(self):return {'jsonrpc':'2.0','id':1,'result':{}}
    g.session.post=lambda *a,**kw:Fake()
    g.forward({'id':1,'method':'tools/call','params':{'name':'OVERVIEW','arguments':{'symbol':'AAPL'}}})
    from datetime import datetime,timezone
    with pytest.raises(RuntimeError):store.reserve('alpha',datetime.now(timezone.utc).date().isoformat(),1)
    with pytest.raises(ValueError):g.forward({'id':2,'method':'tools/call','params':{'name':'BULK_QUERY'}})
