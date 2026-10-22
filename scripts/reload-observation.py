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
from dualtrade.config import load,bridge,config_hash
from dualtrade.cli import source_hash
from dualtrade.mt5_mcp import NativeMT5
from dualtrade.storage import write_json,atomic_write,read_json,fingerprint


def collector_processes():
    matches=[]
    for proc in psutil.process_iter(['name']):
        try:
            if ((proc.info.get('name') or '').lower() in ('python.exe','pythonw.exe') and
                    'dualtrade.cli' in proc.cmdline() and 'collect' in proc.cmdline() and
                    Path(proc.cwd()).resolve()==ROOT.resolve()):
                matches.append(proc)
        except psutil.NoSuchProcess:
            continue
    return matches


def single_collector(matches,pid=None):
    parents={proc.pid:proc.ppid() for proc in matches}
    roots=[p for p,parent in parents.items() if parent not in parents]
    return len(roots)==1 and (pid is None or pid in parents)


def stop_collector_family(matches):
    if matches and not single_collector(matches):
        raise RuntimeError('Multiple independent collectors; no restart attempted')
    leaves=[proc for proc in matches if not any(other.ppid()==proc.pid for other in matches)]
    for proc in leaves+[proc for proc in matches if proc not in leaves]:
        try:proc.terminate()
        except psutil.NoSuchProcess:pass
    _,alive=psutil.wait_procs(matches,timeout=5)
    if alive:raise RuntimeError('Collector did not stop; no replacement started')


def deployment_precheck(cfg,directory,now,allow_active_cutover=False):
    source=source_hash()
    acceptance=read_json(ROOT/'build/acceptance.json',{})
    if acceptance.get('passed') is not True or acceptance.get('source_hash')!=source:
        raise RuntimeError('Current-source functional acceptance required before observation deployment')
    control=read_json(directory/'control.json',{})
    until=control.get('until')
    if not allow_active_cutover and control.get('mode')=='demo' and (type(until) not in (int,float) or not until<=now):
        raise RuntimeError('Active demo entry window; observation reload must wait for expiry or an explicit stop')
    return source,fingerprint({'config_hash':config_hash(cfg),'source_hash':source})


def wait_observation_context(directory,expected,after,pid,*,clock=time.time,sleep=time.sleep):
    for _ in range(40):
        now=clock();ea=read_json(directory/'ea-status.json',{});snap=read_json(directory/'snapshot.json',{})
        if (ea.get('time',0)>=after and snap.get('time',0)>=after and
                0<=now-ea.get('time',0)<=10 and 0<=now-snap.get('time',0)<=60 and
                ea.get('decision_context')==expected and snap.get('decision_context')==expected and
                ea.get('demo') is True and ea.get('mode')=='observe' and
                ea.get('orders_enabled') is False and ea.get('agent_review_required') is True and
                single_collector(collector_processes(),pid)):
            return ea,snap
        sleep(1)
    raise RuntimeError('Fresh observation context, disabled orders and single collector not confirmed; no deployment receipt issued')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    modes=parser.add_mutually_exclusive_group()
    modes.add_argument('--preserve-control',action='store_true',help='Keep control files byte-for-byte and require existing observation-only permission')
    modes.add_argument('--maintenance-stop',action='store_true',help='Explicit maintenance cutover: block new entries on a verified-flat demo before installing reviewed fixes; never rearm')
    args=parser.parse_args(argv)
    cfg=load()
    directory=bridge(cfg)
    source_before,context=deployment_precheck(cfg,directory,time.time(),args.maintenance_stop)
    workers=collector_processes()
    if workers and not single_collector(workers):
        raise RuntimeError('Multiple independent collectors; deployment not started')
    m=NativeMT5();workspace=m.call('get_workspace_info')
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
    if args.maintenance_stop:
        # Protection stays enabled on the existing EA. If a fill raced the first
        # flat check, the second check stops deployment and retains that manager.
        write_json(directory/'control.json',{'mode':'observe','changed_at':time.time(),'styles':['day','swing','investment'],'reason':'reviewed_maintenance'})
        atomic_write(directory/'control.txt','observe\n')
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
    stop_collector_family(collector_processes())
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
    started=time.time()
    with (ROOT/'runtime/collector.stdout.log').open('ab') as stdout, (ROOT/'runtime/collector.stderr.log').open('ab') as stderr:
        process=subprocess.Popen([str(ROOT/'.venv/Scripts/pythonw.exe'),'-m','dualtrade.cli','collect'],
            cwd=ROOT,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,creationflags=subprocess.CREATE_NO_WINDOW)
    atomic_write(ROOT/'runtime/collector.pid',str(process.pid)+'\n')
    wait_observation_context(directory,context,started,process.pid)
    if source_hash()!=source_before or fingerprint({'config_hash':config_hash(load()),'source_hash':source_hash()})!=context:
        raise RuntimeError('Source/config changed during deployment; no receipt issued')
    if read_json(directory/'control.json',{}).get('mode')!='observe':
        raise RuntimeError('Control changed during deployment; no receipt issued')
    m.verify_flat()
    if args.preserve_control and any((directory/name).read_bytes()!=content for name,content in preserved.items()):
        raise RuntimeError('Control changed concurrently during reload; inspect current native state')
    write_json(ROOT/'runtime/agent-deployment.json',{'deployed':True,'source_hash':source_before,'context':context,
        'binary_sha256':hashlib.sha256((root/'Experts/DualTrade/DualTrade.ex5').read_bytes()).hexdigest(),
        'time':time.time(),'orders_enabled':False,'agent_review_required':True,
        'collector_pid':process.pid,'single_collector_writer':True,'deployment_kind':'verified-observation-reload',
        'observation_pass_created':False,'waiver_migrated':False,'entries_armed':False})
    print('Observation EA reloaded with orders disabled; project collector restarted.',flush=True)


if __name__=='__main__':main()
