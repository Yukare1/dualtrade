"""Reviewed news admission; research admission and veto eligibility are separate.

Single-source reporting is usable with less weight. Analysis, distributed press
releases, rumours and old reports remain context. No truth guarantee is inferred.
"""
from __future__ import annotations
from collections import Counter
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from html import unescape
from math import isfinite
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

VERSION = 2
MAX_AGE = 72 * 3600
VOTE_MAX_AGE = 48 * 3600
HALF_LIFE = 24 * 3600
MATCH_WINDOW = 12 * 3600
PUBLISHERS = {
    'reuters.com': 'reuters', 'apnews.com': 'ap', 'bloomberg.com': 'bloomberg',
    'cnbc.com': 'cnbc', 'wsj.com': 'dow_jones', 'barrons.com': 'dow_jones',
    'marketwatch.com': 'dow_jones', 'ft.com': 'financial_times',
    'livemint.com': 'mint', 'business-standard.com': 'business_standard',
    'gurufocus.com': 'gurufocus', 'financefeeds.com': 'financefeeds',
    'foxbusiness.com': 'fox_business', 'jamaica-gleaner.com': 'gleaner',
    'manilatimes.net': 'manila_times', 'finance.yahoo.com': 'yahoo_finance',
    'prnewswire.com': 'pr_newswire', 'businesswire.com': 'business_wire',
    'globenewswire.com': 'globe_newswire',
}
PRESS_HOSTS = {'prnewswire.com', 'businesswire.com', 'globenewswire.com'}
SOURCE_REVIEW = {
    'checked_on': '2026-10-22',
    'meaning': 'Publication identity and editorial/distribution role, not a measured accuracy rating.',
    'new_sources': {
        'livemint.com': 'https://www.livemint.com/about-us',
        'business-standard.com': 'https://www.business-standard.com/about-us',
        'gurufocus.com': 'https://www.gurufocus.com/about',
        'financefeeds.com': 'https://financefeeds.com/about/',
        'foxbusiness.com': 'https://www.foxbusiness.com/about-us',
        'jamaica-gleaner.com': 'https://jamaica-gleaner.com/about-us',
        'manilatimes.net': 'https://beta.manilatimes.net/about-us',
        'finance.yahoo.com': 'https://help.yahoo.com/kb/SLN2263.html',
        'prnewswire.com': 'https://www.prnewswire.com/about-pr-newswire/',
        'businesswire.com': 'https://secure.businesswire.com/services',
        'globenewswire.com': 'https://www.globenewswire.com/about',
    },
}
ALIASES = {
    'AAPL': ('apple', 'aapl'), 'MSFT': ('microsoft', 'msft'),
    'NVDA': ('nvidia', 'nvda'), 'AMZN': ('amazon', 'amzn'),
    'META': ('meta', 'facebook'), 'GOOGL': ('alphabet', 'google', 'googl'),
    'TSLA': ('tesla', 'tsla'), 'AMD': ('advanced micro devices', 'amd'),
    'JPM': ('jpmorgan chase', 'jpmorgan', 'jp morgan', 'jpm'),
    'XOM': ('exxon mobil', 'exxonmobil', 'exxon', 'xom'),
    'SPY': ('spy',), 'QQQ': ('qqq',),
}
EVENTS = {
    'earnings_up': r'\b(?:earnings|eps|profit)\b.{0,35}\b(?:beat\w*|exceed\w*)\b|\bbeat\w*\b.{0,20}\b(?:earnings|eps|profit)\b',
    'earnings_down': r'\b(?:earnings|eps|profit)\b.{0,35}\b(?:miss\w*|below)\b|\bmiss\w*\b.{0,20}\b(?:earnings|eps|profit)\b',
    'revenue_up': r'\brevenue\b.{0,25}\b(?:ris\w*|rose|grow\w*|grew|increas\w*)\b',
    'revenue_down': r'\brevenue\b.{0,25}\b(?:fall\w*|fell|declin\w*|drop\w*)\b',
    'guidance_up': r'\b(?:rais\w*|lift\w*)\b.{0,25}\b(?:guidance|outlook)\b',
    'guidance_down': r'\b(?:cut\w*|lower\w*)\b.{0,25}\b(?:guidance|outlook)\b',
    'bankruptcy': r'\b(?:bankruptcy|chapter 11)\b',
    'layoffs': r'\b(?:layoffs|job cuts|cut\w*.{0,20}\bjobs)\b',
    'product_launch': r'\b(?:introduc\w*|launch\w*|unveil\w*|releas\w*)\b',
    'partnership': r'\b(?:partnership|partners? with|collaborat\w*|joint venture)\b',
    'contract': r'\b(?:contract|order|agreement)\b',
    'corporate_action': r'\b(?:dividend|buyback|repurchas\w*|stock split|merger|acquisition|acquire\w*|ipo)\b',
    'regulatory_legal': r'\b(?:lawsuit|sues|sued|regulat\w*|approval|antitrust|investigation|fine|recall)\b',
    'management': r'\b(?:ceo|cfo|appoint\w*|resign\w*|leadership)\b',
    'operations': r'\b(?:demand|production|deliveries|supply|capacity|factory)\b',
    'earnings_results': r'\b(?:earnings|eps|revenue|profit|quarter|q[1-4])\b',
}
OPPOSITES = {'earnings_up': 'earnings_down', 'revenue_up': 'revenue_down', 'guidance_up': 'guidance_down'}
OPPOSITES.update({v: k for k, v in list(OPPOSITES.items())})
RISK_PATTERNS = {
    'SATIRE_OR_MANIPULATION': r'\b(?:satire|parody|sarcasm|april fools?|to the moon|guaranteed returns?|pump and dump)\b|(?<!\S)/s\b|🚀',
    'UNCONFIRMED_CLAIM': r'\b(?:rumou?rs?|unconfirmed|allegedly|sources say|people familiar with the matter)\b',
    'DISPUTED_OR_CORRECTED': r'\b(?:denies|denied|denial|hoax|debunk\w*|misleading|correction|corrected|retract\w*|no evidence|false report|fake news)\b',
}
ANALYSIS = r'\b(?:opinion|commentary|analysis|analysts?|targets?|upgrade\w*|downgrade\w*|rating|predict\w*|might|could|should|would)\b|\?'
STOP = set('a an the and or of for to in on at as with by from its it is are was were has have after before '
           'says said reports reported announces announced results company shares stock new latest today'.split())


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def _text(value):
    return unicodedata.normalize('NFKC', unescape(value if isinstance(value, str) else '')).casefold()


def canonical_url(value):
    """Exact HTTPS hosts; reject credentials, unreviewed ports and homepage URLs."""
    try:
        if not isinstance(value, str) or re.search(r'[\s\\\x00-\x1f]', value): return '', ''
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or parsed.username or parsed.password
                or parsed.port not in (None, 443) or not parsed.hostname): return '', ''
        host = parsed.hostname.lower()
        host = host[4:] if host.startswith('www.') else host
        if not parsed.path.strip('/'): return '', ''
        query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                 if not k.lower().startswith('utm_') and k.lower() not in ('ref', 'fbclid', 'gclid')]
        return host, urlunsplit(('https', host, parsed.path.rstrip('/'), urlencode(sorted(query)), ''))
    except (ValueError, UnicodeError):
        return '', ''


def _anchors(title):
    title = re.sub(r'\b(?:q[1-4]|20\d{2})\b', '', title)
    values = []
    for m in re.finditer(r'([$€£]?)(\d[\d,]*(?:\.\d+)?)(?:\s*(%|percent|billion|million|trillion|bn|mln))?', title):
        try: value = str(Decimal(m[2].replace(',', '')).normalize())
        except InvalidOperation: continue
        unit = {'percent': '%', 'bn': 'billion', 'mln': 'million'}.get(m[3], m[3] or '')
        values.append((m[1], value, unit))
    return tuple(sorted(values))


def _features(row, now):
    item = dict(row)
    item['reliable'] = False
    c = {'version': VERSION, 'evaluated_at': now, 'status': 'unverified', 'eligible': False,
         'veto_eligible': False, 'reasons': [], 'warnings': [], 'source_group': '', 'source_kind': '',
         'supporting_urls': [], 'supporting_groups': [], 'event_id': '', 'weight': 0., 'age_hours': None}
    item['credibility'] = c
    host, url = canonical_url(row.get('url'))
    c.update(canonical_url=url, source_group=PUBLISHERS.get(host, ''))
    title = _text(row.get('title'))
    # Feed summaries, not arbitrary full-page boilerplate. A lone "will", "may"
    # or "not" is not a rumour, parody or denial detector.
    context = ' '.join((title, _text(row.get('description')) or _text(row.get('snippet'))))[:6000]
    ticker = row.get('ticker') if isinstance(row.get('ticker'), str) else ''
    stamp, retrieved, score = row.get('time'), row.get('retrieved_at'), row.get('score')
    if not url: c['reasons'].append('INVALID_ARTICLE_URL')
    if not c['source_group']: c['reasons'].append('UNKNOWN_SOURCE_DOMAIN')
    if row.get('provider') != 'Marketaux': c['reasons'].append('UNKNOWN_PROVIDER')
    if not (_number(stamp) and now - MAX_AGE <= stamp <= now and _number(retrieved) and stamp <= retrieved <= now):
        c['reasons'].append('STALE_OR_INVALID_TIMESTAMPS')
    else: c['age_hours'] = (now-stamp)/3600
    if not (_number(score) and -1 <= score <= 1): c['reasons'].append('INVALID_SENTIMENT')
    if not title.strip(): c['reasons'].append('MISSING_HEADLINE')
    named = any(re.search(r'\b' + re.escape(a) + r'\b', title) for a in ALIASES.get(ticker, ()))
    market_context = ticker in ('SPY', 'QQQ') and re.search(r'\b(?:nyse|nasdaq|s&p|stocks?|markets?|sector|inflation|fed|interest rates)\b', title)
    if not named and not market_context: c['reasons'].append('AMBIGUOUS_OR_MISSING_SUBJECT')
    indirect = any(re.search(r'\b' + re.escape(a) + r'\s+(?:suppliers?|rivals?|partners?(?!\s+with\b)|competitors?)\b', title)
                   for a in ALIASES.get(ticker, ()))
    for reason, pattern in RISK_PATTERNS.items():
        if re.search(pattern, context):
            (c['reasons'] if reason == 'SATIRE_OR_MANIPULATION' else c['warnings']).append(reason)
    press = (host in PRESS_HOSTS
             or bool(re.search(r'/(?:pr-newswire|press-releases?|news-releases?|tmt-newswire)(?:/|$)', url))
             or bool(re.search(r'\b(?:source:|provided by|distributed by|via)\s*(?:pr newswire|business wire|globenewswire)\b', context)))
    c['source_kind'] = 'press_release' if press else 'editorial'
    for group, pattern in (
        ('reuters', r'\(reuters\)|\b(?:reporting by|according to|source:)\s+reuters\b'),
        ('ap', r'\(ap\)|\b(?:by|source:)\s+(?:the\s+)?associated press\b'),
        ('bloomberg', r'\(bloomberg\)|\b(?:by|source:)\s+bloomberg\b'),
    ):
        if host in PUBLISHERS and re.search(pattern, context): c['source_group'] = group
    if press: c['source_group'] = 'press_distribution'
    normal = title
    for alias in sorted(ALIASES.get(ticker, ()), key=len, reverse=True):
        normal = re.sub(r'\b' + re.escape(alias) + r'\b', ticker.lower(), normal)
    tokens = set(re.findall(r'[a-z0-9]+', normal)) - STOP
    kinds = [name for name, pattern in EVENTS.items() if re.search(pattern, normal)]
    event = kinds[0] if kinds else 'general_reporting'
    if re.search(ANALYSIS, title) or (re.search(r'\b(?:stock|shares)\b', title) and len(_anchors(normal)) >= 3):
        event = 'analyst_rating' if re.search(r'\b(?:target|upgrade|downgrade|rating)\b', title) else 'analysis'
    if event == 'general_reporting': c['warnings'].append('GENERAL_REPORTING_NO_STRUCTURED_CLAIM')
    c.update(claim_type=event, claim_types=kinds)
    feature = {'item': item, 'title': ' '.join(re.findall(r'[a-z0-9]+', normal)), 'tokens': tokens,
               'numbers': _anchors(normal), 'periods': tuple(sorted(re.findall(r'\b(?:q[1-4]|20\d{2})\b', normal))),
               'event': event, 'host': host}
    if c['reasons']:
        c['status'] = 'unverified' if set(c['reasons']) == {'UNKNOWN_SOURCE_DOMAIN'} else 'excluded'
        return feature
    c.update(eligible=True, status='single_source', reasons=['REVIEWED_SOURCE_REPORTING'],
             veto_eligible=True, weight=.7 * 2 ** (-(now-stamp)/HALF_LIFE))
    if press:
        c.update(status='press_release_context', veto_eligible=False, reasons=['PRESS_RELEASE_NOT_INDEPENDENT'])
    elif event in ('analysis', 'analyst_rating'):
        c.update(status='analysis_context', veto_eligible=False, reasons=['ANALYSIS_NOT_CONFIRMED_EVENT'])
    if not named or indirect:
        c.update(status='context_only', veto_eligible=False)
        c['reasons'].append('MARKET_OR_RELATED_COMPANY_CONTEXT')
    if any(w in c['warnings'] for w in ('UNCONFIRMED_CLAIM', 'DISPUTED_OR_CORRECTED')):
        c.update(status='context_only', veto_eligible=False)
        c['reasons'].append('CLAIM_REQUIRES_CONFIRMATION')
    if now-stamp > VOTE_MAX_AGE:
        c.update(status='context_only', veto_eligible=False)
        c['reasons'].append('OLDER_THAN_ACTIVE_WINDOW')
    return feature


def _comparable(a, b, threshold=.5):
    x, y = a['item'], b['item']
    return (x.get('ticker') == y.get('ticker') and _number(x.get('time')) and _number(y.get('time'))
            and abs(x['time']-y['time']) <= MATCH_WINDOW and a['periods'] == b['periods']
            and len(a['tokens'] & b['tokens'])/max(1, len(a['tokens'] | b['tokens'])) >= threshold)


def assess_news(rows, now):
    features = [_features(r, now) for r in rows if isinstance(r, dict)]
    accepted = [f for f in features if f['item']['credibility']['eligible']]
    conflicts = set()
    for a in accepted:
        if not a['item']['credibility']['veto_eligible']: continue
        for b in accepted:
            bc = b['item']['credibility']
            if bc['source_kind'] != 'editorial' or not _comparable(a, b, .4): continue
            opposite = b['event'] == OPPOSITES.get(a['event'])
            same_directional = b['event'] == a['event'] and a['event'] in OPPOSITES
            denied = 'DISPUTED_OR_CORRECTED' in bc['warnings'] and b['event'] == a['event']
            if opposite or (same_directional and b['numbers'] != a['numbers']) or denied: conflicts.add(id(a))
    # Copies and independent coverage share a story identity, so copies cannot
    # become additional votes. Editorial independence is evaluated separately.
    parent = list(range(len(accepted)))
    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]; index = parent[index]
        return index
    for i, a in enumerate(accepted):
        for j in range(i):
            b = accepted[j]; ac, bc = a['item']['credibility'], b['item']['credibility']
            if a['item']['ticker'] != b['item']['ticker']: continue
            same_url = ac['canonical_url'] == bc['canonical_url']
            same_id = a['item'].get('cluster') and a['item'].get('cluster') == b['item'].get('cluster')
            same_claim = a['event'] == b['event'] and a['numbers'] == b['numbers'] and _comparable(a, b)
            if same_url or same_id or a['title'] == b['title'] or same_claim: parent[find(i)] = find(j)
    stories = {}
    for i, f in enumerate(accepted): stories.setdefault(find(i), []).append(f)
    for story in stories.values():
        key = min(f['item']['credibility']['canonical_url'] for f in story)
        event_id = sha256((str(story[0]['item']['ticker']) + ':' + key).encode()).hexdigest()
        oldest = min(f['item']['time'] for f in story)
        if now-oldest > VOTE_MAX_AGE:
            for f in story:
                c = f['item']['credibility']
                if c['veto_eligible']:
                    c.update(status='context_only', veto_eligible=False)
                    c['reasons'].append('ORIGINAL_STORY_OUTSIDE_ACTIVE_WINDOW')
        independent = []
        for f in sorted(story, key=lambda f: (f['item']['time'], f['item']['credibility']['canonical_url'])):
            c = f['item']['credibility']
            if not c['veto_eligible'] or id(f) in conflicts: continue
            if any(c['source_group'] == p['item']['credibility']['source_group'] or f['title'] == p['title']
                   or (f['item'].get('cluster') and f['item'].get('cluster') == p['item'].get('cluster'))
                   or len(f['tokens'] & p['tokens'])/max(1, len(f['tokens'] | p['tokens'])) >= .9
                   for p in independent): continue
            independent.append(f)
        for f in story:
            c = f['item']['credibility']; c['event_id'] = event_id
            if c['veto_eligible']:
                c.update(supporting_urls=sorted({p['item']['credibility']['canonical_url'] for p in independent}),
                         supporting_groups=sorted({p['item']['credibility']['source_group'] for p in independent}))
            if id(f) in conflicts:
                c.update(status='conflicting', eligible=False, veto_eligible=False, weight=0., reasons=['CONFLICTING_REPORTING'])
            elif c['veto_eligible'] and len(independent) >= 2:
                c.update(status='corroborated_reporting', reasons=['MATCHED_INDEPENDENT_REPORTING'],
                         weight=2 ** (-(now-f['item']['time'])/HALF_LIFE),
                         supporting_urls=sorted({p['item']['credibility']['canonical_url'] for p in independent}),
                         supporting_groups=sorted({p['item']['credibility']['source_group'] for p in independent}))
            f['item']['reliable'] = c['veto_eligible']
    return [f['item'] for f in features]


def news_summary(rows):
    return {'version': VERSION, 'counts': dict(Counter(r['credibility']['status'] for r in rows)),
            'accepted_articles': len({r['credibility']['canonical_url'] for r in rows if r['credibility']['eligible']}),
            'eligible_events': len({r['credibility']['event_id'] for r in rows if r['credibility']['eligible']}),
            'veto_events': len({r['credibility']['event_id'] for r in rows if r['credibility']['veto_eligible']}),
            'policy': {'retention_hours': MAX_AGE/3600, 'active_hours': VOTE_MAX_AGE/3600, 'half_life_hours': HALF_LIFE/3600},
            'limitation': 'Research admission is not factual proof; context-only articles cannot veto.'}


def news_report(rows, now):
    assessed = assess_news(rows, now)
    return {'evaluated_at': now, **news_summary(assessed), 'articles': [
        {key: r.get(key) for key in ('ticker', 'title', 'url', 'provider', 'time', 'retrieved_at', 'score', 'credibility')}
        for r in assessed]}


def news_votes(rows, now):
    events = {}
    for row in assess_news(rows, now):
        c = row['credibility']
        if c['veto_eligible'] and c['canonical_url'] in c['supporting_urls']:
            events.setdefault(c['event_id'], []).append(row)
    votes = []
    for event_id, rows in events.items():
        groups = {}
        for row in sorted(rows, key=lambda r: (r['time'], r['credibility']['canonical_url'])):
            groups.setdefault(row['credibility']['source_group'], row)
        scores = [r['score'] for r in groups.values()]
        if min(scores) <= -.5 and max(scores) >= .5: continue
        earliest = min(r['time'] for r in rows)
        quality = 1. if any(r['credibility']['status'] == 'corroborated_reporting' for r in rows) else .7
        votes.append({'cluster': event_id, 'time': earliest, 'score': sum(scores)/len(scores),
                      'weight': quality * 2 ** (-(now-earliest)/HALF_LIFE), 'reliable': True,
                      'source_groups': sorted(groups), 'publisher': ','.join(sorted(groups))})
    return votes


def policy_signature():
    from pathlib import Path
    return sha256(Path(__file__).read_bytes()).hexdigest()
