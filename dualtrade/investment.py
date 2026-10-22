"""Deterministic, point-in-time investment policy. No execution side effects."""
from __future__ import annotations

from datetime import date, datetime, timezone
from math import isfinite

UNIVERSE = ('AAPL', 'MSFT', 'NVDA', 'AMZN', 'META', 'TSLA', 'AMD', 'XOM')
POLICY_VERSION = 1
DEFAULTS = {'enabled': True, 'nominal_fraction': .25, 'early_fraction': .125,
            'early_risk': 20., 'normal_score': 70, 'early_score': 60,
            'discount': .15, 'stop_atr': 4., 'financing_days': 180,
            'max_cost_fraction': .05, 'minimum_rr': 1.5,
            'max_period_age_days': 150, 'max_source_age': 86400,
            'news_max_age': 72*3600, 'early_review_days': 90,
            'events_file': 'runtime/material-events.json'}


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def options(cfg):
    return {**DEFAULTS, **cfg.get('investment', {})}


def _tier(value, high, low, reverse=False):
    if reverse:
        return 20 if value <= high else 10 if value <= low else 0
    return 20 if value >= high else 10 if value >= low else 0


def evaluate(metrics, now, cfg=None):
    """Eight aligned quarters are mandatory; missing evidence is never zero."""
    settings = options(cfg or {})
    result = {'policy_version': POLICY_VERSION, 'ready': False, 'score': None,
              'fair_value': None, 'reasons': [], 'components': {}, 'metrics': metrics}
    required = ('revenue', 'previous_revenue', 'net_income', 'previous_net_income',
                'operating_income', 'fcf', 'cash', 'debt', 'shares')
    if not metrics.get('complete') or not all(number(metrics.get(k)) for k in required):
        result['reasons'].append('INSUFFICIENT_DATA'); return result
    try:
        age = (datetime.fromtimestamp(now, timezone.utc).date() - date.fromisoformat(metrics['period_end'])).days
    except (KeyError, ValueError):
        result['reasons'].append('INVALID_PERIOD'); return result
    if not 0 <= age <= settings['max_period_age_days'] or not 0 <= now-metrics.get('checked_at', 0) <= settings['max_source_age']:
        result['reasons'].append('FUNDAMENTALS_STALE'); return result
    if min(metrics['revenue'], metrics['previous_revenue'], metrics['shares']) <= 0:
        result['reasons'].append('INVALID_DENOMINATOR'); return result
    if min(metrics['cash'], metrics['debt']) < 0:
        result['reasons'].append('INVALID_BALANCE_SHEET'); return result
    if metrics['previous_net_income'] <= 0:
        result['reasons'].append('NONPOSITIVE_COMPARISON_PROFIT'); return result
    if min(metrics['net_income'], metrics['operating_income'], metrics['fcf']) <= 0:
        result['thesis_broken'] = True
        result['reasons'].append('NONPOSITIVE_PROFIT_OR_CASHFLOW'); return result
    growth = metrics['revenue']/metrics['previous_revenue']-1
    earnings_growth = metrics['net_income']/metrics['previous_net_income']-1
    leverage = max(0, metrics['debt']-metrics['cash'])/metrics['fcf']
    components = {'revenue_growth': _tier(growth, .05, 0),
                  'profit_growth': _tier(earnings_growth, .05, 0),
                  'operating_margin': _tier(metrics['operating_income']/metrics['revenue'], .15, .10),
                  'fcf_margin': _tier(metrics['fcf']/metrics['revenue'], .10, .05),
                  'leverage': _tier(leverage, 2, 4, True)}
    result.update(ready=True, components=components, score=sum(components.values()), leverage=leverage,
                  fair_value=min(25*metrics['net_income'], 20*metrics['fcf'])/metrics['shares'])
    return result


def candidate(analysis, ask, daily_atr, news, now, cfg=None):
    s = options(cfg or {})
    out = {'allowed': False, 'early': False, 'reasons': [], 'event_id': '',
           'nominal_fraction': s['nominal_fraction'], 'risk_cap': 40.,
           'fair_value': analysis.get('fair_value') or 0, 'score': analysis.get('score') or 0}
    if not analysis.get('ready') or not s['enabled']:
        out['reasons'] = list(analysis.get('reasons', [])) or ['INVESTMENT_DISABLED']; return out
    if not number(ask) or ask <= 0 or not number(daily_atr) or daily_atr <= 0:
        out['reasons'] = ['PRICE_OR_ATR_UNAVAILABLE']; return out
    if news.get('action') in ('reduce', 'exit') or news.get('conflict'):
        out['reasons'] = ['MATERIAL_NEWS_BLOCK']; return out
    normal = analysis['score'] >= s['normal_score'] and ask <= analysis['fair_value']*(1-s['discount'])
    if normal:
        out['allowed'] = True; return out
    event = news.get('positive', {})
    reference = event.get('reference_price', 0)
    early = (news.get('coverage_ok') is True and analysis['score'] >= s['early_score']
             and analysis['leverage'] <= 4 and ask <= analysis['fair_value']
             and event.get('event_id') and 0 <= now-event.get('published_at', 0) <= s['news_max_age']
             and number(reference) and reference > 0
             and ask-reference <= min(.05*reference, 1.5*daily_atr))
    if early:
        out.update(allowed=True, early=True, event_id=event['event_id'],
                   nominal_fraction=s['early_fraction'], risk_cap=s['early_risk'])
    else:
        out['reasons'] = ['SCORE_DISCOUNT_OR_EARLY_NEWS_REQUIREMENTS']
    return out


def sized_order(decision, ask, atr, spec, equity, budget, cfg=None):
    from .policy import floor_volume
    s = options(cfg or {})
    result = {'allowed': False, 'reason': 'INELIGIBLE', 'quantity': 0}
    if not decision.get('allowed'): return result
    size, tick = spec['contract_size'], spec['tick_size']
    if min(ask, atr, size, tick) <= 0: return result
    from math import floor
    stop = floor((ask-s['stop_atr']*atr)/tick)*tick
    slip = 2*spec['costs']['slippage_points']*spec['point']*size
    unit_cost = spec['commission']+slip+s['financing_days']*spec['costs']['financing_long_per_lot_day']
    risk = (ask-stop)*size+unit_cost
    reward = (decision['fair_value']-ask)*size-unit_cost
    if stop <= 0 or unit_cost > ask*size*s['max_cost_fraction'] or risk <= 0 or reward/risk < s['minimum_rr']:
        result['reason'] = 'COST_OR_NET_RR'; return result
    quantity = floor_volume(min(budget, decision['risk_cap'])/risk, spec['minimum'], spec['maximum'], spec['step'])
    quantity = floor_volume(min(quantity, equity*decision['nominal_fraction']/(ask*size)), spec['minimum'], spec['maximum'], spec['step'])
    result.update(allowed=quantity > 0, reason='' if quantity > 0 else 'MINIMUM_LOT_OR_RISK',
                  quantity=quantity, stop=stop, unit_risk=risk, unit_cost=unit_cost,
                  net_rr=reward/risk, nominal=quantity*ask*size)
    return result


def update_thesis(previous, analysis):
    """Count low scores only once per distinct fiscal quarter."""
    state = dict(previous or {})
    if not analysis.get('ready'): return state
    period = analysis['metrics']['period_end']
    if state.get('period') != period:
        state['low_quarters'] = state.get('low_quarters', 0)+1 if analysis['score'] < 50 else 0
        state['period'] = period
    return state


def exit_action(analysis, thesis, news, daily_close, *, early=False, opened=0, now=0, cfg=None):
    s = options(cfg or {})
    if news.get('action') == 'exit': return 'exit', 'MATERIAL_NEWS_EXIT'
    if analysis.get('thesis_broken'): return 'exit', 'FUNDAMENTAL_THESIS_BROKEN'
    if analysis.get('ready') and thesis.get('low_quarters', 0) >= 2: return 'exit', 'TWO_WEAK_QUARTERS'
    if analysis.get('ready') and daily_close >= analysis['fair_value']: return 'exit', 'FAIR_VALUE_REACHED'
    if early and (now-opened >= s['early_review_days']*86400 or analysis.get('metrics', {}).get('period_end', '') > thesis.get('entry_period', '9999')):
        if analysis.get('ready') and analysis['score'] >= s['normal_score']:
            return 'promote', 'EARLY_THESIS_CONFIRMED'
        return 'exit', 'EARLY_THESIS_EXPIRED'
    if news.get('action') == 'reduce': return 'reduce', 'MATERIAL_NEWS_REDUCE'
    return 'hold', ''
