from pathlib import Path
from types import SimpleNamespace as N
import pytest
from dualtrade.broker import ReadOnlyMT5,BrokerError
from dualtrade.config import load
from dualtrade.shadow import ShadowLedger,ShadowPosition
from dualtrade.validation import validate_sentiment


@pytest.mark.parametrize('field,value',[('trade_mode',2),('server','LIVE'),('currency','EUR'),('login',999)])
def test_readonly_broker_rejects_wrong_identity(monkeypatch,field,value):
    cfg=load();cfg['expected_login']=123
    account=dict(login=123,trade_mode=0,server=cfg['expected_server'],currency='USD');account[field]=value
    mt=N(ACCOUNT_TRADE_MODE_DEMO=0,initialize=lambda *a,**k:True,account_info=lambda:N(**account),shutdown=lambda:None)
    broker=object.__new__(ReadOnlyMT5);broker.cfg=cfg;broker.mt=mt
    monkeypatch.setattr('dualtrade.broker.psutil.process_iter',lambda *a:[N(info={'exe':cfg['terminal_path']})])
    with pytest.raises(BrokerError):broker.connect()


def test_shadow_min_lot_and_daily_capacity():
    ledger=ShadowLedger();p=ShadowPosition('AAPL','day','EMA',1,10000,100,99,102,1,1,.04,1,20000)
    spec={'minimum':1,'maximum':100,'step':1}
    ledger.offer(p,True,True,spec,10000,{},45,20)
    assert all(not b['positions'] for b in ledger.state.values())
    ledger.offer(p,True,True,spec,10000,{},2,20)
    assert all(b['positions']['day']['quantity']==20 for b in ledger.state.values())
    ledger.mark_equity({'AAPL':(80,80.1)})
    ledger.mark('AAPL',80,80.1,11000)
    assert all(b['halted'] and not b['positions'] for b in ledger.state.values())


def test_unlabelled_sentiment_never_passes(tmp_path):
    cfg=load();cfg['research']['sentiment_validation_file']=str(tmp_path/'validation.json')
    labels=tmp_path/'labels.json';labels.write_text('[]')
    assert not validate_sentiment(cfg,object(),labels)['passed']
