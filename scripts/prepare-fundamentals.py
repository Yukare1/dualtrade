"""Prepare the existing SEC analysis cache before the session; no order APIs."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dualtrade import calendar as cal
from dualtrade.config import load
from dualtrade.credentials import load_local_credentials
from dualtrade.fundamentals import analyse
from dualtrade.providers import Providers, ProviderError
from dualtrade.storage import Store, process_lock, read_json, write_json


def scheduled_window(now):
    session = cal.session(cal.now_ny(now).date())
    return bool(session and now < session[0] - 300)


def publish_cache(store, key, candidate, started):
    """A parallel collector's newer result wins, including a newer failure."""
    store.db.execute('BEGIN IMMEDIATE')
    try:
        row = store.db.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
        current = json.loads(row[0]) if row else {}
        if max(current.get('success', 0), current.get('attempt', 0)) > started:
            store.db.rollback()
            return current, False
        store.db.execute('INSERT OR REPLACE INTO kv VALUES (?,?)',
                         (key, json.dumps(candidate, allow_nan=False)))
        store.db.commit()
        return candidate, True
    except BaseException:
        store.db.rollback()
        raise


def prepare(cfg, store, provider, directory, *, tickers=None, clock=time.time,
            analyse_fn=analyse):
    selected = tickers or [t for t, value in cfg['symbols'].items()
                           if value.get('kind') == 'stock' and value.get('cik')]
    if not selected or any(t not in cfg['symbols'] or
                           cfg['symbols'][t].get('kind') != 'stock' or
                           not cfg['symbols'][t].get('cik') for t in selected):
        raise ValueError('Select configured stock issuers with a CIK')
    results = []
    for ticker in dict.fromkeys(selected):
        started = clock()
        item = cfg['symbols'][ticker]
        try:
            submissions = provider.submissions(item['cik'])
            accessions = submissions['filings']['recent']['accessionNumber']
            if not accessions:
                raise ValueError('Missing filing identity')
            accession = accessions[0]  # Same cache identity as Collector.cycle.
            facts = provider.sec('api/xbrl/companyfacts/CIK' + item['cik'] + '.json')
            as_of = datetime.fromtimestamp(clock(), timezone.utc).isoformat()
            report = analyse_fn(facts, submissions, as_of, item.get('sector', ''))
            finished = clock()
            candidate = {'attempt': started, 'success': finished, 'data': report, 'error': ''}
            cached, published = publish_cache(store, 'job:fundamentals:' + ticker + ':' + accession,
                                              candidate, started)
            data = cached.get('data', {})
            metrics = data.get('investment_metrics', {})
            # Independent preparation evidence; live collector keeps ownership
            # of investment:fundamental:* and its ordinary output files.
            write_json(directory / (ticker + '.json'), data)
            results.append({'ticker': ticker, 'status': 'prepared' if published else 'newer_cache_retained',
                            'cache_healthy': not cached.get('error'), 'accession': accession,
                            'checked_at': cached.get('success'), 'as_of': data.get('as_of'),
                            'quarters': len(data.get('rows', [])), 'period_end': metrics.get('period_end'),
                            'investment_metrics_complete': metrics.get('complete', False),
                            'missing': metrics.get('missing', []),
                            'sources': ['https://data.sec.gov/submissions/CIK' + item['cik'] + '.json',
                                        'https://data.sec.gov/api/xbrl/companyfacts/CIK' + item['cik'] + '.json']})
        except Exception as exc:
            # A preparation error cannot invalidate a still-healthy live cache.
            error = str(exc) if isinstance(exc, ProviderError) else type(exc).__name__
            results.append({'ticker': ticker, 'status': 'failed', 'cache_healthy': False, 'error': error})
        write_json(directory / 'progress.json', {'updated_at': clock(), 'items': results})
    return {'prepared_at': clock(), 'passed': all(x['cache_healthy'] for x in results),
            'items': results, 'orders_sent': 0,
            'scope': 'Existing SEC transformations and cache format; no trading eligibility granted',
            'limitations': ['Missing fields remain missing; no substitute financial values are invented',
                            'New filings and material news still require fresh checks during the session']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scheduled', action='store_true', help='Skip holidays and late catch-up runs')
    parser.add_argument('--tickers', nargs='+')
    args = parser.parse_args(argv)
    now = time.time()
    if args.scheduled and not scheduled_window(now):
        result = {'skipped': True, 'reason': 'Outside pre-session preparation window', 'time': now}
        write_json(ROOT / 'runtime/reports/fundamental-preparation-last-attempt.json', result)
        print(json.dumps(result))
        return 0
    cfg = load()
    load_local_credentials()
    store = Store(ROOT / 'runtime/state.sqlite3')
    try:
        with process_lock(ROOT / 'runtime/fundamental-preparation.lock'):
            result = prepare(cfg, store, Providers(cfg, store), ROOT / 'runtime/fundamental-preparation',
                             tickers=args.tickers)
            # Queue ownership stays with the existing document reader. Pending
            # and uncertain documents are reported, never marked reviewed here.
            news = read_json(ROOT / 'runtime/official-news/report.json', {})
            result['document_counts'] = news.get('counts', {})
            write_json(ROOT / 'runtime/reports/fundamental-preparation.json', result)
            print(json.dumps({'passed': result['passed'], 'issuers': len(result['items']),
                              'failed': [x['ticker'] for x in result['items'] if not x['cache_healthy']],
                              'orders_sent': 0, 'report': 'runtime/reports/fundamental-preparation.json'}))
            return 0 if result['passed'] else 1
    finally:
        store.db.close()


if __name__ == '__main__':
    raise SystemExit(main())
