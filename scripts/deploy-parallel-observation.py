"""Deploy the reviewed collector-only split, preserving today's demo permission.

No EA removal, permission disable, manual control write or direct order API.
The dated demo scope is restored only through the existing guarded arm method.
"""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import sys
import time

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dualtrade.config import load, bridge, config_hash
from dualtrade.cli import source_hash
from dualtrade.demo_exception import END, account_pin
from dualtrade.mt5_mcp import NativeMT5
from dualtrade.storage import read_json, write_json, fingerprint, Store, atomic_write

BACKUP = ROOT / 'build/parallel-observation-rollback'


def original_source_hash():
    digest = hashlib.sha256()
    for folder, suffix in ((ROOT/'dualtrade', '.py'), (ROOT/'MQL5', '.mq')):
        for path in sorted(folder.rglob('*')):
            if path.is_file() and (path.suffix == suffix or suffix == '.mq' and path.suffix in ('.mq5', '.mqh')):
                content = ((BACKUP/'collector.py').read_bytes()
                           if path == ROOT/'dualtrade/collector.py' else path.read_bytes())
                digest.update(str(path.relative_to(ROOT)).encode())
                digest.update(content)
    return digest.hexdigest()


def stop_collector_family(matches):
    # Windows venv pythonw.exe is a launcher with a second pythonw child.
    # Treat that verified parent/child chain as one worker, not two collectors.
    pids = {proc.pid for proc in matches}
    roots = [proc for proc in matches if proc.ppid() not in pids]
    if len(roots) > 1 or (matches and not roots):
        raise RuntimeError('Multiple independent collectors; no restart attempted')
    leaves = [proc for proc in matches if not any(other.ppid() == proc.pid for other in matches)]
    for proc in leaves + [proc for proc in matches if proc not in leaves]:
        try:
            proc.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(matches, timeout=5)
    if alive:
        raise RuntimeError('Collector did not stop; no replacement started')


def restart_collector():
    matches = []
    for proc in psutil.process_iter(['name']):
        try:
            if (proc.info['name'].lower() in ('python.exe', 'pythonw.exe') and
                    'dualtrade.cli' in proc.cmdline() and 'collect' in proc.cmdline() and
                    Path(proc.cwd()).resolve() == ROOT):
                matches.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    stop_collector_family(matches)
    started = time.time()
    with (ROOT/'runtime/collector.stdout.log').open('ab') as out, (ROOT/'runtime/collector.stderr.log').open('ab') as err:
        proc = subprocess.Popen([str(ROOT/'.venv/Scripts/pythonw.exe'), '-m', 'dualtrade.cli', 'collect'],
                                cwd=ROOT, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    atomic_write(ROOT/'runtime/collector.pid', str(proc.pid)+'\n')
    return started, proc.pid


def wait_context(directory, expected, after):
    for _ in range(40):
        now = time.time()
        ea = read_json(directory/'ea-status.json', {})
        snap = read_json(directory/'snapshot.json', {})
        if (snap.get('time', 0) >= after and snap.get('decision_context') == expected and
                ea.get('decision_context') == expected and 0 <= now-ea.get('time', 0) <= 10 and
                0 <= now-snap.get('time', 0) <= 60):
            return ea, snap
        time.sleep(1)
    raise RuntimeError('Fresh matching native and collector context not confirmed')


def unchanged_risk(cfg, baseline):
    return (cfg['risk'] == baseline['risk'] and cfg.get('shared_risk') == baseline['shared_risk'] and
            hashlib.sha256((ROOT/'config.local.json').read_bytes()).hexdigest() == baseline['config_local_sha256'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    cfg = load(); directory = bridge(cfg)
    baseline = read_json(ROOT/'runtime/reports/parallel-observation-maintenance-baseline.json', {})
    assert original_source_hash() == baseline['source_hash_before'], 'Only the reviewed collector source may differ'
    assert unchanged_risk(cfg, baseline), 'Risk/config changed'
    acceptance = read_json(ROOT/'build/acceptance.json', {})
    assert acceptance.get('passed') is True and acceptance.get('source_hash') == source_hash(), 'Current real functional acceptance required'
    old_waiver = read_json(ROOT/'runtime/demo-observation-waiver-2026-10-22.json', {})
    old_deployment = read_json(ROOT/'runtime/agent-deployment.json', {})
    assert old_waiver.get('approved') is True and old_waiver.get('expires') == END
    assert old_waiver.get('account_pin') == account_pin(cfg) and time.time() < END
    control = read_json(directory/'control.json', {})
    assert control.get('mode') == 'demo' and control.get('styles') == ['day'] and control.get('until') == END
    native = NativeMT5(); workspace = native.call('get_workspace_info')['workspace']; native.verify_flat()
    binary = Path(workspace['mql5_folder'])/'Experts/DualTrade/DualTrade.ex5'
    binary_hash = hashlib.sha256(binary.read_bytes()).hexdigest()
    assert old_deployment['binary_sha256'] == binary_hash, 'Existing reviewed native binary changed'
    ea = read_json(directory/'ea-status.json', {})
    assert ea.get('orders_enabled') is True and ea.get('agent_review_required') is True
    assert 0 <= time.time()-ea.get('time', 0) <= 10
    assert not any(ea.get(k) for k in ('halt', 'foreign_exposure', 'unreconciled', 'intent_pending'))
    expected = fingerprint({'config_hash': config_hash(cfg), 'source_hash': source_hash()})
    if not args.execute:
        print(json.dumps({'reviewed': True, 'collector_only_change': True, 'permission_stays_enabled': True,
                          'deadline': END, 'context': expected, 'rollback_available': True}))
        return
    store = Store(ROOT/'runtime/state.sqlite3')
    passes = {key: store.get(key, {}) for key in ('observation_pass', 'swing:observation_pass', 'investment:observation_pass')}
    write_json(BACKUP/'original-waiver.json', old_waiver)
    write_json(BACKUP/'original-deployment.json', old_deployment)
    for name in ('acceptance.json', 'three-books-native.json'):
        (BACKUP/('reviewed-new-'+name)).write_bytes((ROOT/'build'/name).read_bytes())
    (BACKUP/'reviewed-new-collector.py').write_bytes((ROOT/'dualtrade/collector.py').read_bytes())
    try:
        native.verify_flat()
        started, pid = restart_collector()
        wait_context(directory, expected, started)
        write_json(ROOT/'runtime/agent-deployment.json', {**old_deployment, 'source_hash': source_hash(),
                   'time': time.time(), 'collector_pid': pid, 'collector_restarted_at': started,
                   'native_binary_unchanged': True, 'orders_enabled': True,
                   'deployment_kind': 'reviewed-collector-only-parallel-observation'})
        # The user requested this specific concurrent integration. Preserve the
        # original approved exception's scope, identity and exclusive deadline.
        migrated = {**old_waiver, 'context': expected,
                    'integration_instruction': baseline['authorization_quote'],
                    'context_migration': {'from': old_waiver['context'], 'to': expected,
                                          'time': time.time(), 'reason': 'explicit user requested parallel observation and trade agents'}}
        write_json(ROOT/'runtime/demo-observation-waiver-2026-10-22.json', migrated)
        native.verify_flat()
        result = native.arm_agent_demo('day', END, observation_waiver=True)
        if result.get('started') is not True:
            raise RuntimeError('Guarded same-session restoration refused: '+json.dumps(result.get('issues', [])))
        current, snap = wait_context(directory, expected, started)
        for _ in range(10):
            current = read_json(directory/'ea-status.json', {})
            if current.get('mode') == 'demo' and current.get('day_observation_waiver_active') is True:
                break
            time.sleep(1)
        final_control = read_json(directory/'control.json', {})
        assert current.get('mode') == 'demo' and current.get('orders_enabled') is True
        assert current.get('agent_review_required') is True and current.get('day_observation_waiver_active') is True
        assert current.get('day_observation_waiver_until') == END
        assert final_control.get('styles') == ['day'] and final_control.get('until') == END
        assert unchanged_risk(load(), baseline)
        assert {key: store.get(key, {}) for key in passes} == passes, 'Real pass records changed unexpectedly'
        receipt = {'deployed': True, 'time': time.time(), 'source_hash': source_hash(), 'context': expected,
                   'collector_pid': pid, 'single_collector_writer': True, 'parallel_observation_deployed': True,
                   'mode': current['mode'], 'orders_enabled': True, 'agent_review_required': True,
                   'until': END, 'risk_config_unchanged': True, 'observation_pass_records_unchanged': True,
                   'native_binary_sha256': binary_hash, 'native_binary_unchanged': True,
                   'observation_is_complete': False, 'restoration': result}
        write_json(ROOT/'runtime/reports/parallel-observation-deployment.json', receipt)
        print(json.dumps({k: receipt[k] for k in ('deployed', 'context', 'single_collector_writer', 'mode', 'orders_enabled', 'until', 'risk_config_unchanged', 'observation_pass_records_unchanged')}))
    except Exception as exc:
        # Restore only the known collector file and genuine prior acceptance;
        # never fabricate new acceptance or observations on a failure.
        try:
            native.verify_flat()
            (ROOT/'dualtrade/collector.py').write_bytes((BACKUP/'collector.py').read_bytes())
            for name in ('acceptance.json', 'three-books-native.json'):
                (ROOT/'build'/name).write_bytes((BACKUP/name).read_bytes())
            write_json(ROOT/'runtime/demo-observation-waiver-2026-10-22.json', old_waiver)
            write_json(ROOT/'runtime/agent-deployment.json', old_deployment)
            stamp, _ = restart_collector()
            wait_context(directory, old_waiver['context'], stamp)
            restored = native.arm_agent_demo('day', END, observation_waiver=True)
            rollback = restored.get('started') is True
        except Exception as rollback_error:
            rollback = False
            write_json(ROOT/'runtime/reports/parallel-observation-rollback-error.json',
                       {'time': time.time(), 'error_type': type(rollback_error).__name__, 'native_permission_never_disabled': True})
        write_json(ROOT/'runtime/reports/parallel-observation-deployment.json',
                   {'deployed': False, 'time': time.time(), 'error_type': type(exc).__name__, 'error': str(exc),
                    'original_session_restored': rollback, 'native_permission_never_disabled': True})
        raise


if __name__ == '__main__':
    main()
