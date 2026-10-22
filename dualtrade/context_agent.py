"""First-party document agent replaces the retired Reddit/FinBERT entry policy.

Health means current source coverage and completed required document reviews;
it is neither measured prediction accuracy nor a synthetic social-media feed.
"""
from .ai_news import decision, read_state


def evaluate(pool, now, state=None):
    state = read_state(now) if state is None else state
    decisions = {ticker: decision(state, ticker, now) for ticker in pool}
    # The shared gate covers only shared feeds and macro documents. Issuer
    # problems are retained on each contract by ai_news.apply, not broadcast.
    shared = decision(state, 'MACRO', now)
    issues = sorted(set(shared['reasons']))
    ready = bool(pool) and shared['ready'] and any(d['ready'] for d in decisions.values())
    return {'provider': 'official_agent', 'ready': ready,
            'source_store_healthy': bool(state['healthy']),
            'shared_ready': shared['ready'], 'decisions': decisions, 'issues': issues,
            'symbol_issues': {t: d['reasons'] for t, d in decisions.items() if not d['ready']},
            'social_media_coverage': False, 'win_probability': None}
