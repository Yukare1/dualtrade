from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import time
from datetime import datetime, timezone
from .config import ROOT, load, bridge, config_hash
from .storage import Store, read_json, write_json, atomic_write, process_lock, fingerprint
from . import __version__


def preflight(cfg):
    from .broker import ReadOnlyMT5, BrokerError
    issues = []
    if not cfg.get("expected_login"): issues.append("Configure expected_login after inspecting the intended demo account")
    for key in ("ALPHAVANTAGE_API_KEY", "MARKETAUX_API_TOKEN", "SEC_USER_AGENT"):
        if not os.environ.get(key): issues.append(key + " missing")
    from .ai_news import read_state
    if not read_state(time.time())['healthy']: issues.append('Official document agent store required')
    from .council import load_council
    try:load_council(cfg)
    except Exception as exc:issues.append('Council configuration: '+type(exc).__name__)
    details = {}
    try:
        details = ReadOnlyMT5(cfg).inspect()
        if not details["account"]["hedging"]: issues.append("Hedging demo account required for ticket-level ownership")
        if not any(m["verified"] for m in details["mappings"].values()): issues.append("No verified tradable contract profiles")
        issues.extend(i for i in details["issues"] if i.startswith(("FOREIGN_", "STARTING_", "ACCOUNT_")))
    except Exception as e:
        issues.append(str(e) if isinstance(e, BrokerError) else type(e).__name__)
    result = {"time": time.time(), "ready": not issues, "issues": issues, "broker": details, "config_hash": config_hash(cfg)}
    write_json(ROOT / "runtime/preflight.json", result)
    return result


def start_demo(cfg, store, now=None, style='all', *, check_only=False, check_order_permission=True, until=None, observation_waiver=False):
    now = now or time.time()
    directory = bridge(cfg)
    if style not in ('day', 'swing', 'investment', 'all'):
        raise ValueError('Unknown strategy section')
    styles = ['day', 'swing', 'investment'] if style == 'all' else [style]
    observation = store.get("observation_pass", {})
    snap = read_json(directory / "snapshot.json", {})
    ea = read_json(directory / "ea-status.json", {})
    functional = read_json(ROOT / "build/acceptance.json", {})
    issues = []
    from .decision_revision import observation_context
    observation_key = observation_context(cfg)
    execution_context = fingerprint({"config_hash":config_hash(cfg),"source_hash":source_hash()})
    waiver = None
    if observation_waiver:
        from .demo_exception import active
        waiver = active(cfg, now, style, until)
        if not waiver: issues.append('Explicit day-demo observation exception unavailable, changed or expired')
        if not check_only: issues.append('Observation exception requires the guarded agent activation path')
    for selected in styles:
        observed = observation if selected == 'day' else store.get(selected+':observation_pass', {})
        if observed.get('config_hash') != observation_key and not (selected == 'day' and waiver): issues.append(selected+': Full healthy observation session required')
    if not functional.get("passed") or functional.get("source_hash") != source_hash(): issues.append("Current-source functional acceptance required")
    if snap.get('decision_context') != execution_context or ea.get('decision_context') != execution_context:
        issues.append('Current-source native and research context required')
    if snap.get('observation_context') != observation_key:
        issues.append('Current decision mechanism research context required')
    if not 0 <= now - snap.get("time", 0) <= 60: issues.append("Research snapshot unavailable or stale")
    for selected in styles:
        if not snap.get('ready' if selected == 'day' else selected+'_ready'): issues.append(selected+': Research blocked')
    if not ea.get("demo") or not 0 <= now - ea.get("time", 0) <= 10: issues.append("Healthy pinned-demo EA heartbeat required")
    if check_order_permission and not ea.get("orders_enabled"): issues.append("EA AllowDemoOrders must be enabled after preflight")
    if ea.get('agent_review_required'):
        if 'investment' in styles: issues.append('Fundamental entry reviews are not supported by the technical agent')
        if (not isinstance(until,(int,float)) or isinstance(until,bool) or not now < until <= now+86400):
            issues.append('Explicit bounded agent session expiry required')
    if ea.get("version")!=__version__:issues.append("Current EA version must be loaded in MT5")
    if any(ea.get(k) for k in ("halt", "hard_halt", "week_halt", "foreign_exposure", "unreconciled", "intent_pending")): issues.append("EA risk/reconciliation gate active")
    if issues: return {"started": False, "issues": issues}
    if check_only: return {'eligible': True, 'styles': styles}
    write_json(directory / "control.json", {"mode": "demo", "changed_at": now, 'styles': styles})
    atomic_write(directory / "control.txt", "demo\nstyles|"+'|'.join(styles)+'\n'+('expires|'+str(int(until))+'\n' if ea.get('agent_review_required') else ''))
    return {"started": True, "mode": "demo", 'styles': styles}


def source_hash():
    import hashlib
    digest = hashlib.sha256()
    for folder, suffix in ((ROOT/"dualtrade", ".py"), (ROOT/"MQL5", ".mq")):
        for p in sorted(folder.rglob("*")):
            if p.is_file() and (p.suffix == suffix or suffix == ".mq" and p.suffix in (".mq5", ".mqh")):
                digest.update(str(p.relative_to(ROOT)).encode()); digest.update(p.read_bytes())
    return digest.hexdigest()


def report(cfg, store):
    from .shadow import ShadowLedger
    from .reporting import broker_report
    try: actual=broker_report(cfg)
    except Exception as exc: actual={"unavailable":type(exc).__name__}
    result = {"generated_at": time.time(), "shadow": ShadowLedger(store.get("shadow")).summary(),
              'investment': read_json(ROOT/'runtime/reports/investment.json', {}),
              'investment_shadow': store.get('investment:shadow', {}),
              "actual":actual,
              "snapshot": read_json(bridge(cfg)/"snapshot.json", {}), "ea": read_json(bridge(cfg)/"ea-status.json", {}),
              "budgets": store.budgets(), "limitations": ["Shadow estimates require recorded bid/ask observations; they are not actual fills", "No historical sentiment is invented", "Broker tester evidence is required before profitability claims"]}
    write_json(ROOT / "runtime/reports/latest.json", result)
    return result


def main(argv=None):
    os.chdir(ROOT)
    from .credentials import load_local_credentials
    load_local_credentials()
    parser = argparse.ArgumentParser(description="Demo-only MT5 research and operation controls")
    parser.add_argument("--config")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("preflight", "observe", "stop", "status", "report", "news-report", 'investment-report', 'swing-report'):
        sub.add_parser(command)
    start = sub.add_parser('start-demo'); start.add_argument('--style', choices=('day', 'swing', 'investment', 'all'), default='all')
    event_import = sub.add_parser('material-import'); event_import.add_argument('file')
    collect = sub.add_parser("collect"); collect.add_argument("--once", action="store_true")
    validation = sub.add_parser("validate-sentiment"); validation.add_argument("labels")
    credentials = sub.add_parser("credentials"); credentials.add_argument("--verify",action="store_true")
    council = sub.add_parser('council');council.add_argument('action',choices=('refresh','status','report'))
    council_import=sub.add_parser('council-import');council_import.add_argument('file')
    args = parser.parse_args(argv)
    cfg = load(args.config); directory = bridge(cfg); store = Store(ROOT / "runtime/state.sqlite3")
    if args.command=='council-import':
        from .council import import_review
        result=import_review(args.file,cfg,store)
    elif args.command == 'council':
        from .council import refresh as council_refresh,report as council_report,snapshot as council_snapshot
        if args.action=='refresh':
            source=council_refresh(cfg,store)
            result=council_report(cfg,store)
        elif args.action=='report':result=council_report(cfg,store)
        else:
            current=council_snapshot(cfg,list(cfg['symbols']),store)
            result={k:v for k,v in current.items() if k!='rows'}
    elif args.command == "credentials":
        from .credentials import status
        result=status(cfg,args.verify)
    elif args.command == "preflight": result = preflight(cfg)
    elif args.command in ("observe", "stop"):
        atomic_write(directory / "control.txt", args.command + "\n")
        write_json(directory / "control.json", {"mode": args.command, "changed_at": time.time()})
        result = {"mode": args.command, "note": "Run collect alongside the EA; stop disables new entries, not protective management"}
    elif args.command == "start-demo": result = start_demo(cfg, store, style=args.style)
    elif args.command == 'material-import':
        from .material_news import verify_review
        from .providers import Providers
        from .investment import options
        review = read_json(Path(args.file), {})
        result = verify_review(review, cfg, Providers(cfg, store), time.time())
        from .broker import ReadOnlyMT5
        broker = ReadOnlyMT5(cfg); inspected = broker.inspect(); mapping=inspected['mappings'].get(result['ticker'])
        if mapping and mapping['verified']:
            offset=mapping['costs']['broker_utc_offset_seconds']
            bars=broker.mt.copy_rates_range(mapping['symbol'], broker.mt.TIMEFRAME_M15,
                datetime.fromtimestamp(result['published_at']+offset-7*86400,timezone.utc),
                datetime.fromtimestamp(result['published_at']+offset,timezone.utc))
            if bars is not None:
                completed=[r for r in bars if r['time']+900<=result['published_at']+offset]
                if completed: result['reference_price']=float(completed[-1]['close'])
        path=ROOT/options(cfg)['events_file']; data=read_json(path, {'events': []})
        prior=next((e for e in data['events'] if e['event_id']==result['event_id']),None)
        if prior: result['first_observed']=prior['first_observed']
        data['events']=[e for e in data['events'] if e['event_id']!=result['event_id']]+[result]
        write_json(path,data);store.audit('MATERIAL_EVENT_IMPORTED', {'event_id':result['event_id'],'source':result['source']})
        result={'imported':True,'event_id':result['event_id'],'reference_available':bool(result.get('reference_price'))}
    elif args.command == 'investment-report': result=read_json(ROOT/'runtime/reports/investment.json', {})
    elif args.command == 'swing-report': result=read_json(directory/'snapshot.json', {}).get('swing_report', [])
    elif args.command == "status":
        result = {"control": read_json(directory/"control.json", {"mode":"stop"}), "research": read_json(directory/"snapshot.json", {}), "ea": read_json(directory/"ea-status.json", {}), "observation": store.get("observation_pass", {}),
                  'section_observations':{s:store.get(s+':observation_pass',{}) for s in ('swing','investment')},'shared_risk':cfg['shared_risk']}
    elif args.command == "report": result = report(cfg, store)
    elif args.command == 'news-report':
        from .news_guard import news_report
        result = news_report(store.get('evidence:news', []), time.time())
    elif args.command == "validate-sentiment":
        from .providers import Providers
        from .validation import validate_sentiment
        result=validate_sentiment(cfg,Providers(cfg,store),args.labels)
    else:
        from .collector import Collector
        with process_lock(ROOT/"runtime/collector.lock"):
            collector = Collector(cfg, store=store)
            while True:
                try:
                    snap = collector.cycle()
                    if args.once:
                        result = {"ready": snap["ready"], "issues": snap["issues"], "pool": snap.get("pool", [])}; break
                except KeyboardInterrupt: return 0
                except Exception as exc:
                    store.audit("COLLECTOR_ERROR", {"type": type(exc).__name__})
                    if args.once: raise
                time.sleep(5)
    # Do not echo account identifiers or raw broker inventories to console.
    if args.command == "preflight": print(json.dumps({"ready": result["ready"], "issues": result["issues"], "details": "runtime/preflight.json"}, indent=2))
    elif args.command in ("status", "report"):
        safe = {"mode": result.get("control", {}).get("mode"), "ea": result.get("ea", {}), "issues": result.get("research", result.get("snapshot", {})).get("issues", []), 'council':result.get('research',result.get('snapshot',{})).get('council'), "shadow": result.get("shadow")}
        safe['news_credibility'] = result.get('research', result.get('snapshot', {})).get('news_credibility')
        safe['investment_ready'] = result.get('research', result.get('snapshot', {})).get('investment_ready')
        safe['investment'] = result.get('investment', result.get('research', {}).get('investment_report'))
        safe['ai_news'] = result.get('research', result.get('snapshot', {})).get('ai_news')
        print(json.dumps(safe, indent=2, default=str))
    else: print(json.dumps(result, indent=2, default=str))
    return 2 if result.get("started") is False or result.get("ready") is False else 0


if __name__ == "__main__": raise SystemExit(main())
