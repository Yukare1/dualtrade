"""Reproduce isolated native order-flow acceptance; never enables chart trading."""
from pathlib import Path
import hashlib
import json
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dualtrade.mt5_mcp import NativeMT5, MCPError
from dualtrade.cli import source_hash
from dualtrade.storage import write_json
from dualtrade import __version__


def main():
    source=source_hash()
    binary=ROOT/'MQL5/Experts/DualTradeIntegrationTests.ex5'
    latest=max(p.stat().st_mtime for p in (ROOT/'MQL5').rglob('*') if p.suffix in ('.mq5','.mqh'))
    if not binary.exists() or binary.stat().st_mtime<latest:
        raise RuntimeError('Compile the current native integration expert first')
    m=NativeMT5();workspace=m.call('get_workspace_info')['workspace'];m.verify_demo()
    root=Path(workspace['mql5_folder'])
    expert=root/'Experts/DualTrade/DualTradeIntegrationTests.ex5'
    m.call('write_binary_file',{'path':str(expert),'data':binary.read_bytes().hex(),'overwrite':True})
    tag='DualTrade030'+str(time.time_ns())
    folder='DualTradeTests030'+str(time.time_ns())
    config=root/'Profiles/Tester'/(tag+'.ini');inputs=root/'Profiles/Tester'/(tag+'.set')
    m.call('tester_prepare_config',{'mql5_program_path':str(expert),'output_path':str(config),
        'deposit':1000.,'deposit_currency':'USD','execution_delay':0,'leverage':20,
        'model':'m1 ohlc','optimization':False,'forward_mode':0,'symbol':'AAPL.NAS',
        'timeframe':'M15','from_date':'2026-10-20T00:00:00','to_date':'2026-10-22T00:00:00'})
    content=m.call('read_file',{'path':str(config)})['text']
    m.call('write_file',{'path':str(config),'content':content+'\nUseLocal=1\nUseRemote=0\nUseCloud=0\n','overwrite':True,'encoding':'utf-8'})
    m.call('tester_prepare_inputs',{'mql5_program_path':str(expert),'output_path':str(inputs),
        'parameters':['BridgeFolder='+folder,'AllowDemoOrders=false']})
    run=m.call('tester_run_backtest',{'config_path':str(config),'inputs_path':str(inputs),'wait':True,'timeout_sec':45})
    if run.get('timed_out'):
        print(json.dumps({'pending':True,'run':run}));return 2
    result_path=Path(workspace['common_folder'])/'Files'/folder/'integration.json'
    result=m.call('read_file',{'path':str(result_path)})
    if 'text' in result:result=json.loads(result['text'])
    report=m.call('tester_get_report',{'run_id':run['run_id'],'file_format':'json'})
    result.update(ea_version=__version__,source_hash=source,binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        time=time.time(),run=run,report=report,model='one-minute OHLC',deposit=1000,
        scope='Native simulated automatic entries/exits and controlled faults, not strategy profitability or physical outages')
    result['passed']=bool(result.get('passed') and source==source_hash() and result.get('checks',0)>=75)
    write_json(ROOT/'build/three-books-native.json',result)
    print(json.dumps({'passed':result['passed'],'checks':result.get('checks'),'deals':report.get('deals'),
                      'run_id':run['run_id'],'evidence':'build/three-books-native.json'}))
    return 0 if result['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
