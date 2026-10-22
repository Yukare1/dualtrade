"""Reload this project's news collector without changing leases or trade control."""
from pathlib import Path
import subprocess
import sys
import time
import psutil

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dualtrade.storage import process_lock,atomic_write


def main():
    directory=ROOT/'runtime/official-news'
    # Exact cwd/module/action matching cannot terminate another project's feed
    # or either Codex chat. Leases, documents and review receipts stay on disk.
    for proc in psutil.process_iter(['name']):
        try:
            if proc.info['name'].lower() not in ('python.exe','pythonw.exe'):continue
            args=proc.cmdline()
            if 'dualtrade.official_news' in args and 'run' in args and Path(proc.cwd()).resolve()==ROOT:
                proc.terminate();proc.wait(timeout=5)
        except (psutil.NoSuchProcess,psutil.AccessDenied):continue
    # A competing startup wins safely; never spawn blindly over a held lock.
    try:
        with process_lock(directory/'collector.lock'):pass
    except OSError:
        print('Existing news collector retained; lock is held.')
        return
    stamp=time.time_ns()
    with (directory/f'collector-{stamp}.stdout.log').open('ab') as stdout, (directory/f'collector-{stamp}.stderr.log').open('ab') as stderr:
        process=subprocess.Popen([str(ROOT/'.venv/Scripts/pythonw.exe'),'-m','dualtrade.official_news','run'],
            cwd=ROOT,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,creationflags=subprocess.CREATE_NO_WINDOW)
    atomic_write(directory/'collector.pid',str(process.pid)+'\n')
    for _ in range(20):
        if process.poll() is not None:raise RuntimeError('News collector exited during startup')
        try:
            with process_lock(directory/'collector.lock'):pass
        except OSError:
            print('News collector restarted; single-instance lock verified. Existing leases retained.')
            return
        time.sleep(.25)
    raise RuntimeError('News collector has not acquired its single-instance lock')


if __name__=='__main__':main()
