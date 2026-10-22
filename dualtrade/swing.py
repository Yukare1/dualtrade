"""Independent technical-swing readiness; no orders and no intraday pool.

Broker tick volume is deliberately not described as exchange volume. The first
version uses closed-bar price structure and same-date benchmark relative strength.
"""
from math import isfinite

DEFAULTS = dict(enabled=True, long_only=True)


def options(cfg=None):
    return {**DEFAULTS, **(cfg or {}).get('swing', {})}


def context(bars, now, offset=0):
    if bars is None or len(bars) < 65:
        return {'ready': False, 'reason': 'DAILY_HISTORY'}
    times = [int(r['time'])-offset for r in bars]
    closes = [float(r['close']) for r in bars]
    if (not all(isfinite(p) and p > 0 for p in closes) or
        any(b <= a for a, b in zip(times, times[1:])) or
        not 86400 <= now-times[-1] <= 7*86400):
        return {'ready': False, 'reason': 'DAILY_TIME_OR_PRICE'}
    def ema(period):
        v = closes[0]; previous = v
        for p in closes[1:]:
            previous, v = v, v+(p-v)*2/(period+1)
        return v, previous
    fast, previous = ema(20); slow, _ = ema(50)
    return dict(ready=True, as_of=times[-1], bullish=closes[-1] > fast > slow and fast > previous,
                return20=closes[-1]/closes[-21]-1, close=closes[-1])


def build(collector, snapshot, mappings, now, macro_ok):
    cfg, mt = collector.cfg, getattr(collector.broker, 'mt', None)
    settings = options(cfg)
    benchmark = mappings.get('SPY')
    reference = {'ready': False}
    if mt is not None and benchmark and benchmark['verified']:
        reference = context(mt.copy_rates_from_pos(benchmark['symbol'], mt.TIMEFRAME_D1, 1, 130),
                            now, benchmark['costs']['broker_utc_offset_seconds'])
    decisions = []
    for c in snapshot['contracts']:
        own = context(mt.copy_rates_from_pos(c['symbol'], mt.TIMEFRAME_D1, 1, 130), now,
                      c['server_offset']) if mt is not None and c['verified'] else {'ready': False}
        ready = bool(settings['enabled'] and c['verified'] and macro_ok and
                     c.get('swing_sources_ok') and own['ready'] and reference['ready'] and
                     own['as_of'] == reference['as_of'] and not snapshot.get('foreign_exposure'))
        allowed = bool(ready and c['entry_allowed'] and own['bullish'] and reference['bullish'] and
                       own['return20'] >= reference['return20'] and not c['veto_buy'])
        row = dict(ready=ready, allowed=allowed, long_only=settings['long_only'],
                   context=own, benchmark=reference,
                   reason='ELIGIBLE' if allowed else 'SOURCE_MARKET_TREND_OR_EVENT')
        c['swing'] = row
        decisions.append({'ticker': c['ticker'], **row})
    snapshot['swing_ready'] = any(r['ready'] for r in decisions)
    snapshot['swing_report'] = decisions

