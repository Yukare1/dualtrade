"""Restart only this project's research workers while demo orders are disabled."""
from pathlib import Path
import json
import subprocess
import sys
import time

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dualtrade.config import load, bridge
from dualtrade.storage import read_json, atomic_write


def main():
    cfg = load()
    directory = bridge(cfg)
    control = read_json(directory/'control.json', {})
    ea = read_json(directory/'ea-status.json', {})
    if (control.get('mode') not in ('observe', 'stop') or ea.get('orders_enabled') is not False or
            ea.get('demo') is not True or not 0 <= time.time()-ea.get('time', 0) <= 10):
        raise RuntimeError('Fresh orders-disabled demo observation required; no workers changed')
    jobs = [('dualtrade.cli', 'collect', ROOT/'runtime'),
            ('dualtrade.official_news', 'run', ROOT/'runtime/official-news')]
    stopped, started = [], []
    for module, action, output in jobs:
        targets = []
        for p in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                args = p.info.get('cmdline') or []
                if ((p.info.get('name') or '').lower() in ('python.exe', 'pythonw.exe') and
                        module in args and action in args and Path(p.cwd()).resolve() == ROOT and
                        Path(p.exe()).resolve().parent == (ROOT/'.venv/Scripts').resolve()):
                    targets.append(p)
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
        for p in targets:
            p.terminate()
        gone, alive = psutil.wait_procs(targets, timeout=5)
        if alive:
            raise RuntimeError('Project worker still running; no duplicate launched')
        stopped.extend(p.pid for p in gone)
        output.mkdir(parents=True, exist_ok=True)
        stamp = time.time_ns()
        with (output/f'ai-upgrade-{stamp}.stdout.log').open('ab') as stdout, (output/f'ai-upgrade-{stamp}.stderr.log').open('ab') as stderr:
            p = subprocess.Popen([str(ROOT/'.venv/Scripts/pythonw.exe'), '-m', module, action],
                cwd=ROOT, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                creationflags=subprocess.CREATE_NO_WINDOW)
        atomic_write(output/'collector.pid', str(p.pid)+'\n')
        started.append({'module': module, 'pid': p.pid})
    print(json.dumps({'stopped': stopped, 'started': started, 'orders_enabled': False}))


if __name__ == '__main__':
    main()
