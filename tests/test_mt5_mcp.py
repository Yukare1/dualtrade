from pathlib import Path
from types import SimpleNamespace
import pytest
from dualtrade.mt5_mcp import NativeMT5,MCPError,unwrap


def test_agent_activation_publishes_bounded_control_through_native_service(monkeypatch,tmp_path):
    import hashlib,time
    from dualtrade import cli
    from dualtrade.storage import write_json
    client,cfg,account,calls=stub(monkeypatch,tmp_path)
    monkeypatch.setattr('dualtrade.mt5_mcp.ROOT',tmp_path)
    monkeypatch.setattr(cli,'source_hash',lambda:'current-source')
    eligibility={'ok':False}
    def check(*a,**kw):
        assert kw['check_only'] is True
        return {'eligible':True,'styles':['day']} if eligibility['ok'] else {'started':False,'issues':['Full healthy observation session required']}
    monkeypatch.setattr(cli,'start_demo',check)
    binary=tmp_path/'Experts/DualTrade/DualTrade.ex5';binary.parent.mkdir(parents=True);binary.write_bytes(b'test-only-binary')
    write_json(tmp_path/'runtime/agent-deployment.json',{'source_hash':'current-source','binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest()})
    write_json(Path(cfg['bridge_directory'])/'ea-status.json',{'agent_review_required':True,'orders_enabled':True})
    client.tools['list_open_charts']={'inputSchema':{'properties':{}}}
    original=client.rpc;writes=[];attachments=[]
    def rpc(method,params):
        if params['name']=='list_open_charts':return {'structuredContent':{'charts':[{'chart_id':'1','expert':{'name':'DualTrade'}}]}}
        if params['name']=='write_file':writes.append(params['arguments']);return {'structuredContent':{'ok':True}}
        if params['name']=='chart_add_expert':attachments.append(params['arguments']);return {'structuredContent':{'ok':True}}
        return original(method,params)
    client.rpc=rpc
    until=time.time()+60
    denied=client.arm_agent_demo('day',until)
    assert not denied['started'] and not writes and not attachments
    eligibility['ok']=True
    allowed=client.arm_agent_demo('day',until)
    assert allowed['started'] and 'RequireAgentReview=true' in attachments[0]['expert_parameters']
    assert [Path(w['path']).name for w in writes]==['control.json','control.txt']
    assert writes[-1]['content']=='demo\nstyles|day\nexpires|'+str(int(until))+'\n'
    assert not (Path(cfg['bridge_directory'])/'control.txt').exists()


def test_explicit_waiver_binds_native_inputs_and_does_not_write_observation(monkeypatch,tmp_path):
    import hashlib,time
    from dualtrade import cli
    from dualtrade.demo_exception import END,AUTHORIZATION
    from dualtrade.storage import write_json
    client,cfg,account,calls=stub(monkeypatch,tmp_path)
    monkeypatch.setattr('dualtrade.mt5_mcp.ROOT',tmp_path)
    monkeypatch.setattr(cli,'source_hash',lambda:'current-source')
    now=1792680000;monkeypatch.setattr(time,'time',lambda:now)
    waiver={'authorization':AUTHORIZATION,'context':'a'*64,'expires':END}
    valid={'yes':False}
    monkeypatch.setattr('dualtrade.demo_exception.active',lambda *a:waiver if valid['yes'] else None)
    def check(*a,**kw):
        assert kw['check_only'] is True and kw['observation_waiver'] is True
        return {'eligible':True,'styles':['day']}
    monkeypatch.setattr(cli,'start_demo',check)
    binary=tmp_path/'Experts/DualTrade/DualTrade.ex5';binary.parent.mkdir(parents=True);binary.write_bytes(b'test-only')
    write_json(tmp_path/'runtime/agent-deployment.json',{'source_hash':'current-source','binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest()})
    write_json(Path(cfg['bridge_directory'])/'ea-status.json',{'agent_review_required':True,'orders_enabled':True,'day_observation_waiver_until':END,'day_observed':False})
    client.tools['list_open_charts']={'inputSchema':{'properties':{}}}
    original=client.rpc;writes=[];attachments=[]
    def rpc(method,params):
        if params['name']=='list_open_charts':return {'structuredContent':{'charts':[{'chart_id':'1','expert':{'name':'DualTrade'}}]}}
        if params['name']=='write_file':writes.append(params['arguments']);return {'structuredContent':{'ok':True}}
        if params['name']=='chart_add_expert':attachments.append(params['arguments']);return {'structuredContent':{'ok':True}}
        return original(method,params)
    client.rpc=rpc
    with pytest.raises(MCPError,match='exception unavailable'):
        client.arm_agent_demo('day',END,observation_waiver=True)
    assert not writes and not attachments
    valid['yes']=True
    result=client.arm_agent_demo('day',END,observation_waiver=True)
    assert result['started'] and result['observation_pass_fabricated'] is False
    assert 'DayObservationWaiverUntil='+str(END) in attachments[0]['expert_parameters']
    assert 'DayObservationWaiverContext='+'a'*64 in attachments[0]['expert_parameters']
    assert 'RequireAgentReview=true' in attachments[0]['expert_parameters']
    assert [Path(w['path']).name for w in writes]==['control.json','control.txt']
    assert writes[-1]['content']=='demo\nstyles|day\nexpires|'+str(END)+'\n'


def stub(monkeypatch,tmp_path):
    cfg={'expected_login':123,'expected_server':'Demo','magic_day':1,'magic_swing':2,'magic_investment':3,
         'bridge_directory':str(tmp_path/'Common/Files/DualTrade')}
    monkeypatch.setattr('dualtrade.mt5_mcp.load',lambda:cfg)
    client=object.__new__(NativeMT5)
    schemas={'get_trading_account_info':{},'get_workspace_info':{},'get_trading_open_positions':{},
             'chart_add_expert':{'chart_id':{},'expert_path':{},'expert_parameters':{}},
             'write_file':{'path':{},'content':{},'encoding':{},'overwrite':{}},
             'write_binary_file':{'path':{},'data':{},'overwrite':{},'offset':{},'truncate':{}},
             'read_file':{'path':{},'length':{},'offset':{}}}
    client.tools={n:{'inputSchema':{'properties':p}} for n,p in schemas.items()}
    account={'login':'123','server':'Demo','currency':'USD','margin_mode':'hedging','type':'demo'}
    calls=[]
    def rpc(method,params):
        name=params['name'];calls.append(name)
        data={'account':account} if name=='get_trading_account_info' else {'workspace':{'mql5_folder':str(tmp_path),'common_folder':str(tmp_path/'Common')}} if name=='get_workspace_info' else {'positions':[],'orders':[]} if name=='get_trading_open_positions' else {'ok':True}
        return {'structuredContent':data}
    client.rpc=rpc
    return client,cfg,account,calls


@pytest.mark.parametrize('tool',['trade_send_market_order','trade_close_single_position','send_web_request','delete_file'])
def test_client_cannot_execute_orders_shell_network_or_delete(monkeypatch,tmp_path,tool):
    client,_,_,calls=stub(monkeypatch,tmp_path)
    with pytest.raises(MCPError,match='outside'):client.call(tool,{})
    assert not calls


@pytest.mark.parametrize('field,value',[('type','real'),('login','999'),('server','Other'),('currency','EUR'),('margin_mode','netting')])
def test_account_switch_blocks_native_mutations(monkeypatch,tmp_path,field,value):
    client,_,account,calls=stub(monkeypatch,tmp_path);account[field]=value
    with pytest.raises(MCPError,match='Pinned USD demo'):client.call('chart_add_expert',{})
    assert 'chart_add_expert' not in calls


def test_observation_reload_rejects_duplicate_or_enabled_trade_input(monkeypatch,tmp_path):
    client,_,_,calls=stub(monkeypatch,tmp_path)
    p={'chart_id':'1','expert_path':str(tmp_path/'Experts/DualTrade/DualTrade.ex5'),
       'expert_parameters':'BridgeFolder=DualTrade,ExpectedLogin=123,ExpectedServer=Demo,DayMagic=1,SwingMagic=2,InvestmentMagic=3,AllowDemoOrders=false'}
    assert client.call('chart_add_expert',p)['ok']
    calls.clear();p['expert_parameters']+=',AllowDemoOrders=true'
    with pytest.raises(MCPError,match='unique'):client.call('chart_add_expert',p)
    assert 'chart_add_expert' not in calls


def test_test_configuration_must_be_local_unpaid_and_cannot_write_other_files(monkeypatch,tmp_path):
    client,_,_,calls=stub(monkeypatch,tmp_path)
    text='[Tester]\nExpert=DualTrade\\DualTradeReplay.ex5\nCurrency=USD\nDeposit=1000\nOptimization=0\nUseLocal=1\nUseRemote=0\nUseCloud=0\n'
    p={'path':str(tmp_path/'Profiles/Tester/DualTradeBase.ini'),'content':text,'overwrite':True}
    assert client.call('write_file',p)['ok']
    p['content']+='UseCloud=1\n'
    with pytest.raises(MCPError,match='Invalid tester'):client.call('write_file',p)
    p['content']=text;p['path']=str(tmp_path/'Experts/Other.ini')
    with pytest.raises(MCPError,match='Only this project'):client.call('write_file',p)


def test_upstream_native_rejection_is_never_reported_as_success():
    with pytest.raises(MCPError):unwrap({'isError':True,'content':[{'type':'text','text':'permission denied'}]})


def test_observation_install_requires_flat_known_account(monkeypatch,tmp_path):
    client,_,_,_=stub(monkeypatch,tmp_path)
    assert client.verify_flat()
    monkeypatch.setattr(client,'call',lambda name,*a:{'positions':[{'symbol':'AAPL'}]} if name=='get_trading_open_positions' else {'account':{}})
    monkeypatch.setattr(client,'verify_demo',lambda:True)
    with pytest.raises(MCPError,match='flat'):client.verify_flat()
    monkeypatch.setattr(client,'call',lambda *a:{})
    with pytest.raises(MCPError,match='flat'):client.verify_flat()


def test_project_binary_install_cannot_write_other_programs_or_partial_data(monkeypatch,tmp_path):
    import dualtrade.mt5_mcp as module
    client,_,_,calls=stub(monkeypatch,tmp_path)
    monkeypatch.setattr(module,'ROOT',tmp_path)
    binary=tmp_path/'MQL5/Experts/DualTradeIntegrationTests.ex5'
    binary.parent.mkdir(parents=True);binary.write_bytes(b'EX5 reviewed test fixture')
    path=tmp_path/'Experts/DualTrade/DualTradeIntegrationTests.ex5'
    args={'path':str(path),'data':binary.read_bytes().hex(),'overwrite':True}
    assert client.call('write_binary_file',args)['ok']
    with pytest.raises(MCPError,match='exactly'):client.call('write_binary_file',{**args,'data':'00'})
    with pytest.raises(MCPError,match='exactly'):client.call('write_binary_file',{**args,'offset':0})
    with pytest.raises(MCPError,match='Only project'):client.call('write_binary_file',{**args,'path':str(tmp_path/'Other.ex5')})


def test_native_result_reads_cannot_access_credentials(monkeypatch,tmp_path):
    client,_,_,calls=stub(monkeypatch,tmp_path)
    with pytest.raises(MCPError,match='Only project'):client.call('read_file',{'path':str(tmp_path/'config/accounts.dat')})
    assert 'read_file' not in calls


def test_native_handoff_cannot_overwrite_an_entry_decision(monkeypatch,tmp_path):
    client,cfg,_,calls=stub(monkeypatch,tmp_path)
    path=Path(cfg['bridge_directory'])/'agent-decisions/123.0.0.txt'
    with pytest.raises(MCPError,match='immutable'):
        client.call('write_file',{'path':str(path),'content':'untrusted','encoding':'ascii','overwrite':True})
    assert 'write_file' not in calls
