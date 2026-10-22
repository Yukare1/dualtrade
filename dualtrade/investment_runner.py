"""Build investment decisions independently of the intraday sentiment gate."""
from __future__ import annotations
from datetime import datetime, timezone
from .investment import UNIVERSE, options, evaluate, candidate, update_thesis, exit_action
from .material_news import assess
from .storage import read_json, write_json
from .config import ROOT
from . import calendar as cal
from .storage import fingerprint


def daily_context(mt, symbol):
    bars = mt.copy_rates_from_pos(symbol, mt.TIMEFRAME_D1, 1, 65)
    if bars is None or len(bars) < 30: return 0., 0.
    tr = [max(float(bars[i]['high']-bars[i]['low']), abs(float(bars[i]['high']-bars[i-1]['close'])),
              abs(float(bars[i]['low']-bars[i-1]['close']))) for i in range(1, len(bars))]
    atr = sum(tr[:14])/14
    for v in tr[14:]: atr = (13*atr+v)/14
    return atr, float(bars[-1]['close'])


def build(collector, snapshot, mappings, now, macro_ok, news, council):
    cfg, store, mt = collector.cfg, collector.store, getattr(collector.broker, 'mt', None)
    settings = options(cfg)
    rows = read_json(ROOT/settings['events_file'], {'events': []}).get('events', [])
    # Preserve severe SEC obligations independently of six-hour news payloads.
    rows += store.get('investment:sec_events', [])
    # Keep validated material obligations durable through research outages.
    ai_events = {e['event_id']: e for e in store.get('investment:ai_events', [])
                 if 0 <= now-e.get('published_at', 0) <= 90*86400}
    for c in snapshot['contracts']:
        for e in c.get('ai_news', {}).get('material_events', []):
            if e['event_id'] in ai_events:
                e = {**e, 'first_observed': ai_events[e['event_id']]['first_observed']}
            ai_events[e['event_id']] = e
    store.set('investment:ai_events', list(ai_events.values()))
    rows += list(ai_events.values())
    reports, decisions = {}, []
    data=news.get('data',{})
    coverage = collector.healthy(news, now, 600) and isinstance(data,dict) and data.get('coverage',{}).get('complete') is True
    snapshot['investment_news_coverage']={'healthy':collector.healthy(news,now,600),'complete':bool(coverage),
        'details':data.get('coverage',{}) if isinstance(data,dict) else {}}
    pending = data.get('items',[]) if isinstance(data,dict) else []
    snapshot['material_news_review_queue']=[{'ticker':r.get('ticker'),'title':r.get('title'),'source':r.get('url'),'published_at':r.get('time'),
        'status':'PRIMARY_ECONOMIC_EVIDENCE_REQUIRED'} for r in pending]
    for c in snapshot['contracts']:
        ticker = c['ticker']
        if ticker not in UNIVERSE or mt is None: continue
        report = store.get('investment:fundamental:'+ticker, {})
        metrics = report.get('investment_metrics', {})
        analysis = evaluate(metrics, now, cfg)
        state = update_thesis(store.get('investment:thesis:'+ticker), analysis)
        store.set('investment:thesis:'+ticker, state)
        event = assess(rows, ticker, metrics, now, coverage)
        atr, close = daily_context(mt, c['symbol'])
        # The pre-event completed M15 close comes only from broker history,
        # never from the model. Persist it by event identity for later cycles.
        positive = event.get('positive', {})
        if positive.get('ai_review_id') and not positive.get('reference_price'):
            key = 'investment:ai_reference:' + positive['event_id']
            reference = store.get(key, 0)
            if not reference:
                stamp = positive['published_at'] + c['server_offset']
                bars = mt.copy_rates_range(c['symbol'], mt.TIMEFRAME_M15,
                    datetime.fromtimestamp(stamp-7*86400, timezone.utc), datetime.fromtimestamp(stamp, timezone.utc))
                completed = [r for r in bars if r['time']+900 <= stamp] if bars is not None else []
                if completed:
                    reference = float(completed[-1]['close'])
                    store.set(key, reference)
            positive['reference_price'] = reference
        quote = mt.symbol_info_tick(c['symbol'])
        price_ok = quote is not None and 0 <= now-(quote.time-c['server_offset']) <= 30
        ask = float(quote.ask) if price_ok else 0
        decision = candidate(analysis, ask, atr, event, now, cfg)
        action, reason = exit_action(analysis, state, event, close)
        if action in ('exit', 'reduce'):
            decision['allowed'] = False
            decision['reasons'].append('ACTIVE_EXIT_OR_REDUCTION')
        warning = []
        ai = c.get('ai_news', {})
        if ai and not ai.get('ready'):
            decision['allowed'] = False
            decision['reasons'].extend(ai.get('reasons', ['AI_NEWS_NOT_READY']))
        vote = council.get('decisions', {}).get(ticker, {})
        if not vote.get('ready'): warning.append('COUNCIL_UNAVAILABLE')
        if not coverage: warning.append('NEWS_COVERAGE_UNAVAILABLE')
        # A validated sentiment veto is supplementary. Missing sentiment is a warning.
        if c.get('veto_buy') or vote.get('ready') and not vote.get('buy_allowed'):
            decision.update(allowed=False); decision['reasons'].append('AUXILIARY_VETO')
        if not c['verified'] or not macro_ok or not c.get('investment_event_ok', False):
            decision.update(allowed=False); decision['reasons'].append('CONTRACT_CALENDAR_OR_MACRO')
        ready = bool(analysis.get('ready') and c['verified'] and c.get('investment_sources_ok',False) and macro_ok and not snapshot.get('foreign_exposure', False))
        row = {**decision, 'ready': ready, 'atr': atr, 'daily_close': close,
               'action': {'hold': 0, 'exit': 1, 'reduce': 2, 'promote': 3}[action],
               'action_reason': reason, 'action_event': event.get('event_id', ''),
               'period': int(metrics.get('period_end', '').replace('-', '') or 0),
               'coverage_ok': coverage, 'reference_price': event.get('positive',{}).get('reference_price',0), 'news_time': event.get('positive',{}).get('published_at',0), 'warnings': warning, 'symbol': c['symbol'], 'ticker': ticker}
        c['investment'] = row
        row['discount'] = 1-ask/row['fair_value'] if row['fair_value']>0 else 0
        decisions.append(row)
        reports[ticker] = {'analysis': analysis, 'decision': row, 'news': event, 'thesis': state, 'ai_news': ai}
    ranked = sorted((r for r in decisions if r['allowed']), key=lambda r: (-r['score'], -r['discount'], r['ticker']))
    day=cal.now_ny(now).date()
    session=cal.session(day)
    signal=fingerprint({'day':day.isoformat(),'companies':{t:{
        'metrics':{k:v for k,v in r['analysis']['metrics'].items() if k not in ('checked_at','provenance')},
        'positive':r['news'].get('positive',{}).get('event_id',''),
        'action':r['news'].get('action'),'event':r['news'].get('event_id',''),
        'ai_reviews':[a['event_id'] for a in r['ai_news'].get('reviews', [])]}
        for t,r in reports.items()}})[:16]
    selection=store.get('investment:selection',{})
    if session and now>=session[0]+900 and selection.get('signal')!=signal:
        selection={'signal':signal,'selected_at':now,'tickers':[r['ticker'] for r in ranked]}
        store.set('investment:selection',selection)
    selected=selection.get('tickers',[]) if selection.get('signal')==signal else []
    for row in decisions:
        row['signal']=signal
        if row['allowed'] and row['ticker'] in selected:row['rank']=selected.index(row['ticker'])+1
    shared_ok = not snapshot.get('foreign_exposure', False) and bool(cfg.get('expected_login'))
    snapshot['investment_ready'] = bool(settings['enabled'] and shared_ok and any(r['ready'] for r in decisions))
    snapshot['investment_report'] = reports
    from . import __version__
    write_json(ROOT/'runtime/reports/investment.json', {'version': __version__, 'time': now, 'companies': reports, 'news_coverage':snapshot['investment_news_coverage'], 'pending_primary_reviews':snapshot['material_news_review_queue'],
        'limitations': ['Research thresholds are unvalidated', 'Primary event reviews are required for unstructured economic facts',
                        'No historical news availability is invented', 'CFD carrying costs can eliminate all candidates']})
    return reports
