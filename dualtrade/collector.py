from __future__ import annotations
from datetime import datetime, date, timezone, timedelta
from pathlib import Path
import time
import math
from . import calendar as cal
from .broker import ReadOnlyMT5, BrokerError
from .config import ROOT, bridge, config_hash
from . import __version__
from .fundamentals import analyse
from .policy import sentiment_veto
from .news_guard import assess_news, news_summary, MAX_AGE as NEWS_RETENTION
from .providers import Providers
from .storage import Store, atomic_write, read_json, write_json, fingerprint


def clean(value):
    text = str(value)
    if any(c in text for c in "|\r\n"): raise ValueError("Invalid bridge field")
    return text


def encode_snapshot(snapshot):
    r = snapshot["risk"]
    rows = [["HEADER", 4, int(snapshot["time"]), snapshot["mode"], int(snapshot["ready"]),
             snapshot["login"], snapshot["server"], "USD", int(snapshot["observation_ready"]),
             r["target"], r["absolute_ceiling"], r["aggregate"], r["daily_loss"], r["free_margin_fraction"],
             r["daily_entries"], r["cooldown_seconds"]]]
    rows.append(['MODES', int(snapshot.get('investment_ready', False)), int(snapshot.get('investment_observation_ready', False)),
                 int('day' in snapshot.get('enabled_styles', ['day', 'swing', 'investment'])), int('investment' in snapshot.get('enabled_styles', ['day', 'swing', 'investment'])),
                 int(snapshot.get('swing_ready', False)), int(snapshot.get('swing_observation_ready', False)),
                 int('swing' in snapshot.get('enabled_styles', ['day', 'swing', 'investment']))])
    if snapshot.get('decision_context'):
        rows.append(['CONTEXT', snapshot['decision_context']])
    from .risk import options as risk_options, FIELDS
    shared = risk_options({'shared_risk': snapshot.get('shared_risk', {})})
    rows.append(['RISK']+[shared[k] for k in FIELDS])
    for c in snapshot["contracts"]:
        rows.append(["CONTRACT", c["ticker"], c["symbol"], int(c["verified"]),
            c["commission"], c["slippage"], c["margin_rate"], c["finance_long"], c["finance_short"], c["server_offset"],
            c["open"], c["close"], int(c["pool"]), int(c["entry_allowed"]), int(c["veto_buy"]), int(c["veto_sell"]),
            c["earnings_close"], c["earnings_end"], c["sec_close_until"], c["previous_high"], c["previous_low"], "USD", 1])
        council=c.get('council',{})
        rows.append(['COUNCIL',c['symbol'],int(council.get('ready',False)),int(council.get('buy_allowed',False)),int(council.get('sell_allowed',False)),int(council.get('as_of',0))])
        rows.append(['GROUP', c['symbol'], c.get('risk_group', 'unknown')])
        swing = c.get('swing', {})
        rows.append(['SWING', c['symbol'], int(swing.get('ready', False)), int(swing.get('allowed', False)), int(swing.get('long_only', True))])
        inv = c.get('investment', {})
        rows.append(['INVESTMENT', c['symbol'], int(inv.get('ready', False)), int(inv.get('allowed', False)),
            inv.get('score', 0), inv.get('fair_value', 0), int(inv.get('early', False)), inv.get('nominal_fraction', .25),
            inv.get('risk_cap', 40), inv.get('action', 0), inv.get('action_event', ''), inv.get('period', 0),
            inv.get('rank', 0), int(inv.get('coverage_ok', False)), inv.get('event_id', ''), inv.get('reference_price',0), int(inv.get('news_time',0)), inv.get('signal','')])
    for event in snapshot["events"]:
        rows.append(["MACRO", int(event["time"] - 1800), int(event.get("end", event["time"]) + 1800), int(event["time"] - 300)])
    rows.append(["END", len(rows)])
    return "".join("|".join(map(clean, row)) + "\n" for row in rows)


def resolve_earnings(ticker, rows, issuer, now):
    today = cal.now_ny(now).date()
    dates = sorted({r["reportDate"] for r in rows if r.get("symbol") == ticker and r.get("reportDate") and r["reportDate"] >= (today - timedelta(days=7)).isoformat()})
    override = issuer.get(ticker)
    if override:
        if not (override.get("verified") and override.get("source", "").startswith("https://") and 0 <= now - override.get("checked_at", 0) <= 86400):
            return None, "ISSUER_CALENDAR_UNVERIFIED"
        dates = [override["date"]]  # Explicit verified issuer announcement wins.
    if not dates: return None, "EARNINGS_COVERAGE_UNKNOWN"
    if len(dates) > 1 and (date.fromisoformat(dates[1]) - date.fromisoformat(dates[0])).days < 30:
        return None, "EARNINGS_DATES_CONFLICT"
    # Keep a recent event exclusion through its post-release regular session.
    for value in dates:
        window = cal.earnings_window(date.fromisoformat(value), outside_hours=True)
        if window["blocked_until"] >= now: return window, ""
    return None, "EARNINGS_COVERAGE_UNKNOWN"


class Collector:
    def __init__(self, cfg, broker=None, providers=None, store=None):
        self.cfg = cfg
        self.directory = bridge(cfg)
        self.store = store or Store(ROOT / "runtime/state.sqlite3")
        # Research stores share the collector's storage scope. An injected test
        # store must never ingest synthetic rows into production news storage.
        self.context_directory = self.store.path.parent / 'official-news'
        self.providers = providers or Providers(cfg, self.store)
        self.broker = broker or ReadOnlyMT5(cfg)

    def job(self, name, seconds, callback, now):
        cached = self.store.get("job:" + name, {})
        retry_after = min(seconds, 300) if cached.get("error") else seconds
        if now - cached.get("attempt", 0) < retry_after: return cached
        try:
            data = callback()
            cached = {"attempt": now, "success": time.time(), "data": data, "error": ""}
        except Exception as exc:
            # Provider errors are sanitized; unexpected errors report their type only.
            from .providers import ProviderError
            error = str(exc) if isinstance(exc, (ProviderError, BrokerError)) else type(exc).__name__
            cached = {**cached, "attempt": now, "error": error}
            self.store.audit("PROVIDER_FAILURE", {"source": name, "error": error})
        self.store.set("job:" + name, cached)
        return cached

    @staticmethod
    def healthy(job, now, age):
        return not job.get("error") and -5 <= now - job.get("success", 0) <= age

    def observation(self, now, valid, session, cfg_hash):
        key = "observation:" + cal.now_ny(now).date().isoformat()
        item = self.store.get(key, {"last": 0, "seconds": 0, "started": 0, "config_hash": cfg_hash, "failed": False})
        if not session: return
        start, finish = session[0] + 900, session[1] - 300
        # A pre-session placeholder contains no observation evidence. Bind it to
        # the current revision before the window; never reset an observed run.
        if (now < start and item.get('config_hash') != cfg_hash and
                not item.get('started') and not item.get('last') and not item.get('seconds')):
            item = {'last': 0, 'seconds': 0, 'started': 0, 'config_hash': cfg_hash, 'failed': False}
        if start <= now <= finish:
            if not valid: item["failed"] = True
            if not item["started"]: item["started"] = now
            if item["config_hash"] != cfg_hash: item["failed"] = True
            if item["last"] and now - item["last"] > 30: item["failed"] = True
            if valid and item["last"]: item["seconds"] += min(30, now - item["last"])
            item["last"] = now
        if now >= finish and item["last"] >= finish - 15 and item["started"] <= start + 15 and not item["failed"] and item["seconds"] >= finish - start - 30:
            self.store.set("observation_pass", {"date": cal.now_ny(now).date().isoformat(), "config_hash": cfg_hash, "completed_at": now})
        self.store.set(key, item)

    def previous_levels(self, ticker, mapping, today):
        key = "levels:" + ticker + ":" + today.isoformat()
        result = self.store.get(key)
        if result: return result
        prior = cal.session(cal.previous_session(today))
        mt = self.broker.mt
        bars = mt.copy_rates_range(mapping["symbol"], mt.TIMEFRAME_M1,
            datetime.fromtimestamp(prior[0]+mapping["costs"]["broker_utc_offset_seconds"], timezone.utc), datetime.fromtimestamp(prior[1]-1+mapping["costs"]["broker_utc_offset_seconds"], timezone.utc))
        if bars is None or len(bars) < 30: return (0, 0)
        result = (float(max(bars["high"])), float(min(bars["low"])))
        self.store.set(key, result)
        return result

    def cycle(self, now=None):
        now = now or time.time()
        cycle_started = now
        cfg = self.cfg; today = cal.now_ny(now).date(); session = cal.session(today)
        command = read_json(self.directory / "control.json", {"mode": "stop"})
        mode = command.get("mode", "stop")
        if mode not in ("stop", "observe", "demo"): mode = "stop"
        snapshot = {"schema": 1, "time": now, "mode": mode, "ready": False,
                    "login": cfg.get("expected_login") or 0, "server": cfg["expected_server"],
                    "observation_ready": False, "risk": cfg["risk"], "shared_risk": cfg['shared_risk'], "contracts": [], "events": [], "issues": []}
        try:
            inspected = self.broker.inspect()
        except Exception as exc:
            # Retain last known risk obligations/specifications during IPC failure.
            previous = read_json(self.directory / "snapshot.json", {})
            for key in ("contracts", "events"):
                snapshot[key] = previous.get(key, [])
            snapshot["issues"] = [str(exc) if isinstance(exc, BrokerError) else type(exc).__name__]
            self.publish(snapshot)
            return snapshot
        snapshot["account"] = inspected["account"]
        snapshot['foreign_exposure'] = inspected['foreign_exposure']
        snapshot['enabled_styles'] = command.get('styles', ['day', 'swing', 'investment'])
        mappings = inspected["mappings"]
        cfg_hash = config_hash(cfg)
        held = [t for t, m in mappings.items() if any(p["symbol"] == m["symbol"] for p in inspected["positions"])]
        if len(inspected['positions']) > 3: snapshot["issues"].append("POSITION_LIMIT_EXCEEDED")
        pool_key = "pool:" + today.isoformat()
        pool = self.store.get(pool_key)
        if pool is None and session and now >= session[0] + 900:
            # A late restart must not retrospectively pick a better research pool.
            ranked = self.broker.rank(mappings) if now <= session[0]+960 else []
            pool = (held + [t for t in ranked if t not in held])[:3]
            self.store.set(pool_key, pool)
        pool = list(dict.fromkeys(held + (pool or [])))[:3]
        snapshot["pool"] = pool
        if session and now>session[0]+960 and not pool: snapshot["issues"].append("MISSED_0945_POOL_SELECTION")
        active = session and session[0] - 3600 <= now <= session[1] + 300
        from .investment import UNIVERSE
        bootstrap = any(t in mappings and not self.store.get('investment:fundamental:'+t) for t in UNIVERSE)
        data_active = bool(active or bootstrap)
        # Keep cached exclusions available even outside polling hours.
        macro = self.job("macro", 86400, lambda: self.providers.macro(now), now) if data_active else self.store.get("job:macro", {})
        earnings = self.job("earnings", 86400, lambda: self.providers.alpha("EARNINGS_CALENDAR", horizon="3month"), now) if data_active else self.store.get("job:earnings", {})
        snapshot["events"] = [e for e in macro.get("data", []) if e.get("time", 0) + 86400 >= now]
        # A stable pool-specific key prevents a new symbol inheriting old feed health.
        pool_id = ",".join(sorted(pool))
        from .investment import UNIVERSE
        investment_symbols = [t for t in UNIVERSE if t in mappings]
        news_symbols = sorted(set(pool+investment_symbols))
        news_key, social_key = "news:" + pool_id, "social:" + pool_id
        def investment_news_job():
            items=self.providers.news(news_symbols)
            return {'items':items,'coverage':getattr(self.providers,'last_news_coverage',{'complete':False})}
        investment_news_key='investment_news:'+','.join(news_symbols)
        news_interval=300 if session and session[0]<=now<=session[1] else 900
        investment_news = self.job(investment_news_key, news_interval, investment_news_job, now) if active and news_symbols else self.store.get('job:'+investment_news_key, {})
        if active and pool and isinstance(investment_news.get('data'),dict):
            # One quota-counted request serves both sections; do not spend 104+ calls/day.
            news={**investment_news,'data':[r for r in investment_news['data'].get('items',[]) if r.get('ticker') in pool]}
            self.store.set('job:'+news_key,news)
        else:
            news=self.store.get('job:'+news_key,{})
        # Reddit was explicitly retired. Never refresh or reuse its cached evidence.
        social = {'data': [], 'disabled': True, 'error': 'REDDIT_RETIRED'}
        for source, job in (("news", news), ("social", social)):
            evidence_key = "evidence:" + source
            old = [] if source == 'social' else self.store.get(evidence_key, [])
            combined = old + job.get("data", [])
            max_age = NEWS_RETENTION if source == 'news' else 21600
            # Preserve a news article's prior publication version. Replacing it
            # by ID alone could let a provider's republished timestamp rejuvenate
            # the same old story before the guard can compare the two versions.
            identity = lambda x: (x['ticker'], x['cluster'], x['time']) if source == 'news' else (x['ticker'], x['cluster'])
            unique = {identity(x): x for x in combined if now - max_age <= x["time"] <= now + 5}
            evidence=list(unique.values())
            # Keep independent reports and withheld verdicts for audit. Collapsing
            # headlines before assessment would destroy corroborating evidence.
            self.store.set(evidence_key, assess_news(evidence, now) if source=="news" else evidence)
        news_evidence = self.store.get('evidence:news', [])
        snapshot['news_credibility'] = news_summary(news_evidence)
        # Purge all expired social payloads, including per-pool cached responses.
        for row in self.store.db.execute("SELECT key,value FROM kv WHERE key LIKE 'job:social:%'").fetchall():
            import json
            cached = json.loads(row[1]); cached["data"] = [x for x in cached.get("data", []) if now - x["time"] <= 21600]
            self.store.set(row[0], cached)
        # Network work may have completed after the cycle's initial timestamp.
        # Evaluate newly retrieved data against the actual completed clock.
        now = time.time()
        from .ai_news import read_state as read_ai_state
        from .context_agent import evaluate as agent_context
        ai_state = read_ai_state(now, self.context_directory)
        # Put admitted publisher articles in the same full-text review queue.
        # Failure blocks entries without changing any protective exit state.
        try:
            from .official_news import OfficialNews, NewsStore, settings as news_settings
            reader_store = NewsStore(self.context_directory)
            try:
                added = OfficialNews(news_settings(), reader_store).ingest_reporting(news_evidence, now)
            finally:
                reader_store.db.close()
            if added:
                ai_state = read_ai_state(now, self.context_directory)
        except Exception:
            ai_state.update(healthy=False, error='AI_ARTICLE_QUEUE_UNAVAILABLE')
        context = agent_context(pool, now, ai_state)
        snapshot['context_agent'] = context
        # Provider sentiment and the retired classifier do not confer readiness.
        sentiment_valid = False
        global_ok = (context['ready'] and self.healthy(macro, now, 86400)
                     and not inspected["foreign_exposure"] and bool(cfg.get("expected_login")))
        snapshot['issues'].extend('context_agent: ' + reason for reason in context['issues'])
        for source, job, age in (("macro", macro, 86400),):
            if not self.healthy(job, now, age): snapshot["issues"].append(source + ": " + (job.get("error") or "STALE_OR_UNAVAILABLE"))
        if inspected["foreign_exposure"]: snapshot["issues"].append("FOREIGN_EXPOSURE")
        if not cfg.get("expected_login"): snapshot["issues"].append("ACCOUNT_NOT_PINNED")
        from .council import snapshot as council_snapshot
        try:
            council=council_snapshot(cfg,list(mappings),self.store,now,refresh_if_due=bool(active))
        except Exception as exc:
            council={'healthy':False,'issues':[type(exc).__name__],'decisions':{}}
        snapshot['council']={k:v for k,v in council.items() if k not in ('rows','decisions')}
        issuer = read_json(Path(cfg["research"]["issuer_calendar_file"]), {})
        for ticker, m in mappings.items():
            costs = m["costs"]
            # A verified broker session is expressed in NY local HH:MM. Holidays
            # and early closes are intersected with the exchange session.
            op, cl = (session[0] + 900, session[1]) if session else (0, 0)
            if session:
                hours = costs.get("sessions", {}).get(str(today.weekday()))
                if hours:
                    try:
                        def localstamp(s):
                            return int(datetime.fromisoformat(today.isoformat() + "T" + s).replace(tzinfo=cal.NY).timestamp())
                        op, cl = max(op, localstamp(hours[0])), min(cl, localstamp(hours[1]))
                    except (ValueError, TypeError, IndexError): op = cl = 0
                else: op = cl = 0
            issue = ""; event = {}; sec_until = self.store.get("sec_close:" + ticker, 0)
            fundamental_ok = True
            if m["kind"] == "stock" and (ticker in pool or ticker in investment_symbols or cfg['swing']['enabled']):
                event, issue = resolve_earnings(ticker, earnings.get("data", []), issuer, now)
                event = event or {}
                if not self.healthy(earnings, now, 86400): issue = "EARNINGS_FEED_STALE"
                cik = cfg["symbols"][ticker]["cik"]
                previous_sec_success = self.store.get("job:SEC:" + ticker, {}).get("success", now-86400)
                sub = self.job("SEC:" + ticker, 300, lambda cik=cik: self.providers.submissions(cik), now) if data_active else self.store.get("job:SEC:" + ticker, {})
                now = time.time()
                if not self.healthy(sub, now, 360): issue = "SEC_FEED_STALE"
                if sub.get("data"):
                    rec = sub["data"]["filings"]["recent"]
                    for j, form in enumerate(rec["form"]):
                        if form not in ("8-K", "8-K/A"): continue
                        stamp = datetime.fromisoformat(rec["acceptanceDateTime"][j].replace("Z", "+00:00")).timestamp()
                        if min(previous_sec_success, now-86400) <= stamp <= now and any(item.strip() in ("1.03", "2.02", "4.02") for item in rec.get("items", [""] * len(rec["form"]))[j].split(",")):
                            sec_until = max(sec_until, cl or now + 86400)
                    self.store.set("sec_close:" + ticker, sec_until)
                    material = self.store.get('investment:sec_events', [])
                    from hashlib import sha256
                    for j, form in enumerate(rec['form']):
                        if form not in ('8-K', '8-K/A'): continue
                        stamp = datetime.fromisoformat(rec['acceptanceDateTime'][j].replace('Z', '+00:00')).timestamp()
                        if not now-90*86400 <= stamp <= now: continue
                        items = {x.strip() for x in rec.get('items', ['']*len(rec['form']))[j].split(',')}
                        kind = 'bankruptcy' if '1.03' in items else 'non_reliance' if '4.02' in items else ''
                        if kind:
                            accession=rec['accessionNumber'][j]
                            event_id=sha256((ticker+':'+accession).encode()).hexdigest()
                            if not any(e['event_id']==event_id for e in material):
                                material.append({'ticker':ticker,'event_id':event_id,'kind':kind,'primary_verified':True,
                                    'published_at':stamp,'first_observed':now,'source':'https://www.sec.gov/Archives/edgar/data/'+str(int(cik))+'/'+accession.replace('-','')+'/'+rec['primaryDocument'][j]})
                    self.store.set('investment:sec_events', material)
                    def research():
                        facts = self.providers.sec(f"api/xbrl/companyfacts/CIK{cik}.json")
                        return analyse(facts, sub["data"], datetime.fromtimestamp(now, timezone.utc).isoformat(), cfg["symbols"][ticker].get("sector", ""))
                    latest = rec["accessionNumber"][0] if rec["accessionNumber"] else "empty"
                    fundamental = self.job("fundamentals:" + ticker + ":" + latest, 86400, research, now) if data_active else self.store.get('job:fundamentals:'+ticker+':'+latest,{})
                    now = time.time()
                    fundamental_ok = self.healthy(fundamental, now, 86400)
                    if fundamental.get("data"):
                        store_report = fundamental['data']
                        store_report.get('investment_metrics', {})['checked_at'] = fundamental.get('success', 0)
                        self.store.set('investment:fundamental:'+ticker, store_report)
                        write_json(ROOT / "runtime/fundamentals" / (ticker + ".json"), store_report)
            old_event = self.store.get("earnings:" + ticker, {})
            if old_event.get("blocked_until", 0) >= now and (not event or old_event["close_by"] < event["close_by"]): event = old_event
            if event: self.store.set("earnings:" + ticker, event)
            if issue and ticker in pool: snapshot["issues"].append(ticker + ": " + issue)
            if not fundamental_ok and ticker in pool: snapshot["issues"].append(ticker + ": FUNDAMENTALS_STALE")
            evidence = {s: [x for x in self.store.get("evidence:" + s, []) if x["ticker"] == ticker] for s in ("news", "social")}
            veto = lambda d: any(sentiment_veto(evidence[s], d, s, now) for s in evidence) if sentiment_valid else False
            high, low = self.previous_levels(ticker, m, today) if m["verified"] and session and ticker in pool else (0, 0)
            entry_ok = bool(self.healthy(macro, now, 86400) and fundamental_ok and not issue and sec_until < now and not (event.get("close_by", 0) <= now <= event.get("blocked_until", 0)))
            if m["kind"] == "stock" and not event: entry_ok = False
            snapshot["contracts"].append({"ticker": ticker, "symbol": m["symbol"], "verified": m["verified"],
                "commission": max(costs.get("commission_round_turn_per_lot", 0), costs.get("minimum_commission_round_turn", 0) / m["minimum"]) if m["minimum"] > 0 else 1e100, "slippage": costs.get("slippage_points", 0),
                "margin_rate": costs.get("max_margin_rate", 0), "finance_long": costs.get("financing_long_per_lot_day", 0),
                "finance_short": costs.get("financing_short_per_lot_day", 0), "server_offset": costs.get("broker_utc_offset_seconds", 0),
                "open": op, "close": cl, "pool": ticker in pool, "entry_allowed": entry_ok,
                'investment_event_ok': bool(fundamental_ok and not issue and not (event.get('close_by', 0) <= now <= event.get('blocked_until', 0))),
                'investment_sources_ok': bool(fundamental_ok and not issue),
                'swing_sources_ok': bool(fundamental_ok and not issue),
                'risk_group': 'technology' if ticker == 'QQQ' else cfg['symbols'][ticker].get('sector', 'broad_market' if ticker == 'SPY' else 'unknown'),
                "veto_buy": veto(1), "veto_sell": veto(-1), "earnings_close": event.get("close_by", 0),
                "earnings_end": event.get("blocked_until", 0), "sec_close_until": sec_until, "previous_high": high, "previous_low": low,
                'council':council.get('decisions',{}).get(ticker,{'ready':False,'buy_allowed':False,'sell_allowed':False,'as_of':now})})
        snapshot["ready"] = bool(global_ok and pool and all(any(c["ticker"] == t and c["verified"] for c in snapshot["contracts"]) for t in pool))
        from .ai_news import apply as apply_ai_news
        apply_ai_news(snapshot, ai_state, now)
        from .investment_runner import build as build_investment
        build_investment(self, snapshot, mappings, now, self.healthy(macro, now, 86400), investment_news, council)
        from .swing import build as build_swing
        build_swing(self, snapshot, mappings, now, self.healthy(macro, now, 86400))
        ea = read_json(self.directory / "ea-status.json", {})
        now = time.time()
        # Reject slow samples before observation can count or certify them.
        if now-cycle_started > 60:
            snapshot['ready'] = False; snapshot['investment_ready'] = False; snapshot['swing_ready'] = False
            snapshot['issues'].append('CYCLE_TOO_SLOW')
        ea_ok = self.ea_healthy(ea, now)
        from .cli import source_hash
        observation_key=fingerprint({"config_hash":cfg_hash,"source_hash":source_hash()})
        snapshot['decision_context'] = observation_key
        # The single collector observes real health while either research-only
        # or demo execution runs; entry permission never certifies observation.
        # Politician evidence is a swing-only optional veto, not a day source.
        if mode in ("observe", "demo"): self.observation(now, snapshot["ready"] and ea_ok and not snapshot["issues"], session, observation_key)
        passed = self.store.get("observation_pass", {})
        snapshot["observation_ready"] = passed.get("config_hash") == observation_key
        for style in ('investment', 'swing'):
            if mode in ('observe', 'demo'):
                # Each section observes independently of optional social feeds.
                class SectionStore:
                    def __init__(inner, wrapped, section): inner.wrapped,inner.section=wrapped,section
                    def get(inner,key,default=None): return inner.wrapped.get(inner.section+':'+key,default)
                    def set(inner,key,value): return inner.wrapped.set(inner.section+':'+key,value)
                saved_store=self.store
                try:
                    self.store=SectionStore(saved_store,style)
                    self.observation(now, snapshot[style+'_ready'] and ea_ok, session, observation_key)
                finally: self.store=saved_store
            snapshot[style+'_observation_ready'] = self.store.get(style+':observation_pass', {}).get('config_hash') == observation_key
        if ea.get("version")!=__version__:snapshot["issues"].append("EA_RELOAD_REQUIRED: expected "+__version__)
        snapshot["budgets"] = self.store.budgets()
        snapshot["feed_health"] = {k: {"success": v.get("success", 0), "error": v.get("error", "")} for k, v in (("news", news), ("social", social), ("macro", macro), ("earnings", earnings))}
        snapshot['feed_health']['social'] = {'disabled': True, 'provider': 'none', 'reason': 'Reddit retired by user'}
        snapshot['feed_health']['context_agent'] = {'ready': context['ready'], 'provider': 'official_agent',
            'shared_ready': context['shared_ready'], 'issues': context['issues'], 'symbol_issues': context['symbol_issues']}
        # Timestamp at completion, not before slow network calls. Health is checked
        # against completion too; a slow request cannot make older data appear fresh.
        finished = time.time()
        if finished - cycle_started > 60 and 'CYCLE_TOO_SLOW' not in snapshot['issues']:
            snapshot['ready'] = False; snapshot['investment_ready'] = False; snapshot['swing_ready'] = False; snapshot['issues'].append('CYCLE_TOO_SLOW')
        snapshot['time'] = finished
        try:
            from .shadow_runner import run_shadow
            run_shadow(self, snapshot, mappings, now)
            from .investment_shadow import run as run_investment_shadow
            run_investment_shadow(self, snapshot, mappings, now)
        except Exception as exc:
            self.store.audit("SHADOW_ERROR", {"type":type(exc).__name__})
        snapshot.pop('_day_candidates', None)
        self.publish(snapshot)
        return snapshot

    def publish(self, snapshot):
        write_json(self.directory / "snapshot.json", snapshot)
        atomic_write(self.directory / "snapshot.txt", encode_snapshot(snapshot))

    @staticmethod
    def ea_healthy(ea, checked_at):
        return (ea.get('demo') is True and ea.get('version') == __version__ and
                0 <= checked_at - ea.get('time', 0) <= 10 and not any(ea.get(k) for k in
                ('foreign_exposure','halt','hard_halt','week_halt','unreconciled','intent_pending')))
