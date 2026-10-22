"""Evidence-bound agent assessments, reduced to the existing native entry gates.

There is no model or broker API here. Codex reads documents separately; this
boundary validates its structured output before the collector can consume it.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3

from .config import ROOT
from .investment import number
from .material_news import numeric_anchor
from .storage import fingerprint

VERSION = 1
PART_SIZE = 6000
MAX_REVIEW_AGE = 24 * 3600
MIN_CONFIDENCE = .8


def part_count(text):
    return max(1, (len(text) + PART_SIZE - 1) // PART_SIZE)


def validate_assessment(review, event, documents, receipts, now):
    """Validate evidence and economic inputs; confidence is not measured accuracy."""
    a = review.get('assessment')
    if not isinstance(a, dict) or a.get('schema') != VERSION:
        raise ValueError('AI_ASSESSMENT_SCHEMA_REQUIRED')
    if a.get('direction') not in ('positive', 'negative', 'neutral', 'uncertain'):
        raise ValueError('AI_DIRECTION_INVALID')
    if (not number(a.get('confidence')) or not 0 <= a['confidence'] <= 1 or
            type(a.get('material')) is not bool or
            a.get('horizon') not in ('intraday', 'multi_day', 'long_term') or
            not isinstance(a.get('rationale_tr'), str) or not 20 <= len(a['rationale_tr']) <= 4000):
        raise ValueError('AI_ASSESSMENT_INCOMPLETE')
    published = event['published']
    # A late full review can discharge an unread obligation, but must never
    # rejuvenate its directional signal or its economic event timestamps.
    if not number(published) or not published <= now:
        raise ValueError('AI_EVENT_TIME_INVALID')
    manifests = review.get('documents_read')
    if not isinstance(manifests, list) or not manifests:
        raise ValueError('AI_FULL_DOCUMENT_MANIFEST_REQUIRED')
    by_url = {d['url']: d for d in documents}
    seen = set()
    for m in manifests:
        if not isinstance(m, dict):
            raise ValueError('AI_DOCUMENT_MANIFEST_INVALID')
        d = by_url.get(m.get('url'))
        if (not d or d['hash'] != m.get('sha256') or d['url'] in seen or
                type(m.get('parts')) is not int or m['parts'] != part_count(d['text'])):
            raise ValueError('AI_DOCUMENT_MANIFEST_INVALID')
        if not all((d['url'], d['hash'], p) in receipts for p in range(1, m['parts'] + 1)):
            raise ValueError('AI_DOCUMENT_PARTS_NOT_READ')
        seen.add(d['url'])
    # Root and every same-filing SEC exhibit must be served in full. A cover
    # page pointing to a results release is not a complete financial review.
    required = {event['url']}
    if event['source_id'].startswith('SEC:'):
        for d in documents:
            if d['url'] in seen:
                required.update(json.loads(d['links']))
    if not required <= seen or review.get('evidence_url') not in seen:
        raise ValueError('AI_ROOT_OR_EXHIBITS_UNREAD')
    facts = review['facts']
    indexes = a.get('evidence_facts')
    if (not isinstance(indexes, list) or not indexes or
            any(type(i) is not int or not 0 <= i < len(facts) for i in indexes)):
        raise ValueError('AI_ASSESSMENT_EVIDENCE_REQUIRED')
    economics = a.get('economics', [])
    if not isinstance(economics, list) or len(economics) > 3:
        raise ValueError('AI_ECONOMICS_INVALID')
    if event['source_id'].startswith('NEWS:'):
        data = json.loads(event['data'])
        if economics:
            raise ValueError('AI_ECONOMICS_REQUIRES_ISSUER_OR_SEC')
        if data.get('source_role') in ('analysis_context', 'press_release_context', 'context_only') and a['material']:
            raise ValueError('AI_CONTEXT_CANNOT_CREATE_DIRECTIONAL_VETO')
    out = []
    quoted = {review['evidence_url']: sum(len(f['quote'].split()) for f in facts)}
    for e in economics:
        if not isinstance(e, dict) or e.get('kind') not in ('guidance', 'contract_win', 'contract_loss'):
            raise ValueError('AI_ECONOMIC_KIND_INVALID')
        doc = by_url.get(e.get('source'))
        quote = e.get('quote')
        if (e.get('source') not in seen or not isinstance(quote, str) or
                len(quote) < 40 or quote not in doc['text']):
            raise ValueError('AI_ECONOMIC_QUOTE_REQUIRED')
        quoted[e['source']] = quoted.get(e['source'], 0) + len(quote.split())
        if quoted[e['source']] > 25:
            raise ValueError('AI_USE_SHORT_ECONOMIC_QUOTES')
        key = e.get('event_key')
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{5,100}', key):
            raise ValueError('AI_STABLE_EVENT_KEY_REQUIRED')
        if e['kind'] == 'guidance':
            if (e.get('metric') not in ('net_income', 'fcf') or e.get('unit') not in ('USD', 'USD million', 'USD billion') or
                    not isinstance(e.get('period'), str) or not e['period'] or e['period'] not in quote):
                raise ValueError('AI_COMPARABLE_GUIDANCE_REQUIRED')
            fields = ('before', 'after')
            unit = e['unit']
        else:
            if (e.get('months') != 12 or e.get('firm') is not True or not e.get('period_text') or
                    e['period_text'] not in quote or not re.search(r'\b(?:12|twelve)[ -]months?\b', e['period_text'], re.I) or
                    not re.search(r'\b(?:binding|committed|non.cancelable|firm)\b', quote, re.I)):
                raise ValueError('AI_FIRM_TWELVE_MONTH_REVENUE_REQUIRED')
            fields, unit = ('revenue_impact',), 'USD'
        for field in fields:
            anchor = e.get(field + '_text')
            if not numeric_anchor(anchor, e.get(field), unit) or anchor not in quote or e[field] < 0:
                raise ValueError('AI_ECONOMIC_NUMBER_WITHOUT_ANCHOR')
        if e['kind'] == 'guidance' and quote.find(e['before_text']) >= quote.find(e['after_text']):
            raise ValueError('AI_GUIDANCE_COMPARISON_ORDER_INVALID')
        if event['ticker'] == 'MACRO':
            raise ValueError('AI_MACRO_CANNOT_CREATE_COMPANY_ECONOMICS')
        # Explicit fields only: model output cannot inject reference prices,
        # risk, verified flags, timestamps, executable actions or arbitrary URLs.
        names = ('kind', 'metric', 'unit', 'period', 'before', 'after', 'before_text', 'after_text',
                 'revenue_impact', 'revenue_impact_text', 'months', 'firm', 'period_text', 'source', 'quote', 'event_key')
        clean = {k: e[k] for k in names if k in e}
        clean.update(ticker=event['ticker'], event_id=fingerprint({'ticker': event['ticker'], 'key': key}),
                     published_at=published, first_observed=now, primary_verified=True,
                     document_sha256=doc['hash'], reviewer='codex-full-text', ai_review_id=event['id'])
        out.append(clean)
    return {k: a[k] for k in ('schema', 'direction', 'confidence', 'material', 'horizon', 'rationale_tr', 'evidence_facts')} | {
        'economics': out, 'published_at': published, 'expires_at': published + MAX_REVIEW_AGE}


def read_state(now, directory=None):
    """Read the authoritative SQLite state, never a user-editable report cache."""
    directory = Path(directory) if directory is not None else ROOT / 'runtime/official-news'
    path = directory / 'state.sqlite3'
    if not path.exists():
        return {'healthy': False, 'error': 'AI_NEWS_STORE_UNAVAILABLE', 'events': [], 'sources': {}}
    db = None
    try:
        db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        rows = db.execute("SELECT id,ticker,url,published,observed,state,analysis FROM events WHERE published<=? AND (published>=? OR state NOT IN ('ignored','baseline')) ORDER BY published,observed,id",
                          (now, now - 90 * 86400)).fetchall()
        source_rows = db.execute('SELECT id,data FROM source_state').fetchall()
        events = []
        for r in rows:
            row = dict(r)
            row['analysis'] = json.loads(row['analysis']) if row['analysis'] else None
            events.append(row)
        return {'healthy': True, 'error': None, 'events': events,
                'sources': {r['id']: json.loads(r['data']) for r in source_rows}}
    except (sqlite3.Error, ValueError, TypeError):
        return {'healthy': False, 'error': 'AI_NEWS_STORE_INVALID', 'events': [], 'sources': {}}
    finally:
        if db is not None:
            db.close()


def decision(state, ticker, now):
    """Pending/uncertain material news waits; opinion alone never closes a holding."""
    out = dict(ready=bool(state['healthy']), veto_buy=False, veto_sell=False,
               pending=[], reviews=[], material_events=[], reasons=[])
    if not state['healthy']:
        out['reasons'].append(state['error'])
        return out
    # SEC coverage is required per company; Fed policy is shared with all books.
    # Sources with a direct issuer feed are checked as well if configured.
    required = ['SEC:' + ticker, 'FED:press', 'FED:speeches']
    from .official_news import COMPANIES
    if ticker in COMPANIES and COMPANIES[ticker][1]:
        required.append('IR:' + ticker)
    for key in required:
        if key.startswith('SEC:') and ticker not in COMPANIES:
            continue
        s = state['sources'].get(key, {})
        if (s.get('ok') is not True or not number(s.get('last_success')) or
                not 0 <= now - s['last_success'] <= 3600 or now > s.get('next_check', 0) + 300):
            out['ready'] = False
            out['reasons'].append('AI_SOURCE_UNHEALTHY:' + key)
    for event in state['events']:
        if event['ticker'] not in (ticker, 'MACRO'):
            continue
        review = event['analysis'] or {}
        a = review.get('assessment', {})
        admitted = bool(review.get('eligible_for_trading') is True and a.get('schema') == VERSION and
                       number(review.get('reviewed_at')) and event['published'] <= review['reviewed_at'] <= now)
        current = bool(admitted and
                       number(a.get('expires_at')) and now <= a['expires_at'])
        # Economic thesis exits/reductions remain obligations after the short
        # directional-veto window; material_news applies its own 72h/90d rules.
        if admitted and event['ticker'] == ticker and a['material'] and a['confidence'] >= MIN_CONFIDENCE:
            out['material_events'].extend(a.get('economics', []))
        recent = 0 <= now - event['published'] <= MAX_REVIEW_AGE
        # Expiry only removes directional authority. Unread/manual/expired queue
        # records and unresolved material uncertainty remain entry obligations.
        uncertainty = bool(admitted and a['material'] and
                           (a['direction'] == 'uncertain' or a['confidence'] < MIN_CONFIDENCE))
        if event['state'] not in ('ignored', 'baseline') and (not admitted or uncertainty or (recent and not current)):
            out['ready'] = False
            out['pending'].append(event['id'])
        if uncertainty:
            out['reasons'].append('AI_MATERIAL_UNCERTAINTY:' + event['id'])
        if not current:
            continue
        out['reviews'].append({'event_id': event['id'], 'source': event['url'], **{k: a[k] for k in (
            'direction', 'confidence', 'material', 'horizon', 'rationale_tr', 'expires_at')}})
        if a['material'] and a['confidence'] >= MIN_CONFIDENCE:
            out['veto_buy'] |= a['direction'] == 'negative'
            out['veto_sell'] |= a['direction'] == 'positive'
    if out['pending']:
        out['reasons'].append('AI_IMPORTANT_NEWS_AWAITING_FULL_REVIEW')
    return out


def apply(snapshot, state, now):
    """Use existing protocol-4 flags; native execution/risk remains authoritative."""
    report = {}
    for c in snapshot['contracts']:
        d = decision(state, c['ticker'], now)
        c['ai_news'] = d
        c['veto_buy'] |= d['veto_buy']
        c['veto_sell'] |= d['veto_sell']
        if not d['ready']:
            c['entry_allowed'] = False
            c['investment_event_ok'] = False
            c['swing_sources_ok'] = False
        report[c['ticker']] = d
    snapshot['ai_news'] = report
