"""Material company events: source evidence, economics, and durable event identity.

Structured reviews are checked against the actual primary document. Headlines
without measurable evidence remain context; sentiment never invents economics.
"""
from __future__ import annotations
from hashlib import sha256
from urllib.parse import urlsplit
import re
from .investment import number

ISSUER_HOSTS = {'AAPL': {'investor.apple.com', 'www.apple.com'},
 'MSFT': {'www.microsoft.com', 'news.microsoft.com'},
 'NVDA': {'investor.nvidia.com', 'nvidianews.nvidia.com'},
 'AMZN': {'ir.aboutamazon.com', 'www.aboutamazon.com'},
 'META': {'investor.atmeta.com', 'about.fb.com'},
 'TSLA': {'ir.tesla.com', 'www.tesla.com'},
 'AMD': {'ir.amd.com', 'www.amd.com'},
 'XOM': {'investor.exxonmobil.com', 'corporate.exxonmobil.com'}}


def numeric_anchor(anchor, value, unit='USD'):
    if not isinstance(anchor,str) or not number(value): return False
    match=re.fullmatch(r'\s*[$€£]?\s*(-?\d+(?:\.\d+)?)\s*(trillion|billion|million|thousand|bn|mln|m|k)?\s*',anchor.lower().replace(',',''))
    if not match:return False
    scales={'trillion':1e12,'billion':1e9,'bn':1e9,'million':1e6,'mln':1e6,'m':1e6,'thousand':1e3,'k':1e3}
    parsed=float(match[1])
    if match[2]:
        unit_scale=next((scale for name,scale in scales.items() if re.search(r'\b'+name+r'\b',unit.lower())),1)
        parsed=parsed*scales[match[2]]/unit_scale
    return abs(parsed-value)<=max(1e-9,abs(value)*1e-9)


def primary_url(url, ticker, cik):
    try:
        p = urlsplit(url)
        if p.scheme != 'https' or p.username or p.password or p.port not in (None, 443) or p.query or p.fragment:
            return False
        if p.hostname == 'www.sec.gov':
            return bool(re.match(r'/Archives/edgar/data/'+str(int(cik))+r'/\d+/[^/]+\.(?:htm|html|txt)$', p.path))
        return p.hostname in ISSUER_HOSTS.get(ticker, set()) and len(p.path.strip('/')) > 5
    except (ValueError, TypeError): return False


def verify_review(review, cfg, providers, now):
    """Only an explicit complete review can supply unstructured economic context."""
    ticker = review.get('ticker')
    if ticker not in ISSUER_HOSTS or not review.get('reviewer') or review.get('reviewed') is not True:
        raise ValueError('COMPLETE_PRIMARY_EVENT_REVIEW_REQUIRED')
    if not primary_url(review.get('source', ''), ticker, cfg['symbols'][ticker]['cik']):
        raise ValueError('PRIMARY_SOURCE_NOT_ALLOWED')
    published = review.get('published_at')
    if not number(published) or not 0 <= now-published <= 90*86400:
        raise ValueError('INVALID_EVENT_TIME')
    response = providers.request('primary', review['source'], headers={'User-Agent': providers.key('SEC_USER_AGENT')}, allow_redirects=False)
    if len(response.content) > 4_000_000: raise ValueError('PRIMARY_DOCUMENT_TOO_LARGE')
    from bs4 import BeautifulSoup
    text = ' '.join(BeautifulSoup(response.text, 'html.parser').get_text(' ', strip=True).split())
    quote = ' '.join(review.get('quote', '').split())
    if len(quote) < 40 or quote not in text: raise ValueError('PRIMARY_QUOTE_NOT_FOUND')
    # Every economic input must be anchored to an exact token in the reviewed quote.
    for field in ('before', 'after', 'revenue_impact'):
        if field in review:
            anchor = review.get(field+'_text')
            if not numeric_anchor(anchor,review[field],review.get('unit','USD') if field!='revenue_impact' else 'USD') or anchor not in quote:
                raise ValueError('ECONOMIC_INPUT_WITHOUT_PRIMARY_ANCHOR')
    if review.get('kind') == 'guidance' and not (review.get('period') and review.get('unit') and review.get('metric') in ('net_income', 'fcf')):
        raise ValueError('COMPARABLE_GUIDANCE_REQUIRED')
    identity = review.get('event_key')
    if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{5,100}', identity):
        raise ValueError('STABLE_EVENT_KEY_REQUIRED')
    out = dict(review)
    out.update(event_id=sha256((ticker+':'+identity).encode()).hexdigest(),
               first_observed=now, primary_verified=True, document_sha256=sha256(response.content).hexdigest())
    # Reference is obtained from broker history at import, never accepted from review.
    out.pop('reference_price', None)
    return out


def classify(event, metrics):
    if event.get('primary_verified') is not True: return 'context'
    kind = event.get('kind')
    if kind in ('bankruptcy', 'non_reliance', 'core_business_ban'): return 'exit'
    if kind == 'guidance':
        before, after = event.get('before'), event.get('after')
        if not number(before) or not number(after) or before <= 0: return 'context'
        change = after/before-1
        if change <= -.25+1e-10: return 'exit'
        if change <= -.10+1e-10: return 'reduce'
        if change >= .10-1e-10: return 'positive'
    if kind in ('contract_win', 'contract_loss'):
        amount, revenue = event.get('revenue_impact'), metrics.get('revenue')
        if not number(amount) or amount < 0 or not number(revenue) or revenue <= 0: return 'context'
        if event.get('months') != 12 or event.get('firm') is not True: return 'context'
        ratio = amount/revenue
        if kind == 'contract_loss':
            if ratio >= .25-1e-10: return 'exit'
            if ratio >= .10-1e-10: return 'reduce'
        elif ratio >= .10-1e-10: return 'positive'
    return 'context'


def assess(events, ticker, metrics, now, coverage_ok=False):
    selected = {}
    for event in events:
        if event.get('ticker') != ticker or not event.get('event_id'): continue
        if not number(event.get('published_at')) or not number(event.get('first_observed')): continue
        if event['published_at'] > now or event['first_observed'] > now: continue
        if now-event['published_at'] > 90*86400: continue
        selected[event['event_id']] = event
    out = {'action': 'hold', 'event_id': '', 'positive': {}, 'conflict': False,
           'coverage_ok': coverage_ok, 'events': []}
    for event in selected.values():
        action = classify(event, metrics)
        out['events'].append({'event_id': event['event_id'], 'action': action, 'source': event.get('source'), 'published_at': event['published_at']})
        if event.get('conflicting') or event.get('corrected'):
            out['conflict'] = True; continue
        if action == 'exit' or action == 'reduce' and out['action'] != 'exit':
            out.update(action=action, event_id=event['event_id'])
        elif action == 'positive' and now-event['published_at'] <= 72*3600:
            if event['published_at'] >= out['positive'].get('published_at', 0): out['positive'] = event
    return out
