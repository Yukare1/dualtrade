"""Reload only this project's collector and observation EA; never enable orders."""
from pathlib import Path
import argparse
import subprocess
import sys
import time
import hashlib
import psutil

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dualtrade.config import load,bridge
from dualtrade.mt5_mcp import NativeMT5
from dualtrade.storage import write_json,atomic_write,read_json


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preserve-control',action='store_true',help='Keep control files byte-for-byte and require existing observation-only permission')
    args=parser.parse_args(argv)
    cfg=load();m=NativeMT5();workspace=m.call('get_workspace_info')
    directory=bridge(cfg)
    preserved={name:(directory/name).read_bytes() for name in ('control.json','control.txt')} if args.preserve_control else {}
    if args.preserve_control:
        status=read_json(directory/'ea-status.json',{})
        control=read_json(directory/'control.json',{})
        if (status.get('orders_enabled') is not False or status.get('mode')!='observe' or control.get('mode')!='observe'
                or not 0<=time.time()-status.get('time',0)<=10):
            raise RuntimeError('Preserving reload requires fresh existing observation mode with orders already disabled')
    m.verify_demo()
    positions=m.call('get_trading_open_positions')
    if positions.get('positions') or positions.get('orders'):
        raise RuntimeError('Observation reload requires a flat account; retain existing protective management')
    m.verify_flat()
    print('Pinned demo is flat; installing observation sources.',flush=True)
    root=Path(workspace['workspace']['mql5_folder'])
    # Exact-source/binary guards in NativeMT5 prevent arbitrary terminal writes.
    for source in (ROOT/'MQL5/Include').rglob('*.mqh'):
        target=root/'Experts/DualTrade/Source/Include'/source.relative_to(ROOT/'MQL5/Include')
        m.call('write_file',{'path':str(target),'content':source.read_text(encoding='utf-8-sig'),'overwrite':True,'encoding':'utf-8'})
    m.call('write_file',{'path':str(root/'Experts/DualTrade/DualTrade.mq5'),
        'content':(ROOT/'MQL5/Experts/DualTrade.mq5').read_text(encoding='utf-8-sig').replace('../Include/','Source/Include/'),
        'overwrite':True,'encoding':'utf-8'})
    # Compile in the terminal tree: MetaEditor notifies MT5's program cache.
    # Copying an EX5 alone can leave MT5 using its previously cached expert.
    compiler=Path(cfg['terminal_path']).parent/'MetaEditor64.exe'
    log=ROOT/'build/observation-deploy-compile.log'
    source=root/'Experts/DualTrade/DualTrade.mq5'
    command=f'"{compiler}" /compile:"{source}" /inc:"{root}" /log:"{log}"'
    subprocess.run(command,check=False,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW)
    if '0 errors, 0 warnings' not in log.read_text(encoding='utf-16',errors='replace'):
        raise RuntimeError('Observation compilation requires zero errors and warnings; see build/observation-deploy-compile.log')
    print('Observation expert compiled; restarting only the project collector.',flush=True)
    for proc in psutil.process_iter(['pid','name']):
        try:
            if proc.info['name'].lower() not in ('python.exe','pythonw.exe'):continue
            process_args=proc.cmdline()
            if ('dualtrade.cli' in process_args and 'collect' in process_args and Path(proc.cwd()).resolve()==ROOT
                    and proc.info['name'].lower() in ('python.exe','pythonw.exe')):
                proc.terminate()
                try:proc.wait(timeout=5)
                except psutil.TimeoutExpired:raise RuntimeError('Collector did not stop; no replacement started')
        except (psutil.NoSuchProcess,psutil.AccessDenied):continue
    if not args.preserve_control:
        write_json(directory/'control.json',{'mode':'observe','changed_at':time.time(),'styles':['day','swing','investment']})
        atomic_write(directory/'control.txt','observe\n')
    elif any((directory/name).read_bytes()!=content for name,content in preserved.items()):
        raise RuntimeError('Concurrent control change detected; no EA reload performed')
    charts=m.call('list_open_charts')['charts']
    chart=next(c for c in charts if c.get('expert',{}).get('name')=='DualTrade')
    params={'BridgeFolder':'DualTrade','ExpectedLogin':str(cfg['expected_login']),'ExpectedServer':cfg['expected_server'],
            'DayMagic':str(cfg['magic_day']),'SwingMagic':str(cfg['magic_swing']),
            'InvestmentMagic':str(cfg['magic_investment']),'AllowDemoOrders':'false','RequireAgentReview':'true'}
    m.call('chart_add_expert',{'chart_id':chart['chart_id'],'expert_path':str(root/'Experts/DualTrade/DualTrade.ex5'),
        'expert_parameters':','.join(k+'='+v for k,v in params.items())})
    with (ROOT/'runtime/collector.stdout.log').open('ab') as stdout, (ROOT/'runtime/collector.stderr.log').open('ab') as stderr:
        process=subprocess.Popen([str(ROOT/'.venv/Scripts/pythonw.exe'),'-m','dualtrade.cli','collect'],
            cwd=ROOT,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,creationflags=subprocess.CREATE_NO_WINDOW)
    atomic_write(ROOT/'runtime/collector.pid',str(process.pid)+'\n')
    from dualtrade.cli import source_hash
    write_json(ROOT/'runtime/agent-deployment.json',{'source_hash':source_hash(),
        'binary_sha256':hashlib.sha256((root/'Experts/DualTrade/DualTrade.ex5').read_bytes()).hexdigest(),
        'time':time.time(),'orders_enabled':False,'agent_review_required':True})
    if args.preserve_control and any((directory/name).read_bytes()!=content for name,content in preserved.items()):
        raise RuntimeError('Control changed concurrently during reload; inspect current native state')
    print('Observation EA reloaded with orders disabled; project collector restarted.',flush=True)


if __name__=='__main__':main()
