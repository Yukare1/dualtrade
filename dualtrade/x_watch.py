"""Budgeted, read-only X research. Separate from the MT5 collector/order gates.

No model API, scraping, user expansions, posting, or broker calls. USD accounting
uses integer microdollars and pessimistically charges uncertain requests in full.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from zoneinfo import ZoneInfo

import requests

from .config import ROOT
from .storage import atomic_write, fingerprint, process_lock, read_json, write_json

UTC = timezone.utc
ISTANBUL = ZoneInfo('Europe/Istanbul')
POST_PRICE = 5_000
USER_PRICE = 10_000
PAGE_SIZE = 10
RETENTION = 72 * 3600
PRICE_MAX_AGE_DAYS = 30
API = 'https://api.x.com/2/'


class WatchError(RuntimeError):
    """Only fixed, credential-free error codes may be used here."""


def usd_units(value):
    try:
        amount = Decimal(str(value)) * 1_000_000
        if not amount.is_finite() or amount != amount.to_integral_value():
            raise ValueError
        return int(amount)
    except (InvalidOperation, ValueError, OverflowError):
        raise ValueError('Invalid USD amount') from None


def load_settings(path=None):
    cfg = read_json(Path(path) if path else ROOT / 'x-watch.json', {})
    if cfg.get('schema') != 1 or type(cfg.get('enabled')) is not bool:
        raise ValueError('Invalid X watch settings')
    if not 0 < usd_units(cfg.get('monthly_limit_usd')) <= 15_000_000:
        raise ValueError('X monthly limit cannot exceed authorized $15')
    if not 0 < usd_units(cfg.get('daily_limit_usd')) <= 500_000:
        raise ValueError('X daily limit cannot exceed $0.50')
    if type(cfg.get('poll_seconds')) is not int or cfg['poll_seconds'] < 120:
        raise ValueError('X polling interval must be at least 120 seconds')
    datetime.strptime(cfg['price_reviewed_on'], '%Y-%m-%d')
    accounts = cfg.get('news_accounts', []) + cfg.get('macro_accounts', [])
    if not accounts or len(accounts) > 10 or len(set(a.lower() for a in accounts)) != len(accounts):
        raise ValueError('Select 1 to 10 distinct X accounts')
    if any(not isinstance(a, str) or not re.fullmatch(r'[A-Za-z0-9_]{1,15}', a) for a in accounts):
        raise ValueError('Invalid X handle')
    symbols = cfg.get('symbols')
    if not isinstance(symbols, dict) or not symbols:
        raise ValueError('Select X watch symbols')
    for ticker, aliases in symbols.items():
        if not re.fullmatch(r'[A-Z]{1,6}', ticker) or not isinstance(aliases, list) or not aliases:
            raise ValueError('Invalid X symbol')
        if any(not isinstance(a, str) or not re.fullmatch(r'[A-Za-z0-9 &.\-]{1,40}', a) for a in aliases):
            raise ValueError('Invalid X search alias')
    build_query(cfg)
    return cfg


def build_query(cfg):
    groups = []
    if cfg['news_accounts']:
        authors = ' OR '.join('from:' + a for a in cfg['news_accounts'])
        terms = list(dict.fromkeys(a for aliases in cfg['symbols'].values() for a in aliases))
        words = ' OR '.join('"' + a + '"' if ' ' in a or '&' in a else a for a in terms)
        groups.append(f'(({authors}) ({words}))')
    if cfg['macro_accounts']:
        groups.append('(' + ' OR '.join('from:' + a for a in cfg['macro_accounts']) + ')')
    query = '(' + ' OR '.join(groups) + ') -is:retweet -is:reply'
    if len(query) > 512:
        raise ValueError('X recent search query exceeds 512 characters')
    return query


def research_window(now):
    """08:00 New York through the actual XNYS close; holidays/DST respected."""
    from .calendar import NY, session
    local = datetime.fromtimestamp(now, UTC).astimezone(NY)
    bounds = session(local.date())
    if not bounds:
        return None
    start = local.replace(hour=8, minute=0, second=0, microsecond=0).timestamp()
    return (int(start), bounds[1]) if start <= now < bounds[1] else None


class Ledger:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS requests(
              id INTEGER PRIMARY KEY, at REAL NOT NULL, day TEXT NOT NULL,
              month TEXT NOT NULL, units INTEGER NOT NULL, unit_price INTEGER NOT NULL,
              maximum INTEGER NOT NULL, settled INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS posts(id TEXT PRIMARY KEY, published REAL NOT NULL,
              retrieved REAL NOT NULL, payload TEXT NOT NULL);
        ''')

    def get(self, key, default=None):
        row = self.db.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO state VALUES (?,?)', (key, json.dumps(value)))

    def _totals(self, now):
        utc = datetime.fromtimestamp(now, UTC)
        day = utc.astimezone(ISTANBUL).date().isoformat()
        month = utc.strftime('%Y-%m')
        rows = self.db.execute('''SELECT
            COALESCE(SUM(CASE WHEN day=? THEN units ELSE 0 END),0),
            COALESCE(SUM(CASE WHEN month=? THEN units ELSE 0 END),0),
            COALESCE(SUM(CASE WHEN at>=? THEN units ELSE 0 END),0),
            COALESCE(MAX(at),0)
            FROM requests''', (day, month, now - 31 * 86400)).fetchone()
        return day, month, rows

    def budget(self, cfg, now):
        day, month, (daily, monthly, rolling, _) = self._totals(now)
        return {'currency': 'USD', 'day_istanbul': day, 'month_utc': month,
                'daily_accounted_usd': daily / 1_000_000,
                'monthly_accounted_usd': monthly / 1_000_000,
                'rolling_31_days_accounted_usd': rolling / 1_000_000,
                'daily_limit_usd': cfg['daily_limit_usd'], 'monthly_limit_usd': cfg['monthly_limit_usd'],
                'accounting': 'Conservative local estimate; unresolved requests retain full reservation'}

    def reserve(self, cfg, now, maximum, unit_price):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            day, month, (daily, monthly, rolling, last) = self._totals(now)
            units = maximum * unit_price
            if now < last:
                raise WatchError('CLOCK_MOVED_BACKWARDS')
            if daily + units > usd_units(cfg['daily_limit_usd']):
                raise WatchError('DAILY_BUDGET_LIMIT')
            limit = usd_units(cfg['monthly_limit_usd'])
            if monthly + units > limit or rolling + units > limit:
                raise WatchError('MONTHLY_BUDGET_LIMIT')
            cursor = self.db.execute('INSERT INTO requests(at,day,month,units,unit_price,maximum) VALUES (?,?,?,?,?,?)',
                                     (now, day, month, units, unit_price, maximum))
            self.db.commit()
            return cursor.lastrowid
        except BaseException:
            self.db.rollback()
            raise

    def settle(self, request_id, count):
        with self.db:
            row = self.db.execute('SELECT maximum,unit_price,settled FROM requests WHERE id=?', (request_id,)).fetchone()
            if not row or row[2] or type(count) is not int or not 0 <= count <= row[0]:
                raise WatchError('INVALID_BUDGET_SETTLEMENT')
            self.db.execute('UPDATE requests SET units=?,settled=1 WHERE id=?', (count * row[1], request_id))

    def prune(self, now):
        with self.db:
            self.db.execute('DELETE FROM posts WHERE published<?', (now - RETENTION,))

    def rows(self, now):
        return [json.loads(r[0]) for r in self.db.execute(
            'SELECT payload FROM posts WHERE published>=? ORDER BY published DESC LIMIT 100', (now - RETENTION,))]


def clean_text(value):
    return ''.join(c for c in value if c in '\n\t' or ord(c) >= 32).strip()


def analyse(post, author, cfg, now):
    text = clean_text(post['text'])
    tickers = [ticker for ticker, aliases in cfg['symbols'].items()
               if any(re.search(r'(?<!\w)' + re.escape(a) + r'(?!\w)', text, re.I) for a in aliases)
               or re.search(r'\$' + re.escape(ticker) + r'\b', text)]
    macro = author.lower() in {a.lower() for a in cfg['macro_accounts']}
    categories = []
    for name, pattern in (
        ('bilanço / beklenti', r'\b(earnings|revenue|profit|guidance|outlook|EPS)\b'),
        ('faiz / ekonomi', r'\b(FOMC|inflation|CPI|PPI|payrolls?|employment|interest rates?|monetary policy)\b'),
        ('hukuk / düzenleme', r'\b(SEC|lawsuit|enforcement|charges|investigation|fraud)\b'),
        ('sermaye / anlaşma', r'\b(acquisition|merger|contract|buyback|offering|dividend)\b'),
        ('kritik risk', r'\b(bankruptcy|restatement|delisting|cyberattack|recall|trading halt)\b')):
        if re.search(pattern, text, re.I):
            categories.append(name)
    if not categories:
        categories = ['makro / kurum duyurusu' if macro else 'şirket gelişmesi']
    stamp = datetime.fromisoformat(post['created_at'].replace('Z', '+00:00'))
    if stamp.tzinfo is None or not now - RETENTION <= stamp.timestamp() <= now + 5:
        raise WatchError('INVALID_POST_TIME')
    # Only exact/near-exact text copies are clustered; independent verification is
    # never inferred from the number of X accounts repeating a claim.
    normalized = re.sub(r'https?://\S+', '', text).lower()
    normalized = re.sub(r'\W+', ' ', normalized).strip()
    return {'id': post['id'], 'source': '@' + author, 'author_id': post['author_id'],
            'url': f'https://x.com/{author}/status/{post["id"]}',
            'published_at': stamp.timestamp(), 'retrieved_at': now,
            'text': text, 'tickers': tickers, 'scope': 'macro' if macro else 'company',
            'categories': categories, 'priority': 'high' if 'kritik risk' in categories else 'normal',
            'verification': 'UNVERIFIED_X_CLAIM', 'eligible_for_trading': False,
            'summary_tr': (', '.join(tickers) or 'Genel piyasa') + ': ' + ', '.join(categories)
                          + '. X gönderisi; kaynak belge henüz doğrulanmadı.',
            'cluster': hashlib.sha256(normalized.encode()).hexdigest()}


class XWatch:
    def __init__(self, cfg, ledger, session=None, report_dir=None):
        self.cfg, self.ledger = cfg, ledger
        self.http = session or requests.Session()
        # Do not implicitly send this secret through an environment-configured proxy.
        if session is None:
            self.http.trust_env = False
        self.report_dir = Path(report_dir) if report_dir else ROOT / 'runtime/reports'

    def request(self, endpoint, params, maximum, price, now):
        reservation = self.ledger.reserve(self.cfg, now, maximum, price)
        try:
            response = self.http.get(API + endpoint, params=params,
                headers={'Authorization': 'Bearer ' + os.environ['X_BEARER_TOKEN']},
                timeout=(5, 20), allow_redirects=False)
        except requests.RequestException:
            raise WatchError('X_NETWORK_FAILURE') from None
        if response.status_code != 200:
            raise WatchError('X_HTTP_' + str(response.status_code))
        try:
            body = response.json()
        except (ValueError, TypeError):
            raise WatchError('INVALID_X_JSON') from None
        # Partial/ambiguous responses retain their worst-case reservation.
        if not isinstance(body, dict) or body.get('errors'):
            raise WatchError('INCOMPLETE_X_RESPONSE')
        rows = body.get('data', [])
        if not isinstance(rows, list) or len(rows) > maximum:
            raise WatchError('INVALID_X_RESPONSE_COUNT')
        if endpoint == 'tweets/search/recent':
            count = body.get('meta', {}).get('result_count')
            if type(count) is not int or count != len(rows):
                raise WatchError('INVALID_X_RESPONSE_COUNT')
        elif 'data' not in body:
            raise WatchError('INVALID_X_RESPONSE_COUNT')
        return body, rows, reservation

    def accounts(self, now):
        names = self.cfg['news_accounts'] + self.cfg['macro_accounts']
        signature = fingerprint(names)
        cached = self.ledger.get('accounts', {})
        if cached.get('signature') == signature and 0 <= now - cached.get('resolved_at', 0) <= 30 * 86400:
            return cached['ids']
        _, rows, reservation = self.request('users/by', {'usernames': ','.join(names)}, len(names), USER_PRICE, now)
        ids = {}
        wanted = {a.lower() for a in names}
        for row in rows:
            if (not isinstance(row, dict) or not isinstance(row.get('username'), str)
                or row['username'].lower() not in wanted or not valid_id(row.get('id')) or row['id'] in ids):
                raise WatchError('INVALID_ACCOUNT_RESPONSE')
            ids[row['id']] = row['username']
        if {a.lower() for a in ids.values()} != wanted:
            raise WatchError('ACCOUNT_NOT_FOUND')
        self.ledger.set('accounts', {'signature': signature, 'resolved_at': now, 'ids': ids})
        self.ledger.settle(reservation, len(rows))
        return ids

    def check(self, now=None):
        now = time.time() if now is None else now
        self.ledger.prune(now)
        status = 'DISABLED'
        if self.cfg['enabled']:
            status = 'KEY_REQUIRED' if not os.environ.get('X_BEARER_TOKEN') else 'OUTSIDE_RESEARCH_HOURS'
            window = research_window(now)
            if window and os.environ.get('X_BEARER_TOKEN'):
                if now < self.ledger.get('next_poll_at', 0):
                    return self.report(now)
                self.ledger.set('next_poll_at', now + self.cfg['poll_seconds'])
                try:
                    reviewed = datetime.strptime(self.cfg['price_reviewed_on'], '%Y-%m-%d').replace(tzinfo=UTC)
                    if not 0 <= (datetime.fromtimestamp(now, UTC).date() - reviewed.date()).days <= PRICE_MAX_AGE_DAYS:
                        raise WatchError('API_PRICE_REVIEW_REQUIRED')
                    self.fetch(now, window)
                    status = 'OK'
                    self.ledger.set('last_success', now)
                except WatchError as exc:
                    status = str(exc)
                    if status in ('X_HTTP_401', 'X_HTTP_403', 'X_HTTP_402'):
                        self.ledger.set('next_poll_at', now + 3600)
                    elif status == 'X_HTTP_429':
                        self.ledger.set('next_poll_at', now + 900)
                except Exception:
                    status = 'X_UNEXPECTED_ERROR'
        self.ledger.set('status', status)
        return self.report(now)

    def fetch(self, now, window):
        ids = self.accounts(now)
        query = build_query(self.cfg)
        signature = fingerprint(query)
        checkpoint = self.ledger.get('checkpoint', {})
        if checkpoint.get('signature') != signature:
            checkpoint = {'signature': signature}
        pending = checkpoint.get('pending')
        # Do not spend tomorrow's budget on an unfinished prior-session backlog.
        if pending and pending['window_start'] != window[0]:
            self.ledger.set('coverage_gap', 'Prior-session backlog was skipped; coverage is incomplete')
            pending = None
        if not pending:
            end = int(now) - 15  # X search indexing is not an instant news wire.
            start = max(window[0], int(checkpoint.get('until', end - 120)))
            if start >= end:
                return
            params = {'query': query, 'max_results': PAGE_SIZE,
                      'tweet.fields': 'created_at,author_id',
                      'start_time': iso(start), 'end_time': iso(end)}
            # X recent search allows start_time OR since_id, never both. Fixed
            # time windows also exclude overnight posts from the paid query.
            pending = {'params': params, 'until': end, 'window_start': window[0],
                       'newest': checkpoint.get('since_id')}
        body, rows, reservation = self.request('tweets/search/recent', pending['params'], PAGE_SIZE, POST_PRICE, now)
        analysed = []
        for post in rows:
            if (not isinstance(post, dict) or not valid_id(post.get('id'))
                or post.get('author_id') not in ids or not isinstance(post.get('text'), str)
                or not isinstance(post.get('created_at'), str)):
                raise WatchError('INVALID_X_POST')
            try:
                analysed.append(analyse(post, ids[post['author_id']], self.cfg, now))
            except (ValueError, TypeError, OverflowError):
                raise WatchError('INVALID_X_POST') from None
        token = body.get('meta', {}).get('next_token')
        if token is not None and (not isinstance(token, str) or not token or len(token) > 4096):
            raise WatchError('INVALID_PAGINATION_TOKEN')
        newest = max([int(p['id']) for p in rows] + [int(pending['newest'] or 0)])
        pending['newest'] = str(newest) if newest else None
        if token:
            pending['params']['next_token'] = token
            checkpoint['pending'] = pending
        else:
            checkpoint.update(until=pending['until'], since_id=pending['newest'])
            checkpoint.pop('pending', None)
        # Persist rows and checkpoint together. A crash cannot advance past rows
        # that were not saved. An uncertain HTTP call remains fully accounted.
        with self.ledger.db:
            for row in analysed:
                self.ledger.db.execute('INSERT OR IGNORE INTO posts VALUES (?,?,?,?)',
                    (row['id'], row['published_at'], now, json.dumps(row, ensure_ascii=False)))
            self.ledger.db.execute('INSERT OR REPLACE INTO state VALUES (?,?)', ('checkpoint', json.dumps(checkpoint)))
        self.ledger.settle(reservation, len(rows))

    def report(self, now=None):
        now = time.time() if now is None else now
        rows = self.ledger.rows(now)
        groups = {}
        for row in rows:
            groups.setdefault(row['cluster'], []).append(row['url'])
        for row in rows:
            row['same_text_sources'] = groups[row['cluster']]
        default_status = 'NOT_STARTED' if os.environ.get('X_BEARER_TOKEN') else 'KEY_REQUIRED'
        result = {'generated_at': now, 'status': self.ledger.get('status', default_status),
                  'key_configured': bool(os.environ.get('X_BEARER_TOKEN')),
                  'last_success': self.ledger.get('last_success'),
                  'accounts': self.cfg['news_accounts'] + self.cfg['macro_accounts'],
                  'symbols': list(self.cfg['symbols']), 'query': build_query(self.cfg),
                  'budget': self.ledger.budget(self.cfg, now),
                  'pagination_pending': bool(self.ledger.get('checkpoint', {}).get('pending')),
                  'coverage_gap': self.ledger.get('coverage_gap'),
                  'limitations': ['Selected accounts only; 08:00 New York to XNYS close on trading days',
                    'Polling and search indexing add delay; budget exhaustion stops collection',
                    'X claims are unverified; matching copies do not establish independent confirmation',
                    'Local categories are rules, not AI fact checking or a trading signal',
                    'Set the separate $15 billing-cycle cap and disable auto-recharge in X Developer Console'],
                  'items': rows}
        write_json(self.report_dir / 'x-watch.json', result)
        budget = result['budget']
        lines = ['DualTrade X araştırma raporu',
                 'Güncelleme: ' + datetime.fromtimestamp(now, ISTANBUL).isoformat(timespec='seconds'),
                 'Durum: ' + result['status'],
                 f'Bütçe: bugün ${budget["daily_accounted_usd"]:.3f} / $0.50; '
                 f'son 31 gün ${budget["rolling_31_days_accounted_usd"]:.3f} / $15.00',
                 'X iddiaları kaynak belgelerle doğrulanmadı. Kapsama sınırlıdır.', '']
        emitted = set()
        for row in rows:
            if row['cluster'] in emitted:
                continue
            emitted.add(row['cluster'])
            lines.extend([row['summary_tr'], row['source'] + ' | ' +
                          datetime.fromtimestamp(row['published_at'], ISTANBUL).isoformat(timespec='seconds'),
                          row['text'], *row['same_text_sources'], ''])
        atomic_write(self.report_dir / 'x-watch.txt', '\n'.join(lines))
        return result


def valid_id(value):
    return isinstance(value, str) and re.fullmatch(r'[1-9][0-9]{0,24}', value) is not None


def iso(stamp):
    return datetime.fromtimestamp(stamp, UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Read-only X monitor; authorized maximum $15 per month')
    parser.add_argument('--config', type=Path)
    parser.add_argument('action', choices=('setup-key', 'status', 'check', 'run'))
    args = parser.parse_args(argv)
    from .credentials import load_local_credentials, save_local_credentials
    if args.action == 'setup-key':
        token = getpass.getpass('X Bearer Token (hidden; saved encrypted for this Windows user): ').strip()
        if not token or any(c.isspace() for c in token):
            raise ValueError('A nonempty Bearer Token without whitespace is required')
        save_local_credentials({'X_BEARER_TOKEN': token})
        print('X token saved encrypted. No API call made. Run check to verify with the budget applied.')
        return 0
    cfg = load_settings(args.config)
    load_local_credentials()
    with process_lock(ROOT / 'runtime/x-watch.lock'):
        ledger = Ledger(ROOT / 'runtime/x-watch.sqlite3')
        watch = XWatch(cfg, ledger)
        if args.action == 'run' and not os.environ.get('X_BEARER_TOKEN'):
            watch.check()
            print('X_BEARER_TOKEN is missing. Run setup-key locally first.')
            return 2
        while True:
            result = watch.report() if args.action == 'status' else watch.check()
            if args.action != 'run':
                print(json.dumps({k: v for k, v in result.items() if k != 'items'}, indent=2, ensure_ascii=False))
                return 0 if result['status'] in ('OK', 'NOT_STARTED', 'OUTSIDE_RESEARCH_HOURS', 'DISABLED') else 2
            time.sleep(15)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        pass
