"""Guarded local MT5 client: read/test tools and bounded agent file handoffs.

Direct order, shell and authentication tools remain inaccessible. A dedicated
demo activation method checks acceptance and observation before EA permission.
Native terminal settings and MCP errors remain authoritative.
"""
from __future__ import annotations
import json
import configparser
from pathlib import Path
import tomllib
from urllib.parse import urlparse
import requests
from .config import ROOT,load
from . import __version__

READ_TOOLS={'get_workspace_info','get_time_information','get_trading_account_info',
            'get_marketwatch_symbols','get_chart_history','terminal_get_info',
            'get_trading_open_positions','list_open_charts','list_available_mql5_programs',
            'tester_get_status','tester_get_configuration','tester_get_report','tester_wait',
            'get_terminal_journal','get_expert_journal','get_tester_journal','read_file'}
SETUP_TOOLS={'chart_add_expert','tester_prepare_config','tester_prepare_inputs','tester_run_backtest','write_file','write_binary_file'}


class MCPError(RuntimeError):pass


def unwrap(result):
    if result.get('isError'):raise MCPError('MT5 MCP rejected the operation; inspect its local diagnostic')
    if 'structuredContent' in result:return result['structuredContent']
    blocks=result.get('content',[])
    text='\n'.join(b.get('text','') for b in blocks if b.get('type')=='text')
    try:return json.loads(text)
    except json.JSONDecodeError:return {'text':text}


class NativeMT5:
    def __init__(self,server='terminal'):
        if server not in ('terminal','metaeditor'):raise MCPError('Only configured local native servers are supported')
        config=tomllib.loads((ROOT/'.codex/config.toml').read_text(encoding='utf-8-sig'))
        entry=config.get('mcp_servers',{}).get(server,{})
        self.url=entry.get('url','');parsed=urlparse(self.url)
        if parsed.scheme!='http' or parsed.hostname not in ('127.0.0.1','localhost') or parsed.path!='/mcp':raise MCPError('Native MT5 must use the configured loopback endpoint')
        self.session=requests.Session();self.session.trust_env=False
        self.session.headers.update({**entry.get('http_headers',{}),'Content-Type':'application/json','Accept':'application/json, text/event-stream'})
        self.number=0
        initialized=self.rpc('initialize',{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'DualTrade','version':__version__}})
        self.info=initialized.get('serverInfo',{})
        self.rpc('notifications/initialized',{},notification=True)
        self.tools={t['name']:t for t in self.rpc('tools/list',{}).get('tools',[])}

    def rpc(self,method,params,notification=False):
        self.number+=1;request={'jsonrpc':'2.0','method':method,'params':params}
        if not notification:request['id']=self.number
        try:
            response=self.session.post(self.url,json=request,timeout=(3,55),allow_redirects=False)
            if response.status_code not in (200,202,204):raise MCPError('Native MCP HTTP '+str(response.status_code))
            if response.headers.get('Mcp-Session-Id'):self.session.headers['Mcp-Session-Id']=response.headers['Mcp-Session-Id']
            if notification:return {}
            if response.headers.get('Content-Type','').startswith('text/event-stream'):
                records=[json.loads(line[5:].strip()) for line in response.text.splitlines() if line.startswith('data:')]
                answer=next(x for x in records if x.get('id')==self.number)
            else:answer=response.json()
            if answer.get('error'):raise MCPError('Native MCP protocol error '+str(answer['error'].get('code')))
            return answer.get('result',{})
        except (requests.RequestException,ValueError,StopIteration):raise MCPError('Native MCP unavailable or invalid response') from None

    def call(self,name,arguments=None):
        arguments=arguments or {}
        if name not in READ_TOOLS|SETUP_TOOLS or name not in self.tools:raise MCPError('Tool is outside the demo observation/test workflow')
        schema=self.tools[name]['inputSchema']
        if set(arguments)-set(schema.get('properties',{})) or set(schema.get('required',[]))-set(arguments):raise MCPError('Arguments do not match the discovered native tool schema')
        if name in SETUP_TOOLS:self.verify_demo()
        if name=='read_file':
            workspace=self.call('get_workspace_info')['workspace']
            path=Path(arguments.get('path','')).resolve()
            profiles=Path(workspace['mql5_folder'])/'Profiles/Tester'
            common=Path(workspace['common_folder'])/'Files'
            tester_config=path.parent==profiles.resolve() and path.name.startswith('DualTrade') and path.suffix=='.ini'
            test_result=path.parent.parent==common.resolve() and path.parent.name.startswith('DualTradeTests030') and path.name=='integration.json'
            if not (tester_config or test_result):raise MCPError('Only project tester configurations and isolated test results may be read')
        if name=='write_binary_file':
            root=Path(self.call('get_workspace_info')['workspace']['mql5_folder'])
            path=Path(arguments.get('path','')).resolve()
            names={'DualTradeIntegrationTests.ex5','DualTrade.ex5'}
            if path.parent!=(root/'Experts/DualTrade').resolve() or path.name not in names:raise MCPError('Only project observation/test experts may be installed')
            if path.name=='DualTrade.ex5':self.verify_flat()
            data=arguments.get('data','')
            if data.lower()!=(ROOT/'MQL5/Experts'/path.name).read_bytes().hex() or 'offset' in arguments or arguments.get('truncate'):
                raise MCPError('Binary must exactly match the complete local compiled project expert')
        if name=='write_file':
            mqlroot=Path(self.call('get_workspace_info')['workspace']['mql5_folder'])
            from .config import bridge
            agent_root=bridge(load()).resolve()
            agent_path=Path(arguments.get('path','')).resolve()
            if agent_path==agent_root/'agent-close.txt' or agent_path.parent==agent_root/'agent-decisions':
                if agent_path.parent==agent_root/'agent-decisions' and arguments.get('overwrite') is not False:
                    raise MCPError('Native request decisions are immutable; overwrite must be false')
                from .agent_bridge import validate_publish_content
                import time
                try:validate_publish_content(load(),agent_path,arguments.get('content',''),time.time())
                except (ValueError,OSError,TypeError,KeyError):raise MCPError('Agent handoff validation failed; no decision published') from None
                if arguments.get('encoding')!='ascii':raise MCPError('Agent packets must use ASCII')
                return unwrap(self.rpc('tools/call',{'name':name,'arguments':arguments}))
            root=mqlroot/'Profiles/Tester'
            path=Path(arguments.get('path',''))
            deployed=mqlroot/'Experts/DualTrade'
            main=deployed/'DualTrade.mq5'
            sources={(deployed/'Source/Include'/p.relative_to(ROOT/'MQL5/Include')).resolve():p.read_text(encoding='utf-8-sig')
                     for p in (ROOT/'MQL5/Include').rglob('*.mqh')}
            sources[main.resolve()]=(ROOT/'MQL5/Experts/DualTrade.mq5').read_text(encoding='utf-8-sig').replace('../Include/','Source/Include/')
            if path.resolve() in sources:
                self.verify_flat()
                if arguments.get('content')!=sources[path.resolve()]:raise MCPError('Deployed source must exactly match reviewed project source')
                return unwrap(self.rpc('tools/call',{'name':name,'arguments':arguments}))
            if path.parent.resolve()!=root.resolve() or not path.name.startswith('DualTrade') or path.suffix!='.ini':raise MCPError('Only this project\'s tester configuration files may be written')
            content=arguments.get('content','')
            parser=configparser.ConfigParser(interpolation=None,strict=True)
            try:parser.read_string(content);test=parser['Tester']
            except (configparser.Error,KeyError):raise MCPError('Invalid tester configuration') from None
            if not (test.get('UseCloud')=='0' and test.get('UseRemote')=='0' and test.get('UseLocal')=='1'):raise MCPError('Only local unpaid tester agents are allowed')
            if test.get('Expert') not in ('DualTrade\\DualTradeIntegrationTests.ex5','DualTrade\\DualTradeReplay.ex5'):raise MCPError('Only reviewed test/replay experts are allowed')
            if test.get('Currency')!='USD' or float(test.get('Deposit','0'))!=1000 or test.get('Optimization')!='0':raise MCPError('Only the USD 1000 unoptimized baseline is allowed')
        if name=='chart_add_expert':
            expected=load();pairs=[p.split('=',1) for p in arguments.get('expert_parameters','').split(',')]
            if any(len(p)!=2 for p in pairs) or len({p[0] for p in pairs})!=len(pairs):raise MCPError('Explicit unique observation inputs required')
            parameters=dict(pairs)
            if parameters.pop('RequireAgentReview','true')!='true':raise MCPError('Observation EA must retain the agent review gate')
            required={'BridgeFolder':'DualTrade','ExpectedLogin':str(expected['expected_login']),'ExpectedServer':expected['expected_server'],
                      'DayMagic':str(expected['magic_day']),'SwingMagic':str(expected['magic_swing']),
                      'InvestmentMagic':str(expected['magic_investment']),'AllowDemoOrders':'false'}
            if parameters!=required:raise MCPError('EA attach must use the pinned observation inputs with orders disabled')
            root=Path(self.call('get_workspace_info')['workspace']['mql5_folder'])/'Experts/DualTrade/DualTrade.ex5'
            if Path(arguments.get('expert_path','')).resolve()!=root.resolve():raise MCPError('Only the reviewed DualTrade observation EA may be attached')
        return unwrap(self.rpc('tools/call',{'name':name,'arguments':arguments}))

    def verify_demo(self):
        expected=load();account=self.call('get_trading_account_info').get('account',{})
        if not (str(account.get('login'))==str(expected['expected_login']) and account.get('server')==expected['expected_server'] and account.get('currency')=='USD' and account.get('margin_mode')=='hedging' and str(account.get('type')).lower()=='demo'):
            raise MCPError('Pinned USD demo identity required')
        return True

    def verify_flat(self):
        self.verify_demo()
        state=self.call('get_trading_open_positions')
        if 'positions' not in state or state.get('positions') or state.get('orders'):
            raise MCPError('Observation deployment requires a verified flat demo; retain existing protection')
        return True

    def arm_agent_demo(self, style, until, *, observation_waiver=False):
        """Enable only the reviewed agent EA after genuine acceptance/observation.

        This dedicated path cannot place orders and does not relax the generic
        observation-only chart tool. Entry and exit side effects stay in the EA.
        """
        import hashlib
        import time
        from .config import bridge
        from .cli import start_demo, source_hash
        from .storage import read_json, Store
        from .calendar import now_ny
        if style not in ('day','swing'): raise MCPError('Technical agent section required')
        cfg=load();now=time.time()
        if not isinstance(until,(int,float)) or not now<until or now_ny(until).date()!=now_ny(now).date():
            raise MCPError('Agent activation is limited to the current authorized session')
        waiver = None
        extra = {}
        if observation_waiver:
            from .demo_exception import active
            waiver = active(cfg, now, style, until)
            if not waiver: raise MCPError('Explicit day-demo observation exception unavailable, changed or expired')
            extra['observation_waiver'] = True
        self.call('get_workspace_info');self.verify_flat()
        ea=read_json(bridge(cfg)/'ea-status.json',{})
        if ea.get('agent_review_required') is not True:raise MCPError('Current native agent gate must be loaded')
        checked=start_demo(cfg,Store(ROOT/'runtime/state.sqlite3'),now,style,check_only=True,check_order_permission=False,until=until,**extra)
        if not checked.get('eligible'):return {'started':False,'issues':checked['issues']}
        deployment=read_json(ROOT/'runtime/agent-deployment.json',{})
        root=Path(self.call('get_workspace_info')['workspace']['mql5_folder'])
        binary=root/'Experts/DualTrade/DualTrade.ex5'
        if (deployment.get('source_hash')!=source_hash() or not binary.exists() or
                deployment.get('binary_sha256')!=hashlib.sha256(binary.read_bytes()).hexdigest()):
            raise MCPError('Current agent deployment receipt and exact binary required')
        charts=self.call('list_open_charts')['charts']
        matches=[c for c in charts if c.get('expert',{}).get('name')=='DualTrade']
        if len(matches)!=1:raise MCPError('Exactly one owned DualTrade chart required')
        parameters={'BridgeFolder':'DualTrade','ExpectedLogin':str(cfg['expected_login']),
            'ExpectedServer':cfg['expected_server'],'DayMagic':str(cfg['magic_day']),
            'SwingMagic':str(cfg['magic_swing']),'InvestmentMagic':str(cfg['magic_investment']),
            'AllowDemoOrders':'true','RequireAgentReview':'true'}
        if waiver:
            parameters.update(DayObservationWaiverUntil=str(waiver['expires']),
                              DayObservationWaiverContext=waiver['context'],
                              DayObservationWaiverAuthorization=waiver['authorization'])
        # run_mode is still observe; enabling permission alone cannot open trades.
        unwrap(self.rpc('tools/call',{'name':'chart_add_expert','arguments':{
            'chart_id':matches[0]['chart_id'],'expert_path':str(binary),
            'expert_parameters':','.join(k+'='+v for k,v in parameters.items())}}))
        for attempt in range(5):
            status=read_json(bridge(cfg)/'ea-status.json',{})
            if (status.get('orders_enabled') and status.get('agent_review_required') and
                    (not waiver or status.get('day_observation_waiver_until') == waiver['expires'])):break
            time.sleep(1)
        if waiver and (not status.get('orders_enabled') or not status.get('agent_review_required') or
                       status.get('day_observation_waiver_until') != waiver['expires']):
            raise MCPError('Native dated observation exception inputs were not confirmed; entry control remains observe')
        # Repeat all gates after reload, including order permission. A failure
        # leaves entry control in observe while keeping protective permission.
        checked=start_demo(cfg,Store(ROOT/'runtime/state.sqlite3'),style=style,until=until,check_only=True,**extra)
        if not checked.get('eligible'):return {'started':False,'issues':checked['issues']}
        styles=checked['styles']
        # Publish through the terminal's allowed file service. Background agents
        # need no OS process visibility or unrestricted common-folder shell write.
        control={'mode':'demo','changed_at':time.time(),'styles':styles,'until':until,'agent_review_required':True}
        if waiver: control['observation_waiver'] = waiver
        for filename,content in (
            ('control.json',json.dumps(control)+'\n'),
            ('control.txt','demo\nstyles|'+'|'.join(styles)+'\nexpires|'+str(int(until))+'\n')):
            self.verify_demo()
            unwrap(self.rpc('tools/call',{'name':'write_file','arguments':{
                'path':str(bridge(cfg)/filename),'content':content,'overwrite':True,'encoding':'utf-8'}}))
        return {'started':True,'mode':'demo','styles':styles,'until':until,'agent_review_required':True,
                'observation_waiver':waiver,'observation_pass_fabricated':False}
