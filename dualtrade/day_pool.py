"""Prospective day scan selection; this never grants entry permission."""
import math


def pool_size(cfg):
    value = cfg.get('research', {}).get('day_pool_size', 5)
    if type(value) is not int or not 1 <= value <= 8:
        raise ValueError('day_pool_size must be an integer from 1 to 8')
    return value


def select(ranked, held, previous, now, context, size):
    """Return None until a fresh same-context eligibility snapshot exists.

    Rank only current known eligible contracts, so an earnings-blocked high-ATR
    symbol does not consume a day's scan slot. Current entry gates run again
    after selection. A held symbol is retained for continuity, never authorized.
    """
    stamp = previous.get('time')
    if (type(stamp) not in (float, int) or not math.isfinite(stamp)
            or not 0 <= now - stamp <= 60
            or previous.get('decision_context') != context):
        return None
    known = {c['ticker']: c for c in previous.get('contracts', [])}
    result = list(dict.fromkeys(held))
    for ticker in ranked:
        contract = known.get(ticker, {})
        if (len(result) < size and ticker not in result
                and contract.get('verified') is True
                and contract.get('entry_allowed') is True):
            result.append(ticker)
    return result
